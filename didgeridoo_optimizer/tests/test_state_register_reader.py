"""C3: bounded saved-row verification and explicit read-only producer identity."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear import register_target as core
from didgeridoo_optimizer.nonlinear.passive_resonator import digest
from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
from didgeridoo_optimizer.reporting import register_target as report
from didgeridoo_optimizer.reporting import state_register_context as evidence
from didgeridoo_optimizer.tests.test_state_register import state_model
from didgeridoo_optimizer.tests.test_register_target import request


def saved_rows(tmp_path, count=64, kind='relaxation'):
    """Native synthetic unit witness; no R49 data, formulas or fixtures replaced."""
    r=request(steps=count);r['budgets']['chunk_steps']=32
    path=tmp_path/'model.json';state_model(kind).save(path)
    m=PassiveState.load(path)
    e=core.Experiment(m,r,source=dict(kind='synthetic',description='C3 native unit witness',model_sha256=digest(m.parameters())))
    e.context_checks={}
    p=dict(request=r,metadata=dict(states=2+m.state_count),accepted_before=0,resume=None,steps_this_run=count)
    p['context_sha256']=digest(p)
    out=tmp_path/'run';out.mkdir();previous=None;chain=[]
    def save(chunk=None):
        nonlocal previous
        previous=report.save_checkpoint(out,len(chain),e,p,previous,chunk)
        cp=Path(previous['path']);chain.append((cp,report.read_json(cp)['payload']))
    save()
    while e.accepted<count:save(e.advance(min(32,count-e.accepted)))
    arrays=report.load_series(chain)
    analysis=core.analyze(r,times=arrays['times'],states=arrays['states'],midpoint_times=arrays['midpoint_times'],
        pressure=arrays['values'][:,0],source=dict(kind='verified_bundle',description='Verified product chunk chain',checkpoint=previous))
    result=dict(plan=p,status='complete',context_checks={},new_steps_this_run=count,new_duration_s=count/4000,
                historical_source_steps=0,checkpoint=previous,analysis=analysis,events=e.events)
    report.export(out,result,8*1024**2)
    return out,result,arrays,e,chain


def test_model_file_checks_are_chunk_bounded_and_L_is_copied_once(tmp_path,monkeypatch):
    args=saved_rows(tmp_path)
    calls=dict(disk=0,L=0)
    original=PassiveState.verify_file_unchanged;get_L=PassiveState.L.fget
    def verified(self):
        if self._loaded_path is not None:calls['disk']+=1
        return original(self)
    def matrix(self):
        calls['L']+=1
        return get_L(self)
    monkeypatch.setattr(PassiveState,'verify_file_unchanged',verified)
    monkeypatch.setattr(PassiveState,'L',property(matrix))
    evidence.verify_saved_analysis(*args)
    print('reader_cost',calls)
    assert calls['L']==1
    assert calls['disk']<=32*(len(args[-1])+1)


@pytest.mark.parametrize('kind',['zero','resistance','relaxation','modal'])
def test_native_rows_ports_energy_and_observations_unchanged_without_steps(tmp_path,monkeypatch,kind):
    args=saved_rows(tmp_path,kind=kind);out,result,arrays,e,chain=args
    before={k:v.copy() for k,v in arrays.items()};parameters=e.model.parameters();snapshot=e.checkpoint()
    raw={p:p.read_bytes() for p in out.iterdir()}
    def no(*a,**kw):pytest.fail('Temporal evolution in reader')
    monkeypatch.setattr(PassiveState,'step',no);monkeypatch.setattr(SimultaneousCoupling,'step',no)
    monkeypatch.setattr(core.Experiment,'advance',no)
    evidence.verify_saved_analysis(*args)
    assert e.model.parameters()==parameters and e.checkpoint()==snapshot
    for k in arrays:assert np.array_equal(arrays[k],before[k])
    assert all(p.read_bytes()==data for p,data in raw.items())


@pytest.mark.parametrize('kind',['model','input'])
@pytest.mark.parametrize('row',[1,33,64])
def test_persistent_mutation_after_admission_refused_at_chunk_or_final(tmp_path,monkeypatch,kind,row):
    from didgeridoo_optimizer.pipeline.state_register_context import context_guard
    args=saved_rows(tmp_path);e=args[3]
    input_path=tmp_path/'input.json';input_path.write_text('{}')
    e._context_guard=context_guard(dict(bundle_files={'model':report.reference(tmp_path/'model.json')},evidence_files={}),
                                   {'config':report.reference(input_path)})
    target=tmp_path/'model.json' if kind=='model' else input_path
    original=SimultaneousCoupling._port;calls=0
    def port(self,*a,**kw):
        nonlocal calls
        value=original(self,*a,**kw);calls+=1
        if calls==row:target.write_bytes(target.read_bytes()+b'\n')
        return value
    monkeypatch.setattr(SimultaneousCoupling,'_port',port)
    with pytest.raises(ValueError,match='changed'):evidence.verify_saved_analysis(*args)
    assert row<=calls<=((row-1)//32+1)*32


@pytest.mark.parametrize('field',['state','pressure_pa','jet_m3_s','resonator_loss_j','total_energy_j'])
def test_native_semantics_refuse_coherent_new_observation_hashes(tmp_path,field):
    args=saved_rows(tmp_path);out,result,arrays,e,chain=args
    if field=='state':arrays['states'][17,2]+=1e-3
    else:arrays['values'][17,core.COLUMNS.index(field)]+=1e-3
    # Recompute the public analysis honestly, so a row cannot be rejected merely
    # because its observation/criterion checksum is stale.
    result['analysis']=core.analyze(result['plan']['request'],times=arrays['times'],states=arrays['states'],
        midpoint_times=arrays['midpoint_times'],pressure=arrays['values'][:,0],
        source=dict(kind='verified_bundle',description='Verified product chunk chain',checkpoint=result['checkpoint']))
    # export is exclusive: remove only these temporary test artifacts.
    for name in ('result.json','window-hold.json','criteria.csv','controls.csv','events.csv','summary.txt'):(out/name).unlink()
    report.export(out,result,8*1024**2)
    with pytest.raises(ValueError,match='residual'):evidence.verify_saved_analysis(*args)


def historical_producer():
    return dict(software=dict(sha=report.READER_PRODUCER_SHA,working_tree_dirty=False,
        origin='git HEAD of verified worktree with tracked loaded D-Calc sources'),
        versions=report.versions(),loaded_sources_sha256={**report.sources(),**report._READER_OLD_SOURCES})


@pytest.mark.parametrize('fault',['sha','dirty','origin','missing','old_hash','physics','criteria','unknown_path','versions'])
def test_historical_identity_refuses_every_unapproved_difference(fault):
    producer=historical_producer();p=producer['loaded_sources_sha256']
    if fault=='sha':producer['software']['sha']='0'*40
    elif fault=='dirty':producer['software']['working_tree_dirty']=True
    elif fault=='origin':producer['software']['origin']='unverified checkout'
    elif fault=='missing':del p['didgeridoo_optimizer/nonlinear/passive_state.py']
    elif fault=='old_hash':p['tools/register_target.py']='0'*64
    elif fault=='physics':p['didgeridoo_optimizer/nonlinear/simultaneous_coupling.py']='0'*64
    elif fault=='criteria':p['didgeridoo_optimizer/nonlinear/register_target.py']='0'*64
    elif fault=='unknown_path':p['../arbitrary.py']='0'*64
    elif fault=='versions':producer['versions']['numpy']='other'
    with pytest.raises((ValueError,FileNotFoundError)):report._ReaderIdentity(report.READER_PRODUCER_SHA).verify(producer)


def test_compatibility_has_no_ambient_effect_and_strict_default_refuses():
    p=historical_producer()
    report._ReaderIdentity(report.READER_PRODUCER_SHA).verify(p)
    with pytest.raises(ValueError):report._ReaderIdentity(None).verify(p)
    with pytest.raises(ValueError):report._ReaderIdentity('0'*40)
    from didgeridoo_optimizer.pipeline import register_target as workflow
    with pytest.raises(TypeError):workflow.preflight('unused','unused',producer_sha=report.READER_PRODUCER_SHA)
    with pytest.raises(TypeError):workflow.worker({},producer_sha=report.READER_PRODUCER_SHA)
    with pytest.raises(ValueError):workflow.worker(dict(plan={},producer_sha=report.READER_PRODUCER_SHA))


@pytest.mark.parametrize('mode',[['--worker'],['--worker-plan','absent'],['--plan','absent','--output-dir','unused'],
                                 ['--plan','absent','--output-dir','unused','--resume','absent'],['--dry-run']])
def test_cli_compatibility_refused_in_all_calculation_modes(mode,capsys):
    from tools import register_target as entry
    assert entry.main([*mode,'--reader-producer-sha',report.READER_PRODUCER_SHA])==2
    assert 'requires --read-result' in json.loads(capsys.readouterr().out)['reason']


@pytest.fixture(scope='module')
def current_run(tmp_path_factory):
    from didgeridoo_optimizer.tests.test_state_register_cli import saved_plan,cli
    root=tmp_path_factory.mktemp('reader-native');path=root/'plan.json';saved_plan(path)
    child=cli(path,root/'run');assert child.returncode==0,child.stdout+child.stderr
    return root/'run'


def test_real_reader_api_preserves_limits_handlers_timers(current_run,monkeypatch):
    import resource,signal
    from didgeridoo_optimizer.pipeline import register_target as workflow
    before=([resource.getrlimit(k) for k in (resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE)],
            signal.getsignal(signal.SIGALRM),signal.getitimer(signal.ITIMER_REAL))
    def no(*a,**kw):pytest.fail('Caller resource policy or trajectory mutated')
    for name in ('alarm','signal','setitimer'):monkeypatch.setattr(signal,name,no)
    monkeypatch.setattr(resource,'setrlimit',no);monkeypatch.setattr(core.Experiment,'advance',no)
    result=report.read_result(current_run);assert result['ok'],result
    assert result['reading']['mode']=='strict'
    after=([resource.getrlimit(k) for k in (resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE)],
            signal.getsignal(signal.SIGALRM),signal.getitimer(signal.ITIMER_REAL))
    assert after==before
    e=workflow.experiment_for(result['result']['plan'],_reader=report._ReaderIdentity(None))
    # Check the actual reader guard, not the test's advance spy.
    with pytest.raises(ValueError,match='cannot advance'):e._context_guard()


@pytest.mark.parametrize('fault',['times','ledger','state'])
def test_rehashed_series_or_ledger_still_refused(tmp_path,fault):
    args=saved_rows(tmp_path);chain=args[-1];path,c=chain[1];series=c['series'];file=Path(series['file']['path'])
    if fault=='ledger':c['experiment']['payload']['sums']['source_work_j']+=1.
    else:
        with np.load(file,allow_pickle=False) as z:values={k:z[k] for k in z.files}
        values['times' if fault=='times' else 'states'][17 if fault=='times' else -1]+=1e-3
        np.savez(file,**values);series['file']=report.reference(file)
    c['experiment']['sha256']=digest(c['experiment']['payload'])
    path.write_text(json.dumps(dict(payload=c,sha256=digest(c))))
    with pytest.raises(ValueError,match='time grid|ledger|state mismatch'):report.load_series(chain)


def historical_run(current_run,tmp_path):
    """Only a disposable unit acquisition is relabelled; never acquired R49."""
    import shutil
    from didgeridoo_optimizer.tests.test_state_register_cli import reclose
    out=tmp_path/'historical';shutil.copytree(current_run,out)
    p=report.read_json(out/'plan.json');p['output']=str(out)
    p['producer']['software']=historical_producer()['software']
    p['producer']['loaded_sources_sha256'].update(report._READER_OLD_SOURCES)
    p['context_sha256']=digest({k:v for k,v in p.items() if k!='context_sha256'})
    previous=None
    for path in sorted(out.glob('checkpoint-*.json')):
        c=report.read_json(path)['payload'];c.update(context=p,context_sha256=p['context_sha256'],previous=previous)
        if c['series'] is not None:
            c['series']['file']=report.reference(out/Path(c['series']['file']['path']).name)
        path.write_text(json.dumps(dict(payload=c,sha256=digest(c))));previous=report.reference(path)
    (out/'plan.json').write_text(json.dumps(p))
    result=report.read_json(out/'result.json');result.update(plan=p,checkpoint=previous)
    result['analysis']['source']['checkpoint']=previous
    (out/'result.json').write_text(json.dumps(result));reclose(out)
    return out


def test_fresh_historical_cli_and_api_keep_original_result_and_strict_resume(current_run,tmp_path):
    import subprocess,sys
    from didgeridoo_optimizer.tests.test_state_register_cli import ROOT,ENV
    from didgeridoo_optimizer.pipeline import register_target as workflow
    out=historical_run(current_run,tmp_path)
    raw={p:p.read_bytes() for p in out.iterdir()}
    strict=report.read_result(out);assert not strict['ok'],strict
    child=subprocess.run([sys.executable,'-B','-m','tools.register_target','--read-result',str(out),
        '--reader-producer-sha',report.READER_PRODUCER_SHA],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=40)
    assert child.returncode==0,child.stdout+child.stderr
    result=json.loads(child.stdout);assert result['ok'] and result['reading']['mode']=='historical_read_only'
    assert result['result']==json.loads(raw[out/'result.json'])
    assert result['reading']['producer']['software']['sha']==report.READER_PRODUCER_SHA
    assert set(result['reading']['compatible_source_paths'])==set(report._READER_OLD_SOURCES)
    assert all(p.read_bytes()==data for p,data in raw.items())
    # No override is retained in a subsequent strict read or calculation.
    assert not report.read_result(out)['ok']
    with pytest.raises(ValueError):workflow.check_inputs(result['result']['plan'])
    with pytest.raises(ValueError):report.inspect_checkpoint(result['result']['checkpoint']['path'])
    with pytest.raises(ValueError):workflow.preflight(result['result']['plan']['request_source']['path'],tmp_path/'resume',
                                                     resume=result['result']['checkpoint']['path'])
    assert not (tmp_path/'resume').exists()


def test_source_mutation_after_admission_refused_in_isolated_checkout(tmp_path):
    import shutil,subprocess,sys
    from didgeridoo_optimizer.tests.test_state_register_cli import ROOT,ENV
    checkout=tmp_path/'checkout'
    shutil.copytree(ROOT/'didgeridoo_optimizer',checkout/'didgeridoo_optimizer',ignore=shutil.ignore_patterns('fixtures','__pycache__'))
    shutil.copytree(ROOT/'tools',checkout/'tools',ignore=shutil.ignore_patterns('__pycache__'))
    code='''from pathlib import Path
from didgeridoo_optimizer.tests.test_state_register_reader import saved_rows
from didgeridoo_optimizer.reporting import register_target as report
from didgeridoo_optimizer.reporting import state_register_context as evidence
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
args=saved_rows(Path('.'))
identity=report._ReaderIdentity(report.READER_PRODUCER_SHA)
original=SimultaneousCoupling._port
calls=0
def port(self,*a,**kw):
    global calls
    value=original(self,*a,**kw);calls+=1
    if calls==64:
        p=Path('didgeridoo_optimizer/reporting/state_register_context.py')
        p.write_bytes(p.read_bytes()+b'\\n# persistent mutation after admission\\n')
    return value
SimultaneousCoupling._port=port
try: evidence.verify_saved_analysis(*args,verify_inputs=identity.verify_current)
except ValueError as exc:
    assert 'source' in str(exc).lower(),exc
else: raise AssertionError('Persistent source mutation accepted')
assert calls==64
'''
    child=subprocess.run([sys.executable,'-B','-c',code],cwd=checkout,env={**ENV,'PYTHONPATH':str(checkout)},
                         capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stdout+child.stderr
