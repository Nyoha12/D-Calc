"""Independent SI algebra, actual-map and refined-continuous R33 witnesses.

Synthetic parameters, not measurements. No research-directory runtime dependency.
The continuous contact reference is refined RK4, not an exact event solution.
"""
from dataclasses import replace
import math

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear.lip_ports import flows, validate_port_model
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters, LipModelV2
from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling, contact_forces
from didgeridoo_optimizer.nonlinear.onset_stability import equilibria
from didgeridoo_optimizer.acoustics.air import AirProperties

M=1e-4; WL=2*np.pi*80; K=M*WL**2; R=.4*M*WL; AREA=3e-6
WA=2*np.pi*70; GAMMA=WA/8; ACOEF=1e7*GAMMA; RHO=1.204
XT=1e-6-.0008
P=DimensionedLipParameters(mass_kg=M, resonance_hz=80., effective_area_m2=AREA,
    damping_ratio=.2, rest_opening_m=.0008, lip_width_m=.012, flow_coefficient=.72,
    pressure_force_sign=-1., min_opening_m=1e-6, contact_stiffness_n_per_m=1e4,
    contact_damping_n_s_per_m=.04)
PH={'jet-only':3996.671891607, 'conjugate':4003.211086430}
FH={'jet-only':64.8712462469, 'conjugate':64.8612883691}


def modal_port(fs=12000, R0=0.):
    return PassiveResonator([ACOEF],[GAMMA],[WA],R0=R0,sample_rate_hz=fs,
        dc_origin=dict(kind='explicit',description='Synthetic SI modal oracle'))


def parameters(pressure, sign=-1.):
    return replace(P,mouth_pressure_kpa=pressure/1000,pressure_force_sign=sign)


def coupling(pressure=1500., fs=12000, mode='conjugate', **kwargs):
    return SimultaneousCoupling(modal_port(fs),params=parameters(pressure),rho=RHO,
                                v2_port_model=mode,**kwargs)


@pytest.mark.parametrize('sign',[-1.,1.])
def test_power_volume_and_wrong_sign(sign):
    # General two-area algebra is not a new simulated valve parameterization.
    for au,ad in ((sign*AREA,-sign*AREA),(-2e-6,3e-6)):
        pu,p,v,jet,dt=1700.,-120.,.13,2e-4,.004
        d=flows(jet,v,au,ad,'conjugate');uu=d['upstream_flow_m3_s'];ud=d['downstream_flow_m3_s']
        assert pu*uu-p*ud==pytest.approx((pu-p)*jet+(au*pu+ad*p)*v,abs=1e-16)
        assert (uu-ud)*dt==pytest.approx((au+ad)*v*dt,abs=1e-22)
    pu,p,v,jet=1500.,270.,.2,2e-4
    wrong=(pu-p)*(jet+AREA*v)-(pu-p)*jet-(-AREA*pu+AREA*p)*v
    assert wrong==pytest.approx(.001476,abs=1e-16)
    assert abs(wrong)>1e-4


@pytest.mark.parametrize('value',[True,None,{},'lambda1',1.,''])
def test_only_two_explicit_port_choices(value):
    with pytest.raises(ValueError):validate_port_model(value)


@pytest.mark.parametrize('sign',[-1.,1.])
@pytest.mark.parametrize('pressure',[0.,1500.,7000.,11000.])
@pytest.mark.parametrize('R0',[0.,1000.])
def test_native_equilibria_and_all_allowed_signs(sign,pressure,R0):
    model=modal_port(R0=R0);params=parameters(pressure,sign)
    eqs=equilibria(params,RHO,pressure,closure='fir_dc',dc=R0)
    assert eqs['status']=='equilibrium_solved'
    for eq in eqs['branches']:
        z=np.r_[eq['x_m'],0.,eq['flow_m3_s']/WA**2,0.]
        c=SimultaneousCoupling(model,params=params,rho=RHO,v2_port_model='conjugate',initial_state=z)
        d=c.step();assert d['ok'],d
        np.testing.assert_allclose(c.state,z,atol=3e-14,rtol=2e-13)
        assert d['pressure_pa']==pytest.approx(eq['downstream_pa'],abs=1e-8)
        assert abs(LipModelV2(params).derivatives(0,z[:2],None,eq['downstream_pa'])[1])<3e-9


def literal_contact_loss(x0,x1,dt):
    # Independent two-point quadrature split geometrically at contact.
    cuts=[0.,1.]
    if x0!=x1 and 0<(XT-x0)/(x1-x0)<1:cuts.insert(1,(XT-x0)/(x1-x0))
    fd=0.;v=(x1-x0)/dt
    for lo,hi in zip(cuts,cuts[1:]):
        for t in ((lo+hi)/2-(hi-lo)/math.sqrt(12),(lo+hi)/2+(hi-lo)/math.sqrt(12)):
            if x0+t*(x1-x0)<XT:fd+=(hi-lo)/2*.04*max(-v,0.)
    return -fd*v


@pytest.mark.parametrize('case',['free','enter','leave','closed','negative_delta','unforced'])
def test_literal_unweighted_storage_and_native_one_way_jet(case):
    pu=1500.;z=np.array([0.,.15,2e-10,-2e-8])
    if case=='enter':pu=7000.;z[:2]=[XT+1e-6,-.1]
    if case=='leave':z[:2]=[XT-1e-6,.1]
    if case=='closed':z[:2]=[-.00082,.01]
    if case=='negative_delta':
        tau=1/24000;z[:2]=[0.,.05]
        z[2]=-1800*(1+tau*GAMMA+tau*tau*WA*WA)/(ACOEF*tau*WA*WA);z[3]=0
    if case=='unforced':pu=0.;z[:2]=[XT-1e-5,-.1]
    c=coupling(pu,initial_state=z);d=c.step();assert d['ok'],d
    end=c.state;mid=(z+end)/2;vm=mid[1];p=d['pressure_pa']
    jet=.72*.012*max(.0008+mid[0],0.)*math.sqrt(2*max(pu-p,0.)/RHO)
    ud=jet-AREA*vm;source=pu*ud;jetloss=(pu-p)*jet
    liploss=R*vm*vm;closs=literal_contact_loss(z[0],end[0],c.dt)
    rloss=ACOEF*GAMMA*mid[3]**2
    energy=lambda a:.5*M*a[1]**2+.5*K*a[0]**2+5000*max(XT-a[0],0.)**2+.5*ACOEF*(a[3]**2+WA**2*a[2]**2)
    defect=energy(end)-energy(z)-c.dt*(source-jetloss-liploss-closs-rloss)
    scale=max(energy(z),energy(end),c.dt*abs(source),1e-30)
    assert abs(defect)/scale<3e-11
    assert min(jetloss,liploss,closs,rloss)>=0
    assert jet==pytest.approx(d['jet_m3_s'],rel=3e-12,abs=1e-17)
    assert ud==pytest.approx(d['flow_m3_s'],rel=3e-12,abs=1e-17)
    assert p==pytest.approx(ACOEF*mid[3],abs=1e-8)
    assert d['source_work_j']==pytest.approx(c.dt*source,abs=1e-17)
    assert d['jet_loss_j']==pytest.approx(c.dt*jetloss,abs=1e-17)
    assert d['total_energy_residual']['normalized']<3e-11
    if case in ('closed','negative_delta'):
        assert d['jet_m3_s']==0 and d['downstream_flow_m3_s']<0
    if case=='negative_delta':assert d['signed_pressure_difference_pa']<0


def test_zero_pressure_relaxes_stored_energy_without_clipping():
    c=coupling(0.,initial_state=[XT-1e-5,-.1,2e-10,-1e-7])
    out=c.simulate(.01);assert out['ok'],out['reason']
    energy=np.array(out['lip_energy_j'])+np.array(out['resonator_energy_j'])
    assert np.all(np.diff(energy)<=1e-16) and 0<energy[-1]<energy[0]
    c.initialize(np.zeros(4));assert c.step()['ok'];np.testing.assert_array_equal(c.state,np.zeros(4))


@pytest.mark.parametrize('sign',[-1.,1.])
@pytest.mark.parametrize('R0',[0.,1000.])
def test_pressure_slope_bound_and_strict_residual_monotonicity(sign,R0):
    c=SimultaneousCoupling(modal_port(R0=R0),params=parameters(1500,sign),rho=RHO,v2_port_model='conjugate')
    for x0 in (-.001,-.0008,-.0002,.0001):
        for ph in (-1000.,1400.,1600.):
            feedback=c._feedback(x0,ph);last=None
            for vm in np.linspace(-30,30,41):
                p,jet,delta,h=c._port(vm,x0,ph);B=c.Pu-ph+c.D*c.Ad*vm
                if h>0 and B>0:
                    t=math.sqrt(delta);dp=c.D*(c.k*c.tau*t-c.Ad)/(1+c.D*c.k*h/(2*t))
                else:dp=-c.D*c.Ad
                eps=1e-5
                if abs(h)>2*c.tau*eps and abs(B)>2*abs(c.D*c.Ad*eps):
                    numeric=(c._port(vm+eps,x0,ph)[0]-c._port(vm-eps,x0,ph)[0])/(2*eps)
                    assert numeric==pytest.approx(dp,rel=2e-6,abs=2e-7)
                if c.Ad>0:assert c.tau*c.Ad*dp<=feedback+1e-14
                else:assert dp>=0 and feedback==0
                fc,fd=contact_forces(x0,x0+c.dt*vm,c.dt,threshold=XT,stiffness=1e4,damping=.04)
                residual=c.M*vm-c.tau*(fc+fd)-c.tau*c.Ad*p
                if last is not None and c.M>feedback:assert residual>last
                last=residual


def quartic(p,mode):
    # Direct elimination, independent of the matrix and implemented port solver.
    b=.72*.012*np.sqrt(2*p/RHO);cc=b*(.0008-AREA*p/K)/(2*p);g=GAMMA+cc*ACOEF
    extra=AREA**2*ACOEF if mode=='conjugate' else 0.
    return np.array([M,M*g+R,M*WA**2+R*g+K+extra,R*WA**2+K*g-AREA*ACOEF*b,K*WA**2])


def continuous_matrix_port(p,mode):
    b=.72*.012*np.sqrt(2*p/RHO);cc=b*(.0008-AREA*p/K)/(2*p)
    return np.array([[0.,1,0,0],[-K/M,-R/M,0,AREA*ACOEF/M],
                     [0,0,0,1],[b,-AREA if mode=='conjugate' else 0.,-WA**2,-GAMMA-ACOEF*cc]])


@pytest.mark.parametrize('mode',['jet-only','conjugate'])
def test_independent_hurwitz_quartic_and_matrix_crossing(mode):
    def hurwitz(p):
        a4,a3,a2,a1,a0=quartic(p,mode)
        return (a3*a2*a1-a4*a1*a1-a3*a3*a0)/(a3*a3*a0)
    lo,hi=3500.,4500.;assert hurwitz(lo)>0>hurwitz(hi)
    for _ in range(50):
        mid=(lo+hi)/2
        if hurwitz(mid)>0:lo=mid
        else:hi=mid
    p=(lo+hi)/2;coef=quartic(p,mode)
    assert p==pytest.approx(PH[mode],abs=1e-6)
    assert math.sqrt(coef[3]/coef[1])/(2*np.pi)==pytest.approx(FH[mode],abs=1e-8)
    for delta in (-10.,0.,10.):
        eig=np.linalg.eigvals(continuous_matrix_port(p+delta,mode));roots=np.roots(quartic(p+delta,mode))
        assert max(min(abs(z-roots)) for z in eig)<1e-8
        if delta:assert (max(eig.real)>0)==(delta>0)
        else:assert abs(max(eig.real))<1e-9


@pytest.mark.parametrize('mode',['jet-only','conjugate'])
@pytest.mark.parametrize('fs',[4000,8000,12000])
@pytest.mark.parametrize('offset',[-10.,0.,10.])
def test_actual_step_jacobian_against_independent_cayley(mode,fs,offset):
    pressure=PH[mode]+offset;x=-AREA*pressure/K
    jet=.72*.012*(.0008+x)*np.sqrt(2*pressure/RHO)
    z=np.array([x,0.,jet/WA**2,0.]);sc=np.array([.0008,.0008*WL,.0003/WA**2,.0003/WA])
    c=coupling(pressure,fs,mode,initial_state=z)
    A=continuous_matrix_port(pressure,mode);As=A*sc[None,:]/sc[:,None];eye=np.eye(4)
    expected=np.linalg.solve(eye-As/(2*fs),eye+As/(2*fs));matrices=[]
    for eps in (1e-2,1e-3,1e-4):
        jac=np.zeros((4,4))
        for j in range(4):
            dz=np.zeros(4);dz[j]=eps*sc[j]
            c.initialize(z+dz);d=c.step();assert d['ok'],d;plus=c.state
            c.initialize(z-dz);d=c.step();assert d['ok'],d;minus=c.state
            jac[:,j]=(plus-minus)/(2*eps*sc)
        matrices.append(jac)
    errors=[np.max(abs(j-expected)) for j in matrices]
    assert min(errors)<2e-9
    eig=np.linalg.eigvals(matrices[int(np.argmin(errors))]);growth=max(np.log(abs(eig)))*fs
    if offset:assert (growth>0)==(offset>0)
    else:
        assert abs(growth)<2e-5
        freq=abs(np.angle(max(eig,key=abs)))*fs/(2*np.pi)
        assert freq==pytest.approx(fs/np.pi*np.arctan(np.pi*FH[mode]/fs),abs=2e-6)


def continuous_reference(pressure,mode,z0,fs):
    # Literal continuous SI ODE, no native force, port, recurrence or RHS helper.
    def rhs(z):
        x,v,q,vr=z;p=ACOEF*vr
        force=-AREA*(pressure-p)-K*x-R*v
        if x<XT:force+=1e4*(XT-x)+.04*max(-v,0.)
        jet=.72*.012*max(.0008+x,0.)*math.sqrt(2*max(pressure-p,0.)/RHO)
        return np.array([v,force/M,vr,jet-(AREA*v if mode=='conjugate' else 0.)-GAMMA*vr-WA**2*q])
    n=round(.02*fs);dt=1/fs;out=np.empty((n+1,4));out[0]=z0
    for i in range(n):
        z=out[i];k1=rhs(z);k2=rhs(z+dt*k1/2);k3=rhs(z+dt*k2/2);k4=rhs(z+dt*k3)
        out[i+1]=z+dt*(k1+2*k2+2*k3+k4)/6
    return out


@pytest.mark.parametrize('mode',['jet-only','conjugate'])
@pytest.mark.parametrize('contact',[False,True])
def test_refined_continuous_free_and_contact_convergence(mode,contact):
    pu=7000. if contact else 1500.
    if contact:z=np.array([.02*.0008,.001,0.,0.]);sc=np.array([.0008,.4,.0003/WA**2,.0003/WA])
    else:
        x=-AREA*pu/K;jet=.72*.012*(.0008+x)*math.sqrt(2*pu/RHO)
        z=np.array([x+1e-6,0.,jet/WA**2,0.]);sc=np.array([.0008,.0008*WL,.0003/WA**2,.0003/WA])
    coarse_fs,fine_fs=(768000,1536000) if contact else (96000,192000)
    coarse=continuous_reference(pu,mode,z,coarse_fs);fine=continuous_reference(pu,mode,z,fine_fs)
    refinement=np.sqrt(np.mean(((coarse-fine[::2])/sc)**2))
    if not contact:assert np.max(abs((coarse-fine[::2])/sc))<1e-9
    errors=[]
    for fs in (4000,8000,12000):
        result=coupling(pu,fs,mode,initial_state=z).simulate(.02);assert result['ok'],result['reason']
        errors.append(np.sqrt(np.mean(((np.array(result['states'])-fine[::(fine_fs//fs)])/sc)**2)))
        if contact:
            transitions={d['contact_transition'] for d in result['steps']}
            assert {'free->contact','contact->free'}<=transitions
            assert min(result['opening_m'])<0 and result['contact_fraction']>0
    assert errors[2]<errors[1]<errors[0]
    assert min(errors)>20*refinement
    if not contact:
        for i,ratio in enumerate((2.,1.5)):
            assert 1.8<math.log(errors[i]/errors[i+1])/math.log(ratio)<2.2
