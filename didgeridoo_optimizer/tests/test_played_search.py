"""Public synthetic oracles; no private model, observed target or runtime data."""
import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from didgeridoo_optimizer.optimization import played_search as core
from didgeridoo_optimizer.pipeline import played_search as workflow
from didgeridoo_optimizer.reporting import played_search as report
from didgeridoo_optimizer.nonlinear import register_target as native
from didgeridoo_optimizer.tests.test_register_target import request, analyze

ROOT=Path(__file__).resolve().parents[2]
EXAMPLE=ROOT/'project_specs/examples/played_search'


@pytest.fixture
def job(tmp_path):
    for name in ('config.yaml','design.json','static_request.yaml','experiment.yaml','request.yaml','assembly.yaml','assembly_request.yaml'):
        (tmp_path/name).write_bytes((EXAMPLE/name).read_bytes())
    config=yaml.safe_load((tmp_path/'config.yaml').read_text())
    for key,name in [('database_file','materials_base_v1.yaml'),('variant_rules_file','wood_variant_rules_v1.yaml')]:
        config['materials'][key]=str(ROOT/'project_specs'/name)
    (tmp_path/'config.yaml').write_text(yaml.safe_dump(config))
    return tmp_path/'request.yaml'


def change(path, fn):
    obj=yaml.safe_load(path.read_text());fn(obj);path.write_text(yaml.safe_dump(obj));return obj


def rows(freqs, role='hard'):
    r=request();r['criteria']=[dict(id='pitch',role=role,observable='played_frequency',windows=['hold'],
                                  target=dict(value=70.,unit='Hz'),tolerance=dict(value=5.,unit='cent'))]
    return [x for i,f in enumerate(freqs) for x in core.played_rows(r,
        native.evaluate_criteria(r,{'hold':dict(frequency_hz=f)})['criteria'],'nominal',f's{i}')]


def evaluated(freqs, complete=True, unsupported=()):
    rs=rows(freqs);return dict(criteria=rs,units_complete=complete,**core.verdict(rs,units_complete=complete,unsupported=unsupported))


def test_no_average_compensates_one_scenario():
    result=evaluated([69.7,70.3])
    assert np.mean([69.7,70.3])==70 and result['status']=='violated' and not result['conforming']
    assert result['max_violation_normalized']>0
    assert result['criteria'][0]['residual_si']<0<result['criteria'][1]['residual_si']


def test_real_poll_leaves_nonconforming_start_without_target_derived_proposal():
    points=[]
    def evaluate(x,alt,i,stage):
        points.append(x[0]);return evaluated([70+(x[0]-1.176604159825398)*10])
    result=core.poll([dict(id='length',low=1.1,high=1.3)],[1.201604159825398],['nominal'],
        dict(normalized_steps=[.125,.0625],directions=[-1,1],stop_on_witness=True),8,evaluate)
    assert points==pytest.approx([1.201604159825398,1.176604159825398])
    assert result['witness']['candidate']==2 and result['history'][0]['status']=='violated'


def test_incomplete_does_not_replace_complete_witness():
    def evaluate(x,a,i,stage):
        value=evaluated([70],complete=i==1)
        if i==2:value['stop_reason']='total_steps'
        return value
    result=core.poll([dict(id='x',low=0.,high=1.)],[.5],['a'],
        dict(normalized_steps=[.125],directions=[-1,1],stop_on_witness=False),3,evaluate)
    assert result['witness']['candidate']==1 and result['best']['candidate']==1
    assert result['termination']=='total_steps' and len(result['history'])==2


def test_fail_unresolved_not_evaluated_and_unsupported_are_distinct():
    assert evaluated([None])['status']=='unresolved'
    assert evaluated([None],complete=False)['status']=='partial'
    assert evaluated([70],unsupported=['continuous'])['status']=='unsupported'
    assert evaluated([69,None])['status']=='violated'
    assert evaluated([None])['max_violation_normalized'] is None


def test_acquired_violation_guides_poll_without_unknown_penalty():
    a=evaluated([69,None],complete=False);b=evaluated([69.5,None],complete=False)
    assert a['max_violation_normalized'] is None
    assert b['observed_violation_lower_bound']<a['observed_violation_lower_bound']
    assert core.better(b,a) and not b['conforming']


def test_observe_unavailable_independent_and_nohard_descriptive():
    rs=rows([70])+rows([None],role='observe')
    assert core.verdict(rs,units_complete=True)['conforming']
    assert core.verdict(rows([70],role='observe'),units_complete=True)['status']=='descriptive'
    r=request();r['criteria']=[dict(id='unknown',role='hard',observable='physiological_accessibility',windows=['hold'])]
    # Known native unsupported names remain unsupported, never a zero residual.
    r['criteria'][0]['observable']=next(iter(native.UNSUPPORTED))
    out=core.played_rows(r,native.evaluate_criteria(r, {})['criteria'],'nominal','s')
    assert core.verdict(out,units_complete=True)['status']=='unsupported'


def test_nohard_and_no_variable_are_valid_evaluations():
    calls=[]
    def evaluate(x,a,i,stage):calls.append(x);return evaluated([70])
    r=core.poll([],[],['a'],dict(normalized_steps=[.1],directions=[-1,1],stop_on_witness=False),3,evaluate)
    assert calls==[[]] and r['termination']=='evaluation_only'
    calls.clear()
    core.poll([dict(id='x',low=0.,high=1.)],[.5],['a'],
        dict(normalized_steps=[.1],directions=[-1,1],stop_on_witness=False),3,evaluate,descriptive=True)
    assert calls==[[.5]]


def test_target_never_changes_native_observer_and_ratio_not_three():
    r=request();r['criteria']=[dict(id='note',role='hard',observable='played_frequency',windows=['hold'],
        target=dict(note='C2',reference_hz=440.,cents=0.),tolerance=dict(value=5.,unit='cent'))]
    y=500*np.sin(2*np.pi*70*(np.arange(4000)+.5)/4000)
    first=analyze(r,y);r['criteria'][0]['target']=dict(value=70.,unit='Hz');second=analyze(r,y)
    assert first['windows']==second['windows']
    scalar=core.played_rows(r,second['criteria'],'nominal','s')[0]
    assert abs(scalar['error_cents'])<.1 and abs(scalar['residual_si'])<.01
    r['criteria']=[dict(id='ratio',role='hard',observable='frequency_ratio',windows=['lo','hi'],
        target=dict(value=2.,unit='1'),tolerance=dict(value=.001,unit='1'))]
    native_rows=native.evaluate_criteria(r,{'lo':dict(frequency_hz=70.),'hi':dict(frequency_hz=140.)})['criteria']
    assert core.played_rows(r,native_rows,'nominal','s')[0]['status']=='pass'


def test_preflight_zero_science_no_destination(job,tmp_path,monkeypatch):
    def no(*a,**kw):pytest.fail('science or output during preflight')
    for module,name in [(workflow.td,'input_impedance'),(workflow.td,'fit_passive'),(np,'roots'),
                        (np.linalg,'eigvals'),(report,'write_json')]:monkeypatch.setattr(module,name,no)
    result=workflow.run(job,tmp_path/'never-created',dry_run=True)
    assert result['ok'] and not (tmp_path/'never-created').exists()
    assert 'pending fresh fit' in result['plan']['scenarios'][0]['validation_context']


@pytest.mark.parametrize('what',['recipe','model','configuration','bool','continuous','preference','optimized'])
def test_strict_templates_and_unsupported_scope(job,what):
    if what in ('continuous','preference','optimized'):
        key={'continuous':'coverage','preference':'objective','optimized':'player_controls'}[what]
        change(job,lambda r:r['scope'].update({key:what}))
        assert key in workflow.preflight(job)['unsupported'];return
    if what=='recipe':change(job.parent/'experiment.yaml',lambda r:r['case']['recipe'].update(guard_points=800))
    if what=='model':change(job.parent/'experiment.yaml',lambda r:r['case'].update(model_in='old-model.json'))
    if what=='configuration':change(job,lambda r:r['scenarios'][0].update(configuration='absent'))
    if what=='bool':change(job,lambda r:r['budgets'].update(fits=True))
    with pytest.raises(ValueError):workflow.preflight(job)


def test_linked_native_fields_and_derived_remain_locked(job):
    change(job.parent/'static_request.yaml',lambda r:r.update(variables=[dict(id='diameter',fields=['segments.0.d_in_cm','segments.0.d_out_cm'],unit='cm',bounds=[2.5,3.5])]))
    c,p=workflow.load_inputs(job);_,items=workflow.generated(c,'fixed',[.032],'nominal')
    raw=items['nominal']['design'].as_dict();assert raw['segments'][0]['d_out_cm']==3.2
    raw['segments'][0]['material_id']='wood';
    with pytest.raises(ValueError):c.check_locks(raw)
    change(job.parent/'static_request.yaml',lambda r:r.update(variables=[dict(id='diameter',fields=['segments.0.d_in_cm'],unit='cm',bounds=[2.5,3.5])],
        derived=[dict(id='out',field='segments.0.d_out_cm',expression={'field':'segments.0.d_in_cm'},unit='cm',bounds=[2.5,3.5])]))
    c,p=workflow.load_inputs(job)
    assert len(c.variables)==1 and c.generate([.032]).segments[0].d_out_cm==3.2


def test_fixed_assembly_no_q_and_multiconfig_common_parts(job):
    change(job,lambda r:r.update(input=dict(kind='assembly',assembly='assembly.yaml',request='assembly_request.yaml')))
    c,p=workflow.load_inputs(job);a,items=workflow.generated(c,'assembly',[1.2],'nominal')
    assert not items['nominal']['positions'] and len(items['nominal']['bom'])==1
    change(job.parent/'assembly.yaml',lambda r:r['configurations'].update(second={}))
    change(job.parent/'assembly_request.yaml',lambda r:r['projections'].append(dict(r['projections'][0],id='second',configuration='second')))
    change(job,lambda r:r['scenarios'].append(dict(r['scenarios'][0],id='second',configuration='second')))
    c,p=workflow.load_inputs(job);a,items=workflow.generated(c,'assembly',[1.18],'nominal')
    assert items['nominal']['bom']==items['second']['bom']
    assert items['nominal']['design'].segments[0].length_cm==118
    assert items['second']['design'].segments[0].length_cm==118


def test_inputs_and_producer_fingerprints(job):
    p=workflow.preflight(job)
    change(job.parent/'design.json',lambda r:r['segments'][0].update(length_cm=121.))
    with pytest.raises(ValueError,match='bytes changed'):workflow.check_plan(p)
    bad=copy.deepcopy(p);bad['producer']['loaded_sources_sha256']['tools/played_search.py']='0'*64
    bad['context_sha256']=workflow.digest({k:v for k,v in bad.items() if k!='context_sha256'})
    with pytest.raises(ValueError,match='sources'):workflow.check_plan(bad)


def test_budget_reservation_counts_failures_and_prevents_launch(job,tmp_path,monkeypatch):
    p=workflow.preflight(job);out=tmp_path/'out';out.mkdir();limits=dict(p['job']['budgets'],fits=1,steps=10)
    b=workflow.Budget(limits,out);b.reserve('fit',10,1024)
    assert b.reserved['fits']==1 and b.counts['fits']==0
    with pytest.raises(workflow.BudgetStop):b.reserve('fit',10,1024)
    with pytest.raises(workflow.BudgetStop):b.reserve('trajectory',10,1024,11)
    assert b.counts['trajectories']==0 and b.counts['steps_charged']==0
    b.start-=limits['seconds']-1
    with pytest.raises(workflow.BudgetStop,match='remaining_wall'):b.reserve('read',2,1)


def test_strict_duplicates_cycle_nonfinite(tmp_path):
    for index,text in enumerate(('x: 1\nx: 2\n','a: &a [*a]\n','x: .nan\n')):
        path=tmp_path/f'bad{index}.yaml';path.write_text(text)
        with pytest.raises(ValueError):workflow.preflight(path)


def test_native_mechanical_rejection_is_proved_not_zero(job):
    change(job,lambda r:r.update(input=dict(kind='assembly',assembly='assembly.yaml',request='assembly_request.yaml')))
    change(job.parent/'assembly.yaml',lambda r:r['pieces']['body'].update(stock=dict(length=dict(value=125.,unit='cm'),outer_diameter=dict(value=33.,unit='mm'))))
    _,p=workflow.load_inputs(job)
    result=workflow.static_unit(dict(plan=p,values=[1.29],alternative='nominal'))
    assert result['ok'] and result['geometry_valid'] is False
    assert result['rows'][0]['status']=='violated' and result['rows'][0]['value_si'] is None
    assert not result['configurations']



def test_live_storage_scan_tolerates_native_atomic_progress_rename(tmp_path,monkeypatch):
    stable=tmp_path/'stable';stable.write_bytes(b'123')
    vanished=tmp_path/'.fit_progress.json.tmp'
    monkeypatch.setattr(Path,'rglob',lambda self,pattern:iter([vanished,stable]))
    assert report.size(tmp_path)==3
    link=tmp_path/'unsafe';link.symlink_to(stable)
    monkeypatch.setattr(Path,'rglob',lambda self,pattern:iter([link]))
    with pytest.raises(ValueError,match='Symbolic'):report.size(tmp_path)
