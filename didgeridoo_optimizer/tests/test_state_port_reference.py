"""Real bounded CLI, strict inputs and independent offline bundle reader."""
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
from didgeridoo_optimizer.nonlinear.passive_resonator import digest
from didgeridoo_optimizer.tests.test_passive_state import relaxation, modal_pair
from didgeridoo_optimizer.pipeline import state_port_reference as workflow
from didgeridoo_optimizer.reporting import state_port_reference as report

ROOT=Path(__file__).resolve().parents[2]


def inputs(tmp_path,kind='relaxation'):
    model=relaxation() if kind=='relaxation' else modal_pair()[1]
    model_file=tmp_path/'model.json';model.save(model_file)
    signal=dict(schema=workflow.FLOW_SCHEMA,units={'flow':'m^3/s'},sample_rate_hz=12000,
        initial_condition='rest',flow_m3_s=[1e-5,-2e-5,3e-5,0.]*24,
        provenance={'kind':'synthetic explicit SI samples'})
    signal_file=tmp_path/'flow.json';signal_file.write_text(json.dumps(signal))
    return model_file,signal_file,tmp_path/'output',signal


def cli(*args,timeout=25):
    env=os.environ.copy();env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable,'-m','tools.state_port_reference',*map(str,args)],
        cwd=ROOT,env=env,capture_output=True,text=True,timeout=timeout)


def test_preflight_no_temporal_or_frequency_work_no_output(tmp_path,monkeypatch):
    model,signal,out,_=inputs(tmp_path)
    def forbidden(*a,**k):raise AssertionError('dry-run computed physics')
    for name in ('step','continuous_response','discrete_response'):
        monkeypatch.setattr(PassiveState,name,forbidden)
    result=workflow.run(model,signal,out,dry_run=True)
    assert result['status']=='preflight_only' and not out.exists()
    assert result['plan']['model_metadata']['dc']['value']==pytest.approx(250.)
    assert result['plan']['model_metadata']['feedthrough']['value']==pytest.approx(1000.)
    got=cli('--model-in',model,'--signal-in',signal,'--output-dir',out,'--dry-run')
    assert got.returncode==0,got.stderr+got.stdout
    assert not out.exists() and json.loads(got.stdout)['status']=='preflight_only'


@pytest.mark.parametrize('kind',['relaxation','modal'])
def test_real_cli_json_csv_french_summary_and_fresh_offline_reader(tmp_path,kind):
    model,signal,out,data=inputs(tmp_path,kind)
    run=cli('--model-in',model,'--signal-in',signal,'--output-dir',out,'--seconds','15')
    assert run.returncode==0,run.stdout+run.stderr
    receipt=json.loads(run.stdout);assert receipt['child']['exit_code']==0 and receipt['child']['reaped']
    bundle=report.read_result(out);p=bundle['result'];rows=p['samples']
    assert len(rows)==len(data['flow_m3_s']) and p['final_time_s']==96/12000
    assert p['initial_state']['payload']['x']==([0.] if kind=='relaxation' else [0.,0.])
    assert p['balance_error_j']==pytest.approx(0.,abs=1e-19)
    assert p['work_j']==pytest.approx(p['dissipation_j']+p['final_energy_j'],rel=3e-12)
    for i,row in enumerate(rows):
        assert row['index']==i and row['midpoint_time_s']==(i+.5)/12000
        assert row['end_time_s']==(i+1)/12000 and row['flow_m3_s']==data['flow_m3_s'][i]
    assert 'aucune validation instrumentale' in (out/'summary.md').read_text()
    assert 'didgeridoo_optimizer/nonlinear/passive_state.py' in p['provenance']['producer_sources']
    assert p['provenance']['inputs']['signal']['requested']==data
    # A fresh reader requires neither input files nor a numerical model instance.
    model.unlink();signal.unlink()
    code='from didgeridoo_optimizer.nonlinear.passive_state import PassiveState\ndef no(*a,**k):raise AssertionError("physics in reader")\nPassiveState.__init__=no\nPassiveState.step=no\nfrom tools.state_port_reference import main\nraise SystemExit(main(["--read-result",'+repr(str(out))+']))'
    read=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert read.returncode==0,read.stdout+read.stderr
    assert json.loads(read.stdout)['result']==p
    (out/'samples.csv').write_text((out/'samples.csv').read_text()+'corrupt')
    with pytest.raises(ValueError,match='fingerprint'):report.read_result(out)


def test_public_api_run_with_extra_loaded_modules(tmp_path):
    import didgeridoo_optimizer.nonlinear.regime_observables
    m,s,out,_=inputs(tmp_path)
    result=workflow.run(m,s,out,seconds=15)
    assert result['ok'],result
    assert report.read_result(out)['ok']


def test_prescribed_flow_leaves_input_state_and_checks_rest():
    m=relaxation();m.step(.002);before=m.export_state()
    result=workflow.prescribed_flow(m,[1e-5]*80,initial_condition='rest')
    assert result['status']=='resolved' and m.export_state()==before
    assert result['initial_state']['payload']['x']==[0.]
    with pytest.raises(ValueError):workflow.prescribed_flow(m,[0],initial_condition='unknown')
    for flow in ([0.]*2401,[True],[1e-5]*4097):
        with pytest.raises(ValueError):workflow.prescribed_flow(m,flow,initial_condition='rest')
    low=relaxation(1000)
    with pytest.raises(ValueError):workflow.prescribed_flow(low,[0.]*201,initial_condition='rest')


def test_partial_retains_only_accepted_steps_and_final_state():
    m=relaxation()
    p=workflow.prescribed_flow(m,[1e-5,1e308,0.],initial_condition='rest')
    assert p['status']=='partial' and len(p['samples'])==1
    assert p['final_time_s']==1/12000 and p['reason']
    fresh=relaxation();fresh.step(1e-5)
    assert p['final_state']==fresh.export_state()


@pytest.mark.parametrize('mutation',['fs','units','rest','budget','unknown','bool','duplicate','nonfinite'])
def test_strict_signal_preflight_refuses_without_output(tmp_path,mutation):
    model,signal,out,data=inputs(tmp_path)
    if mutation=='fs':data['sample_rate_hz']=4000
    if mutation=='units':data['units']['flow']='L/s'
    if mutation=='rest':data['initial_condition']='default'
    if mutation=='budget':data['flow_m3_s']=[0.]*2401
    if mutation=='unknown':data['code']='pass'
    if mutation=='bool':data['flow_m3_s'][0]=True
    text=json.dumps(data)
    if mutation=='duplicate':text=text.replace('"sample_rate_hz": 12000','"sample_rate_hz": 12000, "sample_rate_hz": 12000')
    if mutation=='nonfinite':text=text.replace('1e-05','NaN',1)
    signal.write_text(text)
    with pytest.raises(ValueError):workflow.preflight(model,signal,out)
    assert not out.exists()


def test_timeout_child_reaped_and_existing_output_refused(tmp_path):
    m,s,out,_=inputs(tmp_path)
    r=workflow.run(m,s,out,seconds=.001)
    assert not r['ok'] and r['child']['reaped'] and r['child']['exit_code'] is not None
    assert (out/'execution.json').is_file() and (out/'stderr.txt').is_file()
    with pytest.raises(FileExistsError):workflow.run(m,s,out)


def test_worker_refuses_input_altered_since_plan(tmp_path):
    m,s,out,_=inputs(tmp_path);plan=workflow.preflight(m,s,out)
    s.write_text(s.read_text()+' ');out.mkdir()
    assert workflow.worker(plan)==1
    assert 'changed' in report.read_json(out/'failure.json')['error']
    assert not (out/'result.json').exists()


def test_reader_refuses_false_success_receipt(tmp_path):
    m,s,out,_=inputs(tmp_path);assert workflow.run(m,s,out,seconds=15)['ok']
    receipt=report.read_json(out/'execution.json');receipt['child']['exit_code']=1
    (out/'execution.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError):report.read_result(out)


def test_altered_model_still_returns_coherent_partial(tmp_path):
    model,_,_,_=inputs(tmp_path);m=PassiveState.load(model)
    def alter(p):
        if len(p['samples'])==64:model.write_text(model.read_text()+' ')
    result=workflow.prescribed_flow(m,[1e-5]*80,initial_condition='rest',progress=alter)
    assert result['status']=='partial' and len(result['samples'])==64
    assert result['final_time_s']==64/12000 and 'changed' in result['reason']
    assert result['final_energy_j']==result['samples'][-1]['energy_j']


def test_cancelled_parent_collects_owned_child(tmp_path):
    import signal
    model,signal_file,out,_=inputs(tmp_path)
    script='''
import json, pathlib, signal, subprocess, sys
from didgeridoo_optimizer.pipeline import state_port_reference as w
native=w.subprocess.Popen
pidfile=pathlib.Path(sys.argv[4])
def slow(*args,**kw):
    child=native([sys.executable,'-c','import time; time.sleep(20)'],**kw)
    pidfile.write_text(str(child.pid))
    import os
    os.kill(os.getpid(),signal.SIGTERM)
    return child
w.subprocess.Popen=slow
result=w.run(sys.argv[1],sys.argv[2],sys.argv[3],seconds=15)
print(json.dumps(result))
raise SystemExit(0 if result['ok'] else 1)
'''
    pidfile=tmp_path/'pid'
    child=subprocess.run([sys.executable,'-c',script,str(model),str(signal_file),str(out),str(pidfile)],
        cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert child.returncode==1,child.stdout+child.stderr
    result=json.loads(child.stdout)
    assert not result['ok'] and result['child']['reaped'] and result['child']['exit_code']==-signal.SIGTERM
    assert result['child']['pid']==int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):os.kill(result['child']['pid'],0)
    assert (out/'execution.closed.json').is_file() and (out/'execution.cancelled.json').is_file()


@pytest.mark.parametrize('mutation',['incomplete','bool_count','row_bool','row_string','time','state','summary'])
def test_offline_reader_semantics_even_with_recomputed_hashes(tmp_path,mutation):
    m,s,out,_=inputs(tmp_path);assert workflow.run(m,s,out,seconds=15)['ok']
    p=report.read_result(out)['result']
    if mutation=='incomplete':p['samples'].pop()
    if mutation=='bool_count':p['requested_steps']=True
    if mutation=='row_bool':p['samples'][0]['pressure_pa']=True
    if mutation=='row_string':p['samples'][0]['flow_m3_s']='1e-5'
    if mutation=='time':p['samples'][0]['midpoint_time_s']=0.
    if mutation=='state':p['final_state']['payload']['x'].append(0.);p['final_state']['sha256']=digest(p['final_state']['payload'])
    if mutation=='summary':p['final_energy_j']+=1.
    # Rebuild all artifact hashes: semantics, not merely stale hashes, must reject.
    import hashlib
    texts={'result.json':report.strict_json(dict(payload=p,sha256=digest(p))),
           'samples.csv':report.csv_text(p),'summary.md':report.summary(p)}
    for name,text in texts.items():(out/name).write_text(text)
    manifest={'schema':report.SCHEMA,'files':{name:hashlib.sha256(text.encode()).hexdigest() for name,text in texts.items()}}
    (out/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):report.read_payload(out)


@pytest.mark.parametrize('stage',['before','after'])
def test_closure_failure_never_leaves_readable_success_and_restores_handlers(tmp_path,monkeypatch,stage):
    import signal
    m,s,out,_=inputs(tmp_path);native=report.atomic_bytes
    old={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM)}
    def fail(path,raw,**kw):
        if Path(path).name=='execution.closed.json':
            if stage=='after':native(path,raw,**kw)
            raise OSError('injected closure fsync failure')
        return native(path,raw,**kw)
    monkeypatch.setattr(report,'atomic_bytes',fail)
    with pytest.raises(OSError,match='closure'):workflow.run(m,s,out,seconds=15)
    assert {sig:signal.getsignal(sig) for sig in old}==old
    if stage=='after':assert report.read_result(out)['ok'] is False
    else:
        with pytest.raises((ValueError,OSError)):report.read_result(out)


def test_offline_reader_refuses_dangling_cancellation_marker(tmp_path):
    m,s,out,_=inputs(tmp_path);assert workflow.run(m,s,out,seconds=15)['ok']
    (out/'execution.cancelled.json').symlink_to(out/'absent.json')
    with pytest.raises(ValueError,match='Symbolic'):report.read_result(out)


# R57 correction regressions (original tests above preserved byte-for-byte).
@pytest.mark.parametrize('mode',['stuck','late_channels','late_wait'])
def test_correction_collection_has_only_finite_waits(tmp_path,monkeypatch,mode):
    import signal
    m,s,out,_=inputs(tmp_path)
    clock=[100.];waits=[]
    monkeypatch.setattr(workflow.time,'monotonic',lambda:clock[0])
    class Child:
        pid=12345;returncode=None;stdin=stdout=stderr=None
        def poll(self):return self.returncode
        def terminate(self):pass
        def kill(self):pass
        def wait(self,timeout=None):
            assert timeout is not None and 0<timeout<=2.,'unbounded wait'
            waits.append(timeout)
            if mode=='late_wait' and len(waits)>1:
                clock[0]+=timeout+1.;self.returncode=0;return 0
            raise subprocess.TimeoutExpired('double',timeout)
        def communicate(self,timeout=None):
            assert timeout is not None and 0<timeout<=2.
            if mode=='late_channels':
                clock[0]+=timeout+1.;self.returncode=0;return (None,None)
            raise subprocess.TimeoutExpired('double channels',timeout)
    monkeypatch.setattr(workflow.subprocess,'Popen',lambda *a,**k:Child())
    result=workflow._run(m,s,out,seconds=15,cancelled=[signal.SIGINT])
    assert not result['ok'] and not result['child']['reaped']
    assert waits and all(t is not None for t in waits)
    assert (out/'stdout.txt').exists() and (out/'execution.json').exists()


def test_correction_public_budget_includes_preflight(tmp_path,monkeypatch):
    m,s,out,_=inputs(tmp_path);clock=[100.];native=workflow.preflight
    monkeypatch.setattr(workflow.time,'monotonic',lambda:clock[0])
    def delayed(*a,**k):
        plan=native(*a,**k);clock[0]+=21.;return plan
    monkeypatch.setattr(workflow,'preflight',delayed)
    with pytest.raises(TimeoutError):workflow.run(m,s,out,seconds=15,dry_run=True)
    assert not out.exists()


@pytest.mark.parametrize('kind',['blas','memory','cpu','file'])
def test_correction_worker_checks_actual_limits_without_applying_them(monkeypatch,kind):
    import resource
    real_limits=resource.getrlimit
    limits={resource.RLIMIT_AS:(768*1024**2,)*2,resource.RLIMIT_CPU:(175,)*2,
            resource.RLIMIT_FSIZE:(4*1024**2,)*2}
    if kind!='blas':
        limits[{'memory':resource.RLIMIT_AS,'cpu':resource.RLIMIT_CPU,'file':resource.RLIMIT_FSIZE}[kind]]=(-1,-1)
    else:monkeypatch.setenv('OMP_NUM_THREADS','2')
    monkeypatch.setattr(resource,'getrlimit',lambda key:limits[key])
    def forbidden(*a):raise AssertionError('worker limits applied to caller')
    monkeypatch.setattr(resource,'setrlimit',forbidden)
    with pytest.raises(ValueError,match='requires'):workflow.worker_ready()


def test_correction_direct_worker_cli_refuses_before_output(tmp_path):
    from tools.state_port_reference import main
    m,s,out,_=inputs(tmp_path);plan=workflow.preflight(m,s,out)
    planfile=tmp_path/'plan.json';planfile.write_text(json.dumps(plan))
    out.mkdir()
    assert main(['--worker',str(planfile)])==1
    assert list(out.iterdir())==[]


@pytest.mark.parametrize('boundary',['wait','collect','close'])
def test_correction_signal_at_each_boundary(tmp_path,monkeypatch,boundary):
    import signal
    m,s,out,_=inputs(tmp_path)
    if boundary=='close':
        native=report.atomic_bytes
        def writing(path,raw,**kw):
            result=native(path,raw,**kw)
            if Path(path).name=='execution.closed.json':os.kill(os.getpid(),signal.SIGINT)
            return result
        monkeypatch.setattr(report,'atomic_bytes',writing)
        result=workflow.run(m,s,out,seconds=15)
        assert not result['ok'] and not report.read_result(out)['ok']
    else:
        class Child:
            pid=12345;returncode=None;stdin=stdout=stderr=None
            def poll(self):return self.returncode
            def terminate(self):self.returncode=-signal.SIGTERM
            def kill(self):self.returncode=-signal.SIGKILL
            def wait(self,timeout=None):
                assert timeout is not None
                if boundary=='wait':os.kill(os.getpid(),signal.SIGINT)
                self.returncode=0;return 0
            def communicate(self,timeout=None):
                assert timeout is not None
                if boundary=='collect':os.kill(os.getpid(),signal.SIGINT)
                self.returncode=0;return None,None
        monkeypatch.setattr(workflow.subprocess,'Popen',lambda *a,**k:Child())
        result=workflow.run(m,s,out,seconds=15)
        assert not result['ok'] and (out/'execution.cancelled.json').exists()


@pytest.mark.parametrize('changed',['model','pipeline','reader','cli'])
def test_correction_import_snapshot_precedes_workflow(tmp_path,changed):
    import shutil
    private=tmp_path/'copy';private.mkdir()
    shutil.copytree(ROOT/'didgeridoo_optimizer',private/'didgeridoo_optimizer',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'tools',private/'tools',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'project_specs/examples/state_port',private/'examples')
    modules={'model':'didgeridoo_optimizer.nonlinear.passive_state',
             'pipeline':'didgeridoo_optimizer.pipeline.state_port_reference',
             'reader':'didgeridoo_optimizer.reporting.state_port_reference',
             'cli':'tools.state_port_reference'}
    code='''
import importlib,json,pathlib,sys
kind,name=sys.argv[1:]
module=importlib.import_module(name)
path=pathlib.Path(module.__file__)
original=path.read_text()
path.write_text(original.replace('MAX_STATES = 128','MAX_STATES = 127') if kind=='model' else original+'\\n# altered after import\\n')
try:
 if kind in ('model','pipeline'):
  from didgeridoo_optimizer.pipeline.state_port_reference import preflight
  preflight('examples/modal.json','examples/flow.json','output')
 elif kind=='reader':module.read_result('missing')
 else:
  code=module.main(['--model-in','examples/modal.json','--signal-in','examples/flow.json','--output-dir','output','--dry-run'])
  assert code==1
  raise SystemExit(0)
except ValueError as exc:
 assert 'source' in str(exc).lower() and ('changed' in str(exc).lower() or 'import' in str(exc).lower()),str(exc)
 print(json.dumps({'refused':str(exc)}));raise SystemExit(0)
raise AssertionError('stale loaded source accepted')
'''
    env=dict(os.environ,PYTHONPATH=str(private),PYTHONDONTWRITEBYTECODE='1')
    child=subprocess.run([sys.executable,'-B','-c',code,changed,modules[changed]],cwd=private,
                         env=env,capture_output=True,text=True,timeout=15)
    assert child.returncode==0,child.stdout+child.stderr


def test_correction_reader_does_not_import_producer(tmp_path):
    code='import sys;from didgeridoo_optimizer.reporting import state_port_reference;assert "didgeridoo_optimizer.pipeline.state_port_reference" not in sys.modules'
    child=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert child.returncode==0,child.stdout+child.stderr


def test_correction_late_closure_revokes_success(tmp_path,monkeypatch):
    m,s,out,_=inputs(tmp_path);native=report.atomic_bytes
    now=workflow.time.monotonic
    def late(path,raw,**kw):
        result=native(path,raw,**kw)
        if Path(path).name=='execution.closed.json':
            monkeypatch.setattr(workflow.time,'monotonic',lambda:now()+100.)
        return result
    monkeypatch.setattr(report,'atomic_bytes',late)
    with pytest.raises(TimeoutError):workflow.run(m,s,out,seconds=15)
    assert not (out/'execution.closed.json').exists()
    assert (out/'result.json').exists()
    with pytest.raises(ValueError):report.read_result(out)


@pytest.mark.parametrize('changed',['model','pipeline','reader','cli'])
@pytest.mark.parametrize('boundary',['api','closure'])
def test_correction_source_change_during_workflow(tmp_path,changed,boundary):
    import shutil
    private=tmp_path/'copy';private.mkdir()
    for directory in ('didgeridoo_optimizer','tools'):
        shutil.copytree(ROOT/directory,private/directory,ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'project_specs/examples/state_port',private/'examples')
    code='''
import json,pathlib,sys
from didgeridoo_optimizer.nonlinear import passive_state as model
from didgeridoo_optimizer.pipeline import state_port_reference as pipeline
from didgeridoo_optimizer.reporting import state_port_reference as reader
from tools import state_port_reference as cli
changed,boundary=sys.argv[1:]
module={'model':model,'pipeline':pipeline,'reader':reader,'cli':cli}[changed]
path=pathlib.Path(module.__file__)
def alter():path.write_text(path.read_text()+'\\n# changed while running\\n')
if boundary=='api':
 m=model.PassiveState.load('examples/modal.json')
 def progress(p):
  if len(p['samples'])==64:alter()
 result=pipeline.prescribed_flow(m,[1e-5]*80,initial_condition='rest',progress=progress)
 assert result['status']=='partial' and 'source' in result['reason'].lower(),result
else:
 native=reader.atomic_bytes
 def writing(p,raw,**kw):
  result=native(p,raw,**kw)
  if pathlib.Path(p).name=='execution.closed.json':alter()
  return result
 reader.atomic_bytes=writing
 code=cli.main(['--model-in','examples/modal.json','--signal-in','examples/flow.json','--output-dir','output','--seconds','15'])
 assert code==1
 # A fresh reader loads the modified private source as its own identity;
 # relative integrity alone cannot authenticate wholesale replacement.
 import subprocess
 fresh=subprocess.run([sys.executable,'-B','-c',
  "from didgeridoo_optimizer.reporting.state_port_reference import read_result;assert not read_result('output')['ok']"],
  capture_output=True,text=True,timeout=10)
 assert fresh.returncode==0,fresh.stdout+fresh.stderr
print(json.dumps({'refused':True,'case':changed,'boundary':boundary}))
'''
    child=subprocess.run([sys.executable,'-B','-c',code,changed,boundary],cwd=private,
        env=dict(os.environ,PYTHONPATH=str(private),PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stdout+child.stderr


def test_correction_late_compute_acknowledgement_not_reaped(tmp_path,monkeypatch):
    m,s,out,_=inputs(tmp_path);clock=[100.]
    monkeypatch.setattr(workflow.time,'monotonic',lambda:clock[0])
    class Child:
        pid=12345;returncode=None;stdin=stdout=stderr=None
        def poll(self):return self.returncode
        def wait(self,timeout=None):
            assert timeout is not None
            clock[0]+=15.01;self.returncode=0;return 0
        def communicate(self,timeout=None):
            assert timeout is not None
            return None,None
    monkeypatch.setattr(workflow.subprocess,'Popen',lambda *a,**k:Child())
    result=workflow._run(m,s,out,seconds=15,cancelled=[])
    assert not result['ok'] and not result['child']['reaped']


def test_correction_model_remembers_blas_before_pipeline():
    code='''
import os,resource
os.environ['OMP_NUM_THREADS']='2'
from didgeridoo_optimizer.nonlinear import passive_state
os.environ['OMP_NUM_THREADS']='1'
from didgeridoo_optimizer.pipeline import state_port_reference as w
limits={resource.RLIMIT_AS:(768*1024**2,)*2,resource.RLIMIT_CPU:(175,)*2,resource.RLIMIT_FSIZE:(4*1024**2,)*2}
resource.getrlimit=lambda k:limits[k]
try:w.worker_ready()
except ValueError as exc:
 assert 'BLAS1' in str(exc)
else:raise AssertionError('model loaded before BLAS1 accepted')
'''
    child=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert child.returncode==0,child.stdout+child.stderr
