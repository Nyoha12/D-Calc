from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from didgeridoo_optimizer.reporting.patch_exports import (
    backfill_patch_state_exports,
    derive_patch_state,
    export_patch_state_files,
)


class PatchExportsTests(unittest.TestCase):
    def test_derive_patch_state_accept_local_only(self) -> None:
        report = {
            "decision": "accept_local_only",
            "validation_preserved": True,
            "proposals": {
                "patch": {
                    "materials": {
                        "plywood_varnished": {
                            "beta_nominal": 3.04,
                            "porosity_leak_nominal": 0.0114,
                        },
                        "birch": {
                            "beta_nominal": 3.23,
                        },
                    }
                }
            },
            "directed_patch": {
                "materials": {
                    "plywood_varnished": {
                        "beta_nominal": 3.04,
                        "porosity_leak_nominal": 0.0114,
                    }
                }
            },
        }

        state = derive_patch_state(report)

        self.assertEqual(
            state["patch_status"]["proposal_patch_material_ids"],
            ["birch", "plywood_varnished"],
        )
        self.assertEqual(
            state["patch_status"]["replayed_patch_material_ids"],
            ["plywood_varnished"],
        )
        self.assertEqual(
            state["patch_status"]["accepted_patch_material_ids"],
            ["plywood_varnished"],
        )
        self.assertEqual(state["patch_status"]["patch_to_calibrate_material_ids"], [])

    def test_derive_patch_state_keep_as_to_calibrate(self) -> None:
        report = {
            "decision": "keep_as_to_calibrate",
            "validation_preserved": True,
            "proposals": {
                "patch": {
                    "materials": {
                        "ash": {
                            "beta_nominal": 3.5,
                        },
                        "plywood_varnished": {
                            "beta_nominal": 3.04,
                        },
                    }
                }
            },
            "family_patch": {
                "materials": {
                    "plywood_varnished": {
                        "beta_nominal": 3.04,
                    }
                }
            },
        }

        state = derive_patch_state(report)

        self.assertEqual(state["patch_status"]["accepted_patch_material_ids"], [])
        self.assertEqual(
            state["patch_status"]["patch_to_calibrate_material_ids"],
            ["plywood_varnished"],
        )

    def test_export_and_backfill_roundtrip(self) -> None:
        report = {
            "decision": "accept_local_only",
            "validation_preserved": True,
            "proposals": {
                "patch": {
                    "materials": {
                        "plywood_varnished": {
                            "beta_nominal": 3.04,
                        },
                        "beech": {
                            "beta_nominal": 3.325,
                        },
                    }
                }
            },
            "weighted_patch": {
                "materials": {
                    "plywood_varnished": {
                        "beta_nominal": 3.04,
                    }
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            report_path = tmp_path / "calibration_report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            exports = backfill_patch_state_exports(report_path)

            expected_files = {
                "proposal_patch_yaml": "materials_patch_suggestions.yaml",
                "replayed_patch_yaml": "materials_patch_replayed.yaml",
                "accepted_patch_yaml": "materials_patch_accepted.yaml",
                "to_calibrate_patch_yaml": "materials_patch_to_calibrate.yaml",
                "patch_status_yaml": "materials_patch_status.yaml",
            }
            for key, filename in expected_files.items():
                self.assertEqual(Path(exports[key]).name, filename)
                self.assertTrue(Path(exports[key]).exists())

            accepted_payload = yaml.safe_load((tmp_path / "materials_patch_accepted.yaml").read_text(encoding="utf-8"))
            to_calibrate_payload = yaml.safe_load((tmp_path / "materials_patch_to_calibrate.yaml").read_text(encoding="utf-8"))
            status_payload = yaml.safe_load((tmp_path / "materials_patch_status.yaml").read_text(encoding="utf-8"))

            self.assertEqual(list(accepted_payload["materials"].keys()), ["plywood_varnished"])
            self.assertEqual(to_calibrate_payload["materials"], {})
            self.assertEqual(status_payload["decision"], "accept_local_only")
            self.assertEqual(status_payload["accepted_patch_material_ids"], ["plywood_varnished"])

    def test_export_patch_state_files_supports_mapping_decision_shape(self) -> None:
        report = {
            "decision": {"status": "keep_as_to_calibrate"},
            "validation_preserved": True,
            "patch_proposal": {
                "materials": {
                    "plywood_varnished": {
                        "beta_nominal": 3.04,
                    }
                }
            },
            "patch_replayed": {
                "materials": {
                    "plywood_varnished": {
                        "beta_nominal": 3.04,
                    }
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            exports = export_patch_state_files(report, tmp_dir)
            status_payload = yaml.safe_load(Path(exports["patch_status_yaml"]).read_text(encoding="utf-8"))
            pending_payload = yaml.safe_load(Path(exports["to_calibrate_patch_yaml"]).read_text(encoding="utf-8"))

            self.assertEqual(status_payload["decision"], "keep_as_to_calibrate")
            self.assertEqual(status_payload["patch_to_calibrate_material_ids"], ["plywood_varnished"])
            self.assertEqual(list(pending_payload["materials"].keys()), ["plywood_varnished"])


if __name__ == "__main__":
    unittest.main()
