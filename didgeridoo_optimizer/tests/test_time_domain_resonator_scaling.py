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


if __name__ == "__main__":
    unittest.main()
