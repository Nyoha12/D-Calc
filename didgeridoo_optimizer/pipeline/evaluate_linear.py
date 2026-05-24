from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ..acoustics import AirProperties, extract, find_peaks, input_impedance, radiation_impedance
from ..geometry import Design, DesignBuilder, GeometryDiscretizer, GeometryValidator
from ..materials import MaterialDatabase
from ..optimization import aggregate_score, hard_constraints_ok, penalties, score_objectives


class LinearEvaluationPipeline:
    def __init__(self) -> None:
        self.builder = DesignBuilder()
        self.validator = GeometryValidator()
        self.discretizer = GeometryDiscretizer()

    def evaluate(
        self,
        design: Mapping[str, Any] | Design,
        config: Mapping[str, Any],
        materials: MaterialDatabase | str | Path,
    ) -> dict[str, Any]:
        material_db = materials if isinstance(materials, (MaterialDatabase, dict)) else MaterialDatabase.from_yaml(materials)
        built_design = self.builder.build(design)
        errors = self.validator.validate(built_design, config)
        geometry_penalties = self.validator.soft_penalties(built_design, config)

        if errors:
            return {
                "design_id": built_design.id,
                "design": built_design,
                "analysis_design": built_design,
                "valid": False,
                "errors": errors,
                "warnings": [],
                "freq_hz": np.array([], dtype=float),
                "zin": np.array([], dtype=complex),
                "zin_mag": np.array([], dtype=float),
                "peaks": [],
                "features": {},
                "objective_scores": {},
                "penalties": geometry_penalties,
                "aggregate_score": float("-inf"),
            }

        discretization_cm = float(
            dict((config or {}).get("frequency_analysis", {}) or {}).get("discretization_max_segment_cm", 1.0)
        )
        built_design.metadata["geometry_soft_penalty"] = float(geometry_penalties.get("total_penalty", 0.0))
        discretized_design = self.discretizer.discretize(built_design, max_segment_cm=discretization_cm)

        freq_cfg = dict((config or {}).get("frequency_analysis", {}) or {})
        freq_hz = np.linspace(
            float(freq_cfg.get("f_min_hz", 10.0)),
            float(freq_cfg.get("f_max_hz", 5000.0)),
            int(freq_cfg.get("n_points", 4096)),
        )
        air = AirProperties.from_config(config)
        zin = input_impedance(freq_hz, discretized_design, material_db, air)
        zin_mag = np.abs(zin)
        # Radiation happens at the physical outlet, not the midpoint of the last discretized slice.
        exit_radius_m = float(built_design.segments[-1].d_out_cm) / 200.0
        zr = radiation_impedance(2.0 * np.pi * freq_hz, exit_radius_m, air)
        peaks = find_peaks(freq_hz, zin_mag, config)
        features = extract(freq_hz, zin, peaks, built_design, air, zr=zr)
        objective_scores = score_objectives(features, built_design, config)
        penalty_map = penalties(built_design, features, config)
        aggregate = aggregate_score(objective_scores, penalty_map, config)
        valid = hard_constraints_ok(features, built_design, config)
        warnings = self._build_warnings(built_design, material_db, features)

        return {
            "design_id": built_design.id,
            "design": built_design,
            "analysis_design": discretized_design,
            "valid": bool(valid),
            "errors": [],
            "warnings": warnings,
            "freq_hz": freq_hz,
            "zin": zin,
            "zin_mag": zin_mag,
            "peaks": peaks,
            "features": features,
            "objective_scores": objective_scores,
            "penalties": penalty_map,
            "aggregate_score": aggregate,
        }

    def _build_warnings(self, design: Design, materials: MaterialDatabase | dict[str, Any], features: Mapping[str, Any]) -> list[str]:
        warnings: list[str] = []
        material_lookup = materials.materials if isinstance(materials, MaterialDatabase) else materials
        used_materials = [material_lookup[segment.material_id] for segment in design.segments]
        if float(features.get("model_confidence", 1.0)) < 0.7:
            warnings.append("low_model_confidence")
        if int(features.get("peak_count", 0)) < 3:
            warnings.append("few_detected_peaks")
        if any(
            material.beta.nominal > 5.0
            or material.wall_loss.nominal > 0.03
            or material.porosity_leak.nominal > 0.03
            for material in used_materials
        ):
            warnings.append("high_losses_material")
        if any(_has_limited_loss_calibration(material) for material in used_materials):
            warnings.append("material_loss_calibration_limited")
        if design.segments and design.segments[-1].d_out_cm >= 10.0:
            warnings.append("large_bell_may_reduce_1d_validity")
        warnings.append("placeholder_feature_used")
        return warnings


def evaluate(
    design: Mapping[str, Any] | Design,
    config: Mapping[str, Any],
    materials: MaterialDatabase | str | Path,
) -> dict[str, Any]:
    return LinearEvaluationPipeline().evaluate(design, config, materials)


def _has_limited_loss_calibration(material: Any) -> bool:
    for parameter in (material.beta, material.wall_loss, material.porosity_leak):
        if str(parameter.status) in {"inferred", "to_calibrate"} or str(parameter.confidence) == "low":
            return True
    return False
