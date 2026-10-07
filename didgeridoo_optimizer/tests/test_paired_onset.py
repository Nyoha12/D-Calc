"""Public mathematical oracles; no private R40 files are imported."""
import copy
from dataclasses import replace
import math
import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear import paired_onset as core
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters
from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
from didgeridoo_optimizer.pipeline import paired_onset as pipeline

P=DimensionedLipParameters()
RHO=1.204
GRID={'fractions':[.02,.08,.16,.28,.42,.58,.74,.90,.98]}
VARIANTS=[{}, {'resonance_hz':70.}, {'resonance_hz':90.}, {'damping_ratio':.15},
          {'damping_ratio':.25}, {'rest_opening_m':.0006}, {'rest_opening_m':.001},
          {'effective_area_m2':2.4e-6}, {'effective_area_m2':3.6e-6}]


def modal(R0=0.,fs=12000):
    w=2*np.pi*70;g=w/8
    return PassiveResonator([1e7*g],[g],[w],R0=R0,sample_rate_hz=fs,domain='continuous',
        dc_origin=dict(kind='explicit',description='Synthetic modal oracle, not measurement'))


def run(model=None,**kw):
    options=dict(refinements=23,max_evaluations=100);options.update(kw)
    return core.analyze(model or modal(),P,RHO,GRID,**options)


@pytest.mark.parametrize('R0',[0.,1100.])
@pytest.mark.parametrize('changes',VARIANTS)
def test_independent_quartic(R0,changes):
    p=replace(P,**changes);m=modal(R0);pressure=.4*core.pressure_limit(p)
    _,e=core.equilibrium(m,pressure,RHO,p)
    # Independent expansion of Dl*den +(C*Dl-A*B+A²s)*(R0*den+a*s).
    k=p.mass_kg*(2*np.pi*p.resonance_hz)**2;r=2*p.damping_ratio*p.mass_kg*2*np.pi*p.resonance_hz
    dl=np.poly1d([p.mass_kg,r,k]);den=np.poly1d([1.,m.gamma[0],m.omega[0]**2]);s=np.poly1d([1.,0.])
    poly=dl*den+(e['C']*dl-p.effective_area_m2*e['B']+p.effective_area_m2**2*s)*(R0*den+m.a[0]*s)
    roots=np.linalg.eigvals(core.matrix(m,pressure,RHO,p)[0]);oracle=np.roots(poly)
    assert max(min(abs(v-roots)) for v in oracle)<1e-7
    assert max(abs(core.characteristic(m,v,pressure,RHO,p)[0])/core.characteristic(m,v,pressure,RHO,p)[1] for v in roots)<1e-9


@pytest.mark.parametrize('fs',[4000,12000])
@pytest.mark.parametrize('changes',VARIANTS)
def test_native_equilibrium_and_actual_simultaneous_step_derivatives(fs,changes):
    p=replace(P,**changes);m=modal(1100.,fs);pr=.4*core.pressure_limit(p)
    j,state,e=core.matrix(m,pr,RHO,p);sc=core.scales(m,p);active=replace(p,mouth_pressure_kpa=pr/1000)
    def step(z):
        c=SimultaneousCoupling(m,params=active,rho=RHO,v2_port_model='conjugate',initial_state=z)
        assert c.step()['ok'];return c.state.copy()
    assert np.max(abs((step(state)-state)/sc))<1e-10
    assert e['native_residuals']['scaled_max']<1e-8 and abs(e['static_residual_pa'])<1e-9
    phi=np.linalg.solve(np.eye(len(sc))-j/(2*fs),np.eye(len(sc))+j/(2*fs))
    rng=np.random.default_rng(40)
    for d in [np.eye(len(sc))[0],np.eye(len(sc))[1],*rng.normal(size=(2,len(sc)))]:
        d/=np.linalg.norm(d);target=phi@d
        for eps in (1e-5,3e-6):
            actual=(step(state+eps*sc*d)/sc-step(state-eps*sc*d)/sc)/(2*eps)
            assert np.max(abs(actual-target))/max(np.max(abs(target)),1.)<2e-6


def test_known_conjugate_modal_threshold_and_real_axis():
    m=modal();result=run(m)
    assert result['qualification']=='synthetic' and len(result['candidates'])==1
    candidate=result['candidates'][0];root=candidate['root']
    assert candidate['status']=='local_crossing_verified'
    assert abs(root['pressure_pa']-4003.211086430)<.001
    assert root['marginal_relative_residual']<1e-7
    assert candidate['bracket_pa'][0]<4003.211086430<candidate['bracket_pa'][1]
    checked=[]
    def real_only(f):
        assert type(f) is float;checked.append(f);return m.discrete_response([f])[0]
    result=core.marginal_axis(m,P,RHO,[root['pressure_pa']+2,root['discrete_frequency_hz']+.01],real_only,
        pressure_bounds=[100,6000],frequency_bounds=[20,150],max_iterations=12,max_evaluations=160)
    assert result['status']=='marginal_candidate'
    assert abs(result['pressure_pa']-4003.211086430)<1e-6 and checked


@pytest.mark.parametrize('pressure',[0.,-1.,float('nan'),float('inf'),True,core.pressure_limit(P)])
def test_free_domain_refused(pressure):
    with pytest.raises(ValueError):core.matrix(modal(),pressure,RHO,P)


@pytest.mark.parametrize('changes',[{'pressure_force_sign':1.},{'rest_opening_m':P.min_opening_m},
                                    {'mass_kg':True},{'resonance_hz':float('inf')}])
def test_sign_contact_and_invalid_parameter_refused(changes):
    with pytest.raises(ValueError):core.matrix(modal(),1000,RHO,replace(P,**changes))


def test_uniqueness_bound_and_unsupported_ports():
    with pytest.raises(ValueError,match='uniqueness'):core.matrix(modal(1e10),3000,RHO,P)
    with pytest.raises(ValueError,match='unsupported'):run(port_model='jet-only')


def test_invariants_no_mutation_and_pressure_semantics():
    m=modal(1100);before=m.parameters();fields=P.as_dict();grid=copy.deepcopy(GRID)
    result=core.analyze(m,P,RHO,grid,refinements=23,max_evaluations=100)
    grid['fractions'][0]=.01
    assert result['pressure_grid_requested']==GRID
    assert m.parameters()==before and fields==P.as_dict()
    assert result['reference_mouth_pressure_kpa']==1.5
    assert result['grid'][0]['effective_mouth_pressure_kpa']==result['grid'][0]['pressure_pa']/1000


def test_absence_ambiguity_and_insufficient_refinement():
    absent=core.analyze(modal(),P,RHO,{'pa':[100.,200.]},refinements=23,max_evaluations=100)
    assert not absent['candidates'] and absent['status']=='complete'
    window={'id':'w','pressure_pa':[1000,6000],'frequency_hz':[20,150]}
    assert core.match_candidate(absent,window)['candidate'] is None
    value=run();value['candidates']*=2
    assert core.match_candidate(value,window)['status']=='ambiguous'
    assert run(refinements=0)['candidates'][0]['status']=='not_resolved'
    c=run()['candidates'][0];bad=copy.deepcopy(c)
    bad['root']['pair_separation_s']=0
    assert not core.crossing_verified(bad['root'],bad['sides'],[])
    bad=copy.deepcopy(c);bad['sides'].reverse()
    assert not core.crossing_verified(bad['root'],bad['sides'],[])


def test_pressure_grid_budget_before_eigensolve(monkeypatch):
    monkeypatch.setattr(np.linalg,'eigvals',lambda *a:pytest.fail('must not allocate/solve'))
    for grid in ({'fractions':[.1]*34},{'fractions':[.2,.1]},{'pa':[0.,2.]},{'pa':[True,2.]}, {'fractions':[0.,1.]}):
        with pytest.raises(ValueError):core.analyze(modal(),P,RHO,grid,refinements=23,max_evaluations=100)


@pytest.mark.parametrize('terms',[155,171,192])
def test_native_large_realizations_retained(terms):
    omega=np.arange(1,terms+1)*100.
    m=PassiveResonator(np.ones(terms),np.ones(terms),omega,R0=0.,sample_rate_hz=12000,
        dc_origin=dict(kind='explicit',description='Synthetic size check'))
    j,state,e=core.matrix(m,1000,RHO,P)
    assert j.shape==(2+2*terms,2+2*terms) and len(state)==2+2*terms


def test_partial_counters_and_acquired_units():
    rows=[];value=run(max_evaluations=3,after_unit=lambda r:rows.append(copy.deepcopy(r)))
    assert value['status']=='partial' and value['counters']['evaluations']==3
    assert len(value['grid'])==3 and rows
    stopped=run(stop=lambda:True)
    assert stopped['status']=='partial' and stopped['counters']['evaluations']==0


def test_invalid_tmm_api_never_calls_evaluator():
    for seed,pressure in [([True,70],[100,6000]),([1000,70],[-1,6000]),([1000],[100,6000])]:
        with pytest.raises(ValueError):
            core.marginal_axis(modal(),P,RHO,seed,lambda f:pytest.fail('TMM called'),
                pressure_bounds=pressure,frequency_bounds=[20,150],max_iterations=12,max_evaluations=100)


def comparison_plan(criteria=()):
    recipe=dict(sample_rate_hz=12000,loss_model='zk',radiation_model='legacy',air_reference='ck_dry20',h_cm=.5,basis_completion='r29')
    return dict(request=dict(cases=[dict(id=k,recipe=recipe) for k in ['a','b']],scenarios=[dict(id='central')],
        windows=[dict(id='w',pressure_pa=[1000,6000],frequency_hz=[20,150])],criteria=list(criteria)),
        cases=[dict(id=k,effective={'air':'identical'}) for k in ['a','b']])


def test_pairs_identical_and_permutation():
    a=run();b=copy.deepcopy(a)
    for c in b['candidates']:
        c['root']['pressure_pa']+=1;c['bracket_pa']=[v+1 for v in c['bracket_pa']]
    p=comparison_plan();units={('a','central'):a,('b','central'):b}
    first=pipeline.compare(p,units)['differentials'][0]
    p['request']['cases'].reverse()
    second=pipeline.compare(p,units)['differentials'][0]
    assert first['pressure_difference_pa']==-second['pressure_difference_pa']==1
    assert first['pressure_ratio']*second['pressure_ratio']==pytest.approx(1.)
    same=pipeline.paired_value(units,'a','a','central',p['request']['windows'][0])
    assert same['pressure_difference_pa']==same['frequency_difference_hz']==0
    assert same['pressure_difference_bounds_pa']==[0.,0.]


def test_hard_observe_unsupported_no_compensation_and_notes():
    base=dict(id='pressure',role='hard',case='a',scenario='central',window='w',observable='onset_pressure',
              target=dict(value=4003.21108643,unit='Pa'),tolerance=dict(value=.001,unit='Pa'))
    unsupported=dict(id='played',role='observe',case='a',scenario='central',window='w',observable='played_frequency')
    p=comparison_plan([base,unsupported]);value=run();units={('a','central'):value,('b','central'):value}
    result=pipeline.compare(p,units)
    assert result['hard_conforming'] and result['coverage']=='partial'
    unsupported['role']='hard';p=comparison_plan([base,unsupported])
    assert not pipeline.compare(p,units)['hard_conforming']
    frequency=dict(base,id='frequency',observable='onset_frequency',target=dict(note='A4',reference_hz=440,cents=0),tolerance=dict(value=1,unit='cent'))
    result=pipeline.compare(comparison_plan([base,frequency]),units)
    assert result['criteria'][0]['status']=='satisfied' and result['criteria'][1]['status']=='violated'
    assert not result['hard_conforming']
    assert pipeline.compare(comparison_plan(),units)['hard_conforming']


def test_identical_different_ids_have_correlated_zero_uncertainty():
    value=run();p=comparison_plan([dict(id='zero',role='hard',pair=['a','b'],scenario='central',window='w',
        observable='pressure_difference',target=dict(value=0,unit='Pa'),tolerance=dict(value=0,unit='Pa'))])
    result=pipeline.compare(p,{('a','central'):value,('b','central'):copy.deepcopy(value)})
    assert result['hard_conforming'] and result['differentials'][0]['pressure_difference_bounds_pa']==[0.,0.]
