from __future__ import annotations

import math
import unittest

import numpy as np

from didgeridoo_optimizer.acoustics import AirProperties, extract, find_peaks, input_impedance, radiation_impedance
from didgeridoo_optimizer.geometry.builders import DesignBuilder
from didgeridoo_optimizer.geometry.models import Design
from didgeridoo_optimizer.materials.models import AcousticParameter, Material
from didgeridoo_optimizer.optimization.objectives import score_objectives
from didgeridoo_optimizer.pipeline.evaluate_linear import LinearEvaluationPipeline


class LinearAcousticsCanonicalBenchmarks(unittest.TestCase):
    def _test_material(
        self,
        material_id: str,
        *,
        beta: float = 0.0,
        wall_loss: float = 0.0,
        porosity_leak: float = 0.0,
    ) -> Material:
        return Material(
            id=material_id,
            base_material=material_id,
            family="test_only",
            subtype="test_only",
            variant=None,
            beta=AcousticParameter(beta, beta, beta, "inferred", "low"),
            wall_loss=AcousticParameter(wall_loss, wall_loss, wall_loss, "inferred", "low"),
            porosity_leak=AcousticParameter(porosity_leak, porosity_leak, porosity_leak, "inferred", "low"),
            manufacturability="test_only",
            cost_level="test_only",
            mass_level="test_only",
            recommended_for_mouthpiece=True,
            recommended_for_body=True,
            recommended_for_bell=True,
            notes="Test-only material fixture; not a calibration or validation claim.",
        )

    def _lossless_materials(self) -> dict[str, Material]:
        return {"lossless_test": self._test_material("lossless_test")}

    def _air(self) -> AirProperties:
        return AirProperties(rho=1.204, c=343.0, temperature_c=20.0, humidity_percent=50.0)

    def _peak_config(
        self,
        *,
        n_points: int = 4096,
        f_min_hz: float = 40.0,
        f_max_hz: float = 800.0,
        discretization_max_segment_cm: float = 1.0,
        min_distance_hz: float = 20.0,
    ) -> dict[str, object]:
        return {
            "frequency_analysis": {
                "f_min_hz": f_min_hz,
                "f_max_hz": f_max_hz,
                "n_points": n_points,
                "discretization_max_segment_cm": discretization_max_segment_cm,
                "peak_detection": {
                    "min_prominence": 0.01,
                    "min_distance_hz": min_distance_hz,
                    "max_number_of_peaks": 10,
                },
            }
        }

    def _pipeline_config(
        self,
        *,
        n_points: int = 4096,
        f_min_hz: float = 40.0,
        f_max_hz: float = 800.0,
        discretization_max_segment_cm: float = 1.0,
    ) -> dict[str, object]:
        config = {
            "environment": {
                "air_temperature_c": 20.0,
                "air_density_kg_m3": 1.204,
                "sound_speed_m_s": 343.0,
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
        }
        config.update(
            self._peak_config(
                n_points=n_points,
                f_min_hz=f_min_hz,
                f_max_hz=f_max_hz,
                discretization_max_segment_cm=discretization_max_segment_cm,
            )
        )
        return config

    def _cylinder_design(self, *, material_id: str = "lossless_test") -> Design:
        return DesignBuilder().build(
            {
                "id": f"canonical_cylinder_{material_id}",
                "segments": [
                    {
                        "kind": "cylinder",
                        "length_cm": 100.0,
                        "d_in_cm": 3.0,
                        "d_out_cm": 3.0,
                        "material_id": material_id,
                    }
                ],
            }
        )

    def _direct_linear_features(
        self,
        design: Design,
        materials: dict[str, Material],
        *,
        n_points: int = 4096,
        f_min_hz: float = 40.0,
        f_max_hz: float = 800.0,
    ) -> dict[str, object]:
        config = self._peak_config(n_points=n_points, f_min_hz=f_min_hz, f_max_hz=f_max_hz)
        freq_hz = np.linspace(f_min_hz, f_max_hz, n_points)
        air = self._air()
        zin = input_impedance(freq_hz, design, materials, air)
        zin_mag = np.abs(zin)
        peaks = find_peaks(freq_hz, zin_mag, config)
        exit_radius_m = float(design.segments[-1].d_out_cm) / 200.0
        zr = radiation_impedance(2.0 * np.pi * freq_hz, exit_radius_m, air)
        features = extract(freq_hz, zin, peaks, design, air, zr=zr)
        return {
            "freq_hz": freq_hz,
            "zin": zin,
            "zin_mag": zin_mag,
            "peaks": peaks,
            "features": features,
        }

    def test_synthetic_peak_detection_respects_prominence_distance_and_q(self) -> None:
        freq_hz = np.arange(0.0, 101.0, 1.0)
        magnitude = np.ones_like(freq_hz)
        magnitude[20] = 5.0
        magnitude[25] = 4.0
        magnitude[50] = 4.5
        magnitude[80] = 2.4
        config = {
            "frequency_analysis": {
                "peak_detection": {
                    "min_prominence": 1.0,
                    "min_distance_hz": 10.0,
                    "max_number_of_peaks": 10,
                }
            }
        }

        peaks = find_peaks(freq_hz, magnitude, config)

        self.assertEqual([peak["frequency_hz"] for peak in peaks], [20.0, 50.0, 80.0])
        self.assertEqual(len(peaks), 3)
        self.assertNotIn(25.0, [peak["frequency_hz"] for peak in peaks])
        for peak in peaks:
            self.assertIsNotNone(peak["q"])
            self.assertGreater(float(peak["q"]), 0.0)

        stricter_config = {
            "frequency_analysis": {
                "peak_detection": {
                    "min_prominence": 2.0,
                    "min_distance_hz": 10.0,
                    "max_number_of_peaks": 10,
                }
            }
        }
        stricter_peaks = find_peaks(freq_hz, magnitude, stricter_config)

        self.assertEqual([peak["frequency_hz"] for peak in stricter_peaks], [20.0, 50.0])

    def test_open_cylinder_first_peak_matches_broad_quarter_wave_estimate(self) -> None:
        design = self._cylinder_design()
        result = self._direct_linear_features(design, self._lossless_materials())

        f0_hz = float(result["features"]["f0_hz"])
        radius_m = 0.03 / 2.0
        effective_length_m = 1.0 + 0.613 * radius_m
        expected_hz = self._air().c / (4.0 * effective_length_m)

        self.assertLess(abs(f0_hz - expected_hz) / expected_hz, 0.15)
        self.assertGreaterEqual(int(result["features"]["peak_count"]), 3)
        self.assertGreater(float(result["features"]["model_confidence"]), 0.8)

    def test_cylinder_f0_is_stable_when_frequency_grid_is_refined(self) -> None:
        design = self._cylinder_design()
        materials = self._lossless_materials()
        f_min_hz = 40.0
        f_max_hz = 800.0
        grid_sizes = [512, 1024, 2048, 4096]

        f0_values = [
            float(
                self._direct_linear_features(
                    design,
                    materials,
                    n_points=n_points,
                    f_min_hz=f_min_hz,
                    f_max_hz=f_max_hz,
                )["features"]["f0_hz"]
            )
            for n_points in grid_sizes
        ]

        coarsest_step_hz = (f_max_hz - f_min_hz) / (grid_sizes[0] - 1)
        self.assertLessEqual(max(f0_values) - min(f0_values), 2.0 * coarsest_step_hz)

    def test_tapered_segment_f0_approaches_fine_discretization(self) -> None:
        design = {
            "id": "canonical_cone",
            "segments": [
                {
                    "kind": "cone",
                    "length_cm": 120.0,
                    "d_in_cm": 2.5,
                    "d_out_cm": 8.0,
                    "material_id": "lossless_test",
                }
            ],
        }
        materials = self._lossless_materials()

        def f0_for(max_segment_cm: float) -> float:
            result = LinearEvaluationPipeline().evaluate(
                design,
                self._pipeline_config(discretization_max_segment_cm=max_segment_cm),
                materials,
            )
            self.assertEqual(result["errors"], [])
            self.assertTrue(result["valid"])
            return float(result["features"]["f0_hz"])

        coarse = f0_for(20.0)
        medium = f0_for(10.0)
        fine = f0_for(0.5)
        grid_step_hz = (800.0 - 40.0) / (4096 - 1)

        self.assertLess(abs(medium - fine), abs(coarse - fine))
        self.assertLessEqual(abs(medium - fine), 3.0 * grid_step_hz)

    def test_flare_radiation_uses_true_exit_diameter_across_discretization(self) -> None:
        design = {
            "id": "canonical_hard_exponential_flare",
            "segments": [
                {
                    "kind": "cylinder",
                    "length_cm": 120.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 3.8,
                    "material_id": "lossless_test",
                },
                {
                    "kind": "flare_exponential",
                    "length_cm": 20.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 12.0,
                    "material_id": "lossless_test",
                    "profile_params": {"flare_parameter": 5.0},
                },
            ],
        }
        materials = self._lossless_materials()
        brightness_values: list[float] = []
        f0_values: list[float] = []

        for max_segment_cm in [5.0, 1.0, 0.25]:
            with self.subTest(discretization_max_segment_cm=max_segment_cm):
                config = self._pipeline_config(
                    n_points=8192,
                    f_min_hz=40.0,
                    f_max_hz=3000.0,
                    discretization_max_segment_cm=max_segment_cm,
                )
                config["topology"] = {
                    "allow_bell": True,
                    "allow_bell_types": ["exponential"],
                }
                config["bell"] = {
                    "geometry_constraints": {
                        "length_cm": {"min": 1.0, "max": 40.0},
                        "throat_diameter_cm": {"min": 1.0, "max": 12.0},
                        "exit_diameter_cm": {"min": 1.0, "max": 12.0},
                        "flare_parameter": {"min": 0.05, "max": 6.0},
                    }
                }

                result = LinearEvaluationPipeline().evaluate(design, config, materials)

                self.assertEqual(result["errors"], [])
                self.assertTrue(result["valid"])
                self.assertEqual(float(result["design"].segments[-1].d_out_cm), 12.0)
                self.assertLess(float(result["analysis_design"].segments[-1].d_out_cm), 12.0)
                self.assertIn("large_bell_may_reduce_1d_validity", result["warnings"])
                brightness_values.append(float(result["features"]["brightness_proxy"]))
                f0_values.append(float(result["features"]["f0_hz"]))

        brightness_relative_spread = (max(brightness_values) - min(brightness_values)) / min(brightness_values)
        f0_relative_spread = (max(f0_values) - min(f0_values)) / min(f0_values)

        self.assertLess(brightness_relative_spread, 0.01)
        self.assertLess(f0_relative_spread, 0.01)

    def test_radiation_feature_fields_separate_absolute_and_relative_proxies(self) -> None:
        design = {
            "id": "canonical_conical_bell_semantics",
            "segments": [
                {
                    "kind": "cylinder",
                    "length_cm": 120.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 3.8,
                    "material_id": "lossless_test",
                },
                {
                    "kind": "flare_conical",
                    "length_cm": 20.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 12.0,
                    "material_id": "lossless_test",
                },
            ],
        }
        config = self._pipeline_config(
            n_points=8192,
            f_min_hz=40.0,
            f_max_hz=3000.0,
            discretization_max_segment_cm=1.0,
        )
        config["topology"] = {
            "allow_bell": True,
            "allow_bell_types": ["conical"],
        }
        config["bell"] = {
            "geometry_constraints": {
                "length_cm": {"min": 1.0, "max": 40.0},
                "throat_diameter_cm": {"min": 1.0, "max": 12.0},
                "exit_diameter_cm": {"min": 1.0, "max": 12.0},
            }
        }

        result = LinearEvaluationPipeline().evaluate(design, config, self._lossless_materials())

        self.assertEqual(result["errors"], [])
        self.assertTrue(result["valid"])
        features = result["features"]
        radiation_metrics = features["radiation_metrics"]
        self.assertIsNotNone(radiation_metrics)
        self.assertAlmostEqual(
            float(features["exit_hf_radiation_proxy"]),
            float(radiation_metrics["hf_mean_real_admittance"]),
        )
        self.assertAlmostEqual(
            float(features["radiation_brightness_ratio"]),
            float(radiation_metrics["brightness_proxy"]),
        )
        self.assertAlmostEqual(
            float(features["brightness_proxy"]),
            float(features["radiation_brightness_ratio"]),
        )

    def test_radiation_brightness_objective_scores_relative_ratio(self) -> None:
        config = {
            "objectives": {
                "radiation_brightness": {
                    "enabled": True,
                    "weight": 1.0,
                }
            }
        }

        scores = score_objectives(
            {
                "radiation_brightness_ratio": 3.0,
                "brightness_proxy": 1e-9,
                "exit_hf_radiation_proxy": 1e-9,
            },
            self._cylinder_design(),
            config,
        )

        self.assertAlmostEqual(scores["radiation_brightness"], 0.75)

    def test_higher_test_only_losses_reduce_fundamental_q_and_magnitude(self) -> None:
        materials = {
            "low_loss_test": self._test_material("low_loss_test"),
            "higher_loss_test": self._test_material(
                "higher_loss_test",
                beta=5.0,
                wall_loss=0.1,
                porosity_leak=0.1,
            ),
        }

        low_loss = self._direct_linear_features(
            self._cylinder_design(material_id="low_loss_test"),
            materials,
        )["features"]
        higher_loss = self._direct_linear_features(
            self._cylinder_design(material_id="higher_loss_test"),
            materials,
        )["features"]

        self.assertLess(
            float(higher_loss["fundamental_peak_magnitude"]),
            float(low_loss["fundamental_peak_magnitude"]),
        )
        self.assertLess(
            float(higher_loss["fundamental_q"]),
            float(low_loss["fundamental_q"]),
        )


if __name__ == "__main__":
    unittest.main()
