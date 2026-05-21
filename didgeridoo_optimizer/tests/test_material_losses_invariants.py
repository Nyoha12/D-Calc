from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from didgeridoo_optimizer.acoustics.air import AirProperties
from didgeridoo_optimizer.acoustics.losses import attenuation_alpha, complex_wavenumber
from didgeridoo_optimizer.geometry.builders import DesignBuilder
from didgeridoo_optimizer.materials.models import AcousticParameter, Material
from didgeridoo_optimizer.materials.variants import MaterialVariantGenerator
from didgeridoo_optimizer.pipeline.evaluate_linear import LinearEvaluationPipeline


REPO_ROOT = Path(__file__).resolve().parents[2]


class MaterialLossesInvariantTests(unittest.TestCase):
    def _material(
        self,
        material_id: str,
        *,
        beta: float = 2.0,
        wall_loss: float = 0.0,
        porosity_leak: float = 0.0,
        family: str = "test_only",
        beta_min: float | None = None,
        beta_max: float | None = None,
        wall_loss_min: float | None = None,
        wall_loss_max: float | None = None,
        porosity_min: float | None = None,
        porosity_max: float | None = None,
        beta_status: str = "inferred",
        wall_loss_status: str = "inferred",
        porosity_status: str = "inferred",
        beta_confidence: str = "low",
        wall_loss_confidence: str = "low",
        porosity_confidence: str = "low",
    ) -> Material:
        return Material(
            id=material_id,
            base_material=material_id,
            family=family,
            subtype="test_only",
            variant=None,
            beta=AcousticParameter(
                beta,
                beta if beta_min is None else beta_min,
                beta if beta_max is None else beta_max,
                beta_status,
                beta_confidence,
            ),
            wall_loss=AcousticParameter(
                wall_loss,
                wall_loss if wall_loss_min is None else wall_loss_min,
                wall_loss if wall_loss_max is None else wall_loss_max,
                wall_loss_status,
                wall_loss_confidence,
            ),
            porosity_leak=AcousticParameter(
                porosity_leak,
                porosity_leak if porosity_min is None else porosity_min,
                porosity_leak if porosity_max is None else porosity_max,
                porosity_status,
                porosity_confidence,
            ),
            manufacturability="test_only",
            cost_level="test_only",
            mass_level="test_only",
            recommended_for_mouthpiece=True,
            recommended_for_body=True,
            recommended_for_bell=True,
            notes="Test-only material fixture; not a calibration or coefficient validation claim.",
        )

    def _omega(self) -> np.ndarray:
        return 2.0 * np.pi * np.asarray([80.0, 160.0, 320.0, 640.0], dtype=float)

    def _design_for_material(self, material_id: str):
        return DesignBuilder().build(
            {
                "id": f"loss_warning_{material_id}",
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

    def _warnings_for_material(self, material: Material) -> list[str]:
        return LinearEvaluationPipeline()._build_warnings(
            self._design_for_material(material.id),
            {material.id: material},
            {"model_confidence": 1.0, "peak_count": 4},
        )

    def test_attenuation_alpha_increases_with_beta(self) -> None:
        omega = self._omega()
        diameter_m = 0.03
        low_beta = self._material("low_beta", beta=1.0)
        high_beta = self._material("high_beta", beta=4.0)

        alpha_low = attenuation_alpha(omega, diameter_m, low_beta)
        alpha_high = attenuation_alpha(omega, diameter_m, high_beta)

        self.assertTrue(np.all(alpha_high > alpha_low))

    def test_attenuation_alpha_decreases_with_larger_diameter(self) -> None:
        omega = self._omega()
        material = self._material("diameter_sensitive", beta=3.0)

        alpha_small = attenuation_alpha(omega, 0.02, material)
        alpha_large = attenuation_alpha(omega, 0.06, material)

        self.assertTrue(np.all(alpha_small > alpha_large))

    def test_wall_loss_increases_effective_attenuation(self) -> None:
        omega = self._omega()
        diameter_m = 0.03
        low_wall = self._material("low_wall_loss", beta=3.0, wall_loss=0.0, porosity_leak=0.01)
        high_wall = self._material("high_wall_loss", beta=3.0, wall_loss=0.05, porosity_leak=0.01)

        alpha_low = attenuation_alpha(omega, diameter_m, low_wall)
        alpha_high = attenuation_alpha(omega, diameter_m, high_wall)

        self.assertTrue(np.all(alpha_high > alpha_low))

    def test_porosity_leak_increases_effective_attenuation(self) -> None:
        omega = self._omega()
        diameter_m = 0.03
        low_porosity = self._material("low_porosity", beta=3.0, wall_loss=0.01, porosity_leak=0.0)
        high_porosity = self._material("high_porosity", beta=3.0, wall_loss=0.01, porosity_leak=0.05)

        alpha_low = attenuation_alpha(omega, diameter_m, low_porosity)
        alpha_high = attenuation_alpha(omega, diameter_m, high_porosity)

        self.assertTrue(np.all(alpha_high > alpha_low))

    def test_wall_loss_and_porosity_are_equivalent_when_sum_matches_current_model(self) -> None:
        omega = self._omega()
        diameter_m = 0.03
        wall_heavy = self._material("wall_heavy", beta=3.0, wall_loss=0.02, porosity_leak=0.01)
        porosity_heavy = self._material("porosity_heavy", beta=3.0, wall_loss=0.01, porosity_leak=0.02)

        alpha_wall_heavy = attenuation_alpha(omega, diameter_m, wall_heavy)
        alpha_porosity_heavy = attenuation_alpha(omega, diameter_m, porosity_heavy)

        np.testing.assert_allclose(alpha_wall_heavy, alpha_porosity_heavy)

    def test_complex_wavenumber_uses_current_loss_sign_convention(self) -> None:
        omega = self._omega()
        diameter_m = 0.03
        air = AirProperties(rho=1.204, c=343.0, temperature_c=20.0, humidity_percent=50.0)
        low_loss = self._material("low_complex_loss", beta=1.0, wall_loss=0.0, porosity_leak=0.0)
        high_loss = self._material("high_complex_loss", beta=5.0, wall_loss=0.05, porosity_leak=0.05)

        k_low = complex_wavenumber(omega, diameter_m, low_loss, air)
        k_high = complex_wavenumber(omega, diameter_m, high_loss, air)

        np.testing.assert_allclose(np.real(k_low), omega / air.c)
        np.testing.assert_allclose(np.real(k_high), omega / air.c)
        self.assertTrue(np.all(np.imag(k_high) <= 0.0))
        self.assertTrue(np.all(np.abs(np.imag(k_high)) > np.abs(np.imag(k_low))))

    def test_high_losses_warning_follows_current_thresholds(self) -> None:
        below_threshold = self._material(
            "below_threshold",
            beta=5.0,
            wall_loss=0.03,
            porosity_leak=0.03,
        )
        high_beta = self._material("high_beta_warning", beta=5.01)
        high_wall_loss = self._material("high_wall_warning", beta=1.0, wall_loss=0.031)
        high_porosity = self._material("high_porosity_warning", beta=1.0, porosity_leak=0.031)

        self.assertNotIn("high_losses_material", self._warnings_for_material(below_threshold))
        self.assertIn("high_losses_material", self._warnings_for_material(high_beta))
        self.assertIn("high_losses_material", self._warnings_for_material(high_wall_loss))
        self.assertIn("high_losses_material", self._warnings_for_material(high_porosity))

    def test_wood_variants_preserve_parameter_status_and_confidence(self) -> None:
        generator = MaterialVariantGenerator.from_yaml(REPO_ROOT / "project_specs" / "wood_variant_rules_v1.yaml")
        base = self._material(
            "test_birch",
            family="wood",
            beta=4.0,
            beta_min=3.0,
            beta_max=5.0,
            wall_loss=0.02,
            wall_loss_min=0.01,
            wall_loss_max=0.04,
            porosity_leak=0.02,
            porosity_min=0.01,
            porosity_max=0.05,
            beta_status="to_calibrate",
            wall_loss_status="inferred",
            porosity_status="inferred",
            beta_confidence="low",
            wall_loss_confidence="medium",
            porosity_confidence="medium",
        )

        variant = generator.generate_variant(
            base,
            humidity_state="humid",
            finish="epoxy_lined",
            grade="knotty",
            density_class="medium",
        )

        self.assertEqual(variant.beta.status, base.beta.status)
        self.assertEqual(variant.wall_loss.status, base.wall_loss.status)
        self.assertEqual(variant.porosity_leak.status, base.porosity_leak.status)
        self.assertEqual(variant.beta.confidence, base.beta.confidence)
        self.assertEqual(variant.wall_loss.confidence, base.wall_loss.confidence)
        self.assertEqual(variant.porosity_leak.confidence, base.porosity_leak.confidence)

    def test_wood_variant_rules_apply_simple_loss_directions(self) -> None:
        generator = MaterialVariantGenerator.from_yaml(REPO_ROOT / "project_specs" / "wood_variant_rules_v1.yaml")
        base = self._material(
            "test_wood",
            family="wood",
            beta=4.0,
            beta_min=3.0,
            beta_max=5.0,
            wall_loss=0.02,
            wall_loss_min=0.01,
            wall_loss_max=0.04,
            porosity_leak=0.02,
            porosity_min=0.01,
            porosity_max=0.05,
        )

        raw_airdry = generator.generate_variant(
            base,
            humidity_state="airdry",
            finish="raw",
            grade="clear",
            density_class="medium",
        )
        humid_raw = generator.generate_variant(
            base,
            humidity_state="humid",
            finish="raw",
            grade="clear",
            density_class="medium",
        )
        epoxy_airdry = generator.generate_variant(
            base,
            humidity_state="airdry",
            finish="epoxy_lined",
            grade="clear",
            density_class="medium",
        )
        knotty_airdry = generator.generate_variant(
            base,
            humidity_state="airdry",
            finish="raw",
            grade="knotty",
            density_class="medium",
        )

        self.assertGreater(humid_raw.beta.nominal, raw_airdry.beta.nominal)
        self.assertGreater(humid_raw.wall_loss.nominal, raw_airdry.wall_loss.nominal)
        self.assertGreater(humid_raw.porosity_leak.nominal, raw_airdry.porosity_leak.nominal)
        self.assertLess(epoxy_airdry.porosity_leak.nominal, raw_airdry.porosity_leak.nominal)
        self.assertGreater(knotty_airdry.beta.span, raw_airdry.beta.span)


if __name__ == "__main__":
    unittest.main()
