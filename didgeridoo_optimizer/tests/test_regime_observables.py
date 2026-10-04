from dataclasses import FrozenInstanceError, replace
import json
import numpy as np
import pytest
from didgeridoo_optimizer.nonlinear.regime_observables import ObservationPlan, analyze, numeric_array


def fixture(kind='sine',offset=0.,fs=12000):
    t=np.arange((4 if kind=='rich' else 2)*fs+1)/fs;theta=2*np.pi*66.4824170613*t
    z=np.column_stack((np.sin(theta),np.cos(theta),3*np.cos(theta-.2),3*np.sin(theta-.2)))
    p=offset+np.sin(2*np.pi*66.4824170613*(t[:-1]+.5/fs))
    if kind=='dc':z[:]=0;p[:]=offset
    if kind=='drift':z[:,3]+=.1*t
    if kind=='modulation':z*= (1+.08*np.sin(2*np.pi*1.3*t))[:,None]
    if kind=='quasi':z[:,3]+=np.sin(2*np.pi*np.sqrt(2)*10*t)
    if kind=='weak':z[:,0]=.05*np.sin(theta)+np.sin(2*theta);p=.05*p+np.sin(4*np.pi*66.4824170613*(t[:-1]+.5/fs))
    if kind=='rich':
        phase=(t*66.4824170613)%1;tent=np.maximum(0,1-abs(phase-.70)/.02)
        z[:,1]+=.06*tent
    plan=ObservationPlan(windows=((0.,.5),(.5,1.),(1.,1.5),(1.5,2.)),scales=(1.,1.,3.,3.),section_index=0,section_level=0.)
    if kind=='rich':plan=replace(plan,windows=((2.,2.5),(2.5,3.),(3.,3.5),(3.5,4.)),scales=(1.,1.,1.,1.))
    return t,z,p,plan


@pytest.mark.parametrize('offset',[0.,100.,1e7])
def test_dc_never_gets_first_bin_or_recurrence(offset):
    t,z,p,plan=fixture('dc',offset)
    out=analyze(t,z,p,sample_rate_hz=12000,plan=plan)
    assert out['status']=='equilibrium_observed'
    assert all(w['fft_auxiliary_hz'] is None and w['passage_frequency_hz'] is None for w in out['windows'])
    assert out['fundamental_hz'] is None and out['orbital_stability'] is None
    json.dumps(out,allow_nan=False)


@pytest.mark.parametrize('offset',[0.,100.,1e7])
def test_sinus_dc_mean_removed_before_hann(offset):
    t,z,p,plan=fixture(offset=offset)
    out=analyze(t,z,p,sample_rate_hz=12000,plan=plan)
    assert out['status']=='recurrence_observed'
    assert out['windows'][0]['fft_auxiliary_hz']==pytest.approx(66.,abs=2.)
    assert len(out['groups'])==2
    assert out['minimal_period_s'] is None


@pytest.mark.parametrize('kind',['drift','modulation','quasi'])
def test_hidden_state_and_nonstationarity_not_recurrence(kind):
    t,z,p,plan=fixture(kind)
    out=analyze(t,z,p,sample_rate_hz=12000,plan=plan)
    assert out['status']!='recurrence_observed'
    assert all(len(w['candidates'])==2 for w in out['windows'])


def test_rich_periodic_group_two_cannot_name_33hz_note():
    t,z,p,plan=fixture('rich')
    out=analyze(t,z,p,sample_rate_hz=12000,plan=plan)
    assert out['fundamental_hz'] is None and out['minimal_period_status']=='not_identified'
    assert all(w['passage_frequency_hz']==pytest.approx(66.482417,rel=1e-4) for w in out['windows'])
    assert all(w['candidates'][1]['return_frequency_hz']==pytest.approx(33.2412,rel=1e-4) for w in out['windows'])
    assert any(not w['candidates'][0]['checks']['shape_scaled_span'] for w in out['windows'])
    assert all(w['candidates'][1]['phase_points']>w['candidates'][0]['phase_points'] for w in out['windows'])


def test_weak_fundamental_fft_is_auxiliary_only():
    t,z,p,plan=fixture('weak')
    out=analyze(t,z,p,sample_rate_hz=12000,plan=plan)
    assert out['windows'][0]['fft_auxiliary_hz']>120
    assert out['fundamental_hz'] is None
    assert not out['windows'][0]['candidates'][0]['checks']['return_scaled_max']


@pytest.mark.parametrize('change',[dict(groups=[1,2]),dict(groups=(True,)),dict(scales=(0.,1.)),dict(section_index=9),dict(windows=((0.,1.),(.5,2.))),dict(max_phase_points=5000),dict(scales=(float('nan'),1.))])
def test_plan_strict_and_immutable(change):
    *_,plan=fixture()
    with pytest.raises((ValueError,TypeError)):replace(plan,**change)
    with pytest.raises(FrozenInstanceError):plan.groups=(2,)


@pytest.mark.parametrize('kind',['bool','shape','nan','nonmonotone','huge_scale'])
def test_bad_arrays_refused(kind):
    t,z,p,plan=fixture()
    if kind=='bool':z=z.astype(bool)
    if kind=='shape':z=z[:-1]
    if kind=='nan':z[10,1]=np.nan
    if kind=='nonmonotone':t[1]=t[0]
    if kind=='huge_scale':plan=replace(plan,scales=(1e-320,)*4)
    with pytest.raises(ValueError):analyze(t,z,p,sample_rate_hz=12000,plan=plan)


def test_allocation_guards_precede_array_coercion():
    class Hostile:
        def __array__(self,*a):raise AssertionError('must never convert')
    with pytest.raises(ValueError):numeric_array(Hostile(),'state',(72001,386))
    *_,plan=fixture()
    d=plan.as_dict();d['windows']=[[0.]*10000]
    with pytest.raises(ValueError):ObservationPlan.from_dict(d)
    t,z,p,plan=fixture();plan=replace(plan,max_shape_cells=130)
    out=analyze(t,z,p,sample_rate_hz=12000,plan=plan)
    assert all(c['reason']=='shape_allocation_or_work_budget_exceeded' for w in out['windows'] for c in w['candidates'])
