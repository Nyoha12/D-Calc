"""Independent public analytic and native witnesses. No private runtime fixtures."""
import copy
from dataclasses import replace
import math

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear import register_target as core
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters
from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator,digest
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling


def request(fs=4000,steps=4000):
    return dict(schema=core.SCHEMA,case=dict(kind='synthetic',id='modal',sample_rate_hz=fs,
        description='Public analytic modal source, not a fitted instrument'),reference_lips=DimensionedLipParameters().as_dict(),
        rho_kg_m3=1.204,source='ideal',port_model='conjugate',initial=dict(kind='native'),
        plateaus=[dict(id='a',pressure_pa=1500.,resonance_hz=80.,damping_ratio=.2,steps=steps,duration_s=steps/fs)],
        windows=[dict(id='hold',plateau='a',start_step=0,stop_step=steps)],
        observation=dict(band_hz=[40.,400.],frame_steps=1200,hop_steps=600,min_rms_pa=.01,min_periods=2,
            max_error=.015,competitor_margin=.00001,max_drift_cents=15.,max_dispersion_cents=5.,
            multiple_tolerance=.015,period_error_floor=1e-8,section_level_pa=0.,section_direction='rising',phase=None,pressure_scale_pa=1.),
        criteria=[],budgets=dict(chunk_steps=128,new_steps=steps,new_seconds=steps/fs,child_seconds=60.,
            orchestrator_seconds=120.,memory_mib=768,blas_threads=1,output_mib=40,observation_ops=100000000))


def model(fs=4000):
    return PassiveResonator([1e6],[55.],[2*np.pi*70],R0=1000.,sample_rate_hz=fs,
        dc_origin=dict(kind='explicit',description='Synthetic public modal oracle'))


def experiment(r=None):
    r=request(steps=64) if r is None else r;m=model(core.sample_rate(r))
    return core.Experiment(m,r,source=dict(kind='synthetic',description='Public modal source',model_sha256=digest(m.parameters())))


def two_plateaus(change=True):
    r=request(steps=80);s=r['plateaus'][0];s.update(steps=40,duration_s=.01)
    second=dict(s,id='b');second.update(resonance_hz=110. if change else 80.,damping_ratio=.15 if change else .2,pressure_pa=1800. if change else 1500.)
    r['plateaus'].append(second);r['windows'][0]['stop_step']=40
    return r


def finish(e):
    total=sum(s['steps'] for s in e.request.as_dict()['plateaus']);chunks=[]
    while e.accepted<total:chunks.append(e.advance(min(128,total-e.accepted)))
    return chunks


@pytest.mark.parametrize('split',[0,1,19,39,40,41,79,80])
@pytest.mark.parametrize('change',[False,True])
def test_exact_resume_midplateau_and_boundary(split,change):
    r=two_plateaus(change);full=experiment(r);finish(full)
    partial=experiment(r);a=partial.advance(split);saved=partial.checkpoint()
    resumed=experiment(r);resumed.restore(saved)
    assert resumed.checkpoint()==saved
    finish(resumed)
    assert resumed.checkpoint()==full.checkpoint()
    assert len(resumed.events)==int(change)
    assert resumed.coupling.last_dissipation_w==full.coupling.last_dissipation_w
    reset=copy.deepcopy(resumed.checkpoint()['payload']['native']['payload']['initial_state'])
    resumed.coupling.reset();assert resumed.coupling.state.tolist()==reset and resumed.coupling.time_s==0


def test_bitwise_native_constant_controls_and_diagnostics():
    r=two_plateaus(False);e=experiment(r);c=SimultaneousCoupling(model(),params=DimensionedLipParameters(),rho=1.204,v2_port_model='conjugate')
    out=e.advance(80);rows=[]
    for i in range(80):
        d=c.step();rows.append(d)
        assert np.array_equal(out['arrays']['states'][i+1],c.state)
        assert out['arrays']['times'][i+1]==c.time_s
        assert np.array_equal(out['arrays']['values'][i],[d[k] for k in core.COLUMNS])
    assert e.coupling.export_checkpoint()==c.export_checkpoint()
    for k in core.SUM_FIELDS:
        assert e.sums[k]==pytest.approx(math.fsum(x[k] for x in rows),rel=1e-14,abs=1e-20)


@pytest.mark.parametrize('change',[dict(resonance_hz=120.),dict(pressure_pa=2300.),dict(damping_ratio=.4)])
def test_command_state_clock_loss_energy(change):
    r=two_plateaus(False);r['plateaus'][1].update(change);e=experiment(r);e.advance(40)
    before=e.coupling.export_checkpoint();x=e.coupling.state[0];e.advance(1);event=e.events[0]
    a,b=event['before']['payload'],event['after']['payload']
    assert a==before['payload']
    for key in ('state','time_s','last_dissipation_w'):assert a['snapshot'][key]==b['snapshot'][key]
    assert b['snapshot']['diagnostics'] is None
    assert b['initial_state']==a['snapshot']['state']
    dk=1e-4*(2*np.pi)**2*(r['plateaus'][1]['resonance_hz']**2-80**2)
    assert event['command_work_j']==pytest.approx(.5*dk*x*x,abs=1e-20)
    assert event['lip_energy_after_j']-event['lip_energy_before_j']==pytest.approx(event['command_work_j'],abs=1e-18)
    assert e.initial['payload']['initial_state']!=b['initial_state']


def test_interruption_retains_accepted_step_and_no_duplicate_event():
    r=two_plateaus();full=experiment(r);whole=full.advance(80)
    e=experiment(r);a=e.advance(80,stop=lambda:e.accepted==41)
    assert not a['ok'] and len(a['arrays']['midpoint_times'])==41
    f=experiment(r);f.restore(e.checkpoint());b=f.advance(39)
    assert np.array_equal(np.vstack([a['arrays']['states'],b['arrays']['states'][1:]]),whole['arrays']['states'])
    assert f.checkpoint()==full.checkpoint()
    with pytest.raises(ValueError):f.advance(1)


@pytest.mark.parametrize('mutation',['count','hash','reset','clock','event','request','sum','positive_sum'])
def test_checkpoint_refusals_atomic(mutation):
    e=experiment(two_plateaus());e.advance(42);before=e.checkpoint();bad=copy.deepcopy(before);p=bad['payload']
    if mutation=='count':p['accepted_steps']=81
    if mutation=='hash':bad['sha256']='0'*64
    if mutation=='reset':p['native']['payload']['initial_state'][0]+=1; p['native']['sha256']=digest(p['native']['payload'])
    if mutation=='clock':p['accepted_steps']=43
    if mutation=='event':p['events']*=2
    if mutation=='request':p['request']['rho_kg_m3']=2.
    if mutation=='sum':p['sums']['jet_loss_j']=-1.
    if mutation=='positive_sum':p['sums']['source_work_j']+=.01
    if mutation!='hash':bad['sha256']=digest(p)
    with pytest.raises(ValueError):e.restore(bad)
    assert e.checkpoint()==before


@pytest.mark.parametrize('field,value',[('mass_kg',True),('mass_kg',float('nan')),('resonance_hz',float('inf')),('pressure_force_sign',1.)])
def test_invalid_parameters(field,value):
    r=request();r['reference_lips'][field]=value
    with pytest.raises(ValueError):core.validate_request(r)


@pytest.mark.parametrize('key',['mass_kg','rest_opening_m','flow_coefficient','pressure_force_sign'])
def test_immutable_plateau_fields_refused(key):
    r=request();r['plateaus'][0][key]=1.
    with pytest.raises(ValueError):core.validate_request(r)


def test_request_immutable_cyclic_and_derived_overflow():
    r=request();p=core.Request.validate(r);r['plateaus'][0]['pressure_pa']=7
    assert p.as_dict()['plateaus'][0]['pressure_pa']==1500.
    r['cycle']=r
    with pytest.raises(ValueError):core.validate_request(r)
    r=request();r['reference_lips']['mass_kg']=1e308
    with pytest.raises((ValueError,OverflowError)):core.validate_request(r)


def signal(kind,fs=4000):
    t=(np.arange(fs)+.5)/fs
    if kind=='sine':y=np.sin(2*np.pi*70*t)
    if kind=='strong210':y=.3*np.sin(2*np.pi*70*t)+3*np.sin(2*np.pi*210*t)
    if kind=='missing70':y=np.sin(2*np.pi*140*t)+np.sin(2*np.pi*210*t)
    if kind=='pure210':y=np.sin(2*np.pi*210*t)
    if kind=='constant':y=np.full(fs,3.)
    if kind=='drift':y=10*t
    if kind=='chirp':y=np.sin(2*np.pi*(70*t+30*t*t))
    if kind=='quasi':y=np.sin(2*np.pi*70*t)+np.sin(2*np.pi*70*np.sqrt(2)*t)
    if kind=='noise':y=np.random.default_rng(46).normal(size=fs)
    return y


@pytest.mark.parametrize('kind,target',[('sine',70),('strong210',70),('missing70',70),('pure210',210)])
def test_independent_periodic_signals(kind,target):
    r=request();result=core.observe_pressure(signal(kind),4000,r['observation'])
    assert result['status']=='observed',result
    assert result['frequency_hz']==pytest.approx(target,abs=.15)
    assert len(result['frames'])>1 and all(len(x['sensitivities'])==2 for x in result['frames'])
    if kind=='strong210':assert result['spectral_peak_hz']==210
    candidates=result['frames'][0]['central']['candidates']
    assert all(c['frequency_hz']>=40 for c in candidates)
    if kind=='pure210':assert any(c['multiple_of_selected']==3 for c in candidates)


@pytest.mark.parametrize('kind',['constant','drift','chirp','quasi','noise'])
def test_nonstationary_or_inactive_not_note_found(kind):
    result=core.observe_pressure(signal(kind),4000,request()['observation'])
    assert result['frequency_hz'] is None and result['status']=='unresolved'


def test_window_insufficient_nyquist_and_operations_quota():
    r=request();assert core.observe_pressure(np.ones(100),4000,r['observation'])['reason']=='insufficient_window'
    for high in (1001,2000,3000):
        s=copy.deepcopy(r);s['observation']['band_hz'][1]=high
        with pytest.raises(ValueError):core.validate_request(s)
    r['budgets']['observation_ops']=1
    with pytest.raises(ValueError,match='operation quota'):core.validate_request(r)


def analyze(r,y,hidden=False):
    fs=4000;t=np.arange(fs+1)/fs;z=np.column_stack([np.sin(2*np.pi*70*t),np.cos(2*np.pi*70*t),np.sin(4*np.pi*70*t),np.cos(4*np.pi*70*t)])
    if hidden:z[:,3]+=t
    return core.analyze(r,times=t,states=z,midpoint_times=(np.arange(fs)+.5)/fs,pressure=y,
        source=dict(kind='synthetic',description='Analytic public pressure and complete states'))


def criterion(name='played_frequency',**kw):
    return dict(id='music',role='hard',observable=name,windows=['hold'],**kw)


def test_notes_cents_false_target_independent_pressure_and_target_invariance():
    r=request();r['criteria']=[criterion(target=dict(note='C#2',reference_hz=440.,cents=17.488),tolerance=dict(value=2.,unit='cent')),
        dict(id='pressure',role='hard',observable='pressure_max',windows=['hold'],maximum=dict(value=1000.,unit='Pa'))]
    a=analyze(r,signal('sine'));assert a['criteria'][0]['status']=='pass';assert a['criteria'][1]['status']=='fail';assert a['hard_conforming'] is False
    r['criteria'][0]['target']=dict(value=210.,unit='Hz');b=analyze(r,signal('sine'))
    assert b['criteria'][0]['status']=='fail' and a['windows']==b['windows']


def test_observe_unavailable_does_not_block_independent_hard_and_no_peak_substitution():
    r=request();r['criteria']=[criterion(target=dict(value=210.,unit='Hz'),tolerance=dict(value=1.,unit='Hz')),
        dict(id='pressure',role='hard',observable='pressure_max',windows=['hold'],maximum=dict(value=2000.,unit='Pa'))]
    result=analyze(r,signal('quasi'));assert result['criteria'][0]['status']=='unresolved';assert result['criteria'][1]['status']=='pass'
    assert result['hard_conforming'] is None and result['windows']['hold']['spectral_peak_hz'] is not None
    r['criteria'][0]['role']='observe';assert analyze(r,signal('quasi'))['hard_conforming'] is True


def test_missing_data_null_and_description_without_criterion():
    r=request();r['criteria']=[criterion('activity_rms',minimum=dict(value=.1,unit='Pa'))]
    a=core.analyze(r,times=np.array([0.]),states=np.zeros((1,4)),midpoint_times=np.empty(0),pressure=np.empty(0),
        source=dict(kind='supplied_arrays',description='Explicit empty acquired history'))
    assert a['criteria'][0]['values_si']==[None] and a['hard_conforming'] is None
    r['criteria']=[];assert analyze(r,signal('sine'))['conformity']=='descriptive'


def test_distinct_window_ratios_and_no_double_register():
    r=request();r['windows']=[dict(id='low',plateau='a',start_step=0,stop_step=2000),dict(id='high',plateau='a',start_step=2000,stop_step=4000)]
    r['criteria']=[dict(id='ratio',role='hard',observable='frequency_ratio',windows=['low','high'],target=dict(value=3.,unit='1'),tolerance=dict(value=.01,unit='1'))]
    y=signal('sine');y[2000:]=signal('pure210')[2000:]
    result=analyze(r,y);assert result['hard_conforming'] is True
    r['criteria'][0]['windows']=['low','low']
    with pytest.raises(ValueError):core.validate_request(r)


def test_hidden_state_phase_disjoint_train_and_both_sensitivities():
    r=request();r['observation']['phase']=dict(train=[0.,.5],validations=[[.5,1.]],scales=[1.]*4,state_units=['m','m/s','m^3.s','m^3'],
        section_index=0,section_level=0.,section_method='linear',groups=[1,2],min_crossings=8)
    a=analyze(r,signal('sine'));b=analyze(r,signal('sine'),True)
    assert a['windows']==b['windows']
    for group in b['phase']['groups']:
        assert len(group['sensitivities'])==2
        assert group['central']['windows'][0]['maximum_scaled']>.1
        assert len(group['central']['windows'][0]['components'])==4
        assert 'pressure' in group['central']['signals']
    assert b['phase']['orbital_stability'] is None


@pytest.mark.parametrize('amplitude',[.001,.003,.01,.03])
def test_weak_subharmonic_competitor_not_confident_third_harmonic(amplitude):
    result=core.observe_pressure(amplitude*signal('sine')+signal('pure210'),4000,request()['observation'])
    assert result['frequency_hz'] is None or abs(result['frequency_hz']-70)<.15


def test_integer_signal_safe_conversion_and_duration_si_seconds():
    r=request();r['criteria']=[criterion('duration',minimum=dict(value=.9,unit='s'))]
    assert analyze(r,signal('sine'))['hard_conforming'] is True
    y=(signal('sine')*10000).astype(np.int16)
    observed=core.observe_pressure(y,4000,r['observation'])
    assert observed['frequency_hz']==pytest.approx(70.,abs=.15)
    with pytest.raises(ValueError):core.observe_pressure(np.full(4000,1e308),4000,r['observation'])


def test_explicit_state_recurrence_is_independent_of_music():
    r=request();r['windows']=[dict(id='hold',plateau='a',start_step=2000,stop_step=4000)]
    r['observation']['phase']=dict(train=[0.,.5],validations=[[.5,1.]],scales=[1.]*4,state_units=['m','m/s','m^3.s','m^3'],
        section_index=0,section_level=0.,section_method='linear',groups=[1,2],min_crossings=8)
    r['criteria']=[criterion('state_recurrence',maximum=dict(value=.1,unit='1')),
        dict(criterion(target=dict(value=70.,unit='Hz'),tolerance=dict(value=1.,unit='Hz')),id='frequency')]
    result=analyze(r,signal('sine'),True)
    assert result['criteria'][0]['status']=='fail' and result['criteria'][1]['status']=='pass'


def test_budget_refusal_before_arrays(monkeypatch):
    r=request();r['budgets']['new_seconds']=.1
    monkeypatch.setattr(np,'empty',lambda *a,**kw:pytest.fail('Allocation before quota validation'))
    with pytest.raises(ValueError,match='Cumulative'):core.Request.validate(r)


def test_inadequate_half_support_rejected():
    r=request();r['observation']['frame_steps']=550;r['observation']['hop_steps']=100
    with pytest.raises(ValueError,match='fixed support'):core.validate_request(r)


def tail_request(steps=13200):
    r=request(fs=12000,steps=steps)
    r['observation'].update(frame_steps=4000,hop_steps=2000)
    return r


@pytest.mark.parametrize('tail',[70,210,0])
def test_terminal_tail_frequency_and_hard_duration(tail):
    r=tail_request();fs=12000;n=13200;t=(np.arange(n)+.5)/fs
    y=np.sin(2*np.pi*70*t)
    y[12000:]=np.sin(2*np.pi*tail*t[12000:])
    r['criteria']=[criterion(target=dict(value=70.,unit='Hz'),tolerance=dict(value=2.,unit='cent')),
        dict(criterion('duration',minimum=dict(value=1.09,unit='s')),id='duration')]
    core.Request.validate(r)
    observed=core.observe_pressure(y,fs,r['observation'])
    result=core.analyze(r,times=np.arange(n+1)/fs,states=np.zeros((n+1,2)),
        midpoint_times=t,pressure=y,source=dict(kind='synthetic',description='Public terminal tone or silence'))
    assert result['criteria'][1]['status']=='pass'
    assert result['windows']['hold']['duration_s']==1.1
    assert result['windows']['hold']['activity_rms_pa']>r['observation']['min_rms_pa']
    if tail==70:
        assert observed['status']=='observed'
        assert abs(1200*math.log2(observed['frequency_hz']/70))<2
        assert result['hard_conforming'] is True
    else:
        assert observed['status']=='unresolved' and observed['frequency_hz'] is None
        assert result['criteria'][0]['status']=='unresolved'
        assert result['hard_conforming'] is None
    assert [f['start_sample'] for f in observed['frames']]==[0,2000,4000,6000,8000,9200]
    assert all(len(f['sensitivities'])==2 for f in observed['frames'])


@pytest.mark.parametrize('n,hop,starts',[
    (0,2000,[]),(3999,2000,[]),(4000,2000,[0]),(4001,2000,[0,1]),
    (6000,2000,[0,2000]),(6001,2000,[0,2000,2001]),
    (7199,2000,[0,2000,3199]),(7999,2000,[0,2000,3999]),
    (8000,4000,[0,4000]),(8001,4000,[0,4000,4001]),(10000,4000,[0,4000,6000])])
def test_terminal_frame_coverage_and_no_duplicates(monkeypatch,n,hop,starts):
    o=tail_request()['observation'];o['hop_steps']=hop
    y=np.arange(n,dtype=float);calls=[]
    def period(samples,*args):
        calls.append(samples.copy())
        return dict(frequency_hz=70.)
    monkeypatch.setattr(core,'_period_frame',period)
    result=core.observe_pressure(y,12000,o)
    assert [f['start_sample'] for f in result['frames']]==starts
    assert len(starts)==len(set(starts))
    assert len(calls)==3*len(starts)
    for i,start in enumerate(starts):
        frame=y[start:start+4000]
        assert np.array_equal(calls[3*i],frame)
        for half,actual in zip(np.array_split(frame,2),calls[3*i+1:3*i+3]):
            assert np.array_equal(actual,half)
        assert result['frames'][i]['stop_sample']==start+4000
    if n<4000:
        assert result['reason']=='insufficient_window'
    else:
        covered=np.zeros(n,dtype=bool)
        for start in starts:covered[start:start+4000]=True
        assert covered.all()
        assert starts[-1]+4000==n
        assert set(range(0,n-4000+1,hop))<=set(starts)


@pytest.mark.parametrize('n,frames',[(316,127),(317,128),(318,128),(319,129),(320,129)])
def test_terminal_frame_quota_request_and_api(monkeypatch,n,frames):
    r=request(fs=1000,steps=n)
    r['observation'].update(band_hz=[100.,200.],frame_steps=64,hop_steps=2)
    y=np.arange(n,dtype=np.int16);calls=[]
    if frames>128:
        def no(*args,**kw):pytest.fail('Analysis/allocation before frame quota')
        monkeypatch.setattr(core,'_period_frame',no)
        finite=np.isfinite
        monkeypatch.setattr(np,'isfinite',lambda value:no() if isinstance(value,np.ndarray) else finite(value))
        monkeypatch.setattr(np,'empty',no)
        with pytest.raises(ValueError,match='frame quota'):core.Request.validate(r)
        with pytest.raises(ValueError,match='frame quota'):core.observe_pressure(y,1000,r['observation'])
    else:
        core.Request.validate(r)
        def period(*args):
            calls.append(True)
            return dict(frequency_hz=150.)
        monkeypatch.setattr(core,'_period_frame',period)
        result=core.observe_pressure(y,1000,r['observation'])
        assert len(result['frames'])==frames and len(calls)==3*frames


@pytest.mark.parametrize('n,frames',[(28000,13),(28001,14)])
def test_terminal_operation_ceiling_request_and_api(monkeypatch,n,frames):
    r=tail_request(n);y=np.arange(n,dtype=np.int16);calls=[]
    if frames==14:
        def no(*args,**kw):pytest.fail('Analysis/allocation before operation quota')
        monkeypatch.setattr(core,'_period_frame',no)
        finite=np.isfinite
        monkeypatch.setattr(np,'isfinite',lambda value:no() if isinstance(value,np.ndarray) else finite(value))
        monkeypatch.setattr(np,'empty',no)
        with pytest.raises(ValueError,match='operation quota'):core.Request.validate(r)
        with pytest.raises(ValueError,match='operation quota'):core.observe_pressure(y,12000,r['observation'])
    else:
        core.Request.validate(r)
        def period(*args):
            calls.append(True)
            return dict(frequency_hz=70.)
        monkeypatch.setattr(core,'_period_frame',period)
        result=core.observe_pressure(y,12000,r['observation'])
        assert len(result['frames'])==13 and len(calls)==39


@pytest.mark.parametrize('lengths,budget,accepted',[
    ([12000],36000000,True),([12001],36000000,False),
    ([13200],43200000,True),([13200],43199999,False),
    ([12000,13200],79200000,True),([12000,13200],79199999,False),
    ([16000],100000000,True),([16000,16000],100000000,False)])
def test_terminal_aggregate_window_operation_budget(monkeypatch,lengths,budget,accepted):
    r=tail_request(max(lengths));r['budgets']['observation_ops']=budget
    r['windows']=[dict(id='w'+str(i),plateau='a',start_step=0,stop_step=n) for i,n in enumerate(lengths)]
    def no(*args,**kw):pytest.fail('Arrays/period analysis before request quota')
    monkeypatch.setattr(np,'empty',no);monkeypatch.setattr(core,'_period_frame',no)
    if accepted:core.Request.validate(r)
    else:
        with pytest.raises(ValueError,match='operation quota'):core.Request.validate(r)


def test_terminal_other_window_obligations_stay_independent():
    r=tail_request();n=13200;fs=12000;t=(np.arange(n)+.5)/fs
    y=np.sin(2*np.pi*70*t);y[12000:]=0
    r['windows'].append(dict(id='earlier',plateau='a',start_step=0,stop_step=12000))
    music=criterion(target=dict(value=70.,unit='Hz'),tolerance=dict(value=2.,unit='cent'))
    r['criteria']=[music,dict(music,id='earlier_music',windows=['earlier']),
        dict(music,id='both',windows=['hold','earlier'])]
    result=core.analyze(r,times=np.arange(n+1)/fs,states=np.zeros((n+1,2)),
        midpoint_times=t,pressure=y,source=dict(kind='synthetic',description='Independent window obligations'))
    assert [c['status'] for c in result['criteria']]==['unresolved','pass','unresolved']
    assert result['hard_conforming'] is None
