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


def test_unrepresentable_assembly_scenario_preserves_other_observations(tmp_path,monkeypatch):
    import os
    raw=json.loads((EXAMPLES/'assembly_job.json').read_text())
    for key in ('config','assembly','assembly_request'):
        target=(EXAMPLES/raw['input'][key]).resolve()
        raw['input'][key]=os.path.relpath(target,tmp_path)
    raw['scenarios']=[dict(id='nominal',coefficients={'cut':0,'stock':0}),
                      dict(id='fractional',coefficients={'cut':.3333333333333333,'stock':0}),
                      dict(id='integer',coefficients={'cut':1,'stock':0})]
    job=write(tmp_path/'job.json',raw)
    monkeypatch.setattr(audit.ProjectionEvaluator,'evaluate_design',
        lambda self,design,**kwargs:dict(criteria=report.unavailable_rows(self.contract.criteria,'test no acoustics')))
    result,out=execute(tmp_path,job)
    assert len(result['observations'])==6
    assert [o['geometry_status'] for o in result['observations']]==['valid','valid','unresolved','unresolved','valid','valid']
    assert (out/'unavailable_fractional.json').is_file()
    assert not (out/'assembly_fractional.json').exists()
    assert not result['counterexample_found']


def test_exported_assembly_regenerates_identical_native_profiles(tmp_path):
    from didgeridoo_optimizer.geometry.assemblies import Assembly,read_assembly
    prepared,plan=audit.load_inputs(EXAMPLES/'assembly_job.json')
    raw=prepared['nominal'];context=prepared['context']
    report.write_geometry(tmp_path,'nominal',raw,'assembly',plan)
    reloaded,_=read_assembly(tmp_path/'assembly_nominal.json')
    assert list(reloaded['pieces'])==list(raw['pieces'])
    before=Assembly(raw,context['material_db'],context['config'])
    after=Assembly(reloaded,context['material_db'],context['config'])
    for configuration in raw['configurations']:
        assert before.generate(configuration)['design'].as_dict()==after.generate(configuration)['design'].as_dict()


def sealed_bundle(tmp_path, *, optional=False):
    job = make_job(tmp_path, optional=optional)
    result, out = execute(tmp_path, job)
    report.export_result(out, result)
    report.close_prepared(out, dict(ok=True, status=result['status'],
                                    child=dict(exit_code=0, reaped=True)), result['plan'])
    report.commit_terminal(out)
    return json.loads(json.dumps(result)), out


def rewrite_bundle(out, result):
    """Rehash deliberate semantic corruption; this is not an authenticity test."""
    write(out/'result.json', result)
    for index, observation in enumerate(result['observations'], 1):
        write(out/f'observation_{index:04d}.json', observation)
    manifest = json.loads((out/'manifest.json').read_text())
    manifest = {name: report.file_sha256(out/name) for name in manifest
                if not name.startswith('observation_')}
    manifest.update({f'observation_{i:04d}.json': report.file_sha256(out/f'observation_{i:04d}.json')
                     for i in range(1, len(result['observations'])+1)})
    write(out/'manifest.json', manifest)
    receipt = json.loads((out/'execution.json').read_text())
    receipt['manifest_sha256'] = report.file_sha256(out/'manifest.json')
    write(out/'execution.json', receipt)
    write(out/'execution.closed.json', dict(execution_sha256=report.file_sha256(out/'execution.json'), cancelled=False))
    write(out/'execution.completed.json', report.completion_record(out))


@pytest.mark.parametrize('change', [
    'summary', 'missing', 'duplicate', 'role', 'identity', 'configuration', 'scenario',
    'coefficients', 'nominal', 'request', 'unit', 'margin_unit', 'margin', 'minimum',
    'status', 'criterion_missing', 'criterion_duplicate', 'geometry', 'profile',
])
def test_reader_rejects_semantic_contradictions_with_valid_hashes(tmp_path, change):
    result, out = sealed_bundle(tmp_path)
    observation = result['observations'][0]
    row = observation['criteria'][0]
    if change == 'summary': result.update(sampled_hard_conforming=True, counterexample_found=False)
    elif change == 'missing': result['observations'] = result['observations'][:1]
    elif change == 'duplicate': result['observations'][1] = copy.deepcopy(observation)
    elif change == 'role': row['role'] = 'observe'
    elif change == 'identity': row['id'] = 'renamed'
    elif change == 'configuration': observation['configuration'] = 'other'
    elif change == 'scenario': observation['scenario'] = 'undeclared'
    elif change == 'coefficients': observation['coefficients']['cut'] = 1
    elif change == 'nominal': observation['nominal'] = False
    elif change == 'request': observation['effective_request']['criteria'][0]['target']['value'] = .7
    elif change == 'unit': row['unit_si'] = 'Hz'
    elif change == 'margin_unit': row['margin_unit'] = 'Hz'
    elif change == 'margin': row['margin'] += .1
    elif change == 'minimum': result['margins'][0]['margin'] += .1
    elif change == 'status': row['status'] = 'violated'
    elif change == 'criterion_missing': observation['criteria'] = []
    elif change == 'criterion_duplicate': observation['criteria'].append(copy.deepcopy(row))
    else:
        name = 'fixed_nominal.json' if change == 'geometry' else report.profile_name('nominal', 'fixed')
        value = json.loads((out/name).read_text())
        value['segments'][0]['length_cm'] = 99
        write(out/name, value)
    rewrite_bundle(out, result)
    with pytest.raises(ValueError): report.read_result(out)


def test_reader_partial_result_retains_counterexample(tmp_path):
    result, out = sealed_bundle(tmp_path)
    result['observations'] = result['observations'][:2]
    result.update(audit.summarize(result['observations'], result['plan'], False))
    rewrite_bundle(out, result)
    reread = report.read_result(out)
    assert reread['counterexample_found'] and not reread['execution_complete']
    assert not reread['sampled_hard_conforming'] and not reread['request_fully_covered']


def test_reader_optional_unavailable_and_budget_keep_acquired_hard(tmp_path, monkeypatch):
    job = make_job(tmp_path, acoustic=True, optional=True)
    request_path = tmp_path/'request.json'; request = json.loads(request_path.read_text())
    request['criteria'][1]['role'] = 'observe'; write(request_path, request)
    raw = json.loads(job.read_text()); raw['uncertainties'][0]['delta']['value'] = .1; write(job, raw)
    monkeypatch.setattr(audit.ProjectionEvaluator, 'evaluate_design',
        lambda *a, **k: (_ for _ in ()).throw(audit.BudgetExhausted('optional spectrum')))
    result, out = execute(tmp_path, job)
    report.export_result(out, result)
    report.close_prepared(out, dict(ok=False, status='interrupted'), result['plan'])
    report.commit_terminal(out)
    reread = report.read_result(out)
    assert reread['sampled_hard_conforming'] and not reread['execution_complete']
    assert not reread['request_fully_covered']
    assert reread['observations'][0]['criteria'][2]['status'] == 'unsupported'


def test_reader_optional_unavailable_complete(tmp_path):
    result, out = sealed_bundle(tmp_path, optional=True)
    reread = report.read_result(out)
    assert reread['execution_complete'] and reread['counterexample_found']
    assert not reread['request_fully_covered'] and reread['semantic_verified']


@pytest.mark.parametrize('dimension_change', ['dimension', 'target_si', 'tolerance_si', 'tolerance_dimension'])
def test_reader_rejects_changed_original_dimensions_and_targets(tmp_path, dimension_change):
    result, out = sealed_bundle(tmp_path)
    row = result['observations'][0]['criteria'][0]
    row[dimension_change] = 'frequency' if dimension_change in ('dimension', 'tolerance_dimension') else 7.
    rewrite_bundle(out, result)
    with pytest.raises(ValueError, match='dimension/demande'): report.read_result(out)


def test_reader_fresh_without_importing_producer(tmp_path):
    import os
    import subprocess
    import sys
    _, out = sealed_bundle(tmp_path)
    script = '''
import importlib.abc, json, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == 'didgeridoo_optimizer.pipeline.tolerance_audit':
            raise AssertionError('producer imported during readback')
sys.meta_path.insert(0, Guard())
from didgeridoo_optimizer.reporting.tolerance_audit import read_result
result = read_result(sys.argv[1])
assert result['ok'] and result['semantic_verified'] and result['counterexample_found']
assert 'didgeridoo_optimizer.pipeline.tolerance_audit' not in sys.modules
'''
    child = subprocess.run([sys.executable, '-B', '-c', script, str(out)], cwd=ROOT,
        env=dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1'),
        capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr


def test_reader_assembly_order_and_profile_association(tmp_path, monkeypatch):
    import os
    job = json.loads((EXAMPLES/'assembly_job.json').read_text())
    request = read_request((EXAMPLES/job['input']['assembly_request']).resolve())[0]
    for projection in request['projections']:
        projection['request']['criteria'] = [dict(id='length', observable='geometry',
            expression={'total_length': True}, target={'value': 1, 'unit': 'm'}, unit='m',
            tolerance={'value': 1, 'unit': 'mm'}, level='geometry', scope={}, role='hard')]
    for key in ('config', 'assembly'):
        job['input'][key] = os.path.relpath((EXAMPLES/job['input'][key]).resolve(), tmp_path)
    job['input']['assembly_request'] = 'request.json'
    write(tmp_path/'request.json', request)
    monkeypatch.setattr(audit.TracedProjectionEvaluator, 'spectrum', lambda *a, **k: pytest.fail('acoustics'))
    result, out = execute(tmp_path, write(tmp_path/'job.json', job))
    report.export_result(out, result)
    report.close_prepared(out, dict(ok=True, child=dict(exit_code=0, reaped=True)), result['plan'])
    report.commit_terminal(out)
    assert report.read_result(out)['counterexample_found']
    path = out/'assembly_nominal.json'; raw = json.loads(path.read_text())
    assert len(raw['pieces']) > 1
    raw['pieces'] = dict(reversed(list(raw['pieces'].items())))
    write(path, raw); rewrite_bundle(out, result)
    with pytest.raises(ValueError, match='ordre natif'): report.read_result(out)


def test_reader_checks_coverage_against_original_job(tmp_path):
    job = make_job(tmp_path)
    raw = json.loads(job.read_text()); raw['coverage']['kind'] = 'continuous'; write(job, raw)
    result, out = execute(tmp_path, job)
    report.export_result(out, result)
    report.close_prepared(out, dict(ok=True, child=dict(exit_code=0, reaped=True)), result['plan'])
    report.commit_terminal(out)
    assert not report.read_result(out)['request_fully_covered']
    result['plan']['uncovered'] = []
    result.update(audit.summarize(result['observations'], result['plan'], True))
    write(out/'plan.json', result['plan']); rewrite_bundle(out, result)
    with pytest.raises(ValueError, match='portées originales'): report.read_result(out)
