"""Real CLI plus native saved checkpoints, without CONFIG/model dependencies."""
import copy
import csv
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import numpy as np
import pytest

from didgeridoo_optimizer.pipeline import phase_reference as workflow
from didgeridoo_optimizer.pipeline import regime_reference as history
from didgeridoo_optimizer.reporting import phase_reference as report
from didgeridoo_optimizer.reporting import regime_reference as native
from didgeridoo_optimizer.nonlinear.phase_reference import PhasePlan
from didgeridoo_optimizer.nonlinear.regime_observables import ObservationPlan
from didgeridoo_optimizer.nonlinear.passive_resonator import digest
from didgeridoo_optimizer.tests.test_regime_reference import coupling


def phase_plan():
    return PhasePlan(train=(0.,.002),validations=((.002,.004),),scales=(.001,1.,1e-8,1e-6),section_index=0,section_level=0.,section_method='cubic')


def bundle(path,*,parent=None,steps=48,partial=False):
    path.mkdir();c=coupling();start=0
    if parent is not None:
        previous=native.verify_chain(parent/'checkpoint-000001.json');c.import_checkpoint(previous['payload']['checkpoint'])
        start=previous['payload']['accepted_steps']
        link=dict(path=str(parent/'checkpoint-000001.json'),sha256=previous['sha256'],producer_sources=previous['payload']['producer_sources'])
    else:link=None
    identity=c.checkpoint_identity();observation=ObservationPlan(windows=((0.,.005),(.005,.01)),scales=phase_plan().scales,section_index=0,section_level=0.)
    compat=dict(fs=c.sample_rate_hz,model_parameters_sha256=identity['model_sha256'],parameters=identity['parameters'],rho_kg_m3=identity['rho_kg_m3'],v2_port_model=identity['v2_port_model'],max_extensions=identity['max_extensions'],max_iterations=identity['max_iterations'],observation=observation.as_dict())
    # Use the JSON-normalized object that checkpoints actually retain.
    compat=json.loads(json.dumps(compat))
    record=dict(schema='dcalc.regime_checkpoint.v1',sequence=0,previous_sha256=None,parent=link,chain_id='native-unit-fixture',compatibility=compat,producer_sources={'historical-test':'native-checkpoint-api'},origin_time_s=0.,accepted_steps=start,checkpoint=c.export_checkpoint(),plan_identity=digest(compat),series=None)
    prev=native.checkpoint(path,record);initial=c.export_checkpoint();times=[c.time_s];states=[c.state];data=[]
    for _ in range(steps):
        row=c.step();times.append(c.time_s);states.append(c.state)
        data.append([np.nan if row[k] is None else row[k] for k in history.COLUMNS])
    native.write_npz(path/'samples-000001.npz',dict(time_s=np.array(times),states=np.array(states),data=np.array(data)))
    series=dict(name='samples-000001.npz',sha256=native.file_sha256(path/'samples-000001.npz'),columns=list(history.COLUMNS),start_steps=start,end_steps=start+steps)
    record.update(sequence=1,previous_sha256=prev,accepted_steps=start+steps,checkpoint=c.export_checkpoint(),series=series)
    native.checkpoint(path,record)
    result=dict(schema='dcalc.regime_reference.v1',ok=not partial,status='partial' if partial else 'completed',reason='interrupted' if partial else None,
        chain_id=record['chain_id'],plan_identity=record['plan_identity'],compatibility=compat,last_checkpoint='checkpoint-000001.json',last_complete_checkpoint_steps=start+steps,accepted_chain_steps=start+steps,final=c.export_checkpoint(),initial=initial,
        observations=dict(schema='dcalc.regime_observations.v1',plan_sha256=digest(compat['observation']),status='insufficient_observation',windows=[],groups=[]),fit_certificate_scope='historical fixture only',fit_certificate_sha256=None)
    native.write_json(path/'result.json',result)
    if not partial:
        native.write_json(path/'execution.json',dict(ok=True,status='completed',child=dict(exit_code=0,reaped=True)))
        native.write_json(path/'execution.closed.json',dict(execution_sha256=native.file_sha256(path/'execution.json'),cancelled=False))
    return path


def run_cli(source,planpath,out,*flags):
    return subprocess.run([sys.executable,'-B','-m','tools.phase_reference','--input-bundle',str(source),'--plan',str(planpath),'--output',str(out),*flags],capture_output=True,text=True,timeout=25)


def test_real_cli_dry_run_and_insufficient_phase_success(tmp_path):
    source=bundle(tmp_path/'source');p=tmp_path/'request.json';p.write_text(json.dumps(phase_plan().as_dict()))
    before={f.name:native.file_sha256(f) for f in source.iterdir()}
    dry=run_cli(source,p,tmp_path/'dry','--dry-run');assert dry.returncode==0,dry.stdout+dry.stderr
    assert json.loads(dry.stdout)['phase_calculated'] is False and not (tmp_path/'dry').exists()
    cli=run_cli(source,p,tmp_path/'analysis');assert cli.returncode==0,cli.stdout+cli.stderr
    r=native.read_json(tmp_path/'analysis/result.json')
    assert r['ok'] and r['groups'][0]['reason']=='insufficient_training_crossings'
    assert r['provenance']['plan_request']['sha256']==native.file_sha256(p)
    assert r['provenance']['historical_execution']['ok'] is True
    assert report.read_completion(tmp_path/'analysis')['ok']
    assert before=={f.name:native.file_sha256(f) for f in source.iterdir()}
    assert r['historical_observations']['status']=='insufficient_observation'


def test_resumed_chain_offline_without_original_config_or_model(tmp_path):
    first=bundle(tmp_path/'first',steps=24);second=bundle(tmp_path/'resumed',parent=first,steps=24)
    r=workflow.run(second,phase_plan(),tmp_path/'out')
    assert r['ok'],r
    assert r['verified_saved_steps']==48
    assert len(r['provenance']['series'])==2
    assert r['provenance']['plan_request']['mode']=='inline'


def test_partial_or_missing_result_keeps_verified_series_and_historical_failure(tmp_path):
    source=bundle(tmp_path/'source',partial=True)
    r=workflow.run(source,phase_plan(),tmp_path/'out')
    assert r['ok'] and r['provenance']['historical_execution']['ok'] is False
    assert r['historical_result_status']['status']=='partial'
    # A checkpoint-only native archive is supported as an unconfirmed history.
    other=bundle(tmp_path/'other');(other/'result.json').rename(tmp_path/'retained-result.json')
    r=workflow.run(other,phase_plan(),tmp_path/'out2')
    assert r['ok'] and r['historical_observations'] is None


@pytest.mark.parametrize('mutation',['result_tip','result_final','fs','observation','series','link','unexpected'])
def test_corruption_or_identity_mismatch_refused(tmp_path,mutation):
    source=bundle(tmp_path/'source')
    if mutation in ('result_tip','result_final','fs','observation'):
        path=source/'result.json';r=native.read_json(path)
        if mutation=='result_tip':r['last_checkpoint']='checkpoint-000000.json'
        if mutation=='result_final':r['final']['payload']['snapshot']['state'][0]+=1
        if mutation=='fs':r['compatibility']['fs']=1000
        if mutation=='observation':r['observations']['plan_sha256']='bad'
        path.write_text(json.dumps(r))
    if mutation=='series':
        with (source/'samples-000001.npz').open('ab') as f:f.write(b'corruption')
    if mutation=='link':(source/'unexpected-link').symlink_to(source/'result.json')
    if mutation=='unexpected':(source/'unknown').write_text('x')
    r=workflow.run(source,phase_plan(),tmp_path/'out')
    assert not r['ok'] and not (tmp_path/'out').exists()


def test_output_overlaps_any_ancestor_and_existing_is_never_written(tmp_path):
    source=bundle(tmp_path/'source');resumed=bundle(tmp_path/'resumed',parent=source)
    for out in (source/'nested',resumed/'nested',tmp_path,source):
        r=workflow.run(resumed,phase_plan(),out);assert not r['ok']
    existing=tmp_path/'existing';existing.mkdir();(existing/'plan.json').write_text('{}')
    before=list(existing.iterdir());r=workflow.run(source,phase_plan(),existing)
    assert not r['ok'] and list(existing.iterdir())==before
    link=tmp_path/'link';link.symlink_to(source,target_is_directory=True)
    assert not workflow.run(link,phase_plan(),tmp_path/'linked')['ok']


@pytest.mark.parametrize('text',['{"train":1,"train":2}','{"x":NaN}','{"x":1e400}','{"x":Infinity}'])
def test_strict_plan_json_no_writes(tmp_path,text):
    source=bundle(tmp_path/'source');p=tmp_path/'p.json';p.write_text(text)
    r=run_cli(source,p,tmp_path/'out');assert r.returncode!=0 and not (tmp_path/'out').exists()


def test_copied_cli_rejected_before_parsing(tmp_path,capsys):
    path=tmp_path/'copied.py';path.write_bytes((workflow.ROOT/'tools/phase_reference.py').read_bytes())
    spec=importlib.util.spec_from_file_location('copied_phase',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.main([])==2
    assert 'Mixed CLI producer roots' in capsys.readouterr().out


@pytest.mark.parametrize('stage',['before_export','during_seal','after_seal'])
def test_input_change_at_publication_never_closes_success(tmp_path,monkeypatch,stage):
    source=bundle(tmp_path/'source');p=tmp_path/'plan.json';p.write_text(json.dumps(phase_plan().as_dict()))
    real_export=report.export;real_write=native.write_json
    def change():p.write_text(p.read_text()+' ')
    def export(*a,**k):
        if stage=='before_export':change()
        return real_export(*a,**k)
    def write(path,value,**kw):
        if Path(path).name=='analysis.closed.json' and stage=='during_seal':change()
        value=real_write(path,value,**kw)
        if Path(path).name=='analysis.closed.json' and stage=='after_seal':change()
        return value
    monkeypatch.setattr(report,'export',export);monkeypatch.setattr(native,'write_json',write)
    r=workflow.run(source,p,tmp_path/'out')
    assert not r['ok'];assert not report.read_completion(tmp_path/'out')['ok']


@pytest.mark.parametrize('sig',[signal.SIGTERM,signal.SIGINT])
def test_real_signal_during_closure_nonzero_and_cancelled(tmp_path,monkeypatch,sig):
    source=bundle(tmp_path/'source');real=native.write_json
    def write(path,value,**kw):
        answer=real(path,value,**kw)
        if Path(path).name=='analysis.closed.json':os.kill(os.getpid(),sig)
        return answer
    monkeypatch.setattr(native,'write_json',write)
    r=workflow.run(source,phase_plan(),tmp_path/'out')
    assert not r['ok'] and not report.read_completion(tmp_path/'out')['ok']


def test_preflight_budget_before_allocation_and_dry_run_has_no_phase(tmp_path,monkeypatch):
    source=bundle(tmp_path/'source');p=phase_plan()
    monkeypatch.setattr(workflow,'analyze',lambda *a,**k:pytest.fail('phase calculation in dry run'))
    assert workflow.run(source,p,tmp_path/'dry',dry_run=True)['ok']
    from dataclasses import replace
    r=workflow.run(source,replace(p,max_points=3),tmp_path/'out')
    assert not r['ok'] and 'before allocation' in r['reason']


def test_exports_preserve_null_and_exact_group_records(tmp_path):
    source=bundle(tmp_path/'source');r=workflow.run(source,phase_plan(),tmp_path/'out')
    assert r['ok']
    with (tmp_path/'out/groups.csv').open() as f:rows=list(csv.DictReader(f))
    assert len(rows)==6 and rows[0]['period_s']==''
    assert json.loads(rows[0]['record_json'])['period_s'] is None
    summary=(tmp_path/'out/summary.md').read_text()
    assert 'stabilité orbitale' in summary and len(summary)<5000
    (tmp_path/'out/analysis.closed.json').rename(tmp_path/'retained-closure.json')
    assert not report.read_completion(tmp_path/'out')['ok']


def test_initial_state_tamper_even_with_rehashed_series_is_refused(tmp_path):
    source=bundle(tmp_path/'source');path=source/'samples-000001.npz'
    with np.load(path) as data:arrays={k:data[k] for k in data.files}
    arrays['states'][0,0]+=1.
    with path.open('wb') as f:np.savez_compressed(f,**arrays)
    checkpoint=source/'checkpoint-000001.json';c=native.read_json(checkpoint)
    c['payload']['series']['sha256']=native.file_sha256(path);c['sha256']=digest(c['payload']);checkpoint.write_text(json.dumps(c))
    r=workflow.run(source,phase_plan(),tmp_path/'out')
    assert not r['ok'] and 'initial state' in r['reason']


def test_changed_result_during_manifest_collection_is_refused(tmp_path,monkeypatch):
    source=bundle(tmp_path/'source');real=native.file_sha256;trigger=[False]
    def sha(path,*a,**kw):
        if Path(path)==source/'result.json' and not trigger[0]:
            trigger[0]=True
            path=Path(path);path.write_text(path.read_text()+' ')
        return real(path,*a,**kw)
    monkeypatch.setattr(native,'file_sha256',sha)
    r=workflow.run(source,phase_plan(),tmp_path/'out')
    assert not r['ok'] and 'changed during inspection' in r['reason']


def test_malformed_cancellation_is_not_success(tmp_path):
    native.write_json(tmp_path/'analysis.cancelled.json',[])
    assert not report.read_completion(tmp_path)['ok']


def test_real_cli_timeout_and_normalized_nested_output_are_refused(tmp_path):
    source=bundle(tmp_path/'source');p=tmp_path/'tiny.json'
    from dataclasses import replace
    p.write_text(json.dumps(replace(phase_plan(),seconds=1e-12).as_dict()))
    r=run_cli(source,p,tmp_path/'timeout')
    assert r.returncode!=0 and not (tmp_path/'timeout').exists()
    other=tmp_path/'other';other.mkdir()
    out=other/'..'/'source'/'nested'
    r=workflow.run(source,phase_plan(),out)
    assert not r['ok'] and not out.exists()


def test_native_initial_checkpoint_only_keeps_all_requested_groups(tmp_path):
    full=bundle(tmp_path/'full');source=tmp_path/'initial';source.mkdir()
    (source/'checkpoint-000000.json').write_bytes((full/'checkpoint-000000.json').read_bytes())
    r=workflow.run(source,phase_plan(),tmp_path/'out')
    assert r['ok'] and r['reason']=='insufficient_saved_samples'
    assert [g['group'] for g in r['groups']]==[1,2]
    assert all(g['reason']=='insufficient_saved_samples' for g in r['groups'])
    assert report.read_completion(tmp_path/'out')['ok']


def test_auxiliary_si_errors_are_scalar_csv_columns(tmp_path):
    from didgeridoo_optimizer.tests.test_phase_reference import data,plan
    from didgeridoo_optimizer.nonlinear.phase_reference import analyze
    t,z=data(f=50.);mid=(t[:-1]+t[1:])/2;v=np.sin(2*np.pi*50*mid)
    v[mid>=2.]+=2.
    result=analyze(t,z,plan=plan(groups=(1,)),signals={'pressure':dict(times=mid,values=v,unit='Pa',scale=None)})
    report.export(tmp_path,result)
    with (tmp_path/'signals.csv').open() as stream:rows=list(csv.DictReader(stream))
    assert float(rows[0]['maximum_si'])==pytest.approx(2.,abs=1e-8)
    assert rows[0]['maximum_scaled']=='' and rows[0]['unit']=='Pa'


@pytest.mark.parametrize('fault',['verification','stop','timeout','SIGINT','SIGTERM','seal_write'])
def test_candidate_failure_without_cancellation_write_is_not_success(tmp_path,monkeypatch,fault):
    source=bundle(tmp_path/'source');out=tmp_path/'out';real=native.write_json
    unchanged=workflow._unchanged;clock=workflow.time.monotonic;trigger=[False]
    handlers={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
    mask=signal.pthread_sigmask(signal.SIG_BLOCK,[]) if hasattr(signal,'pthread_sigmask') else None
    def write(path,value,**kw):
        if Path(path).name=='analysis.cancelled.json':raise OSError('cancellation unavailable')
        answer=real(path,value,**kw)
        if Path(path).name=='analysis.closed.json':
            trigger[0]=True
            if fault in ('SIGINT','SIGTERM'):os.kill(os.getpid(),getattr(signal,fault))
            if fault=='seal_write':raise OSError('candidate synchronization failed')
        return answer
    def verify(*a):
        unchanged(*a)
        if trigger[0] and fault=='verification':raise ValueError('postcandidate source failure')
    monkeypatch.setattr(native,'write_json',write);monkeypatch.setattr(workflow,'_unchanged',verify)
    monkeypatch.setattr(workflow.time,'monotonic',lambda:clock()+(1000 if trigger[0] and fault=='timeout' else 0))
    r=workflow.run(source,phase_plan(),out,stop=lambda:trigger[0] and fault=='stop')
    assert trigger[0] and not r['ok'] and not report.read_completion(out)['ok']
    assert (out/'result.json').exists() and not (out/'analysis.cancelled.json').exists()
    assert all(signal.getsignal(s)==h for s,h in handlers.items())
    if mask is not None:assert signal.pthread_sigmask(signal.SIG_BLOCK,[])==mask


@pytest.mark.parametrize('fault',['prepare','link','pending_SIGINT','pending_SIGTERM'])
def test_final_authority_failure_and_pending_signals(tmp_path,monkeypatch,fault):
    source=bundle(tmp_path/'source');out=tmp_path/'out';real_write=native.write_json
    real_link=os.link;real_verify=workflow._unchanged;trigger=[False]
    previous=signal.pthread_sigmask(signal.SIG_BLOCK,[]) if hasattr(signal,'pthread_sigmask') else None
    def write(path,value,**kw):
        if Path(path).name=='analysis.cancelled.json':raise OSError('no cancellation storage')
        answer=real_write(path,value,**kw)
        if Path(path).name=='.pending-analysis-completion.json' and fault=='prepare':
            trigger[0]=True;raise OSError('prepared inode synchronization failed')
        return answer
    def link(src,dst,**kw):
        if Path(dst).name=='analysis.completed.json' and fault=='link':
            trigger[0]=True;raise OSError('final publication failed')
        return real_link(src,dst,**kw)
    def verify(*args):
        real_verify(*args)
        if (out/'.pending-analysis-completion.json').exists() and fault.startswith('pending_'):
            trigger[0]=True;os.kill(os.getpid(),getattr(signal,fault.removeprefix('pending_')))
            if previous is not None:assert signal.sigpending()
    monkeypatch.setattr(native,'write_json',write);monkeypatch.setattr(os,'link',link)
    monkeypatch.setattr(workflow,'_unchanged',verify)
    r=workflow.run(source,phase_plan(),out)
    assert trigger[0] and not r['ok'] and not report.read_completion(out)['ok']
    assert not (out/'analysis.completed.json').exists()
    if previous is not None:assert signal.pthread_sigmask(signal.SIG_BLOCK,[])==previous


@pytest.mark.parametrize('marker',['analysis.closed.json','analysis.completed.json'])
@pytest.mark.parametrize('damage',['absent','corrupt','mismatched_hash'])
def test_authority_requires_both_intact_hash_bound_markers(tmp_path,marker,damage):
    source=bundle(tmp_path/'source');out=tmp_path/'out'
    assert workflow.run(source,phase_plan(),out)['ok'] and report.read_completion(out)['ok']
    path=out/marker
    if damage=='absent':path.rename(tmp_path/'saved-marker.json')
    elif damage=='corrupt':path.write_text('{')
    else:
        value=json.loads(path.read_text());value['result_sha256']='0'*64;path.write_text(json.dumps(value))
    assert not report.read_completion(out)['ok']


@pytest.mark.parametrize('sig',[signal.SIGINT,signal.SIGTERM])
@pytest.mark.parametrize('side',['candidate','completed'])
def test_cli_receipt_agrees_with_reader_on_both_sides_of_boundary(tmp_path,sig,side):
    source=bundle(tmp_path/'source');p=tmp_path/'plan.json';p.write_text(json.dumps(phase_plan().as_dict()))
    out=tmp_path/'out'
    # Execute the canonical CLI main in a targeted child; inject only its IO.
    code='''
import json, os, signal, sys
from pathlib import Path
from tools.phase_reference import main
from didgeridoo_optimizer.reporting import regime_reference as native
from didgeridoo_optimizer.reporting.phase_reference import read_completion
before={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
mask=signal.pthread_sigmask(signal.SIG_BLOCK,[]) if hasattr(signal,'pthread_sigmask') else None
real_write=native.write_json;real_link=os.link
side=sys.argv[1];sig=int(sys.argv[2]);out=Path(sys.argv[5]);trigger=[]
def write(path,value,**kw):
 if Path(path).name=='analysis.cancelled.json':raise OSError('injected cancellation failure')
 result=real_write(path,value,**kw)
 if side=='candidate' and Path(path).name=='analysis.closed.json':
  trigger.append(True);os.kill(os.getpid(),sig)
 return result
def link(src,dst,**kw):
 result=real_link(src,dst,**kw)
 if side=='completed' and Path(dst).name=='analysis.completed.json':
  trigger.append(True);os.kill(os.getpid(),sig)
 return result
native.write_json=write;os.link=link
code=main(['--input-bundle',sys.argv[3],'--plan',sys.argv[4],'--output',sys.argv[5]])
assert trigger and all(signal.getsignal(s)==h for s,h in before.items())
if mask is not None:assert signal.pthread_sigmask(signal.SIG_BLOCK,[])==mask
assert (code==0)==read_completion(out)['ok']==(side=='completed')
sys.exit(code)
'''
    r=subprocess.run([sys.executable,'-B','-c',code,side,str(int(sig)),str(source),str(p),str(out)],capture_output=True,text=True,timeout=25)
    assert (r.returncode==0)==(side=='completed'),r.stdout+r.stderr
    receipt=json.loads(r.stdout)
    assert receipt['ok']==report.read_completion(out)['ok']==(side=='completed')
    assert 'Traceback' not in r.stderr


@pytest.mark.parametrize('value',[np.nan,np.inf])
def test_native_nonfinite_pressure_is_explicitly_outside_cli_format(tmp_path,value):
    source=bundle(tmp_path/'source');path=source/'samples-000001.npz'
    with np.load(path) as data:arrays={k:data[k] for k in data.files}
    arrays['data'][5,history.COLUMNS.index('pressure_pa')]=value
    with path.open('wb') as f:np.savez_compressed(f,**arrays)
    checkpoint=source/'checkpoint-000001.json';c=native.read_json(checkpoint)
    c['payload']['series']['sha256']=native.file_sha256(path);c['sha256']=digest(c['payload']);checkpoint.write_text(json.dumps(c))
    (source/'result.json').rename(tmp_path/'historical-result.json')
    p=tmp_path/'plan.json';p.write_text(json.dumps(phase_plan().as_dict()))
    out=tmp_path/'out';r=run_cli(source,p,out)
    assert r.returncode!=0 and not json.loads(r.stdout)['ok']
    assert 'Nonfinite native state/pressure' in json.loads(r.stdout)['reason']
    assert not report.read_completion(out)['ok']
