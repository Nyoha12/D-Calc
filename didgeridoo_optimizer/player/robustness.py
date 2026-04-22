from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence
import copy
import math

import numpy as np

from ..geometry.models import Design, Segment
from ..materials import MaterialDatabase
from ..materials.models import Material
from ..pipeline.evaluate_linear import evaluate as evaluate_linear
from .models import PlayerProfileSampler, PlayerProfile
from .vocal_tract import VocalTractLibrary, VocalTractPreset


class RobustnessEvaluator:
    def __init__(self, materials_path: str | Path | None = None) -> None:
        self.materials_path = Path(materials_path) if materials_path is not None else None
        self._profiles = PlayerProfileSampler()
        self._tracts = VocalTractLibrary()

    def evaluate(
        self,
        design_result: Mapping[str, Any],
        config: Mapping[str, Any],
        materials: MaterialDatabase | str | Path | None = None,
    ) -> dict[str, Any]:
        scenario_specs = self.sample_scenarios(design_result, config, materials)
        scenario_results = [self.evaluate_scenario(design_result, scenario, config, materials) for scenario in scenario_specs]
        return self.aggregate_robustness(scenario_results, config)

    def sample_scenarios(
        self,
        design_result: Mapping[str, Any],
        config: Mapping[str, Any] | None,
        materials: MaterialDatabase | str | Path | None = None,
    ) -> list[dict[str, Any]]:
        material_db = self._resolve_material_db(config, materials)
        design = self._extract_design(design_result)
        has_wood = any(material_db.get(seg.material_id).family == "wood" for seg in design.segments)

        scenarios: list[dict[str, Any]] = [
            {"id": "nominal", "kind": "baseline"},
            {"id": "beginner_profile", "kind": "player", "player_profile": self._profiles.beginner_preset()},
            {"id": "expert_profile", "kind": "player", "player_profile": self._profiles.expert_preset()},
            {"id": "neutral_tract", "kind": "tract", "tract": self._tracts.get_preset("neutral")},
            {"id": "tongue_high", "kind": "tract", "tract": self._tracts.get_preset("tongue_high")},
            {"id": "tongue_low", "kind": "tract", "tract": self._tracts.get_preset("tongue_low")},
        ]
        if has_wood:
            scenarios.extend(
                [
                    {"id": "humid_materials", "kind": "materials", "humidity_state": "humid"},
                    {"id": "dry_materials", "kind": "materials", "humidity_state": "dry_indoor"},
                    {"id": "epoxy_lined_if_wood", "kind": "materials", "finish": "epoxy_lined"},
                ]
            )
        return scenarios

    def evaluate_scenario(
        self,
        design_result: Mapping[str, Any],
        scenario: Mapping[str, Any],
        config: Mapping[str, Any],
        materials: MaterialDatabase | str | Path | None = None,
    ) -> dict[str, Any]:
        base_result = dict(design_result)
        material_db = self._resolve_material_db(config, materials)
        scenario_id = str(scenario.get("id", "scenario"))
        kind = str(scenario.get("kind", "baseline"))

        if kind == "materials":
            scenario_design = self._material_scenario_design(self._extract_design(base_result), scenario, material_db)
            linear_result = evaluate_linear(scenario_design, config, material_db)
        else:
            linear_result = base_result

        score_components = self._scenario_score_components(base_result, linear_result, scenario, config)
        score = float(np.mean(list(score_components.values()))) if score_components else 0.0
        meets_targets = score >= 0.60 and bool(linear_result.get("valid", False))

        return {
            "scenario_id": scenario_id,
            "scenario_kind": kind,
            "scenario": self._serialize_scenario(scenario),
            "valid": bool(linear_result.get("valid", False)),
            "aggregate_score": float(linear_result.get("aggregate_score", float("-inf"))),
            "robust_scenario_score": score,
            "score_components": score_components,
            "meets_targets": bool(meets_targets),
            "design_id": linear_result.get("design_id", base_result.get("design_id")),
            "result": linear_result,
            "metric_deltas": self._metric_deltas(base_result, linear_result),
        }

    def aggregate_robustness(
        self,
        scenario_results: Sequence[Mapping[str, Any]],
        config: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not scenario_results:
            return {
                "robust_score": 0.0,
                "score_mean": 0.0,
                "score_std": 0.0,
                "valid_fraction": 0.0,
                "probability_meeting_targets": 0.0,
                "sensitivity_summary": {},
                "worst_case_scenario": None,
                "best_case_scenario": None,
                "scenario_results": [],
            }

        scores = np.asarray([float(item.get("robust_scenario_score", 0.0)) for item in scenario_results], dtype=float)
        valid_flags = np.asarray([1.0 if bool(item.get("valid", False)) else 0.0 for item in scenario_results], dtype=float)
        meets_flags = np.asarray([1.0 if bool(item.get("meets_targets", False)) else 0.0 for item in scenario_results], dtype=float)

        score_mean = float(np.mean(scores))
        score_std = float(np.std(scores))
        valid_fraction = float(np.mean(valid_flags))
        probability_meeting_targets = float(np.mean(meets_flags))
        robust_score = float(max(0.0, min(1.0, score_mean - 0.5 * score_std + 0.2 * valid_fraction)))

        ranked = sorted(scenario_results, key=lambda item: float(item.get("robust_scenario_score", 0.0)))
        return {
            "robust_score": robust_score,
            "score_mean": score_mean,
            "score_std": score_std,
            "valid_fraction": valid_fraction,
            "probability_meeting_targets": probability_meeting_targets,
            "sensitivity_summary": self._sensitivity_summary(scenario_results),
            "worst_case_scenario": self._scenario_digest(ranked[0]),
            "best_case_scenario": self._scenario_digest(ranked[-1]),
            "scenario_results": [dict(item) for item in scenario_results],
        }

    def _resolve_material_db(
        self,
        config: Mapping[str, Any] | None,
        materials: MaterialDatabase | str | Path | None,
    ) -> MaterialDatabase:
        if isinstance(materials, MaterialDatabase):
            return materials
        if isinstance(materials, (str, Path)):
            return MaterialDatabase.from_yaml(materials)
        cfg_materials = dict((config or {}).get("materials", {}) or {})
        db_path = cfg_materials.get("database_file") or self.materials_path or "/mnt/data/materials_base_v1.yaml"
        db_path = self._resolve_path(db_path)
        variant_rules = cfg_materials.get("variant_rules_file") or "/mnt/data/wood_variant_rules_v1.yaml"
        return MaterialDatabase.from_yaml(db_path, variant_rules_path=self._resolve_path(variant_rules))

    def _resolve_path(self, maybe_path: str | Path) -> Path:
        p = Path(maybe_path)
        if p.exists():
            return p
        if not p.is_absolute():
            alt = Path("/mnt/data") / p.name
            if alt.exists():
                return alt
        return p

    def _extract_design(self, design_result: Mapping[str, Any]) -> Design:
        design = design_result.get("design")
        if isinstance(design, Design):
            return design
        raise TypeError("RobustnessEvaluator expects a linear evaluation result containing a Design instance.")

    def _material_scenario_design(self, design: Design, scenario: Mapping[str, Any], material_db: MaterialDatabase) -> Design:
        transformed: list[Segment] = []
        for seg in design.segments:
            transformed_id = self._transform_material_id(seg.material_id, scenario, material_db)
            transformed.append(replace(seg, material_id=transformed_id))
        metadata = dict(design.metadata)
        metadata["robustness_material_scenario"] = str(scenario.get("id", "materials"))
        return Design(id=f"{design.id}__{scenario.get('id', 'materials')}", segments=transformed, metadata=metadata)

    def _transform_material_id(self, material_id: str, scenario: Mapping[str, Any], material_db: MaterialDatabase) -> str:
        material = material_db.get(material_id)
        if material.family != "wood":
            return material_id
        generator = material_db.variant_generator
        if generator is None:
            return material_id

        variant = material.variant
        humidity_state = scenario.get("humidity_state", variant.humidity_state if variant else None)
        finish = scenario.get("finish", variant.finish if variant else None)
        grade = scenario.get("grade", variant.grade if variant else None)
        density_class = scenario.get("density_class", variant.density_class if variant else None)
        generated = generator.generate_variant(
            material if "__" not in material.id else material_db.get(material.base_material),
            humidity_state=humidity_state,
            finish=finish,
            grade=grade,
            density_class=density_class,
        )
        return generated.id

    def _scenario_score_components(
        self,
        base_result: Mapping[str, Any],
        scenario_result: Mapping[str, Any],
        scenario: Mapping[str, Any],
        config: Mapping[str, Any] | None,
    ) -> dict[str, float]:
        base_features = dict(base_result.get("features", {}) or {})
        scenario_features = dict(scenario_result.get("features", {}) or {})
        kind = str(scenario.get("kind", "baseline"))
        base_quality = self._aggregate_to_unit_interval(float(scenario_result.get("aggregate_score", 0.0)))
        confidence = float(scenario_features.get("model_confidence", 0.0))
        f0_stability = self._stability_score(base_features.get("f0_hz"), scenario_features.get("f0_hz"), tolerance_ratio=0.08)
        q_value = float(scenario_features.get("fundamental_q") or 0.0)
        q_ratio = self._stability_score(base_features.get("fundamental_q"), scenario_features.get("fundamental_q"), tolerance_ratio=0.20)
        peak_count_score = min(1.0, float(scenario_features.get("peak_count", 0.0)) / 6.0)
        brightness_score = self._positive_ratio_score(float(scenario_features.get("brightness_proxy", 0.0)), reference=float(base_features.get("brightness_proxy") or 1.0))
        vocal_headroom = self._vocal_headroom_score(scenario_features)

        if kind == "player":
            profile = scenario.get("player_profile")
            if not isinstance(profile, PlayerProfile):
                raise TypeError("player scenario requires PlayerProfile")
            q_fit = self._preferred_q_score(q_value, profile.preferred_q, profile.q_tolerance)
            stability_fit = max(0.0, min(1.0, 0.6 * profile.embouchure_stability + 0.4 * confidence))
            pressure_fit = max(0.0, min(1.0, 1.0 - profile.pressure_variability))
            tract_fit = max(0.0, min(1.0, 0.5 * profile.tract_control + 0.5 * vocal_headroom))
            return {
                "base_quality": base_quality,
                "f0_stability": f0_stability,
                "q_fit": q_fit,
                "stability_fit": stability_fit,
                "pressure_fit": pressure_fit,
                "tract_fit": tract_fit,
            }

        if kind == "tract":
            tract = scenario.get("tract")
            if not isinstance(tract, VocalTractPreset):
                raise TypeError("tract scenario requires VocalTractPreset")
            target_band_fit = self._tract_target_fit(scenario_features, tract)
            control_fit = max(0.0, min(1.0, 0.5 * tract.control_gain + 0.5 * vocal_headroom))
            brightness_fit = max(0.0, min(1.0, 0.6 * brightness_score + 0.4 * tract.brightness_bias))
            return {
                "base_quality": base_quality,
                "f0_stability": f0_stability,
                "target_band_fit": target_band_fit,
                "control_fit": control_fit,
                "brightness_fit": brightness_fit,
                "confidence": confidence,
            }

        if kind == "materials":
            return {
                "base_quality": base_quality,
                "f0_stability": f0_stability,
                "q_stability": q_ratio,
                "validity": 1.0 if bool(scenario_result.get("valid", False)) else 0.0,
                "confidence": confidence,
                "peak_structure": peak_count_score,
            }

        return {
            "base_quality": base_quality,
            "f0_stability": f0_stability,
            "confidence": confidence,
            "peak_structure": peak_count_score,
        }

    def _metric_deltas(self, base_result: Mapping[str, Any], scenario_result: Mapping[str, Any]) -> dict[str, float]:
        base_features = dict(base_result.get("features", {}) or {})
        features = dict(scenario_result.get("features", {}) or {})
        out: dict[str, float] = {}
        for name in ("f0_hz", "fundamental_q", "fundamental_peak_magnitude", "brightness_proxy", "model_confidence"):
            base_value = base_features.get(name)
            value = features.get(name)
            if base_value is None or value is None:
                continue
            out[f"delta_{name}"] = float(value) - float(base_value)
        return out

    def _sensitivity_summary(self, scenario_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        by_kind: dict[str, list[float]] = {}
        max_abs_metric_delta = 0.0
        for item in scenario_results:
            kind = str(item.get("scenario_kind", "unknown"))
            by_kind.setdefault(kind, []).append(float(item.get("robust_scenario_score", 0.0)))
            metric_deltas = dict(item.get("metric_deltas", {}) or {})
            if metric_deltas:
                max_abs_metric_delta = max(max_abs_metric_delta, max(abs(float(v)) for v in metric_deltas.values()))
        return {
            "scenario_kind_score_mean": {kind: float(np.mean(values)) for kind, values in by_kind.items()},
            "scenario_kind_score_std": {kind: float(np.std(values)) for kind, values in by_kind.items()},
            "max_abs_metric_delta": float(max_abs_metric_delta),
        }

    def _scenario_digest(self, scenario_result: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "scenario_id": scenario_result.get("scenario_id"),
            "scenario_kind": scenario_result.get("scenario_kind"),
            "robust_scenario_score": float(scenario_result.get("robust_scenario_score", 0.0)),
            "valid": bool(scenario_result.get("valid", False)),
            "metric_deltas": dict(scenario_result.get("metric_deltas", {}) or {}),
        }

    def _serialize_scenario(self, scenario: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in scenario.items():
            if hasattr(value, "as_dict"):
                out[key] = value.as_dict()
            else:
                out[key] = value
        return out

    def _aggregate_to_unit_interval(self, value: float) -> float:
        if math.isnan(value):
            return 0.0
        return float(1.0 / (1.0 + math.exp(-value)))

    def _stability_score(self, reference: Any, value: Any, tolerance_ratio: float) -> float:
        if reference in (None, 0) or value is None:
            return 0.0
        reference_f = float(reference)
        value_f = float(value)
        tolerance = max(abs(reference_f) * tolerance_ratio, 1e-9)
        return float(max(0.0, 1.0 - abs(value_f - reference_f) / tolerance))

    def _preferred_q_score(self, q_value: float, preferred_q: float, tolerance: float) -> float:
        tolerance = max(float(tolerance), 1e-9)
        return float(max(0.0, 1.0 - abs(q_value - preferred_q) / tolerance))

    def _positive_ratio_score(self, value: float, reference: float) -> float:
        reference = max(abs(reference), 1e-9)
        ratio = value / reference
        return float(max(0.0, min(1.0, ratio)))

    def _vocal_headroom_score(self, features: Mapping[str, Any]) -> float:
        band_stats = dict(features.get("band_stats", {}) or {})
        mid = float(dict(band_stats.get("mid", {}) or {}).get("mean", 0.0))
        high = float(dict(band_stats.get("high", {}) or {}).get("mean", 0.0))
        low = float(dict(band_stats.get("low", {}) or {}).get("mean", 0.0))
        denom = max(low + mid + high, 1e-9)
        normalized_high = high / denom
        return float(max(0.0, min(1.0, 1.0 - normalized_high)))

    def _tract_target_fit(self, features: Mapping[str, Any], tract: VocalTractPreset) -> float:
        band_stats = dict(features.get("band_stats", {}) or {})
        if tract.id == "tongue_high":
            target_value = float(dict(band_stats.get("high", {}) or {}).get("mean", 0.0))
            reference = float(dict(band_stats.get("mid", {}) or {}).get("mean", 0.0)) + 1e-9
            raw = 1.0 - min(target_value / reference, 1.5) / 1.5
        elif tract.id == "tongue_low":
            target_value = float(dict(band_stats.get("mid", {}) or {}).get("mean", 0.0))
            reference = float(dict(band_stats.get("high", {}) or {}).get("mean", 0.0)) + 1e-9
            raw = 1.0 - min(target_value / reference, 1.5) / 1.5
        else:
            raw = 0.5 + 0.5 * self._vocal_headroom_score(features)
        return float(max(0.0, min(1.0, 0.5 * raw + 0.5 * tract.impedance_sensitivity)))
