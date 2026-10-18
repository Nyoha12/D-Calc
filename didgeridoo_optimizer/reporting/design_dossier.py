"""French comparative dossiers over existing, freshly verified native evidence."""
from __future__ import annotations

import csv
import io
from itertools import combinations
from pathlib import Path

import hashlib
_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()

from . import design_dossier_schema as s

SCOPE = ('Comparaison descriptive de points numériques acquis. Aucune nouvelle acquisition, '
         'recherche, adoption, fabrication, robustesse continue ou validation physique. '
         'La passivité et la fidélité finie ne sont pas une fidélité physique ou globale. '
         'Sources originales, entrées, ancêtres et séries requis : dossier non autonome. '
         'Intégrité relative et cohérence sémantique, sans authentification d’un ensemble intégralement réécrit.')
PASS = {'pass', 'satisfied'}
FAIL = {'fail', 'violated'}
KNOWN = PASS | FAIL


def _key(kind, path):
    return kind + ':' + str(path)


def usable(kind, response):
    if kind == 'tolerance_audit':
        return response.get('artifacts_verified') is True and response.get('semantic_verified') is True
    if kind == 'register_target':
        return type(response.get('result')) is dict and type(response.get('reading')) is dict
    return response.get('ok') is True


def native_content(response):
    """Current rereader identity changes on a later commit; acquisition does not."""
    import copy
    value = copy.deepcopy(response)
    if 'reading' in value:
        value['reading'].pop('reader', None)
    return value


def _row_check(row):
    for key in ('value_si', 'margin', 'target_si', 'lower_si', 'upper_si', 'tolerance_si', 'error_cents'):
        if row.get(key) is not None:
            s.number(row[key], key)
    s.require(row['role'] in ('hard', 'observe', 'preference'), 'rôle natif inconnu')


def verdict(rows, complete):
    hard = [r for r in rows if r['role'] == 'hard']
    observe = [r for r in rows if r['role'] != 'hard']
    failed = any(r['status'] in FAIL for r in hard)
    known = bool(hard) and all(r['status'] in PASS for r in hard)
    return dict(hard_status='violated' if failed else 'conforming' if complete and known else
                'descriptive' if complete and not hard else 'unknown',
                conforming=bool(complete and known and not failed),
                observation_coverage=dict(total=len(observe), resolved=sum(r['status'] in KNOWN for r in observe),
                    satisfied=sum(r['status'] in PASS for r in observe), violated=sum(r['status'] in FAIL for r in observe),
                    unresolved=sum(r['status'] not in KNOWN for r in observe)))


def _missing(definitions, *, kind, experiment=None):
    rows = []
    for c in definitions:
        windows = (['/'.join(c['windows'])] if c['observable'] == 'frequency_ratio' else c['windows']) if kind == 'played' else [None]
        for window in windows:
            rows.append(dict(id=c['id'], role=c['role'], observable=c['observable'], status='unresolved',
                kind=kind, experiment=experiment, window=window, value_si=None, unit_si=None,
                margin=None, margin_unit=None, reason='Lecture native absente ou non confirmée', definition=c))
    return rows


def contracts(passive_plan, geometry_plan, requests):
    # Keep ALL native request fields. Only candidate identity paths are replaced
    # by the already-associated recipe; geometry/model are the compared variable.
    played = {}
    protocols = {}
    for ident, r in requests.items():
        normalized = dict(r, case=dict(kind=r['case']['kind'], recipe=r['case']['recipe']))
        played[ident] = normalized
        protocols[ident] = {k:v for k,v in normalized.items() if k not in ('criteria', 'budgets')}
    return (dict(passive=passive_plan['native_request'], played=played, geometry_recipe=geometry_plan['recipe'],
                 passive_budgets=passive_plan['budgets'], geometry_budgets=geometry_plan['budgets']), protocols)


def _differences(a, b, prefix=''):
    if type(a) is dict and type(b) is dict:
        result = []
        for key in sorted(a.keys() | b.keys()):
            name = prefix + '.' + key if prefix else key
            if key not in a or key not in b:
                result.append(name)
            elif a[key] != b[key]:
                result.extend(_differences(a[key], b[key], name))
        return result
    return [prefix] if a != b else []


def compare(candidates):
    pairs = []
    for a, b in combinations(candidates, 2):
        contract_diffs = _differences(a['contract'], b['contract'])
        protocol_diffs = _differences(a['playing_protocol'], b['playing_protocol'])
        context_diffs = _differences(a['comparison_context'], b['comparison_context'])
        comparable = not contract_diffs and not protocol_diffs and not context_diffs and a['execution_complete'] and b['execution_complete']
        reasons = []
        if contract_diffs: reasons.append('Contrats différents : ' + ', '.join(contract_diffs))
        if protocol_diffs: reasons.append('Protocoles joués différents : ' + ', '.join(protocol_diffs))
        if context_diffs: reasons.append('Contextes différents : ' + ', '.join(context_diffs))
        if not a['execution_complete'] or not b['execution_complete']: reasons.append('Preuve ou lecture incomplète')
        if comparable:
            reasons.append('Demandes, rôles, unités, observateurs, budgets et protocoles identiques ; delta descriptif b−a.')
        reasons.append('Géométries et modèles scalaires associés distincts possibles ; aucune attribution causale exclusive à la géométrie.')
        def rowkey(r): return r['kind'], r.get('experiment'), r['id'], r.get('window')
        left = {rowkey(r):r for r in a['criteria']}; right = {rowkey(r):r for r in b['criteria']}
        deltas = []
        for key in sorted(left.keys() | right.keys(), key=str):
            x = left.get(key); y = right.get(key)
            unit = x.get('unit_si') if x else y.get('unit_si')
            allowed = (comparable and x is not None and y is not None and x['definition'] == y['definition']
                       and x.get('unit_si') == y.get('unit_si') and unit is not None
                       and x.get('value_si') is not None and y.get('value_si') is not None)
            deltas.append(dict(kind=key[0], experiment=key[1], criterion=key[2], window=key[3], unit=unit,
                a_value=x.get('value_si') if x else None, b_value=y.get('value_si') if y else None,
                delta_b_minus_a=y['value_si']-x['value_si'] if allowed else None,
                reason='Écart descriptif acquis' if allowed else 'Delta interdit : contrat, protocole, preuve ou valeur indisponible'))
        pairs.append(dict(a=a['id'], b=b['id'], same_contract=not contract_diffs,
            same_playing_protocol=not protocol_diffs, same_context=not context_diffs,
            budget_differences=[x for x in contract_diffs if 'budget' in x],
            contract_differences=contract_diffs, protocol_differences=protocol_diffs,
            comparable=comparable, reasons=reasons, deltas=deltas))
    return pairs


def compose(plan, verified, errors):
    """Deterministic derivation, reused at readback against fresh native results."""
    s.loaded_sources(); s.composition_sources()
    from ..optimization.played_search import played_rows
    from ..pipeline.geometry_state import state_recipe
    base = Path(plan['job_path']).parent
    candidates = []; audits = []
    for bundle in plan['bundles']:
        key = _key(bundle['kind'], bundle['path'])
        if bundle['kind'] == 'tolerance_audit' and key in verified:
            native = verified[key]
            audits.append(dict(bundle=bundle['path'], execution_complete=native.get('execution_complete', False),
                producer=native.get('plan', {}).get('provenance'),
                observations=[{k:o[k] for k in ('scenario', 'projection', 'configuration', 'geometry_status', 'criteria', 'effective_request')}
                              for o in native.get('observations', [])]))
    for candidate, assoc in zip(plan['job']['candidates'], plan['associations']):
        pb = s.safe(base/candidate['passive']['bundle']); gb = s.safe(base/candidate['geometry']['bundle'])
        tp = s.read(pb/'plan.json'); gp = s.read(gb/'plan.json'); gr = s.read(gb/'result.json')
        profile = s.read(assoc['profile']); rows = []; requests = {}; acquisitions = {}
        passive = verified.get(_key('tolerance_audit', pb)); geometry = verified.get(_key('geometry_state', gb))
        complete = bool(passive and passive.get('ok') is True and passive.get('semantic_verified') is True and
                        passive.get('execution_complete') is True and geometry and geometry.get('ok') is True)
        definitions = tp['native_request']['criteria']
        selected = [o for o in (passive or {}).get('observations', []) if
                    (o['scenario'], o['projection']) == (candidate['passive']['scenario'], candidate['passive']['projection'])]
        if selected and passive.get('semantic_verified') is True:
            s.require(len(selected) == 1, 'sélection native unique')
            o = selected[0]
            s.require([r['id'] for r in o['criteria']] == [c['id'] for c in definitions], 'toutes obligations passives requises')
            s.require(o['effective_request'] == tp['native_request'], 'request passive native')
            s.require(o['acoustics'].get('effective_models', gp['effective']) == gp['effective'], 'modèles natifs associés')
            if 'physical_exit_radius_m' in o['acoustics']:
                s.require(o['acoustics']['physical_exit_radius_m'] == gp['physical_radius_m'], 'rayon natif associé')
            rows.extend(dict(r, kind='passive', experiment=None, window=None, definition=c) for r,c in zip(o['criteria'], definitions))
        else:
            rows.extend(_missing(definitions, kind='passive')); complete = False
        acquisitions['passive'] = tp['provenance']
        acquisitions['geometry'] = dict(acquisition=gp['acquisition'], base_sha=gp['base_sha'], producer_sources=gp['producer_sources'])
        for exp in candidate['played']:
            rb = s.safe(base/exp['bundle']); rp = s.read(rb/'plan.json'); request = rp['request']; requests[exp['id']] = request
            acquisitions[exp['id']] = rp['producer']
            native = verified.get(_key('register_target', rb))
            if native and usable('register_target', native):
                result = native['result']; meta = result['plan']['metadata']
                s.require(result['plan'] == rp, 'plan joué natif')
                if geometry:
                    for k in ('inputs', 'recipe', 'effective', 'evidence', 'model_sha256', 'model_parameters_sha256'):
                        s.require(meta[k] == geometry['metadata'][k], 'association native jouée/géométrie: ' + k)
                    s.require(result['context_checks']['finite_fidelity'] == gr['fidelity'], 'fidélité native associée')
                s.require(request['case']['recipe'] == state_recipe(gp['recipe']), 'projection native recette')
                native_rows = result['analysis']['criteria']; definitions_played = request['criteria']
                s.require([r['id'] for r in native_rows] == [c['id'] for c in definitions_played], 'toutes obligations jouées requises')
                for r,c in zip(native_rows, definitions_played):
                    s.require(r['role'] == c['role'] and r['observable'] == c['observable'], 'identité obligation jouée')
                    windows = 1 if c['observable'] == 'frequency_ratio' else len(c['windows'])
                    s.require(len(r['values_si']) == windows or
                              r['status'] == 'unsupported' and r['values_si'] == [None], 'valeurs natives manquantes')
                    for v in r['values_si']:
                        if v is not None: s.number(v, 'valeur jouée')
                byid = {c['id']:c for c in definitions_played}
                rows.extend(dict(r, experiment=exp['id'], definition=byid[r['id']]) for r in
                            played_rows(request, native_rows, 'nominal', exp['id']))
                complete = complete and native.get('ok') is True and result['status'] == 'complete'
            else:
                rows.extend(_missing(request['criteria'], kind='played', experiment=exp['id'])); complete = False
        for row in rows: _row_check(row)
        contract, protocol = contracts(tp, gp, requests)
        candidates.append(dict(id=candidate['id'], selection=candidate, association='verified' if complete else 'unconfirmed',
            execution_complete=complete, criteria=rows, **verdict(rows, complete),
            profile_file='profile_' + candidate['id'] + '.json', profile_sha256=assoc['profile_sha256'],
            geometry_sections=[dict(index=i+1, **segment) for i,segment in enumerate(profile['segments'])],
            inputs=assoc['inputs'], model_sha256=assoc['model_sha256'],
            model_parameters_sha256=s.read(gb/'evidence.json')['model_sha256'],
            fidelity=gr['fidelity'] if geometry else None, acquisitions=acquisitions,
            contract=contract, playing_protocol=protocol,
            comparison_context=dict(effective_models=gp['effective'],
                config=assoc['inputs']['config'], materials=assoc['inputs']['materials'], variant_rules=assoc['inputs']['variant_rules'])))
    return dict(candidates=candidates, passive_audits=audits, comparisons=compare(candidates),
                execution_complete=not errors and all(c['execution_complete'] for c in candidates),
                errors=errors, scope=SCOPE)


def csv_bytes(rows, fields):
    stream = io.StringIO(newline=''); writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: (s.encoded(v).decode().strip() if isinstance(v, (dict, list)) else v) for k,v in row.items()})
    return stream.getvalue().encode()


def views(payload):
    criteria = [dict(r, candidate=c['id']) for c in payload['candidates'] for r in c['criteria']]
    sections = [dict(r, candidate=c['id']) for c in payload['candidates'] for r in c['geometry_sections']]
    comparisons = [dict(d, a=p['a'], b=p['b'], same_contract=p['same_contract'], same_playing_protocol=p['same_playing_protocol'],
                        pair_reasons=p['reasons']) for p in payload['comparisons'] for d in p['deltas']]
    lines = ['Dossier comparatif de preuves acquises', SCOPE,
             'Lecture complète : ' + ('oui' if payload['execution_complete'] else 'non')]
    for c in payload['candidates']:
        lines.extend(['', f"Candidat {c['id']} : hard={c['hard_status']}, association={c['association']}",
            'Modèle scalaire : ' + c['model_sha256'], 'Couverture des observations facultatives : ' + str(c['observation_coverage']),
            'Sections physiques en cm (aucune tranche de discrétisation) :'])
        lines.extend(f"  {r['index']}: L={r['length_cm']}, diamètre entrée={r['d_in_cm']}, sortie={r['d_out_cm']}" for r in c['geometry_sections'])
        for r in c['criteria']:
            lines.append(f"  {r['kind']}/{r.get('experiment') or '-'}/{r['id']}/{r.get('window') or '-'} "
                         f"[{r['role']}] {r['status']} : {r.get('value_si')} {r.get('unit_si')}, "
                         f"marge={r.get('margin')} {r.get('margin_unit')}")
    for p in payload['comparisons']:
        lines.extend(['', f"Paire {p['a']} / {p['b']} : même contrat={p['same_contract']}, même protocole={p['same_playing_protocol']}", *p['reasons']])
        lines.extend(f"  {d['kind']}/{d['experiment'] or '-'}/{d['criterion']}/{d['window'] or '-'} : "
                     f"delta b−a={d['delta_b_minus_a']} {d['unit']} ; {d['reason']}" for d in p['deltas'])
    lines.append('\nAutres observations du bundle passif (hors conjonction des candidats sélectionnés) :')
    for audit in payload['passive_audits']:
        for o in audit['observations']:
            for r in o['criteria']:
                lines.append(f"  {o['scenario']}/{o['projection']}/{r['id']} [{r['role']}] : {r['status']}, "
                             f"{r.get('value_si')} {r.get('unit_si')}")
    if payload['errors']: lines.append('Lectures refusées/partielles : ' + s.encoded(payload['errors']).decode().strip())
    return {'criteria.csv': csv_bytes(criteria, ['candidate','kind','experiment','id','window','role','observable','status',
                'value_si','unit_si','target_si','margin','margin_unit','reason','definition']),
            'geometry.csv': csv_bytes(sections, ['candidate','index','kind','length_cm','d_in_cm','d_out_cm','material_id','profile_params']),
            'comparison.csv': csv_bytes(comparisons, ['a','b','same_contract','same_playing_protocol','kind','experiment','criterion','window',
                'a_value','b_value','delta_b_minus_a','unit','reason','pair_reasons']),
            'summary.txt': ('\n'.join(lines)+'\n').encode()}


def read_result(output, *, total_seconds=None):
    """Re-read pinned native sources in fresh children, then reproduce all semantics.

    No historical override or persistent cache. Original sources remain required.
    This checks relative integrity, not authentication of wholly rewritten evidence.
    """
    from ..pipeline.design_dossier import read_native, boundary, confirmed, remaining
    import tempfile
    import time
    start = time.monotonic(); out = s.safe(output)
    s.loaded_sources()
    # Bound even malformed dossier inspection by the public cap.
    cap = s.LIMITS['total_seconds'] if total_seconds is None else total_seconds
    s.require(type(cap) in (int, float) and 0 < s.number(cap, 'budget relecture') <= s.LIMITS['total_seconds'], 'budget relecture')
    public_deadline = start+cap
    remaining(public_deadline)
    s.require(not (out/'execution.failed.json').exists(), 'exécution sans autorité finale')
    terminal_pins = {str(out/name):s.sha(out/name) for name in
                     ('manifest.json','execution.json','execution.closed.json','execution.completed.json')}
    manifest = s.read(out/'manifest.json'); receipt = s.read(out/'execution.json')
    s.exact(manifest, ('schema', 'files'), 'manifest dossier')
    s.require(manifest['schema'] == s.RESULT_SCHEMA, 'schéma manifeste')
    s.require(receipt['manifest_sha256'] == s.sha(out/'manifest.json'), 'manifest altéré')
    s.require(s.same(s.read(out/'execution.closed.json'), dict(execution_sha256=s.sha(out/'execution.json'), children_collected=True)), 'clôture dossier')
    terminal = s.read(out/'execution.completed.json')
    s.exact(terminal, ('schema','ok','seconds','manifest_sha256','execution_sha256','closure_sha256'), 'reçu terminal')
    s.require(type(terminal['ok']) is bool and s.same(terminal, dict(schema=s.RESULT_SCHEMA,
        ok=receipt['candidate_complete'], seconds=terminal['seconds'],
        manifest_sha256=s.sha(out/'manifest.json'), execution_sha256=s.sha(out/'execution.json'),
        closure_sha256=s.sha(out/'execution.closed.json'))), 'reçu terminal dossier')
    required = {'plan.json','preflight.json','result.json','criteria.csv','geometry.csv','comparison.csv','summary.txt'}
    s.require(type(manifest['files']) is dict and required <= set(manifest['files']) and len(manifest['files']) <= 512, 'inventaire dossier')
    total = 0
    for name,h in manifest['files'].items():
        remaining(public_deadline)
        s.require(Path(name).name == name, 'nom artefact dossier')
        total += (out/name).stat().st_size
        s.require(total <= 160*s.MIB and s.sha(out/name) == h, 'artefact dossier altéré: ' + name)
    expected_files = set(manifest['files']) | {'manifest.json','execution.json','execution.closed.json','execution.completed.json'}
    s.require({p.name for p in out.iterdir()} == expected_files, 'inventaire dossier différent')
    result = s.read(out/'result.json')
    s.exact(result, ('schema_version','created_utc','reader','payload','native_results'), 'result dossier')
    s.require(result['schema_version'] == s.RESULT_SCHEMA, 'version résultat')
    plan = s.read(out/'plan.json')
    s.require(s.same(plan, s.read(out/'preflight.json')), 'plan/preflight différents')
    current = s.preflight(plan['job_path'])
    s.require(s.same(current, plan), 'demandes/profils/preuves pinnés différents')
    s.exact(result['reader'], ('sha','sources','source_status','python'), 'identité lecteur')
    import re
    from datetime import datetime
    s.require(type(result['reader']['sha']) is str and re.fullmatch('[0-9a-f]{40}', result['reader']['sha']), 'SHA lecteur')
    s.require(s.same(result['reader']['sources'], {name:plan['pins'][str(s.ROOT/name)] for name in s.OWN_SOURCES}), 'sources lecteur')
    s.require(result['reader']['source_status'] == 'working_tree_fingerprints_not_a_published_commit', 'statut source lecteur')
    s.require(type(result['reader']['python']) is str and re.fullmatch(r'\d+\.\d+\.\d+', result['reader']['python']), 'version Python lecteur')
    s.require(datetime.fromisoformat(result['created_utc']).tzinfo is not None, 'date création avec fuseau requise')
    s.exact(receipt, ('schema','candidate_complete','status','children','operation_seconds','operation_limit_seconds','public_limit_seconds','manifest_sha256'), 'reçu exécution')
    s.require(receipt['schema'] == s.EXECUTION_SCHEMA and type(receipt['candidate_complete']) is bool
              and receipt['status'] == ('ready' if receipt['candidate_complete'] else 'partial'), 'statut reçu')
    b = plan['job']['budgets']
    s.require(sum(p.stat().st_size for p in out.iterdir()) <= b['output_mib']*s.MIB, 'budget sortie dossier')
    s.require(s.number(receipt['operation_limit_seconds'], 'limite opération') == b['total_seconds']-b['closure_seconds']
              and s.number(receipt['public_limit_seconds'], 'limite publique') == b['total_seconds'], 'limites reçu')
    s.require(0 <= s.number(receipt['operation_seconds'], 'durée opération') <= receipt['operation_limit_seconds'], 'durée opération dossier')
    s.require(receipt['operation_seconds'] <= s.number(terminal['seconds'], 'durée publique') <= b['total_seconds'], 'durée publique dossier')
    s.require(type(receipt['children']) is list and len(receipt['children']) <= len(plan['bundles']), 'reçus enfants')
    admitted = {_key(b['kind'], b['path']) for b in plan['bundles']}
    seen = set(); successful_files = {}; usable_files = {}
    child_keys = {'schema','launched','collection_confirmed','operation_seconds','collection_seconds','collection_limit_seconds','errors','kind','bundle','pid','exit_code','reaped','pipes_closed','timed_out','cancelled','seconds',
        'wall_limit_seconds','memory_mib','file_bytes_limit','blas_threads','fresh_interpreter','stdout','stdout_sha256','stderr','stderr_sha256'}
    for child in receipt['children']:
        s.exact(child, child_keys, 'reçu enfant')
        key = _key(child['kind'], child['bundle'])
        s.require(key in admitted and key not in seen, 'enfant étranger/dupliqué'); seen.add(key)
        s.require(child['schema'] == s.CHILD_SCHEMA and child['launched'] is True, 'schéma/lancement enfant')
        s.require(type(child['errors']) is list and all(type(e) is str for e in child['errors']), 'causes enfant')
        s.require(type(child['pid']) is int and child['pid'] > 0 and type(child['exit_code']) is int, 'PID/code enfant')
        s.require(child['reaped'] is True and child['pipes_closed'] is True and child['fresh_interpreter'] is True and child['collection_confirmed'] is True, 'collecte enfant')
        s.require(type(child['timed_out']) is bool and type(child['cancelled']) is bool, 'timeout/annulation booléens')
        s.require(0 < s.number(child['wall_limit_seconds'], 'délai') <= plan['job']['budgets']['child_seconds'], 'délai enfant')
        operation = s.number(child['operation_seconds'], 'durée opération enfant')
        collection = s.number(child['collection_seconds'], 'durée collecte')
        collection_limit = s.number(child['collection_limit_seconds'], 'limite collecte')
        s.require(0 <= collection <= collection_limit <= 4 and operation >= 0, 'budget cumulatif collecte')
        s.require(abs(s.number(child['seconds'], 'durée') - operation - collection) <= 1e-8, 'durées enfant contradictoires')
        s.require(operation <= child['wall_limit_seconds'] or child['timed_out'] is True, 'retour tardif non déclaré')
        s.require(type(child['memory_mib']) is int and child['memory_mib'] == plan['job']['budgets']['memory_mib']
                  and type(child['blas_threads']) is int and child['blas_threads'] == 1, 'limites enfant')
        s.require(type(child['file_bytes_limit']) is int and 0 < child['file_bytes_limit'] <= b['file_mib']*s.MIB, 'limite fichier enfant')
        for stream in ('stdout','stderr'):
            name = child[stream]
            s.require(name in manifest['files'] and manifest['files'][name] == child[stream+'_sha256']
                      and (out/name).stat().st_size <= child['file_bytes_limit'], 'flux/reçu enfant')
        if confirmed(child):
            successful_files[key] = child['stdout']
        try:
            native_response = s.read(out/child['stdout'])
        except (ValueError, OSError):
            native_response = {}
        if key in successful_files and type(native_response) is dict and usable(child['kind'], native_response):
            usable_files[key] = child['stdout']
        if receipt['candidate_complete']:
            s.require(key in successful_files, 'faux succès enfant')
    s.require(sum(c['seconds'] for c in receipt['children']) <= receipt['operation_seconds'] + 1e-8, 'durées cumulées enfants/dossier contradictoires')
    limit = plan['job']['budgets']['total_seconds'] if total_seconds is None else total_seconds
    s.require(type(limit) in (int,float) and 0 < limit <= plan['job']['budgets']['total_seconds'], 'budget relecture')
    deadline = start + limit - plan['job']['budgets']['closure_seconds']
    public_deadline = start+limit
    remaining(deadline)
    bundles = {_key(b['kind'], b['path']):b for b in plan['bundles']}
    verified = {}; rereceipts = []
    s.require(type(result['native_results']) is dict and set(result['native_results']) <= set(bundles), 'lectures étrangères')
    s.require(all(successful_files.get(k) == v for k,v in result['native_results'].items()), 'lectures/reçus non liés')
    s.require(result['native_results'] == usable_files, 'observation native vérifiée omise')
    if receipt['candidate_complete']:
        s.require(set(result['native_results']) == set(bundles), 'succès sans toutes les lectures')
    try:
        remaining(deadline)
        with tempfile.TemporaryDirectory(prefix='dcalc-dossier-read-') as tmp:
            for index, (key, name) in enumerate(result['native_results'].items()):
                s.require(name in manifest['files'], 'lecture native absente du manifeste')
                remaining(deadline); boundary(plan); remaining(deadline)
                response, child = read_native(bundles[key], Path(tmp), index, plan['job']['budgets'], deadline)
                rereceipts.append(child)
                remaining(deadline)
                s.require(confirmed(child) and usable(bundles[key]['kind'], response), 'relecture native non confirmée: ' + key)
                # Native reader provenance is kept separately; semantic payloads
                # and saved snapshots are still bound to the actual native bytes.
                stored = s.read(out/name)
                if 'reading' in stored:
                    identity = stored['reading']['reader']
                    s.require(identity['software']['sha'] == result['reader']['sha'], 'SHA lecteur natif enregistré')
                    fresh_identity = response['reading']['reader']
                    s.require(s.same(identity['versions'], fresh_identity['versions']) and
                              s.same(identity['loaded_sources_sha256'], fresh_identity['loaded_sources_sha256']), 'identité sources/versions lecteur natif')
                    s.require(identity['versions']['python'].split()[0] == result['reader']['python'], 'version Python dossier/lecteur natif')
                    for rel, digest in identity['loaded_sources_sha256'].items():
                        s.require(plan['pins'].get(str(s.ROOT/rel)) == digest, 'source lecteur natif enregistrée')
                s.require(s.same(native_content(response), native_content(stored)), 'réponse native différente des preuves pinnées')
                verified[key] = response
            remaining(deadline)
            rebuilt = compose(plan, verified, result['payload']['errors'])
            remaining(deadline)
            s.require(s.same(rebuilt, result['payload']), 'cohérence sémantique dossier')
            for name, raw in views(rebuilt).items():
                remaining(deadline)
                s.require(s.data(out/name) == raw, 'vue dérivée incohérente: ' + name)
            for c,a in zip(rebuilt['candidates'], plan['associations']):
                s.require(s.data(out/c['profile_file']) == s.data(a['profile']), 'profil natif copié différent')
            s.require(receipt['candidate_complete'] is rebuilt['execution_complete'], 'complétude du reçu')
            remaining(deadline); boundary(plan); remaining(deadline)
            for name,h in manifest['files'].items():
                s.require(s.sha(out/name) == h, 'dossier modifié pendant relecture')
            s.verify_pins(terminal_pins)
            s.require({p.name for p in out.iterdir()} == expected_files, 'inventaire modifié pendant relecture')
        remaining(public_deadline)
    except (Exception, KeyboardInterrupt) as exc:
        return dict(ok=False, status='unconfirmed', reason=str(exc), children=rereceipts, scope=SCOPE)
    return dict(ok=rebuilt['execution_complete'], status='verified' if rebuilt['execution_complete'] else 'partial',
                semantic_verified=True, payload=rebuilt, children=rereceipts, scope=SCOPE)
