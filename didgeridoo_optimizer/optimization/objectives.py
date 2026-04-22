from __future__ import annotations

from typing import Any, Mapping

from ..geometry.models import Design


def score_objectives(features: Mapping[str, Any], design: Design, config: Mapping[str, Any] | None) -> dict[str, float]:
    objectives = dict((config or {}).get("objectives", {}) or {})
    scores: dict[str, float] = {}
    for name, objective_cfg in objectives.items():
        if not bool(dict(objective_cfg or {}).get("enabled", False)):
            continue
        cfg = dict(objective_cfg or {})
        if name == "drone_f0":
            f0 = features.get("f0_hz")
            target_range = cfg.get("target_range_hz", [55.0, 75.0])
            if f0 is None:
                value = 0.0
            else:
                lo, hi = float(target_range[0]), float(target_range[1])
                center = 0.5 * (lo + hi)
                half = max(0.5 * (hi - lo), 1e-9)
                value = max(0.0, 1.0 - abs(float(f0) - center) / half)
        elif name == "impedance_peaks":
            value = min(1.0, float(features.get("peak_count", 0)) / 8.0)
        elif name == "peak_quality_Q":
            q = features.get("fundamental_q")
            if q is None:
                value = 0.0
            else:
                target_mode = str(cfg.get("target_mode", "balanced"))
                target = 12.0 if target_mode == "balanced" else 20.0
                value = max(0.0, 1.0 - abs(float(q) - target) / max(target, 1e-9))
        elif name == "harmonicity":
            err = features.get("harmonicity_error")
            value = 0.0 if err is None else max(0.0, 1.0 - float(err) / 0.5)
        elif name == "backpressure":
            bp = float(features.get("backpressure_proxy", 0.0))
            value = bp / (bp + 1e6)
        elif name == "radiation_brightness":
            bright = float(features.get("brightness_proxy", 0.0))
            value = bright / (bright + 1.0)
        elif name == "toot":
            ratio = features.get("toot_ratio")
            quality = features.get("toot_quality")
            if ratio is None:
                value = 0.0
            else:
                lo, hi = [float(v) for v in cfg.get("preferred_ratio_range", [2.5, 3.2])]
                center = 0.5 * (lo + hi)
                half = max(0.5 * (hi - lo), 1e-9)
                ratio_score = max(0.0, 1.0 - abs(float(ratio) - center) / half)
                value = 0.5 * ratio_score + 0.5 * float(quality or 0.0)
        elif name == "fabrication_simplicity":
            value = 1.0 / max(design.segment_count, 1)
        elif name == "material_simplicity":
            value = 1.0 / max(len(set(design.material_ids)), 1)
        elif name == "beginner_robustness":
            confidence = float(features.get("model_confidence", 0.0))
            q = float(features.get("fundamental_q") or 0.0)
            value = max(0.0, min(1.0, 0.7 * confidence + 0.3 * max(0.0, 1.0 - q / 30.0)))
        elif name == "expert_robustness":
            confidence = float(features.get("model_confidence", 0.0))
            q = float(features.get("fundamental_q") or 0.0)
            value = max(0.0, min(1.0, 0.5 * confidence + 0.5 * min(q / 20.0, 1.0)))
        else:
            value = 0.0
        scores[name] = float(value)
    return scores


def hard_constraints_ok(features: Mapping[str, Any], design: Design, config: Mapping[str, Any] | None) -> bool:
    objectives = dict((config or {}).get("objectives", {}) or {})
    for name, objective_cfg in objectives.items():
        cfg = dict(objective_cfg or {})
        if not bool(cfg.get("enabled", False)) or not bool(cfg.get("hard_constraint", False)):
            continue
        if name == "drone_f0":
            f0 = features.get("f0_hz")
            lo, hi = [float(v) for v in cfg.get("target_range_hz", [55.0, 75.0])]
            if f0 is None or not (lo <= float(f0) <= hi):
                return False
        elif name == "impedance_peaks":
            if int(features.get("peak_count", 0)) <= 0:
                return False
    return True


def penalties(design: Design, features: Mapping[str, Any], config: Mapping[str, Any] | None) -> dict[str, float]:
    materials_cfg = dict(dict((config or {}).get("materials", {}) or {}).get("complexity_penalty", {}) or {})
    material_change_weight = float(materials_cfg.get("extra_penalty_per_material_change", 0.03))
    material_changes = sum(1 for a, b in zip(design.material_ids[:-1], design.material_ids[1:]) if a != b)
    geometry_soft = float(design.metadata.get("geometry_soft_penalty", 0.0))
    topology_penalty = 0.0 if all(segment.kind in {"mouthpiece", "cylinder", "cone", "flare_conical", "flare_exponential", "flare_powerlaw"} for segment in design.segments) else 1.0
    low_confidence_penalty = max(0.0, 1.0 - float(features.get("model_confidence", 0.0)))
    segment_count_penalty = max(0.0, (design.segment_count - 6) / 10.0)
    result = {
        "segment_count_penalty": float(segment_count_penalty),
        "material_change_penalty": float(material_changes * material_change_weight),
        "topology_penalty": float(topology_penalty),
        "low_confidence_penalty": float(low_confidence_penalty),
        "geometry_soft_penalty": float(geometry_soft),
    }
    result["total_penalty"] = float(sum(result.values()))
    return result


def aggregate_score(objective_scores: Mapping[str, float], penalties_map: Mapping[str, float], config: Mapping[str, Any] | None) -> float:
    objectives = dict((config or {}).get("objectives", {}) or {})
    weighted_sum = 0.0
    total_weight = 0.0
    for name, score in objective_scores.items():
        weight = float(dict(objectives.get(name, {}) or {}).get("weight", 1.0))
        weighted_sum += weight * float(score)
        total_weight += weight
    normalized = weighted_sum / total_weight if total_weight > 0.0 else 0.0
    total_penalty = float(penalties_map.get("total_penalty", 0.0))
    return float(normalized - total_penalty)
