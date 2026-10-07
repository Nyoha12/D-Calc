"""Immutable R44 evidence; acquired checkpoints are not automatic resume state."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path

from .regime_reference import safe_path, read_json, read_json_source, write_json, atomic_bytes, file_sha256
from .constrained_design import versions, verify_sources
from .time_domain_reference import sources as native_sources
from ..nonlinear.passive_resonator import digest
from ..pipeline.fixed_design import _software_source
from ..optimization.design_contract import read_request

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'dcalc.paired_onset.result.v1'
COMPLETION = 'dcalc.paired_onset.completion.v1'
MAX_BYTES = 100*1024**2
NEW_SOURCES = ('didgeridoo_optimizer/nonlinear/paired_onset.py',
    'didgeridoo_optimizer/pipeline/paired_onset.py', 'didgeridoo_optimizer/reporting/paired_onset.py',
    'tools/paired_onset_reference.py')


def read_plan_source(path):
    p = safe_path(path)
    value, source = read_request(p)  # strict YAML/JSON, hash of the same interpreted bytes
    if file_sha256(p, maximum=2*1024**2) != source['sha256']:
        raise ValueError('Plan changed during read')
    return value, dict(path=str(p), sha256=source['sha256'])


def sources():
    result = native_sources()
    for name in NEW_SOURCES:
        result[name] = file_sha256(ROOT/name)
    return result


def provenance():
    return dict(software=_software_source(), versions=versions(), loaded_sources_sha256=sources())


def checkpoint(output, sequence, unit, acquired, counters, context_sha256, *, budget_bytes):
    payload = dict(schema='dcalc.paired_onset.checkpoint.v1', sequence=sequence, unit=unit,
        acquired=acquired, counters=counters, context_sha256=context_sha256,
        scope='Acquired results and counters only; not complete solver state or automatic resume')
    write_json(Path(output)/f'checkpoint-{unit}-{sequence:04d}.json',
               dict(payload=payload, sha256=digest(payload)), budget_bytes=budget_bytes)


def table(path, rows, fields, limit):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader(); writer.writerows(rows)
    atomic_bytes(path, stream.getvalue().encode(), budget_bytes=limit)


def export(output, result, *, budget_bytes):
    out = Path(output); plan = result['plan']['request']
    cases = [dict(c, model_sha256=result['plan']['cases'][i]['model_sha256'],
                  states=result['plan']['cases'][i]['states']) for i, c in enumerate(plan['cases'])]
    table(out/'cases.csv', cases, ['id', 'config', 'design', 'model_in', 'model_sha256', 'states'], budget_bytes)
    scenarios = []
    for s in plan['scenarios']:
        scenarios.append({'id':s['id'], **plan['reference_lips'], **s['changes']})
    table(out/'scenarios.csv', scenarios, ['id', *plan['reference_lips']], budget_bytes)
    grid=[]; candidates=[]; sides=[]
    for unit in result['units']:
        value=read_json(out/unit['file']); labels=dict(case=unit['case'], scenario=unit['scenario'])
        grid.extend(dict(labels, **row) for row in value['grid'])
        for c in value['candidates']:
            row=dict(labels, candidate=c['id'], status=c['status'], reason=c.get('reason'),
                     bracket_low_pa=c['bracket_pa'][0], bracket_high_pa=c['bracket_pa'][1],
                     uncertainty_pressure_pa=c.get('uncertainty_pressure_pa'))
            row.update({k:v for k,v in (c.get('root') or {}).items() if k!='status'})
            candidates.append(row)
            sides.extend(dict(labels, candidate=c['id'], side=i, **side) for i,side in enumerate(c['sides']))
    fields=['case','scenario','pressure_pa','status','reason','delta_pa','opening_m','flow_m3_s',
        'static_residual_pa','root_real_s','root_imag_s','realization_frequency_hz','discrete_frequency_hz',
        'discrete_growth_per_s','unstable_roots','other_root_max_real_s','characteristic_relative_residual']
    table(out/'grid.csv',grid,fields,budget_bytes)
    table(out/'candidates.csv',candidates,['candidate','bracket_low_pa','bracket_high_pa',
          'uncertainty_pressure_pa',*fields],budget_bytes)
    table(out/'sides.csv',sides,['candidate','side',*fields],budget_bytes)
    table(out/'differentials.csv',result['differentials'],['case_a','case_b','scenario','window','status','reason',
          'candidate_a','candidate_b','pressure_difference_pa','frequency_difference_hz','pressure_ratio',
          'frequency_ratio','pressure_difference_bounds_pa','frequency_difference_bounds_hz'],budget_bytes)
    table(out/'criteria.csv',result['criteria'],['id','role','case','pair','scenario','window','priority','observable','status','value_si','unit_si',
          'target_si','tolerance_si','tolerance_dimension','uncertainty_bounds_si','reason'],budget_bytes)
    write_json(out/'result.json',result,budget_bytes=budget_bytes)
    lines=['Comparaison appariée de stabilité locale — PAIRED-ONSET-01',
        f"État du calcul : {result['status']} ; exigences hard conformes : {result['hard_conforming']}",
        f"Couverture de la demande : {result['coverage']}",
        'Les paramètres labiaux explicites sont à calibrer ; aucune identification physiologique.',
        'Pcontact est une borne conservatrice du protocole libre, pas une limite physique de jeu.',
        'La coordonnée de réalisation s précompensée n’est pas une fréquence physique TMM.',
        'Aucune fréquence jouée, accessibilité toot, régime ou transition n’est couverte.',
        'Absence dans la grille ≠ impossibilité globale ou seuil minimal universel.',
        'Un candidat marginal TMM ne constitue pas une traversée de stabilité TMM.',
        'Checkpoint : résultats acquis et compteurs, aucun état complet de reprise.',
        'Le manifeste de fin est requis : un fichier résultat seul reste candidat.']
    for row in result['differentials']:
        lines.append(f"{row['scenario']} / {row['window']} : {row['case_b']} − {row['case_a']} : "
                     f"{row.get('pressure_difference_pa')} Pa ; {row.get('frequency_difference_hz')} Hz ; {row['status']}")
    atomic_bytes(out/'summary.txt', ('\n'.join(lines)+'\n').encode(),budget_bytes=budget_bytes)
    manifest={p.name:file_sha256(p) for p in out.iterdir() if p.is_file() and not p.name.startswith('.pending')}
    write_json(out/'manifest.json',manifest,budget_bytes=budget_bytes)


def completion_record(output):
    out=Path(output)
    return dict(schema=COMPLETION, execution_sha256=file_sha256(out/'execution.json'),
                closure_sha256=file_sha256(out/'execution.closed.json'))


def prepare_completion(output, execution, *, budget_bytes):
    out=Path(output)
    write_json(out/'execution.json',execution,budget_bytes=budget_bytes)
    write_json(out/'execution.closed.json',dict(execution_sha256=file_sha256(out/'execution.json')),
               budget_bytes=budget_bytes)
    write_json(out/'.pending-execution-completion.json',completion_record(out),budget_bytes=budget_bytes)


def publish_completion(output):
    # Last fallible operation, AFTER child reaping and signal restoration.
    out=Path(output)
    os.link(out/'.pending-execution-completion.json',out/'execution.completed.json',follow_symlinks=False)


def read_result(output, expected_context=None):
    out=safe_path(output)
    try:
        completed=read_json(out/'execution.completed.json')
        if completed != completion_record(out): raise ValueError('Invalid terminal marker')
        execution=read_json(out/'execution.json')
        if not execution['ok']: return dict(ok=False,status=execution['status'],execution=execution)
        children=execution['children']
        if not children or any(c['exit_code'] != 0 or c['reaped'] is not True for c in children):
            raise ValueError('Successful reaped actual children required')
        manifest=read_json(out/'manifest.json')
        if execution['manifest_sha256'] != file_sha256(out/'manifest.json'): raise ValueError('Manifest mismatch')
        if not {'result.json','plan.json','summary.txt','criteria.csv','differentials.csv'} <= set(manifest):
            raise ValueError('Incomplete bundle')
        for name, sha in manifest.items():
            if Path(name).name!=name or file_sha256(safe_path(out/name))!=sha: raise ValueError('Artifact mismatch')
        result=read_json(out/'result.json'); plan=read_json(out/'plan.json')
        if result['schema']!=SCHEMA or result['plan']!=plan: raise ValueError('Result/plan mismatch')
        if plan['context_sha256']!=digest({k:v for k,v in plan.items() if k!='context_sha256'}):
            raise ValueError('Context identity mismatch')
        if expected_context is not None and plan['context_sha256']!=expected_context:
            raise ValueError('Unexpected calculation context')
        if plan['producer']['versions']!=versions(): raise ValueError('Versions mismatch')
        if not plan['producer']['loaded_sources_sha256']: raise ValueError('Empty source manifest')
        verify_sources(plan['producer']['loaded_sources_sha256'])
        expected={(c['id'],s['id']) for c in plan['request']['cases'] for s in plan['request']['scenarios']}
        actual=[(u['case'],u['scenario']) for u in result['units']]
        child_ids=[(c['case'],c['scenario']) for c in children]
        if set(actual)!=expected or len(actual)!=len(expected) or set(child_ids)!=expected or len(child_ids)!=len(expected):
            raise ValueError('Missing or duplicate unit/child identity')
        for unit in result['units']:
            if Path(unit['file']).name!=unit['file'] or unit['file'] not in manifest:
                raise ValueError('Unit file outside manifest')
            row=read_json(safe_path(out/unit['file']))
            if row['status']!='complete': raise ValueError('Incomplete unit in successful bundle')
            if row['context_sha256']!=plan['context_sha256'] or row['case']!=unit['case'] or row['scenario']!=unit['scenario']:
                raise ValueError('Unit identity mismatch')
        return dict(ok=True,status=result['status'],execution=execution,result=result)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return dict(ok=False,status='unconfirmed',reason=str(exc))
