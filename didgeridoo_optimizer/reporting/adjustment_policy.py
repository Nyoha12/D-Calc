"""Relative integrity and semantic readback, without the adjustment producer/TMM.

Acoustic scalars are producer evidence, not recertified here. A wholly rewritten
bundle has no external authentication. Native immutable IO and terminal protocol.
"""
from __future__ import annotations

import copy
import csv
import io
import inspect
import json
from pathlib import Path
import sys
from types import FunctionType

from . import tolerance_audit as native
from .tolerance_audit import (fingerprint, versions, verify_sources, new_destination,
    safe_path, atomic_bytes, write_json, read_json, file_sha256, unavailable_rows,
    _limit, profile_name, write_geometry, write_unavailable_scenario, write_profile,
    write_observation, write_manifest, close_prepared, commit_terminal, read_execution,
    _require, _verify_row)
from ..optimization.adjustment_policy import (SCHEMA, job_contract, bind_policy, groups,
    commands, apply_commands, candidate_status)

ROOT=Path(__file__).resolve().parents[2]


def source_files():
    result=native.source_files()
    # Cross-check the code objects actually loaded, not just a module's __file__.
    for name,module in tuple(sys.modules.items()):
        if not name.startswith(('didgeridoo_optimizer','tools.')) or not getattr(module,'__file__',None):
            continue
        declared=Path(module.__file__).resolve()
        for value in tuple(vars(module).values()):
            if getattr(value,'__module__',None)!=name: continue
            candidates=[value]
            if isinstance(value,type): candidates=list(vars(value).values())
            for item in candidates:
                if isinstance(item,(staticmethod,classmethod)): item=item.__func__
                if isinstance(item,property): item=item.fget
                item=inspect.unwrap(item)
                if getattr(item,'__module__',None)!=name: continue
                if isinstance(item,FunctionType) and item.__code__.co_filename.endswith('.py'):
                    actual=Path(item.__code__.co_filename).resolve()
                    if actual!=declared: raise ValueError('provenance code chargé différente de __file__: '+name)
    main=getattr(sys.modules.get('__main__'),'__file__',None)
    if main and Path(main).resolve()==ROOT/'tools/adjustment_policy.py':
        result['tools/adjustment_policy.py']=file_sha256(main)
    return result


def provenance(executed=()):
    result=native.provenance(executed);result['loaded_sources_sha256']=source_files()
    return result


def identity_plan(plan):
    result=copy.deepcopy(native.identity_plan(plan))
    if 'audit_plan' in result: result['audit_plan']=native.identity_plan(result['audit_plan'])
    return result


def summarize(observations,plan,records,complete):
    result=native.summarize(observations,plan,complete)
    expected={(s['id'],g['id']) for s in plan['parsed']['scenarios'] for g in plan['groups']}
    seen=[(g['scenario'],g['group']) for g in records]
    all_witness=(set(seen)==expected and len(seen)==len(expected) and all(g['verified'] for g in records))
    sampled=result.pop('sampled_hard_conforming') and all_witness
    fixed=plan['policy']['kind']=='fixed'
    result.update(sampled_policy_conforming=bool(sampled),
        counterexample_found=result['counterexample_found'] if fixed else False,
        witness_found=bool(all_witness),infeasibility_proven=False,global_optimum_proven=False)
    result['status']=('counterexample' if result['counterexample_found'] else
                      'sampled_policy_conforming' if sampled else 'search_not_resolved')
    return result


def tables(result, command_rows):
    observations=result['observations']; plan=result['plan']
    criteria=[dict(r,scenario=o['scenario'],projection=o['projection']) for o in observations for r in o['criteria']]
    scenarios=[dict(o,hard_violations=sum(r['role']=='hard' and r['status']=='violated' for r in o['criteria'])) for o in observations]
    deltas=[dict(scenario=s['id'],uncertainty=u['id'],field=f,coefficient=s['coefficients'][u['id']],
                 delta_si=u['delta_si'],unit_si='m',origin=u['origin'])
            for s in plan['parsed']['scenarios'] for u in plan['parsed']['uncertainties'] for f in u['fields']]
    return {
        'commands.csv':(command_rows,['scenario','configuration','block','nominal_q_si','q_si','delta_si','rational_sum','serialization_error','unit']),
        'criteria.csv':(criteria,['scenario','projection','id','role','observable','status','value_si','unit_si','target_si','lower_si','upper_si','tolerance_si','tolerance_dimension','margin','margin_unit','convergence_estimate','reason']),
        'scenarios.csv':(scenarios,['scenario','projection','configuration','nominal','geometry_status','hard_violations','reason']),
        'deltas.csv':(deltas,['scenario','uncertainty','field','coefficient','delta_si','unit_si','origin']),
        'margins.csv':(result['margins'],['projection','criterion','scenario','margin','unit','status'])}


def table_bytes(rows,fields):
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
    writer.writeheader();writer.writerows(rows)
    return stream.getvalue().encode()


def summary_bytes(result):
    return ('Politique de commandes — instrument et fabrication fixés\n'
        f"Politique : {result['plan']['policy']['kind']}\n"
        f"Information : {result['plan']['policy']['information']}\n"
        f"État : {result['status']}\n"
        f"Exécution complète : {result['execution_complete']}\n"
        f"Scénarios conformes à la politique : {result['sampled_policy_conforming']}\n"
        f"Demande entièrement couverte : {result['request_fully_covered']}\n"
        f"Contre-exemple aux commandes fixes : {result['counterexample_found']}\n"
        'Recherche locale bornée; aucun optimum, impossibilité globale ou continuum certifié.\n'
        'Fabrication connue pour les politiques adaptatives; ni identification ni contrôleur de joueur.\n'
        'Les essais violés ne réfutent pas un témoin ultérieur; les observations indisponibles restent explicites.\n'
        'Relecture relative des scalaires conservés, sans recertification acoustique ni authentification externe.\n').encode()


def export_result(output,result):
    out=Path(output);plan=result['plan'];limit=_limit(plan)
    command_rows=[dict(r,scenario=s['id']) for s in plan['parsed']['scenarios'] for r in read_json(out/f'commands_{s["id"]}.json')]
    for name,(rows,fields) in tables(result,command_rows).items(): atomic_bytes(out/name,table_bytes(rows,fields),budget_bytes=limit)
    # Original native demands are reloadable files, with no role/scope editing.
    for p in projections(plan): write_json(out/f'request_{p["id"]}.json',p['request'],budget_bytes=limit)
    write_json(out/'result.json',result,budget_bytes=limit)
    atomic_bytes(out/'summary.txt',summary_bytes(result),budget_bytes=limit)
    write_manifest(out,limit)


def projections(plan):
    original=plan['audit_plan']; request=original['native_request']
    return ([dict(id='fixed',configuration='nominal',request=request)] if original['job']['input']['kind']=='fixed'
            else request['projections'])


def _context(plan):
    from ..materials.database import MaterialDatabase
    from ..materials.variants import MaterialVariantGenerator
    from ..pipeline.design_input import validate_analysis
    snapshot=plan['material_snapshot']; config=plan['audit_plan']['original_config']
    rules=snapshot['variant_rules']
    db=MaterialDatabase(snapshot['materials'],variant_generator=MaterialVariantGenerator(rules) if rules is not None else None)
    return dict(config=config,material_db=db,effective_parameters=validate_analysis(config))


def verify_semantics(out,plan,result,observations,manifest):
    # Native geometry/contract validation only; no adjustment producer import.
    from ..pipeline.tolerance_audit import geometries, fixed_contract
    from ..pipeline.design_input import validate_design
    from ..pipeline.design_pitch import models
    from ..geometry.assemblies import Assembly
    from ..geometry.tolerance_scenarios import parse_scenarios, perturb, ScenarioUnavailable
    from ..optimization.assembly_contract import AssemblyContract
    original=plan['audit_plan'];kind=original['job']['input']['kind'];nominal=copy.deepcopy(original['nominal'])
    if kind=='assembly':
        order=next(g['piece_order'] for g in original['geometry'] if 'piece_order' in g)
        _require(len(order)==len(nominal['pieces']) and set(order)==set(nominal['pieces']),'ordre physique original')
        nominal['pieces']={k:nominal['pieces'][k] for k in order}
    _require(result['schema_version']==plan['schema_version']==plan['job']['schema_version']==SCHEMA,'schéma')
    policy,limits=job_contract(plan['job']);ps=projections(plan);context=_context(plan)
    if kind=='assembly':
        contract=AssemblyContract(original['native_request'],Assembly(nominal,context['material_db'],context['config']),context)
        native_ps=contract.projections
    else:
        context['design']=validate_design(nominal,context['material_db'],context['config'])
        contract=fixed_contract(original['native_request'],context)
        native_ps=[dict(ps[0],template=contract)]
    policy=bind_policy(policy,kind,nominal,original['native_request'],ps)
    _require(policy==plan['policy'] and limits==plan['budgets'],'politique/budgets du JOB')
    parsed=parse_scenarios(original['job'],kind,nominal,contract)
    _require(parsed==plan['parsed']==original['parsed'],'fabrications originales')
    units=groups(policy,ps);_require(units==plan['groups'],'groupes physiques')
    _require(plan['geometry']==original['geometry'],'grille originale')
    expected={(s['id'],p['id']) for s in parsed['scenarios'] for p in ps}
    _require({(g['scenario'],g['projection']) for g in plan['geometry']}==expected and len(plan['geometry'])==len(expected),'grille complète')
    hard={p['id']:[c['id'] for c in p['request']['criteria'] if c['role']=='hard'] for p in ps}
    _require(plan['expected_hard']==original['expected_hard']==hard,'hard originaux')
    uncovered=[]
    if original['job']['coverage']['kind']=='continuous': uncovered.append('JOB: continuum dimensionnel non couvert')
    if kind=='assembly' and contract.coverage['kind']!='discrete': uncovered.append('ASSEMBLY_REQUEST: continuum de configurations non couvert')
    for p in native_ps:
        for c in p['template'].criteria:
            if c['unsupported_reason']: uncovered.append(p['id']+':'+c['id']+': '+c['unsupported_reason'])
    _require(uncovered==plan['uncovered']==original['uncovered'],'portées originales')
    effective_models=[models(context,p['template'].options)[3] for p in native_ps]
    _require(original['effective_models']==effective_models,'modèles CONFIG/REQUEST')
    scenarios={s['id']:s for s in parsed['scenarios']}; pmap={p['id']:p for p in ps};umap={g['id']:g for g in units}
    physical={}; causes={}
    for sid,s in scenarios.items():
        try: physical[sid]=perturb(nominal,parsed,s,kind)
        except ScenarioUnavailable as exc: physical[sid]=None;causes[sid]=str(exc)
    def verify_observation(o,p,raw,*,profile=None):
        s=scenarios[o['scenario']]
        _require(o['configuration']==p['configuration'] and o['effective_request']==p['request'],'configuration/demande')
        _require(o['coefficients']==s['coefficients'] and o['nominal'] is s['nominal'],'scénario')
        _require(o['policy']==policy['kind'] and o['information']==policy['information'],'information/politique unité')
        # No TMM: verify the relationship to the retained native refinement
        # evidence, never claim that its acoustic scalars were recomputed.
        acoustic=[r for r in o['criteria'] if r['observable'] in ('resonance_frequency','resonance_ratio') and r.get('value_si') is not None]
        if acoustic:
            payload=o['acoustics'];levels=payload.get('refinement_levels',[])
            _require(payload.get('verified') is True and len(levels)==2,'témoin acoustique exige verify=True et deux raffinements')
            spectrum=p['request']['spectrum']
            _require([l['h_cm'] for l in levels]==[spectrum['h_cm'],spectrum['h_cm']/2] and
                     [l['subdivision_uniform'] for l in levels]==[False,True],'raffinements de la demande originale')
            for row in acoustic:
                criterion=next(c for c in p['request']['criteria'] if c['id']==row['id'])
                refs=[criterion['mode']] if row['observable']=='resonance_frequency' else [criterion['numerator'],criterion['denominator']]
                modes=[payload['selected_modes'][k] for k in refs]
                _require(all(m['status']=='resolved' for m in modes),'modes des scalaires conservés')
                fs=[m['peak']['frequency_hz'] for m in modes]
                _require(row['value_si']==(fs[0] if len(fs)==1 else fs[0]/fs[1]),'observable/modes retenus')
                _require(row.get('convergence_estimate') is not None,'convergence du témoin acoustique')
        _require([r['id'] for r in o['criteria']]==[c['id'] for c in p['request']['criteria']],'critères absents/dupliqués')
        if 'effective_models' in o['acoustics']:
            _require(o['acoustics']['effective_models']==effective_models[ps.index(p)],'modèle effectif unité')
        for row,criterion in zip(o['criteria'],p['request']['criteria']):
            _verify_row(row,criterion,p['request'],o['geometry_status'],profile)
    trial_rows={};expected_spectra=set()
    for entry in result['trials']:
        tid=entry['trial'];name=f'trial_{tid:04d}.json'
        _require(type(tid) is int and tid>0 and tid not in trial_rows and name in manifest,'essai absent/dupliqué')
        row=read_json(out/name);sid=entry['scenario'];group=umap[entry['group']]
        _require({k:row[k] for k in ('trial','scenario','group')}==entry,'attribution essai')
        try: expected_commands=commands(policy,group['configurations'],row['variables_si']);refusal=None
        except ValueError as exc: expected_commands=[];refusal=str(exc)
        _require(row['commands']==expected_commands and row['refusal']==refusal,'commandes/bornes/arrondis')
        raw=apply_commands(physical[sid],expected_commands) if physical[sid] is not None else None
        trial_ps=[p for p in ps if p['id'] in group['projections']]
        _require([o['projection'] for o in row['observations']]==group['projections'],'toutes projections du groupe')
        generated=(list(geometries(kind,raw,context,trial_ps)) if raw is not None and not refusal else
                   [(p,None,{},dict(status='unresolved',reason=refusal or causes[sid])) for p in trial_ps])
        for o,(p,design,metadata,issue) in zip(row['observations'],generated):
            _require(o['scenario']==sid and o['trial']==tid,'attribution observation')
            profile=design.as_dict() if design is not None else None
            _require(o['profile']==profile and o['geometry']==metadata,'profil/métadonnées essai')
            _require(o['geometry_status']==(issue['status'] if issue else 'valid'),'géométrie essai')
            verify_observation(o,p,raw,profile=profile)
            traces=o['acoustics'].get('completed_spectra',[])
            for level,trace in enumerate(traces,1):
                name=f'spectrum_{tid:04d}_{p["id"]}_{level}.json';expected_spectra.add(name)
                _require(name in manifest and read_json(out/name)==dict(trace,scenario=sid,projection=p['id'],trial=tid),'trace spectrale attribuée absente/altérée')
            if any(r['observable'] in ('resonance_frequency','resonance_ratio') and r.get('value_si') is not None for r in o['criteria']):
                _require(len(traces)==3,'trois grilles vérifiées conservées')
                _require([t['step_hz'] for t in traces]==[p['request']['spectrum']['step_hz'],p['request']['spectrum']['final_step_hz'],p['request']['spectrum']['final_step_hz']],'grilles de fréquence originales')
        _require(row['criteria']==[dict(c,id=o['projection']+':'+c['id']) for o in row['observations'] for c in o['criteria']],'hard de recherche conservés')
        status=candidate_status(row['observations'])
        _require(row['status']==status and row['verified'] is (status=='witness_found') and row['search_feasible'] is row['verified'],'témoin essai')
        trial_rows[tid]=row
    _require(set(trial_rows)==set(range(1,len(trial_rows)+1)),'séquence essais')
    _require({n for n in manifest if n.startswith('spectrum_')}==expected_spectra,'traces spectrales complètes')
    _require({n for n in manifest if n.startswith('trial_')}=={f'trial_{n:04d}.json' for n in trial_rows},'manifest essais')
    expected_groups={(s,g) for s in scenarios for g in umap};seen=set();selected={};all_commands={s:[] for s in scenarios}
    for record in result['groups']:
        sid,gid=record['scenario'],record['group'];pair=(sid,gid)
        _require(pair in expected_groups and pair not in seen,'groupe absent/dupliqué');seen.add(pair)
        tids=[n for n,r in trial_rows.items() if (r['scenario'],r['group'])==pair]
        _require(record['trials']==tids,'historique groupe complet')
        tid=record['selected_trial'];retained=trial_rows.get(tid)
        _require(tid in tids if tid is not None else not tids,'sélection attribution')
        witness=any(trial_rows[n]['verified'] for n in tids)
        _require(record['verified'] is witness and (not witness or retained['verified']),'témoin trouvé préservé')
        status=('witness_found' if witness else 'candidate_violated' if policy['kind']=='fixed' and retained and retained['status']=='candidate_violated'
                else 'unsupported' if retained and retained['status']=='unsupported' else 'out_of_domain' if retained and retained['status']=='out_of_domain' else 'search_not_resolved')
        _require(record['status']==status and record['infeasibility_proven'] is False and record['global_optimum_proven'] is False,'verdict groupe')
        if retained:
            all_commands[sid].extend(retained['commands'])
            for o in retained['observations']: selected[(sid,o['projection'])]=o
    _require(seen==expected_groups,'tous groupes présents')
    final_physical={};command_table=[]
    for sid in scenarios:
        name=f'commands_{sid}.json';_require(name in manifest and read_json(out/name)==all_commands[sid],'commandes finales')
        command_table.extend(dict(r,scenario=sid) for r in all_commands[sid])
        if physical[sid] is None:
            name=f'unavailable_{sid}.json';_require(name in manifest,'scénario indisponible absent')
            missing=read_json(out/name)
            _require(missing['scenario']==scenarios[sid] and missing['reason']==causes[sid] and missing['physical_geometry_available'] is False,'scénario indisponible')
            final_physical[sid]=None
        else:
            name=f'{kind}_{sid}.json';_require(name in manifest,'instrument final absent')
            raw=read_json(out/name);expected_raw=apply_commands(physical[sid],all_commands[sid])
            _require(raw==expected_raw,'fabrication/pièces/commandes finales gelées')
            if kind=='assembly': _require(list(raw['pieces'])==list(nominal['pieces']),'ordre pièces')
            final_physical[sid]=raw
    present=set()
    for o in observations:
        pair=(o['scenario'],o['projection']);_require(pair in expected and pair not in present,'observation absente/dupliquée');present.add(pair)
        sid,pid=pair;p=pmap[pid];raw=final_physical[sid]
        generated=(list(geometries(kind,raw,context,[p]))[0] if raw is not None else (p,None,{},dict(status='unresolved')))
        _,design,metadata,issue=generated
        _require(o['geometry_status']==(issue['status'] if issue else 'valid') and o['geometry']==metadata,'raccord physique final')
        profile=None
        if design is not None:
            name=profile_name(sid,pid);_require(name in manifest,'profil final absent');profile=read_json(out/name)
            _require(profile==design.as_dict(),'profil final régénéré')
        old=selected.get(pair)
        if old:
            for key in ('criteria','acoustics','trial','effective_request','reason'):
                _require(o[key]==old[key],'sélection finale cohérente: '+key)
            if profile is not None and old['profile'] is not None:
                _require(profile['segments']==old['profile']['segments'],'même profil vérifié')
        else:
            _require(o['trial'] is None and all(r['value_si'] is None for r in o['criteria']),'observation sans essai')
        verify_observation(o,p,raw,profile=profile)
    _require(present==expected,'sortie tronquée, grille manquante')
    for p in ps:
        name=f'request_{p["id"]}.json';_require(name in manifest and read_json(out/name)==p['request'],'demande rechargeable')
    _require(type(result['execution_complete']) is bool,'complétude booléenne')
    for key,value in summarize(observations,plan,result['groups'],result['execution_complete']).items():
        _require(result.get(key)==value and (type(result.get(key)) is bool if type(value) is bool else True),'agrégat '+key)
    _require(result['counters']['evaluations']==len(trial_rows),'compteur essais')
    for name,(rows,fields) in tables(result,command_table).items():
        _require(name in manifest and (out/name).read_bytes()==table_bytes(rows,fields),'table '+name)
    _require('summary.txt' in manifest and (out/'summary.txt').read_bytes()==summary_bytes(result),'synthèse française')


def read_result(output):
    """Fresh reader: relative integrity, complete grid and scalar/physical semantics."""
    out=safe_path(output);receipt=read_execution(out)
    if not (out/'manifest.json').exists():
        return dict(ok=False,execution_complete=False,semantic_verified=False,artifacts_verified=False,
            execution=receipt,observations=[read_json(p) for p in sorted(out.glob('observation_*.json'))],
            trials=[read_json(p) for p in sorted(out.glob('trial_*.json'))])
    manifest=read_json(out/'manifest.json')
    if receipt.get('manifest_sha256'):
        _require(receipt['manifest_sha256']==file_sha256(out/'manifest.json'),'manifest altéré')
    excluded={'manifest.json','execution.json','execution.closed.json','execution.completed.json','execution.cancelled.json'}
    actual={p.name for p in out.iterdir() if not p.name.startswith('.') and p.name not in excluded}
    _require(actual==set(manifest),'fichier omis du manifeste')
    for name,sha in manifest.items():
        _require(Path(name).name==name and file_sha256(safe_path(out/name))==sha,'sortie altérée: '+name)
    _require('plan.json' in manifest,'plan absent');plan=read_json(out/'plan.json')
    from .regime_reference import directory_size
    _,limits=job_contract(plan['job'])
    _require(directory_size(out)<=limits['output_mib']*1024**2,'budget lecture du bundle')
    observations=[read_json(out/n) for n in sorted(manifest) if n.startswith('observation_')]
    if 'result.json' not in manifest:
        _require(receipt.get('ok') is not True,'succès sans résultat')
        return dict(ok=False,execution_complete=False,semantic_verified=False,artifacts_verified=True,
                    execution=receipt,observations=observations,trials=[read_json(out/n) for n in sorted(manifest) if n.startswith('trial_')])
    result=read_json(out/'result.json');producer=result['plan']['provenance']
    _require(bool(producer['loaded_sources_sha256']),'sources absentes')
    verify_sources(producer['loaded_sources_sha256']);verify_sources(producer.get('executed_sources_sha256',{}))
    _require(producer['versions']==versions(),'versions producteur')
    _require(identity_plan(result['plan'])==identity_plan(plan),'plan producteur')
    _require(result['observations']==observations,'observations résultat')
    try: verify_semantics(out,plan,result,observations,manifest)
    except (KeyError,TypeError,IndexError) as exc: raise ValueError('cohérence sémantique: structure invalide') from exc
    if receipt.get('ok'):
        for k in ('execution_complete','sampled_policy_conforming','request_fully_covered','counterexample_found','status'):
            _require(receipt.get(k)==result[k],'reçu/agrégat '+k)
    response=dict(result,ok=receipt.get('ok') is True,artifacts_verified=True,semantic_verified=True,execution=receipt)
    if not response['ok']: response.update(execution_complete=False,request_fully_covered=False)
    return response
