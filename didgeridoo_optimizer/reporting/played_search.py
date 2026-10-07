"""Immutable orchestration evidence; native product readers retain authority."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import uuid

from .regime_reference import safe_path, read_json, file_sha256
from .constrained_design import versions, verify_sources
from . import register_target as register_report
from ..nonlinear.passive_resonator import digest
from ..pipeline.fixed_design import _software_source

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'dcalc.played_search.result.v1'
NEW_SOURCES = ('didgeridoo_optimizer/optimization/played_search.py',
               'didgeridoo_optimizer/pipeline/played_search.py',
               'didgeridoo_optimizer/reporting/played_search.py', 'tools/played_search.py')
FIELDS = ['candidate', 'kind', 'configuration', 'scenario', 'window', 'id', 'observable', 'role',
          'status', 'value_si', 'unit_si', 'target_si', 'residual_si', 'error_cents', 'margin',
          'margin_unit', 'normalization_scale', 'violation_normalized', 'reason']


def sources():
    manifest = register_report.sources()
    for name in NEW_SOURCES:
        manifest[name] = file_sha256(ROOT/name)
    return manifest


def provenance():
    software = _software_source()
    if software['sha'] is None:
        raise ValueError('Tracked canonical source provenance required: '+software['origin'])
    # The native package guard includes loaded modules; explicitly guard this CLI too.
    import subprocess
    subprocess.run(['git', 'ls-files', '--error-unmatch', '--', *NEW_SOURCES], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
    return dict(software=software, versions=versions(), loaded_sources_sha256=sources())


def size(root):
    total = 0; inodes = set()
    for p in Path(root).rglob('*'):
        # Native fit progress atomically renames its temporary file. A live
        # storage scan must tolerate its disappearance, without following links.
        safe_path(p, exists=False)
        if p.is_file():
            try: st = p.stat()
            except FileNotFoundError: continue
            inode = (st.st_dev, st.st_ino)
            if inode not in inodes: total += st.st_size
            inodes.add(inode)
    return total


def write_bytes(path, content, *, root=None, limit=800*1024**2):
    path = safe_path(path, exists=False)
    if path.exists(): raise FileExistsError('Existing publication refused')
    if root is not None and size(root)+len(content) > limit:
        raise ValueError('Orchestration output budget')
    pending = path.with_name('.pending-'+uuid.uuid4().hex)
    with pending.open('xb') as stream:
        stream.write(content); stream.flush(); os.fsync(stream.fileno())
    os.link(pending, path, follow_symlinks=False)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def write_json(path, value, **kw):
    content = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)+'\n').encode()
    if len(content) > 4*1024**2: raise ValueError('JSON record exceeds 4 MiB')
    write_bytes(path, content, **kw)


def table(path, rows, fields, **kw):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fields, extrasaction='ignore')
    writer.writeheader(); writer.writerows(rows)
    write_bytes(path, stream.getvalue().encode(), **kw)


def summary(row):
    return None if row is None else {k:v for k,v in row.items() if k not in ('criteria', 'static')}


def checkpoint(out, index, state, **kw):
    payload = dict(schema='dcalc.played_search.checkpoint.v1', checkpoint='witness',
                   solver_state_complete=False, global_resume='unsupported', **state)
    record = dict(payload=payload, sha256=digest(payload))
    write_json(Path(out)/f'checkpoint-{index:05d}.json', record, **kw)


def manifest(out):
    return {p.relative_to(out).as_posix(): file_sha256(p, maximum=800*1024**2)
            for p in sorted(Path(out).rglob('*')) if p.is_file() and
            not any(part.startswith('.pending') for part in p.relative_to(out).parts)}


def export(out, result, **kw):
    out = Path(out)
    history = result['search']['history']
    table(out/'criteria.csv', [dict(r, candidate=c['candidate']) for c in history for r in c['criteria']], FIELDS, **kw)
    variables = result['plan']['variables']
    table(out/'variables.csv', [dict(candidate=c['candidate'], alternative=c['alternative'],
          variable=v['id'], value_si=x, low_si=v['low'], high_si=v['high'])
          for c in history for v,x in zip(variables,c['variables_si'])],
          ['candidate','alternative','variable','value_si','low_si','high_si'], **kw)
    table(out/'candidates.csv', [summary(c) for c in history],
          ['candidate','alternative','stage','status','conforming','units_complete','max_violation_normalized','file'], **kw)
    saved = dict(result, search=dict(result['search'], history=[summary(c) for c in history],
                   best=summary(result['search']['best']), witness=summary(result['search']['witness'])))
    write_json(out/'result.json', saved, **kw)
    witness = saved['search']['witness']
    lines = ['PLAYED-SEARCH-01 — recherche géométrique sous scénarios prescrits',
             f"Commande : {saved['status']} ; témoin conforme : {witness['candidate'] if witness else 'aucun'}.",
             f"Arrêt : {saved['search']['termination']} ; candidats : {len(history)}.",
             'Chaque obligation et chaque fenêtre requise sont indépendantes ; aucune moyenne compensatrice.',
             'Les critères statiques et joués sont distincts. Aucun réglage joueur optimisé.',
             'Le fit et le calcul courant ont des identités distinctes ; modèles neufs par contexte exact.',
             'Pas d’optimum global, de preuve d’impossibilité, de garantie continue ou physiologique.',
             'Paramètres labiaux à calibrer ; aucune validation matérielle A–E.',
             'Checkpoint = témoin et compteurs ; reprise globale unsupported. Les reprises natives gardent leur portée.',
             'Voir criteria.csv pour valeurs SI, résidus signés, cents et marges, et les bundles natifs liés.']
    lines.extend('Non couvert : '+s for s in saved['plan']['unsupported'])
    write_bytes(out/'summary.txt', ('\n'.join(lines)+'\n').encode(), **kw)
    return saved


def prepare_completion(out, execution, **kw):
    out = Path(out)
    write_json(out/'manifest.json', manifest(out), **kw)
    write_json(out/'execution.json', dict(execution, manifest_sha256=file_sha256(out/'manifest.json')), **kw)
    write_json(out/'execution.closed.json', dict(execution_sha256=file_sha256(out/'execution.json')), **kw)
    marker = dict(schema='dcalc.played_search.completion.v1',
                  execution_sha256=file_sha256(out/'execution.json'), closure_sha256=file_sha256(out/'execution.closed.json'))
    write_json(out/'.pending-completion.json', marker, **kw)


def publish_completion(out):
    # Last fallible operation after every owned child, descriptor and handler is closed.
    out = Path(out)
    os.link(out/'.pending-completion.json', out/'execution.completed.json', follow_symlinks=False)


def local(out, name):
    p = Path(name)
    if p.is_absolute() or '..' in p.parts: raise ValueError('Bundle-relative locator required')
    return safe_path(Path(out)/p)


def read_checkpoint(out):
    paths = sorted(safe_path(out).glob('checkpoint-*.json'))
    if not paths: return None
    record = read_json(paths[-1]); payload = record['payload']
    if digest(payload) != record['sha256'] or payload['solver_state_complete'] is not False:
        raise ValueError('Invalid witness checkpoint')
    verify_sources(payload['producer']['loaded_sources_sha256'])
    from ..pipeline.played_search import check_plan
    check_plan(payload['plan'])
    for unit in payload.get('units', []):
        if unit.get('file') and file_sha256(local(out, unit['file'])) != unit['sha256']:
            raise ValueError('Checkpoint unit changed')
    return payload


def read_result(out, expected_context=None):
    """Synchronous reader: native trajectory reader may allocate/eigensolve.

    A caller must bound this read like any native scientific read. No fits or
    trajectory steps are performed. CLI orchestration invokes it in a child.
    """
    try:
        out = safe_path(out)
        execution = read_json(out/'execution.json')
        marker = dict(schema='dcalc.played_search.completion.v1',
            execution_sha256=file_sha256(out/'execution.json'), closure_sha256=file_sha256(out/'execution.closed.json'))
        if read_json(out/'execution.completed.json') != marker:
            raise ValueError('Terminal marker mismatch')
        if read_json(out/'execution.closed.json') != dict(execution_sha256=marker['execution_sha256']):
            raise ValueError('Closure mismatch')
        hashes = read_json(out/'manifest.json')
        if file_sha256(out/'manifest.json') != execution['manifest_sha256']:
            raise ValueError('Manifest mismatch')
        if not {'plan.json','result.json','criteria.csv','candidates.csv','variables.csv','summary.txt'} <= hashes.keys():
            raise ValueError('Incomplete bundle')
        for name, sha in hashes.items():
            if file_sha256(local(out, name), maximum=800*1024**2) != sha:
                raise ValueError('Artifact mismatch: '+name)
        result = read_json(out/'result.json'); plan = read_json(out/'plan.json')
        if result['schema'] != SCHEMA or result['plan'] != plan:
            raise ValueError('Result plan mismatch')
        if expected_context is not None and plan['context_sha256'] != expected_context:
            raise ValueError('Unexpected context')
        from ..pipeline.played_search import check_plan, verify_candidate
        check_plan(plan)
        if any(c['reaped'] is not True for c in execution['children']):
            raise ValueError('Unreaped child')
        if result['conforming'] != (result['search']['witness'] is not None):
            raise ValueError('Witness/conformity mismatch')
        witness = result['search']['witness']
        if witness is not None and (witness not in result['search']['history'] or not witness['conforming'] or not witness['units_complete']):
            raise ValueError('Witness must be a complete conforming acquired candidate')
        for summary_row in result['search']['history']:
            row = read_json(local(out, summary_row['file']))
            if summary(row) != summary_row: raise ValueError('Candidate summary mismatch')
            verify_candidate(plan, out, row, native_read=True)
        return dict(ok=execution['ok'], status=result['status'], conforming=result['conforming'], result=result,
                    execution=execution)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return dict(ok=False, status='unconfirmed', conforming=False, reason=str(exc))
