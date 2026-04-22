from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ..tests.validation_cases import validation_cases


class RuntimeEstimator:
    """Estimate optimizer runtime from config and optional microbenchmarking."""

    def analytical_estimate(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        cfg = dict(config or {})
        opt_cfg = dict(cfg.get("optimization", {}) or {})
        rt_cfg = dict(cfg.get("runtime_estimation", {}) or {})
        freq_cfg = dict(cfg.get("frequency_analysis", {}) or {})
        geom_cfg = dict(cfg.get("geometry_constraints", {}) or {})
        topology_cfg = dict(cfg.get("topology", {}) or {})
        materials_cfg = dict(cfg.get("materials", {}) or {})
        nonlinear_cfg = dict(cfg.get("nonlinear_simulation", {}) or {})

        population = int(opt_cfg.get("random_initial_population", 200))
        generations = int(opt_cfg.get("generations", 100))
        linear_budget = int(opt_cfg.get("linear_budget", 0) or 0)
        n_eval = linear_budget if linear_budget > 0 else population * max(generations + 1, 1)
        n_f = int(freq_cfg.get("n_points", 4096))

        body_cfg = dict(geom_cfg.get("body_segments", {}) or {})
        min_body = max(1, int(body_cfg.get("min_count", 1)))
        max_body = max(min_body, int(body_cfg.get("max_count", min_body)))
        estimated_body_segments = min(max_body, max(min_body, int(round(0.5 * (min_body + max_body)))))
        expected_bell = 1 if bool(topology_cfg.get("allow_bell", True)) else 0
        n_seg_eff = estimated_body_segments + expected_bell

        c_topology = 1.0
        if bool(topology_cfg.get("allow_bell", False)):
            c_topology += 0.25
        if bool(topology_cfg.get("allow_branches", False)):
            c_topology += 0.75
        if bool(topology_cfg.get("allow_helmholtz", False)):
            c_topology += 0.5
        if max_body > 6:
            c_topology += 0.15

        max_materials = max(1, int(materials_cfg.get("max_distinct_materials_per_design", 1)))
        allowed_materials = list(materials_cfg.get("allowed_materials", []) or [])
        c_materials = 1.0 + 0.15 * (max_materials - 1) + min(0.4, len(allowed_materials) / 200.0)

        # Calibrated only as MVP runtime heuristic, not as a measured constant.
        base_linear_work = n_eval * n_f * n_seg_eff * c_topology * c_materials
        linear_seconds = base_linear_work * 8.0e-7

        n_top = int(opt_cfg.get("top_n_for_robustness", 0) or 0)
        uncertainty_cfg = dict(cfg.get("uncertainty_management", {}) or {})
        sensitivity_cfg = dict(uncertainty_cfg.get("sensitivity_analysis", {}) or {})
        n_tract = 3 if bool(dict(cfg.get("player_model", {}) or {}).get("include_vocal_tract", False)) else 1
        n_beginner_expert = 2
        n_matvar = 3 if bool(uncertainty_cfg.get("enabled", False)) else 1
        monte_carlo_samples = int(sensitivity_cfg.get("monte_carlo_samples", 0) or 0)
        robustness_seconds = 0.0
        if n_top > 0:
            robustness_seconds = n_top * n_beginner_expert * n_tract * n_matvar * max(monte_carlo_samples / 200.0, 1.0) * 0.12

        nonlinear_seconds = 0.0
        if bool(nonlinear_cfg.get("enabled", False)):
            n_top_nonlinear = int(nonlinear_cfg.get("run_only_for_top_n", opt_cfg.get("top_n_for_nonlinear", 0)) or 0)
            pressure_scan_points = int(nonlinear_cfg.get("pressure_scan_points", 0) or 0)
            sample_rate = int(nonlinear_cfg.get("sample_rate_hz", 0) or 0)
            duration = float(nonlinear_cfg.get("simulation_duration_s", 0.0) or 0.0)
            nonlinear_seconds = n_top_nonlinear * pressure_scan_points * sample_rate * duration * 1.2e-6

        warnings: list[str] = []
        if n_f >= 4096:
            warnings.append("high_frequency_grid_cost")
        if max_body >= 8:
            warnings.append("many_segments_cost")
        if max_materials >= 3 or len(allowed_materials) >= 20:
            warnings.append("multi_material_combinatorics_high")
        if c_topology >= 1.4:
            warnings.append("topology_complexity_high")
        if nonlinear_seconds >= 600.0:
            warnings.append("nonlinear_cost_high")

        expected_seconds = linear_seconds + robustness_seconds + nonlinear_seconds
        result = {
            "method": str(rt_cfg.get("method", "analytic_plus_microbenchmark")),
            "benchmark_ran": False,
            "low_seconds": float(expected_seconds * 0.6),
            "expected_seconds": float(expected_seconds),
            "high_seconds": float(expected_seconds * 1.8),
            "expected_minutes": float(expected_seconds / 60.0),
            "dominant_factors": self._dominant_factors(
                {
                    "linear": linear_seconds,
                    "robustness": robustness_seconds,
                    "nonlinear": nonlinear_seconds,
                }
            ),
            "warnings": warnings,
            "cost_factors": {
                "n_eval": n_eval,
                "n_f": n_f,
                "n_seg_eff": n_seg_eff,
                "c_topology": float(c_topology),
                "c_materials": float(c_materials),
                "n_top_robustness": n_top,
                "n_beginner_expert": n_beginner_expert,
                "n_tract": n_tract,
                "n_matvar": n_matvar,
            },
            "components_seconds": {
                "linear": float(linear_seconds),
                "robustness": float(robustness_seconds),
                "nonlinear": float(nonlinear_seconds),
            },
            "confidence": "medium",
        }
        warn_minutes = float(rt_cfg.get("warn_if_estimated_time_exceeds_minutes", 60.0))
        if result["expected_minutes"] > warn_minutes:
            result["warnings"] = list(result["warnings"]) + ["estimated_runtime_exceeds_warning_threshold"]
        return result

    def benchmark(self, config: Mapping[str, Any] | None, linear_pipeline: Any) -> dict[str, Any]:
        if linear_pipeline is None:
            return {"benchmark_ran": False, "reason": "no_linear_pipeline"}

        cfg = dict(config or {})
        rt_cfg = dict(cfg.get("runtime_estimation", {}) or {})
        sample_budget = max(1, int(rt_cfg.get("benchmark_samples_linear", 5)))
        materials_path = self._resolve_materials_path(cfg)
        if materials_path is None:
            return {"benchmark_ran": False, "reason": "materials_path_unresolved"}

        benchmark_designs = self._benchmark_designs(sample_budget)
        timings_ms: list[float] = []
        valid_count = 0
        for design in benchmark_designs:
            t0 = time.perf_counter()
            result = self._call_linear_pipeline(linear_pipeline, design, cfg, materials_path)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            timings_ms.append(elapsed_ms)
            if bool(result.get("valid", False)):
                valid_count += 1

        if not timings_ms:
            return {"benchmark_ran": False, "reason": "no_benchmark_designs"}

        mean_ms = statistics.fmean(timings_ms)
        median_ms = statistics.median(timings_ms)
        p90_ms = max(timings_ms) if len(timings_ms) < 10 else statistics.quantiles(timings_ms, n=10)[-1]
        return {
            "benchmark_ran": True,
            "samples": len(timings_ms),
            "mean_eval_ms": float(mean_ms),
            "median_eval_ms": float(median_ms),
            "p90_eval_ms": float(p90_ms),
            "valid_fraction": float(valid_count / len(timings_ms)),
            "materials_path": str(materials_path),
        }

    def combined_estimate(self, config: Mapping[str, Any] | None, linear_pipeline: Any) -> dict[str, Any]:
        estimate = self.analytical_estimate(config)
        benchmark_info = self.benchmark(config, linear_pipeline)
        estimate["benchmark"] = benchmark_info
        if not benchmark_info.get("benchmark_ran", False):
            return estimate

        mean_eval_ms = float(benchmark_info.get("mean_eval_ms", 0.0))
        n_eval = float(dict(estimate.get("cost_factors", {}) or {}).get("n_eval", 0.0))
        measured_linear_seconds = mean_eval_ms * n_eval / 1000.0
        components = dict(estimate.get("components_seconds", {}) or {})
        analytic_linear = float(components.get("linear", 0.0))
        blended_linear = 0.5 * analytic_linear + 0.5 * measured_linear_seconds
        components["linear_analytic"] = analytic_linear
        components["linear_benchmark"] = measured_linear_seconds
        components["linear"] = blended_linear
        total = blended_linear + float(components.get("robustness", 0.0)) + float(components.get("nonlinear", 0.0))
        estimate["components_seconds"] = components
        estimate["low_seconds"] = float(total * 0.7)
        estimate["expected_seconds"] = float(total)
        estimate["high_seconds"] = float(total * 1.5)
        estimate["expected_minutes"] = float(total / 60.0)
        estimate["benchmark_ran"] = True
        estimate["confidence"] = "medium_high" if benchmark_info.get("valid_fraction", 0.0) >= 0.8 else "medium"
        return estimate

    def _resolve_materials_path(self, config: Mapping[str, Any]) -> Path | None:
        materials_cfg = dict(config.get("materials", {}) or {})
        db_file = materials_cfg.get("database_file")
        if not db_file:
            return None
        candidate = Path(str(db_file))
        candidates = [candidate]
        if not candidate.is_absolute():
            candidates.extend([Path("/mnt/data") / candidate.name, Path.cwd() / candidate])
        for item in candidates:
            if item.exists():
                return item
        return None

    def _benchmark_designs(self, sample_budget: int) -> list[dict[str, Any]]:
        bundle = validation_cases()
        designs: list[dict[str, Any]] = []
        for case in ("A", "B", "C", "D", "E"):
            for design in bundle[case].values():
                designs.append(dict(design))
        return designs[:sample_budget]

    def _call_linear_pipeline(
        self,
        linear_pipeline: Any,
        design: Mapping[str, Any],
        config: Mapping[str, Any],
        materials_path: Path,
    ) -> Mapping[str, Any]:
        if callable(linear_pipeline) and not hasattr(linear_pipeline, "evaluate"):
            return linear_pipeline(design, config, materials_path)
        if hasattr(linear_pipeline, "evaluate"):
            return linear_pipeline.evaluate(design, config, materials_path)
        raise TypeError(f"Unsupported linear_pipeline type for benchmarking: {type(linear_pipeline)!r}")

    def _dominant_factors(self, components_seconds: Mapping[str, float]) -> list[str]:
        ranked = sorted(((name, float(value)) for name, value in components_seconds.items()), key=lambda item: item[1], reverse=True)
        return [name for name, value in ranked if value > 0.0]
