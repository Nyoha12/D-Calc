"""Neutral fixed-design payload and exports, independent of optimizer reports."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .export import _to_builtin, export_json, export_yaml

SCHEMA_VERSION = "dcalc.fixed_design.result.v1"
WORKFLOW = "linear_fixed_design"
NOT_EXECUTED = ["optimization", "pareto", "ranking", "robustness", "nonlinear", "runtime_estimation"]


def _available(value: Any, path: str, unavailable: dict[str, str]) -> Any:
    if isinstance(value, Mapping):
        return {key: _available(item, f"{path}.{key}", unavailable) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_available(item, f"{path}[{index}]", unavailable) for index, item in enumerate(value)]
    if isinstance(value, (complex, np.generic)):
        return _available(_to_builtin(value), path, unavailable)
    if value is None:
        unavailable[path] = "Null value preserved from API result; no numeric value supplied."
    elif isinstance(value, float) and not math.isfinite(value):
        unavailable[path] = "Linear API returned a non-finite diagnostic; unavailable in strict JSON."
        return None
    return value


def prepare_payload(result: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    count = context["effective_parameters"]["frequency_analysis"]["n_points"]
    for field in ("freq_hz", "zin", "zin_mag"):
        curve = np.asarray(result.get(field, []))
        if curve.ndim != 1 or len(curve) != count or not np.all(np.isfinite(curve)):
            raise ValueError(f"calculation result.{field}: expected {count} aligned finite samples")
    if result.get("errors"):
        raise ValueError("calculation failed: " + "; ".join(map(str, result["errors"])))
    unavailable: dict[str, str] = {}
    converted = _available(_to_builtin(result), "result", unavailable)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "workflow": WORKFLOW,
        "ok": True,
        "valid": bool(result["valid"]),
        "result": converted,
        "effective_parameters": context["effective_parameters"],
        "config": context["config"],
        "config_schema_version": context["config_schema_version"],
        "config_schema_status": context["config_schema_status"],
        "materials_used": context["materials_used"],
        "provenance": context["provenance"],
        "not_executed": NOT_EXECUTED,
        "warnings": list(context["warnings"]) + list(result.get("warnings", [])),
        "unavailable_values": unavailable,
    }
    # Validate both formats before any output directory or file is created.
    payload = _to_builtin(payload)
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    return payload


def summarize(payload: Mapping[str, Any]) -> str:
    result = payload["result"]
    features = result["features"]
    lines = [
        f"Fixed design: {result['design_id']}",
        f"Workflow: {WORKFLOW}; schema: {SCHEMA_VERSION}",
        f"ok: true (calculation completed); valid: {str(payload['valid']).lower()} (model constraints)",
        f"Samples: {len(result['freq_hz'])}; aggregate_score (unchanged API): {result['aggregate_score']}",
        f"f0_hz: {features.get('f0_hz')}; peak_count: {features.get('peak_count')}; fundamental_q: {features.get('fundamental_q')}",
        "Not executed, even if configured: " + ", ".join(NOT_EXECUTED) + ".",
        "Interpretation: linear 1D model; valid is not experimental validation.",
        "Zin = p/U in Pa.s/m^3 is input acoustic impedance, not static pressure, a played FFT or an input-output transfer.",
        "The first-two-resonance ratio is not a toot threshold or a guarantee of easy toot/playability.",
        "Material parameter statuses are preserved; this export neither calibrates nor promotes materials.",
        "Warnings: " + (", ".join(payload["warnings"]) or "none"),
    ]
    if payload["unavailable_values"]:
        lines.append("Unavailable values (null; see reasons in result JSON/YAML): " + ", ".join(payload["unavailable_values"]))
    return "\n".join(lines) + "\n"


def export_bundle(payload: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    # Serialization preflight is intentionally repeated for direct callers.
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    summary = summarize(payload)
    paths = {
        "result_json": output_dir / "evaluated_design_result.json",
        "result_yaml": output_dir / "evaluated_design_result.yaml",
        "summary_txt": output_dir / "evaluated_design_summary.txt",
    }
    for path in paths.values():
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing fixed-design output: {path}")
    export_json(payload, paths["result_json"])
    export_yaml(payload, paths["result_yaml"])
    paths["summary_txt"].write_text(summary, encoding="utf-8")
    return {"output_dir": str(output_dir), **{key: str(value) for key, value in paths.items()}}
