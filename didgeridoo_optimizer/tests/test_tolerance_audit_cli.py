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


@pytest.fixture
def supervised_double(tmp_path,monkeypatch):
    """Controlled API protocol only: no live hung child or acoustic calculation."""
    from types import SimpleNamespace
    import signal
    job=make_job(tmp_path);out=tmp_path/'out';prepared,plan=audit.load_inputs(job)
    clock=[0.];children=[]
    monkeypatch.setattr(audit,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(audit,'load_inputs',lambda path:(prepared,plan))
    class Pipe:
        def __init__(self):self.closed=False;self.fail=False;self.delay=0.
        def close(self):
            clock[0]+=self.delay
            if self.fail:raise OSError('controlled close failure')
            self.closed=True
    class Child:
        def __init__(self,initial='timeout',cleanup='normal',wait='normal'):
            self.returncode=None;self.initial=initial;self.cleanup=cleanup;self.wait_mode=wait
            self.communications=[];self.waits=[];self.kills=0
            self.stdin=Pipe();self.stdout=Pipe();self.stderr=Pipe()
        def communicate(self,*args,timeout=None):
            assert timeout is not None and timeout>0
            self.communications.append(timeout)
            if len(self.communications)==1:
                if self.initial in ('normal','failed'):
                    self.returncode=0 if self.initial=='normal' else 7
                    return json.dumps(dict(ok=True,execution_complete=True,status='sampled_conforming')),''
                if self.initial=='late':
                    clock[0]+=timeout+1.;self.returncode=0
                    return json.dumps(dict(ok=True,execution_complete=True,status='sampled_conforming')),''
                report.write_json(out/'observation_0001.json',dict(retained='partial evidence'))
                if self.initial=='finished':self.returncode=7
                clock[0]+=timeout
                raise subprocess.TimeoutExpired('owned-double',timeout)
            if self.cleanup in ('timeout','late','late_phase'):
                clock[0]+=(timeout+.1 if self.cleanup=='late_phase' else
                           timeout if self.cleanup=='timeout' else audit.COLLECTION_WALL_SECONDS+1.)
                if self.cleanup=='timeout':raise subprocess.TimeoutExpired('owned-double cleanup',timeout)
            if self.cleanup in ('sigint','sigterm'):
                signum=signal.SIGINT if self.cleanup=='sigint' else signal.SIGTERM
                signal.getsignal(signum)(signum,None)
            self.returncode=-9 if self.kills else 7
            return '',''
        def poll(self):return self.returncode
        def kill(self):self.kills+=1
        def wait(self,timeout=None):
            assert timeout is not None and timeout>0
            self.waits.append(timeout)
            if self.wait_mode=='timeout':
                clock[0]+=timeout
                raise subprocess.TimeoutExpired('owned-double wait',timeout)
            self.returncode=-9
            return self.returncode
    original=audit.subprocess.Popen
    def launch(child):
        children.append(child)
        def factory(*args,**kwargs):
            if isinstance(args[0],list) and 'tools.tolerance_audit' in args[0]:
                if isinstance(child,Exception):raise child
                return child
            return original(*args,**kwargs)
        monkeypatch.setattr(audit.subprocess,'Popen',factory)
        return audit.run(job,out)
    return SimpleNamespace(Child=Child,launch=launch,out=out,clock=clock)


def test_supervision_normal_closes_pipes_and_confirms_child(supervised_double):
    h=supervised_double;child=h.Child(initial='normal');response=h.launch(child)
    assert response['ok'] and response['execution_complete'] and response['closure_confirmed']
    assert response['child']==dict(created=True,exit_code=0,reaped=True,collected=True,pipes_closed=True)
    assert child.kills==0 and len(child.communications)==1 and child.waits==[]
    assert all(getattr(child,name).closed for name in ('stdin','stdout','stderr'))
    assert report.read_execution(h.out)['ok']


@pytest.mark.parametrize('wait_mode',['normal','timeout'])
def test_supervision_second_timeout_is_cumulative_and_unconfirmed(supervised_double,wait_mode):
    h=supervised_double;child=h.Child(cleanup='timeout',wait=wait_mode);response=h.launch(child)
    assert not response['ok'] and not response['execution_complete'] and not response['closure_confirmed']
    assert child.kills==1 and len(child.communications)==2 and len(child.waits)==1
    assert child.communications[1]+child.waits[0]<=audit.COLLECTION_WALL_SECONDS
    assert response['child']['reaped']==(wait_mode=='normal')
    assert not response['child']['collected'] and response['child']['pipes_closed']
    assert not (h.out/'execution.completed.json').exists()
    assert json.loads((h.out/'observation_0001.json').read_text())==dict(retained='partial evidence')
    assert h.clock[0]<=audit.COMPUTE_WALL_SECONDS+audit.COLLECTION_WALL_SECONDS


@pytest.mark.parametrize('phase',['compute','collection','close'])
def test_supervision_late_return_never_confirms_success(supervised_double,phase):
    h=supervised_double;child=h.Child(initial='late' if phase=='compute' else 'timeout',
                                    cleanup='late' if phase=='collection' else 'normal')
    if phase=='close':child.stdout.delay=audit.COLLECTION_WALL_SECONDS+1.
    response=h.launch(child)
    assert not response['ok'] and not response['execution_complete']
    assert not report.read_execution(h.out).get('ok')
    if phase!='compute':
        assert not response['closure_confirmed'] and not (h.out/'execution.completed.json').exists()
    assert all(getattr(child,name).closed for name in ('stdin','stdout','stderr'))


def test_supervision_close_error_keeps_partial_bundle_unconfirmed(supervised_double):
    h=supervised_double;child=h.Child();child.stdout.fail=True;response=h.launch(child)
    assert not response['ok'] and not response['closure_confirmed']
    assert not response['child']['pipes_closed'] and response['child']['reaped']
    assert child.stdin.closed and child.stderr.closed
    assert any('controlled close failure' in reason for reason in response['cleanup_errors'])
    assert not (h.out/'execution.completed.json').exists()
    assert (h.out/'observation_0001.json').is_file()


def test_supervision_already_finished_child_is_reaped_without_kill(supervised_double):
    h=supervised_double;child=h.Child(initial='finished');response=h.launch(child)
    assert not response['ok'] and response['closure_confirmed']
    assert child.kills==0 and response['child']['reaped'] and response['child']['exit_code']==7
    assert len(child.communications)==2 and not child.waits
    assert report.read_execution(h.out)['ok'] is False


def test_supervision_launch_error_does_not_claim_a_reaped_child(supervised_double):
    h=supervised_double;response=h.launch(OSError('controlled launch failure'))
    assert not response['ok'] and response['status']=='failed'
    assert not response['child']['created'] and not response['child']['reaped']
    assert response['child']['exit_code'] is None
    assert 'controlled launch failure' in response['reason']
    assert report.read_execution(h.out)['ok'] is False


@pytest.mark.parametrize('received',['sigint','sigterm'])
def test_supervision_signal_during_collection_finishes_owned_cleanup(supervised_double,received):
    import signal
    before={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
    h=supervised_double;child=h.Child(cleanup=received);response=h.launch(child)
    assert not response['ok'] and response['status']=='interrupted'
    assert response['cleanup_signals']==[signal.SIGINT if received=='sigint' else signal.SIGTERM]
    assert child.kills==1 and response['child']['reaped'] and response['child']['pipes_closed']
    assert len(child.communications)==2 and child.communications[1]<=audit.COLLECTION_WALL_SECONDS
    assert (h.out/'observation_0001.json').is_file()
    assert {s:signal.getsignal(s) for s in before}==before
    assert report.read_execution(h.out)['ok'] is False


def test_supervision_late_prepared_closure_has_no_terminal_authority(supervised_double,monkeypatch):
    h=supervised_double;child=h.Child(initial='normal');original=report.close_prepared
    def late_closure(*args):
        original(*args)
        h.clock[0]+=audit.PUBLIC_WALL_SECONDS+1.
    monkeypatch.setattr(report,'close_prepared',late_closure)
    response=h.launch(child)
    assert not response['ok'] and not response['execution_complete'] and not response['closure_confirmed']
    assert response['reason']=='retour tardif de clôture'
    assert (h.out/report.PREPARED).exists() and not (h.out/'execution.completed.json').exists()
    assert not report.read_execution(h.out).get('ok')


def test_supervision_nonzero_exit_cannot_echo_success(supervised_double):
    h=supervised_double;child=h.Child(initial='failed');response=h.launch(child)
    assert not response['ok'] and not response['execution_complete']
    assert response['child']['exit_code']==7 and response['child']['reaped']
    assert child.kills==0 and report.read_execution(h.out)['ok'] is False


def test_supervision_collection_late_within_cumulative_budget_is_unconfirmed(supervised_double):
    h=supervised_double;child=h.Child(cleanup='late_phase');response=h.launch(child)
    assert h.clock[0]<audit.COMPUTE_WALL_SECONDS+audit.COLLECTION_WALL_SECONDS
    assert not response['ok'] and not response['closure_confirmed']
    assert response['child']['reaped'] and response['child']['pipes_closed']
    assert 'retour tardif de collecte des canaux' in response['cleanup_errors']
    assert not (h.out/'execution.completed.json').exists()


@pytest.mark.parametrize('closure',['late','error'])
def test_supervision_terminal_return_cannot_leave_success_authority(supervised_double,monkeypatch,closure):
    h=supervised_double;child=h.Child(initial='normal');original=report.commit_terminal
    def faulty_commit(output):
        original(output)
        if closure=='late':h.clock[0]+=audit.PUBLIC_WALL_SECONDS+1.
        else:raise OSError('controlled terminal error after publication')
    monkeypatch.setattr(report,'commit_terminal',faulty_commit)
    response=h.launch(child)
    assert not response['ok'] and not response['execution_complete'] and not response['closure_confirmed']
    assert (h.out/'execution.completed.json').exists() and (h.out/'execution.cancelled.json').exists()
    assert report.read_execution(h.out)['ok'] is False
