"""Controller doubles and small native CLI checks; no blocked real child."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from didgeridoo_optimizer.pipeline import catalogue_robust as api
from didgeridoo_optimizer.optimization.catalogue_robust import ROOT
from didgeridoo_optimizer.tests.test_catalogue_robust import make_job

ENV = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
           MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1",
           PYTHONDONTWRITEBYTECODE="1")


def cli(job, output, *arguments):
    return subprocess.run(
        [sys.executable, "-B", "-m", "tools.catalogue_robust",
         "--job", str(job), "--output-dir", str(output), *arguments],
        cwd=ROOT, env=ENV, text=True, capture_output=True, timeout=105)


def test_dry_run_no_compute_no_write_no_process(tmp_path, monkeypatch):
    job = make_job(tmp_path)
    output = tmp_path / "absent"
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run invoked a producer or output operation")
    monkeypatch.setattr(api, "supervise", forbidden)
    monkeypatch.setattr(api, "_subreaper", forbidden)
    monkeypatch.setattr(api.report, "put", forbidden)
    import didgeridoo_optimizer.pipeline.assembly_path as native
    monkeypatch.setattr(native, "input_impedance", forbidden)
    monkeypatch.setattr(native, "mesh_for", forbidden)
    monkeypatch.setattr(native.ProjectionEvaluator, "evaluate_design", forbidden)
    before = {p.relative_to(tmp_path): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    assert api.run(job, output, dry_run=True)["ok"]
    child = cli(job, output, "--dry-run")
    assert child.returncode == 0, child.stderr
    assert child.stdout == "" and not output.exists()
    after = {p.relative_to(tmp_path): p.read_bytes()
             for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


def test_small_real_cli_and_fresh_reader(tmp_path):
    job = make_job(tmp_path)
    output = tmp_path / "output"
    child = cli(job, output)
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(child.stdout)
    assert result["closure_confirmed"] and result["best_known"] == ["c0"]
    fresh = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; "
         "from didgeridoo_optimizer.reporting.catalogue_robust import read_result; "
         "r=read_result(sys.argv[1],sys.argv[2]); "
         "assert r['ok']; "
         "assert 'didgeridoo_optimizer.pipeline.catalogue_robust' not in sys.modules; "
         "assert 'didgeridoo_optimizer.pipeline.adjustment_policy' not in sys.modules; "
         "assert 'didgeridoo_optimizer.pipeline.time_domain_reference' not in sys.modules",
         str(output), str(job)],
        cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=30)
    assert fresh.returncode == 0, fresh.stderr
    assert cli(job, output).returncode != 0


def test_copied_cli_is_refused(tmp_path):
    job = make_job(tmp_path)
    copied = tmp_path / "copied.py"
    shutil.copyfile(ROOT / "tools/catalogue_robust.py", copied)
    output = tmp_path / "output"
    child = subprocess.run(
        [sys.executable, "-B", str(copied), "--job", str(job),
         "--output-dir", str(output), "--dry-run"],
        cwd=ROOT, env=dict(ENV, PYTHONPATH=str(ROOT)),
        capture_output=True, text=True, timeout=30)
    assert child.returncode == 2 and "provenance CLI" in child.stderr
    assert not output.exists()


@pytest.mark.parametrize("mode", ["normal", "timeout", "late", "nonzero", "orphan"])
def test_owned_group_supervision_is_bounded(tmp_path, monkeypatch, mode):
    clock = [0.0]
    alive = [True]
    signals = []
    waits = []
    reaps = []
    class Child:
        pid = 41234
        returncode = None
        def poll(self):
            return self.returncode
        def wait(self, timeout):
            assert 0 < timeout <= 0.2
            waits.append(timeout)
            if mode == "timeout":
                clock[0] += timeout
                raise subprocess.TimeoutExpired("double", timeout)
            self.returncode = 9 if mode == "nonzero" else 0
            alive[0] = mode == "orphan"
            if mode == "late":
                clock[0] = 500.0
            return self.returncode
    child = Child()
    def send(group, signum):
        assert group == child.pid
        signals.append(signum)
        if signum == signal.SIGKILL or mode == "orphan":
            child.returncode = -9
            alive[0] = False
    def reap(group):
        assert group == child.pid
        reaps.append(group)
        return []
    monkeypatch.setattr(api, "time", SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda n: clock.__setitem__(0, clock[0] + n)))
    monkeypatch.setattr(api, "_group_exists", lambda group: alive[0])
    monkeypatch.setattr(api, "_signal_group", send)
    monkeypatch.setattr(api, "_reap_adopted", reap)
    launches = []
    def launch(command, **kwargs):
        assert kwargs["start_new_session"] is True
        assert kwargs["preexec_fn"] is api._controller_limits
        assert "tools.adjustment_policy" in command
        launches.append(command)
        return child
    result = api.supervise("policy.json", "native", 240.0, [None, None], popen=launch)
    assert len(launches) == 1
    assert result["clean_exit"] is (mode == "normal")
    assert len(signals) <= 2
    assert all(group == child.pid for group in reaps)
    if mode != "late":
        assert clock[0] <= api.CONTROLLER_SECONDS + api.COLLECTION_SECONDS
    if mode == "normal":
        assert not signals and result["collected"]


def test_uncollected_group_prevents_next_candidate(tmp_path, monkeypatch):
    job = make_job(tmp_path)
    output = tmp_path / "output"
    calls = []
    def uncollected(*args, **kwargs):
        calls.append(args)
        return dict(created=True, pid=41234, exit_code=None,
                    collected=False, clean_exit=False, signals=[signal.SIGKILL],
                    adopted_reaped=[], reason="controlled uncollected double",
                    wall_seconds=0.0)
    monkeypatch.setattr(api, "supervise", uncollected)
    _, _, result, receipt = api._run(job, output, api.time.monotonic() + 90)
    assert len(calls) == 1
    assert not receipt["closure_confirmed"]
    assert not result["catalogue_complete"] and not result["selection_complete"]
    assert not (output / "completed.json").exists()


def test_existing_alarm_and_wrong_thread_are_refused(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    job = make_job(tmp_path)
    output = tmp_path / "output"
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="thread principal"):
            executor.submit(api.run, job, output).result()
    old = signal.getitimer(signal.ITIMER_REAL)
    try:
        signal.setitimer(signal.ITIMER_REAL, 30.0)
        with pytest.raises(ValueError, match="alarme préexistante"):
            api.run(job, output)
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old)
    assert not output.exists()


@pytest.mark.parametrize("module_name", [
    "didgeridoo_optimizer.optimization.catalogue_robust",
    "didgeridoo_optimizer.reporting.catalogue_robust",
    "didgeridoo_optimizer.pipeline.catalogue_robust", "tools.catalogue_robust",
])
def test_review_loaded_source_cannot_claim_replacement_bytes(tmp_path, module_name):
    isolated = tmp_path / "source"
    shutil.copytree(ROOT, isolated, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    script = """
import importlib,json,sys
from pathlib import Path
module=importlib.import_module(sys.argv[1]);path=Path(module.__file__)
path.write_text(path.read_text()+'\\n# bytes changed after this module was loaded\\n')
from didgeridoo_optimizer.optimization.catalogue_robust import load_inputs
try:
 load_inputs('not_an_input_to_be_read.json')
except ValueError as exc:
 assert 'source catalogue modifiée depuis chargement' in str(exc),str(exc)
 print('CHANGED_LOADED_SOURCE_REJECTED')
else:
 raise AssertionError('changed loaded source accepted')
"""
    child = subprocess.run([sys.executable, "-B", "-c", script, module_name],
        cwd=isolated, env=dict(ENV, PYTHONPATH=str(isolated)),
        capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    assert child.stdout.strip() == "CHANGED_LOADED_SOURCE_REJECTED"


def test_review_fresh_reader_calls_no_scientific_producer(tmp_path):
    from didgeridoo_optimizer.tests.test_catalogue_robust import produce_fixture
    job = make_job(tmp_path); output = produce_fixture(tmp_path, job)
    script = """
import sys
from didgeridoo_optimizer.reporting.catalogue_robust import read_result
from didgeridoo_optimizer.pipeline import tolerance_audit as audit
from didgeridoo_optimizer.pipeline import assembly_path as assembly
from didgeridoo_optimizer.optimization import constrained_search as search

def forbidden(*args,**kwargs):raise AssertionError('scientific producer called by reader')
for name in ('execute','worker','run'):
 setattr(audit,name,forbidden)
assembly.input_impedance=forbidden;assembly.mesh_for=forbidden
assembly.ProjectionEvaluator.evaluate_design=forbidden
search.search=forbidden
result=read_result(sys.argv[1],sys.argv[2])
assert result['ok'] and result['semantic_verified']
assert 'didgeridoo_optimizer.pipeline.catalogue_robust' not in sys.modules
assert 'didgeridoo_optimizer.pipeline.adjustment_policy' not in sys.modules
print('NATIVE_GEOMETRY_ONLY_READER_OK')
"""
    child = subprocess.run([sys.executable, "-B", "-c", script,str(output),str(job)],
        cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    assert child.stdout.strip() == "NATIVE_GEOMETRY_ONLY_READER_OK"
