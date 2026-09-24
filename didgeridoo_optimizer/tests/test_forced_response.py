"""Independent response/power oracles and contracts; no SciPy or network."""
from dataclasses import replace
import json
from pathlib import Path
import warnings

import numpy as np
import pytest

from didgeridoo_optimizer.acoustics import forced_response as fr
from didgeridoo_optimizer.acoustics import transfer_matrix as tm
from didgeridoo_optimizer.acoustics.losses import LegacyBetaLossModel
from didgeridoo_optimizer.acoustics.thermoviscous import CK_DRY_20C as AIR, ZwikkerKostenLossModel
from didgeridoo_optimizer.acoustics.radiation import radiation_proxy_metrics
from didgeridoo_optimizer.materials.database import MaterialDatabase
from tools.forced_response_compare import builtin_design, synthetic_material

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = json.loads((Path(__file__).parent/'fixtures/forced_response_reference.json').read_text())


def cvalue(item):
    return complex(item['real'], item['imag'])


def evaluate(case='cylinder', model=None, f=(40.,70.,160.,400.,1000.), h=1., material=None):
    mat = material or synthetic_material()
    design = builtin_design(case)
    mesh = fr.prepare_mesh(design, h, len(f))
    tr = fr.loaded_transfer(f, mesh, {mat.id:mat}, AIR.as_air_properties(),
                            exit_radius_m=design.segments[-1].d_out_cm/200, loss_model=model)
    return tr, mesh


@pytest.mark.parametrize('bad', [0., [], [[1]], [1,True], [1,1], [2,1], [0,1], [1,np.nan], [np.inf], [1+0j], ['1']])
def test_frequency_contract(bad):
    with pytest.raises(ValueError):
        fr.transfer_from_slices(bad, [], 1., zref=1.)


@pytest.mark.parametrize('bad', [True, 0, -1, np.nan, np.inf, 1j, [1]])
def test_geometry_radius_reference_contract(bad):
    tr, mesh = evaluate(f=[70.])
    with pytest.raises(ValueError):
        fr.loaded_transfer([70.], mesh, {}, exit_radius_m=bad)
    with pytest.raises(ValueError):
        fr.prepare_mesh(builtin_design('cylinder'), bad, 1)
    with pytest.raises(ValueError):
        fr.transfer_from_slices([70.], [], 1., zref=bad)


@pytest.mark.parametrize('bad', [True, [1,True], [1], [[1,2]], np.inf, 1+np.inf*1j, '1'])
def test_source_shape_and_finitude(bad):
    tr, _ = evaluate(f=[40.,70.])
    with pytest.raises(ValueError):
        fr.apply_source(tr, 'pressure', bad)
    with pytest.raises(ValueError):
        fr.transfer_from_slices([40.,70.], [], bad, zref=1.)


def test_passive_load_wave_and_budget_guards():
    for load in (-1., [1,-1]):
        with pytest.raises(ValueError, match='passive'):
            fr.transfer_from_slices([1.,2.], [], load, zref=1.)
    for k, zc in ((1+1j,1), (1,-1), (np.nan,1)):
        with pytest.raises(ValueError):
            fr.transfer_from_slices([1.], [(1,k,zc)], 1., zref=1.)
    with pytest.raises(ValueError, match='Budget'):
        fr.prepare_mesh(builtin_design('body_bell'), .01, 20000)
    with pytest.raises(ValueError):
        fr.prepare_mesh(fr.prepare_mesh(builtin_design('cylinder'), 1, 2), 1, 2)
    with pytest.raises(ValueError, match='Discretize'):
        fr.loaded_transfer([1.], builtin_design('expansion'), {}, exit_radius_m=.015)


@pytest.mark.parametrize('loss', ['lossless','legacy','zk'])
@pytest.mark.parametrize('load', [0., 3e5, 2e5+4e4j])
def test_analytic_cylinder_and_subdivision(loss, load):
    frequency=np.array([10.,69.5,70.,140.,400.,1000.])
    z0=AIR.rho*AIR.c/(np.pi*.015**2)
    if loss=='lossless':
        k=2*np.pi*frequency/AIR.c; zc=np.full(len(frequency),z0)
    else:
        model=LegacyBetaLossModel() if loss=='legacy' else ZwikkerKostenLossModel(AIR)
        pair=model.evaluate(2*np.pi*frequency,.03,synthetic_material(),z0,AIR.as_air_properties())
        k,zc=pair.k_complex,pair.zc_complex
    co,si=np.cos(k*1.21),np.sin(k*1.21)
    p,u=co*load+1j*zc*si,1j*si*load/zc+co
    expected=dict(Zin=p/u,Yin=u/p,Hu=1/u,Yt=1/p,Zt=load/u,Hp=load/p)
    for count,reference in ((1,1e5),(121,1e7)):
        tr=fr.transfer_from_slices(frequency, ((1.21/count,k,zc) for _ in range(count)),load,zref=reference)
        for key,value in expected.items():
            np.testing.assert_allclose(tr['transfers'][key].values(),value,rtol=1e-10,atol=1e-14)


@pytest.mark.parametrize('model', [LegacyBetaLossModel(),ZwikkerKostenLossModel(AIR)], ids=['legacy','zk'])
@pytest.mark.parametrize('database', [False,True])
def test_zin_parity_no_default_mutation_and_direct_abcd(model,database):
    mat=synthetic_material()
    design=builtin_design('body_bell')
    if database:
        materials=MaterialDatabase.from_yaml(ROOT/'project_specs/materials_base_v1.yaml')
        design=design.copy(segments=[replace(s,material_id='pvc_pressure') for s in design.segments])
    else:
        materials={mat.id:mat}
    f=np.array([40.,67.,67.65,67.73,70.,130.,400.,1000.])
    mesh=fr.prepare_mesh(design,.5,len(f)); before=mesh.as_dict()
    legacy=tm.input_impedance(f,mesh,materials,AIR.as_air_properties(),exit_radius_m=.06)
    tr=fr.loaded_transfer(f,mesh,materials,AIR.as_air_properties(),exit_radius_m=.06,loss_model=model)
    expected=tm.input_impedance(f,mesh,materials,AIR.as_air_properties(),exit_radius_m=.06,loss_model=model)
    error=np.linalg.norm(tr['transfers']['Zin'].values()-expected)/np.linalg.norm(expected)
    print('Zin parity',model.name,database,error)
    np.testing.assert_allclose(tr['transfers']['Zin'].values(),expected,rtol=1e-10,atol=1e-7)
    np.testing.assert_array_equal(legacy,tm.input_impedance(f,mesh,materials,AIR.as_air_properties(),exit_radius_m=.06))
    assert mesh.as_dict()==before and tr['exit_radius_m']==.06
    # Independent moderate ABCD product; production scaled backend is not reused.
    a,d=np.ones(len(f),complex),np.ones(len(f),complex)
    b,c=np.zeros(len(f),complex),np.zeros(len(f),complex)
    for s in mesh.segments:
        diameter=s.average_diameter_cm/100
        material=materials.get(s.material_id)
        coeff=model.evaluate(2*np.pi*f,diameter,material,AIR.rho*AIR.c/(np.pi*(diameter/2)**2),AIR.as_air_properties())
        co=np.cos(coeff.k_complex*s.length_cm/100); si=np.sin(coeff.k_complex*s.length_cm/100)
        bi=1j*coeff.zc_complex*si; ci=1j*si/coeff.zc_complex
        a,b,c,d=a*co+b*ci,a*bi+b*co,c*co+d*ci,c*bi+d*co
    load=tr['load']; p=a*load+b; u=c*load+d
    for key,value in dict(Zin=p/u,Hu=1/u,Yt=1/p,Zt=load/u,Hp=load/p).items():
        np.testing.assert_allclose(tr['transfers'][key].values(),value,rtol=1e-10,atol=1e-13)


def test_extreme_attenuation_and_log_domain_source_recovery():
    tr=fr.transfer_from_slices([1.],[(1.,2-1000j,3e5)],3e5,zref=1e5)
    np.testing.assert_allclose(tr['transfers']['Zin'].values(),3e5,rtol=1e-12)
    hu=tr['transfers']['Hu'].payload()
    assert hu['log_abs'][0]==pytest.approx(-1000,abs=1e-12)
    assert hu['phase_rad'][0]==pytest.approx(-2,abs=1e-12)
    assert hu['value']==[None] and hu['status']==['underflow']
    with np.errstate(over='ignore', invalid='ignore'):
        assert not np.isfinite(np.cos(2-1000j))
    recover=fr.transfer_from_slices([1.],[(1.,2-800j,3e5)],3e5,zref=1e5)
    assert recover['transfers']['Hu'].payload()['status']==['underflow']
    state=fr.apply_source(recover,'volume_flow',np.exp(700))
    np.testing.assert_allclose(state['ports']['U2'].values(),np.exp(-100-2j),rtol=1e-12)
    # Separate Yin remains useful even when the magnitude of Zin is unrepresentable.
    large=fr.transfer_from_slices([1.],[(1.,1.2,1.7e308)],1.,zref=1e308)
    assert large['transfers']['Zin'].payload()['status']==['overflow']
    assert fr.apply_source(large,'pressure',1.)['ports']['U1'].payload()['value'][0] is not None
    finite=fr.transfer_from_slices([1.],[],1.7e308+1.7e308j,zref=1e308)
    assert finite['transfers']['Zin'].payload()['status']==['ok']
    weighted=fr.transfer_from_slices([1.],[(1.,2-800j,1e100)],1e100,zref=1e100)
    assert weighted['transfers']['Hu'].payload()['value']==[None]
    np.testing.assert_allclose(weighted['transfers']['Zt'].values(),np.exp(-800+np.log(1e100)-2j),rtol=1e-12)


def test_zero_source_zero_load_and_component_cancellation():
    tr=fr.transfer_from_slices([1.,2.],[(1.,[.3,.7],3e5)],0.,zref=3e5)
    for source in ('pressure','volume_flow'):
        state=fr.apply_source(tr,source,0.)
        for curve in state['ports'].values():
            np.testing.assert_array_equal(curve.values(),0.)
        for name in ('Pin','Pload','Pdiss'):
            assert state['powers'][name]['value']==[0.,0.]
        assert state['powers']['eta']['value']==[None,None]
    state=fr.apply_source(tr,'volume_flow',1e-6)
    np.testing.assert_array_equal(state['ports']['p2'].values(),0.)
    assert state['powers']['Pload']['status']==['analytic_zero']*2
    nearpole=fr.transfer_from_slices([1.],[(1.,np.pi/2,3e5)],0.,zref=3e5)
    assert nearpole['transfers']['Hu'].payload()['value']==[None]
    assert nearpole['transfers']['Yt'].payload()['value'][0] is not None
    nearzero=fr.transfer_from_slices([1.],[(1.,np.pi,3e5)],0.,zref=3e5)
    assert nearzero['transfers']['Yt'].payload()['value']==[None]
    assert nearzero['transfers']['Hu'].payload()['value'][0] is not None
    identity=fr.transfer_from_slices([1.],[],0.,zref=1.)
    zero_power=fr.apply_source(identity,'volume_flow',1j)['powers']
    assert zero_power['Pin']['value']==[0.] and zero_power['Pin']['status']==['analytic_zero']


@pytest.mark.parametrize('model', [LegacyBetaLossModel(),ZwikkerKostenLossModel(AIR)])
def test_ports_scaling_phase_eta_and_power_quadrature(model):
    f=np.array([40.,70.,400.]); mat=synthetic_material()
    z0=AIR.rho*AIR.c/(np.pi*.015**2)
    pair=model.evaluate(2*np.pi*f,.03,mat,z0,AIR.as_air_properties())
    k,zc=pair.k_complex,pair.zc_complex
    slices=[(.04,k,zc),(.06,k,zc)]
    tr=fr.transfer_from_slices(f,reversed(slices),2e5+3e4j,zref=z0)
    flow=fr.apply_source(tr,'volume_flow',1e-6)
    p=flow['ports']; p1,u1,p2,u2=[p[key].values() for key in ('p1','U1','p2','U2')]
    np.testing.assert_allclose(p2,tr['load']*u2,rtol=1e-12)
    pressure=fr.apply_source(tr,'pressure',p1)
    for key in p:
        np.testing.assert_allclose(pressure['ports'][key].values(),p[key].values(),rtol=1e-12)
    for source in ('pressure','volume_flow'):
        a=fr.apply_source(tr,source,1.)
        doubled=fr.apply_source(tr,source,2.)
        rotated=fr.apply_source(tr,source,np.exp(.7j))
        for key in ('Pin','Pload','Pdiss'):
            np.testing.assert_allclose(doubled['powers'][key]['value'],4*np.array(a['powers'][key]['value']),rtol=1e-11)
            np.testing.assert_allclose(rotated['powers'][key]['value'],a['powers'][key]['value'],rtol=1e-11)
        np.testing.assert_allclose(a['powers']['eta']['value'],flow['powers']['eta']['value'],rtol=1e-11)
    # Independently reconstruct internal states with analytic matrices and integrate.
    integrated=np.zeros(len(f)); pout,uout=p2.copy(),u2.copy()
    for length,k,zc in reversed(slices):
        x=np.linspace(0,length,2049)[:,None]
        co,si=np.cos(k*x),np.sin(k*x)
        px=co*pout+1j*zc*si*uout; ux=1j*si/zc*pout+co*uout
        density=.5*((1j*k*zc).real*abs(ux)**2+(1j*k/zc).real*abs(px)**2)
        integrated+=np.trapezoid(density,x[:,0],axis=0)
        pout,uout=px[-1],ux[-1]
    np.testing.assert_allclose(flow['powers']['Pdiss']['value'],integrated,rtol=2e-7)
    assert np.all(np.array(flow['powers']['Pdiss']['value'])>0)


def test_lossless_balance_signed_roundoff_not_clamped():
    tr=fr.transfer_from_slices([1.,2.],[(1.,[.3,2.],3e5)],2e5+1e4j,zref=1e5)
    result=fr.apply_source(tr,'volume_flow',1e-6)['powers']
    np.testing.assert_allclose(result['Pin']['value'],result['Pload']['value'],rtol=1e-12)
    for residue,tolerance in zip(result['Pdiss']['value'],result['Pdiss']['roundoff_tolerance_w']):
        assert abs(residue)<=tolerance
    assert result['Pdiss']['status']==['roundoff_limited']*2


def test_power_resolution_uses_propagated_component_bounds():
    # Both normalized components are resolved, but the tiny resistive part of
    # an almost reactive input is below their propagated absolute uncertainty.
    tr=fr.transfer_from_slices([1.],[(1.,np.pi+1e-12,3e5)],1e-9,zref=3e5)
    assert tr['transfers']['Zin'].payload()['value'][0] is not None
    state=fr.apply_source(tr,'volume_flow',1.)
    assert state['powers']['Pin']['status']==['roundoff_limited']
    assert state['powers']['eta']['value']==[None]
    assert state['powers']['Pin']['roundoff_tolerance_w'][0]>abs(state['powers']['Pin']['value'][0])


@pytest.mark.parametrize('amplitude',[1e200,1e-200])
def test_efficiency_survives_unrepresentable_power_projection(amplitude):
    tr=fr.transfer_from_slices([1.],[(1.,2-.3j,3e5)],3e5,zref=3e5)
    state=fr.apply_source(tr,'volume_flow',amplitude)
    assert state['powers']['Pin']['value']==[None]
    assert state['powers']['Pin']['positive_log_resolved']==[True]
    np.testing.assert_allclose(state['powers']['eta']['value'],np.exp(-.6),rtol=1e-12)


@pytest.mark.parametrize('kind', ['volume_flow', 'pressure'])
def test_nonfinite_power_log_is_unavailable_not_analytic_zero(kind):
    # Deliberately astronomical numerical input, not a physical tube claim.
    tr = fr.transfer_from_slices([1.], [(1., 2-9e307j, 3e5)], 3e5, zref=3e5)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', RuntimeWarning)
        state = fr.apply_source(tr, kind, 1.)
    for name in ('Pload', 'eta', 'Pdiss'):
        power = state['powers'][name]
        assert power['value'] == [None], (name, power)
        assert power['status'] == ['unavailable']
        assert power['reason'][0]
    assert state['powers']['Pload']['log_abs'] == [None]
    assert state['powers']['eta']['log_abs'] == [None]
    assert state['powers']['Pin']['status'] == ['ok']
    assert state['powers']['Pin']['positive_log_resolved'] == [True]
    hu = tr['transfers']['Hu'].payload()
    assert hu['status'] == ['underflow'] and hu['log_abs'] == [-9e307]
    assert hu['phase_rad'][0] == pytest.approx(-2.)
    assert not caught, [str(w.message) for w in caught]


@pytest.mark.parametrize('kind', ['volume_flow', 'pressure'])
def test_power_log_finite_underflow_recovery_and_true_zeros(kind):
    tr = fr.transfer_from_slices([1.], [(1., 2-800j, 3e5)], 3e5, zref=3e5)
    with np.errstate(over='raise', invalid='raise'):
        tiny = fr.apply_source(tr, kind, 1.)
        recovered = fr.apply_source(tr, kind, np.exp(700))
        zero = fr.apply_source(tr, kind, 0.)
    for name in ('Pload', 'eta'):
        power = tiny['powers'][name]
        assert power['value'] == [None] and power['status'] == ['underflow']
        assert np.isfinite(power['log_abs'][0]) and power['reason'][0]
    assert tiny['powers']['eta']['log_abs'][0] == pytest.approx(-1600.)
    coefficient = 3e5 if kind == 'volume_flow' else 1/3e5
    expected = .5*coefficient*np.exp(-200.)
    np.testing.assert_allclose(recovered['powers']['Pload']['value'], expected, rtol=1e-12, atol=0)
    assert recovered['powers']['Pload']['status'] == ['ok']
    assert recovered['powers']['eta']['log_abs'][0] == pytest.approx(-1600.)
    for name in ('Pin', 'Pload', 'Pdiss'):
        assert zero['powers'][name]['value'] == [0.]
        assert zero['powers'][name]['status'] == ['analytic_zero']
    assert zero['powers']['eta']['value'] == [None]
    # A nonzero imaginary load also establishes zero resistive load power.
    lossless_load = fr.transfer_from_slices([1.], [(1., 2-.3j, 3e5)], 1j*3e5, zref=3e5)
    power = fr.apply_source(lossless_load, kind, 1.)['powers']
    assert power['Pload']['value'] == [0.] and power['Pload']['status'] == ['analytic_zero']
    assert power['eta']['value'] == [0.] and power['eta']['status'] == ['analytic_zero']


@pytest.mark.parametrize('log', [-np.inf, np.inf, np.nan])
def test_nonfinite_real_payload_needs_explicit_zero_sign(log):
    power = fr._real_payload([log], [1.], [''])
    assert power['value'] == [None] and power['status'] == ['unavailable']
    assert power['log_abs'] == [None] and power['reason'][0]


def test_independent_cone_ode_convergence():
    rows=REFERENCE['cone_reference']['curve']; f=[r['frequency_hz'] for r in rows]
    errors={key:[] for key in ('Zin','Hu','Yt')}
    for h in (1.,.5,.25):
        tr,_=evaluate('body_bell',ZwikkerKostenLossModel(AIR),f,h)
        for key,refkey in (('Zin','zin'),('Hu','huu'),('Yt','hup')):
            ref=np.array([cvalue(r[refkey]) for r in rows])
            errors[key].append(np.linalg.norm(tr['transfers'][key].values()-ref)/np.linalg.norm(ref))
    print('ODE convergence',errors)
    for key,limit in (('Zin',2e-4),('Hu',2e-4),('Yt',6e-4)):
        assert errors[key][-1]<limit
        assert errors[key][0]/errors[key][1]>3 and errors[key][1]/errors[key][2]>3


def test_same_outlet_proxy_different_transfer_and_source_order():
    cases={name:evaluate(name,ZwikkerKostenLossModel(AIR),[70.],.5)[0]
           for name in ('cylinder','expansion','constriction')}
    for case in cases.values():
        np.testing.assert_array_equal(case['load'],cases['cylinder']['load'])
        assert radiation_proxy_metrics([70.],case['load'])==radiation_proxy_metrics([70.],cases['cylinder']['load'])
    magnitudes=[abs(case['transfers']['Hu'].values()[0]) for case in cases.values()]
    assert len(set(magnitudes))==3
    powers={kind:{name:fr.apply_source(case,kind,amp)['powers']['Pload']['value'][0] for name,case in cases.items()}
            for kind,amp in (('volume_flow',1e-6),('pressure',1.))}
    assert powers['volume_flow']['cylinder']>powers['volume_flow']['expansion']
    assert powers['pressure']['cylinder']<powers['pressure']['expansion']
    print('same outlet',magnitudes,powers)
