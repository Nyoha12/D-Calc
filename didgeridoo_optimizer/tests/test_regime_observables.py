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


def test_maximum_native_state_duration_validation_stays_bounded():
    # 192 terms, six seconds at 12 kHz. The scientific harness caps the entire
    # process at 768 MiB, including validation/reconstruction temporaries.
    t=np.arange(72001)/12000
    z=np.zeros((72001,386));pressure=np.zeros(72000)
    plan=ObservationPlan(windows=((0.,.01),),scales=(1.,)*386,section_index=0,section_level=0.)
    result=analyze(t,z,pressure,sample_rate_hz=12000,plan=plan)
    assert result['status']=='equilibrium_observed'


@pytest.mark.parametrize('disjoint',[False,True])
def test_maximum_full_windows_under_768_mib(disjoint):
    # A fresh process makes the address-space ceiling independent of pytest's
    # resident plugins/previous tests. This is prescribed data, no simulation.
    import os
    import subprocess
    import sys
    if sys.platform != 'linux':
        pytest.skip('Linux RLIMIT_AS regression')
    script = r'''
import json, resource, sys, time
resource.setrlimit(resource.RLIMIT_AS, (768*1024**2, 768*1024**2))
resource.setrlimit(resource.RLIMIT_CPU, (30, 35))
import numpy as np
from didgeridoo_optimizer.nonlinear.regime_observables import ObservationPlan, analyze
start = time.monotonic()
disjoint = sys.argv[1] == 'True'
t = np.arange(72001)/12000
z = np.zeros((72001,386)); p = np.zeros(72000)
windows = ((0.,6.),)
if disjoint:
    z[:] = 1.25; p[:] = .375
    windows = ((0.,2.),(3.,6.))
plan = ObservationPlan(windows=windows, scales=(1.,)*386,
                       section_index=0, section_level=0.)
result = analyze(t,z,p,sample_rate_hz=12000,plan=plan)
assert result['status'] == 'equilibrium_observed'
assert [w['samples'] for w in result['windows']] == ([24000,36000] if disjoint else [72000])
assert all(w['state_scaled_span'] == 0. and w['pressure_ac_rms_pa'] == 0.
           for w in result['windows'])
assert result['fundamental_hz'] is None and result['orbital_stability'] is None
print(json.dumps(dict(disjoint=disjoint, memory_limit_mib=768,
                     max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                     seconds=time.monotonic()-start, status=result['status'])))
'''
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',
             MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',
             PYTHONDONTWRITEBYTECODE='1')
    child=subprocess.run([sys.executable,'-B','-c',script,str(disjoint)],
                         env=env,capture_output=True,text=True,timeout=40)
    assert child.returncode == 0, child.stdout+child.stderr
    result=json.loads(child.stdout)
    assert result['memory_limit_mib']==768
    print(child.stdout.strip())


@pytest.mark.parametrize('layout',['C','F','rows','columns','reversed_columns'])
@pytest.mark.parametrize('edge',['native','midpoint','below','above'])
def test_window_samples_match_half_open_masks(layout,edge,monkeypatch):
    from didgeridoo_optimizer.nonlinear import regime_observables as observations
    fs=12000
    t,z,p,plan=fixture()
    # Every candidate sees precisely the historic mask-selected samples, with
    # nonzero states/pressure and no mutation of even read-only strided inputs.
    if layout=='F':z=np.asfortranarray(z)
    if layout=='rows':
        storage=np.empty((2*len(z),z.shape[1]));storage[::2]=z;z=storage[::2]
    if layout=='columns':
        storage=np.empty((len(z),2*z.shape[1]));storage[:,::2]=z;z=storage[:,::2]
    if layout=='reversed_columns':z=z[:,::-1]
    if layout!='C':
        ts=np.empty(2*len(t));ts[::2]=t;t=ts[::2]
        ps=np.empty(2*len(p));ps[::2]=p;p=ps[::2]
    pt=(t[:-1]+t[1:])/2
    bounds=np.array([t[600],t[6600],t[9000],t[15000]])
    if edge=='midpoint':bounds=pt[[600,6600,9000,15000]]
    if edge=='below':bounds=np.nextafter(bounds,-np.inf)
    if edge=='above':bounds=np.nextafter(bounds,np.inf)
    plan=replace(plan,windows=((float(bounds[0]),float(bounds[1])),
                              (float(bounds[2]),float(bounds[3]))))
    expected=[((t>=a)&(t<b),(pt>=a)&(pt<b)) for a,b in plan.windows]
    state_pressure=np.interp(t,pt,p)
    old=(t.copy(),z.copy(),p.copy())
    for value in (t,z,p):value.flags.writeable=False
    calls=[]
    def check_candidate(tw,zw,pw,sc,crossings,group,passed_plan,rate):
        mask,_=expected[len(calls)//len(plan.groups)]
        np.testing.assert_array_equal(tw,t[mask])
        np.testing.assert_array_equal(zw,z[mask])
        np.testing.assert_array_equal(pw,state_pressure[mask])
        assert passed_plan==plan and rate==fs
        calls.append(group)
        return dict(group=group,status='unresolved')
    monkeypatch.setattr(observations,'candidate',check_candidate)
    out=observations.analyze(t,z,p,sample_rate_hz=fs,plan=plan)
    assert calls==[1,2,1,2]
    for row,(mask,pmask) in zip(out['windows'],expected):
        assert row['samples']==int(mask.sum())
        assert row['effective_start_s']==t[mask][0]
        assert row['effective_end_s']==t[mask][-1]
        assert row['native_midpoint_pressure_mean_pa']==float(np.mean(p[pmask]))
        assert row['pressure_ac_rms_pa']==float(np.std(p[pmask]))
        assert row['state_scaled_span']==float(np.max(np.ptp(z[mask],axis=0)/plan.scales))
    for current,before in zip((t,z,p),old):np.testing.assert_array_equal(current,before)


@pytest.mark.parametrize('layout',['C','F','rows','columns','reversed_columns'])
@pytest.mark.parametrize('kind',['sine','drift','dc','offsets'])
def test_strided_observations_preserve_complete_results(layout,kind):
    t,z,p,plan=fixture('dc' if kind=='offsets' else kind,offset=5.)
    if kind=='offsets':
        z[t>=1.]=.125  # Constant windows, but different cross-window means.
    if layout=='F':z=np.asfortranarray(z)
    if layout=='rows':
        storage=np.empty((2*len(z),z.shape[1]));storage[::2]=z;z=storage[::2]
    if layout=='columns':
        storage=np.empty((len(z),2*z.shape[1]));storage[:,::2]=z;z=storage[:,::2]
    if layout=='reversed_columns':z=z[:,::-1]
    expected=analyze(t,z.copy(order='C'),p,sample_rate_hz=12000,plan=plan)
    actual=analyze(t,z,p,sample_rate_hz=12000,plan=plan)
    # JSON equality also preserves the sign of zero; no rounding tolerance.
    assert json.dumps(actual,sort_keys=True)==json.dumps(expected,sort_keys=True)
    if kind=='offsets':
        assert actual['status']=='unresolved'
        assert all(w['status']=='equilibrium_observed' for w in actual['windows'])


def test_maximum_nonzero_fortran_window_under_768_mib():
    # Exercise the C-order window copy and cross-window normalization together.
    # Prescribed nonzero data faults in the complete input allocation.
    import os
    import subprocess
    import sys
    if sys.platform != 'linux':
        pytest.skip('Linux RLIMIT_AS regression')
    script = r'''
import json, os, resource, time
resource.setrlimit(resource.RLIMIT_AS, (768*1024**2, 768*1024**2))
resource.setrlimit(resource.RLIMIT_CPU, (30, 35))
import numpy as np
from didgeridoo_optimizer.nonlinear.regime_observables import ObservationPlan, analyze
start = time.monotonic()
t = np.arange(72001)/12000
z = np.ones((72001,386), order='F'); p = np.zeros(72000)
assert z.flags.f_contiguous and not z.flags.c_contiguous
z.flags.writeable = False
plan = ObservationPlan(windows=((0.,6.),), scales=(1.,)*386,
                       section_index=0, section_level=0.)
result = analyze(t,z,p,sample_rate_hz=12000,plan=plan)
assert result['status'] == 'equilibrium_observed'
assert len(result['windows']) == 1
window = result['windows'][0]
assert window['samples'] == 72000
assert window['effective_start_s'] == 0.
assert window['effective_end_s'] == t[71999]
assert window['state_scaled_span'] == 0. and window['pressure_ac_rms_pa'] == 0.
assert window['native_midpoint_pressure_mean_pa'] == 0.
assert window['fft_auxiliary_hz'] is None and window['passage_frequency_hz'] is None
assert result['fundamental_hz'] is None and result['orbital_stability'] is None
assert np.all(z == 1.)
print(json.dumps(dict(layout='F', nonzero=True, memory_limit_mib=768,
                     max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                     pid=os.getpid(), seconds=time.monotonic()-start,
                     status=result['status'])))
'''
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',
             MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',
             PYTHONDONTWRITEBYTECODE='1')
    child=subprocess.run([sys.executable,'-B','-c',script],
                         env=env,capture_output=True,text=True,timeout=40)
    assert child.returncode == 0, child.stdout+child.stderr
    result=json.loads(child.stdout)
    assert result['layout']=='F' and result['nonzero']
    assert result['memory_limit_mib']==768
    print(child.stdout.strip())
