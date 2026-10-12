"""Finite nominal catalogues; native contracts and explicit decision semantics.

No search, mesh, acoustic evaluation or output is performed by this module.
The original documents remain the readback anchor.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
from types import FunctionType
from fractions import Fraction
from pathlib import Path

from .design_contract import (
    InvalidRequest, UNITS, identifier, integer, number, obj, read_request,
)
from .adjustment_policy import bind_policy, groups, job_contract as policy_contract
from ..geometry.tolerance_scenarios import (
    _field_info, check_mask, parse_scenarios, perturb, ScenarioUnavailable,
)

SCHEMA = "dcalc.catalogue_robust.v1"
ROOT = Path(__file__).resolve().parents[2]
MIB = 1024**2
LIMITS = {"seconds": 600, "output_mib": 200}
CODE = (
    "didgeridoo_optimizer/optimization/catalogue_robust.py",
    "didgeridoo_optimizer/pipeline/catalogue_robust.py",
    "didgeridoo_optimizer/reporting/catalogue_robust.py",
    "tools/catalogue_robust.py",
)


_LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise InvalidRequest(message)


def encoded(value):
    # Preserve insertion order: native assemblies expose physical piece order.
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
            + "\n").encode("utf-8")


def source_of(value, name):
    return {"name": name, "sha256": hashlib.sha256(encoded(value)).hexdigest(),
            "read": True}


def code_sources():
    """Bind available code to the bytes captured by each imported catalogue module.

    Hashes are relative provenance, not external authentication. An unimported
    module is a referenced source; a loaded module must still match its snapshot.
    """
    current = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
               for name in CODE}
    for name in CODE:
        module_name = name[:-3].replace("/", ".")
        modules = [sys.modules.get(module_name)]
        main = sys.modules.get("__main__")
        if getattr(main, "__file__", None) and Path(main.__file__).resolve() == ROOT / name:
            modules.append(main)
        for module in modules:
            if module is None:
                continue
            require(Path(module.__file__).resolve() == ROOT / name,
                    "source catalogue chargée hors de son chemin: " + name)
            require(getattr(module, "_LOADED_SOURCE_SHA256", None) == current[name],
                    "source catalogue modifiée depuis chargement: " + name)
            for value in tuple(vars(module).values()):
                if isinstance(value, FunctionType) and value.__module__ == module.__name__:
                    require(Path(value.__code__.co_filename).resolve() == ROOT / name,
                            "code catalogue chargé différent du chemin déclaré: " + name)
    return current


def reference(parent, value):
    require(isinstance(value, str) and value and not Path(value).is_absolute(),
            "référence relative au JOB requise")
    return (parent / value).resolve(strict=True)


def job_contract(raw):
    required = {"schema_version", "adjustment_job", "catalogue",
                "nominal_mask", "budgets"}
    obj(raw, required | {"preferences"}, "CATALOGUE_JOB", required)
    require(raw["schema_version"] == SCHEMA, "version catalogue inconnue")
    obj(raw["budgets"], LIMITS, "budgets", LIMITS)
    limits = {k: integer(raw["budgets"][k], 1, cap, "budgets." + k)
              for k, cap in LIMITS.items()}
    entries = raw["catalogue"]
    require(isinstance(entries, list) and 1 <= len(entries) <= 8,
            "catalogue: 1..8 candidats explicites")
    ids = set()
    for entry in entries:
        obj(entry, {"id", "geometry"}, "candidat", {"id", "geometry"})
        ident = identifier(entry["id"], "candidat.id")
        require(ident not in ids, "candidat.id dupliqué")
        ids.add(ident)
    mask = raw["nominal_mask"]
    require(isinstance(mask, list) and len(mask) <= 64, "masque: liste <=64")
    fields = set()
    for item in mask:
        obj(item, {"field", "unit", "bounds"}, "masque",
            {"field", "unit", "bounds"})
        require(isinstance(item["field"], str) and item["field"] not in fields,
                "champ nominal dupliqué ou invalide")
        fields.add(item["field"])
        require(item["unit"] in ("m", "cm", "mm"), "masque dimensionnel requis")
        bounds = item["bounds"]
        require(isinstance(bounds, list) and len(bounds) == 2,
                "deux bornes nominales requises")
        lo, hi = [number(v, "borne nominale") for v in bounds]
        require(lo < hi, "bornes nominales strictement ordonnées")
    preferences = raw.get("preferences", [])
    require(isinstance(preferences, list) and len(preferences) <= 8,
            "preferences: liste <=8")
    priorities = set()
    for preference in preferences:
        obj(preference, {"field", "unit", "direction", "priority"},
            "préférence", {"field", "unit", "direction", "priority"})
        require(isinstance(preference["field"], str), "champ de préférence requis")
        require(preference["unit"] in ("m", "cm", "mm"),
                "préférence: unité de longueur requise")
        require(preference["direction"] in ("min", "max"),
                "préférence: min ou max requis")
        priority = integer(preference["priority"], 1, 8, "priorité")
        require(priority not in priorities, "priorité dupliquée")
        priorities.add(priority)
    return limits, sorted(preferences, key=lambda p: p["priority"])


def check_declared_positions(raw, kind):
    """Reject stale declared derived data before native normalization."""
    if kind != "fixed":
        return
    position = 0.0
    for segment in raw["segments"]:
        length = number(segment["length_cm"], "length_cm")
        for key, expected in (("position_start_cm", position),
                              ("position_end_cm", position + length)):
            if key in segment:
                value = number(segment[key], key)
                require(math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12),
                        "position déclarée incohérente: " + key)
        position += length
    total = raw.get("metadata", {}).get("total_length_cm")
    if total is not None:
        require(math.isclose(number(total, "total_length_cm"), position,
                             rel_tol=1e-12, abs_tol=1e-12),
                "total_length_cm déclaré incohérent")


def identity_geometry(raw, kind):
    value = copy.deepcopy(raw)
    value.pop("id", None)
    annotations = value.get("metadata" if kind == "fixed" else "annotations", {})
    annotations.pop("catalogue_label", None)
    return value


def check_nominal_mask(base, candidate, mask, kind):
    check_declared_positions(base, kind)
    check_declared_positions(candidate, kind)
    fields = []
    for item in mask:
        field = item["field"]
        _field_info(base, field, kind)
        value, dimension, _ = _field_info(candidate, field, kind)
        require(dimension == "length", "masque non dimensionnel")
        factor = Fraction(str(UNITS[item["unit"]][1]))
        lo, hi = [Fraction(str(v)) * factor for v in item["bounds"]]
        require(lo <= Fraction(str(value)) <= hi,
                "candidat hors borne nominale: " + field)
        fields.append(field)
    if kind == "assembly":
        for key in ("pieces", "configurations"):
            require(list(base[key]) == list(candidate[key]),
                    "identités/ordre communs modifiés: " + key)
    check_mask(identity_geometry(base, kind), identity_geometry(candidate, kind),
               {"exposed_fields": fields}, kind)


def preference_values(raw, kind, preferences):
    values = []
    for preference in preferences:
        field = preference["field"]
        try:
            if field == "total_length":
                require(kind == "fixed",
                        "total_length sans configuration réservé au DESIGN fixe")
                value = sum(Fraction(str(s["length_cm"]))
                            for s in raw["segments"]) / 100
            else:
                value, dimension, _ = _field_info(raw, field, kind)
                require(dimension == "length", "préférence non dimensionnelle")
                value = Fraction(str(value))
            values.append(str(value))
        except (ValueError, KeyError, ArithmeticError):
            # Missing preference evidence never changes a hard verdict.
            values.append(None)
    return values


def _snapshot(context):
    db = context["material_db"]
    return dict(materials=copy.deepcopy(db.raw),
                variant_rules=copy.deepcopy(db.variant_generator.rules)
                if db.variant_generator else None)


def _candidate_audit(raw, base, config_path, policy, limits):
    """Rebind native validators to this nominal input; never reuse a design."""
    from ..geometry.assemblies import Assembly
    from .assembly_contract import AssemblyContract
    from ..pipeline import tolerance_audit as audit
    from ..pipeline.design_input import validate_design
    from ..pipeline.design_pitch import models
    from ..pipeline.fixed_design import load_analysis_context

    kind = base["job"]["input"]["kind"]
    context = load_analysis_context(config_path)
    require(context["config"] == base["original_config"], "CONFIG modifiée")
    request = base["native_request"]
    if kind == "fixed":
        context["design"] = validate_design(copy.deepcopy(raw),
                                            context["material_db"], context["config"])
        nominal = context["design"].as_dict()
        contract = audit.fixed_contract(request, context)
        projections = [dict(id="fixed", configuration="nominal",
                            request=request, template=contract)]
    else:
        nominal = copy.deepcopy(raw)
        contract = AssemblyContract(
            request, Assembly(nominal, context["material_db"], context["config"]),
            context)
        projections = contract.projections
    parsed = parse_scenarios(base["job"], kind, nominal, contract)
    bound = bind_policy(policy, kind, nominal, request, projections)
    units = groups(bound, projections)
    count = len(parsed["scenarios"])
    require(count * len(units) <= min(limits["candidates"], limits["evaluations"]),
            "budget natif scénarios/groupes")
    require(count * len(projections) <= limits["projections"],
            "budget natif scénarios/projections")
    geometry = []
    minimum = dict(segments=0, frequencies=0,
                   frequency_segment_product=0, spectral_calls=0)
    for scenario in parsed["scenarios"]:
        try:
            effective = perturb(nominal, parsed, scenario, kind)
        except ScenarioUnavailable as exc:
            for projection in projections:
                geometry.append(dict(scenario=scenario["id"],
                                     projection=projection["id"],
                                     issue=dict(status="unresolved", reason=str(exc))))
            continue
        check_mask(nominal, effective, parsed, kind)
        for projection, design, metadata, issue in audit.geometries(
                kind, effective, context, projections):
            if design is not None and kind == "fixed":
                check_mask(nominal, design.as_dict(), parsed, kind)
            geometry.append(dict(
                scenario=scenario["id"], projection=projection["id"], issue=issue,
                geometry_sha256=audit.report.fingerprint(effective),
                piece_order=list(effective["pieces"]) if kind == "assembly" else None,
                profile_sha256=audit.report.fingerprint(design.as_dict())
                if design is not None else None,
                metadata_sha256=audit.report.fingerprint(metadata)))
            criteria = projection["template"].criteria
            if design is not None and any(
                    c["observable"] in ("resonance_frequency", "resonance_ratio")
                    and not c["unsupported_reason"] for c in criteria):
                for key, value in audit._mesh_bound(
                        design, projection["template"]).items():
                    minimum[key] += value
    for key, value in minimum.items():
        require(value <= base["budgets"][key] and value <= limits[key],
                "budget minimal des grilles: " + key)
    uncovered = []
    if base["job"]["coverage"]["kind"] == "continuous":
        uncovered.append("JOB: continuum dimensionnel non couvert")
    if kind == "assembly" and contract.coverage["kind"] != "discrete":
        uncovered.append("ASSEMBLY_REQUEST: continuum de configurations non couvert")
    for projection in projections:
        for criterion in projection["template"].criteria:
            if criterion["unsupported_reason"]:
                uncovered.append(projection["id"] + ":" + criterion["id"] + ": "
                                 + criterion["unsupported_reason"])
    result = copy.deepcopy(base)
    result.update(nominal=nominal, parsed=parsed, geometry=geometry,
                  minimum_grid_cost=minimum, uncovered=uncovered,
                  effective_models=[models(context, p["template"].options)[3]
                                    for p in projections],
                  expected_hard={p["id"]: [c["id"] for c in p["template"].criteria
                                           if c["role"] == "hard"]
                                 for p in projections})
    require(result["effective_models"] == base["effective_models"],
            "modèles effectifs différents entre candidats")
    size = len(encoded(result))
    require(size * (4 + limits["evaluations"]) <= limits["memory_mib"] * MIB // 2,
            "budget mémoire natif de l'historique")
    return result, bound, units, _snapshot(context)


def load_inputs(job):
    """Read and validate without staging documents or invoking R54's producer."""
    code_sources()  # Reject changed loaded code before any native preflight.
    from ..pipeline import tolerance_audit as audit

    path = Path(job).resolve(strict=True)
    raw, source = read_request(path)
    limits, preferences = job_contract(raw)
    native_path = reference(path.parent, raw["adjustment_job"])
    native_job, native_source = read_request(native_path)
    policy, native_limits = policy_contract(native_job)
    audit_path = reference(native_path.parent, native_job["audit_job"])
    preview, _ = read_request(audit_path)
    require(len({path, native_path, audit_path}) == 3, "références cycliques")
    obj(preview.get("input"), {"kind", "config", "design", "request",
                              "assembly", "assembly_request"}, "audit.input")
    input_paths = {k: reference(audit_path.parent, v)
                   for k, v in preview["input"].items() if k != "kind"}
    require(not {path, native_path, audit_path}.intersection(input_paths.values()),
            "références communes ambiguës")
    candidate_paths = [reference(path.parent, e["geometry"]) for e in raw["catalogue"]]
    require(len(set(candidate_paths)) == len(candidate_paths),
            "fichier candidat répété")
    forbidden = {path, native_path, audit_path} | {
        p for k, p in input_paths.items() if k not in ("design", "assembly")}
    require(not forbidden.intersection(candidate_paths), "référence candidat ambiguë")
    count = len(candidate_paths)
    # Reserve native output, native logs, staged inputs and catalogue closure.
    require(count * (native_limits["output_mib"] + 16) + 8 <= limits["output_mib"],
            "réservation cumulée d'artefacts insuffisante")
    require(count * len(preview.get("scenarios", []))
            * native_limits["projections"] <= 8 * 65 * 2048,
            "budget produit déclaré")
    prepared_base, base = audit.load_inputs(audit_path)
    base = copy.deepcopy(base)
    base.pop("provenance", None)
    kind = prepared_base["kind"]
    geometry_key = "design" if kind == "fixed" else "assembly"
    base_raw, _ = read_request(input_paths[geometry_key])
    check_declared_positions(base_raw, kind)
    snapshot = _snapshot(prepared_base["context"])
    candidates = []
    for entry, candidate_path in zip(raw["catalogue"], candidate_paths):
        physical, physical_source = read_request(candidate_path)
        check_nominal_mask(base_raw, physical, raw["nominal_mask"], kind)
        row = dict(id=entry["id"], source=physical_source, raw=physical,
                   preference_values=preference_values(physical, kind, preferences),
                   preflight="ready", reason=None)
        try:
            candidate_audit, bound, units, candidate_snapshot = _candidate_audit(
                physical, base, input_paths["config"], policy, native_limits)
            require(candidate_snapshot == snapshot, "octets/contenu DB modifiés")
            row.update(audit_plan=candidate_audit, policy=bound, groups=units)
        except (ValueError, ArithmeticError) as exc:
            row.update(preflight="nominal_geometry_violated"
                       if audit._physical_violation(exc) else "input_unresolved",
                       reason=str(exc))
        candidates.append(row)
    # Recheck all native source fingerprints after the complete preflight.
    from ..pipeline.fixed_design import _file_source
    for label, item in prepared_base["sources"].items():
        require(_file_source(Path(item["path"]), optional=label == "variant_rules")
                == item, "entrée commune modifiée pendant lecture")
    require(read_request(path)[1] == source
            and read_request(native_path)[1] == native_source,
            "JOB modifié pendant lecture")
    for candidate_path, row in zip(candidate_paths, candidates):
        require(read_request(candidate_path)[1] == row["source"],
                "candidat modifié pendant lecture")
    plan = dict(schema_version=SCHEMA, job=raw, source=source,
                adjustment_job=native_job, adjustment_source=native_source,
                native_limits=native_limits, common=base, material_snapshot=snapshot,
                candidates=candidates, preferences=preferences, budgets=limits,
                code_sources=code_sources(), continuous_robustness_certified=False)
    require(len(encoded(plan)) <= 2 * MIB, "plan catalogue supérieur à 2 Mio")
    return dict(job_path=path, input_paths=input_paths), plan


def native_documents(prepared, plan, candidate, directory):
    directory = Path(directory)
    audit_job = copy.deepcopy(plan["common"]["job"])
    kind = audit_job["input"]["kind"]
    geometry_key = "design" if kind == "fixed" else "assembly"
    for key, value in prepared["input_paths"].items():
        audit_job["input"][key] = (
            "nominal.json" if key == geometry_key
            else os.path.relpath(value, directory))
    native_job = copy.deepcopy(plan["adjustment_job"])
    native_job["audit_job"] = "audit.json"
    return {"nominal.json": candidate["raw"], "audit.json": audit_job,
            "policy.json": native_job}


def expected_native_plan(prepared, plan, candidate, directory):
    documents = native_documents(prepared, plan, candidate, directory)
    original = copy.deepcopy(candidate["audit_plan"])
    original["job"] = documents["audit.json"]
    geometry_key = "design" if original["job"]["input"]["kind"] == "fixed" else "assembly"
    original["input_files"][geometry_key] = source_of(documents["nominal.json"],
                                                      "nominal.json")
    original["input_files"]["job"] = source_of(documents["audit.json"], "audit.json")
    policy = candidate["policy"]
    return dict(
        schema_version="dcalc.adjustment_policy.v1",
        job=documents["policy.json"], audit_plan=original, policy=policy,
        groups=candidate["groups"], budgets=plan["native_limits"],
        geometry=original["geometry"], parsed=original["parsed"],
        expected_hard=original["expected_hard"], uncovered=original["uncovered"],
        material_snapshot=plan["material_snapshot"],
        adjustment_job_source=source_of(documents["policy.json"], "policy.json"),
        continuous_robustness_certified=False,
        information_statement=(
            "Existence conditionnelle: fabrication connue du calcul; ni identification ni contrôleur."
            if policy["kind"] != "fixed"
            else "Commandes fournies fixes, aucune liberté supplémentaire."))


def summarize(plan, evidence):
    """One evidence item per physical candidate, never per scenario winner."""
    require([e["id"] for e in evidence] == [c["id"] for c in plan["candidates"]],
            "identité/ordre des candidats")
    rows = []
    for candidate, item in zip(plan["candidates"], evidence):
        native = item.get("native", {})
        verified = native.get("semantic_verified") is True
        preflight = candidate["preflight"]
        if preflight == "nominal_geometry_violated":
            state = "excluded_nominal_geometry"
        elif verified and native.get("sampled_policy_conforming") is True:
            state = "admissible"
        elif (verified and plan["adjustment_job"]["policy"]["kind"] == "fixed"
              and native.get("counterexample_found") is True):
            state = "excluded_fixed_counterexample"
        elif preflight == "ready" and not item.get("attempted"):
            state = "unexamined"
        else:
            state = "unresolved"
        decided = state == "admissible" or state.startswith("excluded_")
        complete = (preflight == "nominal_geometry_violated" or
                    verified and native.get("execution_complete") is True
                    and item.get("clean_exit") is True)
        rows.append(dict(
            id=candidate["id"], state=state, decided=decided,
            examined=preflight != "ready" or bool(item.get("attempted")),
            semantic_verified=verified, execution_complete=bool(complete),
            request_fully_covered=bool(verified and native.get("request_fully_covered")),
            preference_values=candidate["preference_values"],
            reason=item.get("reason") or candidate["reason"],
            native_status=native.get("status"),
            native_counters=native.get("counters", {})))
    admissible = [r["id"] for r in rows if r["state"] == "admissible"]
    unresolved = [r["id"] for r in rows if not r["decided"]]
    preferences = plan["preferences"]
    rankable = [r for r in rows if r["state"] == "admissible"
                and all(v is not None for v in r["preference_values"])]
    missing = [r["id"] for r in rows if r["state"] == "admissible"
               and any(v is None for v in r["preference_values"])]
    best = []
    if preferences and rankable:
        def key(row):
            return tuple(Fraction(v) * (1 if p["direction"] == "min" else -1)
                         for v, p in zip(row["preference_values"], preferences))
        optimum = min(map(key, rankable))
        best = [r["id"] for r in rankable if key(r) == optimum]
    execution = all(r["execution_complete"] for r in rows)
    catalogue = all(r["examined"] for r in rows)
    covered = all(r["request_fully_covered"] for r in rows)
    # A proved geometric exclusion settles that contender without acoustics.
    # Keep global REQUEST coverage false, but do not invent an unknown rival.
    comparison_covered = all(r["request_fully_covered"]
                             or r["state"] == "excluded_nominal_geometry" for r in rows)
    selection = bool(preferences and execution and catalogue and comparison_covered
                     and not unresolved and not missing)
    return dict(
        schema_version=SCHEMA, candidates=rows,
        execution_complete=execution, catalogue_complete=catalogue,
        candidates_decided=not unresolved, request_fully_covered=covered,
        comparison_fully_covered=comparison_covered,
        admissible_candidates=admissible, unresolved_candidates=unresolved,
        best_known=best, preference_unresolved_candidates=missing,
        selection_complete=selection,
        selection_scope="témoins vérifiés et classables du catalogue fini",
        continuous_robustness_certified=False, global_optimum_proven=False,
        counters=dict(
            nominal_candidates=len(rows),
            examined_candidates=sum(r["examined"] for r in rows),
            decided_candidates=sum(r["decided"] for r in rows),
            declared_scenarios=len(plan["common"]["parsed"]["scenarios"]),
            command_trials=sum(r["native_counters"].get("evaluations", 0) for r in rows),
            projections=sum(r["native_counters"].get("projections", 0) for r in rows),
            spectral_calls=sum(r["native_counters"].get("spectral_calls", 0) for r in rows)),
        scientific_limits=[
            "Scénarios finis prescrits; aucune garantie sur un continuum.",
            "Recherche adaptative locale: un échec ne prouve aucune impossibilité.",
            "Les scalaires acoustiques relus ne sont pas recalculés.",
            "Intégrité relative et cohérence sémantique; aucune authentification externe.",
            "Aucune mesure, validation A-E ou promotion matérielle.",
        ])
