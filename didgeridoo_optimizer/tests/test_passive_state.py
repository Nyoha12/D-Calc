"""Independent rational/energy oracles and native modal/coupling comparisons."""
import copy
import json

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear.passive_state import PassiveState, SCHEMA
from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator, digest
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters

ORIGIN = 'Synthetic declared numerical coefficients; not instrument data'


def relaxation(fs=12000, **kw):
    params = dict(J=[[0.]], g=[100.], L=[[np.sqrt(200.),0.],[400/np.sqrt(200.),np.sqrt(200.)]],
                  omega_ref=1., Zscale=1., sample_rate_hz=fs, coefficient_origin=ORIGIN)
    params.update(kw)
    return PassiveState(**params)


def modal_pair(fs=12000, a=2.e5, gamma=70., omega=420., R0=10.):
    old = PassiveResonator([a],[gamma],[omega],R0=R0,sample_rate_hz=fs,
        dc_origin=dict(kind='explicit',description='Synthetic modal resistance'))
    new = PassiveState([[0.,omega],[-omega,0.]],[0.,np.sqrt(a)],
        np.diag([0.,np.sqrt(gamma),np.sqrt(R0)]),omega_ref=1.,Zscale=1.,
        sample_rate_hz=fs,coefficient_origin=ORIGIN)
    return old,new


def test_negative_residue_continuous_and_exact_discrete_frequency():
    m=relaxation()
    np.testing.assert_allclose(m.A,[[-200.]])
    np.testing.assert_allclose(m.B,[-300.])
    np.testing.assert_allclose(m.C,[500.])
    assert m.D==pytest.approx(1000.)
    assert m.feedthrough_pa_s_m3==pytest.approx(1000.)
    assert m.dc_pa_s_m3==pytest.approx(250.)
    f=np.array([0.,1.,23.,500.,3000.,5999.])
    expected=1000-150000/(200+2j*np.pi*f)
    np.testing.assert_allclose(m.continuous_response(f),expected,rtol=3e-15,atol=1e-12)
    z=2j*12000*np.tan(np.pi*f/12000)
    np.testing.assert_allclose(m.discrete_response(f),1000-150000/(z+200),rtol=3e-15,atol=1e-12)
    assert np.min(expected.real)>=250
    assert m.metadata['passivity']=='structural_L_LT'
    assert m.metadata['asymptotic_stability']=='strict_numerical_check'
    assert not hasattr(m,'a') and not hasattr(m,'R0')


@pytest.mark.parametrize('fs',[1000,4000,12000])
@pytest.mark.parametrize('initial',[0.,.002,-.003])
def test_scalar_recurrence_forced_balance_and_wrong_endpoint_oracle(fs,initial):
    m=relaxation(fs);m.initialize([initial]);x=initial
    wrong_errors=[];work=loss=0.;e0=m.energy()
    for i in range(80):
        u=1e-3*np.cos(i*.37)
        xm=(x-150*u/fs)/(1+100/fs)
        xn=2*xm-x
        expected=500*xm+1000*u
        old=m.energy();ph,Dh=m.affine_port();p=m.step(u)
        assert p==pytest.approx(expected,rel=2e-12,abs=1e-14)
        assert p==pytest.approx(ph+Dh*u,rel=2e-12,abs=1e-14)
        assert m.state[0]==pytest.approx(xn,rel=2e-12,abs=1e-17)
        independent_loss=200*xm*xm+800*xm*u+1000*u*u
        assert m.last_dissipation_w==pytest.approx(independent_loss,rel=3e-13,abs=1e-20)
        defect=m.energy()-old-(p*u-independent_loss)/fs
        assert abs(defect)<2e-12*max(old,m.energy(),abs(p*u/fs),independent_loss/fs,1e-30)
        wrong_errors.append(abs(m.energy()-old-((500*xn+1000*u)*u-independent_loss)/fs))
        work+=p*u/fs;loss+=independent_loss/fs;x=xn
    assert m.energy()-e0==pytest.approx(work-loss,rel=2e-12,abs=1e-18)
    # A pressure evaluated at the end of the step visibly fails the SAME balance.
    assert max(wrong_errors)>1e-11


@pytest.mark.parametrize('fs',[1000,4000,12000])
@pytest.mark.parametrize('q,v',[(0.,0.),(2e-9,-1e-6)])
def test_native_modal_pressure_state_energy_loss(fs,q,v):
    old,new=modal_pair(fs)
    old._q=np.array([q]);old._v=np.array([v])
    a,omega=old.a[0],old.omega[0]
    new.initialize([np.sqrt(a)*omega*q,np.sqrt(a)*v])
    rng=np.random.default_rng(593)
    for u in rng.normal(0,1e-4,180):
        p=old.step(u)
        assert new.step(u)==pytest.approx(p,rel=3e-11,abs=3e-14)
        np.testing.assert_allclose(new.state,[np.sqrt(a)*omega*old.state[0][0],np.sqrt(a)*old.state[1][0]],rtol=2e-11,atol=2e-16)
        assert new.energy()==pytest.approx(old.energy(),rel=2e-12,abs=1e-25)
        assert new.last_dissipation_w==pytest.approx(old.last_dissipation_w,rel=3e-12,abs=1e-22)


def test_nonunit_scales_and_exact_discrete_transfer_from_recurrence():
    m=relaxation(4000,omega_ref=3.,Zscale=7.)
    f=np.array([0.,17.,200.,1000.]);s=2j*4000*np.tan(np.pi*f/4000)
    np.testing.assert_allclose(m.discrete_response(f),7*(1000-150000/(s/3+200)),rtol=3e-15)
    h=1/4000;M=1/(1+h*3*200/2);T=2*M-1;G=-h*3*300*M
    # z-transform from endpoint state recurrence and midpoint output, independently.
    z=np.exp(2j*np.pi*f/4000)
    Z=7*(1000+500*G/2+500*M*G/(z-T))
    np.testing.assert_allclose(m.discrete_response(f),Z,rtol=2e-13,atol=1e-10)
    m.initialize([.01]);e=m.energy();p=m.step(.002)
    assert m.energy()-e==pytest.approx((p*.002-m.last_dissipation_w)/4000,rel=2e-12)


def test_zero_state_resistance_and_full_state_budget():
    zero=PassiveState([],[],[[2.]],omega_ref=2.,Zscale=3.,sample_rate_hz=12000,coefficient_origin=ORIGIN)
    assert zero.state.shape==(0,) and zero.dc_pa_s_m3==12
    assert zero.step(-.1)==pytest.approx(-1.2)
    assert zero.energy()==0 and zero.last_dissipation_w==pytest.approx(.12)
    np.testing.assert_allclose(zero.discrete_response([0.,100.]),[12.,12.])
    n=128
    full=PassiveState(np.zeros((n,n)),np.ones(n),np.eye(n+1),omega_ref=1.,Zscale=1.,sample_rate_hz=12000,coefficient_origin=ORIGIN)
    assert len(full.state)==128
    full.step(1e-5)
    assert full.energy()>0
    for method in (full.initialize,full.continuous_response):
        with pytest.raises(ValueError): method([0.]*4097)


@pytest.mark.parametrize('kw',[dict(J=[[1.]]),dict(g=[True]),dict(L=[[1.],[1.,2.]]),
    dict(L=[[float('nan')],[1.]]),dict(J=[[0.,0.]]),dict(omega_ref=0),dict(Zscale=-1),
    dict(sample_rate_hz=True),dict(domain='unknown'),dict(provenance={'bad':float('inf')}),
    dict(L=[[0.],[1.]]),dict(g=[1.]*129),dict(L=[[0.]*130,[0.]*130]),
    dict(L=[[1e308],[1e308]]),dict(Zscale=1e308),dict(coefficient_origin='')])
def test_invalid_model_refused(kw):
    with pytest.raises((ValueError,OverflowError)): relaxation(**kw)


def test_bounds_before_array_coercion(monkeypatch):
    class Trap:
        def __array__(self,*args): raise AssertionError('must not coerce arbitrary input')
    with pytest.raises(ValueError): relaxation(g=Trap())
    with pytest.raises(ValueError): relaxation(L=Trap())
    with pytest.raises(ValueError): relaxation(g=[0.]*129)
    with pytest.raises(ValueError): relaxation(J=[[0.,1.],[-1.0000000000000002,0.]],g=[1,1],L=np.eye(3))


def test_copy_immutability_reset_initialization_and_step_atomicity():
    m=relaxation();m.initialize([.002]);m.step(.001)
    before=m.export_state();other=m.copy(with_state=True)
    assert other.export_state()==before
    other.step(-.001);assert m.export_state()==before
    assert m.copy().energy()==0
    state=m.state;state[:]=99
    for name in ('J','g','L','A','B','C'):
        arr=getattr(m,name);arr[:]=999
    assert m.export_state()==before
    for name in ('_A','sample_rate_hz','_fs','_sealed'):
        with pytest.raises(AttributeError): setattr(m,name,None)
    with pytest.raises(ValueError): m._A.setflags(write=True)
    for x in ([0.,0.],[True],[np.inf],[1e308]):
        with pytest.raises(ValueError):m.initialize(x)
        assert m.export_state()==before
    for u in (True,complex(1),np.nan,1e308):
        with pytest.raises(ValueError):m.step(u)
        assert m.export_state()==before
    m.reset();assert m.energy()==0 and m.last_dissipation_w==0


@pytest.mark.parametrize('mutation',['checksum','identity','shape','negative_loss','bool','extra'])
def test_export_import_state_atomic(mutation):
    a=relaxation();a.step(.01);data=a.export_state();b=relaxation();b.import_state(data)
    assert b.export_state()==data and b.step(.002)==a.step(.002)
    before=b.export_state();bad=copy.deepcopy(before);p=bad['payload']
    if mutation=='checksum':bad['sha256']='bad'
    if mutation=='identity':p['model_sha256']='bad'
    if mutation=='shape':p['x'].append(0.)
    if mutation=='negative_loss':p['last_dissipation_w']=-1.
    if mutation=='bool':p['x']=[True]
    if mutation=='extra':p['extra']=1
    if mutation!='checksum':bad['sha256']=digest(p)
    with pytest.raises(ValueError):b.import_state(bad)
    assert b.export_state()==before


def test_strict_model_files_and_changes_are_atomic(tmp_path):
    path=tmp_path/'m.json';m=relaxation();m.save(path);loaded=PassiveState.load(path)
    assert loaded.parameters()==m.parameters() and loaded.parameters()['schema']==SCHEMA
    with pytest.raises(FileExistsError):m.save(path)
    loaded.step(.01);before=loaded.state;loss=loaded.last_dissipation_w
    path.write_text(path.read_text()+' ')
    for action in (lambda:loaded.step(0.),lambda:loaded.initialize([0.]),loaded.reset,
                   lambda:loaded.continuous_response([1]),lambda:loaded.discrete_response([1])):
        with pytest.raises(ValueError,match='changed'):action()
        np.testing.assert_array_equal(loaded.state,before);assert loaded.last_dissipation_w==loss


@pytest.mark.parametrize('mutation',['duplicate','nan','overflow','schema','units','extra','checksum'])
def test_hostile_json_refused(tmp_path,mutation):
    p=relaxation().parameters()
    if mutation=='schema':p['schema']='dcalc.passive_resonator.v1'
    if mutation=='units':p['units']['state']='m'
    if mutation=='extra':p['code']='eval(1)'
    data=dict(parameters=p,sha256=digest(p))
    if mutation=='checksum':data['sha256']='bad'
    text=json.dumps(data)
    if mutation=='duplicate':text=text.replace('"Zscale": 1.0','"Zscale": 1.0, "Zscale": 1.0')
    if mutation=='nan':text=text.replace('"Zscale": 1.0','"Zscale": NaN')
    if mutation=='overflow':text=text.replace('"Zscale": 1.0','"Zscale": 1e400')
    path=tmp_path/'bad.json';path.write_text(text)
    with pytest.raises(ValueError):PassiveState.load(path)


@pytest.mark.parametrize('method',['continuous_response','discrete_response'])
@pytest.mark.parametrize('f',[[-1],[True],[np.nan],[1j]])
def test_frequency_domains(method,f):
    with pytest.raises(ValueError):getattr(relaxation(),method)(f)
    with pytest.raises(ValueError):relaxation().discrete_response([6000])


@pytest.mark.parametrize('port',['jet-only','conjugate'])
@pytest.mark.parametrize('fs',[4000,12000])
def test_true_coupling_modal_equivalence_and_native_step(port,fs,monkeypatch):
    old,new=modal_pair(fs,a=1e7*55.,gamma=55.,omega=440.,R0=100.)
    params=DimensionedLipParameters()
    q,v=2e-10,-2e-8
    a=SimultaneousCoupling(old,params=params,rho=1.204,v2_port_model=port,initial_state=[1e-5,.001,q,v])
    b=SimultaneousCoupling(new,params=params,rho=1.204,v2_port_model=port,
        initial_state=[1e-5,.001,np.sqrt(old.a[0])*old.omega[0]*q,np.sqrt(old.a[0])*v])
    calls=[];native=PassiveState.step
    def step(self,u):calls.append(u);return native(self,u)
    monkeypatch.setattr(PassiveState,'step',step)
    for _ in range(60):
        da,db=a.step(),b.step();assert da['ok'] and db['ok'],(da,db)
        for key in ('pressure_pa','flow_m3_s','resonator_energy_j','resonator_loss_j'):
            assert db[key]==pytest.approx(da[key],rel=3e-10,abs=2e-12)
        np.testing.assert_allclose(a.state[:2],b.state[:2],rtol=3e-11,atol=1e-16)
    assert len(calls)==60 and calls[-1]==db['flow_m3_s']
    assert new.energy()==0
    if port=='conjugate':assert db['total_energy_residual']['normalized']<2e-10


@pytest.mark.parametrize('port',['jet-only','conjugate'])
def test_relaxation_coupling_checkpoint_resume_and_atomic_failure(port,monkeypatch):
    params=DimensionedLipParameters()
    a=SimultaneousCoupling(relaxation(),params=params,rho=1.204,v2_port_model=port)
    out=a.simulate(.003);assert out['ok'],out['reason']
    assert len(a.state)==3 and len(out['states'][0])==3
    cp=json.loads(json.dumps(a.export_checkpoint()))
    assert cp['payload']['schema']=='dcalc.simultaneous.passive_state.checkpoint.v1'
    b=SimultaneousCoupling(relaxation(),params=params,rho=1.204,v2_port_model=port)
    b.import_checkpoint(cp);assert b.export_checkpoint()==cp
    for _ in range(10):assert a.step()==b.step()
    a.reset();b.reset();assert a.export_checkpoint()==b.export_checkpoint()
    assert a.step()['ok'];before=a.export_checkpoint()
    bad=copy.deepcopy(before);bad['payload']['snapshot']['state'].append(0);bad['sha256']=digest(bad['payload'])
    with pytest.raises(ValueError):a.import_checkpoint(bad)
    assert a.export_checkpoint()==before
    def fail(self,u):self._x=np.ones_like(self._x);raise ValueError('native refusal')
    monkeypatch.setattr(PassiveState,'step',fail)
    assert not a.step()['ok'] and a.export_checkpoint()==before
    for state in ([0,0],[0,0,np.inf],[0,0,1e308]):
        with pytest.raises(ValueError):a.initialize(state)
        assert a.export_checkpoint()==before


def test_legacy_zero_a_preserves_original_state_and_checkpoint():
    old,_=modal_pair(a=0.)
    c=SimultaneousCoupling(old,params=DimensionedLipParameters(),rho=1.204,initial_state=[0.,.001,2e-9,3e-8])
    assert c.step()['ok'] and len(c.state)==4
    assert c.export_checkpoint()['payload']['schema']=='dcalc.simultaneous.checkpoint.v1'
    assert c._backend.energy()==0 and not np.all(c.state[2:]==0)
    for model in (object(),np.ones(3)):
        with pytest.raises(ValueError,match='no FIR'):SimultaneousCoupling(model,params=DimensionedLipParameters(),rho=1.204)


def test_step_has_no_dense_solve_or_factorization(monkeypatch):
    m=relaxation();m.initialize([.001])
    def forbidden(*args,**kw):raise AssertionError('factorization inside time loop')
    for name in ('solve','inv','eigvals','cholesky','svd'):
        monkeypatch.setattr(np.linalg,name,forbidden)
    for u in (1e-5,-1e-5,0.):
        ph,D=m.affine_port();assert m.step(u)==pytest.approx(ph+D*u,rel=2e-13)


def test_general_three_state_certificate_independent_quadratic_balance():
    J=np.array([[0.,20.,-3.],[-20.,0.,5.],[3.,-5.,0.]])
    L=np.array([[2.,0.,0.,0.],[.3,3.,0.,0.],[-.5,.2,4.,0.],[.7,-.3,.1,5.]])
    g=np.array([2.,-1.,3.]);m=PassiveState(J,g,L,omega_ref=2.,Zscale=7.,sample_rate_hz=4000,coefficient_origin=ORIGIN)
    x=np.array([.001,-.002,.003]);m.initialize(x)
    W=L@L.T;A=J-W[:3,:3];B=g-W[:3,3];C=g+W[:3,3];h=1/4000
    for u in (1e-3,-2e-3,0.,1e-4)*10:
        mid=np.linalg.solve(np.eye(3)-h*A,x+h*B*u)
        nxt=2*mid-x;p=7*(C@mid+W[3,3]*u)
        old=7/2*np.dot(x,x)/2;energy=7/2*np.dot(nxt,nxt)/2
        loss=7*np.dot(np.r_[mid,u],W@np.r_[mid,u])
        assert energy-old==pytest.approx(h*(p*u-loss),rel=3e-12,abs=1e-20)
        assert m.step(u)==pytest.approx(p,rel=3e-13)
        np.testing.assert_allclose(m.state,nxt,rtol=2e-13,atol=1e-18)
        x=nxt


def test_undamped_passive_is_separately_refused_for_stability():
    with pytest.raises(ValueError,match='stability'):
        PassiveState([[0.,1.],[-1.,0.]],[0.,1.],np.zeros((3,1)),
            omega_ref=1.,Zscale=1.,sample_rate_hz=12000,coefficient_origin=ORIGIN)


def test_unrepresentable_underflow_refused_without_silent_zero():
    with pytest.raises(ValueError,match='Unrepresentable'):
        relaxation(L=[[1e-200],[1e-200]])
    m=relaxation();before=m.export_state()
    with pytest.raises(ValueError):m.step(1e-300)
    assert m.export_state()==before


# R57 correction regressions (original tests above preserved byte-for-byte).
@pytest.mark.parametrize('resistance',[0.,4.])
@pytest.mark.parametrize('flow',[0.,1e-5,-1e-5])
def test_correction_zero_state_strict_balance_reset(resistance,flow):
    with np.errstate(all='raise'):
        m=PassiveState([],[],[[np.sqrt(resistance)]],omega_ref=1.,Zscale=1.,
                       sample_rate_hz=12000,coefficient_origin=ORIGIN)
        m.initialize([],last_dissipation_w=2.)
        assert m.affine_port()==(0.,resistance)
        assert m.step(flow)==resistance*flow
        assert m.energy()==0. and m.state.shape==(0,)
        assert m.last_dissipation_w==pytest.approx(resistance*flow*flow)
        m.reset();assert m.last_dissipation_w==0.
        assert m.step(0.)==0.


@pytest.mark.parametrize('resistance',[0.,4.])
@pytest.mark.parametrize('port',['jet-only','conjugate'])
def test_correction_zero_state_true_coupling(resistance,port):
    model=PassiveState([],[],[[np.sqrt(resistance)]],omega_ref=1.,Zscale=1.,
                       sample_rate_hz=12000,coefficient_origin=ORIGIN)
    native=PassiveResonator([],[],[],R0=resistance,sample_rate_hz=12000,
                           dc_origin={'kind':'explicit','description':ORIGIN})
    a=SimultaneousCoupling(model,params=DimensionedLipParameters(),rho=1.204,v2_port_model=port)
    b=SimultaneousCoupling(native,params=DimensionedLipParameters(),rho=1.204,v2_port_model=port)
    with np.errstate(all='raise'):
        for _ in range(3):
            da,db=a.step(),b.step()
            assert da['ok'] and db['ok'],(da,db)
            assert da['pressure_pa']==db['pressure_pa']
            assert da['flow_m3_s']==db['flow_m3_s']
            np.testing.assert_array_equal(a.state,b.state)
        a.reset();assert a.state.shape==(2,)
        assert a.step()['ok']


def test_correction_zero_state_real_overflow_atomic():
    m=PassiveState([],[],[[2.]],omega_ref=1.,Zscale=1.,sample_rate_hz=12000,coefficient_origin=ORIGIN)
    m.step(.1);before=m.export_state()
    with np.errstate(all='raise'),pytest.raises(ValueError):m.step(1e308)
    assert m.export_state()==before
