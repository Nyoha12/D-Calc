from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from didgeridoo_optimizer.pipeline import run_optimizer


REPO_ROOT = Path(__file__).resolve().parents[2]


class RunOptimizerCliTests(unittest.TestCase):
    def _write_config(
        self,
        tmp_path: Path,
        output_dir: Path | None = None,
        *,
        include_schema_version: bool = False,
        schema_version: str | None = None,
    ) -> Path:
        config_path = tmp_path / "optimizer_config.yaml"
        config = {
            "project": {
                "output_dir": str(output_dir or (tmp_path / "configured_results")),
            },
            "materials": {
                "database_file": str(REPO_ROOT / "project_specs" / "materials_base_v1.yaml"),
                "variant_rules_file": str(REPO_ROOT / "project_specs" / "wood_variant_rules_v1.yaml"),
            },
        }
        if include_schema_version:
            config["schema_version"] = schema_version or run_optimizer.CONFIG_SCHEMA_VERSION
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return config_path

    def _write_tiny_smoke_config(
        self,
        tmp_path: Path,
        *,
        save_plots: bool = False,
        save_best_design_plots: bool | None = None,
    ) -> Path:
        config_path = tmp_path / "optimizer_tiny.yaml"
        reporting = {
            "save_yaml_summary": True,
            "save_json_summary": True,
            "save_csv_scores": True,
            "save_plots": save_plots,
        }
        if save_best_design_plots is not None:
            reporting["save_best_design_plots"] = save_best_design_plots
        config = {
            "schema_version": run_optimizer.CONFIG_SCHEMA_VERSION,
            "project": {
                "name": "cli_e2e_smoke",
                "random_seed": 7,
                "output_dir": str(tmp_path / "configured_output_should_be_overridden"),
            },
            "materials": {
                "database_file": str(REPO_ROOT / "project_specs" / "materials_base_v1.yaml"),
                "variant_rules_file": str(REPO_ROOT / "project_specs" / "wood_variant_rules_v1.yaml"),
                "allowed_materials": ["pvc_pressure"],
                "max_distinct_materials_per_design": 1,
            },
            "topology": {
                "allow_bell": False,
                "allow_bell_types": [],
                "allow_branches": False,
                "allow_helmholtz": False,
            },
            "frequency_analysis": {
                "f_min_hz": 40.0,
                "f_max_hz": 600.0,
                "n_points": 256,
                "discretization_max_segment_cm": 5.0,
                "peak_detection": {
                    "min_prominence": 0.01,
                    "min_distance_hz": 5.0,
                    "max_number_of_peaks": 10,
                },
            },
            "optimization": {
                "random_initial_population": 2,
                "generations": 1,
                "linear_budget": 2,
                "keep_top_n_linear": 2,
                "top_n_for_robustness": 1,
                "top_n_for_nonlinear": 0,
                "final_output_count": 1,
                "final_selector": "weighted_sum",
            },
            "runtime_estimation": {
                "benchmark_samples_linear": 1,
                "benchmark_samples_nonlinear": 0,
                "warn_if_estimated_time_exceeds_minutes": 1,
            },
            "nonlinear_simulation": {
                "enabled": False,
                "run_only_for_top_n": 0,
            },
            "reporting": reporting,
        }
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return config_path

    def _run_cli(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "didgeridoo_optimizer.pipeline.run_optimizer", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )

    def test_cli_help_exits_successfully(self) -> None:
        result = self._run_cli("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config", result.stdout)
        self.assertIn("--dry-run", result.stdout)
        self.assertNotIn("RuntimeWarning", result.stderr)

    def test_missing_config_returns_nonzero_with_useful_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_config = Path(tmp_dir) / "missing.yaml"

            result = self._run_cli("--config", str(missing_config), "--dry-run")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Config file not found", result.stderr)

    def test_dry_run_rejects_non_mapping_yaml_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / "not_a_mapping.yaml"
            config_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

            result = self._run_cli("--config", str(config_path), "--dry-run")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Config YAML must be a mapping", result.stderr)
            self.assertFalse((tmp_path / "optimizer_summary.json").exists())

    def test_dry_run_valid_config_succeeds_without_optimizer_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            output_dir = tmp_path / "dry_run_results"
            config_path = self._write_config(tmp_path, output_dir=output_dir)

            result = self._run_cli("--config", str(config_path), "--dry-run")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], run_optimizer.CLI_PAYLOAD_SCHEMA_VERSION)
            self.assertEqual(payload["payload_type"], "dry_run")
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["config_schema_version"], run_optimizer.CONFIG_SCHEMA_VERSION)
            self.assertEqual(payload["config_schema_status"], run_optimizer.CONFIG_SCHEMA_STATUS_MISSING_ASSUMED_V1)
            self.assertEqual(Path(payload["output_dir"]), output_dir.resolve())
            self.assertTrue(payload["materials"]["database_exists"])
            self.assertTrue(payload["materials"]["variant_rules_exists"])
            self.assertFalse(output_dir.exists())
            self.assertFalse((output_dir / "optimizer_summary.json").exists())

    def test_dry_run_missing_material_database_returns_payload_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            output_dir = tmp_path / "dry_run_results"
            config_path = tmp_path / "optimizer_config.yaml"
            config = {
                "project": {
                    "output_dir": str(output_dir),
                },
                "materials": {
                    "database_file": str(tmp_path / "missing_materials.yaml"),
                    "variant_rules_file": str(REPO_ROOT / "project_specs" / "wood_variant_rules_v1.yaml"),
                },
            }
            config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

            result = self._run_cli("--config", str(config_path), "--dry-run")

            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], run_optimizer.CLI_PAYLOAD_SCHEMA_VERSION)
            self.assertEqual(payload["payload_type"], "dry_run")
            self.assertFalse(payload["ok"])
            self.assertTrue(payload["dry_run"])
            self.assertFalse(payload["materials"]["database_exists"])
            self.assertTrue(
                any("Material database not found" in message for message in payload["errors"]),
                payload["errors"],
            )
            self.assertFalse(output_dir.exists())
            self.assertFalse((output_dir / "optimizer_summary.json").exists())

    def test_output_dir_override_is_reported_without_modifying_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            configured_output = tmp_path / "configured_results"
            override_output = tmp_path / "override_results"
            config_path = self._write_config(tmp_path, output_dir=configured_output)

            result = self._run_cli(
                "--config",
                str(config_path),
                "--output-dir",
                str(override_output),
                "--dry-run",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(Path(payload["output_dir"]), override_output.resolve())
            self.assertEqual(payload["output_dir_source"], "cli_override")

            config_after = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config_after["project"]["output_dir"], str(configured_output))

    def test_dry_run_explicit_config_schema_v1_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = self._write_config(tmp_path, include_schema_version=True)

            result = self._run_cli("--config", str(config_path), "--dry-run")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["config_schema_version"], run_optimizer.CONFIG_SCHEMA_VERSION)
            self.assertEqual(payload["config_schema_status"], run_optimizer.CONFIG_SCHEMA_STATUS_EXPLICIT)

    def test_dry_run_unsupported_config_schema_returns_useful_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = self._write_config(
                tmp_path,
                include_schema_version=True,
                schema_version="dcalc.optimizer.config.v999",
            )

            result = self._run_cli("--config", str(config_path), "--dry-run")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unsupported config schema_version", result.stderr)
            self.assertIn(run_optimizer.CONFIG_SCHEMA_VERSION, result.stderr)

    def test_full_cli_tiny_smoke_exports_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = self._write_tiny_smoke_config(tmp_path)
            env = dict(os.environ)
            env["MPLCONFIGDIR"] = str(tmp_path / "mplconfig")

            result = self._run_cli(
                "--config",
                str(config_path),
                "--output-dir",
                "output",
                env=env,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["dry_run"])
            self.assertEqual(payload["payload_type"], "run_summary")
            self.assertEqual(payload["schema_version"], run_optimizer.CLI_PAYLOAD_SCHEMA_VERSION)
            self.assertEqual(payload["config_schema_version"], run_optimizer.CONFIG_SCHEMA_VERSION)

            output_dir = Path(payload["output_dir"]).resolve()
            self.assertEqual(output_dir, (tmp_path / "output").resolve())
            output_dir.relative_to(tmp_path.resolve())

            expected_files = [
                output_dir / "optimizer_summary.json",
                output_dir / "optimizer_summary.yaml",
                output_dir / "top20_scores.csv",
                output_dir / "best_design" / "best_design_summary.txt",
                output_dir / "best_design" / "best_design_result.json",
                output_dir / "best_design" / "best_design_result.yaml",
                output_dir / "best_design" / "best_design_impedance.png",
                output_dir / "best_design" / "best_design_radiation.png",
            ]
            for path in expected_files:
                with self.subTest(path=path.name):
                    self.assertTrue(path.exists(), f"Expected export not found: {path}")
                    path.resolve().relative_to(output_dir)

            summary = json.loads((output_dir / "optimizer_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["schema_version"], run_optimizer.REPORT_SCHEMA_VERSION)
            self.assertEqual(summary["config_schema_version"], run_optimizer.CONFIG_SCHEMA_VERSION)
            self.assertEqual(summary["nonlinear_results"]["selected_count"], 0)
            self.assertFalse((output_dir / "pareto_overview.png").exists())

    def test_full_cli_can_skip_best_design_pngs_without_disabling_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = self._write_tiny_smoke_config(
                tmp_path,
                save_plots=True,
                save_best_design_plots=False,
            )
            env = dict(os.environ)
            env["MPLCONFIGDIR"] = str(tmp_path / "mplconfig")

            result = self._run_cli(
                "--config",
                str(config_path),
                "--output-dir",
                "output",
                env=env,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["payload_type"], "run_summary")

            output_dir = Path(payload["output_dir"]).resolve()
            output_dir.relative_to(tmp_path.resolve())
            best_dir = output_dir / "best_design"
            expected_bundle_files = [
                best_dir / "best_design_summary.txt",
                best_dir / "best_design_result.json",
                best_dir / "best_design_result.yaml",
            ]
            for path in expected_bundle_files:
                with self.subTest(path=path.name):
                    self.assertTrue(path.exists(), f"Expected best-design bundle file not found: {path}")
                    path.resolve().relative_to(output_dir)

            self.assertTrue((output_dir / "pareto_overview.png").exists())
            self.assertFalse((best_dir / "best_design_impedance.png").exists())
            self.assertFalse((best_dir / "best_design_radiation.png").exists())

            bundle_exports = payload["exports"]["best_design_bundle"]
            self.assertIn("summary_txt", bundle_exports)
            self.assertIn("result_json", bundle_exports)
            self.assertIn("result_yaml", bundle_exports)
            self.assertNotIn("impedance_plot", bundle_exports)
            self.assertNotIn("radiation_plot", bundle_exports)

    def test_existing_run_callable_remains_importable_without_cli_args(self) -> None:
        self.assertTrue(callable(run_optimizer.run))
        with mock.patch.object(run_optimizer.OptimizerRunner, "run", return_value={"ok": True}) as mocked_run:
            self.assertEqual(run_optimizer.run("config.yaml"), {"ok": True})

        mocked_run.assert_called_once_with("config.yaml")

    def test_pipeline_package_run_exports_remain_available(self) -> None:
        import didgeridoo_optimizer.pipeline as pipeline

        self.assertTrue(callable(pipeline.run))
        self.assertTrue(callable(pipeline.OptimizerRunner))

    def test_run_summary_payload_includes_schema_metadata(self) -> None:
        payload = run_optimizer._run_cli_payload(
            {
                "config_schema_version": run_optimizer.CONFIG_SCHEMA_VERSION,
                "config_schema_status": run_optimizer.CONFIG_SCHEMA_STATUS_EXPLICIT,
                "exports": {"output_dir": "results"},
                "warnings": [],
            }
        )

        self.assertEqual(payload["schema_version"], run_optimizer.CLI_PAYLOAD_SCHEMA_VERSION)
        self.assertEqual(payload["payload_type"], "run_summary")
        self.assertEqual(payload["config_schema_version"], run_optimizer.CONFIG_SCHEMA_VERSION)
        self.assertEqual(payload["config_schema_status"], run_optimizer.CONFIG_SCHEMA_STATUS_EXPLICIT)

    def test_report_summary_payload_includes_schema_metadata(self) -> None:
        payload = run_optimizer.OptimizerRunner()._build_summary_payload(
            config={},
            context={},
            runtime_info={},
            runtime_actual_seconds=0.0,
            linear_results={},
            robust_results={},
            nonlinear_results={},
            best_candidate={},
            ranked_top_n=[],
        )

        self.assertEqual(payload["schema_version"], run_optimizer.REPORT_SCHEMA_VERSION)
        self.assertEqual(payload["config_schema_version"], run_optimizer.CONFIG_SCHEMA_VERSION)
        self.assertEqual(payload["config_schema_status"], run_optimizer.CONFIG_SCHEMA_STATUS_MISSING_ASSUMED_V1)

    def test_finalize_payload_covers_minimal_report_v1_contract(self) -> None:
        runner = run_optimizer.OptimizerRunner()
        selector = mock.Mock()
        selector.select_best.return_value = {}
        selector.rank_top_n.return_value = []
        config = {
            "optimization": {
                "final_output_count": 1,
                "final_selector": "weighted_sum",
            }
        }
        context = {
            "selector": selector,
            "config_schema_version": run_optimizer.CONFIG_SCHEMA_VERSION,
            "config_schema_status": run_optimizer.CONFIG_SCHEMA_STATUS_EXPLICIT,
        }
        runtime_info = {"warnings": ["runtime warning"], "estimated_seconds": 0.0}
        linear_results = {"ranked": [], "evaluation_count": 0}
        robust_results = {"ranked": [], "selected_count": 0}
        nonlinear_results = {"ranked": [], "selected_count": 0}

        with mock.patch.object(runner, "_export_results", return_value={"output_dir": "results"}) as export_mock:
            payload = runner.finalize(
                linear_results=linear_results,
                robust_results=robust_results,
                nonlinear_results=nonlinear_results,
                runtime_info=runtime_info,
                config=config,
                context=context,
                runtime_actual_seconds=1.25,
            )

        stable_top_level_keys = {
            "config",
            "runtime_estimate",
            "runtime_actual_seconds",
            "linear_results",
            "robust_results",
            "nonlinear_results",
            "best_design",
            "top_20",
            "warnings",
            "exports",
        }
        self.assertEqual(payload["schema_version"], run_optimizer.REPORT_SCHEMA_VERSION)
        self.assertEqual(payload["config_schema_version"], run_optimizer.CONFIG_SCHEMA_VERSION)
        self.assertEqual(payload["config_schema_status"], run_optimizer.CONFIG_SCHEMA_STATUS_EXPLICIT)
        self.assertTrue(stable_top_level_keys.issubset(payload.keys()))
        self.assertIsInstance(payload["warnings"], list)
        self.assertIsInstance(payload["exports"], dict)
        self.assertIsInstance(payload["runtime_actual_seconds"], (float, int))
        export_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
