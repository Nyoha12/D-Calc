"""Independent supplied references and physical/contract checks; no network."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from didgeridoo_optimizer.acoustics import transfer_matrix as tm
from didgeridoo_optimizer.acoustics.losses import LegacyBetaLossModel
from didgeridoo_optimizer.acoustics.radiation import radiation_impedance
from didgeridoo_optimizer.acoustics.thermoviscous import (
    CK_DRY_20C, CK_DRY_25C, ThermoviscousAir, ZwikkerKostenLossModel,
    bessel_factors, zk_coefficients,
)
from didgeridoo_optimizer.geometry.builders import DesignBuilder
from didgeridoo_optimizer.geometry.discretization import GeometryDiscretizer
from didgeridoo_optimizer.materials.database import MaterialDatabase
from didgeridoo_optimizer.materials.models import AcousticParameter

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = json.loads((Path(__file__).parent / 'fixtures/thermo_reference.json').read_text())


def cvalue(value):
    return complex(value['real'], value['imag'])


def design(bell=False):
    segments = [dict(kind='cylinder', length_cm=120 if bell else 121,
                     d_in_cm=3.8 if bell else 3, d_out_cm=3.8 if bell else 3,
                     material_id='pvc_pressure')]
    if bell:
        segments.append(dict(kind='flare_conical', length_cm=20, d_in_cm=3.8,
                             d_out_cm=12, material_id='pvc_pressure'))
    return DesignBuilder().build(dict(id='synthetic', segments=segments))


@pytest.fixture(scope='module')
def materials():
    return MaterialDatabase.from_yaml(ROOT / 'project_specs/materials_base_v1.yaml')


@pytest.mark.parametrize('row', REFERENCE['bessel_ratio_reference'])
def test_independent_bessel_reference(row):
    f, g = bessel_factors(row['stokes'])
    # Check real/imag separately too: tiny real(G) must not be lost.
    for actual, expected in ((f, cvalue(row['F'])), (g, cvalue(row['one_minus_F']))):
        np.testing.assert_allclose([actual.real, actual.imag], [expected.real, expected.imag], rtol=1e-10, atol=0)


@pytest.mark.parametrize('row', REFERENCE['k_zc_reference'])
def test_independent_pair_reference(row):
    result = zk_coefficients(2*np.pi*row['frequency_hz'], row['diameter_m'], CK_DRY_25C)
    np.testing.assert_allclose(result.k, cvalue(row['k_m_inv']), rtol=1e-10, atol=0)
    np.testing.assert_allclose(result.zc, cvalue(row['zc_pa_s_m3']), rtol=1e-10, atol=0)


@pytest.mark.parametrize('bad', [0, -1, True, np.bool_(False), complex(1, 0), np.nan, np.inf, [1, True], [1, np.nan], [[1]], [], '10', None])
def test_omega_rejected(bad):
    with pytest.raises(ValueError):
        zk_coefficients(bad, .03, CK_DRY_20C)


@pytest.mark.parametrize('bad', [0, -1, True, 1j, np.nan, np.inf, [.03], '0.03', None])
def test_diameter_rejected(bad):
    with pytest.raises(ValueError):
        zk_coefficients(100., bad, CK_DRY_20C)


@pytest.mark.parametrize('field', ['rho', 'c', 'mu', 'kappa', 'cp', 'gamma'])
@pytest.mark.parametrize('bad', [0, -1, True, 1j, np.nan, np.inf])
def test_air_rejected(field, bad):
    with pytest.raises(ValueError):
        replace(CK_DRY_20C, **{field: bad})


def test_air_explicit_immutable_and_shape():
    with pytest.raises(FrozenInstanceError):
        CK_DRY_20C.rho = 1
    with pytest.raises(ValueError):
        replace(CK_DRY_20C, gamma=1)
    for state, expected in ((CK_DRY_25C, REFERENCE['air_ck25']), (CK_DRY_20C, REFERENCE['cone_reference']['air'])):
        for key, value in expected.items():
            assert getattr(state, key) == pytest.approx(value, rel=2e-15)
    assert zk_coefficients(100., .03, CK_DRY_20C).k.shape == ()
    assert zk_coefficients([100.], .03, CK_DRY_20C).k.shape == (1,)
    assert zk_coefficients(np.array([100., 200.]), .03, CK_DRY_20C).k.shape == (2,)


def test_numerical_domain_and_budget_fail_explicitly():
    for st in (1e-6, 10001.):
        with pytest.raises(ValueError, match='interval'):
            bessel_factors(st)
    with pytest.raises(ArithmeticError, match='converge'):
        bessel_factors(1e4, max_depth=32)
    with pytest.raises(ValueError, match='max_depth'):
        bessel_factors(1, max_depth=True)


def test_temperature_humidity_and_nominal_validation(materials):
    for field, bad in [('temperature_c',True),('temperature_c',np.nan),('temperature_c',-273.15),
                       ('humidity_percent',True),('humidity_percent',-1),('humidity_percent',101),('humidity_percent',np.inf)]:
        with pytest.raises(ValueError):
            replace(CK_DRY_20C, **{field:bad})
    model = ZwikkerKostenLossModel(CK_DRY_20C)
    for bad in (True, np.nan, np.inf, 1j, [1,True], [[1]], -1):
        with pytest.raises(ValueError):
            model.evaluate([100.,200.],.03,materials.get('pvc_pressure'),bad,CK_DRY_20C.as_air_properties())


@pytest.mark.parametrize('state', [CK_DRY_20C, CK_DRY_25C])
@pytest.mark.parametrize('diameter', [.002, .014, .12])
def test_telegraphist_passivity_and_power_balance(state, diameter):
    w = 2*np.pi*np.array([.01, 10, 70, 1000, 5000])
    result = zk_coefficients(w, diameter, state)
    np.testing.assert_allclose(1j*result.k*result.zc, result.zprime, rtol=2e-14)
    np.testing.assert_allclose(1j*result.k/result.zc, result.yprime, rtol=2e-14)
    assert np.all(result.zprime.real > 0) and np.all(result.yprime.real > 0)
    assert np.all(result.k.real > 0) and np.all(result.k.imag < 0) and np.all(result.zc.real > 0)
    # Arbitrary passive output state, then reconstruct through a short segment.
    pout = state.rho*state.c/(np.pi*(diameter/2)**2) * np.ones(w.shape)
    uout = np.ones(w.shape, dtype=complex)
    a, b, c, d = tm.segment_matrix(result.zc, result.k, .01)
    pin, uin = a*pout+b*uout, c*pout+d*uout
    power_lost = .5*np.real(pin*np.conj(uin) - pout*np.conj(uout))
    assert np.all(power_lost > 0)
    # Independently integrate local dissipated power along the state solution.
    x = np.linspace(0, .01, 513)[:, None]
    co, si = np.cos(result.k*x), np.sin(result.k*x)
    px = co*pout + 1j*result.zc*si*uout
    ux = 1j*si/result.zc*pout + co*uout
    density = .5*(result.zprime.real*abs(ux)**2 + result.yprime.real*abs(px)**2)
    integrated = np.trapezoid(density, x[:, 0], axis=0)
    np.testing.assert_allclose(power_lost, integrated, rtol=2e-6)


def test_limits_poiseuille_isothermal_and_large_stokes():
    s = CK_DRY_20C
    radius = .015
    area = np.pi*radius**2
    w = (1e-4/radius)**2*s.mu/s.rho
    low = zk_coefficients(w, 2*radius, s)
    np.testing.assert_allclose(low.zprime, 8*s.mu/(np.pi*radius**4), rtol=1e-8)
    np.testing.assert_allclose(low.yprime/(1j*w*area), s.gamma/(s.rho*s.c**2), rtol=1e-8)
    # Small diffusion at St=8000; first-order expansion error is O(St^-2).
    w = (8000/radius)**2*s.mu/s.rho
    high = zk_coefficients(w, 2*radius, s)
    pr = s.mu*s.cp/s.kappa
    term = (1-1j)/np.sqrt(2)/8000
    np.testing.assert_allclose(high.k/(w/s.c), 1+term*(1+(s.gamma-1)/np.sqrt(pr)), rtol=5e-8)
    np.testing.assert_allclose(high.zc/(s.rho*s.c/area), 1+term*(1-(s.gamma-1)/np.sqrt(pr)), rtol=5e-8)
    assert abs(high.k/(w/s.c)-1) < 3e-4
    assert abs(high.zc/(s.rho*s.c/area)-1) < 3e-4


def test_adapter_air_nominal_material_independence(materials):
    s = CK_DRY_20C
    model = ZwikkerKostenLossModel(s)
    material = materials.get('pvc_pressure')
    z0 = s.rho*s.c/(np.pi*.015**2)
    baseline = model.evaluate([100., 200.], .03, material, z0, s.as_air_properties())
    wild = replace(material, beta=AcousticParameter(100,100,100,'inferred','low'),
                   wall_loss=AcousticParameter(20,20,20,'inferred','low'),
                   porosity_leak=AcousticParameter(30,30,30,'inferred','low'))
    with patch('didgeridoo_optimizer.acoustics.losses.attenuation_alpha', side_effect=AssertionError('legacy called')):
        other = model.evaluate([100., 200.], .03, wild, z0, s.as_air_properties())
    np.testing.assert_array_equal(baseline.k_complex, other.k_complex)
    np.testing.assert_array_equal(baseline.zc_complex, other.zc_complex)
    assert [c.name for c in other.components] == ['air_thermoviscous']
    assert other.warnings and other.provenance_status == 'inferred'
    for field in ('rho', 'c', 'temperature_c', 'humidity_percent'):
        with pytest.raises(ValueError, match=field):
            model.evaluate(100., .03, material, z0, replace(s.as_air_properties(), **{field:getattr(s, field)+.01}))
    with pytest.raises(ValueError, match='zc_nominal'):
        model.evaluate(100., .03, material, z0*1.001, s.as_air_properties())
    with pytest.raises(ValueError, match='explicit'):
        model.evaluate(100., .03, material, z0)
    zero = replace(material, beta=AcousticParameter(0,0,0,'inferred','low'), wall_loss=AcousticParameter(0,0,0,'inferred','low'), porosity_leak=AcousticParameter(0,0,0,'inferred','low'))
    assert LegacyBetaLossModel().evaluate(100., .03, zero, z0, s.as_air_properties()).alpha_total == 0
    assert model.evaluate(100., .03, zero, z0, s.as_air_properties()).alpha_total > 0


@pytest.mark.parametrize('use_database', [False, True])
def test_opt_in_default_exact_and_no_contamination(materials, use_database):
    db = materials if use_database else materials.materials
    physical = design(True)
    mesh = GeometryDiscretizer().discretize(physical, 1.)
    original = mesh.as_dict()
    f = np.array([40., 70., 400., 1000.])
    air = CK_DRY_20C.as_air_properties()
    default_object = tm.DEFAULT_LOSS_MODEL
    before = tm.input_impedance(f, mesh, db, air, exit_radius_m=.06)
    explicit = tm.input_impedance(f, mesh, db, air, exit_radius_m=.06, loss_model=LegacyBetaLossModel())
    zk = tm.input_impedance(f, mesh, db, air, exit_radius_m=.06, loss_model=ZwikkerKostenLossModel(CK_DRY_20C))
    after = tm.input_impedance(f, mesh, db, air, exit_radius_m=.06)
    np.testing.assert_array_equal(before, explicit)
    np.testing.assert_array_equal(before, after)
    assert not np.array_equal(before, zk)
    assert tm.DEFAULT_LOSS_MODEL is default_object and mesh.as_dict() == original


def test_loaded_cylinder_analytic_and_subdivision(materials):
    s = CK_DRY_20C
    f = np.array([40., 69.5, 160., 400., 1000.])
    r = zk_coefficients(2*np.pi*f, .03, s)
    load = radiation_impedance(2*np.pi*f, .015, s.as_air_properties())
    co, si = np.cos(r.k*1.21), np.sin(r.k*1.21)
    expected = (co*load + 1j*r.zc*si)/(1j*si/r.zc*load + co)
    for h in (121., 1., .5, .25):
        mesh = GeometryDiscretizer().discretize(design(), h)
        actual = tm.input_impedance(f, mesh, materials, s.as_air_properties(), exit_radius_m=.015, loss_model=ZwikkerKostenLossModel(s))
        np.testing.assert_allclose(actual, expected, rtol=3e-12, atol=1e-7)


def test_cone_spatial_convergence_independent_ode(materials):
    rows = REFERENCE['cone_reference']['curve']
    f = np.array([row['frequency_hz'] for row in rows])
    expected = np.array([cvalue(row['zin_pa_s_m3']) for row in rows])
    errors = []
    for h in (1., .5, .25):
        mesh = GeometryDiscretizer().discretize(design(True), h)
        actual = tm.input_impedance(f, mesh, materials, CK_DRY_20C.as_air_properties(), exit_radius_m=.06, loss_model=ZwikkerKostenLossModel(CK_DRY_20C))
        errors.append(np.linalg.norm(actual-expected)/np.linalg.norm(expected))
    # Midpoint approximation has second-order spatial error for this smooth cone.
    assert errors[1] < errors[0]/3 and errors[2] < errors[1]/3
    assert errors[-1] < 2e-4
    print('ODE relative L2 errors at h=1,.5,.25 cm:', errors)
