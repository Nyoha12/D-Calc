"""Public supervised CLI/API, native passive oracles and durable receipt failures."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from didgeridoo_optimizer.pipeline import assembly_path as pipeline
from didgeridoo_optimizer.reporting import assembly_path as reporting
from didgeridoo_optimizer.optimization.constrained_search import search
from didgeridoo_optimizer.optimization.assembly_contract import AssemblyContract
from didgeridoo_optimizer.geometry.assemblies import Assembly
from didgeridoo_optimizer.pipeline.design_input import load_design
from didgeridoo_optimizer.tests.test_assembly_contract import (
    ROOT, EXAMPLE, fixed_inputs, geometry_request, make)
from didgeridoo_optimizer.tests.test_assemblies import q, specimen


def files(tmp_path, data=None):
    if data is None:
        data = fixed_inputs()
    context, raw, request = data
    config = copy.deepcopy(context['config'])
    config['materials']['database_file'] = str(ROOT/'project_specs/materials_base_v1.yaml')
    config['materials']['variant_rules_file'] = str(ROOT/'project_specs/wood_variant_rules_v1.yaml')
    paths = []
    for name, value in [('config',config), ('assembly',raw), ('request',request)]:
        path = tmp_path/(name+'.json'); path.write_text(json.dumps(value)); paths.append(path)
    return paths


def direct(tmp_path, paths):
    contract, plan = pipeline.load_inputs(*paths)
    out = tmp_path/'data'; out.mkdir()
    reporting.write_json(out/'plan.json', plan)
    return contract, out, pipeline.execute(contract, plan, out)


def cli(paths, output, *args, entry=None, timeout=45):
    cmd = [sys.executable, '-B'] + ([str(entry)] if entry else ['-m','tools.assembly_path'])
    cmd += ['--config',str(paths[0]),'--assembly',str(paths[1]),'--request',str(paths[2]),'--output-dir',str(output),*args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
        env=dict(os.environ,PYTHONPATH=str(ROOT)), cwd=ROOT)


def peak(f, order=1, basin=None, status='resolved'):
    return dict(order=order, frequency_hz=f, status=status, reason=None,
                basin_hz=basin or [f-1,f+1], frequency_estimate_hz=1.e-5)


def test_modes_order_missing_and_no_nearest_target():
    modes = {'low':dict(order=1,window_hz=[90,120]), 'optional':dict(order=3,window_hz=[500,600])}
    selected = pipeline.track_modes([peak(100),peak(300,2)],modes)
    assert selected['low']['status'] == 'resolved'
    assert selected['optional']['status'] == 'unresolved'
    selected = pipeline.track_modes([peak(80),peak(101,2)],modes)
    assert selected['low']['status'] == 'unresolved'
    assert selected['low']['peak']['frequency_hz'] == 80


def test_high_neighbour_entry_exit_does_not_rename_low_requested_modes():
    modes = {'m1':dict(order=1,window_hz=[11,999]), 'm2':dict(order=2,window_hz=[11,999])}
    old = [peak(100),peak(300,2)]
    new = old+[peak(990,3)]
    assert all(v['status']=='resolved' for v in pipeline.track_modes(new,modes,old).values())
    assert all(v['status']=='resolved' for v in pipeline.track_modes(old,modes,new).values())
    low_entry = [peak(60),peak(100,2),peak(300,3)]
    assert all(v['status']=='unresolved' for v in pipeline.track_modes(low_entry,modes,old).values())


def test_true_ambiguous_low_peak_stays_unresolved():
    mode = {'m':dict(order=1,window_hz=[50,150])}
    assert pipeline.track_modes([peak(100,status='unresolved')],mode)['m']['status']=='unresolved'


def test_dry_run_no_spectrum_no_files_even_played(tmp_path, monkeypatch):
    data = fixed_inputs()
    c = data[2]['projections'][0]['request']['criteria'][0]
    c.pop('expression');c.update(observable='played_frequency',level='played',unit='Hz',target=q(140,'Hz'),tolerance=q(1,'Hz'))
    paths = files(tmp_path,data)
    monkeypatch.setattr(pipeline.ProjectionEvaluator,'spectrum',lambda *a:pytest.fail('dry-run spectrum'))
    response = pipeline.run(*paths,tmp_path/'dry',dry_run=True)
    assert response['ok'] and not (tmp_path/'dry').exists()
    assert not list(tmp_path.glob('design*'))


def test_fixed_geometry_native_evaluation_and_nondestructive_destination(tmp_path):
    paths = files(tmp_path)
    result = pipeline.run(*paths,tmp_path/'out')
    assert result['ok'] and result['hard_conforming']
    contract,_ = pipeline.load_inputs(*paths)
    reloaded = reporting.read_result(tmp_path/'out',contract)
    assert reloaded['ok']
    best = reloaded['result']['best']
    assert best['variables_si']==[] and len(best['bom'])==1
    assert best['positions'][0]['q_m'] is None
    projection = best['projections'][0]
    design = load_design(tmp_path/'out'/projection['design_file'],contract.context['material_db'],contract.context['config'])[1]
    assert design.as_dict()==projection['physical_design']
    with pytest.raises(FileExistsError):pipeline.run(*paths,tmp_path/'out')
    assert reloaded['result']['counters']['spectral_calls']==0


def test_unavailable_optional_mode_does_not_veto_hard_geometry(tmp_path):
    data=fixed_inputs();req=data[2]['projections'][0]['request']
    req['modes']=[dict(id='absent',order=32,window_hz=[100,650])]
    req['criteria'].append(dict(id='optional',observable='resonance_frequency',mode='absent',
        target=q(300,'Hz'),unit='Hz',tolerance=q(1,'Hz'),level='passive',scope={},role='observe'))
    _,_,result=direct(tmp_path,files(tmp_path,data))
    assert result['sampled_hard_conforming'] and result['hard_conforming']
    assert result['best']['criteria'][1]['status']=='unresolved'
    assert not result['request_fully_covered']


def test_played_hard_not_substituted_and_optional_independent(tmp_path):
    data=fixed_inputs();req=data[2]['projections'][0]['request']
    req['criteria'].append(dict(id='played',observable='played_frequency',target=q(100,'Hz'),
        unit='Hz',tolerance=q(1,'Hz'),level='played',scope={},role='observe'))
    _,_,result=direct(tmp_path,files(tmp_path,data))
    assert result['hard_conforming'] and not result['request_fully_covered']
    assert result['best']['criteria'][1]['status']=='unsupported'


def test_budget_one_keeps_unverified_witness_no_vacuous_compliance(tmp_path):
    data=fixed_inputs();data[2]['budgets']['evaluations']=1
    _,out,result=direct(tmp_path,files(tmp_path,data))
    assert result['status']=='partial'
    assert result['best']['criteria'][0]['status']=='satisfied'
    assert not result['sampled_hard_conforming']
    assert result['counters']['evaluations']==1
    assert (out/'checkpoint_0001.json').exists()


def test_global_projection_budget_keeps_partial_rows(tmp_path):
    context,_,request=fixed_inputs()
    raw=specimen();request['variables']=[]
    request['projections']=[dict(id=name,configuration=name,request=geometry_request(target))
        for name,target in [('short',.590895109027624),('long',.9999350298196076)]]
    request['budgets']['projections']=1
    _,_,result=direct(tmp_path,files(tmp_path,(context,raw,request)))
    assert result['status']=='partial' and result['counters']['projections']==1
    assert result['best']['criteria'][0]['status']=='satisfied'
    assert result['best']['criteria'][1]['status']=='unresolved'
    assert not result['request_fully_covered']


def test_all_hard_positions_before_preference_no_compensation(tmp_path):
    context,_,request=fixed_inputs();raw=specimen();request['variables']=[]
    request['projections']=[dict(id=name,configuration=name,request=geometry_request(target))
        for name,target in [('short',.590895109027624),('long',.8)]]
    _,_,result=direct(tmp_path,files(tmp_path,(context,raw,request)))
    assert result['best']['criteria'][0]['status']=='satisfied'
    assert result['best']['criteria'][1]['status']=='violated'
    assert not result['hard_conforming'] and result['status']=='violated'


def test_continuum_geometry_certificate_separate_from_acoustic_coverage(tmp_path):
    context,_,request=fixed_inputs();raw=specimen();request['variables']=[]
    request['coverage']={'kind':'continuous','path':'travel'}
    request['projections']=[dict(id=name,configuration=name,request=geometry_request(target))
        for name,target in [('short',.590895109027624),('long',.9999350298196076)]]
    _,_,result=direct(tmp_path,files(tmp_path,(context,raw,request)))
    assert result['sampled_hard_conforming']
    assert not result['request_fully_covered'] and not result['hard_conforming']
    certificate=result['best']['geometry_certificate']['paths'][0]
    assert certificate['geometry_certified'] and not certificate['acoustics_certified']
    assert result['plan']['parent_request']==request
    # Independent interior counterexample: endpoints alone cannot certify acoustics.
    assert 6*math.sin(math.pi*.5)**2>5 and math.sin(math.pi)**2<1e-20


def test_budget_counts_native_tmm_calls_and_prevents_allocation(tmp_path,monkeypatch):
    data=fixed_inputs();req=data[2]['projections'][0]['request']
    req['criteria']=[dict(id='f1',observable='resonance_frequency',mode='m1',target=q(140,'Hz'),
        unit='Hz',tolerance=q(1,'Hz'),level='passive',scope={},role='hard')]
    data[2]['budgets']['spectral_calls']=1
    count=0;native=pipeline.input_impedance
    def spy(*args,**kwargs):
        nonlocal count;count+=1
        return native(*args,**kwargs)
    monkeypatch.setattr(pipeline,'input_impedance',spy)
    _,_,result=direct(tmp_path,files(tmp_path,data))
    assert count==result['counters']['spectral_calls']==1
    assert result['status']=='partial' and not result['request_fully_covered']


@pytest.fixture(scope='module')
def solved(tmp_path_factory):
    tmp=tmp_path_factory.mktemp('assembly_solved')
    paths=[EXAMPLE/'config.yaml',EXAMPLE/'assembly.yaml',EXAMPLE/'request.yaml']
    done=cli(paths,tmp/'out',timeout=90)
    assert done.returncode==0,done.stdout+done.stderr
    response=json.loads(done.stdout)
    assert response['ok'] and response['conforming'],response
    contract,_=pipeline.load_inputs(*paths)
    bundle=reporting.read_result(tmp/'out',contract)
    return tmp/'out',contract,bundle['result']


def test_real_shared_dimensions_position_search_and_catalogue(solved):
    out,c,result=solved
    assert c.initial==[.56,.4]
    best=result['best']
    assert best['variables_si']!=c.initial
    assert abs(best['variables_si'][0]-.550895109027624)<2e-5
    assert abs(best['variables_si'][1]-.4190399207919836)<2e-5
    assert max(abs(row['error_cents']) for row in best['criteria'])<.2
    assert [a['status'] for a in result['alternatives']]==['rejected_or_unresolved']*2+['admissible_witness']
    assert result['discrete_catalogue_exhausted']
    assert not result['continuous_design_impossibility_proven']
    assert all(a['candidate_best'].get('reason') for a in result['alternatives'][:2])
    assert result['counters']['evaluations']<=c.budgets['evaluations']
    assert result['counters']['projections']>2
    assert len(best['bom'])==3
    assert [r['global_evaluation'] for r in result['history']]==list(range(1,len(result['history'])+1))
    assert result['search_witness']['verified'] is False


def test_r42_ratio_obligation_violates_despite_good_f1(solved,tmp_path):
    _,old,result=solved
    raw=result['best']['assembly'];req=old.request;req['variables']=[];req['catalogue']=[{'id':'nominal','values':{}}]
    for p in req['projections']:
        p['request']['modes'].append(dict(id='m2',order=2,window_hz=[200,500]))
        p['request']['criteria'].append(dict(id='ratio',observable='resonance_ratio',numerator='m2',denominator='m1',
            target=q(3.0160503796168734,'1'),unit='1',tolerance=q(5,'cent'),level='passive',scope={},role='hard'))
    _,_,result=direct(tmp_path,files(tmp_path,(old.context,raw,req)))
    rows={r['id']:r for r in result['best']['criteria']}
    assert rows['short:f1']['status']==rows['long:f1']['status']=='satisfied'
    assert rows['long:ratio']['status']=='violated'
    assert rows['long:ratio']['error_cents']>400
    assert not result['hard_conforming']


def test_fresh_reader_without_cli_import_and_context_changes(solved,tmp_path):
    out,_,_=solved
    code=f'''from didgeridoo_optimizer.pipeline.assembly_path import load_inputs
from didgeridoo_optimizer.reporting.assembly_path import read_result,read_checkpoint
import sys
c,_=load_inputs({str(EXAMPLE/'config.yaml')!r},{str(EXAMPLE/'assembly.yaml')!r},{str(EXAMPLE/'request.yaml')!r})
assert 'tools.assembly_path' not in sys.modules
assert read_result({str(out)!r},c)['ok']
assert read_checkpoint({str(out)!r},c)['solver_state_complete'] is False
'''
    done=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,cwd=tmp_path,
                        env=dict(os.environ,PYTHONPATH=str(ROOT)),timeout=30)
    assert done.returncode==0,done.stderr


@pytest.mark.parametrize('mutation',['request','config','assembly','versions','sources','candidate'])
def test_checkpoint_changed_context_or_witness_rejected(solved,tmp_path,mutation):
    out,c,_=solved
    checkpoint=json.loads(sorted(out.glob('checkpoint_*.json'))[-1].read_text())
    if mutation in ('request','config','assembly'):
        changed=copy.deepcopy(c)
        if mutation=='request':changed._request['metadata']={'changed':True}
        elif mutation=='config':changed.context['config']['environment']['sound_speed_m_s']+=1
        else:changed.base['pieces']['outer']['length']['value']+=.01
        with pytest.raises(ValueError):reporting.read_checkpoint(out,changed)
        return
    if mutation=='versions':checkpoint['producer_provenance']['versions']['python']='changed'
    if mutation=='sources':checkpoint['producer_provenance']['loaded_sources_sha256']['tools/assembly_path.py']='0'*64
    if mutation=='candidate':checkpoint['candidate']['variables_si'][0]+=.001
    checkpoint['checkpoint_sha256']=reporting.fingerprint({k:v for k,v in checkpoint.items() if k!='checkpoint_sha256'})
    reporting.write_json(tmp_path/'checkpoint_0001.json',checkpoint)
    with pytest.raises(ValueError):reporting.read_checkpoint(tmp_path,c)


def test_manifest_without_command_closure_is_not_success(tmp_path):
    reporting.write_json(tmp_path/'manifest.json',{})
    assert not reporting.read_result(tmp_path)['ok']
    reporting.write_json(tmp_path/'execution.json',dict(ok=True,child={'exit_code':0,'reaped':True}))
    assert not reporting.read_result(tmp_path)['ok']


@pytest.mark.parametrize('phase',['before','after'])
def test_interruption_publication_priority(tmp_path,monkeypatch,phase):
    paths=files(tmp_path);original=reporting.write_json
    def interrupt(path,value,**kw):
        if Path(path).name=='execution.closed.json':
            if phase=='after':original(path,value,**kw)
            raise KeyboardInterrupt('prescribed publication interruption')
        return original(path,value,**kw)
    monkeypatch.setattr(reporting,'write_json',interrupt)
    with pytest.raises(KeyboardInterrupt):pipeline.run(*paths,tmp_path/'out')
    assert not reporting.read_result(tmp_path/'out')['ok']


def test_real_cli_dry_refusal_and_copied_entry(tmp_path):
    paths=files(tmp_path)
    dry=cli(paths,tmp_path/'dry','--dry-run')
    assert dry.returncode==0 and not (tmp_path/'dry').exists()
    (tmp_path/'existing').mkdir()
    assert cli(paths,tmp_path/'existing').returncode!=0
    (tmp_path/'link').symlink_to(tmp_path/'target',target_is_directory=True)
    assert cli(paths,tmp_path/'link','--dry-run').returncode!=0
    copied=tmp_path/'copied_cli.py';copied.write_bytes((ROOT/'tools/assembly_path.py').read_bytes())
    response=cli(paths,tmp_path/'copied','--dry-run',entry=copied)
    assert response.returncode==2 and 'provenance CLI' in response.stdout
    assert not (tmp_path/'copied').exists()


def test_real_sigterm_closes_only_owned_child(tmp_path):
    out=tmp_path/'interrupted'
    args=[sys.executable,'-B','-m','tools.assembly_path','--config',str(EXAMPLE/'config.yaml'),
          '--assembly',str(EXAMPLE/'assembly.yaml'),'--request',str(EXAMPLE/'request.yaml'),'--output-dir',str(out)]
    child=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    deadline=time.monotonic()+20
    while not (out/'plan.json').exists() and child.poll() is None and time.monotonic()<deadline:
        time.sleep(.02)
    time.sleep(.1)
    child.send_signal(signal.SIGTERM)
    stdout,stderr=child.communicate(timeout=10)
    assert child.returncode!=0,stdout+stderr
    assert not reporting.read_result(out)['ok']


def test_native_search_last_probe_kept_before_budget():
    c=SimpleNamespace(budgets=dict(evaluations=2,iterations=1,seconds=5),initial=[.5],
        variables=[dict(low=0,high=1)],preference_supported=True,
        criteria=[dict(id='h',role='hard',unsupported_reason=None)])
    values=iter([2.,.5])
    def evaluate(x):
        v=next(values)
        return dict(criteria=[dict(id='h',status='satisfied' if v<=1 else 'violated',residual_normalized=v)],search_feasible=v<=1)
    result=search(c,evaluate)
    assert result['best']['search_feasible'] and result['best']['stage']=='finite_difference'


def test_homogeneous_cylinder_matches_native_tmm_without_slice_identity(tmp_path):
    # Independent series-matrix identity: two equal cylinders versus one whole tube.
    from didgeridoo_optimizer.geometry.builders import DesignBuilder
    from didgeridoo_optimizer.acoustics.transfer_matrix import input_impedance
    context,raw,req=fixed_inputs()
    a=DesignBuilder().build(dict(id='one',segments=[dict(kind='cylinder',length_cm=100,d_in_cm=3,d_out_cm=3,material_id='pvc_pressure')]))
    b=DesignBuilder().build(dict(id='two',segments=[dict(kind='cylinder',length_cm=l,d_in_cm=3,d_out_cm=3,material_id='pvc_pressure') for l in (30,70)]))
    air,loss,radiation,_=pipeline.models(context,{'loss_model':'legacy','air_reference':None,'radiation_model':'legacy'})
    frequencies=np.linspace(50,500,91)
    z=[input_impedance(frequencies,d,context['material_db'],air,exit_radius_m=.015,loss_model=loss,radiation_model=radiation) for d in (a,b)]
    np.testing.assert_allclose(z[0],z[1],rtol=2e-12,atol=1e-8)


def test_real_extractor_detects_low_twin_peak_ambiguity():
    def twin(f):
        f=np.asarray(f)
        return (1+np.exp(-((f-100.12)/.1)**2)+np.exp(-((f-99.88)/.1)**2)).astype(complex)
    peaks=pipeline.extract_peaks(twin,dict(min_hz=90,max_hz=110,refinement_hz=1.e-5),1.)
    assert peaks and peaks[0]['status']=='unresolved'
    selected=pipeline.track_modes(peaks,{'low':dict(order=1,window_hz=[91,109])})
    assert selected['low']['status']=='unresolved'


def test_only_rejected_catalogue_still_exports_reasons(tmp_path):
    context,_,_=fixed_inputs()
    raw=yaml.safe_load((EXAMPLE/'assembly.yaml').read_text())
    req=yaml.safe_load((EXAMPLE/'request.yaml').read_text());req['catalogue']=req['catalogue'][:2]
    c,out,result=direct(tmp_path,files(tmp_path,(context,raw,req)))
    assert result['discrete_catalogue_exhausted'] and not result['hard_conforming']
    assert len(result['alternatives'])==2
    assert reporting.read_checkpoint(out,c)['candidate']['reason']
    assert not list(out.glob('design_*.json'))


def test_true_midpoint_counterexample_not_hidden_by_correct_endpoints(solved,tmp_path):
    _,old,solved_result=solved
    raw=copy.deepcopy(solved_result['best']['assembly']);req=old.request
    req['variables']=[];req['catalogue']=[dict(id='nominal',values={})]
    req['coverage']={'kind':'continuous','path':'travel'}
    qs=raw['configurations']['short']['q']['slide']['value']
    ql=raw['configurations']['long']['q']['slide']['value']
    raw['configurations']['middle']={'q':{'slide':q((qs+ql)/2)}}
    middle=copy.deepcopy(req['projections'][0]);middle.update(id='middle',configuration='middle')
    middle['request']['criteria'][0]['target']=q(105.,'Hz')
    req['projections'].append(middle)
    _,_,result=direct(tmp_path,files(tmp_path,(old.context,raw,req)))
    rows={r['id']:r for r in result['best']['criteria']}
    assert rows['short:f1']['status']==rows['long:f1']['status']=='satisfied'
    assert rows['middle:f1']['status']=='violated'
    assert not result['request_fully_covered'] and result['status']=='violated'


def test_orchestrator_deadline_covers_preflight_without_success(tmp_path,monkeypatch):
    paths=files(tmp_path)
    monkeypatch.setattr(pipeline,'PUBLIC_WALL_SECONDS',.01)
    original=pipeline.load_inputs
    def slow(*args):
        time.sleep(.1)
        return original(*args)
    monkeypatch.setattr(pipeline,'load_inputs',slow)
    response=pipeline.run(*paths,tmp_path/'deadline')
    assert not response['ok'] and response['status']=='interrupted'
    assert not (tmp_path/'deadline').exists()
    assert signal.getitimer(signal.ITIMER_REAL)==(0.,0.)


def test_empty_budget_not_valid_input(tmp_path):
    data=fixed_inputs();data[2]['budgets']['evaluations']=0
    with pytest.raises(ValueError):pipeline.load_inputs(*files(tmp_path,data))


def test_no_models_specified_means_native_legacy(tmp_path):
    data=fixed_inputs();data[2]['projections'][0]['request'].pop('models',None)
    contract,plan=pipeline.load_inputs(*files(tmp_path,data))
    assert contract.projections[0]['template'].options==dict(loss_model='legacy',radiation_model='legacy',air_reference=None)
    assert plan['effective_models'][0]['air_substitution']=='none; exact CONFIG air'


def test_mesh_budget_rejected_before_allocation(tmp_path,monkeypatch):
    data=fixed_inputs();data[2]['projections'][0]['request']['budgets']['segments']=1
    monkeypatch.setattr(pipeline,'mesh_for',lambda *a:pytest.fail('allocated before preflight'))
    with pytest.raises(ValueError,match='maillage'):pipeline.load_inputs(*files(tmp_path,data))


@pytest.mark.parametrize('field',['bom','positions','effective_request','component_ids'])
def test_reader_rebuilds_physical_identity_even_after_rehash(solved,tmp_path,field):
    out,c,_=solved
    checkpoint=json.loads(sorted(out.glob('checkpoint_*.json'))[-1].read_text())
    candidate=checkpoint['candidate']
    if field=='bom':candidate['bom'][0]['stock_length_m']+=.01
    elif field=='positions':candidate['positions'][0]['q_m']+=.01
    elif field=='effective_request':candidate['projections'][0]['effective_request']['metadata']={'changed':True}
    else:candidate['projections'][0]['component_ids']=[]
    checkpoint['checkpoint_sha256']=reporting.fingerprint({k:v for k,v in checkpoint.items() if k!='checkpoint_sha256'})
    reporting.write_json(tmp_path/'checkpoint_0001.json',checkpoint)
    with pytest.raises(ValueError):reporting.read_checkpoint(tmp_path,c)


def test_source_changed_since_import_is_refused_before_preflight(tmp_path,monkeypatch):
    paths=files(tmp_path)
    monkeypatch.setitem(pipeline.LOADED_SOURCES,'didgeridoo_optimizer/pipeline/assembly_path.py','0'*64)
    with pytest.raises(ValueError,match='sources producteur modifiées'):
        pipeline.run(*paths,tmp_path/'changed',dry_run=True)
    assert not (tmp_path/'changed').exists()
