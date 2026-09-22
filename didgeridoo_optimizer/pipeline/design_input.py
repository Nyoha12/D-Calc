"""Strict boundary for physical fixed-design files; geometry policy stays in geometry."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from numbers import Real
from pathlib import Path
from typing import Any

import yaml

from ..geometry import Design, DesignBuilder, GeometryValidator
from ..geometry.models import SUPPORTED_SEGMENT_KINDS
from ..materials import MaterialDatabase


def finite_real(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field}: expected a real number (not a boolean)")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field}: expected a finite real number") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{field}: expected a finite {'strictly positive ' if positive else ''}number")
    return number


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field}: expected a mapping")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field}: keys must be strings")
    return dict(value)


def _keys(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{field}: unknown fields {sorted(unknown)}; put annotations in metadata")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"design: non-standard JSON constant {value}")


def load_design(path: str | Path, materials: MaterialDatabase, config: Mapping[str, Any]) -> tuple[Path, Design]:
    # Unlike material/config helper resolution, an explicit design has no fallback.
    source = Path(path).resolve()
    try:
        text = source.read_text(encoding="utf-8-sig")
        if source.suffix.lower() == ".json":
            raw = json.loads(text, parse_constant=_reject_json_constant)
        elif source.suffix.lower() in {".yaml", ".yml"}:
            raw = yaml.safe_load(text)
        else:
            raise ValueError("design: expected a .yaml, .yml or .json file")
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ValueError(f"design {source}: syntax error: {exc}") from exc
    return source, validate_design(raw, materials, config)


def validate_design(raw: Any, materials: MaterialDatabase, config: Mapping[str, Any]) -> Design:
    data = _mapping(raw, "design")
    _keys(data, {"id", "segments", "metadata"}, "design")
    design_id = data.get("id", "fixed_design")
    if not isinstance(design_id, str) or not design_id.strip():
        raise ValueError("design.id: expected a non-empty string")
    metadata = _mapping(data.get("metadata", {}), "design.metadata")
    if metadata.get("is_discretized"):
        raise ValueError("design.metadata.is_discretized: supply a physical design, not an analysis_design mesh")
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("design.metadata: expected finite, JSON-compatible annotations") from exc
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("design.segments: expected a non-empty list")
    normalized = []
    required = {"kind", "length_cm", "d_in_cm", "d_out_cm", "material_id"}
    for index, entry in enumerate(segments):
        field = f"design.segments[{index}]"
        segment = _mapping(entry, field)
        _keys(segment, required | {"profile_params", "position_start_cm", "position_end_cm"}, field)
        missing = required - set(segment)
        if missing:
            raise ValueError(f"{field}: missing required fields {sorted(missing)}")
        kind = segment["kind"]
        if not isinstance(kind, str) or kind not in SUPPORTED_SEGMENT_KINDS:
            raise ValueError(f"{field}.kind: unsupported kind {kind!r}")
        if kind in {"branch", "helmholtz_neck"}:
            raise ValueError(f"{field}.kind: {kind} is not implemented in the linear workflow")
        for key in ("length_cm", "d_in_cm", "d_out_cm"):
            segment[key] = finite_real(segment[key], f"{field}.{key}", positive=True)
        for key in ("position_start_cm", "position_end_cm"):
            if key in segment:
                finite_real(segment.pop(key), f"{field}.{key}")
        material_id = segment["material_id"]
        if not isinstance(material_id, str) or not material_id.strip():
            raise ValueError(f"{field}.material_id: expected a non-empty string")
        try:
            materials.get(material_id)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{field}.material_id: {exc}") from exc
        params = _mapping(segment.get("profile_params", {}), f"{field}.profile_params")
        allowed = {"throat_diameter_cm"} if kind == "mouthpiece" else set()
        if kind.startswith("flare_"):
            allowed = {"flare_parameter", "power"} if kind == "flare_powerlaw" else {"flare_parameter"}
        _keys(params, allowed, f"{field}.profile_params")
        segment["profile_params"] = {
            key: finite_real(value, f"{field}.profile_params.{key}", positive=key == "throat_diameter_cm")
            for key, value in params.items()
        }
        normalized.append(segment)
    design = DesignBuilder().build({"id": design_id, "segments": normalized, "metadata": metadata})
    errors = GeometryValidator().validate(design, config)
    if errors:
        raise ValueError("design.geometry: " + "; ".join(errors))
    return design


def validate_analysis(config: Mapping[str, Any]) -> dict[str, Any]:
    freq = _mapping(config.get("frequency_analysis", {}), "config.frequency_analysis")
    env = _mapping(config.get("environment", {}), "config.environment")
    frequency = {
        key: finite_real(freq.get(key, default), f"config.frequency_analysis.{key}", positive=True)
        for key, default in (("f_min_hz", 10.0), ("f_max_hz", 5000.0), ("discretization_max_segment_cm", 1.0))
    }
    if frequency["f_max_hz"] <= frequency["f_min_hz"]:
        raise ValueError("config.frequency_analysis.f_max_hz: must exceed f_min_hz")
    count = freq.get("n_points", 4096)
    if isinstance(count, bool) or not isinstance(count, int) or count < 2:
        raise ValueError("config.frequency_analysis.n_points: expected an integer >= 2 (not a boolean)")
    frequency["n_points"] = count
    air = {
        key: finite_real(env.get(key, default), f"config.environment.{key}", positive=positive)
        for key, default, positive in (
            ("air_density_kg_m3", 1.204, True), ("sound_speed_m_s", 343.0, True),
            ("air_temperature_c", 20.0, False), ("relative_humidity_percent", 50.0, False),
        )
    }
    return {"frequency_analysis": frequency, "environment": air}
