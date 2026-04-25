from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from collections.abc import Mapping as MappingABC
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

import yaml

from ..materials import MaterialDatabase
from ..optimization import FinalSelector, ParetoOptimizer, RuntimeEstimator, SearchSpace, aggregate_score
from ..reporting.export import export_best_design_bundle, export_csv_scores, export_json, export_yaml
from ..reporting.plots import plot_pareto
from ..reporting.ranking import rank
from ..pipeline.evaluate_linear import LinearEvaluationPipeline
from ..pipeline.evaluate_nonlinear import NonlinearPipeline
from ..pipeline.evaluate_robustness import RobustnessPipeline


class OptimizerRunner:
    def load_context(self, config_path: str | Path, *, output_dir_override: str | Path | None = None) -> dict[str, Any]:
        config_file, config = self._load_config_file(config_path)
        config = self._apply_output_dir_override(config, output_dir_override)

        materials_cfg = self._config_section(config, "materials")
        materials_path = self._resolve_path(config_file, materials_cfg.get("database_file", "materials_base_v1.yaml"))
        variant_rules_path = self._resolve_path(config_file, materials_cfg.get("variant_rules_file", "wood_variant_rules_v1.yaml"))
        output_dir = self._resolve_output_dir(config_file, config, create=True)

        material_db = MaterialDatabase.from_yaml(materials_path, variant_rules_path=variant_rules_path)
        linear_pipeline = LinearEvaluationPipeline()
        robustness_pipeline = RobustnessPipeline(materials_path=materials_path)
        nonlinear_pipeline = NonlinearPipeline()
        runtime_estimator = RuntimeEstimator()
        search_space = SearchSpace(config, material_db)
        pareto = ParetoOptimizer()
        selector = FinalSelector()

        return {
            "config_path": config_file,
            "config": config,
            "materials_path": materials_path,
            "variant_rules_path": variant_rules_path,
            "output_dir": output_dir,
            "material_db": material_db,
            "linear_pipeline": linear_pipeline,
            "robustness_pipeline": robustness_pipeline,
            "nonlinear_pipeline": nonlinear_pipeline,
            "runtime_estimator": runtime_estimator,
            "search_space": search_space,
            "pareto": pareto,
            "selector": selector,
        }

    def estimate_runtime(self, config: Mapping[str, Any], linear_pipeline: LinearEvaluationPipeline, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        estimator = RuntimeEstimator()
        return estimator.combined_estimate(config, linear_pipeline)

    def run_linear_phase(self, config: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        search_space: SearchSpace = context["search_space"]
        pareto: ParetoOptimizer = context["pareto"]
        linear_pipeline: LinearEvaluationPipeline = context["linear_pipeline"]
        materials = context["material_db"]

        def evaluator(genome: Mapping[str, Any]) -> dict[str, Any]:
            design = search_space.decode(genome)
            return linear_pipeline.evaluate(design, config, materials)

        pareto_run = pareto.run(evaluator=evaluator, search_space=search_space, config=config)
        ranked = rank(pareto_run["evaluated"], config)
        keep_n = int(dict((config or {}).get("optimization", {}) or {}).get("keep_top_n_linear", len(ranked)))
        return {
            "evaluated": pareto_run["evaluated"],
            "pareto_front": pareto_run["pareto_front"],
            "ranked": ranked[: max(0, keep_n)],
            "evaluation_count": int(pareto_run.get("evaluation_count", 0)),
            "generations_completed": int(pareto_run.get("generations_completed", 0)),
        }

    def run_robustness_phase(
        self,
        ranked_linear: Sequence[Mapping[str, Any]],
        config: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        robustness_pipeline: RobustnessPipeline = context["robustness_pipeline"]
        materials = context["material_db"]
        top_n = int(dict((config or {}).get("optimization", {}) or {}).get("top_n_for_robustness", len(ranked_linear)))
        selected = list(ranked_linear[: max(0, top_n)])
        updated_candidates: list[dict[str, Any]] = []
        for candidate in selected:
            robust_result = robustness_pipeline.evaluate(candidate["result"], config, materials)
            robust_result = self._refresh_aggregate_score(robust_result, config)
            updated_candidates.append(self._candidate_with_updated_result(candidate, robust_result))
        ranked = rank(updated_candidates, config)
        return {
            "selected_count": len(selected),
            "evaluated": updated_candidates,
            "ranked": ranked,
        }

    def run_nonlinear_phase(
        self,
        ranked_robust: Sequence[Mapping[str, Any]],
        config: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        nonlinear_pipeline: NonlinearPipeline = context["nonlinear_pipeline"]
        materials_path = context["materials_path"]
        nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
        if not bool(nonlinear_cfg.get("enabled", True)):
            return {"selected_count": 0, "evaluated": [], "ranked": []}

        top_n = int(nonlinear_cfg.get("run_only_for_top_n", dict((config or {}).get("optimization", {}) or {}).get("top_n_for_nonlinear", len(ranked_robust))))
        selected = list(ranked_robust[: max(0, top_n)])
        updated_candidates: list[dict[str, Any]] = []
        for candidate in selected:
            nonlinear_result = nonlinear_pipeline.evaluate(candidate["result"], config, materials_path)
            nonlinear_result = self._refresh_aggregate_score(nonlinear_result, config)
            updated_candidates.append(self._candidate_with_updated_result(candidate, nonlinear_result))
        ranked = rank(updated_candidates, config)
        return {
            "selected_count": len(selected),
            "evaluated": updated_candidates,
            "ranked": ranked,
        }

    def finalize(
        self,
        linear_results: Mapping[str, Any],
        robust_results: Mapping[str, Any],
        nonlinear_results: Mapping[str, Any],
        runtime_info: Mapping[str, Any],
        config: Mapping[str, Any],
        context: Mapping[str, Any],
        runtime_actual_seconds: float,
    ) -> dict[str, Any]:
        merged_candidates = self._merge_progressive_candidates(
            linear_results.get("ranked", []),
            robust_results.get("ranked", []),
            nonlinear_results.get("ranked", []),
        )
        ranked_final = rank(merged_candidates, config)
        selector: FinalSelector = context["selector"]
        selector_method = str(dict((config or {}).get("optimization", {}) or {}).get("final_selector", "knee"))
        best_candidate = selector.select_best(ranked_final, selector_method, config)
        final_output_count = int(dict((config or {}).get("optimization", {}) or {}).get("final_output_count", 20))
        top_n = selector.rank_top_n(ranked_final, final_output_count, selector_method, config)
        ranked_top_n = rank(top_n, config)
        exports = self._export_results(config, context, runtime_info, runtime_actual_seconds, linear_results, robust_results, nonlinear_results, best_candidate, ranked_top_n)

        warnings: list[str] = list(runtime_info.get("warnings", []))
        if best_candidate:
            warnings.extend(best_candidate.get("result", best_candidate).get("warnings", []))
        warnings = list(dict.fromkeys(str(item) for item in warnings))

        return {
            "config": dict(config),
            "runtime_estimate": dict(runtime_info),
            "runtime_actual_seconds": float(runtime_actual_seconds),
            "linear_results": linear_results,
            "robust_results": robust_results,
            "nonlinear_results": nonlinear_results,
            "best_design": best_candidate,
            "top_20": ranked_top_n,
            "warnings": warnings,
            "exports": exports,
        }

    def run(self, config_path: str | Path, *, output_dir_override: str | Path | None = None) -> dict[str, Any]:
        context = self.load_context(config_path, output_dir_override=output_dir_override)
        config = context["config"]
        started = time.perf_counter()
        runtime_info = self.estimate_runtime(config, context["linear_pipeline"], context)
        linear_results = self.run_linear_phase(config, context)
        robust_results = self.run_robustness_phase(linear_results.get("ranked", []), config, context)
        nonlinear_source = robust_results.get("ranked", []) or linear_results.get("ranked", [])
        nonlinear_results = self.run_nonlinear_phase(nonlinear_source, config, context)
        runtime_actual_seconds = time.perf_counter() - started
        return self.finalize(
            linear_results=linear_results,
            robust_results=robust_results,
            nonlinear_results=nonlinear_results,
            runtime_info=runtime_info,
            config=config,
            context=context,
            runtime_actual_seconds=runtime_actual_seconds,
        )

    def dry_run(self, config_path: str | Path, *, output_dir_override: str | Path | None = None) -> dict[str, Any]:
        config_file, config = self._load_config_file(config_path)
        config = self._apply_output_dir_override(config, output_dir_override)
        materials_cfg = self._config_section(config, "materials")

        database_file = materials_cfg.get("database_file", "materials_base_v1.yaml")
        materials_path = self._resolve_path(config_file, database_file)
        materials_exists = materials_path.exists()

        variant_rules_configured = "variant_rules_file" in materials_cfg
        variant_rules_file = materials_cfg.get("variant_rules_file", "wood_variant_rules_v1.yaml")
        variant_rules_path = self._resolve_path(config_file, variant_rules_file)
        variant_rules_exists = variant_rules_path.exists()

        output_dir = self._resolve_output_dir(config_file, config, create=False)

        errors: list[str] = []
        warnings: list[str] = []
        if not materials_exists:
            errors.append(f"Material database not found: {materials_path}")
        if not variant_rules_exists:
            warnings.append(f"Variant rules file absent: {variant_rules_path}")

        return {
            "ok": not errors,
            "dry_run": True,
            "config_path": str(config_file.resolve()),
            "materials": {
                "database_file": str(database_file),
                "database_path": str(materials_path),
                "database_exists": materials_exists,
                "variant_rules_file": str(variant_rules_file),
                "variant_rules_configured": variant_rules_configured,
                "variant_rules_path": str(variant_rules_path),
                "variant_rules_exists": variant_rules_exists,
            },
            "output_dir": str(output_dir),
            "output_dir_exists": output_dir.exists(),
            "output_dir_created": False,
            "output_dir_source": "cli_override" if output_dir_override is not None else "config_or_default",
            "errors": errors,
            "warnings": warnings,
        }

    def _load_config_file(self, config_path: str | Path) -> tuple[Path, dict[str, Any]]:
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {config_file}")
        if not config_file.is_file():
            raise IsADirectoryError(f"Config path is not a file: {config_file}")

        with open(config_file, "r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
        if loaded is None:
            return config_file, {}
        if not isinstance(loaded, MappingABC):
            raise TypeError(f"Config YAML must be a mapping: {config_file}")
        return config_file, dict(loaded)

    def _apply_output_dir_override(self, config: Mapping[str, Any], output_dir_override: str | Path | None) -> dict[str, Any]:
        merged = copy.deepcopy(dict(config))
        if output_dir_override is None:
            return merged

        project_cfg = dict(merged.get("project", {}) or {})
        project_cfg["output_dir"] = str(output_dir_override)
        merged["project"] = project_cfg
        return merged

    def _config_section(self, config: Mapping[str, Any], section_name: str) -> dict[str, Any]:
        section = config.get(section_name, {}) or {}
        if not isinstance(section, MappingABC):
            raise TypeError(f"Config section {section_name!r} must be a mapping.")
        return dict(section)

    def _resolve_output_dir(self, config_file: Path, config: Mapping[str, Any], *, create: bool = True) -> Path:
        project_cfg = self._config_section(config, "project")
        output_dir = Path(str(project_cfg.get("output_dir", "./results")))
        if output_dir.is_absolute():
            resolved = output_dir.resolve()
            if create:
                resolved.mkdir(parents=True, exist_ok=True)
            return resolved
        resolved = (config_file.parent / output_dir).resolve()
        if create:
            resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _resolve_path(self, config_file: Path, maybe_path: str | Path) -> Path:
        candidate = Path(maybe_path)
        candidates = [candidate]
        if not candidate.is_absolute():
            candidates.extend([config_file.parent / candidate, Path("/mnt/data") / candidate.name])
        for item in candidates:
            if item.exists():
                return item.resolve()
        return (config_file.parent / candidate).resolve() if not candidate.is_absolute() else candidate

    def _candidate_with_updated_result(self, candidate: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(candidate)
        out["result"] = dict(result)
        out["aggregate_score"] = float(result.get("aggregate_score", out.get("aggregate_score", float("-inf"))))
        out["valid"] = bool(result.get("valid", out.get("valid", False)))
        objective_scores = dict(result.get("objective_scores", {}) or {})
        out["normalized_objectives"] = {name: self._clip01(float(score)) for name, score in objective_scores.items()}
        return out

    def _refresh_aggregate_score(self, result: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        refreshed = dict(result)
        penalties_map = dict(refreshed.get("penalties", {}) or {})
        if penalties_map and "total_penalty" not in penalties_map:
            penalties_map["total_penalty"] = float(sum(float(v) for v in penalties_map.values()))
        refreshed["penalties"] = penalties_map
        refreshed["aggregate_score"] = float(
            aggregate_score(
                dict(refreshed.get("objective_scores", {}) or {}),
                penalties_map,
                config,
            )
        )
        return refreshed

    def _merge_progressive_candidates(self, *candidate_groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for group in candidate_groups:
            for candidate in group:
                key = self._candidate_key(candidate)
                merged[key] = dict(candidate)
        return list(merged.values())

    def _candidate_key(self, candidate: Mapping[str, Any]) -> str:
        genome = dict(candidate.get("genome", {}) or {})
        result = dict(candidate.get("result", {}) or {})
        return str(genome.get("id") or result.get("design_id") or id(candidate))

    def _export_results(
        self,
        config: Mapping[str, Any],
        context: Mapping[str, Any],
        runtime_info: Mapping[str, Any],
        runtime_actual_seconds: float,
        linear_results: Mapping[str, Any],
        robust_results: Mapping[str, Any],
        nonlinear_results: Mapping[str, Any],
        best_candidate: Mapping[str, Any],
        ranked_top_n: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        output_dir: Path = context["output_dir"]
        reporting_cfg = dict((config or {}).get("reporting", {}) or {})
        exports: dict[str, Any] = {"output_dir": str(output_dir)}

        final_payload = {
            "config": dict(config),
            "runtime_estimate": dict(runtime_info),
            "runtime_actual_seconds": float(runtime_actual_seconds),
            "linear_results": self._lighten_results(linear_results),
            "robust_results": self._lighten_results(robust_results),
            "nonlinear_results": self._lighten_results(nonlinear_results),
            "best_design": self._lighten_results(best_candidate),
            "top_20": self._lighten_results(ranked_top_n),
        }

        if bool(reporting_cfg.get("save_json_summary", True)):
            exports["summary_json"] = str(export_json(final_payload, output_dir / "optimizer_summary.json"))
        if bool(reporting_cfg.get("save_yaml_summary", True)):
            exports["summary_yaml"] = str(export_yaml(final_payload, output_dir / "optimizer_summary.yaml"))
        if bool(reporting_cfg.get("save_csv_scores", True)):
            exports["top20_csv"] = str(export_csv_scores(ranked_top_n, output_dir / "top20_scores.csv"))
        if bool(reporting_cfg.get("save_plots", True)):
            exports["pareto_plot"] = str(plot_pareto(ranked_top_n, output_dir / "pareto_overview.png"))
        if best_candidate:
            exports["best_design_bundle"] = export_best_design_bundle(best_candidate, output_dir / "best_design")
        return exports

    def _lighten_results(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            if {"freq_hz", "zin", "zin_mag"} & set(value.keys()):
                reduced = dict(value)
                reduced.pop("zin", None)
                reduced.pop("zin_mag", None)
                reduced.pop("freq_hz", None)
                return {k: self._lighten_results(v) for k, v in reduced.items()}
            return {k: self._lighten_results(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._lighten_results(item) for item in value]
        return value

    def _clip01(self, value: float) -> float:
        return max(0.0, min(1.0, value))


def run(config_path: str | Path) -> dict[str, Any]:
    return OptimizerRunner().run(config_path)


def load_context(config_path: str | Path) -> dict[str, Any]:
    return OptimizerRunner().load_context(config_path)


def estimate_runtime(config: Mapping[str, Any], linear_pipeline: LinearEvaluationPipeline) -> dict[str, Any]:
    return OptimizerRunner().estimate_runtime(config, linear_pipeline)


def run_linear_phase(config: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    return OptimizerRunner().run_linear_phase(config, context)


def run_robustness_phase(
    ranked_linear: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    return OptimizerRunner().run_robustness_phase(ranked_linear, config, context)


def run_nonlinear_phase(
    ranked_robust: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    return OptimizerRunner().run_nonlinear_phase(ranked_robust, config, context)


def finalize(
    linear_results: Mapping[str, Any],
    robust_results: Mapping[str, Any],
    nonlinear_results: Mapping[str, Any],
    runtime_info: Mapping[str, Any],
    config: Mapping[str, Any],
    context: Mapping[str, Any],
    runtime_actual_seconds: float,
) -> dict[str, Any]:
    return OptimizerRunner().finalize(
        linear_results=linear_results,
        robust_results=robust_results,
        nonlinear_results=nonlinear_results,
        runtime_info=runtime_info,
        config=config,
        context=context,
        runtime_actual_seconds=runtime_actual_seconds,
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m didgeridoo_optimizer.pipeline.run_optimizer",
        description="Run the D-Calc optimizer from an explicit YAML config path.",
    )
    parser.add_argument("--config", required=True, help="Path to the optimizer YAML config.")
    parser.add_argument(
        "--output-dir",
        help="Optional output directory override. Relative paths are resolved like project.output_dir.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config/path resolution without running optimization or writing optimizer artifacts.",
    )
    return parser


def _best_design_id(summary: Mapping[str, Any]) -> Any:
    best = dict(summary.get("best_design", {}) or {})
    result = dict(best.get("result", best) or {})
    return result.get("design_id")


def _run_cli_payload(summary: Mapping[str, Any]) -> dict[str, Any]:
    exports = dict(summary.get("exports", {}) or {})
    return {
        "ok": True,
        "dry_run": False,
        "best_design_id": _best_design_id(summary),
        "output_dir": exports.get("output_dir"),
        "exports": exports,
        "warnings": list(summary.get("warnings", []) or []),
    }


def _print_json(payload: Mapping[str, Any], stream: TextIO) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True), file=stream)


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = _build_argument_parser()
    args = parser.parse_args(argv)
    runner = OptimizerRunner()

    try:
        if args.dry_run:
            payload = runner.dry_run(args.config, output_dir_override=args.output_dir)
            _print_json(payload, out)
            return 0 if bool(payload.get("ok")) else 1

        summary = runner.run(args.config, output_dir_override=args.output_dir)
        _print_json(_run_cli_payload(summary), out)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=err)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
