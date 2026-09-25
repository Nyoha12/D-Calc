"""Arithmetic references of rounded fits, not exact diffraction or measurements."""
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import pytest

from didgeridoo_optimizer.acoustics.air import AirProperties
from didgeridoo_optimizer.acoustics.radiation import radiation_impedance
from didgeridoo_optimizer.acoustics.radiation_models import (
    LegacyRadiationModel, SilvaRadiationModel, normalized_impedance_and_reflection, get_radiation_model)

REF = json.loads((Path(__file__).parent/'fixtures/radiation_reference.json').read_text())
AIR = AirProperties(1.204, 343.)


@pytest.mark.parametrize('row', REF['rows'])
def test_independent_80_decimal_components(row):
    z, r = normalized_impedance_and_reflection(row['ka'], 'silva_'+row['termination'])
    assert z.shape == r.shape == ()
    for actual, key in ((z, 'z_normalized'), (r, 'reflection')):
        np.testing.assert_allclose([actual.real, actual.imag], [row[key]['real'],row[key]['imag']], rtol=2e-12, atol=0)
    if row['ka'] == 0:
        assert z == 0 and r == -1


@pytest.mark.parametrize('variant,resistance,length', [('unflanged',.24964,.613),('flanged',.49987525,.8215)])
def test_limits_symmetry_passivity_poles_and_corrected_sign(variant, resistance, length):
    name = 'silva_'+variant
    x = np.array([1e-6, .01, .1, .5, 1., 2., 3., 100., 1e8])
    z, r = normalized_impedance_and_reflection(x, name)
    zn, rn = normalized_impedance_and_reflection(-x, name)
    np.testing.assert_array_equal(zn, z.conj()); np.testing.assert_array_equal(rn, r.conj())
    assert z.real.min() >= 0 and np.all(abs(r) <= 1+4*np.finfo(float).eps)
    assert z.real[0]/x[0]**2 == pytest.approx(resistance, rel=2e-12)
    assert z.imag[0]/x[0] == pytest.approx(length, rel=2e-12)
    # Relation is checked away from 1+R cancellation at tiny x.
    np.testing.assert_allclose(z[1:], (1+r[1:])/(1-r[1:]), rtol=2e-12, atol=0)
    assert z[-1] == pytest.approx(1, abs=1e-8) and abs(r[-1]) < 1e-8
    n1,d1,d2 = (REF['table'][variant][k] for k in ('n1','d1','d2'))
    assert d1*d1-n1*n1-2*d2 > 0
    for polynomial in ([d2,d1,1],[d2,d1+n1,2]):
        assert np.all(np.roots(polynomial).real < 0)
    # Guard against the uncorrected overall Eq20 sign after convention conversion.
    s=1j*x
    wrong=-((d1-n1)*s+d2*s*s)/(2+(d1+n1)*s+d2*s*s)
    np.testing.assert_allclose(wrong.real, -z.real, rtol=2e-12, atol=0)
    assert np.all(wrong.real < 0)


@pytest.mark.parametrize('bad', [True, 1j, '2', [1,True], [1,1j], [], [[1]], [np.nan], np.inf, None])
def test_strict_frequency_before_coercion(bad):
    model=SilvaRadiationModel()
    with pytest.raises(ValueError): model.evaluate(bad,.03,AIR)
    with pytest.raises(ValueError): normalized_impedance_and_reflection(bad,model.name)


@pytest.mark.parametrize('bad', [True, 1j, '2', [1], 0., -1., np.nan, np.inf])
def test_radius_and_air_guards(bad):
    model=SilvaRadiationModel()
    with pytest.raises(ValueError): model.evaluate(1.,bad,AIR)
    # AirProperties has a deliberately weaker historic constructor; no change to it.
    for field in ('rho','c'):
        air=object.__new__(AirProperties)
        for key,value in asdict(AIR).items(): object.__setattr__(air,key,value)
        object.__setattr__(air,field,bad)
        with pytest.raises(ValueError): model.evaluate(1.,.03,air)


def test_numerical_range_shapes_metadata_and_no_default_mutation():
    model=SilvaRadiationModel('silva_flanged')
    for omega in (0., 1., np.array([-2.,0.,3.])):
        result=model.evaluate(omega,.03,AIR)
        assert result.impedance.shape == np.shape(omega)
    z,r=normalized_impedance_and_reflection([1e100,1e200],model.name)
    assert np.all(np.isfinite(z)) and np.all(np.isfinite(r)) and np.all(z.imag>0)
    for args in ((1e-200,.03,AIR),(1e308,1e308,AIR),(1.,1e-300,AIR)):
        with pytest.raises(ValueError, match='range'): model.evaluate(*args)
    result=model.evaluate(np.array([0.,2.,2.01])*AIR.c/.03,.03,AIR)
    assert result.metadata['model_status']==['within_reference_band','within_reference_band','extrapolation']
    assert result.metadata['model_reason'][-1] and result.metadata['warnings']
    assert result.metadata['coefficients']==REF['table']['flanged']
    result.metadata['coefficients']['n1']=9
    assert model.describe()['coefficients']['n1']==.182
    old=radiation_impedance(np.array([0.,100.,1000.]),.03,AIR)
    np.testing.assert_array_equal(LegacyRadiationModel().evaluate([0.,100.,1000.],.03,AIR).impedance, old)
    assert get_radiation_model('legacy').name=='legacy'
    with pytest.raises(ValueError): get_radiation_model('unknown')
    with pytest.raises(ValueError): SilvaRadiationModel('legacy')


def test_legacy_identity_and_historical_effective_radius():
    with pytest.raises(TypeError): LegacyRadiationModel('silva_unflanged')
    with pytest.raises(TypeError): LegacyRadiationModel(name='silva_flanged')
    result=LegacyRadiationModel().evaluate([1.],1e-12,AIR)
    np.testing.assert_array_equal(result.impedance,radiation_impedance([1.],1e-12,AIR))
    assert result.metadata['radius_m']==1e-12
    assert result.metadata['normalization_radius_m']==1e-9
    assert result.metadata['ka_out']==[1e-12/AIR.c]
