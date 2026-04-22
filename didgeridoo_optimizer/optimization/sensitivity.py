from __future__ import annotations

from dataclasses import replace
from statistics import mean
from typing import Any, Mapping, Sequence

from ..materials import MaterialDatabase
from ..materials.models import AcousticParameter, Material
from ..pipeline.evaluate_linear import LinearEvaluationPipeline


PARAMETER_NAMES: tuple[str, ...] = ("beta", "porosity_leak", "wall_loss")


class SensitivityAnalyzer:
    """Local sensitivity analysis focused on uncertain material parameters."""

    def __init__(self, linear_pipeline: LinearEvaluationPipeline | None = None) -> None:
        self.linear_pipeline = linear_pipeline or LinearEvaluationPipeline()

    def analyze_result(
        self,
        result: Mapping[str, Any],
        config: Mapping[str, Any],
        materials: MaterialDatabase,
        perturbation_percents: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        design = result.get("design")
        if design is None:
            raise ValueError("Sensitivity analysis requires a result containing 'design'.")

        perturbation_percents = tuple(float(p) for p in (perturbation_percents or self._default_perturbations(config)) if float(p) > 0.0)
        if not perturbation_percents:
            perturbation_percents = (5.0, 10.0)

        baseline_objectives = dict(result.get("objective_scores", {}) or {})
        baseline_aggregate = float(result.get("aggregate_score", 0.0))
        objective_weights = self._active_objective_weights(config)

        material_reports: dict[str, Any] = {}
        for material_id in sorted({segment.material_id for segment in design.segments}):
            base_material = materials.get(material_id)
            parameter_reports: dict[str, Any] = {}
            for parameter_name in PARAMETER_NAMES:
                trials: list[dict[str, Any]] = []
                for percent in perturbation_percents:
                    rel_step = percent / 100.0
                    for direction in (-1, +1):
                        factor = max(0.0, 1.0 + direction * rel_step)
                        perturbed_material = self._perturb_material_parameter(base_material, parameter_name, factor)
                        overlay = self._overlay_materials(materials, design, {material_id: perturbed_material})
                        perturbed_result = self.linear_pipeline.evaluate(design, config, overlay)
                        objective_deltas = {
                            name: float(perturbed_result.get("objective_scores", {}).get(name, 0.0)) - float(baseline_objectives.get(name, 0.0))
                            for name in objective_weights
                        }
                        weighted_abs_delta = self._weighted_abs_objective_delta(objective_deltas, objective_weights)
                        trials.append(
                            {
                                "direction": int(direction),
                                "percent": float(percent),
                                "relative_step": rel_step,
                                "factor": factor,
                                "aggregate_delta": float(perturbed_result.get("aggregate_score", 0.0)) - baseline_aggregate,
                                "objective_deltas": objective_deltas,
                                "weighted_abs_objective_delta": weighted_abs_delta,
                                "f0_delta_hz": self._numeric_delta(
                                    perturbed_result.get("features", {}).get("f0_hz"),
                                    result.get("features", {}).get("f0_hz"),
                                ),
                                "fundamental_q_delta": self._numeric_delta(
                                    perturbed_result.get("features", {}).get("fundamental_q"),
                                    result.get("features", {}).get("fundamental_q"),
                                ),
                                "brightness_delta": self._numeric_delta(
                                    perturbed_result.get("features", {}).get("brightness_proxy"),
                                    result.get("features", {}).get("brightness_proxy"),
                                ),
                            }
                        )
                parameter_reports[parameter_name] = self._summarize_trials(trials, objective_weights)

            material_reports[material_id] = {
                "family": base_material.family,
                "subtype": base_material.subtype,
                "parameters": parameter_reports,
            }

        return {
            "design_id": str(result.get("design_id", getattr(design, "id", "unknown"))),
            "baseline_aggregate_score": baseline_aggregate,
            "baseline_objective_scores": baseline_objectives,
            "perturbation_percents": list(perturbation_percents),
            "materials": material_reports,
        }

    def analyze_candidates(
        self,
        results: Sequence[Mapping[str, Any]],
        config: Mapping[str, Any],
        materials: MaterialDatabase,
        perturbation_percents: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        per_result = [self.analyze_result(result, config, materials, perturbation_percents) for result in results]
        aggregated: dict[str, Any] = {}
        for report in per_result:
            for material_id, material_report in dict(report.get("materials", {}) or {}).items():
                target = aggregated.setdefault(
                    material_id,
                    {
                        "family": material_report.get("family"),
                        "subtype": material_report.get("subtype"),
                        "parameters": {},
                        "occurrences": 0,
                    },
                )
                target["occurrences"] += 1
                for parameter_name, parameter_report in dict(material_report.get("parameters", {}) or {}).items():
                    bucket = target["parameters"].setdefault(parameter_name, [])
                    bucket.append(parameter_report)

        for material_id, material_report in aggregated.items():
            summarized: dict[str, Any] = {}
            for parameter_name, entries in dict(material_report.get("parameters", {}) or {}).items():
                summarized[parameter_name] = self._merge_parameter_summaries(entries)
            material_report["parameters"] = summarized

        return {
            "candidate_count": len(results),
            "per_candidate": per_result,
            "materials": aggregated,
        }

    def _default_perturbations(self, config: Mapping[str, Any]) -> Sequence[float]:
        uncertainty = dict((config or {}).get("uncertainty_management", {}) or {})
        sensitivity = dict(uncertainty.get("sensitivity_analysis", {}) or {})
        return tuple(float(v) for v in sensitivity.get("local_perturbation_percent", [5.0, 10.0]))

    def _active_objective_weights(self, config: Mapping[str, Any]) -> dict[str, float]:
        objectives = dict((config or {}).get("objectives", {}) or {})
        return {
            name: float(dict(cfg or {}).get("weight", 1.0))
            for name, cfg in objectives.items()
            if bool(dict(cfg or {}).get("enabled", False))
        }

    def _overlay_materials(
        self,
        materials: MaterialDatabase,
        design: Any,
        overrides: Mapping[str, Material],
    ) -> dict[str, Material]:
        overlay = dict(materials.materials)
        for segment in getattr(design, "segments", []):
            overlay[segment.material_id] = materials.get(segment.material_id)
        overlay.update(overrides)
        return overlay

    def _perturb_material_parameter(self, material: Material, parameter_name: str, factor: float) -> Material:
        parameter = getattr(material, parameter_name)
        updated = AcousticParameter(
            nominal=max(0.0, parameter.nominal * factor),
            min=max(0.0, parameter.min * factor),
            max=max(0.0, parameter.max * factor),
            status=parameter.status,
            confidence=parameter.confidence,
        )
        return replace(material, **{parameter_name: updated})

    def _weighted_abs_objective_delta(self, objective_deltas: Mapping[str, float], objective_weights: Mapping[str, float]) -> float:
        if not objective_weights:
            return 0.0
        total_weight = 0.0
        total = 0.0
        for name, weight in objective_weights.items():
            total += abs(float(objective_deltas.get(name, 0.0))) * float(weight)
            total_weight += float(weight)
        return total / max(total_weight, 1e-12)

    def _summarize_trials(self, trials: Sequence[Mapping[str, Any]], objective_weights: Mapping[str, float]) -> dict[str, Any]:
        if not trials:
            return {
                "trials": [],
                "aggregate_sensitivity": 0.0,
                "weighted_objective_sensitivity": 0.0,
                "best_direction": 0,
                "best_relative_step": 0.0,
                "dominant_objective_weight": 1.0,
                "dominant_objectives": [],
            }

        aggregate_sensitivity = mean(abs(float(t["aggregate_delta"])) / max(float(t["relative_step"]), 1e-12) for t in trials)
        weighted_objective_sensitivity = mean(
            float(t["weighted_abs_objective_delta"]) / max(float(t["relative_step"]), 1e-12) for t in trials
        )

        grouped_by_direction = {
            direction: [float(t["aggregate_delta"]) for t in trials if int(t["direction"]) == direction]
            for direction in (-1, +1)
        }
        direction_scores = {direction: mean(values) if values else float("-inf") for direction, values in grouped_by_direction.items()}
        best_direction = max(direction_scores, key=direction_scores.get)
        best_trials = [t for t in trials if int(t["direction"]) == int(best_direction)]
        best_trial = max(best_trials, key=lambda item: float(item["aggregate_delta"])) if best_trials else trials[0]

        objective_impact: dict[str, float] = {}
        for trial in trials:
            for name, delta in dict(trial.get("objective_deltas", {}) or {}).items():
                objective_impact.setdefault(name, []).append(abs(float(delta)))
        ranked_objectives = sorted(
            (
                {
                    "objective": name,
                    "mean_abs_delta": mean(values),
                    "weight": float(objective_weights.get(name, 1.0)),
                }
                for name, values in objective_impact.items()
            ),
            key=lambda item: (item["mean_abs_delta"] * item["weight"], item["mean_abs_delta"]),
            reverse=True,
        )
        dominant_objective_weight = float(ranked_objectives[0]["weight"]) if ranked_objectives else 1.0

        return {
            "trials": [dict(item) for item in trials],
            "aggregate_sensitivity": float(aggregate_sensitivity),
            "weighted_objective_sensitivity": float(weighted_objective_sensitivity),
            "best_direction": int(best_direction),
            "best_relative_step": float(best_trial.get("relative_step", 0.0)),
            "best_aggregate_delta": float(best_trial.get("aggregate_delta", 0.0)),
            "dominant_objective_weight": dominant_objective_weight,
            "dominant_objectives": ranked_objectives[:3],
        }

    def _merge_parameter_summaries(self, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not entries:
            return self._summarize_trials([], {})
        trials = []
        for entry in entries:
            trials.extend(list(entry.get("trials", []) or []))
        objective_weights = {}
        for entry in entries:
            for objective in entry.get("dominant_objectives", []) or []:
                objective_weights[objective["objective"]] = max(
                    float(objective_weights.get(objective["objective"], 0.0)),
                    float(objective.get("weight", 0.0)),
                )
        return self._summarize_trials(trials, objective_weights or {"aggregate": 1.0})

    def _numeric_delta(self, new_value: Any, base_value: Any) -> float | None:
        if new_value is None or base_value is None:
            return None
        return float(new_value) - float(base_value)
