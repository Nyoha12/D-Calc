from __future__ import annotations

import copy
import json
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

import yaml

from ..optimization.calibration import TargetedCalibrationAdvisor
from ..optimization.sensitivity import SensitivityAnalyzer
from ..optimization.search_space import SearchSpace
from ..pipeline.evaluate_linear import LinearEvaluationPipeline
from ..tests.validation_runner import run_validation_bench
from .run_optimizer import OptimizerRunner


class CalibrationRunner:
    def __init__(self, optimizer_runner: OptimizerRunner | None = None) -> None:
        self.optimizer_runner = optimizer_runner or OptimizerRunner()

    def run(
        self,
        config_path: str | Path,
        optimizer_result: Mapping[str, Any] | None = None,
        *,
        top_n: int | None = None,
        export_results: bool = True,
    ) -> dict[str, Any]:
        context = self.optimizer_runner.load_context(config_path)
        config = context["config"]
        if optimizer_result is None:
            optimizer_result = self.optimizer_runner.run(config_path)

        ranked = list(optimizer_result.get("top_20", []) or ([] if not optimizer_result.get("best_design") else [optimizer_result["best_design"]]))
        if not ranked:
            raise ValueError("CalibrationRunner requires at least one ranked candidate or best_design.")

        target_n = int(top_n or dict((config or {}).get("optimization", {}) or {}).get("top_n_for_robustness", 5) or 5)
        selected = ranked[: max(1, target_n)]
        results = [dict(candidate.get("result", candidate) or {}) for candidate in selected]

        sensitivity_analyzer = SensitivityAnalyzer(context["linear_pipeline"])
        sensitivity_report = sensitivity_analyzer.analyze_candidates(results, config, context["material_db"])

        advisor = TargetedCalibrationAdvisor()
        prioritized = advisor.prioritize_materials(sensitivity_report, context["material_db"])
        proposals = advisor.propose_adjustments(prioritized, context["material_db"])
        proposal_patch = self._normalize_patch(dict(proposals.get("patch", {}) or {}))

        output = {
            "config_path": str(Path(config_path)),
            "selected_count": len(results),
            "selected_design_ids": [str(result.get("design_id", "unknown")) for result in results],
            "sensitivity_report": sensitivity_report,
            "prioritized_materials": prioritized,
            "proposals": proposals,
            "patch_proposal": proposal_patch,
            "patch_replayed": self._empty_patch(),
            "patch_accepted": self._empty_patch(),
            "patch_to_calibrate": proposal_patch,
        }
        if export_results:
            output["exports"] = self._export(output, context["output_dir"] / "calibration")
        return output

    def validate_material_directed_patch(
        self,
        config_path: str | Path,
        material_id: str,
        *,
        pool_size: int = 6,
        max_attempts: int = 24,
        top_n: int = 3,
        export_results: bool = True,
    ) -> dict[str, Any]:
        context = self.optimizer_runner.load_context(config_path)
        base_config = self._compact_linear_config(context["config"], seed=int(dict((context["config"] or {}).get("project", {}) or {}).get("random_seed", 42) or 42))
        directed_config = self._force_material_only(base_config, material_id)
        material_db = context["material_db"]
        validation = run_validation_bench(config_path=config_path, materials=material_db)

        pool = self._build_material_directed_pool(
            directed_config,
            material_db,
            material_id,
            pool_size=pool_size,
            max_attempts=max_attempts,
        )
        if not pool:
            raise ValueError(f"No valid candidates generated for material {material_id!r}.")

        selected = sorted(pool, key=lambda item: float(item.get("aggregate_score", float("-inf"))), reverse=True)[: max(1, top_n)]
        sensitivity_analyzer = SensitivityAnalyzer(context["linear_pipeline"])
        sensitivity_report = sensitivity_analyzer.analyze_candidates(selected, directed_config, material_db)
        advisor = TargetedCalibrationAdvisor()
        prioritized = advisor.prioritize_materials(sensitivity_report, material_db)
        proposals = advisor.propose_adjustments(prioritized, material_db, min_priority_score=0.0, max_relative_step=0.05)
        directed_patch = self._filter_patch_to_material(proposals.get("patch", {}), material_id)
        patched_db = material_db.clone_with_patch(directed_patch)
        patched_validation = run_validation_bench(config_path=config_path, materials=patched_db)

        baseline_scores = [float(result.get("aggregate_score", float("-inf"))) for result in pool]
        patched_scores = [
            float(context["linear_pipeline"].evaluate(result["design"], directed_config, patched_db).get("aggregate_score", float("-inf")))
            for result in pool
        ]
        deltas = [patched - base for patched, base in zip(patched_scores, baseline_scores)]
        improved_count = sum(delta > 0 for delta in deltas)
        worsened_count = sum(delta < 0 for delta in deltas)
        mean_delta = float(mean(deltas)) if deltas else 0.0
        decision = self._decide_local_patch(validation["all_passed"], patched_validation["all_passed"], mean_delta, improved_count, worsened_count)
        patch_tracking = self._patch_tracking(proposals.get("patch", {}), directed_patch, decision)

        output = {
            "config_path": str(Path(config_path)),
            "material_id": material_id,
            "pool_size": len(pool),
            "selected_count": len(selected),
            "validation_preserved": bool(validation["all_passed"] and patched_validation["all_passed"]),
            "baseline_validation": self._validation_summary(validation),
            "patched_validation": self._validation_summary(patched_validation),
            "prioritized_materials": prioritized,
            "proposals": proposals,
            "directed_patch": directed_patch,
            "patch_proposal": patch_tracking["proposal_patch"],
            "patch_replayed": patch_tracking["replayed_patch"],
            "patch_accepted": patch_tracking["accepted_patch"],
            "patch_to_calibrate": patch_tracking["patch_to_calibrate"],
            "deltas": {
                "mean_delta": mean_delta,
                "min_delta": min(deltas) if deltas else 0.0,
                "max_delta": max(deltas) if deltas else 0.0,
                "improved_count": improved_count,
                "worsened_count": worsened_count,
                "all_candidate_deltas": deltas,
            },
            "decision": decision,
            "patch_material_ids": patch_tracking["material_ids"],
        }
        if export_results:
            output["exports"] = self._export(output, context["output_dir"] / "calibration_material_directed")
        return output

    def _compact_linear_config(self, config: Mapping[str, Any], *, seed: int) -> dict[str, Any]:
        cfg = copy.deepcopy(dict(config or {}))
        cfg["project"] = dict(cfg.get("project", {}) or {})
        cfg["project"]["random_seed"] = int(seed)
        cfg["project"]["output_dir"] = str(cfg["project"].get("output_dir", "/mnt/data/results"))
        cfg["optimization"] = dict(cfg.get("optimization", {}) or {})
        cfg["optimization"]["random_initial_population"] = min(12, int(cfg["optimization"].get("random_initial_population", 200)))
        cfg["optimization"]["generations"] = 1
        cfg["optimization"]["linear_budget"] = min(12, int(cfg["optimization"].get("linear_budget", 12)))
        cfg["optimization"]["keep_top_n_linear"] = min(6, int(cfg["optimization"].get("keep_top_n_linear", 6)))
        cfg["optimization"]["top_n_for_robustness"] = 0
        cfg["optimization"]["top_n_for_nonlinear"] = 0
        cfg["nonlinear_simulation"] = dict(cfg.get("nonlinear_simulation", {}) or {})
        cfg["nonlinear_simulation"]["enabled"] = False
        cfg["reporting"] = dict(cfg.get("reporting", {}) or {})
        cfg["reporting"]["save_json_summary"] = False
        cfg["reporting"]["save_yaml_summary"] = False
        cfg["reporting"]["save_csv_scores"] = False
        cfg["reporting"]["save_plots"] = False
        cfg["frequency_analysis"] = dict(cfg.get("frequency_analysis", {}) or {})
        cfg["frequency_analysis"]["n_points"] = min(512, int(cfg["frequency_analysis"].get("n_points", 512)))
        return cfg

    def _force_material_only(self, config: Mapping[str, Any], material_id: str) -> dict[str, Any]:
        cfg = copy.deepcopy(dict(config or {}))
        cfg["materials"] = dict(cfg.get("materials", {}) or {})
        cfg["materials"]["allowed_materials"] = [material_id]
        cfg["materials"]["max_distinct_materials_per_design"] = 1
        return cfg

    def _build_material_directed_pool(
        self,
        config: Mapping[str, Any],
        material_db: Any,
        material_id: str,
        *,
        pool_size: int,
        max_attempts: int,
    ) -> list[dict[str, Any]]:
        search_space = SearchSpace(config, material_db)
        linear_pipeline = LinearEvaluationPipeline()
        seen: set[str] = set()
        pool: list[dict[str, Any]] = []
        for _ in range(max_attempts):
            genome = search_space.sample_random()
            design = search_space.decode(genome)
            if not any(segment.get("material_id") == material_id for segment in design.get("segments", [])):
                continue
            key = json.dumps(design, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            result = linear_pipeline.evaluate(design, config, material_db)
            if result.get("valid"):
                pool.append(result)
            if len(pool) >= pool_size:
                break
        return pool

    def _filter_patch_to_material(self, patch: Mapping[str, Any], material_id: str) -> dict[str, Any]:
        materials_patch = dict((patch or {}).get("materials", {}) or {})
        if material_id not in materials_patch:
            return {"materials": {}}
        return {"materials": {material_id: dict(materials_patch[material_id])}}

    def _validation_summary(self, report: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "all_passed": bool(report.get("all_passed", False)),
            "failed_cases": [name for name, case in dict(report.get("case_results", {}) or {}).items() if not bool(case.get("passed", False))],
        }

    def _decide_local_patch(self, baseline_ok: bool, patched_ok: bool, mean_delta: float, improved_count: int, worsened_count: int) -> str:
        if not baseline_ok or not patched_ok:
            return "reject"
        if mean_delta >= 1e-4 and improved_count > 0 and worsened_count == 0:
            return "accept_local_only"
        return "keep_as_to_calibrate"

    def _empty_patch(self) -> dict[str, Any]:
        return {"materials": {}}

    def _normalize_patch(self, patch: Mapping[str, Any] | None) -> dict[str, Any]:
        materials_patch = dict((patch or {}).get("materials", {}) or {})
        normalized_materials = {
            str(material_id): {str(key): float(value) for key, value in dict(updates or {}).items()}
            for material_id, updates in materials_patch.items()
        }
        return {"materials": normalized_materials}

    def _patch_material_ids(self, patch: Mapping[str, Any] | None) -> list[str]:
        return sorted(str(material_id) for material_id in dict((patch or {}).get("materials", {}) or {}).keys())

    def _is_patch_accepted(self, decision: str | None) -> bool:
        return decision in {"accept_local_only", "accept_family", "accept_weighted"}

    def _patch_tracking(
        self,
        proposal_patch: Mapping[str, Any] | None,
        replayed_patch: Mapping[str, Any] | None,
        decision: str | None,
    ) -> dict[str, Any]:
        normalized_proposal = self._normalize_patch(proposal_patch)
        normalized_replayed = self._normalize_patch(replayed_patch)
        accepted_patch = normalized_replayed if self._is_patch_accepted(decision) else self._empty_patch()
        to_calibrate_patch = normalized_replayed if decision == "keep_as_to_calibrate" else self._empty_patch()
        return {
            "proposal_patch": normalized_proposal,
            "replayed_patch": normalized_replayed,
            "accepted_patch": accepted_patch,
            "patch_to_calibrate": to_calibrate_patch,
            "material_ids": {
                "proposal": self._patch_material_ids(normalized_proposal),
                "replayed": self._patch_material_ids(normalized_replayed),
                "accepted": self._patch_material_ids(accepted_patch),
                "to_calibrate": self._patch_material_ids(to_calibrate_patch),
            },
        }

    def validate_material_family_patch(
        self,
        config_path: str | Path,
        material_id: str,
        *,
        pool_size: int = 10,
        max_attempts: int = 64,
        top_n: int = 5,
        export_results: bool = True,
    ) -> dict[str, Any]:
        context = self.optimizer_runner.load_context(config_path)
        seed = int(dict((context["config"] or {}).get("project", {}) or {}).get("random_seed", 42) or 42)
        base_config = self._compact_linear_config(context["config"], seed=seed)
        family_config = self._prefer_material_family_scope(base_config, context["material_db"], material_id)
        material_db = context["material_db"]
        validation = run_validation_bench(config_path=config_path, materials=material_db)

        pool = self._build_material_family_pool(family_config, material_db, material_id, pool_size=pool_size, max_attempts=max_attempts)
        if not pool:
            raise ValueError(f"No family-directed candidates generated for material {material_id!r}.")

        selected = sorted(pool, key=lambda item: float(item.get("aggregate_score", float("-inf"))), reverse=True)[: max(1, top_n)]
        sensitivity_analyzer = SensitivityAnalyzer(context["linear_pipeline"])
        sensitivity_report = sensitivity_analyzer.analyze_candidates(selected, family_config, material_db)
        advisor = TargetedCalibrationAdvisor()
        prioritized = advisor.prioritize_materials(sensitivity_report, material_db)
        proposals = advisor.propose_adjustments(prioritized, material_db, min_priority_score=0.0, max_relative_step=0.05)
        patch = self._filter_patch_to_material(proposals.get("patch", {}), material_id)
        patched_db = material_db.clone_with_patch(patch)
        patched_validation = run_validation_bench(config_path=config_path, materials=patched_db)

        deltas = self._score_deltas(context["linear_pipeline"], pool, family_config, patched_db)
        decision = self._decide_family_patch(validation["all_passed"], patched_validation["all_passed"], deltas["mean_delta"], deltas["improved_count"], deltas["worsened_count"])
        patch_tracking = self._patch_tracking(proposals.get("patch", {}), patch, decision)

        output = {
            "config_path": str(Path(config_path)),
            "material_id": material_id,
            "pool_size": len(pool),
            "selected_count": len(selected),
            "validation_preserved": bool(validation["all_passed"] and patched_validation["all_passed"]),
            "baseline_validation": self._validation_summary(validation),
            "patched_validation": self._validation_summary(patched_validation),
            "family_material_ids": list(dict((family_config.get("materials", {}) or {})).get("allowed_materials", [])),
            "prioritized_materials": prioritized,
            "proposals": proposals,
            "family_patch": patch,
            "patch_proposal": patch_tracking["proposal_patch"],
            "patch_replayed": patch_tracking["replayed_patch"],
            "patch_accepted": patch_tracking["accepted_patch"],
            "patch_to_calibrate": patch_tracking["patch_to_calibrate"],
            "pool_material_mix": [self._segment_material_ids(result.get("design", {})) for result in pool],
            "deltas": deltas,
            "decision": decision,
            "patch_material_ids": patch_tracking["material_ids"],
        }
        if export_results:
            output["exports"] = self._export(output, context["output_dir"] / "calibration_material_family")
        return output

    def validate_material_family_weighted_patch(
        self,
        config_path: str | Path,
        material_id: str,
        *,
        pool_size: int = 12,
        max_attempts: int = 80,
        top_n: int = 5,
        export_results: bool = True,
    ) -> dict[str, Any]:
        context = self.optimizer_runner.load_context(config_path)
        seed = int(dict((context["config"] or {}).get("project", {}) or {}).get("random_seed", 42) or 42)
        base_config = self._compact_linear_config(context["config"], seed=seed)
        weighted_config = self._prefer_material_family_weighted_scope(base_config, context["material_db"], material_id)
        material_db = context["material_db"]
        validation = run_validation_bench(config_path=config_path, materials=material_db)

        pool = self._build_material_family_weighted_pool(weighted_config, material_db, material_id, pool_size=pool_size, max_attempts=max_attempts)
        if not pool:
            raise ValueError(f"No family-weighted candidates generated for material {material_id!r}.")

        selected = sorted(pool, key=lambda item: float(item.get("aggregate_score", float("-inf"))), reverse=True)[: max(1, top_n)]
        sensitivity_analyzer = SensitivityAnalyzer(context["linear_pipeline"])
        sensitivity_report = sensitivity_analyzer.analyze_candidates(selected, weighted_config, material_db)
        advisor = TargetedCalibrationAdvisor()
        prioritized = advisor.prioritize_materials(sensitivity_report, material_db)
        proposals = advisor.propose_adjustments(prioritized, material_db, min_priority_score=0.0, max_relative_step=0.05)
        patch = self._filter_patch_to_material(proposals.get("patch", {}), material_id)
        patched_db = material_db.clone_with_patch(patch)
        patched_validation = run_validation_bench(config_path=config_path, materials=patched_db)

        deltas = self._score_deltas(context["linear_pipeline"], pool, weighted_config, patched_db)
        decision = self._decide_family_weighted_patch(validation["all_passed"], patched_validation["all_passed"], deltas["mean_delta"], deltas["improved_count"], deltas["worsened_count"])
        patch_tracking = self._patch_tracking(proposals.get("patch", {}), patch, decision)

        output = {
            "config_path": str(Path(config_path)),
            "material_id": material_id,
            "pool_size": len(pool),
            "selected_count": len(selected),
            "validation_preserved": bool(validation["all_passed"] and patched_validation["all_passed"]),
            "baseline_validation": self._validation_summary(validation),
            "patched_validation": self._validation_summary(patched_validation),
            "weighted_material_ids": list(dict((weighted_config.get("materials", {}) or {})).get("allowed_materials", [])),
            "prioritized_materials": prioritized,
            "proposals": proposals,
            "weighted_patch": patch,
            "patch_proposal": patch_tracking["proposal_patch"],
            "patch_replayed": patch_tracking["replayed_patch"],
            "patch_accepted": patch_tracking["accepted_patch"],
            "patch_to_calibrate": patch_tracking["patch_to_calibrate"],
            "pool_material_mix": [self._segment_material_ids(result.get("design", {})) for result in pool],
            "deltas": deltas,
            "decision": decision,
            "patch_material_ids": patch_tracking["material_ids"],
        }
        if export_results:
            output["exports"] = self._export(output, context["output_dir"] / "calibration_material_family_weighted")
        return output

    def _prefer_material_family_scope(self, config: Mapping[str, Any], material_db: Any, material_id: str) -> dict[str, Any]:
        cfg = copy.deepcopy(dict(config or {}))
        target = material_db.get(material_id)
        family_ids: list[str] = [material_id]
        subtype_ids: list[str] = []
        wood_ids: list[str] = []
        for mid in material_db.list_ids():
            if mid == material_id:
                continue
            mat = material_db.get(mid)
            if not bool(mat.recommended_for_body):
                continue
            if mat.subtype == target.subtype:
                subtype_ids.append(mid)
            elif mat.family == target.family:
                wood_ids.append(mid)
        family_ids.extend(sorted(subtype_ids)[:3])
        for mid in sorted(wood_ids):
            if mid not in family_ids:
                family_ids.append(mid)
            if len(family_ids) >= 10:
                break
        cfg["materials"] = dict(cfg.get("materials", {}) or {})
        cfg["materials"]["allowed_materials"] = family_ids
        cfg["materials"]["max_distinct_materials_per_design"] = max(2, int(cfg["materials"].get("max_distinct_materials_per_design", 2) or 2))
        return cfg

    def _prefer_material_family_weighted_scope(self, config: Mapping[str, Any], material_db: Any, material_id: str) -> dict[str, Any]:
        cfg = self._prefer_material_family_scope(config, material_db, material_id)
        allowed = list(dict((cfg.get("materials", {}) or {})).get("allowed_materials", []))
        target = material_db.get(material_id)
        extras: list[str] = []
        for mid in material_db.list_ids():
            if mid in allowed:
                continue
            mat = material_db.get(mid)
            if not bool(mat.recommended_for_body):
                continue
            if mat.family != target.family:
                extras.append(mid)
        for mid in [m for m in ["pvc_drain", "fiberglass_polyester", "cast_epoxy", "aluminum_6061"] if m in extras] + sorted(extras):
            if mid not in allowed:
                allowed.append(mid)
            if len(allowed) >= 14:
                break
        cfg["materials"]["allowed_materials"] = allowed
        return cfg

    def _build_material_family_pool(self, config: Mapping[str, Any], material_db: Any, material_id: str, *, pool_size: int, max_attempts: int) -> list[dict[str, Any]]:
        seed = int(dict((config or {}).get("project", {}) or {}).get("random_seed", 42) or 42)
        search_space = SearchSpace(config, material_db, rng_seed=seed)
        linear_pipeline = LinearEvaluationPipeline()
        target = material_db.get(material_id)
        seen: set[str] = set()
        pool: list[dict[str, Any]] = []
        for _ in range(max_attempts):
            genome = search_space.sample_random()
            design = search_space.decode(genome)
            segment_materials = self._segment_material_ids(design)
            if material_id not in segment_materials:
                continue
            family_count = sum(1 for mid in segment_materials if material_db.get(mid).family == target.family)
            if family_count < max(1, len(segment_materials)):
                continue
            key = self._design_fingerprint(design)
            if key in seen:
                continue
            seen.add(key)
            result = linear_pipeline.evaluate(design, config, material_db)
            if result.get("valid"):
                pool.append(result)
            if len(pool) >= pool_size:
                break
        return pool

    def _build_material_family_weighted_pool(self, config: Mapping[str, Any], material_db: Any, material_id: str, *, pool_size: int, max_attempts: int) -> list[dict[str, Any]]:
        seed = int(dict((config or {}).get("project", {}) or {}).get("random_seed", 42) or 42)
        search_space = SearchSpace(config, material_db, rng_seed=seed)
        linear_pipeline = LinearEvaluationPipeline()
        target = material_db.get(material_id)
        seen: set[str] = set()
        preferred: list[dict[str, Any]] = []
        fallback: list[dict[str, Any]] = []
        for _ in range(max_attempts):
            genome = search_space.sample_random()
            design = search_space.decode(genome)
            segment_materials = self._segment_material_ids(design)
            if material_id not in segment_materials:
                continue
            key = self._design_fingerprint(design)
            if key in seen:
                continue
            seen.add(key)
            family_count = sum(1 for mid in segment_materials if material_db.get(mid).family == target.family)
            result = linear_pipeline.evaluate(design, config, material_db)
            if not result.get("valid"):
                continue
            if family_count >= max(1, len(segment_materials) - 1):
                preferred.append(result)
            else:
                fallback.append(result)
            if len(preferred) >= pool_size:
                break
            if len(preferred) + len(fallback) >= pool_size * 2:
                break
        pool = preferred[:pool_size]
        if len(pool) < pool_size:
            pool.extend(fallback[: max(0, pool_size - len(pool))])
        return pool

    def _score_deltas(self, linear_pipeline: LinearEvaluationPipeline, pool: Sequence[Mapping[str, Any]], config: Mapping[str, Any], patched_db: Any) -> dict[str, Any]:
        baseline_scores = [float(result.get("aggregate_score", float("-inf"))) for result in pool]
        patched_scores = [
            float(linear_pipeline.evaluate(result["design"], config, patched_db).get("aggregate_score", float("-inf")))
            for result in pool
        ]
        deltas = [patched - base for patched, base in zip(patched_scores, baseline_scores)]
        return {
            "mean_delta": float(mean(deltas)) if deltas else 0.0,
            "min_delta": min(deltas) if deltas else 0.0,
            "max_delta": max(deltas) if deltas else 0.0,
            "improved_count": sum(delta > 0 for delta in deltas),
            "worsened_count": sum(delta < 0 for delta in deltas),
            "all_candidate_deltas": deltas,
        }

    def _design_fingerprint(self, design: Mapping[str, Any] | Any) -> str:
        if isinstance(design, Mapping):
            segments = list(dict(design or {}).get("segments", []) or [])
        else:
            segments = list(getattr(design, "segments", []) or [])
        normalized = []
        for segment in segments:
            if isinstance(segment, Mapping):
                seg = dict(segment)
            else:
                seg = {
                    "kind": getattr(segment, "kind", ""),
                    "length_cm": getattr(segment, "length_cm", 0.0),
                    "d_in_cm": getattr(segment, "d_in_cm", 0.0),
                    "d_out_cm": getattr(segment, "d_out_cm", 0.0),
                    "material_id": getattr(segment, "material_id", ""),
                    "flare_parameter": getattr(segment, "flare_parameter", None),
                }
            normalized.append({
                "kind": str(seg.get("kind", "")),
                "length_cm": round(float(seg.get("length_cm", 0.0)), 6),
                "d_in_cm": round(float(seg.get("d_in_cm", 0.0)), 6),
                "d_out_cm": round(float(seg.get("d_out_cm", 0.0)), 6),
                "material_id": str(seg.get("material_id", "")),
                "flare_parameter": None if seg.get("flare_parameter") is None else round(float(seg.get("flare_parameter")), 6),
            })
        return json.dumps(normalized, sort_keys=True)

    def _segment_material_ids(self, design: Mapping[str, Any] | Any) -> list[str]:
        if isinstance(design, Mapping):
            segments = list(dict(design or {}).get("segments", []) or [])
        else:
            segments = list(getattr(design, "segments", []) or [])
        material_ids: list[str] = []
        for segment in segments:
            if isinstance(segment, Mapping):
                material_ids.append(str(segment.get("material_id", "")))
            else:
                material_ids.append(str(getattr(segment, "material_id", "")))
        return material_ids

    def _decide_family_patch(self, baseline_ok: bool, patched_ok: bool, mean_delta: float, improved_count: int, worsened_count: int) -> str:
        if not baseline_ok or not patched_ok:
            return "reject"
        if mean_delta >= 0.005 and improved_count >= max(4, worsened_count + 4) and worsened_count == 0:
            return "accept_family"
        if mean_delta > 0.0 and improved_count > worsened_count:
            return "accept_local_only"
        return "keep_as_to_calibrate"

    def _decide_family_weighted_patch(self, baseline_ok: bool, patched_ok: bool, mean_delta: float, improved_count: int, worsened_count: int) -> str:
        if not baseline_ok or not patched_ok:
            return "reject"
        if mean_delta >= 0.001 and improved_count >= max(4, worsened_count + 3) and worsened_count <= 1:
            return "accept_weighted"
        if mean_delta > 0.0 and improved_count > worsened_count:
            return "accept_local_only"
        return "keep_as_to_calibrate"

    def _export(self, output: Mapping[str, Any], out_dir: Path) -> dict[str, str]:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "calibration_report.json"
        yaml_path = out_dir / "calibration_report.yaml"
        proposal_patch_yaml_path = out_dir / "materials_patch_suggestions.yaml"
        replayed_patch_yaml_path = out_dir / "materials_patch_replayed.yaml"
        accepted_patch_yaml_path = out_dir / "materials_patch_accepted.yaml"
        to_calibrate_patch_yaml_path = out_dir / "materials_patch_to_calibrate.yaml"
        patch_status_yaml_path = out_dir / "materials_patch_status.yaml"

        proposal_patch = self._normalize_patch(output.get("patch_proposal", dict(output.get("proposals", {}) or {}).get("patch", {})))
        replayed_patch = self._normalize_patch(output.get("patch_replayed", output.get("directed_patch", output.get("family_patch", output.get("weighted_patch", {})))))
        accepted_patch = self._normalize_patch(output.get("patch_accepted", {}))
        to_calibrate_patch = self._normalize_patch(output.get("patch_to_calibrate", {}))
        patch_status = {
            "decision": output.get("decision"),
            "validation_preserved": bool(output.get("validation_preserved", False)),
            "proposal_patch_material_ids": self._patch_material_ids(proposal_patch),
            "replayed_patch_material_ids": self._patch_material_ids(replayed_patch),
            "accepted_patch_material_ids": self._patch_material_ids(accepted_patch),
            "patch_to_calibrate_material_ids": self._patch_material_ids(to_calibrate_patch),
        }

        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(_to_serializable(output), handle, ensure_ascii=False, indent=2)
        with open(yaml_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(_to_serializable(output), handle, sort_keys=False, allow_unicode=True)
        with open(proposal_patch_yaml_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(_to_serializable(proposal_patch), handle, sort_keys=False, allow_unicode=True)
        with open(replayed_patch_yaml_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(_to_serializable(replayed_patch), handle, sort_keys=False, allow_unicode=True)
        with open(accepted_patch_yaml_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(_to_serializable(accepted_patch), handle, sort_keys=False, allow_unicode=True)
        with open(to_calibrate_patch_yaml_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(_to_serializable(to_calibrate_patch), handle, sort_keys=False, allow_unicode=True)
        with open(patch_status_yaml_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(_to_serializable(patch_status), handle, sort_keys=False, allow_unicode=True)

        return {
            "json": str(json_path),
            "yaml": str(yaml_path),
            "patch_yaml": str(proposal_patch_yaml_path),
            "replayed_patch_yaml": str(replayed_patch_yaml_path),
            "accepted_patch_yaml": str(accepted_patch_yaml_path),
            "to_calibrate_patch_yaml": str(to_calibrate_patch_yaml_path),
            "patch_status_yaml": str(patch_status_yaml_path),
        }


def _to_serializable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _to_serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_serializable(item) for item in value]
    if hasattr(value, "as_dict"):
        return _to_serializable(value.as_dict())
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


def run(config_path: str | Path, optimizer_result: Mapping[str, Any] | None = None, *, top_n: int | None = None) -> dict[str, Any]:
    return CalibrationRunner().run(config_path, optimizer_result=optimizer_result, top_n=top_n)
