"""Immutable audit bundles and semantic fresh-process readback (no acoustics)."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys

from .constrained_design import fingerprint, versions, verify_sources, new_destination
from .regime_reference import (safe_path, atomic_bytes, write_json, read_json,
                               file_sha256, read_execution as native_read_execution)

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = 'dcalc.tolerance_completion.v1'
PREPARED = '.pending-tolerance-completion.json'


def source_files():
    """Actual imported files, including the true CLI entry; no guessed provenance."""
    result = {}
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('didgeridoo_optimizer','tools.')):
            f = getattr(module,'__file__',None)
            if f:
                path = Path(f).resolve()
                if not path.is_relative_to(ROOT): raise ValueError('source chargée hors dépôt')
                result[path.relative_to(ROOT).as_posix()] = file_sha256(path)
    main = getattr(sys.modules.get('__main__'),'__file__',None)
    if main and Path(main).resolve() == ROOT/'tools/tolerance_audit.py':
        result['tools/tolerance_audit.py'] = file_sha256(main)
    return result


def provenance(executed=()):
    from ..pipeline.fixed_design import _software_source
    loaded = source_files()
    return dict(software=_software_source(),versions=versions(),loaded_sources_sha256=loaded,
                executed_sources_sha256={p:file_sha256(ROOT/p) for p in sorted(executed)})


def identity_plan(plan):
    return {k:v for k,v in plan.items() if k != 'provenance'}


def unavailable_rows(criteria,reason):
    return [dict(id=c['id'],observable=c['observable'],role=c['role'],
        status='unsupported' if c['unsupported_reason'] else 'unresolved',
        reason=c['unsupported_reason'] or reason,value_si=None,unit_si=None,margin=None,margin_unit=None)
        for c in criteria]


def _limit(plan):
    # Bound the manifest by the actual planned number and maximum name length.
    reserve = 65536 + 512*(5*len(plan['geometry'])+len(plan['parsed']['scenarios'])+16)
    return plan['budgets']['output_mib']*1024**2 - reserve


def profile_name(scenario, projection):
    return f'profile_{len(scenario)}_{scenario}__{projection}.json'


def write_geometry(output,scenario,raw,kind,plan):
    # Assembly.generate exposes piece insertion order in component_ids. Preserve
    # that native order so the exported stock regenerates identical metadata.
    data = (json.dumps(raw,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode()
    if len(data) > 4*1024**2:
        raise ValueError('JSON output budget')
    atomic_bytes(Path(output)/f'{kind}_{scenario}.json',data,budget_bytes=_limit(plan))


def write_unavailable_scenario(output,scenario,reason,plan):
    write_json(Path(output)/f"unavailable_{scenario['id']}.json",
        dict(scenario=scenario,status='unresolved',reason=reason,physical_geometry_available=False),
        budget_bytes=_limit(plan))


def write_profile(output,scenario,projection,raw,plan):
    write_json(Path(output)/profile_name(scenario,projection),raw,budget_bytes=_limit(plan))


def write_observation(output,observation,sequence,plan):
    write_json(Path(output)/f'observation_{sequence:04d}.json',observation,budget_bytes=_limit(plan))


def write_spectral_trace(output,sequence,level,trace,plan):
    write_json(Path(output)/f'spectrum_{sequence:04d}_{level}.json',trace,budget_bytes=_limit(plan))


def _table(output,name,rows,fields,limit):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
    writer.writeheader(); writer.writerows(rows)
    atomic_bytes(Path(output)/name,stream.getvalue().encode(),budget_bytes=limit)


def write_manifest(output,limit):
    out = Path(output)
    manifest = {p.name:file_sha256(p) for p in sorted(out.iterdir())
                if not p.name.startswith('.') and p.name not in ('manifest.json','execution.json',
                'execution.closed.json','execution.completed.json','execution.cancelled.json')}
    write_json(out/'manifest.json',manifest,budget_bytes=limit)


def export_result(output,result):
    out = Path(output); plan = result['plan']; limit = _limit(plan)
    criteria = [dict(r,scenario=o['scenario'],projection=o['projection'])
                for o in result['observations'] for r in o['criteria']]
    _table(out,'criteria.csv',criteria,['scenario','projection','id','role','observable','status',
        'value_si','unit_si','target_si','lower_si','upper_si','tolerance_si','tolerance_dimension',
        'margin','margin_unit','convergence_estimate','reason'],limit)
    _table(out,'scenarios.csv',[dict(o,hard_violations=sum(r['role']=='hard' and r['status']=='violated'
        for r in o['criteria'])) for o in result['observations']],
        ['scenario','projection','configuration','nominal','geometry_status','hard_violations','reason'],limit)
    deltas = []
    for s in plan['parsed']['scenarios']:
        for u in plan['parsed']['uncertainties']:
            for field in u['fields']:
                deltas.append(dict(scenario=s['id'],uncertainty=u['id'],field=field,
                    coefficient=s['coefficients'][u['id']],delta_si=u['delta_si'],unit_si='m',
                    effective_shift_si=s['coefficients'][u['id']]*u['delta_si'],origin=u['origin']))
    _table(out,'deltas.csv',deltas,['scenario','uncertainty','field','coefficient','delta_si',
                                  'effective_shift_si','unit_si','origin'],limit)
    _table(out,'margins.csv',result['margins'],['projection','criterion','scenario','margin','unit','status'],limit)
    write_json(out/'result.json',result,budget_bytes=limit)
    atomic_bytes(out/'summary.txt',(
        'Audit dimensionnel fini — instrument fixé\n'
        f"État : {result['status']}\n"
        f"Exécution complète : {result['execution_complete']}\n"
        f"Obligations conformes dans les scénarios calculés : {result['sampled_hard_conforming']}\n"
        f"Demande entièrement couverte : {result['request_fully_covered']}\n"
        f"Contre-exemple trouvé : {result['counterexample_found']}\n"
        'Robustesse continue certifiée : false\n'
        'Les marges minimales portent seulement sur les observations calculées ; aucune borne intérieure.\n'
        'Une géométrie commune par scénario, commandes q et demandes natives conservées.\n'
        'Aucune recherche, réparation, simulation jouée ou validation matérielle.\n'
        'Portées non couvertes : '+json.dumps(plan['uncovered'],ensure_ascii=False)+'\n'
    ).encode(),budget_bytes=limit)
    write_manifest(out,limit)


def completion_record(output):
    out = Path(output)
    return dict(schema=PROTOCOL,execution_sha256=file_sha256(out/'execution.json'),
                closure_sha256=file_sha256(out/'execution.closed.json'))


def close_prepared(output,response,plan):
    """Prepare authority; caller commits only after every fallible cleanup."""
    out = Path(output); limit = plan['budgets']['output_mib']*1024**2
    if not (out/'manifest.json').exists(): write_manifest(out,limit)
    response.update(manifest_sha256=file_sha256(out/'manifest.json'),completion_protocol=PROTOCOL)
    write_json(out/'execution.json',response,budget_bytes=limit)
    write_json(out/'execution.closed.json',dict(execution_sha256=file_sha256(out/'execution.json'),cancelled=False),budget_bytes=limit)
    write_json(out/PREPARED,completion_record(out),budget_bytes=limit)


def commit_terminal(output):
    out = Path(output)
    os.link(out/PREPARED,out/'execution.completed.json',follow_symlinks=False)


def read_execution(output):
    receipt = native_read_execution(output)
    try:
        if receipt.get('completion_protocol') != PROTOCOL:
            raise ValueError('protocole terminal absent/inconnu')
        if read_json(Path(output)/'execution.completed.json') != completion_record(output):
            raise ValueError('reçu terminal incohérent')
    except (OSError,ValueError,TypeError) as exc:
        return dict(ok=False,status='unconfirmed',reason=str(exc))
    return receipt


def _require(condition, message):
    if not condition:
        raise ValueError('cohérence sémantique: '+message)


def _same_number(actual, expected, label):
    # Both sides use the native scalar arithmetic on the same serialized values;
    # no extra tolerance may turn a contradiction into a conforming observation.
    _require(type(actual) in (int, float) and math.isfinite(actual) and actual == expected, label)


def _unsupported(criterion, request):
    from ..optimization.design_contract import scope_reason
    obs = criterion['observable']
    return bool(scope_reason(request.get('scope', {})) or scope_reason(criterion['scope']) or
        obs not in ('geometry', 'resonance_frequency', 'resonance_ratio') or
        criterion['level'] != ('geometry' if obs == 'geometry' else 'passive') or
        criterion['role'] == 'preference' and request.get('preference_method') == 'weighted')


def _verify_row(row, criterion, request, geometry_status, profile):
    from ..optimization.design_contract import UNITS, quantity, target_quantity, expression
    for key in ('id', 'observable', 'role'):
        _require(row.get(key) == criterion[key], 'identité/rôle du critère: '+key)
    for key in ('expression', 'mode', 'numerator', 'denominator', 'level', 'scope', 'unit'):
        if key in row:
            _require(row[key] == criterion.get(key), 'critère original: '+key)
    dimension = UNITS[criterion['unit']][0]
    unit = dict(length='m', frequency='Hz', scalar='1', pressure='Pa', cent='cent')[dimension]
    tolerance, td = quantity(criterion['tolerance'])
    if 'target' in criterion:
        target = target_quantity(criterion['target'], dimension)
        lower, upper = ((target*2**(-tolerance/1200), target*2**(tolerance/1200)) if td == 'cent'
                        else (target-tolerance, target+tolerance))
    else:
        target = None
        lo, hi = [quantity(q, dimension)[0] for q in criterion['bounds']]
        lower, upper = lo-tolerance, hi+tolerance
    expected = dict(dimension=dimension, target_si=target, lower_si=lower, upper_si=upper,
                    tolerance_si=tolerance, tolerance_dimension=td)
    for key, value in expected.items():
        if key in row:
            _require(row[key] == value, 'dimension/demande du critère: '+key)
    obs = criterion['observable']
    unsupported = _unsupported(criterion, request)
    status = row.get('status')
    _require(status in ('satisfied', 'violated', 'unsupported', 'unresolved', 'out_of_domain'), 'statut critère')
    _require((status == 'unsupported') == unsupported, 'capacité/portée originale')
    out_of_domain = status == 'out_of_domain'
    if out_of_domain:
        _require(obs in ('resonance_frequency', 'resonance_ratio') and
                 request.get('models', {}).get('radiation_model', 'legacy') != 'legacy' and
                 row.get('reason') == 'radiation Silva: domaine déclaré dépasse |ka|=2',
                 'domaine acoustique déclaré')
    if geometry_status != 'valid' or unsupported:
        _require(status == ('unsupported' if unsupported else 'unresolved') and
                 row.get('value_si') is None and row.get('margin') is None and
                 row.get('margin_unit') is None, 'observation indisponible')
        _require(row.get('unit_si') in (None, unit), 'unité indisponible')
        return
    _require(row.get('unit_si') == unit, 'unité SI')
    value = row.get('value_si')
    if value is None:
        _require((status == 'unresolved' or out_of_domain) and row.get('margin') is None and
                 row.get('margin_unit') is None, 'valeur absente')
        return
    _same_number(value, value, 'valeur finie')
    if obs == 'geometry':
        expected_value, actual_dimension, _ = expression(criterion['expression'], profile)
        _require(actual_dimension == dimension, 'dimension géométrique')
        _same_number(value, expected_value, 'valeur géométrique/profil')
    # Acoustic values/convergence estimates remain evidence supplied by the
    # producer. Only their scalar relationship to the original request is checked.
    if td == 'cent' and value <= 0:
        _require(status == 'unresolved' and row.get('margin') is None, 'cents non positifs')
        return
    error_cents = None
    if td == 'cent':
        error_cents = 1200*math.log2(value/target)
        margin = tolerance-abs(error_cents); residual = error_cents/tolerance
    elif target is not None:
        error = value-target; margin = tolerance-abs(error); residual = error/tolerance
        if dimension == 'frequency' and value > 0 and target > 0:
            error_cents = 1200*math.log2(value/target)
    else:
        margin = min(value-lower, upper-value)
        low, high = lower+tolerance, upper-tolerance
        residual = (value-low)/tolerance if value < low else (value-high)/tolerance if value > high else 0.
    _same_number(row.get('margin'), margin, 'marge/demande')
    _require(row.get('margin_unit') == ('cent' if td == 'cent' else unit), 'unité marge')
    for key, expected_value in (('residual_normalized', residual), ('error_cents', error_cents)):
        if key in row:
            if expected_value is None: _require(row[key] is None, key)
            else: _same_number(row[key], expected_value, key)
    estimate = row.get('convergence_estimate')
    if estimate is not None:
        _same_number(estimate, estimate, 'estimation finie')
        _require(estimate >= 0, 'estimation négative')
    expected_status = ('unresolved' if estimate is not None and estimate > 0 and abs(margin) <= estimate
                       else 'satisfied' if margin >= 0 else 'violated')
    _require(out_of_domain or status == expected_status, 'statut/marge/estimation')


def verify_semantics(out, plan, result, observations, manifest):
    """Compare bundle evidence to its original plan, without a producer import.

    Preflight profile/metadata fingerprints bind files to the planned projection;
    they are relative integrity evidence, never external authentication. Stored
    acoustic quantities are not recalculated or acoustically revalidated here.
    """
    from ..geometry.tolerance_scenarios import parse_scenarios, perturb, ScenarioUnavailable
    _require(result['schema_version'] == plan['schema_version'] == plan['job']['schema_version'] ==
             'dcalc.tolerance_audit.v1', 'schéma original')
    _require(plan['continuous_robustness_certified'] is False, 'garantie continue du plan')
    kind = plan['job']['input']['kind']
    parsed = parse_scenarios(plan['job'], kind, plan['nominal'])
    _require(parsed == plan['parsed'], 'scénarios/écarts du JOB original')
    request = plan['native_request']
    projections = ([dict(id='fixed', configuration='nominal', request=request)] if kind == 'fixed'
                   else request['projections'])
    pmap = {p['id']: p for p in projections}
    _require(len(pmap) == len(projections), 'projection dupliquée')
    scenarios = {s['id']: s for s in parsed['scenarios']}
    expected = {(s, p) for s in scenarios for p in pmap}
    geometry = {(g['scenario'], g['projection']): g for g in plan['geometry']}
    _require(set(geometry) == expected and len(geometry) == len(plan['geometry']), 'grille originale')
    hard = {p['id']: [c['id'] for c in p['request']['criteria'] if c['role'] == 'hard'] for p in projections}
    _require(plan['expected_hard'] == hard, 'hard originaux')
    uncovered = (plan['job']['coverage']['kind'] == 'continuous' or
                 kind == 'assembly' and request.get('coverage', {'kind': 'discrete'})['kind'] != 'discrete' or
                 any(_unsupported(c, p['request']) for p in projections for c in p['request']['criteria']))
    _require(bool(plan['uncovered']) == bool(uncovered), 'portées originales non couvertes')
    seen = set(); files = {}
    for observation in observations:
        pair = observation['scenario'], observation['projection']
        _require(pair in expected and pair not in seen, 'observation inconnue/dupliquée')
        seen.add(pair)
        sid, pid = pair; scenario = scenarios[sid]; projection = pmap[pid]; g = geometry[pair]
        _require(observation['configuration'] == projection['configuration'], 'configuration')
        _require(observation['effective_request'] == projection['request'], 'demande effective')
        if 'effective_models' in observation['acoustics']:
            _require(observation['acoustics']['effective_models'] ==
                     plan['effective_models'][projections.index(projection)], 'modèles effectifs')
        _require(observation['coefficients'] == scenario['coefficients'] and
                 observation['nominal'] is scenario['nominal'], 'coefficients/nominal')
        issue = g['issue']; status = issue['status'] if issue else 'valid'
        _require(observation['geometry_status'] == status, 'statut géométrie/plan')
        if sid not in files:
            try: physical = perturb(plan['nominal'], parsed, scenario, kind)
            except ScenarioUnavailable:
                name = f'unavailable_{sid}.json'
                _require(name in manifest, 'descripteur indisponible manquant')
                unavailable = read_json(out/name)
                _require(unavailable['scenario'] == scenario and unavailable['status'] == 'unresolved' and
                         unavailable['physical_geometry_available'] is False, 'descripteur scénario')
                files[sid] = None
            else:
                name = f'{kind}_{sid}.json'
                _require(name in manifest, 'géométrie physique manquante')
                files[sid] = read_json(out/name)
                _require(files[sid] == physical, 'géométrie/écarts prescrits')
        physical = files[sid]
        if physical is not None:
            _require(fingerprint(physical) == g['geometry_sha256'], 'géométrie/plan')
            if kind == 'assembly':
                _require(list(physical['pieces']) == g['piece_order'], 'ordre natif des pièces')
            _require(fingerprint(observation['geometry']) == g['metadata_sha256'], 'métadonnées/projection')
        profile = None
        if status == 'valid':
            name = profile_name(sid, pid)
            _require(name in manifest, 'profil manquant')
            profile = read_json(out/name)
            _require(fingerprint(profile) == g['profile_sha256'], 'profil/projection prévue')
        criteria = projection['request']['criteria']; rows = observation['criteria']
        _require([r['id'] for r in rows] == [c['id'] for c in criteria], 'critères absents/dupliqués/inconnus')
        for row, criterion in zip(rows, criteria):
            _verify_row(row, criterion, projection['request'], status, profile)
    _require(type(result['execution_complete']) is bool, 'complétude booléenne')
    _require(not result['execution_complete'] or seen == expected, 'sortie complète tronquée')
    for key, expected_value in summarize(observations, plan, result['execution_complete']).items():
        _require(result.get(key) == expected_value and
                 (type(result.get(key)) is bool if type(expected_value) is bool else True), 'agrégat '+key)


def read_result(output):
    """Verify bytes and internal semantics without importing/replaying the producer.

    ok means a confirmed command, not conformity. Partial observations remain
    accessible with ok=false after failed or unconfirmed execution.
    """
    out = safe_path(output); receipt = read_execution(out)
    manifest_path = out/'manifest.json'
    if not manifest_path.exists():
        # No completed manifest: inspect individually committed observations, never
        # promote them to an authenticated complete result.
        observations = [read_json(p) for p in sorted(out.glob('observation_*.json'))]
        return dict(ok=False,artifacts_verified=False,semantic_verified=False,execution=receipt,
            observations=observations, counterexample_found=counterexample(observations),
            sampled_hard_conforming=False,request_fully_covered=False,
            execution_complete=False,continuous_robustness_certified=False)
    manifest = read_json(manifest_path)
    if receipt.get('manifest_sha256') and receipt['manifest_sha256'] != file_sha256(manifest_path):
        raise ValueError('manifest altéré')
    if 'plan.json' not in manifest: raise ValueError('plan absent du manifeste')
    for name,sha in manifest.items():
        if Path(name).name != name or file_sha256(safe_path(out/name)) != sha:
            raise ValueError('sortie altérée: '+name)
    plan = read_json(out/'plan.json')
    result = read_json(out/'result.json') if 'result.json' in manifest else None
    producer = (result['plan'] if result else plan)['provenance']
    if not producer['loaded_sources_sha256']: raise ValueError('sources producteur absentes')
    verify_sources(producer['loaded_sources_sha256'])
    verify_sources(producer.get('executed_sources_sha256',{}))
    if producer['versions'] != versions(): raise ValueError('versions producteur différentes')
    if result is not None and identity_plan(result['plan']) != identity_plan(plan):
        raise ValueError('plan producteur incohérent')
    observations = [read_json(out/name) for name in sorted(manifest) if name.startswith('observation_')]
    if result is not None and result['observations'] != observations:
        raise ValueError('observations incohérentes')
    if result is not None:
        if result['continuous_robustness_certified'] is not False:
            raise ValueError('garantie continue interdite')
        try:
            verify_semantics(out,plan,result,observations,manifest)
        except (KeyError,TypeError,IndexError) as exc:
            raise ValueError('cohérence sémantique: structure invalide') from exc
    response = dict(result or {},ok=receipt.get('ok') is True,artifacts_verified=True,semantic_verified=result is not None,
                    execution=receipt,observations=observations)
    if result is None:
        response.update(counterexample_found=counterexample(observations),sampled_hard_conforming=False,
                        execution_complete=False,request_fully_covered=False)
    if not receipt.get('ok'):
        response.update(execution_complete=False,request_fully_covered=False)
    response['continuous_robustness_certified'] = False
    return response


def summarize(observations, plan, complete):
    rows = [r for o in observations for r in o['criteria']]
    hard = [r for r in rows if r['role']=='hard']
    physical_failure = any(o['geometry_status']=='geometry_violated' for o in observations)
    failure = physical_failure or any(r['status']=='violated' for r in hard)
    all_hard = bool(hard) and all(r['status']=='satisfied' and r.get('unit_si') for r in hard)
    margins = {}
    for o in observations:
        for r in o['criteria']:
            if r.get('value_si') is None or r.get('margin') is None or not r.get('margin_unit'):
                continue
            key = (o['projection'],r['id'])
            if key not in margins or r['margin'] < margins[key]['margin']:
                margins[key] = dict(projection=o['projection'],criterion=r['id'],scenario=o['scenario'],
                                    margin=r['margin'],unit=r['margin_unit'],status=r['status'])
    expected = {(g['scenario'],g['projection'],c) for g in plan.get('geometry',[])
                for c in plan.get('expected_hard',{}).get(g['projection'],[])}
    observed = {(o['scenario'],o['projection'],r['id']) for o in observations
                for r in o['criteria'] if r['role']=='hard'}
    grid = {(g['scenario'],g['projection']) for g in plan.get('geometry',[])}
    present = [(o['scenario'],o['projection']) for o in observations]
    grid_present = (set(present) == grid and len(present) == len(grid)) if grid else bool(complete)
    sampled = bool(grid_present and all_hard and not physical_failure and (observed == expected if expected else complete))
    covered = not plan['uncovered'] and all(r['status'] in ('satisfied','violated') and r.get('unit_si') for r in rows)
    return dict(execution_complete=bool(complete),sampled_hard_conforming=sampled,
                request_fully_covered=bool(complete and grid_present and covered),continuous_robustness_certified=False,
                counterexample_found=failure, status='counterexample' if failure else 'sampled_conforming' if sampled
                else 'unresolved', margins=list(margins.values()))


def counterexample(observations):
    return any(o.get('geometry_status') == 'geometry_violated' or
               any(r.get('role') == 'hard' and r.get('status') == 'violated' for r in o['criteria'])
               for o in observations)
