"""Independent length oracles, quantifiers, immutable fabrication and hostile readback."""
import copy
import json
from fractions import Fraction
from pathlib import Path

import pytest

from didgeridoo_optimizer.pipeline import adjustment_policy as api
from didgeridoo_optimizer.reporting import adjustment_policy as report
from didgeridoo_optimizer.optimization.adjustment_policy import commands, apply_commands
from didgeridoo_optimizer.optimization.design_contract import read_request
from didgeridoo_optimizer.geometry.assemblies import Assembly, field_info
from didgeridoo_optimizer.tests.test_tolerance_audit import make_job as fixed_audit, write

ROOT=Path(__file__).resolve().parents[2]
EXAMPLES=ROOT/'project_specs/examples/adjustment_policy'


def make_job(tmp_path,kind='per_configuration',*,fixed=False,duplicate=False,optional=False,acoustic=False,one=False):
    if fixed:
        source=fixed_audit(tmp_path,acoustic=acoustic,optional=optional)
        source.rename(tmp_path/'audit.json')
    else:
        a=read_request(EXAMPLES/'assembly.json')[0]
        # Two configurations, the same physical pieces, one 2 mm cut error.
        a['configurations'].pop('middle')
        r=read_request(EXAMPLES/'request.json')[0]
        r['projections']=[p for p in r['projections'] if p['configuration']!='middle']
        for p in r['projections']:
            if not acoustic: p['request']['criteria']=p['request']['criteria'][:1]
            if optional:
                p['request']['criteria'].append(dict(id='played',observable='played_frequency',
                    target={'value':140,'unit':'Hz'},unit='Hz',tolerance={'value':1,'unit':'Hz'},
                    level='played',scope={},role='observe'))
        if duplicate:
            r['projections'].append(dict(copy.deepcopy(r['projections'][0]),id='short_repeat'))
        write(tmp_path/'assembly.json',a);write(tmp_path/'request.json',r)
        j=read_request(EXAMPLES/'audit.json')[0]
        j['input']['config']=str(Path('..')) # replaced by a relative real native CONFIG
        import os
        j['input']['config']=os.path.relpath(ROOT/'project_specs/examples/assembly_path/config.yaml',tmp_path)
        write(tmp_path/'audit.json',j)
    if one:
        j=read_request(tmp_path/'audit.json')[0];j['scenarios']=j['scenarios'][:1];write(tmp_path/'audit.json',j)
    policy={'kind':'fixed'} if kind=='fixed' else dict(kind=kind,block='slide',q={'unit':'m','bounds':[.05,.35]})
    if kind=='shared_offset': policy['delta']={'unit':'m','bounds':[-.02,.02]}
    job=dict(schema_version=api.SCHEMA,audit_job='audit.json',policy=policy,
             information='none' if kind=='fixed' else 'scenario_known',budgets=dict(api.LIMITS,evaluations=80,output_mib=20))
    return write(tmp_path/'job.json',job)


def execute(tmp_path,job):
    prepared,plan=api.load_inputs(job);out=tmp_path/'bundle';out.mkdir()
    report.write_json(out/'plan.json',plan)
    result=api.execute(prepared,plan,out)
    return result,out


def seal(out,result):
    report.export_result(out,result)
    response={k:result[k] for k in ('status','execution_complete','sampled_policy_conforming','request_fully_covered','counterexample_found')}
    response.update(ok=result['execution_complete'],child=dict(exit_code=0,reaped=True))
    report.close_prepared(out,response,result['plan']);report.commit_terminal(out)


def rewrite(out,result):
    """Deliberate attacker with coherent relative hashes, no external authority."""
    write(out/'result.json',result)
    manifest=json.loads((out/'manifest.json').read_text())
    for name in manifest: manifest[name]=report.file_sha256(out/name)
    write(out/'manifest.json',manifest)
    execution=json.loads((out/'execution.json').read_text());execution['manifest_sha256']=report.file_sha256(out/'manifest.json')
    write(out/'execution.json',execution)
    write(out/'execution.closed.json',dict(execution_sha256=report.file_sha256(out/'execution.json'),cancelled=False))
    write(out/'execution.completed.json',report.native.completion_record(out))


def test_fixed_exact_native_parity(tmp_path):
    job=make_job(tmp_path,'fixed',fixed=True)
    result,out=execute(tmp_path,job)
    prepared,plan=api.audit.load_inputs(tmp_path/'audit.json');other=tmp_path/'native';other.mkdir()
    native=api.audit.execute(prepared,plan,other)
    assert result['counterexample_found'] and result['execution_complete']
    assert not result['sampled_policy_conforming']
    assert [o['criteria'] for o in result['observations']]==[o['criteria'] for o in native['observations']]
    for o in native['observations']:
        name=report.profile_name(o['scenario'],o['projection'])
        assert report.read_json(out/name)==report.read_json(other/name)
    assert result['counters']['evaluations']==3 and result['counters']['spectral_calls']==0
    assert all(report.read_json(out/f'commands_{s}.json')==[] for s in ('nominal','plus','minus'))
    seal(out,result);assert report.read_result(out)['semantic_verified']


@pytest.mark.parametrize('kind',['per_configuration','shared_offset'])
def test_two_mm_cut_requires_minus_two_mm_command(tmp_path,kind):
    result,out=execute(tmp_path,make_job(tmp_path,kind,duplicate=True))
    assert result['sampled_policy_conforming'] and result['witness_found']
    plus=report.read_json(out/'assembly_plus.json')
    assert field_info(plus,'pieces.inner.length')[0]==.402
    for c,q in [('short',.1),('long',.3)]:
        actual=field_info(plus,f'configurations.{c}.q.slide')[0]
        assert actual==pytest.approx(q-.002,abs=1e-9)
        # Independent oracle: Li + prefix + q, no acoustic evaluator.
        assert .402+.03+actual==pytest.approx(.4+.03+q,abs=1e-8)
    assert len([o for o in result['observations'] if o['scenario']=='plus' and o['configuration']=='short'])==2
    assert not result['counterexample_found']
    assert any(report.read_json(out/f'trial_{t["trial"]:04d}.json')['status']=='candidate_violated' for t in result['trials'])
    nominal=result['plan']['audit_plan']['nominal']
    for s in ('nominal','plus','minus'):
        raw=report.read_json(out/f'assembly_{s}.json')
        assert raw['blocks']==nominal['blocks'] and raw['annotations']==nominal['annotations']
        assert raw['pieces']['outer']==nominal['pieces']['outer']
        assert raw['pieces']['prefix']==nominal['pieces']['prefix']
        assert raw['configurations']['short']['annotations']=={'supplied':True}
    seal(out,result);assert report.read_result(out)['sampled_policy_conforming']


@pytest.mark.parametrize('kind,expected',[('per_configuration',True),('shared_offset',False)])
def test_incompatible_deltas_not_combined_across_candidates(tmp_path,kind,expected):
    job=make_job(tmp_path,kind,one=True)
    request=read_request(tmp_path/'request.json')[0]
    request['projections'][0]['request']['criteria'][0]['target']['value']+=.002
    request['projections'][1]['request']['criteria'][0]['target']['value']-=.002
    write(tmp_path/'request.json',request)
    result,out=execute(tmp_path,job)
    assert result['sampled_policy_conforming'] is expected
    assert not result['infeasibility_proven'] and not result['counterexample_found']
    if not expected:
        assert result['groups'][0]['status']=='search_not_resolved'
        rows=report.read_json(out/'commands_nominal.json')
        assert len({r['delta_si'] for r in rows})==1
    seal(out,result);assert report.read_result(out)['semantic_verified']


def test_duplicate_projections_cannot_get_independent_q(tmp_path):
    job=make_job(tmp_path,duplicate=True,one=True)
    r=read_request(tmp_path/'request.json')[0]
    r['projections'][-1]['request']['criteria'][0]['target']['value']+=.005
    write(tmp_path/'request.json',r)
    result,out=execute(tmp_path,job)
    assert not result['sampled_policy_conforming']
    assert next(g for g in result['groups'] if g['group']=='long')['verified']
    short=[o for o in result['observations'] if o['configuration']=='short']
    assert len({o['trial'] for o in short})==1
    assert short[0]['geometry']==short[1]['geometry']
    assert short[0]['criteria'][0]['value_si']==short[1]['criteria'][0]['value_si']


@pytest.mark.parametrize('role,conforming',[('observe',True),('hard',False)])
def test_played_obligation_vs_optional_observation(tmp_path,role,conforming):
    job=make_job(tmp_path,optional=True)
    r=read_request(tmp_path/'request.json')[0]
    for p in r['projections']:p['request']['criteria'][-1]['role']=role
    write(tmp_path/'request.json',r)
    result,out=execute(tmp_path,job)
    assert result['sampled_policy_conforming'] is conforming
    assert not result['request_fully_covered']
    assert all(o['criteria'][-1]['status']=='unsupported' for o in result['observations'])
    seal(out,result);assert report.read_result(out)['semantic_verified']


@pytest.mark.parametrize('change',[
    lambda j:j.update(extra=True),lambda j:j['budgets'].update(evaluations=True),
    lambda j:j.update(information='unknown'),lambda j:j['policy'].update(q={'bounds':[0,1]}),
    lambda j:j['policy']['q'].update(unit='cm'),lambda j:j['policy']['q'].update(bounds=[.2,.35]),
    lambda j:j.update(audit_job='job.json'),lambda j:j['budgets'].update(evaluations=1),
    lambda j:j['budgets'].update(projections=1),lambda j:j['policy'].update(block='mouth'),
    lambda j:j['policy']['q'].update(bounds=[float('inf'),1])])
def test_strict_preflight(tmp_path,change):
    job=make_job(tmp_path);j=read_request(job)[0];change(j)
    job.write_text(json.dumps(j))
    with pytest.raises(ValueError):api.load_inputs(job)


@pytest.mark.parametrize('case',['catalogue','equal_link','derived'])
def test_incompatible_historical_physical_contract_refused(tmp_path,case):
    job=make_job(tmp_path)
    r=read_request(tmp_path/'request.json')[0]
    if case=='catalogue': r['catalogue']=[dict(id='other',values={'pieces.inner.length':{'value':.41,'unit':'m'}})]
    if case=='equal_link':
        a=read_request(tmp_path/'assembly.json')[0];a['configurations']['long']['q']['slide']['value']=.1;write(tmp_path/'assembly.json',a)
        r['variables']=[dict(id='link',fields=['configurations.short.q.slide','configurations.long.q.slide'],unit='m',bounds=[.05,.35])]
    if case=='derived':
        r['derived']=[dict(id='link',field='configurations.long.q.slide',expression={'affine':[{'coefficient':1,'expression':{'field':'configurations.short.q.slide'}}],'offset':{'value':.2,'unit':'m'}},unit='m',bounds=[.05,.35])]
    write(tmp_path/'request.json',r)
    with pytest.raises(ValueError,match='catalogue|liaison|dérivée'):api.load_inputs(job)


def test_shared_rounding_law_and_absolute_boundary():
    p=dict(kind='shared_offset',block='slide',nominal_q={'a':.1,'b':.2},q_bounds=[.05,.35],delta_bounds=[-.02,.2])
    rows=commands(p,['a','b'],[.1])
    assert rows[1]['q_si']==.3 and rows[1]['q_si']!=.2+.1
    assert rows[1]['rational_sum']=='3/10' and rows[1]['serialization_error']=='0'
    rows=commands(p,['a'],[.12345678901234568])
    assert rows[0]['serialization_error']==str(Fraction(str(rows[0]['q_si']))-Fraction(rows[0]['rational_sum']))
    with pytest.raises(ValueError,match='bornes'):commands(p,['b'],[.2])


def test_exact_unrepresentable_piece_stays_unavailable(tmp_path):
    job=make_job(tmp_path)
    j=read_request(tmp_path/'audit.json')[0]
    j['scenarios'][1]['coefficients']['cut']=.3333333333333333
    write(tmp_path/'audit.json',j)
    result,out=execute(tmp_path,job)
    assert not result['sampled_policy_conforming'] and not result['counterexample_found']
    assert not (out/'assembly_plus.json').exists() and (out/'unavailable_plus.json').exists()
    assert all(g['verified'] for g in result['groups'] if g['scenario']!='plus')
    seal(out,result);assert report.read_result(out)['semantic_verified']


@pytest.mark.parametrize('kind',['fixed','per_configuration'])
def test_bad_initial_mechanics_is_counterexample_only_when_fixed(tmp_path,kind):
    job=make_job(tmp_path,kind)
    j=read_request(tmp_path/'audit.json')[0]
    j['uncertainties'][0].update(fields=['pieces.outer.length'],delta={'value':200,'unit':'mm'})
    write(tmp_path/'audit.json',j)
    result,out=execute(tmp_path,job)
    assert result['counterexample_found'] is (kind=='fixed')
    if kind!='fixed': assert any(g['status']=='search_not_resolved' for g in result['groups'])
    assert any(o['geometry_status']=='geometry_violated' for o in result['observations'])
    seal(out,result);assert report.read_result(out)['semantic_verified']


def test_verified_probe_survives_exhaustion(tmp_path,monkeypatch):
    job=make_job(tmp_path,one=True)
    original=api.search
    def exhaust_after_witness(contract,evaluate):
        evaluate(contract.initial)
        raise api.BudgetExhausted('after witness')
    monkeypatch.setattr(api,'search',exhaust_after_witness)
    result,out=execute(tmp_path,job)
    assert not result['execution_complete'] and result['sampled_policy_conforming']
    assert all(g['verified'] for g in result['groups'])
    assert all((out/f'trial_{t["trial"]:04d}.json').exists() for t in result['trials'])


def test_native_finite_difference_witness_retained(tmp_path):
    job=make_job(tmp_path,one=True)
    # Native finite difference step = 1e-4 * (.35-.05) = 30 micrometres.
    r=read_request(tmp_path/'request.json')[0]
    r['projections'][0]['request']['criteria'][0]['target']['value']+=.00003
    r['projections'][0]['request']['criteria'][0]['tolerance']['value']=.000001
    write(tmp_path/'request.json',r)
    j=read_request(job)[0];j['budgets']['evaluations']=2;write(job,j)
    result,out=execute(tmp_path,job)
    g=result['groups'][0]
    assert g['verified'] and g['selected_trial']==2
    assert g['search_trace'][1]['stage']=='finite_difference'
    assert not result['execution_complete'] and result['groups'][1]['status']=='search_not_resolved'
    seal(out,result);assert report.read_result(out)['semantic_verified']


def test_budget_during_verification_does_not_accept_hard_acoustic(tmp_path,monkeypatch):
    job=make_job(tmp_path,one=True,acoustic=True)
    monkeypatch.setattr(api.audit.TracedProjectionEvaluator,'spectrum',lambda *a,**k: (_ for _ in ()).throw(api.BudgetExhausted('verification')))
    result,out=execute(tmp_path,job)
    assert not result['execution_complete'] and not result['sampled_policy_conforming']
    assert all(o['criteria'][0]['value_si'] is not None for o in result['observations'])
    assert all(o['criteria'][1]['status']=='unresolved' for o in result['observations'])


@pytest.mark.parametrize('attack',['summary','margin','role','missing','command','physical','profile','table','aggregate','manifest_omission'])
def test_coherent_hash_tampering_rejected(tmp_path,attack):
    result,out=execute(tmp_path,make_job(tmp_path,one=True));seal(out,result)
    assert report.read_result(out)['ok']
    result=read_request(out/'result.json')[0]
    if attack=='summary':(out/'summary.txt').write_text('Conforme partout')
    if attack=='margin':
        result['observations'][0]['criteria'][0]['margin']=999
        write(out/'observation_0001.json',result['observations'][0])
    if attack=='role':
        result['observations'][0]['criteria'][0]['role']='observe';write(out/'observation_0001.json',result['observations'][0])
    if attack=='missing':
        result['observations'].pop();(out/'observation_0002.json').unlink()
        m=read_request(out/'manifest.json')[0];m.pop('observation_0002.json');write(out/'manifest.json',m)
    if attack=='command':
        rows=read_request(out/'commands_nominal.json')[0];rows[0]['q_si']+=.001;write(out/'commands_nominal.json',rows)
    if attack=='physical':
        a=read_request(out/'assembly_nominal.json')[0];a['pieces']['inner']['length']['value']+=.001;write(out/'assembly_nominal.json',a)
    if attack=='profile':
        name=report.profile_name('nominal','short');a=read_request(out/name)[0];a['segments'][0]['length_cm']+=1;write(out/name,a)
    if attack=='table':(out/'criteria.csv').write_text('invented')
    if attack=='aggregate':result['sampled_policy_conforming']=False
    if attack=='manifest_omission':
        m=read_request(out/'manifest.json')[0];m.pop('observation_0001.json');write(out/'manifest.json',m)
    rewrite(out,result)
    with pytest.raises(ValueError):report.read_result(out)


def test_other_telescope_q_and_annotations_frozen(tmp_path):
    job=make_job(tmp_path,one=True)
    a=read_request(tmp_path/'assembly.json')[0]
    a['pieces']['inner2']=copy.deepcopy(a['pieces']['inner'])
    a['pieces']['outer2']=copy.deepcopy(a['pieces']['outer'])
    a['blocks'].append(dict(a['blocks'][1],id='second',inner='inner2',outer='outer2'))
    for c in a['configurations']:a['configurations'][c]['q']['second']={'value':100,'unit':'mm'}
    write(tmp_path/'assembly.json',a)
    r=read_request(tmp_path/'request.json')[0]
    for p in r['projections']:p['request']['criteria'][0]['target']['value']+=.5
    write(tmp_path/'request.json',r)
    result,out=execute(tmp_path,job)
    assert result['sampled_policy_conforming']
    raw=read_request(out/'assembly_nominal.json')[0]
    assert raw['pieces']==a['pieces'] and raw['blocks']==a['blocks']
    for c in raw['configurations']:
        assert raw['configurations'][c]['q']['second']=={'value':100,'unit':'mm'}
    seal(out,result);assert report.read_result(out)['semantic_verified']


def test_optional_acoustic_budget_does_not_contaminate_geometry_hard(tmp_path,monkeypatch):
    job=make_job(tmp_path,one=True,acoustic=True)
    r=read_request(tmp_path/'request.json')[0]
    for p in r['projections']:
        for c in p['request']['criteria'][1:]:c['role']='observe'
    write(tmp_path/'request.json',r)
    monkeypatch.setattr(api.audit.TracedProjectionEvaluator,'spectrum',lambda *a,**k: (_ for _ in ()).throw(api.BudgetExhausted('optional spectrum')))
    result,out=execute(tmp_path,job)
    assert result['sampled_policy_conforming'] and not result['execution_complete']
    assert not result['request_fully_covered']
    assert all(o['criteria'][0]['status']=='satisfied' for o in result['observations'])
    seal(out,result);assert report.read_result(out)['semantic_verified']


def test_native_technical_failure_never_becomes_physical_counterexample(tmp_path,monkeypatch):
    job=make_job(tmp_path,'fixed',fixed=True,one=True)
    def unavailable(*a,**k):raise ValueError('capacity missing')
    monkeypatch.setattr(api.audit,'validate_design',unavailable)
    # Preflight uses the same native validator; replace only after it completes.
    monkeypatch.undo();prepared,plan=api.load_inputs(job)
    monkeypatch.setattr(api.audit,'validate_design',unavailable)
    out=tmp_path/'out';out.mkdir();result=api.execute(prepared,plan,out)
    assert not result['counterexample_found']
    assert all(o['geometry_status']=='unresolved' and 'capacity missing' in o['reason'] for o in result['observations'])


@pytest.mark.parametrize('value',[True,'0.1',float('nan')])
def test_command_api_rejects_nonnumeric_or_boolean(value):
    p=dict(kind='per_configuration',block='slide',nominal_q={'a':.1},q_bounds=[.05,.35])
    with pytest.raises(ValueError):commands(p,['a'],[value])


def test_loaded_code_must_match_declared_source(tmp_path,monkeypatch):
    import types,sys
    fake=types.ModuleType('didgeridoo_optimizer.fake_source')
    fake.__file__=str(ROOT/'didgeridoo_optimizer/optimization/adjustment_policy.py')
    namespace={'__name__':fake.__name__}
    exec(compile('def f(): return 1',str(tmp_path/'foreign.py'),'exec'),namespace)
    fake.f=namespace['f'];monkeypatch.setitem(sys.modules,fake.__name__,fake)
    with pytest.raises(ValueError,match='code chargé'):report.source_files()
