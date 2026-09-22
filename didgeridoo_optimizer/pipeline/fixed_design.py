"""One supplied physical design, one linear evaluation, no optimizer phases."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from ..materials import MaterialDatabase
from ..reporting.fixed_design import NOT_EXECUTED, SCHEMA_VERSION, WORKFLOW, export_bundle, prepare_payload
from .design_input import load_design, validate_analysis
from .evaluate_linear import LinearEvaluationPipeline


def _file_source(path: Path, *, optional: bool = False) -> dict[str, Any]:
    if optional and not path.exists():
        return {"path": str(path), "sha256": None, "read": False, "reason": "Optional variant rules absent."}
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "read": True}


def _software_source() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    try:
        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        head = git("rev-parse", "--verify", "HEAD")
        dirty = bool(git("status", "--porcelain"))
        return {"sha": head, "origin": "git HEAD of installed source checkout", "working_tree_dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"sha": None, "origin": "Git source revision could not be established", "working_tree_dirty": None}


def load_fixed_context(config_path: str | Path, design_path: str | Path, *, output_dir_override: str | Path | None = None) -> dict[str, Any]:
    # Reuse only existing read/schema/path helpers, never load_context (which builds other phases).
    from .run_optimizer import OptimizerRunner

    adapter = OptimizerRunner()
    config_file = Path(config_path).resolve()
    design_file = Path(design_path).resolve()
    sources = {"config": _file_source(config_file), "design": _file_source(design_file)}
    config_file, config = adapter._load_config_file(config_file)
    config = adapter._apply_output_dir_override(config, output_dir_override)
    effective = validate_analysis(config)
    schema = adapter._config_schema_metadata(config)
    materials_cfg = adapter._config_section(config, "materials")
    materials_path = adapter._resolve_path(config_file, materials_cfg.get("database_file", "materials_base_v1.yaml"))
    variants_path = adapter._resolve_path(config_file, materials_cfg.get("variant_rules_file", "wood_variant_rules_v1.yaml"))
    sources["materials"] = _file_source(materials_path)
    sources["variant_rules"] = _file_source(variants_path, optional=True)
    material_db = MaterialDatabase.from_yaml(materials_path, variant_rules_path=variants_path)
    design_file, design = load_design(design_file, material_db, config)
    # If files changed while loading, do not claim a fingerprint for different bytes.
    for label, source in sources.items():
        if _file_source(Path(source["path"]), optional=label == "variant_rules") != source:
            raise ValueError(f"Input changed while loading: {source['path']}")
    warnings = [] if sources["variant_rules"]["read"] else [f"Variant rules file absent: {variants_path}; only resolvable material IDs are accepted."]
    return {
        "config": config,
        **schema,
        "design": design,
        "material_db": material_db,
        "materials_used": {material_id: material_db.get(material_id).as_dict() for material_id in dict.fromkeys(design.material_ids)},
        "effective_parameters": effective,
        "provenance": {"files": sources, "software": _software_source()},
        "output_dir": adapter._resolve_output_dir(config_file, config, create=False),
        "warnings": warnings,
    }


def run_fixed_design(config_path: str | Path, design_path: str | Path, *, dry_run: bool = False, output_dir_override: str | Path | None = None) -> dict[str, Any]:
    context = load_fixed_context(config_path, design_path, output_dir_override=output_dir_override)
    response = {
        "schema_version": SCHEMA_VERSION,
        "workflow": WORKFLOW,
        "ok": True,
        "dry_run": dry_run,
        "design_id": context["design"].id,
        "design_path": context["provenance"]["files"]["design"]["path"],
        "config_path": context["provenance"]["files"]["config"]["path"],
        "output_dir": str(context["output_dir"]),
        "output_dir_created": False,
        "not_executed": NOT_EXECUTED,
        "warnings": context["warnings"],
    }
    if dry_run:
        response["geometry_valid"] = True
        response["effective_parameters"] = context["effective_parameters"]
        response["provenance"] = context["provenance"]
        return response
    result = LinearEvaluationPipeline().evaluate(context["design"], context["config"], context["material_db"])
    payload = prepare_payload(result, context)
    existed = context["output_dir"].exists()
    response["exports"] = export_bundle(payload, context["output_dir"])
    response["output_dir_created"] = not existed
    response["valid"] = payload["valid"]
    response["warnings"] = payload["warnings"]
    return response
