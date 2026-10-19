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


def batch_artifact_pins(out, result, child):
    """Check local native closure/integrity without a semantic trajectory read.

    Only the terminal native reader determines the normative musical criteria.
    Earlier publications remain intact traces, including their observation files.
    """
    native = register_report
    out = native.safe_path(out)
    names = ('manifest.json', 'execution.json', 'execution.closed.json', 'execution.completed.json')
    pins = {str(out/name): native.file_sha256(out/name) for name in names}
    execution = native.read_json(out/'execution.json')
    marker = native.read_json(out/'execution.completed.json')
    execution_sha = pins[str(out/'execution.json')]
    if marker != dict(schema='dcalc.register_target.completion.v1', execution_sha256=execution_sha,
                      closure_sha256=pins[str(out/'execution.closed.json')]):
        raise ValueError('Batch native terminal marker differs')
    if native.read_json(out/'execution.closed.json') != dict(execution_sha256=execution_sha):
        raise ValueError('Batch native closure differs')
    if digest(execution['children']) != digest([child]):
        raise ValueError('Batch native child receipt differs')
    if type(execution['ok']) is not bool or (execution['ok'] and
            (child['exit_code'] != 0 or child['launched'] is not True or child['timeout_or_error'])):
        raise ValueError('Batch native execution success differs')
    manifest = native.read_json(out/'manifest.json')
    if pins[str(out/'manifest.json')] != execution['manifest_sha256']:
        raise ValueError('Batch native manifest differs')
    if not {'plan.json', 'result.json', 'criteria.csv', 'controls.csv', 'events.csv', 'summary.txt'} <= set(manifest):
        raise ValueError('Batch native artifact missing')
    for name, sha in manifest.items():
        if Path(name).name != name or native.file_sha256(out/name) != sha:
            raise ValueError('Batch native artifact integrity')
        pins[str(out/name)] = sha
    if result['schema'] != native.SCHEMA:
        raise ValueError('Batch native result schema')
    request = result['plan']['request']; analysis = result['analysis']
    if set(analysis['windows']) != {w['id'] for w in request['windows']}:
        raise ValueError('Batch native observation references')
    for wid, name in analysis['windows'].items():
        if name != 'window-'+wid+'.json' or name not in manifest:
            raise ValueError('Batch uncommitted observation')
        native.read_json(out/name)
    phase = 'phase.json' if request['observation']['phase'] is not None else None
    if analysis['phase_file'] != phase or (phase is not None and phase not in manifest):
        raise ValueError('Batch uncommitted PHASE')
    return pins


_IMPORTED_SEARCH_SOURCES = {name: file_sha256(ROOT/name) for name in NEW_SOURCES}


def sources():
    verify_sources(_IMPORTED_SEARCH_SOURCES)
    manifest = register_report.sources()
    for name in NEW_SOURCES:
        manifest[name] = file_sha256(ROOT/name)
    return manifest


def provenance(*, scalar=False):
    software = _software_source()
    if software['sha'] is None and not scalar:
        raise ValueError('Tracked canonical source provenance required: '+software['origin'])
    # The native package guard includes loaded modules; explicitly guard this CLI too.
    import subprocess
    subprocess.run(['git', 'ls-files', '--error-unmatch', '--', *NEW_SOURCES], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
    manifest=sources()
    if scalar:
        from .geometry_state import sources as geometry_sources
        manifest.update(geometry_sources())
    return dict(software=software, versions=versions(), loaded_sources_sha256=manifest)


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


def table_bytes(rows, fields):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fields, extrasaction='ignore')
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode()


def table(path, rows, fields, **kw):
    write_bytes(path, table_bytes(rows, fields), **kw)


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


def start_records(plan):
    records = [plan['starts']['initial']]
    return records+[s for s in plan['starts']['selected'] if s['origin']['id'] != 'initial']


def export_starts(out, plan, **kw):
    folder = Path(out)/'starts'; folder.mkdir()
    for start in start_records(plan):
        origin = start['origin']; source = safe_path(origin['source']['path'])
        target = folder/(origin['id']+'.supplied'+source.suffix)
        if file_sha256(source, maximum=2*1024**2) != origin['source']['sha256']:
            raise ValueError('Start source changed before export')
        write_bytes(target, source.read_bytes(), **kw)
        if file_sha256(target, maximum=2*1024**2) != origin['source']['sha256']:
            raise ValueError('Start source changed during export')
        write_json(folder/(origin['id']+'.native.json'), start['native_design'], **kw)


def verify_starts(out, plan):
    for start in start_records(plan):
        origin = start['origin']; source = Path(origin['source']['path'])
        if file_sha256(local(out, 'starts/'+origin['id']+'.supplied'+source.suffix), maximum=2*1024**2) != origin['source']['sha256']:
            raise ValueError('Supplied start bytes differ')
        if digest(read_json(local(out, 'starts/'+origin['id']+'.native.json'))) != digest(start['native_design']):
            raise ValueError('Generated start representation differs')


def start_columns(row):
    origin = row.get('start_origin')
    return {} if origin is None else dict(start_id=origin['id'], start_source_sha256=origin['source']['sha256'],
        start_variables_si=json.dumps(origin['variables_si']), start_generated_sha256=origin['generated_sha256'],
        start_mode=origin['mode'])


def export_tables(result):
    history = result['search']['history']
    extra = ['start_id','start_source_sha256','start_variables_si','start_generated_sha256','start_mode'] if 'starts' in result['plan'] else []
    variables = result['plan']['variables']
    return [
        ('criteria.csv', [dict(r, candidate=c['candidate'], **start_columns(c)) for c in history for r in c['criteria']], FIELDS+extra),
        ('variables.csv', [dict(candidate=c['candidate'], alternative=c['alternative'],
          variable=v['id'], value_si=x, low_si=v['low'], high_si=v['high'], **start_columns(c))
          for c in history for v,x in zip(variables,c['variables_si'])],
          ['candidate','alternative','variable','value_si','low_si','high_si']+extra),
        ('candidates.csv', [dict(summary(c), **start_columns(c)) for c in history],
          ['candidate','alternative','stage','status','conforming','units_complete','max_violation_normalized','file']+extra)]


def export(out, result, **kw):
    out = Path(out)
    history = result['search']['history']
    for name, rows, fields in export_tables(result):
        table(out/name, rows, fields, **kw)
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
             ('Session explicite terminée ; frontières de candidats conservées, budgets cumulés.'
              if saved.get('global_resume') == 'explicit_session' and saved['status'] == 'complete' else
              'Checkpoint = témoin et compteurs ; reprise globale unsupported. Les reprises natives gardent leur portée.'),
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
    from ..pipeline.played_search import (check_plan, verify_search, verify_candidate, batched,
                                         instantiate, verify_batch_unit, verify_batch_counters)
    check_plan(payload['plan'])
    for unit in payload.get('units', []):
        if unit.get('file') and file_sha256(local(out, unit['file'])) != unit['sha256']:
            raise ValueError('Checkpoint unit changed')
    if 'starts' in payload['plan'] or batched(payload['plan']):
        if 'starts' in payload['plan']: verify_starts(out, payload['plan'])
        verify_search(payload['plan'], payload, prefix=True, current=payload.get('current'))
        rows = []
        for row in payload['history']:
            full = read_json(local(out, row['file']))
            if digest(summary(full)) != digest(row): raise ValueError('Checkpoint candidate summary differs')
            verify_candidate(payload['plan'], out, full, native_read=False)
            rows.append(full)
        if batched(payload['plan']):
            current = payload.get('current')
            if current is not None:
                for scenario in payload['plan']['scenarios']:
                    unit = next((u for u in current['units'] if u['scenario']==scenario['id']),None)
                    if unit:
                        expected = instantiate(scenario['template'],payload['plan']['paths']['config'],
                            local(out,current['configurations'][scenario['configuration']]['design']),
                            local(out,unit['fit'])/'model.json')
                        verify_batch_unit(scenario,unit,out,expected,native_read=False)
                rows.append(current)
            verify_batch_counters(payload['plan'],rows,payload['counters'],payload['children'])
        check_plan(payload['plan'])
    return payload


def read_result(out, expected_context=None, *, _session_checked=False):
    """Synchronous reader: native trajectory reader may allocate/eigensolve.

    A caller must bound this read like any native scientific read. No fits or
    trajectory steps are performed. CLI orchestration invokes it in a child.
    """
    try:
        out = safe_path(out)
        if (out/'session.json').exists() and not _session_checked:
            value = read_session(out)
            if expected_context is not None and value.get('result', {}).get('plan', {}).get('context_sha256') != expected_context:
                raise ValueError('Unexpected context')
            return value
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
        if result['schema'] != SCHEMA or (digest(result['plan']) != digest(plan) if 'starts' in plan else result['plan'] != plan):
            raise ValueError('Result plan mismatch')
        if expected_context is not None and plan['context_sha256'] != expected_context:
            raise ValueError('Unexpected context')
        from ..pipeline.played_search import check_plan, verify_candidate, verify_search, verify_batch_counters
        check_plan(plan)
        if 'starts' in plan:
            verify_starts(out, plan)
            verify_search(plan, result['search'])
        if any(c['reaped'] is not True for c in execution['children']):
            raise ValueError('Unreaped child')
        if result['conforming'] != (result['search']['witness'] is not None):
            raise ValueError('Witness/conformity mismatch')
        witness = result['search']['witness']
        if witness is not None and (witness not in result['search']['history'] or not witness['conforming'] or not witness['units_complete']):
            raise ValueError('Witness must be a complete conforming acquired candidate')
        full_history = []
        for summary_row in result['search']['history']:
            row = read_json(local(out, summary_row['file']))
            if (digest(summary(row)) != digest(summary_row) if 'starts' in plan else summary(row) != summary_row):
                raise ValueError('Candidate summary mismatch')
            verify_candidate(plan, out, row, native_read=not _session_checked)
            full_history.append(row)
        verify_batch_counters(plan, full_history, result['counters'], execution['children'])
        if 'starts' in plan:
            complete_rows = dict(result, search=dict(result['search'], history=full_history))
            for name, rows, fields in export_tables(complete_rows):
                if local(out, name).read_bytes() != table_bytes(rows, fields):
                    raise ValueError('Seeded CSV correspondence changed: '+name)
        check_plan(plan)
        return dict(ok=execution['ok'], status=result['status'], conforming=result['conforming'], result=result,
                    execution=execution)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return dict(ok=False, status='unconfirmed', conforming=False, reason=str(exc))


# Session records are separate from historical witness checkpoints. All names
# are exclusive creations; interruption leaves evidence, never a rollback.
SESSION_SCHEMA = 'dcalc.played_search.session.v1'


def session_write(path, value, **kw):
    """One append-only record, including failed/incomplete writes in inventory."""
    content = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
    if len(content) > 4*1024**2: raise ValueError('Session record exceeds 4 MiB')
    path = safe_path(path, exists=False)
    if size(kw['root'])+len(content) > kw['limit']: raise ValueError('Session storage budget')
    with path.open('xb') as stream:
        stream.write(content); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def session_inventory(out):
    """Include native pending aliases too; no unaccounted later acquisition."""
    import stat
    files = {}
    for path in sorted(Path(out).rglob('*')):
        safe_path(path)
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            files[path.relative_to(out).as_posix()] = 'directory'
            continue
        if not stat.S_ISREG(mode): raise ValueError('Session requires regular files')
        maximum = (4 if path.suffix == '.json' else 8)*1024**2
        files[path.relative_to(out).as_posix()] = file_sha256(path, maximum=maximum)
        if len(files) > 16000: raise ValueError('Session file inventory limit')
    return files


def session_lock(out, *, shared=False):
    """Kernel lock on the directory, held only by the current invocation."""
    import contextlib
    import fcntl
    @contextlib.contextmanager
    def locked():
        path = safe_path(out)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try: fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError as exc: raise ValueError('Session already has an active writer/reader') from exc
            yield
        finally: os.close(fd)
    return locked()


def inspect_session(out, *, resume=None, _active_start=None):
    """Bounded metadata/hash admission, no models, TMM, fitting or steps."""
    from ..pipeline.played_search import check_plan, verify_search, session_scope, verify_session_counters
    out = safe_path(out)
    header = read_json(out/'session.json'); plan = read_json(out/'plan.json')
    if header != dict(schema=SESSION_SCHEMA, output=str(out), context_sha256=plan['context_sha256']):
        raise ValueError('Session directory/identity differs; relocated branches unsupported')
    session_scope(plan); check_plan(plan); verify_starts(out, plan)
    starts = sorted(out.glob('invocation-*.json')); points = sorted(out.glob('session-*.json'))
    if _active_start is not None: starts = [p for p in starts if p.name != _active_start]
    if not points or len(starts) != len(points):
        raise ValueError('Unclosed invocation: active/unknown cost; no older boundary fallback')
    if len(points) > plan['job']['budgets']['candidates']+1:
        raise ValueError('Session generation limit')
    if resume is not None and safe_path(resume) != points[-1]:
        raise ValueError('Stale session generation; latest explicit point required')
    actual = session_inventory(out)
    if _active_start is not None: actual.pop(_active_start)
    prior = None; elapsed = 0.; previous_rows = []; latest = None
    for number, (start_path, point_path) in enumerate(zip(starts, points), 1):
        if start_path.name != f'invocation-{number:05d}.json' or point_path.name != f'session-{number:05d}.json':
            raise ValueError('Session generation gap')
        started = read_json(start_path); envelope = read_json(point_path); point = envelope['payload']
        if file_sha256(out/f'boundary-{number:05d}.json') != file_sha256(point_path):
            raise ValueError('Session boundary publication unconfirmed')
        if envelope['sha256'] != digest(point) or type(point['generation']) is not int or point['generation'] != number:
            raise ValueError('Session point checksum/generation differs')
        if point['previous'] != prior or started['previous'] != prior or type(started['generation']) is not int or started['generation'] != number:
            raise ValueError('Session ancestry differs')
        if point['invocation_sha256'] != file_sha256(start_path): raise ValueError('Invocation receipt differs')
        if started['schema'] != SESSION_SCHEMA or started['job_source'] != plan['job_source']:
            raise ValueError('Invocation context differs')
        verify_sources(started['producer']['loaded_sources_sha256'])
        if started['producer']['versions'] != versions(): raise ValueError('Invocation versions differ')
        duration = point['active_seconds']; charged = point['charged_seconds']
        if (type(duration) not in (int,float) or duration < 0 or type(charged) not in (int,float) or
                charged != duration+15 or point['closure_reserved_seconds'] != 15 or
                duration != point['accounted_monotonic']-started['started_monotonic']):
            raise ValueError('Session active-time accounting differs')
        elapsed += charged
        result = point['result']
        if result['status'] not in ('paused','complete') or point['publication'] != ('paused' if result['status']=='paused' else 'final'):
            raise ValueError('Session publication state differs')
        if result['status']=='complete' and result['search']['termination']=='session_paused':
            raise ValueError('Pause is not a complete result')
        if digest(result['plan']) != digest(plan) or result['counters']['elapsed_seconds'] != elapsed:
            raise ValueError('Session plan/cumulative time differs')
        history = result['search']['history']
        if digest(history[:len(previous_rows)]) != digest(previous_rows): raise ValueError('Session prefix changed')
        if prior is not None and latest['result']['status'] != 'paused': raise ValueError('Terminal session reopened')
        if not len(previous_rows) < len(history): raise ValueError('Session did not acquire a new candidate')
        quota = started['pause_after']
        if quota is not None and (type(quota) is not int or not 1 <= quota <= 16 or len(history)-len(previous_rows) > quota):
            raise ValueError('Invocation pause quota differs')
        verify_search(plan, result['search'], session=True)
        if result['status'] == 'paused' and (result['search']['termination'] != 'session_paused' or
                quota is None or len(history)-len(previous_rows) != quota):
            raise ValueError('Invalid pause boundary')
        verify_session_counters(plan, history, result['counters'], point['children'])
        if [c['session_generation'] for c in point['children'] if 'session_generation' in c] != list(range(2, number+1)):
            raise ValueError('Session admission receipts/generation differ')
        if any(actual.get(name) != sha for name,sha in point['files'].items()):
            raise ValueError('Session immutable artifact differs')
        prior = dict(file=point_path.name, sha256=file_sha256(point_path)); previous_rows = history; latest = point
    if set(actual) != set(latest['files']) | {points[-1].name, f'boundary-{len(points):05d}.json'}:
        raise ValueError('Unaccounted session acquisition/publication after latest point')
    if size(out) > plan['job']['budgets']['output_mib']*1024**2: raise ValueError('Session storage exhausted')
    if latest['result']['status'] == 'paused' and any((out/name).exists() for name in ('result.json','execution.json','execution.completed.json')):
        raise ValueError('Pause cannot publish final results')
    check_plan(plan)
    return dict(point=latest, reference=prior, plan=plan)


def read_session(out, *, resume=None, _locked=False):
    """Read full native evidence once per candidate, without new science."""
    try:
        out = safe_path(out)
        if not _locked:
            with session_lock(out, shared=True):
                return read_session(out, resume=resume, _locked=True)
        value = inspect_session(out, resume=resume)
        from ..pipeline.played_search import load_inputs, verify_candidate, verify_session_state, check_plan
        admitted = load_inputs(value['plan']['job_source']['path'])
        rows = []
        for row in value['point']['result']['search']['history']:
            full = read_json(local(out, row['file']))
            if digest(summary(full)) != digest(row): raise ValueError('Session candidate summary differs')
            verify_candidate(value['plan'], out, full, native_read=True, admitted=admitted, session=True)
            rows.append(full)
        verify_session_state(value, out, rows)
        check_plan(value['plan'])
        point = value['point']; result = point['result']
        if result['status'] == 'complete':
            closed = read_result(out, _session_checked=True)
            if not closed['ok'] or digest(closed['result']) != digest(result):
                raise ValueError('Final session publication differs')
        # Detect mutation during semantic reading too, without another native read.
        if session_inventory(out) != dict(point['files'], **{value['reference']['file']:value['reference']['sha256'],
                f"boundary-{point['generation']:05d}.json":value['reference']['sha256']}):
            raise ValueError('Session changed during reading')
        return dict(ok=result['status'] in ('paused','complete'), status=result['status'],
                    conforming=result['conforming'], resumable=result['status']=='paused',
                    result=result, resume_point=str(out/value['reference']['file']),generation=point['generation'])
    except (ValueError,OSError,KeyError,TypeError,RecursionError) as exc:
        return dict(ok=False,status='unconfirmed',conforming=False,resumable=False,reason=str(exc))
