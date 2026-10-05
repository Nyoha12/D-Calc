"""Independent analytic witnesses for stationary TRAIN maps, never acoustics."""
from dataclasses import FrozenInstanceError, replace
import json
import os
import subprocess
import sys
import numpy as np
import pytest
from didgeridoo_optimizer.nonlinear.phase_reference import PhasePlan, analyze, section, _phase_index


def plan(**kw):
    fields=dict(train=(1.,2.),validations=((2.,3.),),scales=(1.,1.,1.,1.),section_index=0,section_level=0.,section_method='cubic',state_units=('m','m/s','m^3.s','m^3'))
    fields.update(kw);return PhasePlan(**fields)


def data(fs=4000,f=66.482417,kind='sine'):
    t=np.arange(4*fs+1,dtype=float)/fs
    angle=2*np.pi*(f*t+(.15*t*t if kind=='chirp' else 0.))
    z=np.column_stack((np.sin(angle),np.cos(angle),.3*np.sin(2*angle),.3*np.cos(2*angle)))
    if kind=='spike':
        phase=np.mod(f*t,1);dist=abs((phase-.70+.5)%1-.5);z[:,1]+=.2*np.maximum(1-dist/.002,0)
    if kind=='drift':z[:,3]+=.03*t
    if kind=='grow':z*=np.exp(.08*t[:,None])
    if kind=='decay':z*=np.exp(-.08*t[:,None])
    if kind=='modulation':z[:,3]*=1+.5*np.sin(2*np.pi*.23*t)
    if kind=='quasi':z[:,3]+=.1*np.sin(2*np.pi*np.sqrt(2)*f*t)
    if kind=='dc':z[:]=2.
    return t,z


def central(result,g=0):return result['groups'][g]['central']
def maximum(result,g=0):return central(result,g)['windows'][0]['maximum_scaled']


@pytest.mark.parametrize('fs',[4000,8000,12000])
@pytest.mark.parametrize('group',[1,2])
def test_sine_three_native_rates_two_fixed_groups(fs,group):
    t,z=data(fs);r=analyze(t,z,plan=plan(groups=(group,)))
    c=central(r);assert r['ok'] and c['status']=='evaluated'
    assert c['period_s']==pytest.approx(group/66.482417,rel=2e-7)
    assert maximum(r)<.002
    assert len(c['windows'][0]['components'])==4
    assert c['passage_frequency_hz']==pytest.approx(66.482417,rel=2e-7)
    assert c['return_frequency_hz']==pytest.approx(66.482417/group,rel=2e-7)
    assert r['minimal_period'] is r['fundamental'] is r['orbital_stability'] is None
    json.dumps(r,allow_nan=False)


@pytest.mark.parametrize('kind',['drift','grow','decay','modulation','quasi','chirp'])
def test_nonstationarity_of_any_recorded_state_is_visible(kind):
    t,z=data(kind=kind);r=analyze(t,z,plan=plan())
    assert maximum(r)>.01 and maximum(r,1)>.01
    assert central(r)['windows'][0]['maximum_scaled']>=central(r)['windows'][0]['rms_scaled']


def test_exact_periodic_spike_can_reconstruct_poorly():
    t,z=data(kind='spike');r=analyze(t,z,plan=plan())
    assert maximum(r)>.01
    assert central(r)['period_s']==pytest.approx(1/66.482417,rel=2e-7)
    assert r['minimal_period'] is None


def test_weak_fundamental_and_true_two_passage_return():
    t,z=data(f=40.)
    z[:,0]=np.sin(2*np.pi*80*t)
    z[:,1]=.02*np.sin(2*np.pi*40*t)+np.sin(2*np.pi*80*t)
    z[:,2]=np.cos(2*np.pi*40*t)
    r=analyze(t,z,plan=plan())
    assert maximum(r)>.5 and maximum(r,1)<1e-6
    assert central(r,1)['period_s']==pytest.approx(1/40,rel=1e-9)
    assert r['fundamental'] is None


def test_dc_has_no_section_and_no_claim():
    t,z=data(kind='dc');r=analyze(t,z,plan=plan())
    assert r['ok'] and all(g['reason']=='insufficient_training_crossings' for g in r['groups'])


def test_holdout_injection_preserves_period_map_and_sensitivity():
    t,z=data();p=plan();before=analyze(t,z,plan=p)
    z[t>=2,3]+=3.
    after=analyze(t,z,plan=p)
    for ga,gb in zip(before['groups'],after['groups']):
        assert ga['central']['period_s']==gb['central']['period_s']
        assert ga['central']['origin_s']==gb['central']['origin_s']
        assert ga['central']['coverage']==gb['central']['coverage']
        for a,b in zip(ga['sensitivities'],gb['sensitivities']):
            assert a['result']['period_s']==b['result']['period_s']
    assert maximum(after)>2.99
    assert central(after)['windows'][0]['worst_component']==3


def test_section_train_boundaries_and_no_neighbor_read():
    t=np.arange(5,dtype=float);z=np.column_stack(([-1.,1.,-1.,-1.,1.],np.ones(5)))
    p=plan(scales=(1.,1.),state_units=(),section_level=0.)
    roots,info=section(t,z,p)
    np.testing.assert_allclose(roots,[.5,3.5]);assert info['boundary_linear_roots']==2
    # Cubic on only a TRAIN slice cannot access the sentinel outside it.
    full=np.r_[z[:,0],1e200][:,None]
    p=replace(p,scales=(1.,))
    roots2,_=section(t,full[:5],p);np.testing.assert_array_equal(roots,roots2)


def test_cubic_ambiguous_segment_is_explicit():
    # Positive endpoint crossing with polynomial derivative negative inside.
    t=np.arange(6,dtype=float);z=np.array([0.,-10.,-1.,1.,10.,0.])[:,None]
    p=plan(scales=(1.,),state_units=(),section_level=0.)
    # Deterministic search through analytic polynomials, no tested root oracle.
    z=np.array([-30.,-1.,1.,30.])[:,None];t=np.arange(4,dtype=float)
    roots,info=section(t,z,p)
    assert roots is None and 'ambiguous' in info['reason']


def test_duplicate_phases_and_circular_seam_aggregate():
    p=plan(phase_tolerance=1e-8)
    phase=np.array([1e-10,1-1e-10,.2,.4,.6,.8,.2+1e-11])
    idx,info=_phase_index(phase,1.,0.,p)
    assert idx is not None and info['nodes']==5 and info['duplicate_samples']==2
    assert info['maximum_phase_gap']==pytest.approx(.2,abs=1e-8)
    assert '0/1' in info['circular_policy']


def test_gap_and_degenerate_coverage_are_explicit():
    for phases,reason in [(np.array([0.,.001,.002]),'phase_coverage_gap'),(np.ones(20),'degenerate_phase_coverage')]:
        index,info=_phase_index(phases,1.,0.,plan())
        assert index is None and info['reason']==reason


def test_period_sensitivities_keep_worse_results_and_null_short_half():
    t,z=data(kind='chirp');r=analyze(t,z,plan=plan())
    g=r['groups'][0]
    assert len(g['sensitivities'])==2
    periods=[s['result']['period_s'] for s in g['sensitivities']]
    assert periods[0]>g['central']['period_s']>periods[1]
    assert all(s['result']['map_window']==[1.,2.] for s in g['sensitivities'])
    short=analyze(t,z,plan=plan(train=(1.,1.25),validations=((2.,3.),),groups=(1,)))
    assert short['groups'][0]['sensitivities'][0]['result']['period_s'] is None
    assert short['groups'][0]['sensitivities'][0]['reason']=='insufficient_training_crossings'


def test_midpoint_signals_and_nonfinite_optional_values():
    t,z=data(f=50.);mid=(t[1:]+t[:-1])/2
    v=np.sin(2*np.pi*50*mid)
    signals=dict(pressure=dict(times=mid,values=v,unit='Pa',scale=None),jet=dict(times=mid,values=v*np.nan,unit='m^3/s',scale=1.))
    r=analyze(t,z,plan=plan(),signals=signals);c=central(r)
    assert c['signals']['jet']['status']=='unavailable'
    row=c['signals']['pressure']['windows'][0]
    assert row['components'][0]['maximum_si']<1e-8 and row['maximum_scaled'] is None
    assert row['worst_time_s'] in mid
    assert maximum(r)<1e-8


@pytest.mark.parametrize('change',[{'groups':(1,1)},{'groups':(9,)},{'scales':(1.,0.,1.,1.)},
    {'section_method':'auto'},{'validations':((1.9,3.),)},{'max_points':72002},{'max_states':387},
    {'phase_tolerance':float('nan')},{'seconds':181.},{'section_index':True}])
def test_plan_rejects_invalid_or_unbounded_requests(change):
    with pytest.raises(ValueError):plan(**change)


def test_immutable_serializable_plan_and_inline_provenance():
    p=plan();assert PhasePlan.from_dict(json.loads(json.dumps(p.as_dict())))==p
    with pytest.raises(FrozenInstanceError):p.train=(0.,1.)
    t,z=data();r=analyze(t,z,plan=p);assert r['provenance']['mode']=='inline'
    assert 'path' not in r['provenance']


@pytest.mark.parametrize('kind',['nan','time','shape','budget','dtype'])
def test_invalid_arrays_refused_before_work(kind):
    t,z=data();p=plan()
    if kind=='nan':z[300,2]=np.nan
    if kind=='time':t[100]=t[99]
    if kind=='shape':z=z[:,:3]
    if kind=='budget':p=replace(p,max_points=100)
    if kind=='dtype':z=z.astype(complex)
    with pytest.raises(ValueError):analyze(t,z,plan=p)


def test_window_outside_archive_is_unavailable_and_stop_is_partial():
    t,z=data();r=analyze(t,z,plan=plan(validations=((5.,6.),)))
    assert r['ok'];assert central(r)['windows'][0]['reason']=='validation_not_covered'
    r=analyze(t,z,plan=plan(),stop=lambda:True)
    assert not r['ok'] and r['status']=='partial'
    assert all(g['status']=='not_evaluated' for g in r['groups'])


def test_linear_c2_and_corner_oracles_are_not_universal_bounds():
    h=.01;x=np.linspace(0,h,1001)
    chord=np.sin(h)*x/h
    assert max(abs(np.sin(x)-chord))<=h*h/8
    theta=.37;J=3.;corner=J*np.maximum(x-theta*h,0)
    chord=corner[-1]*x/h
    assert np.max(abs(chord-corner))==pytest.approx(J*h*theta*(1-theta))
    assert np.max(abs(chord-corner))<=abs(J)*h/4


@pytest.mark.parametrize('order',['C','F'])
def test_maximal_nonzero_layout_under_768_mib(order):
    code='''
import resource, json, numpy as np
from didgeridoo_optimizer.nonlinear.phase_reference import PhasePlan, analyze
resource.setrlimit(resource.RLIMIT_AS,(768*1024**2,768*1024**2))
t=np.arange(72001,dtype=float)/12000
z=np.empty((72001,386),order=ORDER)
for k in range(386): z[:,k]=np.sin(2*np.pi*53.371*t+k*.01)
p=PhasePlan(train=(0.,3.),validations=((3.,6.),),scales=(1.,)*386,section_index=0,section_level=0.,section_method='linear',groups=(1,2))
r=analyze(t,z,plan=p)
assert r['ok'],r.get('reason')
assert len(r['groups'][0]['central']['windows'][0]['components'])==386
assert r['groups'][0]['central']['windows'][0]['maximum_scaled']<.002
print(json.dumps(dict(maxrss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)))
'''.replace('ORDER',repr(order))
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
    p=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,env=env,timeout=60)
    assert p.returncode==0,p.stderr+p.stdout
    assert json.loads(p.stdout)['maxrss_kib']<768*1024


def test_auxiliary_train_coverage_and_unavailable_windows_are_final():
    t,z=data();mid=(t[1:]+t[:-1])/2;mask=mid>=1.7
    r=analyze(t,z,plan=plan(),signals={'pressure':dict(times=mid[mask],values=np.sin(mid[mask]),unit='Pa',scale=None)})
    assert central(r)['signals']['pressure']['reason']=='auxiliary_train_not_covered'
    t,z=data(kind='dc');r=analyze(t,z,plan=plan())
    assert central(r)['windows'][0]['status']=='not_evaluated'


def test_result_budget_and_scaled_overflow_refused_before_calculation():
    with pytest.raises(ValueError,match='record budget'):
        plan(scales=(1.,)*386,state_units=(),groups=tuple(range(1,9)),validations=tuple((3.+i,4.+i) for i in range(16)))
    t,z=data()
    with pytest.raises(ValueError,match='scales'):
        analyze(t,z,plan=plan(scales=(1e-310,1.,1.,1.)))


def test_archived_clock_roundoff_support_does_not_shift_sample_masks():
    t,z=data();shift=2e-12;t=t+shift
    p=plan(train=(0.,1.),validations=((1.,2.),))
    r=analyze(t,z,plan=p)
    assert central(r)['status']=='evaluated'
    assert r['effective_train_samples_s'][0]==shift
    assert r['train_samples']==4000
    t=t+.01
    r=analyze(t,z,plan=p)
    assert r['reason']=='train_not_covered'


def test_r37_ordinal_sensitivity_against_independent_regression():
    t,z=data(f=50.);p=plan(sensitivity_partition='r37_grouped_crossings')
    r=analyze(t,z,plan=p)
    for g in r['groups']:
        for s in g['sensitivities']:
            c=s['result'];assert c['period_s']==pytest.approx(g['group']/50.,rel=1e-10)
            assert c['fit_window'] is None
            assert 'sharing middle' in c['section']['partition']


def test_numpy_scalar_plan_is_serializable():
    p=plan(groups=(np.int64(1),),section_index=np.int64(0),scales=(np.float64(1.),)*4)
    json.dumps(p.as_dict(),allow_nan=False)
