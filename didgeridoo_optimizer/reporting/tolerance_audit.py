"""Immutable audit bundles and byte-only fresh-process readback (no acoustics)."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
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


def read_result(output):
    """Verify producer bytes directly, without importing/replaying producer modules.

    ok means a confirmed command, not conformity. Partial observations remain
    accessible with ok=false after failed or unconfirmed execution.
    """
    out = safe_path(output); receipt = read_execution(out)
    manifest_path = out/'manifest.json'
    if not manifest_path.exists():
        # No completed manifest: inspect individually committed observations, never
        # promote them to an authenticated complete result.
        observations = [read_json(p) for p in sorted(out.glob('observation_*.json'))]
        return dict(ok=False,artifacts_verified=False,execution=receipt,
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
        # Geometries/profiles have separate files and must all be manifest members.
        for o in observations:
            if (f"{plan['job']['input']['kind']}_{o['scenario']}.json" not in manifest and
                    not (o['geometry_status']=='unresolved' and f"unavailable_{o['scenario']}.json" in manifest)):
                raise ValueError('géométrie physique manquante')
            if o['geometry_status']=='valid' and profile_name(o['scenario'],o['projection']) not in manifest:
                raise ValueError('profil manquant')
    response = dict(result or {},ok=receipt.get('ok') is True,artifacts_verified=True,
                    execution=receipt,observations=observations)
    if result is None:
        response.update(counterexample_found=counterexample(observations),sampled_hard_conforming=False)
    if not receipt.get('ok'):
        response.update(execution_complete=False,request_fully_covered=False)
    response['continuous_robustness_certified'] = False
    return response


def counterexample(observations):
    return any(o.get('geometry_status') == 'geometry_violated' or
               any(r.get('role') == 'hard' and r.get('status') == 'violated' for r in o['criteria'])
               for o in observations)
