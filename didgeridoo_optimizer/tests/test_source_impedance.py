"""SOURCE-02: independent moderate ABCD and targeted normalized edge states."""
from copy import deepcopy
from decimal import Decimal, localcontext

import numpy as np
import pytest

from didgeridoo_optimizer.acoustics import forced_response as fr
from didgeridoo_optimizer.acoustics.source_impedance import apply_thevenin_source as apply


def identity(n=1, load=2., zref=1.):
    return fr.transfer_from_slices(np.arange(1., n+1), [], load, zref=zref)


def state(q=1., v=1., ep=1e-16, eu=1e-16, length=0., ref=1., load=1.):
    tr = identity(load=load)
    tr.update(normalized_pressure=np.array([q], complex), normalized_flow=np.array([v], complex),
              pressure_roundoff_bound=np.array([ep]), flow_roundoff_bound=np.array([eu]),
              log_scale=np.array([length]), zref=ref, segment_count=1)
    return tr


def values(response):
    return {k: x.values() for k, x in response['ports'].items()}


def test_independent_abcd_scalar_vector_and_power_balance():
    f = np.array([30., 70., 210.])
    sections = [(0.43, 2*np.pi*f/343-.014j, 4e5),
                (0.72, 2*np.pi*f/330-.028j, 1.7e5)]
    load = np.array([4e4+2e4j, 5e4+8e4j, 9e4+1e5j])
    # Direct moderate matrix product, independent of normalized recurrence.
    matrix = np.broadcast_to(np.eye(2, dtype=complex), (3, 2, 2)).copy()
    for length, k, zc in sections:
        co, si = np.cos(k*length), np.sin(k*length)
        section = np.empty_like(matrix)
        section[:, 0, 0] = section[:, 1, 1] = co
        section[:, 0, 1] = 1j*zc*si
        section[:, 1, 0] = 1j*si/zc
        matrix = matrix @ section
    x = matrix[:, 0, 0]*load+matrix[:, 0, 1]
    y = matrix[:, 1, 0]*load+matrix[:, 1, 1]
    for ref in (1e3, 2e5, 1e8):
        tr = fr.transfer_from_slices(f, reversed(sections), load, zref=ref)
        for ps, zs in [(2-3j, 7e4), (np.array([1+1j, 2-3j, 4j]), np.array([0, 5e4+8e4j, -2e5j]))]:
            response = apply(tr, ps, zs)
            u2 = ps/(x+zs*y)
            expected = dict(p1=x*u2, U1=y*u2, p2=load*u2, U2=u2)
            for name, observed in values(response).items():
                np.testing.assert_allclose(observed, expected[name], rtol=2e-11, atol=1e-20)
            pin = .5*np.real(expected['p1']*expected['U1'].conj())
            pload = .5*load.real*abs(u2)**2
            supply = .5*np.real(ps*expected['U1'].conj())
            internal = .5*np.real(zs)*abs(expected['U1'])**2
            for group, expected_powers in [('powers', dict(Pin=pin, Pload=pload, Pdiss=pin-pload, eta=pload/pin)),
                                           ('source_powers', dict(Psupply=supply, Pinternal=internal, eta_source=pload/supply))]:
                for key, expected_power in expected_powers.items():
                    np.testing.assert_allclose(response[group][key]['value'], expected_power, rtol=2e-11, atol=1e-20)
                    if key not in {'eta', 'eta_source'}:
                        error = abs(np.array(response[group][key]['value'])-expected_power)
                        assert np.all(error <= np.array(response[group][key]['roundoff_tolerance_w'])+1e-22)
            p = response['powers']; s = response['source_powers']
            residual = np.array(s['Psupply']['value'])-p['Pin']['value']-np.array(s['Pinternal']['value'])
            bound = sum(np.array(z['roundoff_tolerance_w']) for z in (s['Psupply'], p['Pin'], s['Pinternal']))
            assert np.all(abs(residual) <= bound)


@pytest.mark.parametrize('bad', [True, np.bool_(False), '1', [1], [[1, 2]], [1, True], np.inf, np.nan, [1, np.inf]])
def test_source_admission(bad):
    tr = identity(2)
    for ps, zs in ((bad, 1.), (1., bad)):
        with pytest.raises(ValueError):
            apply(tr, ps, zs)


@pytest.mark.parametrize('zs', [-1, [-1, 2], -1+5j])
def test_negative_resistance(zs):
    with pytest.raises(ValueError, match='Re'):
        apply(identity(2), 1, zs)


@pytest.mark.parametrize('key,bad', [('normalized_pressure', [1, 2]), ('normalized_flow', True),
    ('pressure_roundoff_bound', [[1]]), ('flow_roundoff_bound', [True]), ('log_scale', [1j]),
    ('propagation_invalid', [1]), ('propagation_invalid', []), ('zref', 0), ('zref', True),
    ('segment_count', True), ('segment_count', -1), ('load', 1.)])
def test_transfer_structure(key, bad):
    tr = identity(); tr[key] = bad
    with pytest.raises(ValueError):
        apply(tr, 1., 1.)


def test_missing_frame_is_not_inferred_from_ideal_transfer_reasons():
    tr = identity(); del tr['propagation_invalid']
    with pytest.raises(ValueError, match='propagation_invalid'):
        apply(tr, 1., 1.)
    with pytest.raises(ValueError):
        apply(None, 1., 1.)


def test_pressure_norton_amplitude_phase_and_eta():
    tr = fr.transfer_from_slices([30., 70.], [(0.5, [1-.02j, 2-.03j], 3e5)], 4e4+2e4j, zref=1e5)
    ideal = fr.apply_source(tr, 'pressure', 2-1j)
    zero = apply(tr, 2-1j, 0.)
    for name in ideal['ports']:
        np.testing.assert_allclose(zero['ports'][name].values(), ideal['ports'][name].values(), rtol=2e-12)
    us, zs = 1e-6-2e-6j, 1e17
    flow = fr.apply_source(tr, 'volume_flow', us)
    norton = apply(tr, zs*us, zs)
    for name in flow['ports']:
        np.testing.assert_allclose(norton['ports'][name].values(), flow['ports'][name].values(), rtol=1e-10)
    a = apply(tr, 1., 2e5+1e5j); factor = 3*np.exp(.7j)
    b = apply(tr, factor, 2e5+1e5j)
    for name in a['ports']:
        np.testing.assert_allclose(b['ports'][name].values(), factor*a['ports'][name].values(), rtol=2e-12)
    for group in ('powers', 'source_powers'):
        for name, curve in a[group].items():
            scale = 1. if name.startswith('eta') else abs(factor)**2
            np.testing.assert_allclose(b[group][name]['value'], np.array(curve['value'])*scale, rtol=2e-11)
    np.testing.assert_allclose(a['powers']['eta']['value'], zero['powers']['eta']['value'], rtol=2e-12)
    assert a['source_powers']['eta_source']['value'] != a['powers']['eta']['value']


def test_zero_forcing_singular_denominator_and_zero_load():
    singular = state(q=1j, v=1.)
    assert apply(singular, 1., -1j)['ports']['U2'].payload()['status'] == ['unavailable']
    response = apply(singular, 0., -1j)
    for curve in response['ports'].values():
        assert curve.payload()['status'] == ['analytic_zero']
    for group in ('powers', 'source_powers'):
        for name, curve in response[group].items():
            assert curve['status'] == ['unavailable' if name.startswith('eta') else 'analytic_zero']
    response = apply(identity(load=0.), 2., 4.)
    assert response['ports']['p1'].payload()['status'] == ['analytic_zero']
    assert response['ports']['p2'].payload()['status'] == ['analytic_zero']
    assert response['powers']['Pload']['status'] == ['analytic_zero']
    assert response['powers']['Pin']['status'] == ['analytic_zero']
    assert response['source_powers']['eta_source']['value'] == [0.]
    np.testing.assert_allclose(response['ports']['U1'].values(), .5)


@pytest.mark.parametrize('component', ['q', 'v'])
def test_unresolved_component_does_not_poison_resolved_denominator(component):
    tr = state(**{component: 0.})
    result = apply(tr, 1., 2.)
    assert result['ports']['U2'].payload()['status'] == ['ok']
    name = 'p1' if component == 'q' else 'U1'
    assert result['ports'][name].payload()['status'] == ['unavailable']
    assert result['powers']['Pin']['status'] == ['unavailable']
    assert result['powers']['eta']['value'] == [None]


@pytest.mark.parametrize('ps', [0., 1.])
@pytest.mark.parametrize('key,value', [('propagation_invalid', [True]), ('log_scale', [np.inf]),
                                      ('flow_roundoff_bound', [-1.]), ('normalized_flow', [np.nan])])
def test_global_invalidity_survives_zero_source_load_and_resistance(ps, key, value):
    tr = identity(load=0.); tr[key] = np.array(value)
    result = apply(tr, ps, 0.)
    for curve in result['ports'].values():
        assert curve.payload()['status'] == ['unavailable']
    for group in ('powers', 'source_powers'):
        assert all(curve['value'] == [None] for curve in result[group].values())


def test_actual_propagation_global_invalidity():
    tr = fr.transfer_from_slices([1.], [(1., 1., 1e300)], 0., zref=1e-300)
    assert tr['propagation_invalid'][0]
    assert apply(tr, 1., 0.)['ports']['p2'].payload()['status'] == ['unavailable']


def test_exact_reactive_loss_zero_does_not_certify_unresolved_flow():
    response = apply(state(v=0.), 1., 1j)
    assert response['ports']['U1'].payload()['status'] == ['unavailable']
    assert response['source_powers']['Psupply']['value'] == [None]
    assert response['source_powers']['Pinternal']['status'] == ['analytic_zero']
    assert response['source_powers']['Pinternal']['value'] == [0.]
    assert response['source_powers']['eta_source']['value'] == [None]


@pytest.mark.parametrize('amplitude', [1e-300, 1e300])
def test_ratios_when_watts_outside_range(amplitude):
    response = apply(identity(load=1.), amplitude, 1.)
    assert response['powers']['Pload']['value'] == [None]
    assert response['powers']['Pload']['log_abs'][0] is not None
    assert response['powers']['eta']['value'][0] == pytest.approx(1., rel=2e-12)
    assert response['source_powers']['eta_source']['value'][0] == pytest.approx(.5, rel=2e-12)
    if amplitude < 1:
        bound = response['powers']['Pload']
        assert bound['roundoff_tolerance_w'][0] > 0
        assert bound['roundoff_tolerance_log_w'][0] < fr.LOG_MIN


def test_extreme_logs_and_impedance_without_direct_ratio():
    tr = fr.transfer_from_slices([1.], [(1., 2-800j, 1.)], 1., zref=1.)
    result = apply(tr, np.exp(700), 1.)
    np.testing.assert_allclose(result['ports']['U2'].values(), .5*np.exp(-100-2j), rtol=2e-12)
    assert tr['transfers']['Hu'].payload()['status'] == ['underflow']
    result = apply(state(q=1., v=1e-300, ep=0., eu=0., ref=1e-300), 1., 1e300)
    np.testing.assert_allclose(result['ports']['U2'].values(), 1., rtol=2e-12)
    # Individual Cartesian source components are finite even when hypot overflows.
    result = apply(identity(load=1e308, zref=1e308), 1.6e308+1.6e308j, 1.6e308+1.6e308j)
    assert result['source']['impedance']['value'][0] == dict(real=1.6e308, imag=1.6e308)
    assert result['ports']['U1'].payload()['status'] == ['ok']


def test_nonfinite_power_log_is_not_an_analytic_zero():
    response = apply(state(length=1e308), 1., 1.)
    assert response['ports']['U2'].payload()['status'] == ['underflow']
    assert response['ports']['U2'].payload()['log_abs'] == [-1e308]
    assert response['powers']['Pload']['status'] == ['unavailable']
    assert response['powers']['Pload']['value'] == [None]
    assert 'Nonfinite power log' in response['powers']['Pload']['reason'][0]
    assert response['powers']['eta']['value'] == [None]


def test_near_cancellation_denominator_bound_and_signed_powers():
    vanished = apply(state(q=0., v=0., ep=0., eu=0.), 1., 0.)
    assert 'denominator' in vanished['ports']['U2'].payload()['reason'][0]
    assert apply(state(q=1j), 1., -1j+1e-15)['ports']['U2'].payload()['status'] == ['unavailable']
    result = apply(state(q=1j, ep=1e-3), 1., .01-1j)
    assert result['ports']['U2'].payload()['status'] == ['ok']
    assert result['powers']['Pin']['status'] == ['roundoff_limited']
    assert result['powers']['eta']['value'] == [None]


def test_unresolved_power_cannot_hide_behind_overflow():
    result = apply(state(q=1., v=1., ep=.4, eu=.4), 1e300, 1.)
    assert result['powers']['Pload']['status'] == ['roundoff_limited']
    assert result['powers']['Pload']['value'] == [None]
    assert result['powers']['eta']['value'] == [None]
    assert result['source_powers']['eta_source']['value'] == [None]


def test_signed_passivity_violation_is_not_clamped():
    result = apply(state(q=-1., load=1.), 1., 2.)
    assert result['powers']['Pdiss']['status'] == ['passivity_violation']
    assert result['powers']['Pdiss']['value'][0] < 0
    assert result['powers']['eta']['value'] == [None]


def test_decimal_division_error_is_covered_by_power_bound():
    # Exact binary64 state coefficients, high precision real closure independent
    # of all log-domain arithmetic. Perturb the state within its given ball too.
    tr = state(q=.73, v=.41, ep=1e-8, eu=2e-8, ref=3., load=.5)
    response = apply(tr, 1.7, 2.1)
    with localcontext() as ctx:
        ctx.prec = 80
        D = Decimal.from_float
        for sq in (-1, 1):
            for sv in (-1, 1):
                q, v = D(.73)+sq*D(1e-8), D(.41)+sv*D(2e-8)
                d = D(3.)*q+D(2.1)*v
                p, u = D(1.7)*D(3.)*q/d, D(1.7)*v/d
                expected = float(p*u/2)
                pin = response['powers']['Pin']
                assert abs(pin['value'][0]-expected) <= pin['roundoff_tolerance_w'][0]


def test_reuse_no_propagation_loss_radiation_or_ideal_projection(monkeypatch):
    tr = identity(2)
    def forbidden(*args, **kw):
        raise AssertionError('repropagation/projection forbidden')
    monkeypatch.setattr(fr, 'loaded_transfer', forbidden)
    monkeypatch.setattr(fr, 'transfer_from_slices', forbidden)
    monkeypatch.setattr(fr.LegacyBetaLossModel, 'evaluate', forbidden)
    monkeypatch.setattr(fr, 'radiation_impedance', forbidden)
    monkeypatch.setattr(fr.ComplexCurve, 'values', forbidden)
    before = deepcopy(tr)
    for zs in (0., 1., 1e20, 1j):
        result = apply(tr, [1., 2.], zs)
        assert result['ports']['U2'].payload()['status'] == ['ok', 'ok']
    for key in ('normalized_pressure', 'normalized_flow', 'propagation_invalid', 'log_scale'):
        np.testing.assert_array_equal(tr[key], before[key])
