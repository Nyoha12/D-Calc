"""Native fixed/assembly audit, explicit statuses, and independent geometry oracles."""
import copy
import json
from pathlib import Path

import pytest

from didgeridoo_optimizer.pipeline import tolerance_audit as audit
from didgeridoo_optimizer.reporting import tolerance_audit as report
from didgeridoo_optimizer.optimization.design_contract import read_request
from didgeridoo_optimizer.geometry.tolerance_scenarios import perturb

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT/'project_specs/examples/tolerance_audit'


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False))
    return path


def make_job(tmp_path, *, acoustic=False, optional=False):
    config = read_request(ROOT/'project_specs/examples/assembly_path/config.yaml')[0]
    config['materials'] = dict(database_file=str(ROOT/'project_specs/materials_base_v1.yaml'),
                              variant_rules_file=str(ROOT/'project_specs/wood_variant_rules_v1.yaml'))
    write(tmp_path/'config.json',config)
    design = dict(id='fixed',metadata={'annotation':{'untouched':[1,'x']}},segments=[dict(
        kind='cylinder',length_cm=60.,d_in_cm=3.,d_out_cm=3.,material_id='pvc_pressure')])
    write(tmp_path/'design.json',design)
    request = read_request(ROOT/'project_specs/examples/assembly_path/request.yaml')[0]['projections'][0]['request']
    request['criteria'] = [dict(id='length',observable='geometry',expression={'total_length':True},
        target={'value':.6,'unit':'m'},unit='m',tolerance={'value':1,'unit':'mm'},
        level='geometry',scope={},role='hard')]
    if acoustic:
        request['criteria'].append(dict(id='f1',observable='resonance_frequency',mode='m1',
            target={'value':140,'unit':'Hz'},unit='Hz',tolerance={'value':1,'unit':'Hz'},
            level='passive',scope={},role='hard'))
    if optional:
        request['criteria'].append(dict(id='played',observable='played_frequency',
            target={'value':140,'unit':'Hz'},unit='Hz',tolerance={'value':1,'unit':'Hz'},
            level='played',scope={},role='observe'))
    write(tmp_path/'request.json',request)
    job = dict(schema_version=audit.SCHEMA,input=dict(kind='fixed',config='config.json',
        design='design.json',request='request.json'),uncertainties=[dict(id='cut',
        fields=['segments.0.length_cm'],delta={'value':2,'unit':'mm'},origin='prescrit synthétique')],
        scenarios=[dict(id='nominal',coefficients={'cut':0}),dict(id='plus',coefficients={'cut':1}),
                   dict(id='minus',coefficients={'cut':-1})],budgets=dict(audit.LIMITS),coverage={'kind':'finite'})
    return write(tmp_path/'job.json',job)


def execute(tmp_path,job):
    prepared,plan=audit.load_inputs(job)
    out=tmp_path/'bundle';out.mkdir()
    report.write_json(out/'plan.json',plan)
    return audit.execute(prepared,plan,out),out


def test_native_geometry_values_and_minimum_margin(tmp_path):
    job=make_job(tmp_path)
    result,out=execute(tmp_path,job)
    values=[o['criteria'][0]['value_si'] for o in result['observations']]
    assert values==pytest.approx([.600,.602,.598],abs=1e-14)
    assert result['counterexample_found'] and not result['sampled_hard_conforming']
    assert result['execution_complete'] and result['request_fully_covered']
    assert result['margins'][0]['margin']==pytest.approx(-.001)
    nominal=json.loads((out/'fixed_nominal.json').read_text())
    assert nominal==audit.load_inputs(job)[0]['nominal']
    assert nominal['metadata']['annotation']=={'untouched':[1,'x']}
    assert result['counters']['spectral_calls']==0


def test_optional_unavailable_does_not_contaminate_hard(tmp_path):
    job=make_job(tmp_path,optional=True)
    raw=json.loads(job.read_text());raw['uncertainties'][0]['delta']['value']=.1;write(job,raw)
    result,_=execute(tmp_path,job)
    assert result['sampled_hard_conforming'] and result['execution_complete']
    assert not result['request_fully_covered']
    assert all(o['criteria'][1]['status']=='unsupported' for o in result['observations'])


def test_favorable_corners_do_not_certify_continuum(tmp_path):
    job=make_job(tmp_path)
    raw=json.loads(job.read_text());raw['coverage']['kind']='continuous'
    raw['uncertainties'][0]['delta']['value']=.1;write(job,raw)
    result,_=execute(tmp_path,job)
    assert result['sampled_hard_conforming']
    assert not result['request_fully_covered'] and not result['continuous_robustness_certified']


def test_unavailable_units_preserve_counterexample():
    rows=[dict(scenario='nominal',projection='p',geometry_status='valid',criteria=[
        dict(id='bad',role='hard',status='violated',value_si=1.,unit_si='m',margin=-1.,margin_unit='m'),
        dict(id='unknown',role='hard',status='unresolved',value_si=None,unit_si=None)])]
    summary=audit.summarize(rows,{'uncovered':[]},True)
    assert summary['counterexample_found'] and not summary['sampled_hard_conforming']
    assert not summary['request_fully_covered'] and summary['margins'][0]['criterion']=='bad'


def test_global_budget_preserves_geometry_and_other_scenarios(tmp_path,monkeypatch):
    job=make_job(tmp_path,acoustic=True)
    calls=[]
    def exhausted(self,design,**kwargs):
        calls.append(design.total_length_cm)
        raise audit.BudgetExhausted('test spectral budget')
    monkeypatch.setattr(audit.ProjectionEvaluator,'evaluate_design',exhausted)
    result,out=execute(tmp_path,job)
    assert len(result['observations'])==3 and len(list(out.glob('observation_*.json')))==3
    assert not result['execution_complete'] and result['counterexample_found']
    assert all(o['criteria'][0]['value_si'] is not None for o in result['observations'])
    assert all(o['criteria'][1]['status']=='unresolved' for o in result['observations'])


def test_technical_error_is_not_physical_failure(tmp_path,monkeypatch):
    job=make_job(tmp_path)
    prepared,plan=audit.load_inputs(job)
    monkeypatch.setattr(audit,'validate_design',lambda *a,**k: (_ for _ in ()).throw(ValueError('capacity missing')))
    values=list(audit.geometries('fixed',prepared['nominal'],prepared['context'],prepared['projections']))
    assert values[0][3]['status']=='unresolved'


def test_assembly_shared_piece_q_and_native_mechanics():
    prepared,plan=audit.load_inputs(EXAMPLES/'assembly_job.json')
    scenario=next(s for s in prepared['parsed']['scenarios'] if s['id']=='minus')
    raw=perturb(prepared['nominal'],prepared['parsed'],scenario,'assembly')
    assert raw['configurations']==prepared['nominal']['configurations']
    profiles=list(audit.geometries('assembly',raw,prepared['context'],prepared['projections']))
    assert profiles[0][1].total_length_cm==pytest.approx(59.9)
    assert profiles[1][3]['status']=='geometry_violated'
    assert 'minimum_overlap' in profiles[1][3]['reason']
    # Independent stock/command balance: .475-.400=.075 < .080 m.
    assert .475-.4 < .08


@pytest.mark.parametrize('key,value',[('projections',1),('scenario_fields',1),('frequencies',1),('seconds',166)])
def test_budget_refused_before_acoustic_allocation(tmp_path,key,value,monkeypatch):
    job=make_job(tmp_path,acoustic=True);raw=json.loads(job.read_text());raw['budgets'][key]=value;write(job,raw)
    monkeypatch.setattr(audit.ProjectionEvaluator,'evaluate_design',lambda *a,**k:pytest.fail('calculation'))
    with pytest.raises(ValueError,match='budget|entier'):audit.load_inputs(job)


def test_native_hard_unsupported_keeps_parent_scope(tmp_path):
    job=make_job(tmp_path)
    path=tmp_path/'request.json';request=json.loads(path.read_text())
    request['scope']={'configurations':['nominal','other']};write(path,request)
    result,_=execute(tmp_path,job)
    assert result['plan']['native_request']['scope']==request['scope']
    assert not result['sampled_hard_conforming'] and not result['request_fully_covered']


def test_mask_after_native_validation(tmp_path):
    job=make_job(tmp_path);prepared,plan=audit.load_inputs(job)
    raw=perturb(prepared['nominal'],prepared['parsed'],prepared['parsed']['scenarios'][1],'fixed')
    design=audit.validate_design(raw,prepared['context']['material_db'],prepared['context']['config'])
    audit.check_mask(prepared['nominal'],design.as_dict(),prepared['parsed'],'fixed')
    assert design.segments[0].position_end_cm==pytest.approx(60.2)


def test_optional_budget_failure_keeps_all_calculated_hard(tmp_path,monkeypatch):
    job=make_job(tmp_path,acoustic=True)
    request_path=tmp_path/'request.json';request=json.loads(request_path.read_text())
    request['criteria'][1]['role']='observe';write(request_path,request)
    raw=json.loads(job.read_text());raw['uncertainties'][0]['delta']['value']=.1;write(job,raw)
    monkeypatch.setattr(audit.ProjectionEvaluator,'evaluate_design',
        lambda *a,**k: (_ for _ in ()).throw(audit.BudgetExhausted('optional spectrum')))
    result,_=execute(tmp_path,job)
    assert not result['execution_complete'] and result['sampled_hard_conforming']
    assert not result['request_fully_covered']


def test_fixed_nonconforming_nominal_retains_prior_links(tmp_path):
    job=make_job(tmp_path)
    path=tmp_path/'request.json';request=json.loads(path.read_text())
    request['criteria'][0]['target']['value']=.7
    request['variables']=[dict(id='diameter',fields=['segments.0.d_in_cm','segments.0.d_out_cm'],
                               unit='cm',bounds=[2.,4.])]
    write(path,request)
    prepared,plan=audit.load_inputs(job)
    assert len(prepared['contract'].variables)==1
    result,_=execute(tmp_path,job)
    assert result['counterexample_found'] and result['observations'][0]['criteria'][0]['status']=='violated'
    raw=json.loads(job.read_text());raw['uncertainties'][0]['fields']=['segments.0.d_in_cm'];write(job,raw)
    with pytest.raises(ValueError,match='liaison'):audit.load_inputs(job)


def test_filename_id_pairs_are_injective():
    assert report.profile_name('a__b','c') != report.profile_name('a','b__c')


def test_source_snapshot_detects_changes(tmp_path,monkeypatch):
    job=make_job(tmp_path)
    monkeypatch.setattr(audit,'LOADED_SOURCES',{'didgeridoo_optimizer/pipeline/tolerance_audit.py':'0'*64})
    with pytest.raises(ValueError,match='sources producteur modifiées'):audit.load_inputs(job)
