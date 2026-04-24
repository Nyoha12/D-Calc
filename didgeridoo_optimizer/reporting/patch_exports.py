from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


ACCEPTED_DECISIONS = {"accept_local_only", "accept_family", "accept_weighted"}


def _empty_patch() -> dict[str, Any]:
    return {"materials": {}}


def _normalize_patch(patch: Mapping[str, Any] | None) -> dict[str, Any]:
    materials_patch = dict((patch or {}).get("materials", {}) or {})
    normalized_materials: dict[str, dict[str, Any]] = {}
    for material_id, updates in materials_patch.items():
        normalized_updates: dict[str, Any] = {}
        for key, value in dict(updates or {}).items():
            try:
                normalized_updates[str(key)] = float(value)
            except (TypeError, ValueError):
                normalized_updates[str(key)] = value
        normalized_materials[str(material_id)] = normalized_updates
    return {"materials": normalized_materials}


def _patch_material_ids(patch: Mapping[str, Any] | None) -> list[str]:
    return sorted(str(material_id) for material_id in dict((patch or {}).get("materials", {}) or {}).keys())


def _decision_status(value: Any) -> str | None:
    if isinstance(value, Mapping):
        status = value.get("status")
        return None if status is None else str(status)
    if value is None:
        return None
    return str(value)


def derive_patch_state(report: Mapping[str, Any]) -> dict[str, Any]:
    decision_status = _decision_status(report.get("decision"))
    proposal_patch = _normalize_patch(
        report.get("patch_proposal")
        or dict((report.get("proposals", {}) or {})).get("patch", {})
    )
    replayed_patch = _normalize_patch(
        report.get("patch_replayed")
        or report.get("directed_patch")
        or report.get("semidirected_patch")
        or report.get("family_patch")
        or report.get("family_multiseed_patch")
        or report.get("weighted_patch")
        or {}
    )
    accepted_patch = _normalize_patch(
        report.get("patch_accepted")
        or (replayed_patch if decision_status in ACCEPTED_DECISIONS else _empty_patch())
    )
    to_calibrate_patch = _normalize_patch(
        report.get("patch_to_calibrate")
        or (replayed_patch if decision_status == "keep_as_to_calibrate" else _empty_patch())
    )
    patch_status = {
        "decision": decision_status,
        "validation_preserved": bool(report.get("validation_preserved", False)),
        "proposal_patch_material_ids": _patch_material_ids(proposal_patch),
        "replayed_patch_material_ids": _patch_material_ids(replayed_patch),
        "accepted_patch_material_ids": _patch_material_ids(accepted_patch),
        "patch_to_calibrate_material_ids": _patch_material_ids(to_calibrate_patch),
    }
    return {
        "patch_proposal": proposal_patch,
        "patch_replayed": replayed_patch,
        "patch_accepted": accepted_patch,
        "patch_to_calibrate": to_calibrate_patch,
        "patch_status": patch_status,
    }


def export_patch_state_files(report: Mapping[str, Any], out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    state = derive_patch_state(report)

    file_payloads = {
        "proposal_patch_yaml": ("materials_patch_suggestions.yaml", state["patch_proposal"]),
        "replayed_patch_yaml": ("materials_patch_replayed.yaml", state["patch_replayed"]),
        "accepted_patch_yaml": ("materials_patch_accepted.yaml", state["patch_accepted"]),
        "to_calibrate_patch_yaml": ("materials_patch_to_calibrate.yaml", state["patch_to_calibrate"]),
        "patch_status_yaml": ("materials_patch_status.yaml", state["patch_status"]),
    }

    exports: dict[str, str] = {}
    for key, (filename, payload) in file_payloads.items():
        path = out / filename
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
        exports[key] = str(path)
    return exports


def _read_report(report_path: str | Path) -> dict[str, Any]:
    path = Path(report_path)
    with open(path, "r", encoding="utf-8") as handle:
        if path.suffix.lower() == ".json":
            return dict(json.load(handle) or {})
        return dict(yaml.safe_load(handle) or {})


def backfill_patch_state_exports(report_path: str | Path) -> dict[str, str]:
    report_file = Path(report_path)
    report = _read_report(report_file)
    return export_patch_state_files(report, report_file.parent)


def backfill_patch_state_exports_in_directory(directory: str | Path) -> list[dict[str, Any]]:
    root = Path(directory)
    outputs: list[dict[str, Any]] = []
    candidate_names = ("calibration_report.yaml", "calibration_report.json")
    for report_file in sorted(root.rglob("*")):
        if report_file.name not in candidate_names or not report_file.is_file():
            continue
        if report_file.name == "calibration_report.json" and report_file.with_name("calibration_report.yaml").exists():
            continue
        outputs.append({
            "report_path": str(report_file),
            "exports": backfill_patch_state_exports(report_file),
        })
    return outputs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill patch-state YAML exports from calibration reports.")
    parser.add_argument("paths", nargs="+", help="Calibration report files or directories to process.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    processed = 0
    for raw_path in args.paths:
        path = Path(raw_path)
        if path.is_dir():
            for item in backfill_patch_state_exports_in_directory(path):
                print(json.dumps(item, ensure_ascii=False))
                processed += 1
            continue
        exports = backfill_patch_state_exports(path)
        print(json.dumps({"report_path": str(path), "exports": exports}, ensure_ascii=False))
        processed += 1
    return 0 if processed > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
