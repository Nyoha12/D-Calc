"""Checkpoint-aware DESIGN-PITCH reports and native offline comparisons."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

from . import forced_response_comparison as comparison

LIMITS = [
    'Diagnostic numérique conditionnel, cible étudiée 50..90 Hz ; aucun optimum universel.',
    'Accord longitudinal uniforme : diamètres, profils, matériaux et annotations conservés.',
    'Premier maximum de |Zin| dans 10..700 Hz ; ni plus grand pic, ni fréquence de jeu, ni loi 1/L exacte.',
    'Vérification à h/2 sans réaccord ; extraction des trois premiers modes et Q à demi-puissance.',
    'Un Q absent garde sa raison ; null ne signifie pas zéro.',
    'ZK : parois rigides, lisses et étanches ; effets des matériaux omis, aucun matériau promu.',
    'Silva : charge cylindrique transposée aux sorties variables ; montage extérieur non validé.',
    'Pload est la puissance acoustique de charge sous la source imposée ; aucun rendement du joueur.',
    'Aucun score, classement, somme broadband, calibration ou validation expérimentale A–E.',
]


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                     sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, payload):
    content = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)+'\n'
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(content)


def artifact_names(count):
    names = ['plan.json', 'summary.json', 'summary.csv', 'report.md']
    for index in range(1, count+1):
        names += [f'{stage}_{index:03d}.json' for stage in
                  ('job', 'profile', 'original_design', 'original', 'tuning', 'tuned_design', 'validation')]
        names += [f'trace_{index:03d}.jsonl']
        for stage in ('original', 'tuned'):
            names += [f'response_{stage}_{index:03d}/forced_response.{suffix}' for suffix in ('json', 'csv', 'txt')]
        names += [f'comparison_before_after_{index:03d}/']
        if index > 1:
            names += [f'comparison_001_{index:03d}/']
    return names


def provenance(root):
    paths = {Path(__file__).resolve(), root/'tools/design_pitch_compare.py'}
    for name, module in tuple(sys.modules.items()):
        if name.startswith(('didgeridoo_optimizer.', 'tools.')):
            path = getattr(module, '__file__', None)
            if path and Path(path).suffix == '.py' and Path(path).resolve().is_relative_to(root):
                paths.add(Path(path).resolve())
    def git(*args):
        try:
            return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True,
                                  check=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return dict(head_sha=git('rev-parse', 'HEAD'), origin_main_sha=git('rev-parse', 'origin/main'),
        worktree=str(root), status=git('status', '--short'), executable=sys.executable, python=sys.version,
        source_sha256={p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})


def _read(path, errors):
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('Expected JSON object')
        return payload
    except (ValueError, OSError) as exc:
        # An interrupted exclusive write may leave a truncated artifact. Keep it
        # as evidence and report the missing result rather than losing all rows.
        errors.append(dict(path=str(path), reason=f'{type(exc).__name__}: {exc}'))
        return None


def _metrics(design, level, response, target):
    modes = (level or {}).get('modes', [])
    resolved = {m['mode_ordinal']: m for m in modes if m['status'] == 'resolved'}
    f1, f2 = resolved.get(1, {}), resolved.get(2, {})
    first = f1.get('frequency_max_abs_hz')
    second = f2.get('frequency_max_abs_hz')
    p = dict(value=None, status='unavailable', reason='Forced-response bundle not acquired')
    if response:
        model = response['cases'][0]['models'][0]
        i = model['frequency_hz'].index(target)
        p = {key: values[i] for key, values in model['powers']['Pload'].items()}
    return dict(length_cm=sum(s['length_cm'] for s in design['segments']) if design else None,
                f1_hz=first, f2_over_f1=second/first if first and second else None,
                q1=f1.get('q_half_power'), q1_reason=f1.get('q_unavailable_reason', 'First mode unavailable'),
                pload_target_w=p['value'], pload_target_status=p['status'], pload_target_reason=p['reason'])


def _compare(output, baseline, candidate, name):
    try:
        a, b = comparison.load_export(baseline), comparison.load_export(candidate)
        payload = comparison.compare_exports(a, b, comparator=comparison.source_identity(__file__))
        content = comparison.render_bundle(payload)
        directory = comparison.preflight_output(output/name)
        exports = comparison.write_bundle(content, directory)
        return dict(status='complete', exports=exports, changed_factors=payload['changed_factors'],
                    coverage=payload['coverage'])
    except Exception as exc:
        return dict(status='unavailable', reason=f'{type(exc).__name__}: {exc}')


def summarize(output, plan, jobs, identity):
    rows, comparisons, artifact_errors = [], [], []
    for mapping, job in zip(plan['mapping'], jobs, strict=True):
        index, suffix = mapping['index'], f"{mapping['index']:03d}"
        def read(name):
            return _read(output/f'{name}_{suffix}.json', artifact_errors)
        original, tuning, validation, profile = (read(name) for name in ('original', 'tuning', 'validation', 'profile'))
        before_path = output/f'response_original_{suffix}/forced_response.json'
        after_path = output/f'response_tuned_{suffix}/forced_response.json'
        before_response, after_response = _read(before_path, artifact_errors), _read(after_path, artifact_errors)
        physical_before, physical_after = read('original_design'), read('tuned_design')
        fine = (validation or {}).get('levels', [{}])[-1]
        reasons = list((profile or {}).get('reasons', []))
        verified = bool(job.get('ok') is True and job.get('verified') is True and
                        profile and profile.get('verified') is True and validation and
                        validation.get('verified') is True and len(validation.get('levels', [])) == 2 and
                        original and original.get('verified') is True and tuning and physical_after and
                        before_response and after_response)
        if verified:
            from tools.equal_pitch_study import TOLERANCE_HZ
            for level in validation['levels']:
                verified &= (abs(level['error_hz']) <= TOLERANCE_HZ and
                             level['physical_sha256'] == fingerprint(physical_after))
            for payload in (before_response, after_response):
                comparison.validate_export(payload)
                verified &= payload['cases'][0]['models'][0]['numerically_complete'] is True
        if job.get('error'):
            reasons.append(job['error'])
        if not verified and not reasons:
            reasons.append('Required verified artifacts missing or inconsistent; child exit 0 is insufficient')
        row = dict(**mapping, status='verified' if verified else 'partial', reasons=reasons,
            error_hz=(validation or {}).get('error_hz'), factor=(tuning or {}).get('factor'),
            original=_metrics(physical_before, original, before_response, plan['options']['target_hz']),
            after=_metrics(physical_after, fine, after_response, plan['options']['target_hz']),
            inputs=plan['shared_input_files'])
        if before_response and after_response:
            compared = _compare(output, before_path, after_path, f'comparison_before_after_{suffix}')
            comparisons.append(dict(baseline=f'original_{suffix}', candidate=f'tuned_{suffix}', **compared))
        rows.append(row)
    # Stable reference: first supplied design. Missing reference is explicit.
    reference = output/'response_tuned_001/forced_response.json'
    for index in range(2, len(rows)+1):
        path = output/f'response_tuned_{index:03d}/forced_response.json'
        result = (_compare(output, reference, path, f'comparison_001_{index:03d}')
                  if reference.exists() and path.exists() else
                  dict(status='unavailable', reason='Reference or candidate tuned bundle not acquired'))
        comparisons.append(dict(baseline='tuned_001', candidate=f'tuned_{index:03d}', **result))
    complete = not artifact_errors and all(row['status'] == 'verified' for row in rows) and all(c['status'] == 'complete' for c in comparisons)
    return dict(schema='dcalc.design_pitch.v1', status='complete' if complete else 'partial',
                plan=plan, provenance=identity, limitations=LIMITS, profiles=rows, comparisons=comparisons,
                jobs=jobs, source=plan['source'], effective=plan['effective'], artifact_errors=artifact_errors)


def write_summary(output, payload):
    write_json(output/'summary.json', payload)
    rows = []
    for profile in payload['profiles']:
        row = {key: profile[key] for key in ('index', 'design_id', 'input_path', 'status', 'factor', 'error_hz')}
        row['reasons'] = '; '.join(profile['reasons'])
        for side in ('original', 'after'):
            row.update({side+'_'+key: value for key, value in profile[side].items()})
        rows.append(row)
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    with (output/'summary.csv').open('x', encoding='utf-8', newline='') as handle:
        handle.write(stream.getvalue())
    def cell(value):
        return str(value if value is not None else 'indisponible').replace('|', '&#124;').replace('\n', '<br>').replace('\r', '')
    def pair(row, key):
        return cell(row['original'][key])+' → '+cell(row['after'][key])
    lines = ['# Accord et comparaison de profils utilisateur', '',
             f"Statut : **{payload['status']}**. Cible : {payload['plan']['options']['target_hz']:g} Hz.", '',
             'Les colonnes présentent **original → accordé** au maillage fin ; le même facteur est vérifié à h et h/2.', '',
             '| N° / ID | Statut | Longueur (cm) | f1 (Hz) | f2/f1 | Q1 | Pload cible (W) | Erreur finale (Hz) |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    for row in payload['profiles']:
        lines.append('| '+' | '.join([f"{row['index']:03d} / {cell(row['design_id'])}", row['status'],
            *(pair(row, k) for k in ('length_cm', 'f1_hz', 'f2_over_f1', 'q1', 'pload_target_w')),
            cell(row['error_hz'])])+' |')
        for reason in row['reasons']:
            lines.append(f"\nProfil {row['index']:03d} : {cell(reason)}.\n")
        for side in ('original', 'after'):
            if row[side]['q1'] is None:
                lines.append(f"\nQ1 {row['index']:03d} ({side}) : {cell(row[side]['q1_reason'])}.\n")
    lines += ['', 'Source commune (amplitude crête ; Rs absolu identique entre profils) :',
              '```json', json.dumps(payload['source'], ensure_ascii=False, indent=2), '```',
              '', 'Air et modèles effectifs :', '```json',
              json.dumps(payload['effective'], ensure_ascii=False, indent=2), '```', '',
              'Les bundles `response_original_NNN` et `response_tuned_NNN` partagent la grille cible ×1..7 et ±1/±5 Hz.',
              'Les dossiers `comparison_*` proviennent du comparateur existant ; aucune interpolation ni nouveau score.',
              'Les IDs sont des étiquettes ; seule la table `mapping` associe les fichiers numériques aux entrées.', '']
    for c in payload['comparisons']:
        lines.append(f"Comparaison {c['baseline']} → {c['candidate']} : {c['status']}. {c.get('reason', '')}")
    lines += ['', *LIMITS]
    with (output/'report.md').open('x', encoding='utf-8') as handle:
        handle.write('\n'.join(lines)+'\n')
