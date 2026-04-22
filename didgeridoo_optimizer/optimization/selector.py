from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .pareto import ParetoOptimizer


class FinalSelector:
    def select_best(self, candidates: Sequence[Mapping[str, Any]], method: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
        ranked = self.rank_top_n(candidates, 1, method, config)
        return ranked[0] if ranked else {}

    def rank_top_n(
        self,
        candidates: Sequence[Mapping[str, Any]],
        n: int,
        method: str,
        config: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        cfg = dict(config or {})
        normalized_candidates = [self._coerce_candidate(candidate, cfg) for candidate in candidates]
        normalized_candidates = [candidate for candidate in normalized_candidates if candidate]
        normalized_candidates = self._deduplicate(normalized_candidates)
        if not normalized_candidates:
            return []

        method_name = str(method or dict(cfg.get("optimization", {}) or {}).get("final_selector", "knee"))
        if method_name == "weighted_sum":
            key_fn = self._weighted_sum_score
        elif method_name == "minimax":
            key_fn = self._minimax_score
        else:
            front = ParetoOptimizer().pareto_front(normalized_candidates)
            front_ids = {self._candidate_identity(candidate) for candidate in front}
            ranked_front = sorted(front, key=lambda item: self._knee_score(item, front), reverse=True)
            ranked_rest = sorted(
                [candidate for candidate in normalized_candidates if self._candidate_identity(candidate) not in front_ids],
                key=self._weighted_sum_score,
                reverse=True,
            )
            return (ranked_front + ranked_rest)[: max(0, int(n))]

        return sorted(normalized_candidates, key=key_fn, reverse=True)[: max(0, int(n))]

    def _coerce_candidate(self, candidate: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        objectives_cfg = dict(config.get("objectives", {}) or {})
        if "normalized_objectives" in candidate:
            normalized = {str(k): self._clip01(float(v)) for k, v in dict(candidate.get("normalized_objectives", {}) or {}).items()}
            result = dict(candidate)
            result["normalized_objectives"] = normalized
            result["aggregate_score"] = float(candidate.get("aggregate_score", float(dict(candidate.get("result", {}) or {}).get("aggregate_score", 0.0))))
            result["valid"] = bool(candidate.get("valid", dict(candidate.get("result", {}) or {}).get("valid", False)))
            result.setdefault("config_objectives", objectives_cfg)
            return result

        result_map = dict(candidate.get("result", candidate) or {})
        objective_scores = dict(result_map.get("objective_scores", {}) or {})
        normalized = {
            name: self._clip01(float(score))
            for name, score in objective_scores.items()
            if bool(dict(objectives_cfg.get(name, {}) or {}).get("enabled", True))
        }
        return {
            **dict(candidate),
            "result": result_map,
            "normalized_objectives": normalized,
            "aggregate_score": float(result_map.get("aggregate_score", candidate.get("aggregate_score", 0.0))),
            "valid": bool(result_map.get("valid", candidate.get("valid", False))),
            "config_objectives": objectives_cfg,
        }

    def _weighted_sum_score(self, candidate: Mapping[str, Any]) -> float:
        result = dict(candidate.get("result", {}) or {})
        objectives = dict(candidate.get("normalized_objectives", {}) or {})
        objectives_cfg = dict(candidate.get("config_objectives", {}) or {})
        if not objectives:
            return float(candidate.get("aggregate_score", float("-inf")))
        if not objectives_cfg:
            return sum(objectives.values()) / max(len(objectives), 1) + 0.1 * float(candidate.get("aggregate_score", 0.0))
        total_weight = 0.0
        weighted = 0.0
        for name, value in objectives.items():
            weight = float(dict(objectives_cfg.get(name, {}) or {}).get("weight", 1.0))
            weighted += weight * value
            total_weight += weight
        return weighted / max(total_weight, 1e-9)

    def _minimax_score(self, candidate: Mapping[str, Any]) -> float:
        objectives = dict(candidate.get("normalized_objectives", {}) or {})
        if not objectives:
            return float(candidate.get("aggregate_score", float("-inf")))
        worst_regret = max(1.0 - value for value in objectives.values())
        return 1.0 - worst_regret

    def _knee_score(self, candidate: Mapping[str, Any], front: Sequence[Mapping[str, Any]]) -> float:
        objectives = sorted(dict(candidate.get("normalized_objectives", {}) or {}).keys())
        if len(objectives) < 2:
            return self._weighted_sum_score(candidate)
        points = [self._objective_vector(item, objectives) for item in front]
        candidate_point = self._objective_vector(candidate, objectives)
        ideal = [max(point[idx] for point in points) for idx in range(len(objectives))]
        nadir = [min(point[idx] for point in points) for idx in range(len(objectives))]
        diagonal = [ideal[idx] - nadir[idx] for idx in range(len(objectives))]
        diagonal_norm = math.sqrt(sum(value * value for value in diagonal))
        if diagonal_norm <= 1e-12:
            return self._weighted_sum_score(candidate)
        rel = [candidate_point[idx] - nadir[idx] for idx in range(len(objectives))]
        proj_scale = sum(rel[idx] * diagonal[idx] for idx in range(len(objectives))) / (diagonal_norm ** 2)
        projection = [nadir[idx] + proj_scale * diagonal[idx] for idx in range(len(objectives))]
        orth_distance = math.sqrt(sum((candidate_point[idx] - projection[idx]) ** 2 for idx in range(len(objectives))))
        balance_bonus = min(candidate_point)
        return orth_distance + 0.2 * balance_bonus + 0.05 * float(candidate.get("aggregate_score", 0.0))

    def _objective_vector(self, candidate: Mapping[str, Any], objective_names: Sequence[str]) -> list[float]:
        normalized = dict(candidate.get("normalized_objectives", {}) or {})
        return [float(normalized.get(name, 0.0)) for name in objective_names]

    def _candidate_identity(self, candidate: Mapping[str, Any]) -> tuple[str, str]:
        genome = dict(candidate.get("genome", {}) or {})
        result = dict(candidate.get("result", {}) or {})
        design = result.get("design")
        if isinstance(design, Mapping):
            topology = str(genome.get("topology", dict(design.get("metadata", {}) or {}).get("topology", "candidate")))
        else:
            topology = str(genome.get("topology", getattr(getattr(design, "metadata", {}), "get", lambda *_: "candidate")("topology", "candidate")))
        return (str(genome.get("id", result.get("design_id", "candidate"))), topology)

    def _deduplicate(self, candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        best_by_id: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in candidates:
            key = self._candidate_identity(candidate)
            current = best_by_id.get(key)
            if current is None or float(candidate.get("aggregate_score", 0.0)) > float(current.get("aggregate_score", 0.0)):
                best_by_id[key] = dict(candidate)
        return list(best_by_id.values())

    def _clip01(self, value: float) -> float:
        if math.isnan(value):
            return 0.0
        return max(0.0, min(1.0, value))
