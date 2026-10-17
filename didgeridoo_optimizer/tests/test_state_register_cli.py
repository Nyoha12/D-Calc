"""Real saved R61 B CLI; finite acoustic acquisition, no fitting or conversion."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear import register_target as core
from didgeridoo_optimizer.nonlinear.passive_resonator import digest
from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
from didgeridoo_optimizer.pipeline import register_target as workflow
from didgeridoo_optimizer.reporting import register_target as report
from didgeridoo_optimizer.reporting import state_register_context as evidence
from didgeridoo_optimizer.reporting import passive_realization
from didgeridoo_optimizer.tests.test_register_target import request

ROOT=Path(__file__).resolve().parents[2]
FIXTURE=Path(__file__).parent/'fixtures'/'state_register'
ENV={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}


def saved_plan(path, *, fixture=FIXTURE):
    r=request(fs=12000,steps=96)
    e=report.read_json(fixture/'evidence.json')
    r['case']=dict(kind='saved_state',id='B',config=str(fixture/'config.json'),design=str(fixture/'design.json'),
                   realization=str(fixture/'realization-B'),evidence=str(fixture/'evidence.json'),recipe=e['recipe'])
    r['rho_kg_m3']=e['effective']['air']['rho'];r['observation'].update(frame_steps=4000,hop_steps=2000)
    r['plateaus'][0].update(steps=48,duration_s=48/12000)
    r['plateaus'].append(dict(r['plateaus'][0],id='b',pressure_pa=1700.,resonance_hz=100.))
    r['windows'][0]['stop_step']=48
    r['criteria']=[dict(id='pitch',role='hard',observable='played_frequency',windows=['hold'],
        target=dict(value=70.,unit='Hz'),tolerance=dict(value=5.,unit='cent'))]
    r['budgets']['chunk_steps']=48
    path.write_text(json.dumps(r));return r


def cli(path,out,*args):
    return subprocess.run([sys.executable,'-B','-m','tools.register_target','--plan',str(path),'--output-dir',str(out),*args],
        cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=90)


@pytest.fixture(scope='module')
def acquired(tmp_path_factory):
    root=tmp_path_factory.mktemp('state-acquired');path=root/'plan.json';r=saved_plan(path)
    whole=cli(path,root/'full');assert whole.returncode==0,whole.stdout+whole.stderr
    partial=cli(path,root/'partial','--steps-this-run','48');assert partial.returncode==1,partial.stdout+partial.stderr
    cp=report.read_json(root/'partial'/'result.json')['checkpoint']['path']
    resume=cli(path,root/'resume','--resume',cp);assert resume.returncode==0,resume.stdout+resume.stderr
    return root,path,r


def test_real_cli_complete_partial_resume_bitwise_and_fresh_reader(acquired):
    root,path,r=acquired
    full=report.read_result(root/'full');resumed=report.read_result(root/'resume')
    assert full['ok'],full
    assert resumed['ok'],resumed
    a=report.load_series(report.inspect_checkpoint(full['result']['checkpoint']['path']))
    b=report.load_series(report.inspect_checkpoint(resumed['result']['checkpoint']['path']))
    assert a['states'].shape==(97,108)
    for key in a:assert np.array_equal(a[key],b[key]),key
    assert full['result']['events']==resumed['result']['events']
    assert resumed['result']['new_steps_this_run']==48 and resumed['result']['new_duration_s']==48/12000
    assert full['result']['analysis']['criteria'][0]['status']=='unresolved'
    assert full['result']['context_checks']['finite_fidelity']['bands'][-1]['admitted'] is False
    code='''import sys,json
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
from didgeridoo_optimizer.nonlinear import passive_fit
from didgeridoo_optimizer.acoustics import transfer_matrix
def no(*a,**k): raise AssertionError("no step/fit/TMM in reader")
SimultaneousCoupling.step=no
PassiveState.step=no
passive_fit.fit_passive=no
transfer_matrix.input_impedance=no
from didgeridoo_optimizer.reporting.register_target import read_result
r=read_result(sys.argv[1]);print(json.dumps(r));assert r['ok'],r
'''
    fresh=subprocess.run([sys.executable,'-B','-c',code,str(root/'full')],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=90)
    assert fresh.returncode==0,fresh.stdout+fresh.stderr


def test_dryrun_no_science_or_destination(tmp_path,monkeypatch):
    path=tmp_path/'plan.json';saved_plan(path);out=tmp_path/'none'
    def no(*a,**k):pytest.fail('Scientific computation or destination during preflight')
    for name in ('__init__','load','step','discrete_response'):monkeypatch.setattr(PassiveState,name,no)
    monkeypatch.setattr(np.linalg,'eigvals',no);monkeypatch.setattr(np,'load',no)
    monkeypatch.setattr(passive_realization,'read_result',no);monkeypatch.setattr(report,'write_json',no)
    p=workflow.run(path,out,dry_run=True)
    assert p['ok'] and p['plan']['metadata']['states']==108 and not out.exists()


@pytest.mark.parametrize('mutation',['fs','rho','h','observation','phase_units'])
def test_bad_context_before_model(tmp_path,monkeypatch,mutation):
    path=tmp_path/'plan.json';r=saved_plan(path)
    if mutation=='fs':r['case']['recipe']['sample_rate_hz']=10000
    if mutation=='rho':r['rho_kg_m3']=1.204
    if mutation=='h':r['case']['recipe']['h_cm']=1.
    if mutation=='observation':r['observation']['band_hz']=[.01,400.]
    if mutation=='phase_units':
        r['observation']['phase']=dict(train=[0.,.002],validations=[[.002,.008]],scales=[1.]*108,state_units=['m']*108,
            section_index=0,section_level=0.,section_method='linear',groups=[1,2],min_crossings=8)
    path.write_text(json.dumps(r))
    monkeypatch.setattr(PassiveState,'load',lambda *a,**k:pytest.fail('Too late'))
    with pytest.raises(ValueError):workflow.preflight(path,tmp_path/'none')
    assert not (tmp_path/'none').exists()


def reclose(out):
    manifest=report.read_json(out/'manifest.json')
    for name in manifest:manifest[name]=report.file_sha256(out/name)
    (out/'manifest.json').write_text(json.dumps(manifest))
    execution=report.read_json(out/'execution.json');execution['manifest_sha256']=report.file_sha256(out/'manifest.json')
    (out/'execution.json').write_text(json.dumps(execution))
    (out/'execution.closed.json').write_text(json.dumps(dict(execution_sha256=report.file_sha256(out/'execution.json'))))
    (out/'execution.completed.json').write_text(json.dumps(dict(schema='dcalc.register_target.completion.v1',
        execution_sha256=report.file_sha256(out/'execution.json'),closure_sha256=report.file_sha256(out/'execution.closed.json'))))


@pytest.mark.parametrize('mutation',['criterion','observation','checks','table'])
def test_semantic_tampering_rehashed_is_refused(acquired,tmp_path,mutation):
    root,_,_=acquired;out=tmp_path/'altered';shutil.copytree(root/'full',out)
    if mutation=='observation':
        path=out/'window-hold.json';v=report.read_json(path);v['frequency_hz']=70.;path.write_text(json.dumps(v))
    elif mutation=='table':(out/'criteria.csv').write_text('forged\n')
    else:
        path=out/'result.json';v=report.read_json(path)
        if mutation=='criterion':v['analysis']['criteria'][0]['status']='pass'
        else:v['context_checks']['finite_fidelity']['bands'][-1]['admitted']=True
        path.write_text(json.dumps(v))
    reclose(out);result=report.read_result(out)
    assert not result['ok'] and ('differ' in result['reason'] or 'mismatch' in result['reason']),result


def test_changed_plan_refuses_resume_without_destination(acquired,tmp_path):
    root,path,r=acquired;changed=tmp_path/'plan.json';v=copy.deepcopy(r);v['criteria'][0]['target']['value']=71.;changed.write_text(json.dumps(v))
    cp=report.read_json(root/'partial'/'result.json')['checkpoint']['path']
    before=Path(cp).read_bytes()
    with pytest.raises(ValueError):workflow.preflight(changed,tmp_path/'none',resume=cp)
    assert Path(cp).read_bytes()==before and not (tmp_path/'none').exists()


def test_changed_source_after_import_is_refused_in_isolated_copy(tmp_path):
    # Copy only Python product files: no source mutation in the actual checkout.
    checkout=tmp_path/'checkout'
    shutil.copytree(ROOT/'didgeridoo_optimizer',checkout/'didgeridoo_optimizer',ignore=shutil.ignore_patterns('fixtures','__pycache__'))
    shutil.copytree(ROOT/'tools',checkout/'tools',ignore=shutil.ignore_patterns('__pycache__'))
    code='''from pathlib import Path
from didgeridoo_optimizer.pipeline import register_target as w
p=Path('didgeridoo_optimizer/reporting/state_register_context.py')
p.write_text(p.read_text()+'\\n# changed after import\\n')
try: w.report.verify_sources(w.LOADED_SOURCES)
except ValueError: pass
else: raise AssertionError('imported source change accepted')
'''
    p=subprocess.run([sys.executable,'-B','-c',code],cwd=checkout,env={**ENV,'PYTHONPATH':str(checkout)},capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stdout+p.stderr


def _reclose_realization(bundle):
    from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as certs
    manifest=report.read_json(bundle/'manifest.json')
    manifest['files']={name:report.file_sha256(bundle/name) for name in manifest['files']}
    (bundle/'manifest.json').write_text(certs.strict_json(manifest))
    receipt=report.read_json(bundle/'execution.json')
    receipt['manifest_sha256']=report.file_sha256(bundle/'manifest.json')
    (bundle/'execution.json').write_text(certs.strict_json(receipt))
    closed=report.read_json(bundle/'execution.closed.json')
    closed['execution_sha256']=report.file_sha256(bundle/'execution.json')
    (bundle/'execution.closed.json').write_text(certs.strict_json(closed))


@pytest.mark.parametrize('mutation',['certificate','witness'])
def test_rehashed_false_r61_proof_refused_before_native_step(tmp_path,monkeypatch,mutation):
    from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as certs
    path=tmp_path/'plan.json';r=saved_plan(path)
    bundle=tmp_path/'realization';shutil.copytree(FIXTURE/'realization-B',bundle)
    r['case']['realization']=str(bundle);path.write_text(json.dumps(r))
    if mutation=='certificate':
        cert=report.read_json(bundle/'certificate.json')
        cert['leaves'][0]['lower_units']+=1  # Still well-typed, wrong exact enclosure.
        (bundle/'certificate.json').write_text(certs.strict_json(cert))
        sha=report.file_sha256(bundle/'certificate.json')
        result=report.read_json(bundle/'result.json');result['certificate_sha256']=sha
        (bundle/'result.json').write_text(certs.strict_json(result))
        plan=report.read_json(bundle/'plan.json');plan['certificate']['sha256']=sha
        (bundle/'plan.json').write_text(certs.strict_json(plan))
    else:
        witness=report.read_json(bundle/'witness.json');witness['T'][0][0]+=1.
        (bundle/'witness.json').write_text(certs.strict_json(witness))
    _reclose_realization(bundle)
    p=workflow.preflight(path,tmp_path/'unused').as_dict()  # Shape/hash inspection only.
    monkeypatch.setattr(PassiveState,'step',lambda *a,**k:pytest.fail('Step before proof refusal'))
    with pytest.raises(ValueError,match='enclosure|representation identity'):
        workflow.experiment_for(p)
    assert not (tmp_path/'unused').exists()


@pytest.mark.parametrize('role',['config','design','materials','variant_rules','bundle','evidence'])
def test_changed_actual_file_guard_before_next_step(tmp_path,role):
    from didgeridoo_optimizer.pipeline.state_register_context import context_guard
    from didgeridoo_optimizer.tests.test_state_register import state_experiment
    path=tmp_path/'plan.json';saved_plan(path)
    p=workflow.preflight(path,tmp_path/'unused').as_dict()
    metadata=copy.deepcopy(p['metadata']);inputs=copy.deepcopy(p['input_files'])
    if role=='bundle': record=metadata['bundle_files']['source.json']
    elif role=='evidence': record=metadata['evidence_files']['manifest']
    else:record=inputs[role]
    local=tmp_path/(role+'.snapshot');local.write_bytes(Path(record['path']).read_bytes())
    record['path']=str(local)
    guard=context_guard(metadata,inputs)
    e=state_experiment();e._context_guard=guard
    assert e.advance(1)['ok'];accepted=e.checkpoint();original=local.read_bytes()
    def edit_between_steps():
        local.write_bytes(original+b'\n')
        return False
    with pytest.raises(ValueError,match='changed before native step'):
        e.advance(1,stop=edit_between_steps)
    assert e.checkpoint()==accepted and e.accepted==1


@pytest.mark.parametrize('mode',['late_return','deadline','uncollected'])
def test_saved_state_process_double_never_reports_late_success(tmp_path,monkeypatch,mode):
    from didgeridoo_optimizer.pipeline import tolerance_audit
    clock=[0.];calls=[];prepared=[]
    class Child:
        returncode=0 if mode!='deadline' else None
        stdin=stdout=stderr=None
        pid=12345
        def poll(self):return self.returncode
        def wait(self,timeout=None):
            assert timeout is not None and 0 < timeout <= 2.
            calls.append(('wait',timeout));clock[0]+=61.
            raise subprocess.TimeoutExpired('fake',timeout)
        def kill(self):self.returncode=-9;calls.append(('kill',))
        def communicate(self,timeout=None):
            assert timeout is not None and timeout>0
            calls.append(('communicate',timeout))
            if mode=='uncollected':raise subprocess.TimeoutExpired('fake channels',timeout)
            if mode=='late_return':clock[0]+=timeout+1e-6
            self.returncode=0
            return None,None
    child=Child()
    def popen(*args,**kwargs):
        assert 'stdin' not in kwargs and kwargs['stdout'] is not subprocess.PIPE
        assert '--worker-plan' in args[0]
        kwargs['stdout'].write((json.dumps(dict(ok=True,status='complete',reason=None))+'\n').encode())
        kwargs['stdout'].flush()
        return child
    monkeypatch.setattr(workflow.subprocess,'Popen',popen)
    monkeypatch.setattr(workflow.time,'monotonic',lambda:clock[0])
    # Both modules share Python's time module; explicit patch documents collection clock.
    monkeypatch.setattr(tolerance_audit.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(workflow,'check_inputs',lambda p:None)
    monkeypatch.setattr(report,'prepare_completion',lambda out,receipt,limit:prepared.append(receipt))
    (tmp_path/'result.json').write_text('{}')
    p=dict(request=dict(budgets=dict(child_seconds=60.)))
    result=workflow._run_state_child(p,tmp_path,1024**2)
    assert result['ok'] is False and prepared[0]['ok'] is False
    assert result['reason'] in ('late_child_collection','child_wall_budget','child_collection_unconfirmed','child_failed')
    assert not (tmp_path/'execution.completed.json').exists()
    assert calls and all(c[1]>0 for c in calls if c[0] in ('wait','communicate'))


def test_saved_state_semantic_rows_are_verified_without_replaying(acquired):
    root,_,_=acquired
    result=report.read_json(root/'full'/'result.json')
    chain=report.inspect_checkpoint(result['checkpoint']['path'])
    arrays=report.load_series(chain)
    experiment=workflow.experiment_for(result['plan'])
    for key,index in [('states',(10,2)),('values',(10,core.COLUMNS.index('jet_m3_s')))]:
        altered={k:v.copy() for k,v in arrays.items()}
        altered[key][index]+=.001
        with pytest.raises(ValueError,match='Saved native'):
            evidence.verify_saved_analysis(root/'full',result,altered,experiment,chain)


@pytest.mark.parametrize('nature',['measurement','synthetic'])
def test_known_r59_tmm_cannot_be_relabelled(tmp_path,monkeypatch,nature):
    path=tmp_path/'plan.json';r=saved_plan(path)
    for name in ('training.npz','audit.npz'):shutil.copyfile(FIXTURE/name,tmp_path/name)
    m=report.read_json(FIXTURE/'evidence.json');m['acquisition']['nature']=nature
    proof=tmp_path/'evidence.json';proof.write_text(json.dumps(m));r['case']['evidence']=str(proof)
    path.write_text(json.dumps(r))
    monkeypatch.setattr(PassiveState,'load',lambda *a,**k:pytest.fail('Late provenance refusal'))
    with pytest.raises(ValueError,match='cannot be relabelled'):workflow.preflight(path,tmp_path/'none')
    assert not (tmp_path/'none').exists()


def test_collection_helper_is_pinned_before_lazy_import(tmp_path):
    path=tmp_path/'plan.json';saved_plan(path)
    p=workflow.preflight(path,tmp_path/'none').as_dict()
    name='didgeridoo_optimizer/pipeline/tolerance_audit.py'
    assert name in report.REQUIRED_SOURCES
    assert p['producer']['loaded_sources_sha256'][name]==report.file_sha256(ROOT/name)


@pytest.mark.parametrize('fault',[
    'none','capture_missing','capture_bad','current_blas',
    'as_unbounded','cpu_unbounded','fsize_unbounded',
    'as_soft_high','as_hard_high','cpu_soft_high','cpu_hard_high',
    'fsize_soft_high','fsize_hard_high'])
def test_state_worker_checks_actual_environment_before_calculate(tmp_path,monkeypatch,fault):
    import resource
    import signal
    snapshot={key:'1' for key in workflow._STATE_BLAS_KEYS}
    for key in snapshot:monkeypatch.setenv(key,'1')
    caps={resource.RLIMIT_AS:768*1024**2,resource.RLIMIT_CPU:175,resource.RLIMIT_FSIZE:8*1024**2}
    limits={key:(value,value) for key,value in caps.items()}
    if fault=='capture_missing':snapshot=None
    elif fault=='capture_bad':snapshot['OPENBLAS_NUM_THREADS']='2'
    elif fault=='current_blas':monkeypatch.setenv('OMP_NUM_THREADS','2')
    elif fault!='none':
        key,kind=fault.split('_',1)
        key={'as':resource.RLIMIT_AS,'cpu':resource.RLIMIT_CPU,'fsize':resource.RLIMIT_FSIZE}[key]
        cap=caps[key]
        limits[key]={'unbounded':(-1,-1),'soft_high':(cap+1,cap+1),'hard_high':(cap,cap+1)}[kind]
    monkeypatch.setattr(workflow.resource,'getrlimit',lambda key:limits[key])
    monkeypatch.setattr(workflow.resource,'setrlimit',lambda *a:pytest.fail('Worker mutated caller limits'))
    calls=[]
    def calculate(p,**kw):
        calls.append(p)
        return dict(ok=True,status='complete',reason=None)
    monkeypatch.setattr(workflow,'calculate',calculate)
    before={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM)}
    plan=dict(request=dict(case=dict(kind='saved_state')),output=str(tmp_path/'none'))
    if fault=='none':
        assert workflow.worker(dict(plan=plan),_blas_before_import=snapshot)['ok']
        assert calls==[plan]
    else:
        monkeypatch.setattr(workflow.signal,'signal',lambda *a:pytest.fail('Handler before readiness'))
        with pytest.raises(ValueError,match='State worker requires'):
            workflow.worker(dict(plan=plan),_blas_before_import=snapshot)
        assert calls==[]
    assert {sig:signal.getsignal(sig) for sig in before}==before
    assert not (tmp_path/'none').exists()


def test_modal_worker_keeps_historical_path(monkeypatch):
    monkeypatch.setattr(workflow,'_state_worker_ready',lambda *a:pytest.fail('State guard on modal worker'))
    monkeypatch.setattr(workflow,'calculate',lambda *a,**kw:dict(ok=True,status='complete'))
    assert workflow.worker(dict(plan=dict(request=dict(case=dict(kind='saved')))))['ok']


def test_imported_cli_reader_refuses_without_limits_handlers_or_timer_mutation(monkeypatch,capsys):
    import resource
    import signal
    from tools import register_target as entry
    limits={key:resource.getrlimit(key) for key in (resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE)}
    timer=signal.getitimer(signal.ITIMER_REAL);handler=signal.getsignal(signal.SIGALRM)
    def no(*a,**kw):pytest.fail('Imported reader mutated/read its caller')
    monkeypatch.setattr(workflow,'_state_limits',no)
    monkeypatch.setattr(report,'read_result',no)
    for name in ('alarm','signal','setitimer'):monkeypatch.setattr(signal,name,no)
    assert entry.main(['--read-result','unused'])==2
    assert 'fresh owned CLI' in capsys.readouterr().out
    assert {key:resource.getrlimit(key) for key in limits}==limits
    assert signal.getitimer(signal.ITIMER_REAL)==timer and signal.getsignal(signal.SIGALRM)==handler


_SPECIAL_MODES=[['--read-result','unused'],['--worker-plan','unused'],['--worker']]
_NORMAL_OPTIONS=[['--plan','unused'],['--output-dir','unused'],['--dry-run'],['--resume','unused'],['--steps-this-run','0']]
@pytest.mark.parametrize('argv',[a+b for i,a in enumerate(_SPECIAL_MODES) for b in _SPECIAL_MODES[i+1:]]+
                         [a+b for a in _SPECIAL_MODES for b in _NORMAL_OPTIONS])
def test_ambiguous_cli_modes_refused_before_effects(monkeypatch,capsys,argv):
    import signal
    from tools import register_target as entry
    before=dict(os.environ)
    def no(*a,**kw):pytest.fail('Effect before ambiguous mode refusal')
    for name in ('run','worker','_state_limits','_state_worker_ready'):monkeypatch.setattr(workflow,name,no)
    for name in ('read_json','read_result'):monkeypatch.setattr(report,name,no)
    monkeypatch.setattr(entry,'_reader_owned',no)
    monkeypatch.setattr(signal,'alarm',no)
    assert entry.main(argv)==2
    assert 'Ambiguous CLI modes' in capsys.readouterr().out
    assert dict(os.environ)==before


def test_worker_plan_checks_readiness_before_reading(monkeypatch,capsys):
    from tools import register_target as entry
    calls=[]
    def refuse(snapshot):
        calls.append(snapshot)
        raise ValueError('State worker requires bounded RLIMIT_AS')
    monkeypatch.setattr(workflow,'_state_worker_ready',refuse)
    monkeypatch.setattr(report,'read_json',lambda *a:pytest.fail('Read before worker readiness'))
    monkeypatch.setattr(workflow,'worker',lambda *a,**kw:pytest.fail('Work before readiness'))
    assert entry.main(['--worker-plan','unused'])==2
    assert len(calls)==1 and 'RLIMIT_AS' in capsys.readouterr().out


@pytest.mark.parametrize('existing',['timer','handler'])
def test_fresh_reader_preserves_external_timer_and_handler_in_short_process(existing):
    code='''import sys,signal,resource,runpy
assert 'numpy' not in sys.modules
limits={k:resource.getrlimit(k) for k in (resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE)}
if sys.argv[1]=='timer':signal.setitimer(signal.ITIMER_REAL,30.,7.)
else:signal.signal(signal.SIGALRM,lambda *a:None)
before=signal.getitimer(signal.ITIMER_REAL);handler=signal.getsignal(signal.SIGALRM)
sys.argv=['tools.register_target','--read-result','unused']
try:runpy.run_module('tools.register_target',run_name='__main__')
except SystemExit as exc:assert exc.code==2
else:raise AssertionError('Missing refusal')
assert 'numpy' not in sys.modules
assert limits=={k:resource.getrlimit(k) for k in limits}
after=signal.getitimer(signal.ITIMER_REAL)
assert signal.getsignal(signal.SIGALRM) is handler
if before[0]:assert 0<after[0]<=before[0] and after[1]==before[1]
else:assert after==before
'''
    p=subprocess.run([sys.executable,'-B','-c',code,existing],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=10)
    assert p.returncode==0,p.stdout+p.stderr
    assert 'existing caller timer or SIGALRM handler' in p.stdout


def test_real_cli_reader_establishes_blas_before_numpy(acquired):
    root,_,_=acquired
    # A fresh CLI owns its environment even when the launching shell has BLAS>1.
    env={**ENV,**{key:'2' for key in workflow._STATE_BLAS_KEYS}}
    p=subprocess.run([sys.executable,'-B','-m','tools.register_target','--read-result',str(root/'full')],
        cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
    assert p.returncode==0,p.stdout+p.stderr
    result=json.loads(p.stdout.splitlines()[-1])
    assert result['ok']
    assert result['result']['context_checks']['finite_fidelity']['audited_response_domain']=='discrete'
