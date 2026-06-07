from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.experimental_nonlinear_sweep as sweep


def _minimal_existing_report() -> dict:
    return {
        "repo": {"branch": "fixture", "head_short": "abc1234", "tracked_clean": True},
        "linear_references": [
            {"design": "cylinder_control", "f0_hz": 60.0, "second_peak_hz": 180.0, "warnings": []},
            {"design": "conical_bell_9", "f0_hz": 62.0, "second_peak_hz": 190.0, "warnings": []},
        ],
        "sweep_results": [
            {
                "phase": "sweep",
                "design": "cylinder_control",
                "effective_area_m2": 3.0e-6,
                "damping_ratio": 0.2,
                "rest_opening_m": 8.0e-4,
                "mass_kg": 1.0e-4,
                "pressure_kpa": 2.0,
                "pressure_force_sign": -1.0,
                "onset_detected": True,
                "dominant_f0_ratio": 1.0,
                "surrogate_excitation_used": False,
                "primary_classification": "acceptable",
                "classification": ["acceptable"],
            },
            {
                "phase": "sweep",
                "design": "conical_bell_9",
                "effective_area_m2": 1.0e-5,
                "damping_ratio": 0.4,
                "rest_opening_m": 1.2e-3,
                "mass_kg": 3.0e-4,
                "pressure_kpa": 4.0,
                "pressure_force_sign": -1.0,
                "onset_detected": False,
                "dominant_f0_ratio": 0.2,
                "surrogate_excitation_used": False,
                "primary_classification": "quasi_dc",
                "classification": ["quasi_dc", "failed_onset"],
            },
        ],
        "confirmation_results": [
            {
                "phase": "confirmation",
                "rank": 1,
                "design": "cylinder_control",
                "effective_area_m2": 3.0e-6,
                "damping_ratio": 0.2,
                "rest_opening_m": 8.0e-4,
                "mass_kg": 1.0e-4,
                "pressure_kpa": 2.0,
                "pressure_force_sign": -1.0,
                "onset_detected": True,
                "dominant_f0_ratio": 1.01,
                "rms_pressure": 150.0,
                "contact_fraction": 0.01,
                "surrogate_excitation_used": False,
                "stability_score": 0.7,
                "quality_score": 88.0,
                "source_quality_score": 84.0,
                "primary_classification": "acceptable",
                "confirmed_long_run": True,
            }
        ],
        "second_peak_probe_results": [
            {
                "phase": "second_peak_probe",
                "design": "cylinder_control",
                "effective_area_m2": 3.0e-6,
                "damping_ratio": 0.2,
                "rest_opening_m": 8.0e-4,
                "mass_kg": 1.0e-4,
                "pressure_kpa": 2.0,
                "pressure_force_sign": -1.0,
                "onset_detected": True,
                "dominant_freq_hz": 181.0,
                "dominant_f0_ratio": 3.02,
                "dominant_second_peak_ratio": 1.01,
                "surrogate_excitation_used": False,
                "primary_classification": "acceptable",
                "near_second_probe": True,
            }
        ],
    }


class ExperimentalNonlinearSweepToolTests(unittest.TestCase):
    def test_default_grid_matches_medium_sweep_shape(self) -> None:
        cases = sweep.default_lip_cases()
        specs = sweep.build_run_specs(sweep.DEFAULT_DESIGNS, cases, sweep.DEFAULT_PRESSURES_KPA)

        self.assertEqual(len(cases), 108)
        self.assertEqual(len(specs), 1296)
        self.assertTrue(all(spec["pressure_force_sign"] == -1.0 for spec in specs))

        sanity_cases = sweep.default_lip_cases(include_positive_sign_sanity=True)
        sanity_specs = sweep.build_run_specs(sweep.DEFAULT_DESIGNS, sanity_cases, sweep.DEFAULT_PRESSURES_KPA)

        self.assertEqual(len(sanity_cases), 110)
        self.assertEqual(len(sanity_specs), 1320)
        self.assertEqual(sum(spec["pressure_force_sign"] > 0.0 for spec in sanity_specs), 24)

    def test_classification_labels_synthetic_rows(self) -> None:
        base = {
            "onset_detected": True,
            "surrogate_excitation_used": False,
            "dominant_freq_hz": 60.0,
            "dominant_f0_ratio": 1.0,
            "rms_pressure": 150.0,
            "rms_flow": 1.0e-4,
            "contact_fraction": 0.0,
            "stability_score": 0.6,
            "regime_switch_detected": False,
            "extinction_detected": False,
        }

        primary, labels = sweep.classify_run(base)
        self.assertEqual(primary, "acceptable")
        self.assertEqual(labels, ["acceptable"])

        quasi_dc = dict(base, onset_detected=False, dominant_freq_hz=5.0, dominant_f0_ratio=0.08)
        primary, labels = sweep.classify_run(quasi_dc)
        self.assertEqual(primary, "quasi_dc")
        self.assertIn("failed_onset", labels)

        explosive = dict(base, rms_pressure=4000.0)
        primary, labels = sweep.classify_run(explosive)
        self.assertEqual(primary, "explosive")
        self.assertIn("outside_rms_pressure_range", labels)

        high_contact = dict(base, contact_fraction=0.25)
        primary, labels = sweep.classify_run(high_contact)
        self.assertEqual(primary, "high_contact")
        self.assertIn("high_contact", labels)

        zero_flow = dict(base, rms_flow=0.0)
        primary, labels = sweep.classify_run(zero_flow)
        self.assertEqual(primary, "zero_or_tiny_flow")
        self.assertIn("zero_or_tiny_flow", labels)

    def test_json_and_markdown_render_from_synthetic_report(self) -> None:
        report = {
            "repo": {"branch": "main", "head_short": "abc1234", "head_subject": "test", "tracked_clean": True},
            "linear_references": [
                {"design": "cylinder_control", "f0_hz": 60.0, "second_peak_hz": 180.0, "warnings": []}
            ],
            "sweep_summary": {
                "total": 1,
                "acceptable": 1,
                "onset": 1,
                "near_f0": 1,
                "surrogate": 0,
                "primary_classification_counts": {"acceptable": 1},
            },
            "confirmation_results": [
                {
                    "rank": 1,
                    "design": "cylinder_control",
                    "effective_area_m2": 3.0e-6,
                    "damping_ratio": 0.2,
                    "rest_opening_m": 8.0e-4,
                    "mass_kg": 1.0e-4,
                    "pressure_kpa": 2.0,
                    "dominant_f0_ratio": 1.0,
                    "rms_pressure": 150.0,
                    "contact_fraction": 0.0,
                    "stability_score": 0.6,
                    "confirmed_long_run": True,
                }
            ],
            "second_peak_probe_summary": {"total": 0},
        }

        json_text = json.dumps(sweep._json_safe(report), sort_keys=True)
        markdown = sweep.render_markdown(report)

        self.assertIn("cylinder_control", json_text)
        self.assertIn("Experimental LipModelV2 Nonlinear Sweep", markdown)
        self.assertIn("not player validation", markdown)
        self.assertIn("confirmed", markdown)

    def test_summarize_existing_cli_reads_fixture_without_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "sweep.json"
            markdown_output = tmp_path / "sweep_summary.md"
            summary_json = tmp_path / "sweep_summary.json"
            source.write_text(json.dumps(_minimal_existing_report()), encoding="utf-8")

            with mock.patch.object(sweep, "run_experiment", side_effect=AssertionError("simulation launched")):
                exit_code = sweep.main(
                    [
                        "--summarize-existing",
                        str(source),
                        "--markdown-output",
                        str(markdown_output),
                        "--summary-json",
                        str(summary_json),
                    ]
                )

            self.assertEqual(exit_code, 0)
            loaded = sweep.load_results(source)
            summary = sweep.summarize_results(loaded)
            for key in (
                "global_totals",
                "by_design",
                "by_parameter",
                "top_confirmed_candidates",
                "second_peak_probe",
                "caution_notes",
            ):
                self.assertIn(key, summary)

            markdown = markdown_output.read_text(encoding="utf-8")
            self.assertIn("## Global Totals", markdown)
            self.assertIn("## By Design", markdown)
            self.assertIn("## By Parameter", markdown)
            self.assertIn("## Top Confirmed Candidates", markdown)
            self.assertIn("## Second-Peak Probe", markdown)
            self.assertIn("No toot validation; second-peak probe is diagnostic only.", markdown)

            summary_data = json.loads(summary_json.read_text(encoding="utf-8"))
            self.assertEqual(summary_data["global_totals"]["sweep_runs"], 2)
            self.assertEqual(summary_data["global_totals"]["confirmed_long_run"], 1)
            self.assertIn("cylinder_control", summary_data["by_design"])
            self.assertIn("effective_area_m2", summary_data["by_parameter"])
            self.assertEqual(summary_data["top_confirmed_candidates"][0]["design"], "cylinder_control")
            self.assertEqual(
                summary_data["second_peak_probe"]["note"],
                "No toot validation; second-peak probe is diagnostic only.",
            )

    def test_load_results_rejects_invalid_json_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "invalid.json"
            source.write_text("{not json", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid JSON"):
                sweep.load_results(source)

    def test_quick_smoke_sweep_runs_without_onset_assumption(self) -> None:
        options = sweep.SweepOptions(
            design_names=("cylinder_control",),
            sample_rate_hz=1000,
            simulation_duration_s=0.04,
            warmup_duration_s=0.01,
            confirmation_duration_s=0.04,
            confirmation_warmup_s=0.01,
            resonator_kernel_duration_s=0.05,
            confirm_top_global=0,
            confirm_top_per_design=0,
            quick=True,
        )

        report = sweep.run_experiment(options)

        self.assertTrue(report["notice"]["experimental"])
        self.assertEqual(report["config"]["lip_model_type"], "dimensioned_v2")
        self.assertEqual(report["grid"]["run_count"], 1)
        self.assertEqual(len(report["sweep_results"]), 1)
        row = report["sweep_results"][0]
        self.assertEqual(row["design"], "cylinder_control")
        self.assertEqual(row["resonator_model_type"], "fir_long_logfit")
        self.assertFalse(row["surrogate_excitation_used"])
        self.assertIn("primary_classification", row)


if __name__ == "__main__":
    unittest.main()
