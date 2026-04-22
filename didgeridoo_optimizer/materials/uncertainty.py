from __future__ import annotations

import random
from typing import Any

from .models import Material


class MaterialUncertaintyManager:
    def __init__(self, rng_seed: int | None = None):
        self._random = random.Random(rng_seed)

    def sample_parameters(self, material: Material, n: int) -> list[dict[str, float]]:
        samples: list[dict[str, float]] = []
        for _ in range(max(0, n)):
            sample = {}
            for name, param in material.parameter_map().items():
                sample[name] = self._sample_triangular(param.min, param.nominal, param.max)
            samples.append(sample)
        return samples

    def calibration_priority_score(self, material: Material, sensitivities: dict[str, Any]) -> dict[str, Any]:
        per_parameter: dict[str, dict[str, float]] = {}
        for name, param in material.parameter_map().items():
            raw_sensitivity = sensitivities.get(name, 0.0)
            if isinstance(raw_sensitivity, dict):
                sensitivity = float(raw_sensitivity.get("sensitivity", 0.0))
                objective_weight = float(raw_sensitivity.get("objective_weight", 1.0))
            else:
                sensitivity = float(raw_sensitivity)
                objective_weight = 1.0

            uncertainty = param.relative_uncertainty
            score = uncertainty * sensitivity * objective_weight
            per_parameter[name] = {
                "relative_uncertainty": uncertainty,
                "sensitivity": sensitivity,
                "objective_weight": objective_weight,
                "priority_score": score,
            }

        scores = [entry["priority_score"] for entry in per_parameter.values()]
        return {
            "material_id": material.id,
            "per_parameter": per_parameter,
            "max_priority_score": max(scores) if scores else 0.0,
            "mean_priority_score": sum(scores) / len(scores) if scores else 0.0,
        }

    def _sample_triangular(self, low: float, mode: float, high: float) -> float:
        if low == high:
            return float(low)
        return float(self._random.triangular(low, high, mode))
