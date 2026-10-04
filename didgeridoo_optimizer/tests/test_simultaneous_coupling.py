"""Literal contact integrals, actual-map Cayley and continuous event oracles."""
from dataclasses import replace
import math

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling, contact_forces
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters, LipModelV2
from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator
from didgeridoo_optimizer.nonlinear.onset_stability import equilibria
from didgeridoo_optimizer.tests.test_passive_resonator import modal, DC

P = DimensionedLipParameters()
RHO = 1.204
PH = 3996.6718916070563
XT = 1e-6-.0008


def resistor(fs=12000, R0=0.):
    return PassiveResonator([], [], [], R0=R0, sample_rate_hz=fs, dc_origin=DC)


def literal_contact_integral(x0, x1, dt):
    cuts = [0., 1.]
    if x1 != x0 and 0 < (XT-x0)/(x1-x0) < 1:
        cuts.insert(1, (XT-x0)/(x1-x0))
    fc = fd = 0.
    for lo, hi in zip(cuts, cuts[1:]):
        for t in ((lo+hi)/2-(hi-lo)/math.sqrt(12), (lo+hi)/2+(hi-lo)/math.sqrt(12)):
            h = .0008+x0+t*(x1-x0)
            if h < 1e-6:
                fc += (hi-lo)/2*1e4*(1e-6-h)
                fd += (hi-lo)/2*.04*max(-(x1-x0)/dt, 0.)
    return fc, fd


@pytest.mark.parametrize('d0,d1',[(2e-5,1e-5),(1e-5,-2e-5),(-2e-5,1e-5),
    (-2e-5,-1e-5),(-1e-5,-2e-5),(0,-1e-5),(-1e-5,0),(0,0),(-1e-5,-1e-5)])
def test_potential_literal_integrals_and_dissipative_work(d0,d1):
    x0,x1=XT+d0,XT+d1;dt=1/12000
    fc,fd=contact_forces(x0,x1,dt,threshold=XT,stiffness=1e4,damping=.04)
    np.testing.assert_allclose([fc,fd],literal_contact_integral(x0,x1,dt),rtol=1e-12,atol=2e-15)
    phi=lambda x:5000*max(XT-x,0)**2
    assert abs(phi(x1)-phi(x0)+fc*(x1-x0))<2e-20
    assert fd*(x1-x0)/dt<=0


@pytest.mark.parametrize('x,v,pressure,transition',[(0,.001,1500,'free->free'),
    (XT+1e-6,-.1,7000,'free->contact'),(XT-1e-6,.1,1500,'contact->free'),
    (XT-1e-5,-.01,11000,'contact->contact')])
def test_actual_transitions_and_literal_energy(x,v,pressure,transition):
    c=SimultaneousCoupling(resistor(),params=replace(P,mouth_pressure_kpa=pressure/1000),rho=RHO,initial_state=[x,v])
    d=c.step(); assert d['ok'],d
    assert d['contact_transition']==transition
    xn,vn=c.state; vm=(vn+v)/2
    E=lambda x,v: .5*1e-4*v*v+.5*1e-4*(2*np.pi*80)**2*x*x+5000*max(XT-x,0)**2
    fd=literal_contact_integral(x,xn,c.dt)[1]
    work=c.dt*(-3e-6*pressure*vm-.4*1e-4*2*np.pi*80*vm*vm+fd*vm)
    assert abs(E(xn,vn)-E(x,v)-work)<1e-18
    assert max(r['normalized'] for r in d['residuals'].values())<2e-11


@pytest.mark.parametrize('pressure',[1500.,7000.,11000.])
@pytest.mark.parametrize('R0',[0.,1000.])
@pytest.mark.parametrize('sign',[-1.,1.])
def test_native_law_equilibria_all_branches(pressure,R0,sign):
    params=replace(P,mouth_pressure_kpa=pressure/1000,pressure_force_sign=sign)
    model=resistor(R0=R0)
    eq=equilibria(params,RHO,pressure,closure='fir_dc',dc=R0)
    assert eq['status']=='equilibrium_solved'
    for row in eq['branches']:
        c=SimultaneousCoupling(model,params=params,rho=RHO,initial_state=[row['x_m'],0.])
        d=c.step(); assert d['ok'],d
        np.testing.assert_allclose(c.state,[row['x_m'],0],atol=3e-14,rtol=1e-13)
        assert d['pressure_pa']==pytest.approx(row['downstream_pa'],abs=1e-9)
        assert abs(LipModelV2(params).derivatives(0,c.state,params,d['pressure_pa'])[1])<2e-9


@pytest.mark.parametrize('edge',[XT,-.0008])
def test_boundary_and_nextafter_are_labelled_without_snapping(edge):
    for x in (np.nextafter(edge,-np.inf),edge,np.nextafter(edge,np.inf)):
        c=SimultaneousCoupling(resistor(),params=P,rho=RHO,initial_state=[x,0.])
        d=c.step();assert d['ok'],d
        name='contact' if edge==XT else 'flow_closure'
        assert name+'_segment_boundary_or_roundoff' in d['non_regular_reasons']
        assert d['event_localization'].startswith('not_performed')
    # A recomposed h can lie above hmin: labels compare x directly to xt.
    assert .0008+XT != 1e-6


def test_closure_reopening_delta_zero_negative_and_contact_zero_velocity():
    for x,v in [(-.000799,-.2),(-.000802,.3)]:
        c=SimultaneousCoupling(resistor(),params=P,rho=RHO,initial_state=[x,v])
        d=c.step();assert d['ok'],d
        assert 'flow_closure_segment_boundary_or_roundoff' in d['non_regular_reasons']
    for ph in (1500.,1600.):
        c=SimultaneousCoupling(modal(),params=P,rho=RHO)
        # Prescribe history pressure with q, independently of downstream endpoint.
        q=-ph*(1+c.tau*(2*np.pi*70/8)+c.tau**2*(2*np.pi*70)**2)/(c.a[0]*c.tau*(2*np.pi*70)**2)
        c.initialize([0.,0.,q,0.])
        d=c.step();assert d['ok'],d
        assert d['flow_m3_s']<1e-18
        assert 'bernoulli_delta_nonpositive_or_roundoff' in d['non_regular_reasons']
    params=replace(P,mouth_pressure_kpa=7.)
    x=(-3e-6*7000+1e4*XT)/(1e-4*(2*np.pi*80)**2+1e4)
    c=SimultaneousCoupling(resistor(),params=params,rho=RHO,initial_state=[x,0.])
    d=c.step();assert d['ok'],d
    assert 'contact_velocity_zero_or_roundoff' in d['non_regular_reasons']


def test_independence_reset_strict_initialization_and_atomic_failures(monkeypatch):
    model=modal(); c=SimultaneousCoupling(model,params=P,rho=RHO)
    initial=c.snapshot();assert c.step()['ok']
    assert model.energy()==0
    copy_state=c.state;copy_state[:]=99
    assert c.state[0]!=99
    c.reset();assert c.snapshot()==initial
    for state in ([0,0],[True,0,0,0],[0,0,np.inf,0],[0,0,1j,0]):
        with pytest.raises(ValueError):c.initialize(state)
        assert c.snapshot()==initial
    assert c.step()['ok'];before=c.snapshot()
    def fail(self,u):
        self._q=np.ones_like(self._q);self.last_dissipation_w=999
        raise ValueError('deliberate native failure')
    monkeypatch.setattr(PassiveResonator,'step',fail)
    assert c.step()['status']=='non_resolu'
    assert c.snapshot()==before


def test_certificate_failure_iteration_limit_and_overflow_are_atomic():
    for model,params,kw,state in [
        (resistor(R0=1e12),replace(P,mouth_pressure_kpa=30),{},[0.,0.]),
        (modal(),P,dict(max_iterations=1),[0.,.001,0.,0.])]:
        c=SimultaneousCoupling(model,params=params,rho=RHO,initial_state=state,**kw)
        before=c.snapshot();d=c.step()
        assert not d['ok'] and d['status']=='non_resolu',d
        assert c.snapshot()==before
    # Sufficient certificate failure does not claim absence of solutions.
    c=SimultaneousCoupling(resistor(R0=1e12),params=replace(P,mouth_pressure_kpa=30),rho=RHO)
    assert 'certificate_not_established' in c.step()['reason']


def test_bounded_simulation_partial_and_zero_forcing():
    c=SimultaneousCoupling(resistor(),params=replace(P,mouth_pressure_kpa=0.),rho=RHO,initial_state=[0.,0.])
    d=c.simulate(.001);assert d['ok']
    assert not np.any(d['states']) and not np.any(d['flow_m3_s'])
    partial=c.simulate(.01,max_steps=2)
    assert not partial['ok'] and len(partial['steps'])==2 and len(partial['states'])==3
    assert partial['duration_s']==2/12000
    assert not c.simulate(.001,seconds=0)['ok']
    for duration in (True,np.nan,.201,0.,1e-20):
        with pytest.raises(ValueError):c.simulate(duration)


def continuous_matrix(pressure):
    w=2*np.pi*70;g=w/8;a=1e7*g;wl=2*np.pi*80
    h=.0008-3e-6*pressure/(1e-4*wl**2)
    b=.72*.012*np.sqrt(2*pressure/RHO);cc=b*h/(2*pressure)
    return np.array([[0,1,0,0],[-wl**2,-.4*wl,0,3e-6*a/1e-4],
                     [0,0,0,1],[b,0,-w*w,-g-a*cc]])


@pytest.mark.parametrize('fs',[4000,8000,12000])
@pytest.mark.parametrize('pressure',[1500.,PH-10,PH,PH+10])
def test_real_free_step_jacobian_cayley_and_literal_threshold(fs,pressure):
    c=SimultaneousCoupling(modal(fs),params=replace(P,mouth_pressure_kpa=pressure/1000),rho=RHO)
    x=-3e-6*pressure/(1e-4*(2*np.pi*80)**2)
    U=.72*.012*(.0008+x)*np.sqrt(2*pressure/RHO)
    z=np.array([x,0.,U/(2*np.pi*70)**2,0.])
    s=np.array([.0008,.0008*2*np.pi*80,.0003/(2*np.pi*70)**2,.0003/(2*np.pi*70)])
    A=continuous_matrix(pressure);As=A*s[None,:]/s[:,None];I=np.eye(4)
    expected=np.linalg.solve(I-As/(2*fs),I+As/(2*fs))
    matrices=[]
    for eps in (1e-2,1e-3,1e-4):
        J=np.zeros((4,4))
        for j in range(4):
            dz=np.zeros(4);dz[j]=eps*s[j]
            c.initialize(z+dz);assert c.step()['ok'];plus=c.state
            c.initialize(z-dz);assert c.step()['ok'];minus=c.state
            J[:,j]=(plus-minus)/(2*eps*s)
        matrices.append(J)
    errors=[np.max(abs(J-expected)) for J in matrices]
    assert min(errors)<2e-9
    J=matrices[int(np.argmin(errors))]
    growth=max(np.log(abs(np.linalg.eigvals(J))))*fs
    eig=np.linalg.eigvals(A);continuous=max(eig.real)
    if pressure==PH:
        assert abs(growth)<2e-5
        root=max(eig,key=lambda z:(z.real,z.imag))
        assert abs(continuous)<1e-9
        assert root.imag/(2*np.pi)==pytest.approx(64.87124624686057,abs=1e-9)
    if pressure==PH-10:
        assert continuous==pytest.approx(-.0596518928,abs=1e-9) and growth<0
    if pressure==PH+10:
        assert continuous==pytest.approx(.0595101696,abs=1e-9) and growth>0


def continuous_contact_oracle(times, pressure, initial, *, damping=.2, contact_damping=.04):
    """Independent exact affine 2x2 propagator + bracketed continuous events.

    Downstream pressure is fixed at zero. Branches change at x=xt and at v=0
    inside contact. Small analytic scan intervals locate event brackets, then
    bisection locates the event; no product integrator or product Fc/Fd used.
    """
    mass=1e-4;spring=mass*(2*np.pi*80)**2;r=2*damping*mass*2*np.pi*80
    x,v=map(float,initial);t=0.;out=[];events=[]
    def exact(x,v,dt,contact,closing):
        k=spring+(1e4 if contact else 0.)
        d=r+(contact_damping if contact and closing else 0.)
        eq=(-3e-6*pressure+(1e4*XT if contact else 0.))/k
        alpha=d/(2*mass);freq=np.sqrt(complex(k/mass-alpha**2))
        cs=np.cos(freq*dt);sn=np.sin(freq*dt)/freq
        decay=np.exp(-alpha*dt);z=x-eq
        return float((eq+decay*(z*cs+(v+alpha*z)*sn)).real),float((decay*(v*cs-(alpha*v+k/mass*z)*sn)).real)
    for target in times:
        while t < target-1e-16:
            contact=x<XT or (x==XT and v<0)
            accel=(-3e-6*pressure-spring*x+(1e4*(XT-x) if contact else 0.))/mass
            closing=v<0 or (v==0 and accel<0)
            dt=min(target-t,2e-6)
            xn,vn=exact(x,v,dt,contact,closing)
            candidates=[]
            if (x-XT)*(xn-XT)<0: candidates.append(('x',XT))
            if contact and v*vn<0: candidates.append(('v',0.))
            roots=[]
            for kind,edge in candidates:
                j=0 if kind=='x' else 1;lo=0.;hi=dt;f0=(x,v)[j]-edge
                for _ in range(45):
                    mid=(lo+hi)/2;fm=exact(x,v,mid,contact,closing)[j]-edge
                    if f0*fm>0:lo=mid
                    else:hi=mid
                roots.append(((lo+hi)/2,kind,edge))
            if roots:
                dt,kind,edge=min(roots);xn,vn=exact(x,v,dt,contact,closing)
                if kind=='x':xn=edge
                else:vn=edge
                events.append((t+dt,kind))
            x,v=xn,vn;t+=dt
        out.append([x,v])
    return np.array(out),events


def test_real_normal_start_contact_trajectory_converges_to_continuous_events():
    errors=[];fractions=[]
    for fs in (4000,8000,12000):
        c=SimultaneousCoupling(resistor(fs),params=replace(P,mouth_pressure_kpa=7.),rho=RHO)
        d=c.simulate(.02);assert d['ok'],d.get('reason')
        oracle,events=continuous_contact_oracle(d['time_s'],7000,d['states'][0])
        assert any(kind=='x' for _,kind in events) and any(kind=='v' for _,kind in events)
        transition={r['contact_transition'] for r in d['steps']}
        assert {'free->contact','contact->free','contact->contact'}<=transition
        assert min(d['opening_m'])<0<max(d['opening_m'])
        errors.append(np.sqrt(np.mean(((np.array(d['states'])-oracle)/[.0008,.4])**2)))
        fractions.append(d['contact_fraction'])
        assert max(r['residuals']['lip_energy']['normalized'] for r in d['steps'])<2e-11
    assert errors[2]<errors[1]<errors[0]
    assert errors[0]/errors[2]>2
    assert all(0<f<1 for f in fractions)


def test_contact_conservation_without_damping_and_dissipation_with_damping():
    for damping,cd in ((0.,0.),(.2,.04)):
        params=replace(P,mouth_pressure_kpa=0.,damping_ratio=damping,contact_damping_n_s_per_m=cd)
        c=SimultaneousCoupling(resistor(),params=params,rho=RHO,initial_state=[XT-1e-5,-.1])
        d=c.simulate(.003);assert d['ok'],d.get('reason')
        energy=np.asarray(d['lip_energy_j'])
        if damping==0:np.testing.assert_allclose(energy,energy[0],rtol=3e-12,atol=1e-20)
        else:assert np.max(np.diff(energy))<1e-18


def test_nonfinite_native_candidate_cannot_commit(monkeypatch):
    c=SimultaneousCoupling(modal(),params=P,rho=RHO)
    before=c.snapshot()
    original=PassiveResonator.step
    def nonfinite(self,u):
        p=original(self,u)
        self._q=np.full_like(self._q,np.inf)
        return p
    monkeypatch.setattr(PassiveResonator,'step',nonfinite)
    assert not c.step()['ok']
    assert c.snapshot()==before


def test_source_io_failure_keeps_exploitable_partial(monkeypatch):
    c=SimultaneousCoupling(modal(),params=P,rho=RHO)
    original=c._source.verify_file_unchanged
    calls=[]
    def check():
        calls.append(1)
        if len(calls)>2:raise FileNotFoundError('source unavailable')
        original()
    monkeypatch.setattr(c._source,'verify_file_unchanged',check)
    d=c.simulate(.001)
    assert not d['ok'] and len(d['steps'])==1 and len(d['states'])==2
    assert d['final']==c.snapshot()
    before=c.snapshot();assert not c.step()['ok'];assert c.snapshot()==before


def test_unrepresentable_active_bernoulli_is_refused_not_zero_flow():
    params=replace(P,flow_coefficient=200.,pressure_force_sign=1.)
    c=SimultaneousCoupling(resistor(R0=1e308),params=params,rho=RHO,initial_state=[0.,0.])
    before=c.snapshot();d=c.step()
    assert not d['ok'] and c.snapshot()==before


@pytest.mark.parametrize('rho',[1.204,2.])
@pytest.mark.parametrize('sign',[-1.,1.])
def test_free_reduction_uses_native_force_and_flow(rho,sign):
    params=replace(P,pressure_force_sign=sign)
    c=SimultaneousCoupling(modal(),params=params,rho=rho)
    old=c.state;d=c.step();assert d['ok'],d
    force=LipModelV2(params).pressure_force(params,d['pressure_pa'])
    mid=np.linalg.solve([[1,-c.dt/2],[c.dt/2*c.K,c.m+c.dt/2*c.r]],
        [old[0],c.m*old[1]+c.dt/2*force])
    np.testing.assert_allclose((c.state[:2]+old[:2])/2,mid,rtol=2e-12,atol=1e-16)
    assert d['flow_m3_s']==pytest.approx(c.lip.flow(mid,params,d['pressure_pa'],c.air),rel=2e-13,abs=1e-18)


def test_bracket_limit_and_final_step_wall_limit(monkeypatch):
    c=SimultaneousCoupling(resistor(),params=P,rho=RHO,initial_state=[-.01,0.],max_extensions=0)
    before=c.snapshot();d=c.step()
    assert not d['ok'] and 'bracket_budget' in d['reason'] and c.snapshot()==before
    from didgeridoo_optimizer.nonlinear import simultaneous_coupling as module
    c=SimultaneousCoupling(resistor(),params=P,rho=RHO)
    ticks=iter([0.,0.,2.])
    monkeypatch.setattr(module.time,'monotonic',lambda:next(ticks))
    d=c.simulate(1/12000,seconds=1.)
    assert not d['ok'] and len(d['steps'])==1 and d['duration_s']==1/12000


def test_prewarped_uses_its_native_frequency_and_source_state_is_not_shared():
    model=modal();p=model.parameters();p['domain']='discrete_prewarped'
    model=PassiveResonator.from_parameters(p);model.step(1e-5)
    old=model.state
    c=SimultaneousCoupling(model,params=P,rho=RHO)
    assert not np.any(c.state[2:]) and c.sample_rate_hz==12000
    d=c.simulate(.001);assert d['ok'] and len(d['steps'])==12
    for actual,expected in zip(model.state,old):np.testing.assert_array_equal(actual,expected)
    assert d['steps'][0]['delta_clipped_pa']>=0


@pytest.mark.parametrize('mode',['jet-only','conjugate'])
def test_ports_native_backend_called_once_and_independent(mode,monkeypatch):
    model=modal();model.step(1e-6);original=model.state;parameters_before=model.parameters()
    calls=[];native=PassiveResonator.step
    def counted(backend,flow):
        calls.append((flow,backend.sample_rate_hz))
        return native(backend,flow)
    monkeypatch.setattr(PassiveResonator,'step',counted)
    c=SimultaneousCoupling(model,params=P,rho=RHO,v2_port_model=mode)
    assert not np.any(c.state[2:]);before=c.snapshot();d=c.step();assert d['ok'],d
    assert calls==[(d['downstream_flow_m3_s'],12000)]
    assert d['flow_m3_s']==d['downstream_flow_m3_s']
    assert model.parameters()==parameters_before
    for actual,expected in zip(model.state,original):np.testing.assert_array_equal(actual,expected)
    state=c.state;state[:]=0;diag=c.diagnostics;diag['flow_m3_s']=999
    assert c.diagnostics['flow_m3_s']!=999
    c.reset();assert c.snapshot()==before
    if mode=='jet-only':
        assert d['jet_m3_s']==d['flow_m3_s']
        assert d['induced_downstream_m3_s']==0 and d['total_energy_residual'] is None
    else:assert d['total_energy_residual']['normalized']<2e-10


@pytest.mark.parametrize('kind',['certificate','bracket','iterations','overflow','zero_iterations'])
def test_conjugate_refusals_are_atomic_without_limit_relaxation(kind):
    model=modal();params=replace(P,mouth_pressure_kpa=30.);state=[0.,.001,0.,0.];kw={}
    if kind=='certificate':model=resistor(R0=1e12);state=[-.0008,0.]
    if kind=='bracket':model=resistor();state=[-.01,0.];kw['max_extensions']=0
    if kind=='iterations':kw['max_iterations']=1
    if kind=='overflow':
        model=resistor(R0=1e308);state=[0.,0.]
        params=replace(P,flow_coefficient=200.,pressure_force_sign=1.)
    if kind=='zero_iterations':
        with pytest.raises(ValueError):SimultaneousCoupling(model,params=params,rho=RHO,v2_port_model='conjugate',max_iterations=0)
        return
    c=SimultaneousCoupling(model,params=params,rho=RHO,v2_port_model='conjugate',initial_state=state,**kw)
    before=c.snapshot();d=c.step()
    assert not d['ok'] and d['status']=='non_resolu' and d['v2_port_model']=='conjugate'
    assert c.snapshot()==before
    if kind=='certificate':assert 'certificate_not_established' in d['reason']
    if kind=='bracket':assert 'bracket_budget' in d['reason']
    if kind=='iterations':assert 'iteration_budget' in d['reason']


@pytest.mark.parametrize('failure',[ValueError('native candidate'),MemoryError(),KeyboardInterrupt()])
def test_conjugate_candidate_interruptions_and_zero_step_serialization(monkeypatch,failure):
    import json
    c=SimultaneousCoupling(modal(),params=P,rho=RHO,v2_port_model='conjugate')
    assert c.step()['ok'];before=c.snapshot()
    def fail(backend,flow):
        backend._q=np.ones_like(backend._q);backend.last_dissipation_w=999
        raise failure
    monkeypatch.setattr(PassiveResonator,'step',fail)
    out=c.simulate(.001)
    assert not out['ok'] and out['initial']==out['final']==before
    assert out['states']==[before['state']] and out['duration_s']==0
    assert out['v2_port_model']=='conjugate' and out['jet_m3_s']==[]
    assert json.loads(json.dumps(out,allow_nan=False))==out


def test_conjugate_partial_time_indexing_and_zero_budget():
    c=SimultaneousCoupling(modal(),params=P,rho=RHO,v2_port_model='conjugate')
    zero=c.simulate(.001,max_steps=0)
    assert not zero['ok'] and zero['duration_s']==0 and zero['initial']==zero['final']
    out=c.simulate(.01,max_steps=2)
    assert not out['ok'] and len(out['states'])==len(out['time_s'])==3
    assert len(out['jet_m3_s'])==len(out['steps'])==len(out['midpoint_time_s'])==2
    for i,row in enumerate(out['steps']):
        assert row['time_s']==out['time_s'][i+1]
        assert row['midpoint_time_s']==pytest.approx((out['time_s'][i]+out['time_s'][i+1])/2)
    assert out['requested_duration_s']==.01 and out['duration_s']==2/12000


def test_jet_only_optional_overflow_cannot_add_a_historical_refusal():
    params=replace(P,mouth_pressure_kpa=1e297,effective_area_m2=1e-300)
    c=SimultaneousCoupling(resistor(),params=params,rho=RHO,initial_state=[0.,0.])
    d=c.step();assert d['ok'],d
    assert np.isfinite(d['flow_m3_s']) and d['flow_m3_s']>0
    assert d['source_work_j'] is None and d['jet_loss_j'] is None
    assert d['total_energy_residual'] is None
    vm=c.tau*c.Au*c.Pu/c.M
    np.testing.assert_allclose(c.state,[c.dt*vm,2*vm],rtol=2e-14,atol=1e-16)
