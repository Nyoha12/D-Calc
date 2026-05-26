from __future__ import annotations

import unittest

import numpy as np

from didgeridoo_optimizer.materials.models import AcousticParameter, Material
from didgeridoo_optimizer.pipeline.evaluate_linear import LinearEvaluationPipeline
from didgeridoo_optimizer.pipeline.evaluate_nonlinear import NonlinearPipeline
from didgeridoo_optimizer.nonlinear.resonator_td import TimeDomainResonator
from didgeridoo_optimizer.nonlinear.thresholds import OscillationThresholdEstimator


class TimeDomainResonatorScalingTests(unittest.TestCase):
    def _lossless_materials(self) -> dict[str, Material]:
        material = Material(
            id="lossless_test",
            base_material="lossless_test",
            family="test_only",
            subtype="test_only",
            variant=None,
            beta=AcousticParameter(0.0, 0.0, 0.0, "sourced", "high"),
            wall_loss=AcousticParameter(0.0, 0.0, 0.0, "sourced", "high"),
            porosity_leak=AcousticParameter(0.0, 0.0, 0.0, "sourced", "high"),
            manufacturability="test_only",
            cost_level="test_only",
            mass_level="test_only",
            recommended_for_mouthpiece=True,
            recommended_for_body=True,
            recommended_for_bell=True,
            notes="Test-only material fixture; not a calibration or validation claim.",
        )
        return {"lossless_test": material}

    def _controlled_loss_materials(self) -> dict[str, Material]:
        material = Material(
            id="lossless_test",
            base_material="lossless_test",
            family="test_only",
            subtype="test_only",
            variant=None,
            beta=AcousticParameter(1.0, 1.0, 1.0, "sourced", "high"),
            wall_loss=AcousticParameter(0.0, 0.0, 0.0, "sourced", "high"),
            porosity_leak=AcousticParameter(0.0, 0.0, 0.0, "sourced", "high"),
            manufacturability="test_only",
            cost_level="test_only",
            mass_level="test_only",
            recommended_for_mouthpiece=True,
            recommended_for_body=True,
            recommended_for_bell=True,
            notes="Test-only controlled-loss fixture; not a calibration or validation claim.",
        )
        return {"lossless_test": material}

    def _config(self) -> dict[str, object]:
        return {
            "environment": {
                "air_density_kg_m3": 1.204,
                "sound_speed_m_s": 343.0,
                "air_temperature_c": 20.0,
                "relative_humidity_percent": 50.0,
            },
            "geometry_constraints": {
                "total_length_cm": {"min": 50.0, "max": 200.0},
                "body_segments": {
                    "min_count": 1,
                    "max_count": 2,
                    "min_length_cm": 10.0,
                    "max_length_cm": 200.0,
                },
                "diameter_cm": {"min": 1.0, "max": 12.0},
                "allow_steps": True,
                "allow_reverse_taper": True,
                "allow_local_constrictions": True,
                "allow_local_expansions": True,
            },
            "topology": {
                "allow_bell": False,
                "allow_bell_types": [],
            },
            "materials": {
                "complexity_penalty": {
                    "enabled": False,
                },
            },
            "objectives": {},
            "frequency_analysis": {
                "f_min_hz": 40.0,
                "f_max_hz": 3000.0,
                "n_points": 8192,
                "discretization_max_segment_cm": 1.0,
                "peak_detection": {
                    "min_prominence": 0.01,
                    "min_distance_hz": 5.0,
                    "max_number_of_peaks": 30,
                },
            },
            "nonlinear_simulation": {
                "enabled": True,
                "sample_rate_hz": 12000,
                "simulation_duration_s": 0.15,
                "warmup_duration_s": 0.03,
                "pressure_scan_points": 3,
            },
        }

    def _cylinder_design(self) -> dict[str, object]:
        return {
            "id": "td_resonator_scaling_cylinder",
            "segments": [
                {
                    "kind": "cylinder",
                    "length_cm": 140.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 3.8,
                    "material_id": "lossless_test",
                }
            ],
        }

    def _linear_result(self) -> dict[str, object]:
        result = LinearEvaluationPipeline().evaluate(
            self._cylinder_design(),
            self._config(),
            self._lossless_materials(),
        )
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["valid"])
        return result

    def _controlled_loss_linear_result(self) -> dict[str, object]:
        result = LinearEvaluationPipeline().evaluate(
            self._cylinder_design(),
            self._config(),
            self._controlled_loss_materials(),
        )
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["valid"])
        return result

    def _experimental_config(self) -> dict[str, object]:
        config = self._config()
        nonlinear_cfg = dict(config["nonlinear_simulation"])
        nonlinear_cfg["resonator_model_type"] = "fir_long_logfit"
        nonlinear_cfg["resonator_kernel_duration_s"] = 1.0
        config["nonlinear_simulation"] = nonlinear_cfg
        return config

    def _interpolated_zin_magnitude(self, result: dict[str, object], frequency_hz: float) -> float:
        freq_hz = np.asarray(result["freq_hz"], dtype=float)
        zin = np.asarray(result["zin"], dtype=complex)
        real_part = np.interp(frequency_hz, freq_hz, np.real(zin))
        imag_part = np.interp(frequency_hz, freq_hz, np.imag(zin))
        return float(abs(real_part + 1j * imag_part))

    def _sinusoidal_gain(self, resonator: TimeDomainResonator, frequency_hz: float) -> float:
        sample_rate_hz = int(resonator.sample_rate_hz)
        time_s = np.arange(int(0.6 * sample_rate_hz), dtype=float) / sample_rate_hz
        flow = 1.0e-6 * np.sin(2.0 * np.pi * frequency_hz * time_s)
        pressure = resonator.pressure_from_flow(flow)
        start = int(0.1 * sample_rate_hz)
        rms_flow = float(np.sqrt(np.mean(flow[start:] ** 2)))
        rms_pressure = float(np.sqrt(np.mean(pressure[start:] ** 2)))
        return rms_pressure / max(rms_flow, 1e-30)

    def test_legacy_time_domain_resonator_is_not_impedance_scaled(self) -> None:
        result = self._linear_result()
        resonator = TimeDomainResonator.from_linear_result(result, self._config())
        f0_hz = float(result["features"]["f0_hz"])

        td_gain = self._sinusoidal_gain(resonator, f0_hz)
        zin_magnitude = self._interpolated_zin_magnitude(result, f0_hz)

        self.assertGreater(td_gain, 0.0)
        self.assertGreater(zin_magnitude, 1.0)
        self.assertLess(td_gain / zin_magnitude, 1.0e-4)

    def test_default_kernel_duration_is_shorter_than_low_drone_period(self) -> None:
        result = self._linear_result()
        resonator = TimeDomainResonator.from_linear_result(result, self._config())
        f0_hz = float(result["features"]["f0_hz"])

        kernel_duration_s = resonator.impulse_kernel.size / float(resonator.sample_rate_hz)
        f0_period_s = 1.0 / f0_hz

        self.assertAlmostEqual(kernel_duration_s, 0.01)
        self.assertLess(kernel_duration_s, f0_period_s)
        self.assertEqual(resonator.metadata["resonator_model_type"], "legacy")
        self.assertEqual(resonator.metadata["resonator_scaling_mode"], "legacy_normalized")
        self.assertFalse(resonator.metadata["experimental"])

    def test_direct_nonlinear_simulation_exposes_surrogate_excitation_flag(self) -> None:
        result = self._linear_result()
        config = self._config()
        resonator = TimeDomainResonator.from_linear_result(result, config)
        lip_params = NonlinearPipeline()._default_lip_params(result["features"])

        simulation = OscillationThresholdEstimator().simulate_at_pressure(
            resonator,
            lip_params,
            pressure_kpa=4.5,
            config=config,
            reference_freq_hz=float(result["features"]["f0_hz"]),
        )

        self.assertIn("surrogate_excitation_used", simulation)
        self.assertIsInstance(simulation["surrogate_excitation_used"], bool)

    def test_nonlinear_pipeline_default_remains_legacy_and_metadata_safe(self) -> None:
        result = self._linear_result()
        config = self._config()

        evaluated = NonlinearPipeline().evaluate(result, config)
        nonlinear = evaluated["nonlinear"]

        self.assertTrue(nonlinear["enabled"])
        self.assertEqual(NonlinearPipeline()._lip_model_type(config["nonlinear_simulation"]), "legacy")
        self.assertIn("surrogate_excitation_used", nonlinear)
        self.assertIsInstance(nonlinear["surrogate_excitation_used"], bool)
        self.assertEqual(nonlinear["resonator_model_type"], "legacy")
        self.assertFalse(nonlinear["experimental_resonator"])
        self.assertNotIn("experimental_lip_model", nonlinear)

    def test_experimental_long_fir_improves_impedance_scale_at_first_peaks(self) -> None:
        result = self._controlled_loss_linear_result()
        legacy = TimeDomainResonator.from_linear_result(result, self._config())
        experimental = TimeDomainResonator.from_linear_result(result, self._experimental_config())
        frequencies = [
            float(result["features"]["f0_hz"]),
            float(result["peaks"][1]["frequency_hz"]),
        ]

        for frequency_hz in frequencies:
            with self.subTest(frequency_hz=frequency_hz):
                zin_magnitude = self._interpolated_zin_magnitude(result, frequency_hz)
                legacy_ratio = self._sinusoidal_gain(legacy, frequency_hz) / zin_magnitude
                experimental_ratio = self._sinusoidal_gain(experimental, frequency_hz) / zin_magnitude

                self.assertLess(legacy_ratio, 1.0e-4)
                self.assertGreater(experimental_ratio, 0.3)
                self.assertLess(experimental_ratio, 3.0)

    def test_experimental_long_fir_metadata_describes_fit(self) -> None:
        result = self._controlled_loss_linear_result()
        resonator = TimeDomainResonator.from_linear_result(result, self._experimental_config())
        metadata = resonator.metadata

        self.assertEqual(metadata["resonator_model_type"], "fir_long_logfit")
        self.assertEqual(metadata["resonator_scaling_mode"], "log_magnitude_multipoint")
        self.assertTrue(metadata["experimental"])
        self.assertAlmostEqual(metadata["kernel_duration_s"], 1.0)
        self.assertEqual(metadata["kernel_length"], resonator.sample_rate_hz)
        self.assertGreaterEqual(len(metadata["scaling_reference_points_hz"]), 4)
        for key in [
            "frequency_response_fit_error_40_1000",
            "frequency_response_fit_error_40_3000",
            "max_over_response",
            "max_under_response",
            "scaling_gain",
        ]:
            self.assertIn(key, metadata)
            self.assertGreater(float(metadata[key]), 0.0)

    def test_experimental_long_fir_nonlinear_pipeline_smoke_does_not_claim_onset(self) -> None:
        result = self._controlled_loss_linear_result()
        config = self._experimental_config()
        nonlinear_cfg = dict(config["nonlinear_simulation"])
        nonlinear_cfg["sample_rate_hz"] = 4000
        nonlinear_cfg["simulation_duration_s"] = 0.05
        nonlinear_cfg["warmup_duration_s"] = 0.01
        nonlinear_cfg["pressure_scan_points"] = 2
        config["nonlinear_simulation"] = nonlinear_cfg

        evaluated = NonlinearPipeline().evaluate(result, config)

        self.assertTrue(evaluated["nonlinear"]["enabled"])
        self.assertEqual(evaluated["nonlinear"]["impulse_kernel_length"], 4000)
        self.assertIn("threshold", evaluated["nonlinear"])
        self.assertIn("regime", evaluated["nonlinear"])

    def test_dimensioned_lip_model_opt_in_smoke_with_experimental_long_fir(self) -> None:
        result = self._controlled_loss_linear_result()
        config = self._experimental_config()
        nonlinear_cfg = dict(config["nonlinear_simulation"])
        nonlinear_cfg["sample_rate_hz"] = 4000
        nonlinear_cfg["simulation_duration_s"] = 0.05
        nonlinear_cfg["warmup_duration_s"] = 0.01
        nonlinear_cfg["pressure_scan_points"] = 2
        nonlinear_cfg["lip_model_type"] = "dimensioned_v2"
        nonlinear_cfg["resonator_max_kernel_duration_s"] = 1.0
        config["nonlinear_simulation"] = nonlinear_cfg

        evaluated = NonlinearPipeline().evaluate(result, config)
        nonlinear = evaluated["nonlinear"]

        self.assertTrue(nonlinear["enabled"])
        self.assertEqual(nonlinear["impulse_kernel_length"], 4000)
        self.assertEqual(nonlinear["lip_model_type"], "dimensioned_v2")
        self.assertTrue(nonlinear["experimental_lip_model"])
        self.assertFalse(nonlinear["surrogate_excitation_used"])
        self.assertEqual(nonlinear["resonator_model_type"], "fir_long_logfit")
        self.assertEqual(nonlinear["resonator_scaling_mode"], "log_magnitude_multipoint")
        self.assertAlmostEqual(nonlinear["kernel_duration_s"], 1.0)
        self.assertEqual(nonlinear["kernel_length"], 4000)
        self.assertTrue(nonlinear["experimental_resonator"])
        self.assertGreaterEqual(len(nonlinear["scaling_reference_points_hz"]), 4)
        for key in [
            "lip_effective_area_m2",
            "lip_mass_kg",
            "lip_damping_ratio",
            "lip_rest_opening_m",
            "lip_pressure_force_sign",
            "opening_min_m",
            "opening_max_m",
            "contact_fraction",
        ]:
            self.assertIn(key, nonlinear)
            self.assertTrue(np.isfinite(float(nonlinear[key])))
        for key in [
            "frequency_response_fit_error_40_1000",
            "frequency_response_fit_error_40_3000",
            "max_over_response",
            "max_under_response",
        ]:
            self.assertIn(key, nonlinear)
            self.assertTrue(np.isfinite(float(nonlinear[key])))
            self.assertGreater(float(nonlinear[key]), 0.0)


if __name__ == "__main__":
    unittest.main()
