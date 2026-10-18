"""Portable metadata stubs, explicitly synthetic; no instrument acquisition/fit.

Mocked-reader tests exercise orchestration and semantic composition, not native
proof validity. A separate test invokes the real native geometry-only audit reader.
The real acceptance demonstration is deliberately separate from this suite.
"""
import copy
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

import pytest

from didgeridoo_optimizer.reporting import design_dossier_schema as s
from didgeridoo_optimizer.reporting import design_dossier as report
from didgeridoo_optimizer.pipeline import design_dossier as flow


def write(path, value):
    path.write_bytes(s.encoded(value)); return path


def synthetic_job(root):
    """Documentary stubs only: native geometry/played readers must reject these."""
    for name in ('passive','geometry','played'):
        (root/name).mkdir()
    (root/'geometry/realization').mkdir()
    for name in ('config','materials','variant_rules'):
        write(root/(name+'.json'), {'synthetic_fixture':name})
    profile = dict(id='synthetic', metadata={'nature':'synthetic metadata stub, not an acquisition'},
        segments=[dict(kind='cylinder', length_cm=100., d_in_cm=3., d_out_cm=3., material_id='synthetic', profile_params={})])
    profile_path = write(root/'passive/profile_7_nominal__fixed.json', profile)
    inputs = {k:dict(path=str(root/(k+'.json')),sha256=s.sha(root/(k+'.json'))) for k in ('config','materials','variant_rules')}
    inputs['design'] = dict(path=str(profile_path), sha256=s.sha(profile_path))
    hashes = {k:v['sha256'] for k,v in inputs.items()}
    model = write(root/'geometry/realization/model.json', {'synthetic_model':True})
    source = write(root/'geometry/source.json', {'synthetic_source':True})
    recipe = dict(sample_rate_hz=12000,h_cm=.5,loss_model='zk',radiation_model='legacy',air_reference='ck_dry20')
    effective = {'synthetic_effective_model':True}
    evidence = dict(inputs=hashes, recipe=recipe, effective=effective, physical_radius_m=.015,
                    model_sha256='0'*64, source_sha256=s.sha(source))
    write(root/'geometry/evidence.json', evidence)
    fidelity = {'status':'synthetic_metadata_stub_not_acquisition','physical_validation':'not_validated'}
    gp = dict(schema='dcalc.geometry_state.plan.v1', inputs=inputs, recipe=recipe, effective=effective,
        physical_radius_m=.015, acquisition={'nature':'synthetic_metadata_stub'}, base_sha='0'*40, producer_sources={}, budgets={})
    write(root/'geometry/plan.json',gp);write(root/'geometry/result.json',dict(fidelity=fidelity))
    producer = {'software':{'sha':'0'*40},'synthetic':True}
    criterion = dict(id='length',role='hard',observable='geometry',target=dict(value=1.,unit='m'),
                     tolerance=dict(value=.01,unit='m'),unit='m',level='geometry',scope={},expression={'total_length':True})
    optional = dict(id='f1',role='observe',observable='resonance_frequency',target=dict(value=111.,unit='Hz'),
                    tolerance=dict(value=.1,unit='Hz'),unit='Hz',level='passive',scope={},mode='m1')
    tp = dict(job={'input':{'kind':'fixed'}},geometry=[dict(scenario='nominal',projection='fixed',issue=None)],
        input_files=inputs,effective_models=[effective],native_request=dict(criteria=[criterion,optional]),provenance=producer,budgets={})
    passive_rows = [dict(id='length',role='hard',observable='geometry',status='satisfied',value_si=1.,unit_si='m',margin=.01,margin_unit='m'),
                    dict(id='f1',role='observe',observable='resonance_frequency',status='unresolved',value_si=None,unit_si='Hz',margin=None,margin_unit=None)]
    observation = dict(scenario='nominal',projection='fixed',configuration='nominal',geometry_status='valid',
        criteria=passive_rows,effective_request=tp['native_request'],acoustics=dict(effective_models=effective,physical_exit_radius_m=.015))
    tr = dict(plan=tp,observations=[observation],execution_complete=True,ok=True,artifacts_verified=True,semantic_verified=True)
    write(root/'passive/plan.json',tp);write(root/'passive/result.json',tr)
    meta = dict(kind='saved_state',inputs=hashes,recipe=recipe,effective=effective,evidence=evidence,
                model_sha256=s.sha(model),model_parameters_sha256='0'*64)
    request = dict(case=dict(kind='saved_state',id='synthetic',config=inputs['config']['path'],design=str(profile_path),recipe=recipe),
        criteria=[dict(id='pitch',role='hard',observable='played_frequency',windows=['hold'],target=dict(value=111.,unit='Hz'),tolerance=dict(value=5.,unit='cent'))],
        reference_lips={'mass_kg':.0001},plateaus=[{'pressure_pa':1000.}],windows=[{'id':'hold'}],
        observation={'method':'synthetic'},initial={'kind':'native'},budgets={'synthetic_budget':1})
    rp = dict(request=request,metadata=meta,input_files=inputs,producer=producer)
    rr = dict(plan=rp,status='complete',context_checks={'finite_fidelity':fidelity},analysis={'criteria':[
        dict(id='pitch',role='hard',observable='played_frequency',status='pass',values_si=[111.],unit_si='Hz',windows=['hold'],reason=None)]})
    write(root/'played/plan.json',rp);write(root/'played/result.json',rr)
    for name in ('passive','geometry','played'):write(root/name/'manifest.json',{'synthetic_stub':True})
    raw = dict(schema_version=s.SCHEMA,budgets=dict(s.LIMITS),candidates=[dict(id='one',passive=dict(bundle='passive',scenario='nominal',projection='fixed'),
        geometry={'bundle':'geometry'},played=[dict(id='hold',bundle='played')])])
    return write(root/'job.json',raw)


@pytest.fixture
def job(tmp_path): return synthetic_job(tmp_path)


def mocked_native(bundle, directory, index, budgets, deadline, **kwargs):
    """Explicit unit-test reader double, never claimed as native validation."""
    root=Path(bundle['path']);kind=bundle['kind']
    if kind=='tolerance_audit': value=s.read(root/'result.json')
    elif kind=='geometry_state': value=dict(ok=True,metadata=s.read(root.parent/'played/plan.json')['metadata'])
    else:
        identity=flow.reader_identity()
        value=dict(ok=True,result=s.read(root/'result.json'),reading={'producer':{'synthetic':True},'reader':{
            'software':{'sha':identity['sha']}, 'versions':{'python':identity['python']}, 'loaded_sources_sha256':identity['sources']}})
    stdout=write(directory/f'native_{index:03d}.json',value)
    stderr=directory/f'native_{index:03d}.stderr.txt';stderr.write_bytes(b'')
    receipt=dict(kind=kind,bundle=str(root),pid=999999,exit_code=0,reaped=True,pipes_closed=True,timed_out=False,cancelled=False,
        seconds=.01,wall_limit_seconds=175.,memory_mib=768,file_bytes_limit=s.FILE_LIMIT,blas_threads=1,fresh_interpreter=True,
        stdout=stdout.name,stdout_sha256=s.sha(stdout),stderr=stderr.name,stderr_sha256=s.sha(stderr))
    return value,receipt


def reseal(out):
    """Rehash disposable test artifacts; deliberately not an authentication test."""
    m=s.read(out/'manifest.json');m['files']={n:s.sha(out/n) for n in m['files']};write(out/'manifest.json',m)
    e=s.read(out/'execution.json');e['manifest_sha256']=s.sha(out/'manifest.json');write(out/'execution.json',e)
    write(out/'execution.closed.json',dict(execution_sha256=s.sha(out/'execution.json'),children_collected=True))
    write(out/'execution.completed.json',dict(schema=s.RESULT_SCHEMA,manifest_sha256=s.sha(out/'manifest.json'),
        execution_sha256=s.sha(out/'execution.json'),closure_sha256=s.sha(out/'execution.closed.json')))


@pytest.mark.parametrize('change', ['unknown','duplicate','bool','nan','absolute','projection','experiments','reads','seconds','output','kind'])
def test_strict_admission_before_output(job,tmp_path,change,monkeypatch):
    raw=s.read(job)
    if change=='unknown':raw['extra']=1
    elif change=='duplicate':raw['candidates']*=2
    elif change=='bool':raw['budgets']['child_seconds']=True
    elif change=='nan':raw['budgets']['child_seconds']=float('inf')
    elif change=='absolute':raw['candidates'][0]['geometry']['bundle']=str(tmp_path/'geometry')
    elif change=='projection':raw['candidates'][0]['passive']['projection']='assembly'
    elif change=='experiments':raw['budgets']['experiments']=0
    elif change=='reads':raw['budgets']['native_reads']=1
    elif change=='seconds':raw['budgets']['total_seconds']=5
    elif change=='output':raw['budgets']['output_mib']=3
    elif change=='kind':raw['candidates'][0]['kind']='optimizer'
    job.write_text(json.dumps(raw))
    monkeypatch.setattr(flow,'read_native',lambda *a,**k:pytest.fail('reader before admission'))
    with pytest.raises(ValueError):flow.run(job,tmp_path/'out')
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('raw',['{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}','{"x":1e999}'])
def test_strict_json(raw):
    with pytest.raises(ValueError):s.loads(raw)


def test_duplicate_experiments_paths_and_no_symlink(job,tmp_path):
    raw=s.read(job);raw['candidates'][0]['played']*=2;write(job,raw)
    with pytest.raises(ValueError,match='dupliquée'):s.preflight(job)
    raw['candidates'][0]['played']=raw['candidates'][0]['played'][:1]
    (tmp_path/'link').symlink_to(tmp_path/'geometry',target_is_directory=True)
    raw['candidates'][0]['geometry']['bundle']='link';write(job,raw)
    with pytest.raises(ValueError,match='symbolique'):s.preflight(job)


def test_preflight_zero_science_no_cache_no_destination(job,tmp_path,monkeypatch):
    import numpy as np
    def no(*a,**k):pytest.fail('science/reader in preflight')
    monkeypatch.setattr(np.linalg,'eigvals',no);monkeypatch.setattr(flow,'read_native',no)
    before={str(p) for p in tmp_path.rglob('*')}
    p=flow.run(job,tmp_path/'out',dry_run=True)
    assert p['ok'] and p['plan']['native_read_count']==3 and not p['output_created']
    assert {str(p) for p in tmp_path.rglob('*')}==before
    raw=s.read(job);other=copy.deepcopy(raw['candidates'][0]);other['id']='two';raw['candidates'].append(other);write(job,raw)
    assert s.preflight(job)['native_read_count']==3


@pytest.mark.parametrize('field',['model','materials','recipe','geometry','fidelity','unsupported'])
def test_bad_association_before_reader(job,tmp_path,field,monkeypatch):
    p=tmp_path/'played/plan.json';r=s.read(p)
    if field=='model':r['metadata']['model_sha256']='f'*64
    elif field=='materials':r['metadata']['inputs']['materials']='f'*64
    elif field=='recipe':r['request']['case']['recipe']['h_cm']=1.
    elif field=='geometry':r['request']['case']['design']=str(tmp_path/'config.json')
    elif field=='unsupported':r['request']['case']['kind']='saved'
    elif field=='fidelity':
        f=tmp_path/'played/result.json';v=s.read(f);v['context_checks']['finite_fidelity']={};write(f,v)
    write(p,r)
    monkeypatch.setattr(flow,'read_native',lambda *a,**k:pytest.fail('heavy reader'))
    with pytest.raises(ValueError):flow.run(job,tmp_path/'out')
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('statuses,complete,expected',[
    (['pass','satisfied'],True,'conforming'),(['pass','fail'],True,'violated'),
    (['fail','unresolved'],False,'violated'),(['pass','unsupported'],True,'unknown'),
    (['pass'],False,'unknown'),([],True,'descriptive')])
def test_conjunction_no_compensation(statuses,complete,expected):
    rows=[dict(role='hard',status=x,value_si=1000 if x=='pass' else None) for x in statuses]
    rows.append(dict(role='observe',status='violated',value_si=None))
    v=report.verdict(rows,complete);assert v['hard_status']==expected
    assert v['observation_coverage']['violated']==1
    assert 'score' not in v


def test_composition_comparison_and_native_margins(job,tmp_path,monkeypatch):
    raw=s.read(job);other=copy.deepcopy(raw['candidates'][0]);other['id']='two';raw['candidates'].append(other);write(job,raw)
    monkeypatch.setattr(flow,'read_native',mocked_native)
    result=flow.run(job,tmp_path/'out');assert result['ok'] and len(result['children'])==3
    c=result['payload']['candidates'][0]
    assert c['conforming'] and c['observation_coverage']['unresolved']==1
    played=[r for r in c['criteria'] if r['kind']=='played'][0]
    assert played['value_si']==111. and played['margin']==5. and played['margin_unit']=='cent'
    pair=result['payload']['comparisons'][0]
    assert pair['same_contract'] and pair['same_playing_protocol'] and pair['comparable']
    assert report.read_result(tmp_path/'out')['ok']
    for field in ('reference_lips','observation','windows','plateaus','budgets','criteria'):
        b=copy.deepcopy(result['payload']['candidates'][1]);b['contract']['played']['hold'][field]={'different':True}
        if field not in ('budgets','criteria'):b['playing_protocol']['hold'][field]={'different':True}
        p=report.compare([c,b])[0]
        assert not p['same_contract'] and not p['comparable']
        assert all(d['delta_b_minus_a'] is None for d in p['deltas'])


@pytest.mark.parametrize('change',['value','numeric_bool','role','profile','contract','receipt','reader','missing','summary'])
def test_rehash_does_not_hide_semantic_tamper(job,tmp_path,monkeypatch,change):
    monkeypatch.setattr(flow,'read_native',mocked_native);out=tmp_path/'out';assert flow.run(job,out)['ok']
    result=s.read(out/'result.json')
    if change=='value':result['payload']['candidates'][0]['criteria'][0]['value_si']=2.
    elif change=='numeric_bool':result['payload']['candidates'][0]['criteria'][0]['value_si']=True
    elif change=='role':result['payload']['candidates'][0]['criteria'][1]['role']='hard'
    elif change=='contract':result['payload']['candidates'][0]['contract']['played']['hold']['reference_lips']={}
    elif change=='reader':result['reader']['sources'][s.OWN_SOURCES[0]]='0'*64
    elif change=='missing':result['payload']['candidates'][0]['criteria'].pop()
    elif change=='profile':(out/'profile_one.json').write_text('{}')
    elif change=='summary':(out/'summary.txt').write_text('Conforme et validé physiquement')
    elif change=='receipt':
        r=s.read(out/'execution.json');r['children'][0]['reaped']=False;write(out/'execution.json',r)
    write(out/'result.json',result);reseal(out)
    try: read=report.read_result(out)
    except ValueError: return
    assert not read['ok']


def test_mutation_after_admission_and_terminal_mutation(job,tmp_path,monkeypatch):
    plan=s.preflight(job);(tmp_path/'config.json').write_text('{}')
    with pytest.raises(ValueError,match='modifiée'):flow.boundary(plan)


def test_terminal_mutation_during_reread(job,tmp_path,monkeypatch):
    monkeypatch.setattr(flow,'read_native',mocked_native);out=tmp_path/'out';assert flow.run(job,out)['ok']
    def changed(*args,**kwargs):
        value=mocked_native(*args,**kwargs);(out/'execution.closed.json').write_text('{}');return value
    monkeypatch.setattr(flow,'read_native',changed)
    assert not report.read_result(out)['ok']


def test_partial_preserves_verified_violation(job,tmp_path,monkeypatch):
    p=tmp_path/'passive/result.json';r=s.read(p);r['ok']=False;r['execution_complete']=False
    r['observations'][0]['criteria'][0].update(status='violated',value_si=2.,margin=-.99);write(p,r)
    monkeypatch.setattr(flow,'read_native',mocked_native)
    result=flow.run(job,tmp_path/'out')
    assert not result['ok'] and result['payload']['candidates'][0]['hard_status']=='violated'
    reread=report.read_result(tmp_path/'out')
    assert not reread['ok'] and reread['semantic_verified']
    assert reread['payload']['candidates'][0]['hard_status']=='violated'


def test_real_native_audit_reader_zero_tmm(tmp_path):
    from didgeridoo_optimizer.tests.test_tolerance_audit import sealed_bundle
    _,bundle=sealed_bundle(tmp_path)
    response,child=flow.read_native(dict(kind='tolerance_audit',path=str(bundle)),tmp_path,90,dict(s.LIMITS),time.monotonic()+20)
    assert response['ok'] and response['semantic_verified'] and response['counters']['spectral_calls']==0
    assert child['exit_code']==0 and child['reaped'] and child['pipes_closed']


def test_real_timeout_collects_and_preserves_host(tmp_path,monkeypatch):
    original_limits=[resource.getrlimit(x) for x in (resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE)]
    handler=signal.getsignal(signal.SIGALRM);timer=signal.getitimer(signal.ITIMER_REAL)
    monkeypatch.setattr(flow,'CHILD','import time; time.sleep(30)')
    response,child=flow.read_native(dict(kind='tolerance_audit',path=str(tmp_path)),tmp_path,0,dict(s.LIMITS),time.monotonic()+.15)
    assert response['status']=='timeout' and child['timed_out'] and child['reaped'] and child['pipes_closed']
    with pytest.raises(ProcessLookupError):os.kill(child['pid'],0)
    assert original_limits==[resource.getrlimit(x) for x in (resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE)]
    assert handler==signal.getsignal(signal.SIGALRM) and timer==signal.getitimer(signal.ITIMER_REAL)


def test_real_cancellation_collects_receipt(tmp_path,monkeypatch):
    monkeypatch.setattr(flow,'CHILD','import time; time.sleep(30)')
    original=subprocess.Popen.wait;raised=False
    def interrupt(self,*args,**kwargs):
        nonlocal raised
        if not raised:raised=True;raise KeyboardInterrupt()
        return original(self,*args,**kwargs)
    monkeypatch.setattr(subprocess.Popen,'wait',interrupt)
    response,child=flow.read_native(dict(kind='tolerance_audit',path=str(tmp_path)),tmp_path,0,dict(s.LIMITS),time.monotonic()+5)
    assert response['status']=='cancelled' and child['cancelled'] and child['reaped']
    with pytest.raises(ProcessLookupError):os.kill(child['pid'],0)


def test_total_budget_refuses_before_launch(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('process beyond total budget'))
    with pytest.raises(TimeoutError):
        flow.read_native(dict(kind='tolerance_audit',path=str(tmp_path)),tmp_path,0,dict(s.LIMITS),time.monotonic()-1)
    assert not list(tmp_path.iterdir())


def test_oversized_input_and_existing_destination(job,tmp_path):
    out=tmp_path/'existing';out.mkdir()
    with pytest.raises(ValueError,match='destination'):flow.run(job,out,dry_run=True)
    path=tmp_path/'played/too-large.bin'
    with path.open('wb') as stream:stream.truncate(s.FILE_LIMIT+1)
    with pytest.raises(ValueError,match='fichier'):s.preflight(job,tmp_path/'new')
    assert not (tmp_path/'new').exists()


def test_checkpoint_ancestors_and_series_are_pinned(job,tmp_path):
    series=tmp_path/'series.bin';series.write_bytes(b'synthetic series bytes')
    ancestor=write(tmp_path/'ancestor.json',{'series':{'file':{'path':str(series),'sha256':s.sha(series)}}})
    write(tmp_path/'played/checkpoint.json',{'previous':{'path':str(ancestor),'sha256':s.sha(ancestor)}})
    plan=s.preflight(job)
    assert str(ancestor) in plan['pins'] and str(series) in plan['pins']
    series.write_bytes(b'changed')
    with pytest.raises(ValueError):flow.boundary(plan)


def test_cannot_hide_native_violation_by_omitting_reader(job,tmp_path,monkeypatch):
    monkeypatch.setattr(flow,'read_native',mocked_native);out=tmp_path/'out';assert flow.run(job,out)['ok']
    r=s.read(out/'result.json');r['native_results'].pop(next(k for k in r['native_results'] if k.startswith('tolerance_audit:')))
    write(out/'result.json',r);reseal(out)
    with pytest.raises(ValueError,match='omise'):report.read_result(out)


@pytest.mark.parametrize('value',[True,float('inf')])
def test_numeric_measurement_cannot_be_bool_or_infinite(job,tmp_path,monkeypatch,value):
    monkeypatch.setattr(flow,'read_native',mocked_native)
    if value is True:
        p=tmp_path/'played/result.json';r=s.read(p);r['analysis']['criteria'][0]['values_si']=[True];write(p,r)
        result=flow.run(job,tmp_path/'out');assert not result['ok']
    else:
        with pytest.raises(ValueError):s.number(value,'measurement')


def test_unsupported_multiple_windows_preserves_all_obligations(job,tmp_path,monkeypatch):
    path=tmp_path/'played/plan.json';p=s.read(path)
    p['request']['criteria'].append(dict(id='unsupported',role='hard',observable='primitive_period',windows=['a','b']))
    write(path,p)
    path=tmp_path/'played/result.json';r=s.read(path);r['plan']=p
    r['analysis']['criteria'].append(dict(id='unsupported',role='hard',observable='primitive_period',status='unsupported',
        windows=['a','b'],values_si=[None],unit_si=None,reason='unsupported native obligation'))
    write(path,r)
    monkeypatch.setattr(flow,'read_native',mocked_native)
    result=flow.run(job,tmp_path/'out')
    assert result['ok'] and result['payload']['candidates'][0]['hard_status']=='unknown'
    rows=result['payload']['candidates'][0]['criteria']
    assert len([r for r in rows if r['id']=='unsupported'])==2
    assert all(r['value_si'] is None for r in rows if r['id']=='unsupported')


def test_repeated_experiments_budget_before_output(job,tmp_path):
    raw=s.read(job);raw['budgets']['output_mib']=8
    for i in range(8):
        exp=copy.deepcopy(raw['candidates'][0]['played'][0]);exp['id']='p'+str(i)
        if i==0:raw['candidates'][0]['played']=[exp]
        else:raw['candidates'][0]['played'].append(exp)
    raw['candidates']=[dict(copy.deepcopy(raw['candidates'][0]),id='c'+str(i)) for i in range(8)]
    write(job,raw)
    with pytest.raises(ValueError,match='budget'):s.preflight(job,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_native_reader_identity_rehash_refused(job,tmp_path,monkeypatch):
    monkeypatch.setattr(flow,'read_native',mocked_native);out=tmp_path/'out';assert flow.run(job,out)['ok']
    result=s.read(out/'result.json');key=next(k for k in result['native_results'] if k.startswith('register_target:'))
    name=result['native_results'][key];native=s.read(out/name);native['reading']['reader']['software']['sha']='1'*40;write(out/name,native)
    e=s.read(out/'execution.json')
    for child in e['children']:
        if child['stdout']==name:child['stdout_sha256']=s.sha(out/name)
    write(out/'execution.json',e);reseal(out)
    assert not report.read_result(out)['ok']
