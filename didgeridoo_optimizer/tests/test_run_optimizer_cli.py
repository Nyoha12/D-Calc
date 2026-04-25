from __future__ import annotations

import json
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
    def _write_config(self, tmp_path: Path, output_dir: Path | None = None) -> Path:
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
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return config_path

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "didgeridoo_optimizer.pipeline.run_optimizer", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
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

    def test_dry_run_valid_config_succeeds_without_optimizer_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            output_dir = tmp_path / "dry_run_results"
            config_path = self._write_config(tmp_path, output_dir=output_dir)

            result = self._run_cli("--config", str(config_path), "--dry-run")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["dry_run"])
            self.assertEqual(Path(payload["output_dir"]), output_dir.resolve())
            self.assertTrue(payload["materials"]["database_exists"])
            self.assertTrue(payload["materials"]["variant_rules_exists"])
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

    def test_existing_run_callable_remains_importable_without_cli_args(self) -> None:
        self.assertTrue(callable(run_optimizer.run))
        with mock.patch.object(run_optimizer.OptimizerRunner, "run", return_value={"ok": True}) as mocked_run:
            self.assertEqual(run_optimizer.run("config.yaml"), {"ok": True})

        mocked_run.assert_called_once_with("config.yaml")

    def test_pipeline_package_run_exports_remain_available(self) -> None:
        import didgeridoo_optimizer.pipeline as pipeline

        self.assertTrue(callable(pipeline.run))
        self.assertTrue(callable(pipeline.OptimizerRunner))


if __name__ == "__main__":
    unittest.main()
