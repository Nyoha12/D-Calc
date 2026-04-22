from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..materials import MaterialDatabase, MaterialUncertaintyManager
from ..materials.models import AcousticParameter, Material


class TargetedCalibrationAdvisor:
    """Prioritize uncertain materials and propose bounded experimental updates."""

    def __init__(self, uncertainty_manager: MaterialUncertaintyManager | None = None) -> None:
        self.uncertainty_manager = uncertainty_manager or MaterialUncertaintyManager()

    def prioritize_materials(
        self,
        sensitivity_report: Mapping[str, Any],
        materials: MaterialDatabase,
    ) -> dict[str, Any]:
        material_rankings: list[dict[str, Any]] = []
        for material_id, material_report in dict(sensitivity_report.get("materials", {}) or {}).items():
            material = materials.get(material_id)
            per_parameter_inputs = {}
            for parameter_name, parameter_report in dict(material_report.get("parameters", {}) or {}).items():
                per_parameter_inputs[parameter_name] = {
                    "sensitivity": float(parameter_report.get("weighted_objective_sensitivity", 0.0)),
                    "objective_weight": float(parameter_report.get("dominant_objective_weight", 1.0)),
                }
            priority = self.uncertainty_manager.calibration_priority_score(material, per_parameter_inputs)
            material_rankings.append(
                {
                    "material_id": material_id,
                    "family": material.family,
                    "subtype": material.subtype,
                    "occurrences": int(material_report.get("occurrences", 0)),
                    "priority": priority,
                    "sensitivity": material_report,
                }
            )

        material_rankings.sort(
            key=lambda item: (
                float(dict(item.get("priority", {}) or {}).get("max_priority_score", 0.0)),
                float(dict(item.get("priority", {}) or {}).get("mean_priority_score", 0.0)),
                int(item.get("occurrences", 0)),
            ),
            reverse=True,
        )
        return {
            "material_rankings": material_rankings,
            "top_material_ids": [item["material_id"] for item in material_rankings],
        }

    def propose_adjustments(
        self,
        prioritized_report: Mapping[str, Any],
        materials: MaterialDatabase,
        *,
        min_priority_score: float = 0.01,
        max_relative_step: float = 0.08,
    ) -> dict[str, Any]:
        recommendations: list[dict[str, Any]] = []
        patches: dict[str, Any] = {"materials": {}}

        for entry in list(prioritized_report.get("material_rankings", []) or []):
            material_id = str(entry.get("material_id"))
            material = materials.get(material_id)
            priority = dict(entry.get("priority", {}) or {})
            sensitivity = dict(entry.get("sensitivity", {}) or {})

            material_patch = {}
            for parameter_name, priority_info in dict(priority.get("per_parameter", {}) or {}).items():
                priority_score = float(priority_info.get("priority_score", 0.0))
                if priority_score < float(min_priority_score):
                    continue
                parameter_sensitivity = dict(sensitivity.get("parameters", {}) or {}).get(parameter_name, {})
                best_direction = int(parameter_sensitivity.get("best_direction", 0))
                if best_direction == 0:
                    continue

                suggested_step = min(
                    max_relative_step,
                    max(0.02, 0.5 * float(parameter_sensitivity.get("best_relative_step", 0.0))),
                )
                factor = 1.0 + best_direction * suggested_step
                updated_parameter = self._scaled_parameter(getattr(material, parameter_name), factor)
                material_patch.update(
                    {
                        f"{parameter_name}_nominal": updated_parameter.nominal,
                        f"{parameter_name}_min": updated_parameter.min,
                        f"{parameter_name}_max": updated_parameter.max,
                    }
                )
                recommendations.append(
                    {
                        "material_id": material_id,
                        "parameter": parameter_name,
                        "priority_score": priority_score,
                        "current": getattr(material, parameter_name).as_dict(),
                        "proposed": updated_parameter.as_dict(),
                        "relative_change": best_direction * suggested_step,
                        "direction": "increase" if best_direction > 0 else "decrease",
                        "basis": {
                            "weighted_objective_sensitivity": float(parameter_sensitivity.get("weighted_objective_sensitivity", 0.0)),
                            "best_aggregate_delta": float(parameter_sensitivity.get("best_aggregate_delta", 0.0)),
                            "dominant_objectives": list(parameter_sensitivity.get("dominant_objectives", []) or []),
                        },
                        "provenance": "inferred_targeted_calibration",
                        "note": "Suggested bounded update for experiment prioritization; not a sourced final coefficient.",
                    }
                )

            if material_patch:
                patches["materials"][material_id] = material_patch

        recommendations.sort(key=lambda item: float(item.get("priority_score", 0.0)), reverse=True)
        return {
            "recommendations": recommendations,
            "patch": patches,
            "material_ids_with_patch": sorted(patches["materials"].keys()),
        }

    def _scaled_parameter(self, parameter: AcousticParameter, factor: float) -> AcousticParameter:
        return AcousticParameter(
            nominal=max(0.0, parameter.nominal * factor),
            min=max(0.0, parameter.min * factor),
            max=max(0.0, parameter.max * factor),
            status=parameter.status,
            confidence=parameter.confidence,
        )
