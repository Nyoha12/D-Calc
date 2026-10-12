"""Catalogue readback anchored to the original JOB and native R54 semantics."""
from __future__ import annotations

import csv
import hashlib
import io
import os
from pathlib import Path
import stat
import uuid

from ..optimization.catalogue_robust import (
    MIB, encoded, expected_native_plan, load_inputs, native_documents,
    require, summarize,
)
from ..optimization.design_contract import read_request
from . import adjustment_policy as native

ENVELOPES = {"manifest.json", "receipt.json", "completed.json", "cancelled.json"}
MAX_FILES = 100000
_LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
REQUIRED_DELIVERY = ("plan.json", "result.json", "comparison.csv", "summary.txt", "manifest.json")


def inventory(output, limit):
    """Bound bytes and entry count before hashing; refuse links and devices."""
    output = native.safe_path(output)
    paths = []
    total = 0
    inodes = set()
    seen = 0
    for directory, directories, files in os.walk(output, followlinks=False):
        for name in directories + files:
            seen += 1
            require(seen <= MAX_FILES, "budget nombre d'artefacts")
            path = Path(directory) / name
            mode = path.lstat().st_mode
            require(stat.S_ISDIR(mode) or stat.S_ISREG(mode),
                    "lien ou fichier spécial dans le bundle")
            if stat.S_ISDIR(mode):
                continue
            info = path.stat()
            inode = (info.st_dev, info.st_ino)
            if inode not in inodes:
                total += info.st_size
                inodes.add(inode)
            require(total <= limit, "budget cumulé d'artefacts")
            relative = path.relative_to(output).as_posix()
            if path.name.startswith(".pending-"):
                continue
            if path.parent == output and path.name in ENVELOPES:
                continue
            paths.append((relative, path))
    return {name: native.file_sha256(path) for name, path in sorted(paths)}


def put(output, relative, value, limit):
    put_bytes(output, relative, encoded(value), limit)


def put_bytes(output, relative, content, limit):
    """Publish new files only; preserve incomplete temporary evidence."""
    root = native.safe_path(output)
    path = root / relative
    require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
            "destination relative interne requise")
    require(len(content) <= 4 * MIB, "budget fichier")
    native.safe_path(path, exists=False)
    require(not os.path.lexists(path), "sortie immuable déjà présente")
    inventory(root, limit - len(content))
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(".pending-" + uuid.uuid4().hex)
    with pending.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(pending, path, follow_symlinks=False)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read(path):
    return native.read_json(path)


def inspect_candidates(output, prepared, plan):
    """An invalid candidate bundle cannot contribute a witness."""
    output = Path(output)
    evidence = []
    for candidate in plan["candidates"]:
        ident = candidate["id"]
        directory = output / ident
        attempt_path = output / ("attempt_" + ident + ".json")
        item = dict(id=ident, attempted=False, clean_exit=False, native={})
        if attempt_path.exists():
            attempt = read(attempt_path)
            require(attempt["id"] == ident, "reçu attribué à un autre candidat")
            require(type(attempt.get("collected")) is bool
                    and type(attempt.get("clean_exit")) is bool,
                    "reçu de collecte invalide")
            require(not attempt["clean_exit"] or (
                attempt["collected"] and attempt["exit_code"] == 0
                and not attempt["signals"] and attempt["reason"] is None),
                "reçu de collecte contradictoire")
            item.update(attempted=True, clean_exit=attempt["clean_exit"],
                        reason=attempt["reason"])
        elif directory.exists():
            # Staging or execution may have been interrupted before its receipt.
            item.update(attempted=True, reason="reçu de collecte absent")
        bundle = directory / "native"
        if candidate["preflight"] == "ready" and bundle.exists():
            try:
                documents = native_documents(prepared, plan, candidate, directory)
                for name, expected in documents.items():
                    require(read_request(directory / name)[0] == expected,
                            "document natif substitué: " + ident + "/" + name)
                expected = expected_native_plan(prepared, plan, candidate, directory)
                actual = read(bundle / "plan.json")
                require(native.identity_plan(actual) == expected,
                        "plan natif différent du plan original: " + ident)
                result = native.read_result(bundle)
                if result.get("semantic_verified"):
                    require(native.identity_plan(result["plan"]) == expected,
                            "résultat natif attribué à un autre candidat")
                item["native"] = result
            except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
                item.update(native={}, reason="bundle refusé: " + str(exc))
        evidence.append(item)
    return evidence


def comparison_bytes(result):
    stream = io.StringIO(newline="")
    fields = ["id", "state", "decided", "examined", "execution_complete",
              "request_fully_covered", "preference_values", "reason"]
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(result["candidates"])
    return stream.getvalue().encode("utf-8")


def summary_bytes(result):
    return (
        "Catalogue nominal fini — un même instrument dans tous ses scénarios\n"
        f"Catalogue parcouru : {result['catalogue_complete']}\n"
        f"Candidats décidés : {result['candidates_decided']}\n"
        f"Exécution complète : {result['execution_complete']}\n"
        f"Demande entièrement couverte : {result['request_fully_covered']}\n"
        f"Témoins admissibles : {', '.join(result['admissible_candidates'])}\n"
        f"Meilleurs témoins classables connus : {', '.join(result['best_known'])}\n"
        f"Sélection complète : {result['selection_complete']}\n"
        f"Non résolus : {', '.join(result['unresolved_candidates'])}\n"
        "Hard avant préférence ; ex aequo conservés ; aucune moyenne compensatrice.\n"
        "Ce document nécessite le reçu terminal du catalogue.\n"
        + "\n".join(result["scientific_limits"]) + "\n"
    ).encode("utf-8")


def export(output, prepared, plan):
    limit = plan["budgets"]["output_mib"] * MIB
    evidence = inspect_candidates(output, prepared, plan)
    result = summarize(plan, evidence)
    put(output, "result.json", result, limit)
    put_bytes(output, "comparison.csv", comparison_bytes(result), limit)
    put_bytes(output, "summary.txt", summary_bytes(result), limit)
    put(output, "manifest.json", inventory(output, limit), limit)
    return result


def read_result(output, job):
    """Fresh read; no catalogue producer, R54 producer, search or TMM call."""
    prepared, plan = load_inputs(job)
    output = native.safe_path(output)
    limit = plan["budgets"]["output_mib"] * MIB
    actual_inventory = inventory(output, limit)
    missing_delivery = [name for name in REQUIRED_DELIVERY if not (output / name).is_file()]
    require(read(output / "plan.json") == plan,
            "plan catalogue différent des entrées originales")
    evidence = inspect_candidates(output, prepared, plan)
    result = summarize(plan, evidence)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        require(read(manifest_path) == actual_inventory, "manifeste catalogue incohérent")
    stored = output / "result.json"
    if stored.exists():
        require(read(stored) == result, "sélection/agrégats catalogue incohérents")
        if (output / "comparison.csv").exists():
            require((output / "comparison.csv").read_bytes() == comparison_bytes(result),
                    "CSV catalogue incohérent")
        if (output / "summary.txt").exists():
            require((output / "summary.txt").read_bytes() == summary_bytes(result),
                    "synthèse catalogue incohérente")
    terminal = False
    receipt_path = output / "receipt.json"
    if receipt_path.exists():
        receipt = read(receipt_path)
        require(receipt["execution_complete"] == result["execution_complete"]
                and receipt["ok"] == result["execution_complete"],
                "reçu/agrégats contradictoires")
        if manifest_path.exists():
            require(receipt["manifest_sha256"] == native.file_sha256(manifest_path),
                    "manifeste différent du reçu")
        attempted = [e["id"] for e in evidence if e["attempted"]]
        require(receipt["attempted_candidates"] == attempted,
                "reçu: candidats absents ou réordonnés")
        collected = all(
            (output / ("attempt_" + ident + ".json")).exists()
            and read(output / ("attempt_" + ident + ".json"))["collected"]
            for ident in attempted)
        require(receipt["closure_confirmed"] is collected,
                "reçu: collecte contradictoire")
        marker = output / "completed.json"
        if marker.exists():
            require(read(marker) == {
                "receipt_sha256": native.file_sha256(receipt_path)},
                "reçu terminal contradictoire")
            terminal = collected
    if (output / "cancelled.json").exists():
        cancellation = read(output / "cancelled.json")
        require(cancellation["receipt_sha256"] == native.file_sha256(receipt_path),
                "annulation contradictoire")
        terminal = False
    terminal = terminal and not missing_delivery
    if not terminal:
        result.update(execution_complete=False, request_fully_covered=False,
                      selection_complete=False)
    result.update(ok=bool(terminal and result["execution_complete"]),
                  closure_confirmed=terminal,
                  delivery_files_complete=not missing_delivery,
                  missing_delivery_files=missing_delivery,
                  semantic_verified=all(
                      c["preflight"] != "ready"
                      or not e["attempted"]
                      or e["native"].get("semantic_verified") is True
                      for c, e in zip(plan["candidates"], evidence)))
    return result
