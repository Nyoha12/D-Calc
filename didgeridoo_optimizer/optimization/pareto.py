from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence


class ParetoOptimizer:
    """Simple MVP multi-objective optimizer with Pareto-front survival."""

    def run(
        self,
        evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any]] | Any,
        search_space: Any,
        config: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        cfg = dict(config or {})
        population = self.initialize_population(search_space, cfg)
        evaluated_history: list[dict[str, Any]] = []
        generations = max(1, int(dict(cfg.get("optimization", {}) or {}).get("generations", 1)))
        budget = int(dict(cfg.get("optimization", {}) or {}).get("linear_budget", 0) or 0)
        evaluation_count = 0
        generations_completed = 0

        for _ in range(generations):
            if budget and evaluation_count >= budget:
                break
            remaining_budget = max(0, budget - evaluation_count) if budget else None
            current_population = population[:remaining_budget] if remaining_budget is not None else population
            if not current_population:
                break
            evaluated = self.evaluate_population(current_population, evaluator, cfg)
            evaluation_count += len(evaluated)
            evaluated_history.extend(evaluated)
            generations_completed += 1
            if budget and evaluation_count >= budget:
                break
            population = self.next_generation(evaluated, search_space, cfg)
            if not population:
                break

        final_front = self.pareto_front(evaluated_history)
        return {
            "evaluated": evaluated_history,
            "pareto_front": final_front,
            "evaluation_count": evaluation_count,
            "generations_completed": generations_completed,
        }

    def initialize_population(self, search_space: Any, config: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        cfg = dict(config or {})
        opt_cfg = dict(cfg.get("optimization", {}) or {})
        population_size = max(1, int(opt_cfg.get("random_initial_population", 200)))
        return [search_space.sample_random() for _ in range(population_size)]

    def evaluate_population(
        self,
        population: Sequence[Mapping[str, Any]],
        evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any]] | Any,
        config: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        cfg = dict(config or {})
        results: list[dict[str, Any]] = []
        for genome in population:
            if hasattr(evaluator, "evaluate"):
                result = evaluator.evaluate(genome)
            else:
                result = evaluator(genome)
            entry = {
                "genome": dict(genome),
                "result": dict(result),
                "normalized_objectives": self._normalized_objectives(result, cfg),
                "valid": bool(result.get("valid", False)),
                "aggregate_score": float(result.get("aggregate_score", float("-inf"))),
            }
            results.append(entry)
        return results

    def dominates(self, a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        if not bool(a.get("valid", False)) and bool(b.get("valid", False)):
            return False
        if bool(a.get("valid", False)) and not bool(b.get("valid", False)):
            return True
        a_obj = dict(a.get("normalized_objectives", {}) or {})
        b_obj = dict(b.get("normalized_objectives", {}) or {})
        if not a_obj and not b_obj:
            return float(a.get("aggregate_score", float("-inf"))) > float(b.get("aggregate_score", float("-inf")))
        keys = sorted(set(a_obj) | set(b_obj))
        if not keys:
            return False
        better_or_equal = all(float(a_obj.get(key, 0.0)) >= float(b_obj.get(key, 0.0)) for key in keys)
        strictly_better = any(float(a_obj.get(key, 0.0)) > float(b_obj.get(key, 0.0)) for key in keys)
        return better_or_equal and strictly_better

    def pareto_front(self, evaluated: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        front: list[dict[str, Any]] = []
        for candidate in evaluated:
            dominated = False
            for other in evaluated:
                if other is candidate:
                    continue
                if self.dominates(other, candidate):
                    dominated = True
                    break
            if not dominated:
                front.append(dict(candidate))
        front.sort(key=self._crowding_then_score, reverse=True)
        return front

    def next_generation(
        self,
        evaluated: Sequence[Mapping[str, Any]],
        search_space: Any,
        config: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        cfg = dict(config or {})
        opt_cfg = dict(cfg.get("optimization", {}) or {})
        population_size = max(1, int(opt_cfg.get("random_initial_population", len(evaluated) or 1)))
        ranked = self._rank_candidates(evaluated)
        if not ranked:
            return [search_space.sample_random() for _ in range(population_size)]

        n_elite = max(1, int(round(population_size * 0.30)))
        n_mutation = max(1, int(round(population_size * 0.40)))
        n_crossover = max(1, int(round(population_size * 0.20)))
        n_random = max(0, population_size - n_elite - n_mutation - n_crossover)

        next_population: list[dict[str, Any]] = [dict(candidate["genome"]) for candidate in ranked[:n_elite]]
        parent_pool = ranked[: max(4, min(len(ranked), n_elite * 3))]

        for idx in range(n_mutation):
            parent = parent_pool[idx % len(parent_pool)]
            next_population.append(search_space.mutate(parent["genome"]))

        for idx in range(n_crossover):
            parent_a = parent_pool[idx % len(parent_pool)]
            parent_b = parent_pool[(idx + 1) % len(parent_pool)]
            next_population.append(search_space.crossover(parent_a["genome"], parent_b["genome"]))

        for _ in range(n_random):
            next_population.append(search_space.sample_random())

        return next_population[:population_size]

    def _normalized_objectives(self, result: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, float]:
        objective_scores = dict(result.get("objective_scores", {}) or {})
        objectives_cfg = dict(config.get("objectives", {}) or {})
        normalized: dict[str, float] = {}
        for name, score in objective_scores.items():
            cfg = dict(objectives_cfg.get(name, {}) or {})
            if not bool(cfg.get("enabled", True)):
                continue
            normalized[name] = self._clip01(float(score))
        return normalized

    def _rank_candidates(self, evaluated: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        front = self.pareto_front(evaluated)
        front_ids = {self._candidate_identity(candidate) for candidate in front}
        remaining = [dict(candidate) for candidate in evaluated if self._candidate_identity(candidate) not in front_ids]
        front_sorted = sorted(front, key=self._crowding_then_score, reverse=True)
        remaining_sorted = sorted(remaining, key=self._crowding_then_score, reverse=True)
        return front_sorted + remaining_sorted

    def _crowding_then_score(self, candidate: Mapping[str, Any]) -> tuple[float, float]:
        objectives = dict(candidate.get("normalized_objectives", {}) or {})
        if not objectives:
            return (0.0, float(candidate.get("aggregate_score", float("-inf"))))
        values = list(objectives.values())
        spread = statistics_span(values)
        mean_value = sum(values) / max(len(values), 1)
        return (mean_value + 0.1 * spread, float(candidate.get("aggregate_score", float("-inf"))))

    def _candidate_identity(self, candidate: Mapping[str, Any]) -> tuple[str, float]:
        genome = dict(candidate.get("genome", {}) or {})
        result = dict(candidate.get("result", {}) or {})
        return (str(genome.get("id", result.get("design_id", "candidate"))), float(candidate.get("aggregate_score", result.get("aggregate_score", 0.0))))

    def _clip01(self, value: float) -> float:
        if math.isnan(value):
            return 0.0
        return max(0.0, min(1.0, value))



def statistics_span(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return max(values) - min(values)
