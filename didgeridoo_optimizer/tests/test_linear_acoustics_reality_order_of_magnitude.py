from __future__ import annotations

import unittest

import numpy as np

from didgeridoo_optimizer.acoustics import AirProperties, extract, find_peaks, input_impedance, radiation_impedance
from didgeridoo_optimizer.geometry.builders import DesignBuilder
from didgeridoo_optimizer.geometry.models import Design
from didgeridoo_optimizer.materials.models import AcousticParameter, Material


class LinearAcousticsRealityOrderOfMagnitudeTests(unittest.TestCase):
    def _test_material(self) -> Material:
        return Material(
            id="lossless_order_test",
            base_material="lossless_order_test",
            family="test_only",
            subtype="test_only",
            variant=None,
            beta=AcousticParameter(0.0, 0.0, 0.0, "inferred", "low"),
            wall_loss=AcousticParameter(0.0, 0.0, 0.0, "inferred", "low"),
            porosity_leak=AcousticParameter(0.0, 0.0, 0.0, "inferred", "low"),
            manufacturability="test_only",
            cost_level="test_only",
            mass_level="test_only",
            recommended_for_mouthpiece=True,
            recommended_for_body=True,
            recommended_for_bell=True,
            notes="Test-only material fixture; not a calibration or validation claim.",
        )

    def _materials(self) -> dict[str, Material]:
        return {"lossless_order_test": self._test_material()}

    def _air(self) -> AirProperties:
        return AirProperties(rho=1.204, c=343.0, temperature_c=20.0, humidity_percent=50.0)

    def _peak_config(
        self,
        *,
        n_points: int = 4096,
        f_min_hz: float = 40.0,
        f_max_hz: float = 900.0,
    ) -> dict[str, object]:
        return {
            "frequency_analysis": {
                "f_min_hz": f_min_hz,
                "f_max_hz": f_max_hz,
                "n_points": n_points,
                "peak_detection": {
                    "min_prominence": 0.01,
                    "min_distance_hz": 20.0,
                    "max_number_of_peaks": 12,
                },
            }
        }

    def _cylinder_design(self, *, length_cm: float, diameter_cm: float = 3.0) -> Design:
        return DesignBuilder().build(
            {
                "id": f"order_cylinder_{length_cm:g}_{diameter_cm:g}",
                "segments": [
                    {
                        "kind": "cylinder",
                        "length_cm": length_cm,
                        "d_in_cm": diameter_cm,
                        "d_out_cm": diameter_cm,
                        "material_id": "lossless_order_test",
                    }
                ],
            }
        )

    def _direct_linear_features(
        self,
        *,
        length_cm: float,
        diameter_cm: float = 3.0,
        n_points: int = 4096,
        f_min_hz: float = 40.0,
        f_max_hz: float = 900.0,
    ) -> dict[str, object]:
        design = self._cylinder_design(length_cm=length_cm, diameter_cm=diameter_cm)
        config = self._peak_config(n_points=n_points, f_min_hz=f_min_hz, f_max_hz=f_max_hz)
        freq_hz = np.linspace(f_min_hz, f_max_hz, n_points)
        air = self._air()
        zin = input_impedance(freq_hz, design, self._materials(), air)
        zin_mag = np.abs(zin)
        peaks = find_peaks(freq_hz, zin_mag, config)
        exit_radius_m = float(design.segments[-1].d_out_cm) / 200.0
        zr = radiation_impedance(2.0 * np.pi * freq_hz, exit_radius_m, air)
        features = extract(freq_hz, zin, peaks, design, air, zr=zr)
        return {
            "peaks": peaks,
            "features": features,
        }

    def _quarter_wave_hz(self, *, length_cm: float, diameter_cm: float) -> float:
        length_m = float(length_cm) / 100.0
        radius_m = float(diameter_cm) / 200.0
        effective_length_m = length_m + 0.613 * radius_m
        return self._air().c / (4.0 * effective_length_m)

    def test_cylinder_f0_decreases_with_length(self) -> None:
        lengths_cm = [100.0, 120.0, 140.0]

        f0_values = [
            float(self._direct_linear_features(length_cm=length_cm)["features"]["f0_hz"])
            for length_cm in lengths_cm
        ]

        self.assertGreater(f0_values[0], f0_values[1])
        self.assertGreater(f0_values[1], f0_values[2])

    def test_cylinder_f0_matches_quarter_wave_order_of_magnitude(self) -> None:
        for length_cm in [100.0, 120.0, 140.0]:
            with self.subTest(length_cm=length_cm):
                f0_hz = float(self._direct_linear_features(length_cm=length_cm)["features"]["f0_hz"])
                expected_hz = self._quarter_wave_hz(length_cm=length_cm, diameter_cm=3.0)

                self.assertLess(abs(f0_hz - expected_hz) / expected_hz, 0.15)

    def test_cylinder_first_peaks_follow_odd_mode_pattern_broadly(self) -> None:
        length_cm = 120.0
        diameter_cm = 3.0
        result = self._direct_linear_features(
            length_cm=length_cm,
            diameter_cm=diameter_cm,
            f_max_hz=900.0,
        )

        peak_frequencies = [float(peak["frequency_hz"]) for peak in result["peaks"][:3]]
        self.assertGreaterEqual(len(peak_frequencies), 3)
        expected_f0 = self._quarter_wave_hz(length_cm=length_cm, diameter_cm=diameter_cm)
        expected_modes = [((2 * index) - 1) * expected_f0 for index in range(1, 4)]

        for actual_hz, expected_hz in zip(peak_frequencies, expected_modes):
            with self.subTest(actual_hz=actual_hz, expected_hz=expected_hz):
                self.assertLess(abs(actual_hz - expected_hz) / expected_hz, 0.15)

    def test_model_confidence_decreases_with_large_diameter(self) -> None:
        confidences = [
            float(
                self._direct_linear_features(
                    length_cm=100.0,
                    diameter_cm=diameter_cm,
                    f_max_hz=6000.0,
                )["features"]["model_confidence"]
            )
            for diameter_cm in [2.5, 4.0, 8.0]
        ]

        self.assertGreater(confidences[0], confidences[1])
        self.assertGreater(confidences[1], confidences[2])


if __name__ == "__main__":
    unittest.main()
