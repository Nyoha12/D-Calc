from __future__ import annotations

import math
import unittest

import numpy as np

from didgeridoo_optimizer.acoustics.air import AirProperties
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters, LipModel, LipModelV2, LipParameters, opening
from didgeridoo_optimizer.nonlinear.thresholds import LIP_MODEL_LEGACY, OscillationThresholdEstimator


class LipModelV2Tests(unittest.TestCase):
    def test_default_selection_and_legacy_helpers_remain_legacy(self) -> None:
        estimator = OscillationThresholdEstimator()

        self.assertEqual(estimator._lip_model_type({}), LIP_MODEL_LEGACY)
        self.assertIsInstance(estimator.lip_model, LipModel)
        self.assertEqual(LipParameters().coupling_pa_per_m, 9.0e5)
        self.assertEqual(opening([-1.0e-3, 0.0]), 0.0)

    def test_dimensioned_mechanics_use_mass_resonance_damping_and_pressure_force(self) -> None:
        params = DimensionedLipParameters(
            mouth_pressure_kpa=1.2,
            resonance_hz=100.0,
            effective_area_m2=2.0e-6,
            mass_kg=2.0e-4,
            damping_ratio=0.25,
            rest_opening_m=1.0e-3,
            pressure_force_sign=-1.0,
        )
        model = LipModelV2(params)

        omega = 2.0 * math.pi * params.resonance_hz
        self.assertAlmostEqual(model.stiffness_n_per_m(), params.mass_kg * omega * omega)
        self.assertAlmostEqual(model.damping_n_s_per_m(), 2.0 * params.damping_ratio * params.mass_kg * omega)

        pressure_force = model.pressure_force(params, p_acoustic_pa=200.0)
        self.assertAlmostEqual(pressure_force, -1.0 * params.effective_area_m2 * 1000.0)

        deriv = model.derivatives(0.0, [0.0, 0.0], params, p_acoustic_pa=200.0)
        self.assertTrue(np.all(np.isfinite(deriv)))
        self.assertAlmostEqual(float(deriv[1]), pressure_force / params.mass_kg)

    def test_dimensioned_flow_is_bernoulli_and_closes_cleanly(self) -> None:
        air = AirProperties(rho=1.204, c=343.0, temperature_c=20.0, humidity_percent=50.0)
        params = DimensionedLipParameters(mouth_pressure_kpa=1.0, rest_opening_m=8.0e-4, lip_width_m=0.012)
        model = LipModelV2(params)

        self.assertGreater(model.flow([0.0, 0.0], params, p_acoustic_pa=0.0, air=air), 0.0)
        self.assertEqual(model.flow([0.0, 0.0], params, p_acoustic_pa=1200.0, air=air), 0.0)
        self.assertEqual(model.flow([-2.0 * params.rest_opening_m, 0.0], params, p_acoustic_pa=0.0, air=air), 0.0)

    def test_dimensioned_contact_and_contact_fraction_diagnostics(self) -> None:
        params = DimensionedLipParameters(
            rest_opening_m=5.0e-4,
            min_opening_m=1.0e-4,
            contact_stiffness_n_per_m=1000.0,
            contact_damping_n_s_per_m=0.5,
        )
        model = LipModelV2(params)

        self.assertFalse(model.contact_active([0.0, 0.0], params))
        self.assertEqual(model.contact_force([0.0, 0.0], params), 0.0)

        contact_state = [-4.5e-4, -0.2]
        self.assertTrue(model.contact_active(contact_state, params))
        self.assertGreater(model.contact_force(contact_state, params), 0.0)

        metadata = OscillationThresholdEstimator()._dimensioned_lip_metadata(
            params,
            opening_signal=[5.0e-4, 5.0e-5, 2.0e-4, 4.0e-5],
            contact_signal=[False, True, False, True],
            warmup_index=0,
        )
        self.assertEqual(metadata["lip_model_type"], "dimensioned_v2")
        self.assertAlmostEqual(metadata["contact_fraction"], 0.5)
        self.assertAlmostEqual(metadata["opening_min_m"], 4.0e-5)
        self.assertAlmostEqual(metadata["opening_max_m"], 5.0e-4)


if __name__ == "__main__":
    unittest.main()
