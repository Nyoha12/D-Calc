"""True module CLI, fresh reader, terminal authority, tamper and no-write dry-run."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from didgeridoo_optimizer.tests.test_tolerance_audit import ROOT, make_job
from didgeridoo_optimizer.pipeline import tolerance_audit as audit
from didgeridoo_optimizer.reporting import tolerance_audit as report

ENV=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',
         NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')


def cli(job,out,*args):
    return subprocess.run([sys.executable,'-B','-m','tools.tolerance_audit','--job',str(job),
        '--output-dir',str(out),*args],cwd=ROOT,env=ENV,text=True,capture_output=True,timeout=30)


def test_real_cli_and_fresh_reader(tmp_path):
    job=make_job(tmp_path);out=tmp_path/'out';child=cli(job,out)
    assert child.returncode==0,child.stderr+child.stdout
    response=json.loads(child.stdout)
    assert response['counterexample_found'] and response['child']['reaped']
    fresh=subprocess.run([sys.executable,'-B','-c',
        'import json,sys; from didgeridoo_optimizer.reporting.tolerance_audit import read_result; '
        'r=read_result(sys.argv[1]); print(json.dumps({k:r[k] for k in '
        '["ok","execution_complete","counterexample_found","artifacts_verified"]}))',str(out)],
        cwd=ROOT,env=ENV,text=True,capture_output=True,timeout=15)
    assert fresh.returncode==0,fresh.stderr
    assert json.loads(fresh.stdout)==dict(ok=True,execution_complete=True,counterexample_found=True,artifacts_verified=True)
    result=report.read_result(out)
    assert result['plan']['provenance']['executed_sources_sha256']
    assert 'didgeridoo_optimizer/pipeline/tolerance_audit.py' in result['plan']['provenance']['executed_sources_sha256']
    assert all(name in result['plan']['provenance']['loaded_sources_sha256'] for name in (
        'tools/tolerance_audit.py','didgeridoo_optimizer/geometry/tolerance_scenarios.py'))
    profile=json.loads((out/'profile_7_nominal__fixed.json').read_text())
    assert profile['segments'][0]['length_cm']==60
    assert len(list(out.glob('*.csv')))==4


def test_dry_run_no_tmm_mesh_output_or_stdout(tmp_path,monkeypatch):
    job=make_job(tmp_path,acoustic=True);out=tmp_path/'out'
    monkeypatch.setattr(audit.ProjectionEvaluator,'evaluate_design',lambda *a,**k:pytest.fail('acoustics'))
    import didgeridoo_optimizer.pipeline.assembly_path as native
    monkeypatch.setattr(native,'mesh_for',lambda *a,**k:pytest.fail('mesh'))
    monkeypatch.setattr(native,'input_impedance',lambda *a,**k:pytest.fail('TMM'))
    result=audit.run(job,out,dry_run=True)
    assert result['ok'] and not out.exists()
    before={p.relative_to(tmp_path) for p in tmp_path.rglob('*')}
    child=cli(job,out,'--dry-run')
    assert child.returncode==0,child.stderr
    assert child.stdout=='' and not out.exists()
    assert {p.relative_to(tmp_path) for p in tmp_path.rglob('*')}==before


def test_tamper_rejected_and_reader_never_recalculates(tmp_path,monkeypatch):
    job=make_job(tmp_path);out=tmp_path/'out';assert cli(job,out).returncode==0
    monkeypatch.setattr(audit.ProjectionEvaluator,'evaluate_design',lambda *a,**k:pytest.fail('readback calculation'))
    assert report.read_result(out)['ok']
    (out/'criteria.csv').write_text('tamper')
    with pytest.raises(ValueError,match='altérée'):report.read_result(out)


def test_copied_cli_cannot_claim_repository_provenance(tmp_path):
    job=make_job(tmp_path);copied=tmp_path/'copied.py';shutil.copyfile(ROOT/'tools/tolerance_audit.py',copied)
    child=subprocess.run([sys.executable,'-B',str(copied),'--job',str(job),'--output-dir',str(tmp_path/'out')],
        cwd=ROOT,env=dict(ENV,PYTHONPATH=str(ROOT)),text=True,capture_output=True,timeout=15)
    assert child.returncode==2 and 'provenance CLI' in child.stderr
    assert not (tmp_path/'out').exists()


def test_terminal_receipt_required(tmp_path):
    job=make_job(tmp_path);out=tmp_path/'out';assert cli(job,out).returncode==0
    (out/'execution.completed.json').rename(out/'not_completed.json')
    result=report.read_result(out)
    assert not result['ok'] and not result['execution_complete']
    assert result['counterexample_found']


def test_timeout_kills_only_owned_child_and_preserves_observations(tmp_path,monkeypatch):
    job=make_job(tmp_path);out=tmp_path/'out'
    original=audit.subprocess.Popen
    class TimeoutChild:
        def __init__(self,*args,**kwargs):
            self.returncode=None;self.killed=False;self.communications=0
        def communicate(self,*args,**kwargs):
            self.communications+=1
            if self.communications==1:
                report.write_json(out/'observation_0001.json',{'criteria':[{'id':'retained','value_si':.6}]})
                raise subprocess.TimeoutExpired('owned-test-child',180)
            return ('','')
        def poll(self):return self.returncode
        def kill(self):self.killed=True;self.returncode=-9
    owned=TimeoutChild()
    def factory(*args,**kwargs):
        # Provenance git queries use subprocess.run/Popen too.
        return owned if isinstance(args[0],list) and 'tools.tolerance_audit' in args[0] else original(*args,**kwargs)
    monkeypatch.setattr(audit.subprocess,'Popen',factory)
    result=audit.run(job,out)
    assert not result['ok'] and result['status']=='interrupted'
    assert owned.killed and owned.communications==2 and result['child']['reaped']
    reread=report.read_result(out)
    assert not reread['ok'] and reread['observations'][0]['criteria'][0]['value_si']==.6


def test_wrong_thread_refuses_before_output(tmp_path):
    import concurrent.futures
    job=make_job(tmp_path);out=tmp_path/'out'
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(ValueError,match='thread principal'):
            pool.submit(audit.run,job,out).result()
    assert not out.exists()


def test_direct_worker_rejects_unbounded_environment(tmp_path):
    child=subprocess.run([sys.executable,'-B','-m','tools.tolerance_audit','--worker'],input='{}',
        cwd=ROOT,env=ENV,text=True,capture_output=True,timeout=15)
    assert child.returncode==2 and 'limites mémoire/CPU' in child.stderr
    assert not list(tmp_path.iterdir())
