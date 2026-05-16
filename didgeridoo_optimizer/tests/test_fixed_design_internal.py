from __future__ import annotations

import unittest
from collections.abc import Mapping
from pathlib import Path

from didgeridoo_optimizer.geometry.builders import DesignBuilder
from didgeridoo_optimizer.geometry.models import Design
from didgeridoo_optimizer.materials.database import MaterialDatabase
from didgeridoo_optimizer.pipeline.evaluate_linear import LinearEvaluationPipeline


REPO_ROOT = Path(__file__).resolve().parents[2]


class FixedDesignInternalTests(unittest.TestCase):
    def _minimal_design_mapping(self) -> dict[str, object]:
        return {
            "id": "fixed_linear_smoke",
            "segments": [
                {
                    "kind": "cylinder",
                    "length_cm": 100.0,
                    "d_in_cm": 3.0,
                    "d_out_cm": 3.0,
                    "material_id": "pvc_pressure",
                }
            ],
        }

    def _minimal_linear_config(self) -> dict[str, object]:
        return {
            "environment": {
                "air_temperature_c": 20.0,
                "air_density_kg_m3": 1.204,
                "sound_speed_m_s": 343.0,
                "relative_humidity_percent": 50.0,
            },
            "geometry_constraints": {
                "total_length_cm": {"min": 50.0, "max": 150.0},
                "body_segments": {
                    "min_count": 1,
                    "max_count": 1,
                    "min_length_cm": 10.0,
                    "max_length_cm": 150.0,
                },
                "diameter_cm": {"min": 1.0, "max": 10.0},
                "allow_steps": True,
                "allow_reverse_taper": True,
                "allow_local_constrictions": True,
                "allow_local_expansions": True,
            },
            "topology": {
                "allow_bell": False,
                "allow_bell_types": [],
            },
            "frequency_analysis": {
                "f_min_hz": 40.0,
                "f_max_hz": 600.0,
                "n_points": 128,
                "discretization_max_segment_cm": 5.0,
                "peak_detection": {
                    "min_prominence": 0.01,
                    "min_distance_hz": 5.0,
                    "max_number_of_peaks": 10,
                },
            },
            "materials": {
                "complexity_penalty": {
                    "enabled": False,
                },
            },
            "objectives": {},
        }

    def test_minimal_mapping_builds_and_evaluates_linearly(self) -> None:
        design_mapping = self._minimal_design_mapping()
        config = self._minimal_linear_config()
        material_db = MaterialDatabase.from_yaml(REPO_ROOT / "project_specs" / "materials_base_v1.yaml")

        design = DesignBuilder().build(design_mapping)

        self.assertIsInstance(design, Design)
        self.assertEqual(design.id, "fixed_linear_smoke")
        self.assertEqual(len(design.segments), 1)
        segment = design.segments[0]
        self.assertEqual(segment.position_start_cm, 0.0)
        self.assertEqual(segment.position_end_cm, 100.0)
        self.assertEqual(design.metadata["total_length_cm"], 100.0)

        result = LinearEvaluationPipeline().evaluate(design_mapping, config, material_db)

        self.assertEqual(result["design_id"], "fixed_linear_smoke")
        self.assertIs(result["valid"], True)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["freq_hz"]), 128)
        self.assertEqual(len(result["zin"]), 128)
        self.assertEqual(len(result["zin_mag"]), 128)
        self.assertIsInstance(result["features"], Mapping)
        self.assertIsInstance(result["objective_scores"], Mapping)
        self.assertIsInstance(result["penalties"], Mapping)


if __name__ == "__main__":
    unittest.main()
