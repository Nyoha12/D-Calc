"""Real bounded CLI, fresh reader, strict dry-run and owned-child doubles."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from didgeridoo_optimizer.tests.test_adjustment_policy import ROOT, make_job
from didgeridoo_optimizer.pipeline import adjustment_policy as api
from didgeridoo_optimizer.reporting import adjustment_policy as report

ENV=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',
         NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')


def cli(job,out,*args):
    return subprocess.run([sys.executable,'-B','-m','tools.adjustment_policy','--job',str(job),
        '--output-dir',str(out),*args],cwd=ROOT,env=ENV,text=True,capture_output=True,timeout=35)


@pytest.mark.parametrize('kind',['fixed','per_configuration','shared_offset'])
def test_real_cli_and_fresh_reader(tmp_path,kind):
    job=make_job(tmp_path,kind,fixed=kind=='fixed');out=tmp_path/'out'
    child=cli(job,out)
    assert child.returncode==0,child.stderr+child.stdout
    response=json.loads(child.stdout)
    assert response['child']['reaped'] and response['closure_confirmed']
    fresh=subprocess.run([sys.executable,'-B','-c',
        'import json,sys; from didgeridoo_optimizer.reporting.adjustment_policy import read_result; '
        'r=read_result(sys.argv[1]); assert "didgeridoo_optimizer.pipeline.adjustment_policy" not in sys.modules; '
        'print(json.dumps({k:r[k] for k in ["ok","execution_complete","semantic_verified"]}))',str(out)],
        cwd=ROOT,env=ENV,text=True,capture_output=True,timeout=15)
    assert fresh.returncode==0,fresh.stderr
    assert json.loads(fresh.stdout)==dict(ok=True,execution_complete=True,semantic_verified=True)
    result=report.read_result(out)
    provenance=result['plan']['provenance']
    assert 'tools/adjustment_policy.py' in provenance['loaded_sources_sha256']
    assert 'didgeridoo_optimizer/pipeline/adjustment_policy.py' in provenance['executed_sources_sha256']
    assert (out/'.worker-stdout.log').stat().st_size<65536
    assert len(list(out.glob('*.csv')))==5


def test_dry_run_no_search_mesh_tmm_output_stdout(tmp_path,monkeypatch):
    job=make_job(tmp_path,acoustic=True);out=tmp_path/'out'
    def forbidden(*a,**k): pytest.fail('dry-run computed')
    monkeypatch.setattr(api,'search',forbidden)
    monkeypatch.setattr(api.audit.ProjectionEvaluator,'evaluate_design',forbidden)
    import didgeridoo_optimizer.pipeline.assembly_path as native
    monkeypatch.setattr(native,'mesh_for',forbidden);monkeypatch.setattr(native,'input_impedance',forbidden)
    before={p.relative_to(tmp_path) for p in tmp_path.rglob('*')}
    assert api.run(job,out,dry_run=True)['ok'] and not out.exists()
    child=cli(job,out,'--dry-run');assert child.returncode==0,child.stderr
    assert child.stdout=='' and {p.relative_to(tmp_path) for p in tmp_path.rglob('*')}==before


def test_cli_copied_source_refused(tmp_path):
    job=make_job(tmp_path);out=tmp_path/'out';copied=tmp_path/'copied.py'
    shutil.copyfile(ROOT/'tools/adjustment_policy.py',copied)
    result=subprocess.run([sys.executable,'-B',str(copied),'--job',str(job),'--output-dir',str(out)],
        cwd=ROOT,env=dict(ENV,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=15)
    assert result.returncode==2 and 'provenance CLI' in result.stderr and not out.exists()


def test_unbounded_worker_refused():
    result=subprocess.run([sys.executable,'-B','-m','tools.adjustment_policy','--worker'],input='{}',
        cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=15)
    assert result.returncode==2 and 'limites mémoire/CPU' in result.stderr


def test_receipt_missing_and_no_overwrite(tmp_path):
    job=make_job(tmp_path,one=True);out=tmp_path/'out'
    assert cli(job,out).returncode==0
    assert cli(job,out).returncode==2
    (out/'execution.completed.json').rename(out/'.withheld-terminal.json')
    assert not report.read_result(out)['ok']


def test_reader_never_searches_or_calls_tmm(tmp_path,monkeypatch):
    job=make_job(tmp_path,one=True);out=tmp_path/'out';assert cli(job,out).returncode==0
    def forbidden(*a,**k):pytest.fail('reader computation')
    import didgeridoo_optimizer.pipeline.assembly_path as native
    monkeypatch.setattr(native,'input_impedance',forbidden);monkeypatch.setattr(native,'mesh_for',forbidden)
    monkeypatch.setattr(api,'search',forbidden)
    assert report.read_result(out)['semantic_verified']


@pytest.fixture
def supervised(tmp_path,monkeypatch):
    job=make_job(tmp_path,one=True);out=tmp_path/'out';prepared,plan=api.load_inputs(job)
    clock=[0.];monkeypatch.setattr(api,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(api.audit,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(api,'load_inputs',lambda *a:(prepared,plan))
    class Pipe:
        closed=False
        def close(self):self.closed=True
    class Child:
        def __init__(self,mode='timeout',cleanup='normal'):
            self.mode=mode;self.cleanup=cleanup;self.returncode=None;self.kills=0;self.calls=[];self.waits=[]
            self.stdin=Pipe();self.stdout=None;self.stderr=None
        def communicate(self,*args,timeout=None):
            assert timeout is not None and timeout>0;self.calls.append(timeout)
            if len(self.calls)==1:
                if self.mode in ('normal','late','nonzero'):
                    self.returncode=7 if self.mode=='nonzero' else 0
                    self.log.write(json.dumps(dict(ok=True,status='test',execution_complete=True)))
                    self.log.flush()
                    if self.mode=='late':clock[0]+=timeout+1
                    return None,None
                report.write_json(out/'observation_0001.json',dict(partial='preserved'))
                clock[0]+=timeout
                if self.mode=='signal':raise api.RunInterrupted('test signal')
                raise subprocess.TimeoutExpired('owned double',timeout)
            if self.cleanup=='timeout':
                clock[0]+=timeout;raise subprocess.TimeoutExpired('owned collect',timeout)
            if self.cleanup=='late':clock[0]+=timeout+1
            if self.cleanup=='signal':signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
            self.returncode=-9 if self.kills else 0
            return None,None
        def poll(self):return self.returncode
        def kill(self):self.kills+=1
        def wait(self,timeout=None):
            assert timeout is not None and timeout>0;self.waits.append(timeout);self.returncode=-9;return -9
    original=api.subprocess.Popen
    def install(child):
        def factory(*a,**k):
            if 'tools.adjustment_policy' in a[0]:child.log=k['stdout'];return child
            return original(*a,**k)
        monkeypatch.setattr(api.subprocess,'Popen',factory)
    return job,out,clock,Child,install


@pytest.mark.parametrize('mode,cleanup',[('timeout','normal'),('timeout','timeout'),('timeout','late'),('timeout','signal'),('signal','normal'),('late','normal'),('nonzero','normal')])
def test_supervision_never_promotes_timeout_signal_late_or_nonzero(supervised,mode,cleanup):
    job,out,clock,Child,install=supervised;child=Child(mode,cleanup);install(child)
    result=api._run(job,out,False,deadline=200.)
    assert not result['ok'] and not result['execution_complete']
    assert all(t<=180 for t in child.calls)
    assert sum(child.calls[1:])+sum(child.waits)<=10
    assert child.kills<=1
    if mode in ('timeout','signal'):
        assert report.read_json(out/'observation_0001.json')==dict(partial='preserved')
    assert not (out/'execution.completed.json').exists()


def test_child_reaped_success_still_needs_terminal_commit(supervised):
    job,out,clock,Child,install=supervised;child=Child('normal');install(child)
    result=api._run(job,out,False,deadline=200.)
    assert result['ok'] and child.kills==0 and result['child']['reaped']
    assert not report.read_execution(out)['ok']


def test_wrong_thread_refused(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    job=make_job(tmp_path);out=tmp_path/'out'
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError,match='thread principal'):executor.submit(api.run,job,out).result()
    assert not out.exists()


@pytest.mark.parametrize('syntax',['{"schema_version":1,"schema_version":2}', 'x: &cycle [*cycle]', 'x: .nan'])
def test_duplicate_cycle_nonfinite_native_reader(tmp_path,syntax):
    job=tmp_path/('bad.json' if syntax.startswith('{') else 'bad.yaml');job.write_text(syntax)
    with pytest.raises(ValueError):api.load_inputs(job)


def test_log_close_failure_invalidates_success(supervised,monkeypatch):
    job,out,clock,Child,install=supervised;child=Child('normal');install(child)
    real_open=open
    class BadClose:
        def __init__(self,*a,**k):self.file=real_open(*a,**k)
        def __getattr__(self,name):return getattr(self.file,name)
        def close(self):self.file.close();raise OSError('controlled log close failure')
    monkeypatch.setattr(api,'open',BadClose,raising=False)
    result=api._run(job,out,False,deadline=200.)
    assert not result['ok'] and not result['execution_complete']
    assert any('controlled log close' in e for e in result['cleanup_errors'])


def test_source_snapshot_modified_refused(tmp_path,monkeypatch):
    job=make_job(tmp_path)
    monkeypatch.setattr(api,'LOADED_SOURCES',{'didgeridoo_optimizer/pipeline/adjustment_policy.py':'0'*64})
    with pytest.raises(ValueError,match='sources producteur modifiées'):api.load_inputs(job)
