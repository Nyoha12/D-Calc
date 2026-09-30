"""Strict, additive exports for the onset diagnostic (not played signals)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path


def source_fingerprints(producer: str | Path | None = None) -> dict:
    root = Path(__file__).resolve().parents[2]
    paths = set()
    if producer is not None:
        # The caller supplies its defining module, not the host's __main__
        # (which may be pytest or another legitimate API consumer).
        actual_producer = Path(producer).resolve()
        if actual_producer != root / "tools/nonlinear_onset_audit.py":
            raise ValueError("Producer CLI is outside the loaded package worktree: mixed source roots")
        paths.add(actual_producer)
    for name, module in tuple(sys.modules.items()):
        if name == "didgeridoo_optimizer" or name.startswith("didgeridoo_optimizer."):
            source = getattr(module, "__file__", None)
            if source:
                paths.add(Path(source).resolve())
    result = {}
    for path in sorted(paths):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("Mixed source installation roots") from exc
        if path.suffix != ".py" or not path.is_file():
            raise ValueError("Loaded Python source is unavailable")
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def strict_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def french_summary(payload: dict) -> str:
    lines = ["NL-SEUIL-01 — diagnostic local d'équilibre et de stabilité", "",
             f"État : {payload['status']}",
             "Convention SI : x positif ouvre ; h=h0+x ; amont idéal Zu=0.",
             "Les paramètres labiaux restent provisoires (to_calibrate).",
             "Les signes de force ne constituent aucune identification physiologique."]
    for closure, groups in payload.get("equilibria", {}).items():
        lines.append(f"Fermeture {closure} : {sum(g['branch_count'] for g in groups)} équilibres conservés.")
        for index, group in enumerate(groups):
            lines.append(f"  Groupe {index} : énumération={group['status']} ; {group['branch_count']} branches conservées ; absence dans le domaine algébrique={group.get('absence_in_algebraic_domain', 'non renseignée')}.")
            if group["ambiguous_roots"]:
                lines.append("    Ambiguïtés : " + strict_json(group["ambiguous_roots"]).strip())
            if group["rejected"]:
                lines.append("    Rejets : " + strict_json(group["rejected"]).strip())
            if not group["branches"]:
                lines.append("    aucune branche conservée ; aucun état d'équilibre à afficher.")
            for row in group["branches"]:
                lines.append(f"  P={row['pressure_pa']:.9g} Pa ; branche {row['branch_id']} ; Pd={row['downstream_pa']:.9g} Pa ; h={row['h_m']:.9g} m ; U={row['flow_m3_s']:.9g} m³/s ; résidu={row['residuals']['scaled_max']:.3g} ; état de branche={row['status']} ; multiplicité={row['algebraic_multiplicity_t']} ; frontière={row['boundary_status'] or 'aucune'} ; raisons non régulières={';'.join(row['non_regular_reasons']) or 'aucune'}.")
    if "fir" in payload:
        fir = payload["fir"]
        lines.extend([f"FIR effectif : {fir['length']} coefficients, fs={fir['sample_rate_hz']} Hz, DC={fir['dc_pa_s_m3']:.9g} Pa.s/m³.",
                      f"Passivité : {fir['passivity']}. Une grille positive ne certifie pas la passivité."])
    for search in payload.get("searches", []):
        lines.append(f"Recherche {search['representation']} / {search['closure']} : {search['status']} ; {len(search['candidates'])} candidats ; recensement incomplet.")
        for row in search["candidates"]:
            lines.append(f"  P={row['pressure_pa']:.9g} Pa ; f={row['frequency_hz']:.9g} Hz ; {row['status']} ; traversée={row['crossing']}.")
    lines.extend(["", "Un candidat marginal sur l'axe réel n'est pas une traversée ni un seuil universel.",
                  "La stabilité discrète |z|<1 diffère de Re(s)<0 en continu.",
                  "Une traversée suivie du FIR concerne une paire locale, pas toutes ses racines.",
                  "Contact, fermeture du débit et Bernoulli à ΔP=0 sont des frontières distinctes.",
                  "Aucune stabilité couplée n'est déduite du bilan mécanique à pressions fixées.",
                  "Aucun cycle périodique, registre joué, score de joueur ou validation A–E n'est établi.",
                  "3*f0 est un point de référence de gain FIR ; ce n'est pas un deuxième mode."])
    if payload.get("error"):
        lines.append(f"Interruption/limite : {payload['error']}")
    return "\n".join(lines) + "\n"


EXPORT_NAMES = ("onset_audit.json", "onset_equilibria.csv", "onset_response.csv", "onset_candidates.csv", "onset_summary_fr.txt")
PROGRESS_NAMES = ("onset_partial.json", "onset_trace.jsonl", ".onset_partial.tmp")
TEMPORARY_NAMES = tuple("." + name + ".tmp" for name in EXPORT_NAMES)


def check_output(path: Path, *, own_progress=False) -> None:
    names = EXPORT_NAMES + TEMPORARY_NAMES + ((".onset_partial.tmp",) if own_progress else PROGRESS_NAMES)
    # lexists includes dangling symlinks, which exists() silently misses.
    if os.path.lexists(path) and (path.is_symlink() or not path.is_dir() or
                                  any(os.path.lexists(path / name) for name in names)):
        raise ValueError("Output target exists: refusing to overwrite diagnostic artifacts")


class Progress:
    """Durable partial snapshot and stage trace, even if final export is interrupted."""
    def __init__(self, path):
        self.path = path
        self.started = time.monotonic()
        check_output(path)
        path.mkdir(parents=True, exist_ok=True)
        with (path / "onset_trace.jsonl").open("x", encoding="utf-8"):
            pass

    def save(self, payload, stage):
        snapshot = {**payload, "partial": True, "checkpoint_stage": stage}
        content = strict_json(snapshot)
        temporary = self.path / ".onset_partial.tmp"
        # Exclusive creation also closes the preflight/open race: an existing
        # file or symlink is neither followed nor truncated.
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(content)
        temporary.replace(self.path / "onset_partial.json")
        with (self.path / "onset_trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"stage": stage, "elapsed_seconds": time.monotonic()-self.started}, allow_nan=False) + "\n")


def _csv(fields, rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def export_bundle(payload: dict, output_dir: Path, *, own_progress=False) -> dict:
    check_output(output_dir, own_progress=own_progress)
    # Validate all JSON and render all documents before creating anything.
    contents = {"onset_audit.json": strict_json(payload), "onset_summary_fr.txt": french_summary(payload)}
    rows = []
    for closure, groups in payload.get("equilibria", {}).items():
        for index, group in enumerate(groups):
            metadata = {"closure": closure, "group_index": index, "group_status": group["status"],
                        "branch_count": group["branch_count"],
                        "absence_in_algebraic_domain": group.get("absence_in_algebraic_domain"),
                        "ambiguous_roots": json.dumps(group["ambiguous_roots"], ensure_ascii=False, allow_nan=False),
                        "rejected": json.dumps(group["rejected"], ensure_ascii=False, allow_nan=False)}
            # A group with no retained branch is still an enumeration result.
            # Leave branch fields empty, including pressure (absent in the core
            # group schema); never manufacture a representative equilibrium.
            if not group["branches"]:
                rows.append({**metadata, "record_type": "group"})
            rows.extend({**metadata, **r, "record_type": "branch", "scaled_residual": r["residuals"]["scaled_max"],
                         "non_regular_reasons": ";".join(r["non_regular_reasons"])} for r in group["branches"])
    contents["onset_equilibria.csv"] = _csv(["closure", "pressure_pa", "branch_id", "status", "downstream_pa", "delta_pa", "x_m", "h_m", "flow_m3_s", "regular_free", "boundary_status", "non_regular_reasons", "scaled_residual",
                                           "record_type", "group_index", "group_status", "branch_count", "algebraic_multiplicity_t", "absence_in_algebraic_domain", "ambiguous_roots", "rejected"], rows)
    contents["onset_response.csv"] = _csv(["frequency_hz", "documented", "real_pa_s_m3", "imag_pa_s_m3", "phase_rad", "magnitude_pa_s_m3", "target_real", "target_imag", "relative_complex_error", "phase_error_rad", "negative_real"], payload.get("fir", {}).get("samples", []))
    candidates = [{**r, "representation": s["representation"], "closure": s["closure"]} for s in payload.get("searches", []) for r in s["candidates"]]
    contents["onset_candidates.csv"] = _csv(["representation", "closure", "branch_id", "status", "pressure_pa", "frequency_hz", "scaled_residual", "crossing"], candidates)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        temporary = output_dir / ("." + name + ".tmp")
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
        # An exclusive hard link publishes complete bytes and never overwrites
        # an existing export, even if another process creates it after preflight.
        os.link(temporary, output_dir / name)
        temporary.unlink()
    return {name: str(output_dir / name) for name in EXPORT_NAMES}
