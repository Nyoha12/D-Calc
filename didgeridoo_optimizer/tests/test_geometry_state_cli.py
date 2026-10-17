import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
EXAMPLE=ROOT/'project_specs/examples/geometry_state'
ENV=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')


def test_real_dry_cli_and_exclusive_modes(tmp_path):
    args=[sys.executable,'-B','-m','tools.geometry_state','--config',str(EXAMPLE/'config.yaml'),'--design',str(EXAMPLE/'design.json'),
          '--recipe',str(EXAMPLE/'recipe.json'),'--output-dir',str(tmp_path/'none'),'--dry-run']
    result=subprocess.run(args,cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['output_created'] is False and not (tmp_path/'none').exists()
    bad=subprocess.run(args+['--read-result','absent'],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=15)
    assert bad.returncode==2 and 'Exclusive' in bad.stdout


def test_imported_worker_reader_refuses_without_caller_resource_changes():
    import resource,signal
    from tools.geometry_state import main
    old=[resource.getrlimit(k) for k in (resource.RLIMIT_CPU,resource.RLIMIT_AS,resource.RLIMIT_FSIZE)]
    handler=signal.getsignal(signal.SIGALRM);timer=signal.getitimer(signal.ITIMER_REAL)
    assert main(['--read-result','absent'])==2
    assert old==[resource.getrlimit(k) for k in (resource.RLIMIT_CPU,resource.RLIMIT_AS,resource.RLIMIT_FSIZE)]
    assert handler==signal.getsignal(signal.SIGALRM) and timer==signal.getitimer(signal.ITIMER_REAL)


def _product():
    import pytest
    path=os.environ.get('DCALC_GEOMETRY_STATE_BUNDLE')
    if not path:pytest.skip('Requires the separately acquired bounded two-profile demonstration; no implicit fit')
    return Path(path)


def test_real_product_reader_has_no_fit_tmm_conversion_or_step(monkeypatch):
    bundle=_product()
    from didgeridoo_optimizer.reporting import geometry_state as report
    from didgeridoo_optimizer.nonlinear import scalar_fit,scalar_certificate_builder,passive_realization
    from didgeridoo_optimizer.acoustics import transfer_matrix
    from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
    from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
    def no(*a,**kw):raise AssertionError('Reader performed new science/evolution')
    for obj,name in [(scalar_fit,'fit'),(scalar_certificate_builder,'certify'),(passive_realization,'convert'),
                     (transfer_matrix,'input_impedance'),(SimultaneousCoupling,'step'),(PassiveState,'step')]:
        monkeypatch.setattr(obj,name,no)
    assert report.read_result(bundle)['ok']


def test_real_geometry_reader_requires_completed_read_closure(tmp_path):
    import shutil,pytest
    from didgeridoo_optimizer.reporting import geometry_state as report
    bundle=_product();dest=tmp_path/'copy';shutil.copytree(bundle,dest)
    (dest/'complete.json').unlink()
    with pytest.raises((ValueError,OSError)):report.read_result(dest)


def test_rehashed_acquisition_still_refused(tmp_path):
    import shutil,pytest
    from didgeridoo_optimizer.reporting import geometry_state as report
    from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as strict
    bundle=_product();dest=tmp_path/'copy';shutil.copytree(bundle,dest)
    value=strict.read_json(dest/'evidence.json');value['acquisition']['nature']='measurement'
    (dest/'evidence.json').write_text(strict.strict_json(value))
    manifest=strict.read_json(dest/'manifest.json')
    manifest['files']['evidence.json']=strict.sha(strict.read_bytes(dest/'evidence.json'))
    (dest/'manifest.json').write_text(strict.strict_json(manifest))
    execution=strict.read_json(dest/'execution.json');execution['manifest_sha256']=strict.sha(strict.read_bytes(dest/'manifest.json'))
    (dest/'execution.json').write_text(strict.strict_json(execution))
    (dest/'execution.closed.json').write_text(strict.strict_json(dict(execution_sha256=strict.sha(strict.read_bytes(dest/'execution.json')))))
    complete=strict.read_json(dest/'complete.json');complete.update(execution_sha256=strict.sha(strict.read_bytes(dest/'execution.json')),manifest_sha256=strict.sha(strict.read_bytes(dest/'manifest.json')))
    (dest/'complete.json').write_text(strict.strict_json(complete))
    with pytest.raises(ValueError,match='provenance'):report.read_result(dest)


def test_failed_owned_child_is_collected_with_unchanged_caller_limits(tmp_path,monkeypatch):
    import resource
    from didgeridoo_optimizer.pipeline import geometry_state as flow
    class Child:
        pid=123;returncode=None;calls=0;terminated=False
        def wait(self,timeout):
            self.calls+=1
            if self.calls==1:raise subprocess.TimeoutExpired('double',timeout)
            self.returncode=-15;return self.returncode
        def poll(self):return self.returncode
        def terminate(self):self.terminated=True
        def kill(self):self.returncode=-9
    child=Child();monkeypatch.setattr(flow.subprocess,'Popen',lambda *a,**kw:child)
    before=[resource.getrlimit(k) for k in (resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE)]
    receipt=flow._child('worker','unused',tmp_path,.01)
    assert receipt['reaped'] and receipt['exit_code']==-15 and receipt['error'] and child.terminated
    assert before==[resource.getrlimit(k) for k in (resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE)]
