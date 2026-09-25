"""IO-N2 fixed-mesh roundoff regressions; unittest + NumPy, no pytest/SciPy.

Decimal oracles use Taylor exp on complex Decimal pairs (70 digits), then an
analytic homogeneous section / independent unscaled ABCD product. They never
use production co/si, normalization, error recurrence or tangent propagation.
"""
from decimal import Decimal, localcontext
import json
from pathlib import Path
import unittest
import warnings

import numpy as np

from didgeridoo_optimizer.acoustics import forced_response as fr
from didgeridoo_optimizer.acoustics import transfer_matrix as tm
from didgeridoo_optimizer.acoustics.losses import LegacyBetaLossModel
from didgeridoo_optimizer.acoustics.thermoviscous import CK_DRY_20C as AIR, ZwikkerKostenLossModel
from tools.forced_response_compare import builtin_design, synthetic_material


def dc(z):
    z = complex(z)
    return Decimal.from_float(z.real), Decimal.from_float(z.imag)


def add(a, b):
    return a[0]+b[0], a[1]+b[1]


def mul(a, b):
    return a[0]*b[0]-a[1]*b[1], a[0]*b[1]+a[1]*b[0]


def div(a, b):
    den = b[0]*b[0]+b[1]*b[1]
    return (a[0]*b[0]+a[1]*b[1])/den, (a[1]*b[0]-a[0]*b[1])/den


def dexp(z):
    term = total = dc(1)
    for n in range(1, 2000):
        term = div(mul(term, z), dc(n))
        total = add(total, term)
        if max(abs(v) for v in term) < Decimal('1e-65'):
            return total
    raise AssertionError('Decimal exponential failed to converge')


def decimal_state(sections, load):
    """sections already outlet->inlet; length is (binary64 length, repeats)."""
    with localcontext() as ctx:
        ctx.prec = 70
        p, u = dc(load), dc(1)
        for (length, repeats), wave, impedance in sections:
            theta = mul(dc(wave), (Decimal.from_float(length)*repeats, Decimal(0)))
            jtheta = mul(dc(1j), theta)
            plus, minus = dexp(jtheta), dexp(mul(dc(-1), jtheta))
            co = div(add(plus, minus), dc(2))
            si = div(add(plus, mul(dc(-1), minus)), dc(2))
            p, u = add(mul(co, p), mul(mul(dc(impedance), si), u)), add(mul(div(si, dc(impedance)), p), mul(co, u))
        return p, u


def as_complex(z):
    return complex(float(z[0]), float(z[1]))


class RoundoffTests(unittest.TestCase):
    def assert_oracle(self, tr, p, u):
        # Compare normalized states as well as observables, not only Zin.
        with localcontext() as ctx:
            ctx.prec = 70
            scale = (-Decimal.from_float(float(tr['log_scale'][0]))).exp()
            pn = (p[0]*scale/Decimal.from_float(tr['zref']), p[1]*scale/Decimal.from_float(tr['zref']))
            un = (u[0]*scale, u[1]*scale)
            for name, exact, bound in (('normalized_pressure', pn, 'pressure_roundoff_bound'),
                                       ('normalized_flow', un, 'flow_roundoff_bound')):
                error = abs(as_complex(add(dc(tr[name][0]), mul(dc(-1), exact))))
                self.assertLessEqual(error, tr[bound][0], (name, error, tr[bound][0]))
            expected = dict(Zin=div(p, u), Yin=div(u, p), Hu=div(dc(1), u), Yt=div(dc(1), p))
            for key, value in expected.items():
                np.testing.assert_allclose(tr['transfers'][key].values(), as_complex(value), rtol=1e-10, atol=0)

    def test_body_bell_1500_refinement_sources_and_recurrence(self):
        freq = [1000., 1500.]
        mat = synthetic_material(); design = builtin_design('body_bell')
        model = ZwikkerKostenLossModel(AIR)
        for h in (1., .5, .25):
            with self.subTest(h_cm=h):
                mesh = fr.prepare_mesh(design, h, len(freq))
                tr = fr.loaded_transfer(freq, mesh, {mat.id: mat}, AIR.as_air_properties(), exit_radius_m=.06, loss_model=model)
                expected = tm.input_impedance(freq, mesh, {mat.id: mat}, AIR.as_air_properties(), exit_radius_m=.06, loss_model=model)
                for key in tr['transfers']:
                    self.assertEqual(tr['transfers'][key].payload()['status'], ['ok', 'ok'])
                np.testing.assert_allclose(tr['transfers']['Zin'].values(), expected, rtol=1e-10, atol=1e-7)
                self.assertLess(float(max(tr['pressure_roundoff_bound'])), 1e-8)
                states = [fr.apply_source(tr, kind, amp) for kind, amp in (('volume_flow', 1e-6), ('pressure', 1.))]
                for state in states:
                    for key in ('Pin', 'Pload', 'Pdiss', 'eta'):
                        self.assertEqual(state['powers'][key]['status'], ['ok', 'ok'])
                    p1, u1, p2, u2 = [state['ports'][key].values() for key in ('p1', 'U1', 'p2', 'U2')]
                    np.testing.assert_allclose(state['powers']['Pin']['value'], .5*(p1*u1.conj()).real, rtol=1e-11)
                    np.testing.assert_allclose(state['powers']['Pload']['value'], .5*(p2*u2.conj()).real, rtol=1e-11)
                np.testing.assert_allclose(states[0]['powers']['eta']['value'], states[1]['powers']['eta']['value'], rtol=1e-11)

    def test_decimal_long_subdivision_and_reference_scaling(self):
        # Exactly summed binary64 slice lengths; homogeneous oracle evaluated once.
        for wave, impedance in ((17., 3e5), (17-.2j, 3e5-9e3j)):
            p, u = decimal_state([((1/4096, 4096), wave, impedance)], 2e5+4e4j)
            for count in (1, 4096):
                for reference in (1e-5, 1e5, 1e15):
                    with self.subTest(wave=wave, count=count, zref=reference):
                        tr = fr.transfer_from_slices([1500.], ((1/count, wave, impedance) for _ in range(count)), 2e5+4e4j, zref=reference)
                        self.assert_oracle(tr, p, u)
                        self.assertLess(tr['pressure_roundoff_bound'][0]/abs(tr['normalized_pressure'][0]), 1e-8)

    def test_decimal_variable_complex_impedance_frame_transport(self):
        # Complex phase and modulus changes; blocks have exact dyadic lengths.
        blocks = [((1/512, 128), 9-.4j, 2e5+1e4j),
                  ((1/512, 192), 13-.1j, 5e5-3e4j),
                  ((1/512, 64), 17-.3j, 1e5+8e3j)]
        phase_changes = [((1/16, 1), 2-.1j, z) for z in (1+3j, 4-2j, 2+5j, 3-4j)]
        for sections, load in ((blocks, 3e5+2e4j), (phase_changes, 2+1j)):
            p, u = decimal_state(sections, load)
            for reference in (1e2, 1e5, 1e10):
                with self.subTest(zref=reference, load=load):
                    rows = ((length, wave, z) for (length, count), wave, z in sections for _ in range(count))
                    self.assert_oracle(fr.transfer_from_slices([1500.], rows, load, zref=reference), p, u)

    def test_analytic_cylinder_same_physics_and_moderate_abcd(self):
        freq = np.array([40., 70., 400., 1000., 1500.]); nominal = AIR.rho*AIR.c/(np.pi*.015**2)
        for model in (None, LegacyBetaLossModel(), ZwikkerKostenLossModel(AIR)):
            if model is None:
                wave, impedance = 2*np.pi*freq/AIR.c, np.full(len(freq), nominal)
            else:
                pair = model.evaluate(2*np.pi*freq, .03, synthetic_material(), nominal, AIR.as_air_properties())
                wave, impedance = pair.k_complex, pair.zc_complex
            for load in (0., 3e5, 2e5+4e4j):
                for sections in ([(1.21, wave, impedance)], [(.6, wave, impedance), (.61, wave*1.02, impedance*.8)]):
                    a = d = np.ones(len(freq), complex); b = c = np.zeros(len(freq), complex)
                    for length, k, z in sections:
                        co, si = np.cos(k*length), np.sin(k*length)
                        bi, ci = 1j*z*si, 1j*si/z
                        a, b, c, d = a*co+b*ci, a*bi+b*co, c*co+d*ci, c*bi+d*co
                    p, u = a*load+b, c*load+d
                    for count, reference in ((1, 1e5), (128, 1e7)):
                        rows = ((length/count, k, z) for length, k, z in reversed(sections) for _ in range(count))
                        tr = fr.transfer_from_slices(freq, rows, load, zref=reference)
                        for key, expected in dict(Zin=p/u, Yin=u/p, Hu=1/u, Yt=1/p, Zt=load/u, Hp=load/p).items():
                            np.testing.assert_allclose(tr['transfers'][key].values(), expected, rtol=1e-10, atol=1e-14)

    def test_quarter_wave_and_pressure_null_remain_unresolved(self):
        for count in (1, 256):
            for theta, missing, available in ((np.pi/2, 'Hu', 'Yt'), (np.pi, 'Yt', 'Hu')):
                tr = fr.transfer_from_slices([1.], ((1/count, theta, 3e5) for _ in range(count)), 0., zref=3e5)
                self.assertEqual(tr['transfers'][missing].payload()['status'], ['unavailable'])
                self.assertEqual(tr['transfers'][available].payload()['status'], ['ok'])
        # Small but well resolved pressure must survive; no blanket null masking.
        tr = fr.transfer_from_slices([1.], [(1., np.pi+1e-8, 3e5)], 0., zref=3e5)
        self.assertEqual(tr['transfers']['Yt'].payload()['status'], ['ok'])

    def test_attenuation_log_recovery_and_io_n1_limits(self):
        tr = fr.transfer_from_slices([1.], [(1., 2-1000j, 3e5)], 3e5, zref=1e5)
        np.testing.assert_allclose(tr['transfers']['Zin'].values(), 3e5, rtol=1e-12)
        hu = tr['transfers']['Hu'].payload()
        self.assertAlmostEqual(hu['log_abs'][0], -1000., places=11)
        self.assertAlmostEqual(hu['phase_rad'][0], -2., places=11)
        self.assertEqual(hu['status'], ['underflow'])
        for kind in ('pressure', 'volume_flow'):
            for attenuation in (800., 9e307):
                transfer = fr.transfer_from_slices([1.], [(1., 2-attenuation*1j, 3e5)], 3e5, zref=3e5)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always', RuntimeWarning)
                    tiny = fr.apply_source(transfer, kind, 1.)
                    zero = fr.apply_source(transfer, kind, 0.)
                self.assertFalse(caught)
                for name in ('Pin', 'Pload', 'Pdiss'):
                    self.assertEqual(zero['powers'][name]['status'], ['analytic_zero'])
                if attenuation == 800.:
                    self.assertEqual(tiny['powers']['Pload']['status'], ['underflow'])
                    self.assertAlmostEqual(tiny['powers']['eta']['log_abs'][0], -1600., places=10)
                    recovered = fr.apply_source(transfer, kind, np.exp(700))
                    coefficient = 3e5 if kind == 'volume_flow' else 1/3e5
                    np.testing.assert_allclose(recovered['powers']['Pload']['value'], .5*coefficient*np.exp(-200), rtol=1e-12)
                else:
                    for name in ('Pload', 'Pdiss', 'eta'):
                        self.assertEqual(tiny['powers'][name]['status'], ['unavailable'])
                    self.assertEqual(tiny['powers']['Pin']['positive_log_resolved'], [True])
                    self.assertEqual(transfer['transfers']['Hu'].payload()['log_abs'], [-9e307])

    def test_large_cartesian_components_and_subnormal_projection(self):
        large = fr.transfer_from_slices([1.], [(1., 1.2, 1.7e308)], 1., zref=1e308)
        self.assertEqual(large['transfers']['Zin'].payload()['status'], ['overflow'])
        self.assertIsNotNone(fr.apply_source(large, 'pressure', 1.)['ports']['U1'].payload()['value'][0])
        finite = fr.transfer_from_slices([1.], [], 1.7e308+1.7e308j, zref=1e308)
        self.assertEqual(finite['transfers']['Zin'].payload()['status'], ['ok'])
        weighted = fr.transfer_from_slices([1.], [(1., 2-800j, 1e100)], 1e100, zref=1e100)
        self.assertEqual(weighted['transfers']['Hu'].payload()['status'], ['underflow'])
        np.testing.assert_allclose(weighted['transfers']['Zt'].values(), np.exp(-800+np.log(1e100)-2j), rtol=1e-12)
        subnormal = fr.transfer_from_slices([1.], [(1., 2-720j, 1.)], 1., zref=1.)
        self.assertEqual(subnormal['transfers']['Hu'].payload()['status'], ['subnormal'])
        np.testing.assert_allclose(subnormal['transfers']['Hu'].values(), np.exp(-720-2j), rtol=1e-10, atol=0)

    def test_zero_load_source_and_signed_power_resolution(self):
        for load in (0., 3e5j):
            tr = fr.transfer_from_slices([1.], [(1., 2-.3j, 3e5)], load, zref=3e5)
            for kind in ('pressure', 'volume_flow'):
                zero = fr.apply_source(tr, kind, 0.)
                for port in zero['ports'].values():
                    np.testing.assert_array_equal(port.values(), 0.)
                for name in ('Pin', 'Pload', 'Pdiss'):
                    self.assertEqual(zero['powers'][name]['status'], ['analytic_zero'])
                self.assertEqual(zero['powers']['eta']['value'], [None])
                state = fr.apply_source(tr, kind, 1.)
                self.assertEqual(state['powers']['Pload']['status'], ['analytic_zero'])
                self.assertEqual(state['powers']['eta']['value'], [0.])
        tr = fr.transfer_from_slices([1., 2.], [(1., [.3, 2.], 3e5)], 2e5+1e4j, zref=1e5)
        power = fr.apply_source(tr, 'volume_flow', 1e-6)['powers']
        self.assertEqual(power['Pdiss']['status'], ['roundoff_limited']*2)
        for value, bound in zip(power['Pdiss']['value'], power['Pdiss']['roundoff_tolerance_w']):
            self.assertLessEqual(abs(value), bound)
        tr = fr.transfer_from_slices([1.], [(1., np.pi+1e-12, 3e5)], 1e-9, zref=3e5)
        self.assertEqual(tr['transfers']['Zin'].payload()['status'], ['ok'])
        power = fr.apply_source(tr, 'volume_flow', 1.)['powers']
        self.assertEqual(power['Pin']['status'], ['roundoff_limited'])
        self.assertEqual(power['eta']['value'], [None])
        # Im(k)<=0, Re(Zc)>0 alone do not certify every arbitrary pair passive.
        tr = fr.transfer_from_slices([1.], [(1., 1., 1+2j)], 1., zref=1.)
        power = fr.apply_source(tr, 'volume_flow', 1.)['powers']
        self.assertLess(power['Pin']['value'][0], 0.)
        self.assertEqual(power['Pdiss']['status'], ['passivity_violation'])
        self.assertEqual(power['eta']['value'], [None])

    def test_efficiency_survives_power_projection(self):
        tr = fr.transfer_from_slices([1.], [(1., 2-.3j, 3e5)], 3e5, zref=3e5)
        for amplitude in (1e-200, 1e200):
            power = fr.apply_source(tr, 'volume_flow', amplitude)['powers']
            self.assertEqual(power['Pin']['value'], [None])
            self.assertEqual(power['Pin']['positive_log_resolved'], [True])
            np.testing.assert_allclose(power['eta']['value'], np.exp(-.6), rtol=1e-12)
        for log in (-np.inf, np.inf, np.nan):
            self.assertEqual(fr._real_payload([log], [1.], [''])['status'], ['unavailable'])

    def test_extreme_reference_ratios_fail_closed(self):
        for reference in (1e-300, 1e300):
            # Very ill-scaled yet representable matrices; do not suppress them.
            tr = fr.transfer_from_slices([1.], [(1., 2-.3j, 1.)], 1., zref=reference)
            np.testing.assert_allclose(tr['transfers']['Zin'].values(), 1., rtol=1e-12)
        for impedance, reference in ((1e300, 1e-300), (1e-300, 1e300)):
            tr = fr.transfer_from_slices([1.], [(1., 2-.3j, impedance)], 1., zref=reference)
            for curve in tr['transfers'].values():
                self.assertEqual(curve.payload()['status'], ['unavailable'])

    def test_main_independent_ode_mesh_convergence(self):
        rows = json.loads((Path(__file__).parent/'fixtures/forced_response_reference.json').read_text())['cone_reference']['curve']
        freq = [r['frequency_hz'] for r in rows]
        mat = synthetic_material(); design = builtin_design('body_bell')
        errors = {key: [] for key in ('Zin', 'Hu', 'Yt')}
        for h in (1., .5, .25):
            mesh = fr.prepare_mesh(design, h, len(freq))
            tr = fr.loaded_transfer(freq, mesh, {mat.id: mat}, AIR.as_air_properties(), exit_radius_m=.06, loss_model=ZwikkerKostenLossModel(AIR))
            for key, refkey in (('Zin', 'zin'), ('Hu', 'huu'), ('Yt', 'hup')):
                expected = np.array([complex(r[refkey]['real'], r[refkey]['imag']) for r in rows])
                errors[key].append(np.linalg.norm(tr['transfers'][key].values()-expected)/np.linalg.norm(expected))
        for key, bound in (('Zin', 2e-4), ('Hu', 2e-4), ('Yt', 6e-4)):
            self.assertLess(errors[key][-1], bound)
            self.assertGreater(errors[key][0]/errors[key][1], 3.)
            self.assertGreater(errors[key][1]/errors[key][2], 3.)


if __name__ == '__main__':
    unittest.main()
