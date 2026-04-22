from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from ..materials import MaterialDatabase
from ..player import RobustnessEvaluator


class RobustnessPipeline:
    def __init__(self, materials_path: str | Path | None = None) -> None:
        self.evaluator = RobustnessEvaluator(materials_path=materials_path)

    def evaluate(
        self,
        design_result: Mapping[str, Any],
        config: Mapping[str, Any],
        materials: MaterialDatabase | str | Path | None = None,
    ) -> dict[str, Any]:
        robust = self.evaluator.evaluate(design_result, config, materials)
        out = dict(design_result)
        out["robustness"] = robust

        objective_scores = dict(out.get("objective_scores", {}) or {})
        objective_scores["beginner_robustness"] = float(
            self._scenario_score(robust, scenario_id="beginner_profile")
        )
        objective_scores["expert_robustness"] = float(
            self._scenario_score(robust, scenario_id="expert_profile")
        )
        out["objective_scores"] = objective_scores
        return out

    def evaluate_batch(
        self,
        design_results: Sequence[Mapping[str, Any]],
        config: Mapping[str, Any],
        materials: MaterialDatabase | str | Path | None = None,
    ) -> list[dict[str, Any]]:
        top_n = int(dict((config or {}).get("optimization", {}) or {}).get("top_n_for_robustness", len(design_results)))
        ordered = sorted(design_results, key=lambda item: float(item.get("aggregate_score", float("-inf"))), reverse=True)
        selected = ordered[: max(0, top_n)]
        return [self.evaluate(item, config, materials) for item in selected]

    def _scenario_score(self, robust: Mapping[str, Any], scenario_id: str) -> float:
        for item in robust.get("scenario_results", []):
            if str(item.get("scenario_id")) == scenario_id:
                return float(item.get("robust_scenario_score", 0.0))
        return 0.0


def evaluate(
    design_result: Mapping[str, Any],
    config: Mapping[str, Any],
    materials: MaterialDatabase | str | Path | None = None,
) -> dict[str, Any]:
    return RobustnessPipeline().evaluate(design_result, config, materials)


def evaluate_batch(
    design_results: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    materials: MaterialDatabase | str | Path | None = None,
) -> list[dict[str, Any]]:
    return RobustnessPipeline().evaluate_batch(design_results, config, materials)
