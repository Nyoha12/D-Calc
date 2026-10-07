"""Strict paired local-onset plans and sequential bounded execution."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import itertools
import json
import math
import os
import resource
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np

from . import time_domain_reference as td
from .design_pitch import execution_ready, child_limits, destination, models
from ..geometry import GeometryDiscretizer
from ..acoustics.transfer_matrix import input_impedance
from ..nonlinear.lips import DimensionedLipParameters
from ..nonlinear.passive_resonator import PassiveResonator, real, integer, digest, UNITS, _pairs, _constant
from ..nonlinear import paired_onset as core
from ..optimization.design_contract import obj, identifier, quantity, target_quantity
from ..reporting import paired_onset as report

ROOT=Path(__file__).resolve().parents[2]
SCHEMA='dcalc.paired_onset.plan.v1'
RECIPE={'sample_rate_hz','h_cm','loss_model','radiation_model','air_reference','R0','dc_origin',
        'basis_completion','fit_min_hz','fit_max_hz','fit_points','guard_max_hz','guard_points','audit_points'}
BUDGETS={'refinements','evaluations_per_unit','total_evaluations','child_seconds','orchestrator_seconds',
         'memory_mib','blas_threads','output_mib'}
OBSERVABLES={'onset_pressure':('pressure','Pa'), 'onset_frequency':('frequency','Hz'),
             'pressure_difference':('pressure','Pa'), 'frequency_difference':('frequency','Hz'),
             'pressure_ratio':('scalar','1'), 'frequency_ratio':('scalar','1')}
UNSUPPORTED={'played_frequency','toot_accessibility','regime','transitions'}


def exact(value, keys, where):
    return obj(value, keys, where, keys)


def bounds(value, where, *, positive=True):
    if type(value) is not list or len(value)!=2: raise ValueError(where+': two bounds required')
    lo,hi=[real(v,where) for v in value]
    if lo>=hi or (positive and lo<=0): raise ValueError(where+': ordered bounds required')
    return [lo,hi]


def unique(rows, where, lo, hi):
    if type(rows) is not list or not lo<=len(rows)<=hi: raise ValueError(where+': count budget')
    ids=[]
    for row in rows:
        if type(row) is not dict: raise ValueError(where+': mapping required')
        ids.append(identifier(row.get('id'),where))
    if len(set(ids))!=len(ids): raise ValueError(where+': duplicate id')
    return set(ids)


def validate_request(value):
    """Pure validation; quotas and numeric types before any scientific allocation."""
    keys={'schema','cases','reference_lips','scenarios','rho_kg_m3','source','port_model',
          'pressure_grid','windows','criteria','budgets','tmm'}
    exact(value,keys,'plan')
    if value['schema']!=SCHEMA: raise ValueError('Unsupported plan schema')
    b=exact(value['budgets'],BUDGETS,'budgets')
    for key,lo,hi in [('refinements',0,32),('evaluations_per_unit',2,1200),('total_evaluations',2,76800),
                      ('memory_mib',768,768),('blas_threads',1,1),('output_mib',1,100)]:
        integer(b[key],key,lo,hi)
    for key,maximum in [('child_seconds',180),('orchestrator_seconds',600)]:
        if not 0<real(b[key],key)<=maximum: raise ValueError(key+': time budget')
    case_ids=unique(value['cases'],'cases',1,4)
    scenario_ids=unique(value['scenarios'],'scenarios',1,16)
    window_ids=unique(value['windows'],'windows',1,16)
    unique(value['criteria'],'criteria',0,128)
    if value['source']!='ideal' or value['port_model']!='conjugate':
        raise ValueError('unsupported: only ideal source and conjugate ports')
    required=set(DimensionedLipParameters().as_dict())
    exact(value['reference_lips'],required,'reference_lips')
    rho=real(value['rho_kg_m3'],'rho',minimum=1e-9)
    params=DimensionedLipParameters(**value['reference_lips']); core.validate_domain(params,rho)
    for scenario in value['scenarios']:
        exact(scenario,{'id','changes'},'scenario')
        obj(scenario['changes'],required-{'pressure_force_sign','mouth_pressure_kpa'},'changes')
        active=replace(params,**scenario['changes']); core.validate_domain(active,rho)
        core.pressures(value['pressure_grid'],active)
    for case in value['cases']:
        exact(case,{'id','config','design','model_in','recipe'},'case')
        for key in ('config','design','model_in'):
            if type(case[key]) is not str or not case[key].strip(): raise ValueError('Explicit paths required')
        exact(case['recipe'],RECIPE,'recipe')
        integer(case['recipe']['sample_rate_hz'],'fs',1000,12000)
    for window in value['windows']:
        exact(window,{'id','pressure_pa','frequency_hz'},'window')
        bounds(window['pressure_pa'],'pressure window');bounds(window['frequency_hz'],'frequency window')
        if window['frequency_hz'][1]>=min(c['recipe']['sample_rate_hz'] for c in value['cases'])/2:
            raise ValueError('Frequency window must be below each Nyquist')
    for c in value['criteria']:
        obj(c,{'id','role','case','pair','scenario','window','observable','target','tolerance','priority'},
            'criterion',{'id','role','scenario','window','observable'})
        if c['role'] not in ('hard','observe') or c['scenario'] not in scenario_ids or c['window'] not in window_ids:
            raise ValueError('Criterion role/scenario/window invalid')
        if ('case' in c)==('pair' in c): raise ValueError('Criterion requires exactly case or pair')
        if 'case' in c and c['case'] not in case_ids: raise ValueError('Unknown criterion case')
        if 'pair' in c and (type(c['pair']) is not list or len(c['pair'])!=2 or any(x not in case_ids for x in c['pair'])):
            raise ValueError('Unknown criterion pair')
        if 'priority' in c: integer(c['priority'],'priority',0,1000)
        observable=c['observable']
        if observable not in OBSERVABLES and observable not in UNSUPPORTED: raise ValueError('Unknown observable')
        if observable in OBSERVABLES:
            paired=observable.endswith(('_difference','_ratio'))
            if paired!=('pair' in c): raise ValueError('Observable requires compatible case/pair selector')
            if 'target' not in c or 'tolerance' not in c: raise ValueError('Explicit target and tolerance required')
            dimension=OBSERVABLES[observable][0]
            target=target_quantity(c['target'],dimension)
            tol,tdim=quantity(c['tolerance'])
            if not math.isfinite(target) or tol<0 or (tdim!=dimension and not (dimension=='frequency' and tdim=='cent')):
                raise ValueError('Target/tolerance dimensions invalid')
            if tdim=='cent' and (target<=0 or observable=='frequency_difference'):
                raise ValueError('Cents require a positive absolute frequency target')
        else:
            # Retain explicitly declared unsupported targets without substituting onset.
            if 'target' in c:
                target_quantity(c['target'],'frequency' if observable=='played_frequency' else 'scalar')
            if 'tolerance' in c: quantity(c['tolerance'])
    tmm=value['tmm']
    if tmm is not None:
        exact(tmm,{'h_cm','loss_model','radiation_model','air_reference','pressure_pa','frequency_hz',
                   'max_iterations','max_evaluations','max_ka'},'tmm')
        if not 0<real(tmm['h_cm'],'TMM mesh')<=2: raise ValueError('TMM mesh')
        bounds(tmm['pressure_pa'],'TMM pressure');bounds(tmm['frequency_hz'],'TMM frequency')
        integer(tmm['max_iterations'],'TMM iterations',1,16)
        integer(tmm['max_evaluations'],'TMM evaluations',1,256)
        if not 0<real(tmm['max_ka'],'max_ka')<=2: raise ValueError('Declared ka <=2 required')
        for scenario in value['scenarios']:
            if tmm['pressure_pa'][1]>=core.pressure_limit(replace(params,**scenario['changes'])):
                raise ValueError('TMM pressure outside protocol')
        if tmm['frequency_hz'][1]>=min(c['recipe']['sample_rate_hz'] for c in value['cases'])/2:
            raise ValueError('TMM frequency outside Nyquist')
    if b['total_evaluations']<len(value['cases'])*len(value['scenarios'])*len(next(iter(value['pressure_grid'].values()))):
        raise ValueError('Total evaluation budget cannot cover the declared grid')
    return value


@dataclass(frozen=True)
class Plan:
    document: str

    def as_dict(self):
        value=json.loads(self.document,object_pairs_hook=_pairs, parse_constant=_constant)
        validate_request(value['request'])
        if value['context_sha256']!=digest({k:v for k,v in value.items() if k!='context_sha256'}):
            raise ValueError('Immutable plan context mismatch')
        return value


def _model_metadata(path,base,context,recipe):
    """Validate historical data WITHOUT constructing a model (no np.roots in dry-run)."""
    if report.safe_path(path).stat().st_size>2*1024**2: raise ValueError('Model size budget 2 MiB')
    raw,source=report.read_json_source(path)
    exact(raw,{'parameters','sha256'},'saved model')
    mp=raw['parameters']
    if raw['sha256']!=digest(mp): raise ValueError('Model content fingerprint mismatch')
    exact(mp,{'schema','units','pressure_port','sample_rate_hz','domain','R0','dc_origin',
              'a','gamma','omega','provenance','quality'},'model parameters')
    if mp['schema']!='dcalc.passive_resonator.v1' or mp['units']!=UNITS or mp['pressure_port']!='midpoint':
        raise ValueError('Saved model schema/units/port')
    fs=recipe['sample_rate_hz']
    integer(mp['sample_rate_hz'],'saved fs',1000,12000)
    if mp['domain']!='discrete_prewarped' or mp['sample_rate_hz']!=fs:
        raise ValueError('Saved native discrete_prewarped model and exact fs required')
    def vectors(data,maximum):
        if any(type(data.get(k)) is not list or not 1<=len(data[k])<=maximum for k in ('a','gamma','omega')):
            raise ValueError('Modal inventory budget')
        if len({len(data[k]) for k in ('a','gamma','omega')})!=1: raise ValueError('Modal shape mismatch')
        for a,g,w in zip(data['a'],data['gamma'],data['omega']):
            if real(a,'a')<0 or real(g,'gamma')<=0 or real(w,'omega')<=0:
                raise ValueError('Nonpassive modal data')
        if len(set(zip(data['gamma'],data['omega'])))!=len(data['a']): raise ValueError('Duplicate modal poles')
    vectors(mp,192);real(mp['R0'],'R0',minimum=0.)
    q=mp['quality'];saved=mp['provenance']
    if type(q) is not dict or type(saved) is not dict: raise ValueError('Historical metadata required')
    inv=exact(q.get('candidate_inventory'),{'a','gamma','omega'},'historical inventory');vectors(inv,256)
    active=[i for i,a in enumerate(inv['a']) if a>0]
    if any([inv[k][i] for i in active]!=mp[k] for k in inv): raise ValueError('Saved coefficients differ from historical inventory')
    frequencies=q.get('fit_frequency_hz')
    if (type(frequencies) is not list or len(frequencies)!=recipe['fit_points']
            or frequencies[0]!=recipe['fit_min_hz'] or frequencies[-1]!=recipe['fit_max_hz']
            or any(real(a,'fit frequency')>=real(b,'fit frequency') for a,b in zip(frequencies,frequencies[1:]))):
        raise ValueError('Historical fit grid mismatch')
    if not np.array_equal(np.asarray(frequencies),np.linspace(recipe['fit_min_hz'],recipe['fit_max_hz'],recipe['fit_points'])):
        raise ValueError('Historical full linear fit grid mismatch')
    certificate=q.get('certificate')
    if (type(certificate) is not dict or certificate.get('candidate_count')!=len(inv['a'])
            or certificate.get('converged') is not True or q.get('status')!='converged'):
        raise ValueError('Historical fit certificate absent or refused; no auxiliary retry')
    td._check_completion(SimpleNamespace(parameters=lambda:mp),base['basis_completion'])
    for key in ('fit_spectrum_sha256','guard_spectrum_sha256'):
        value=q.get(key)
        if type(value) is not str or len(value)!=64 or value!=saved.get(key):
            raise ValueError('Historical spectral identity mismatch')
    inputs=td._inputs(context)
    if set(inputs)!={'config','design','materials','variant_rules'} or digest(saved.get('inputs'))!=digest(inputs):
        raise ValueError('Saved model must identify the four actual input fingerprints')
    dc=(dict(kind='explicit',description=recipe['dc_origin']) if recipe['R0'] is not None else
        dict(kind='zk_local_1d',description='Sum 8*mu*L/(pi*a^4) on local circular rigid slices; linear acoustic DC limit, not finite mean-flow law'))
    if digest(dc)!=digest(mp['dc_origin']) or (recipe['R0'] is not None and recipe['R0']!=mp['R0']):
        raise ValueError('DC recipe mismatch')
    identity=digest(dict(inputs=inputs,effective=base['effective'],h_cm=recipe['h_cm'],fs=fs,
                         R0=mp['R0'],dc=dc,basis_completion=base['basis_completion']))
    if saved.get('context_identity')!=identity: raise ValueError('Acoustic recipe / saved context mismatch')
    return dict(model_sha256=source['sha256'],model_parameters_sha256=digest(mp),states=2+2*len(mp['a']),
        inputs=inputs,recipe_identity=identity,effective=base['effective'],
        fit_certificate_sha256=digest(q),fit_producer_sources=saved.get('sources_sha256'),
        fit_certificate_scope='Historical saved fit only; integrity is not a current refit or recertification',
        recipe_evidence=dict(verified=['four_inputs','fs','domain','h_cm','loss_model','radiation_model','air_reference','R0','dc_origin','basis_completion','fit_grid'],
            declared_not_recoverable_from_saved_model=['guard_max_hz','guard_points','audit_points']),
        fit_historical_status={k:q.get(k) for k in ('status','passivity','fidelity','mesh','physical')}),mp


def _case_context(case,output):
    for key in ('config','design','model_in'): report.safe_path(case[key])
    cfg=td.strict_input(case['config'])
    for key,default in [('database_file','materials_base_v1.yaml'),('variant_rules_file','wood_variant_rules_v1.yaml')]:
        path=Path(cfg.get('materials',{}).get(key,default))
        path=path if path.is_absolute() else Path(case['config']).parent/path
        report.safe_path(path,exists=key=='database_file')
    # Native read-only preflight with no model construction; minimal metadata adapter below.
    base,context=td.preflight(case['config'],case['design'],output,model_in=None,
                             v2_schedule='simultaneous',v2_port_model='conjugate',**case['recipe'])
    metadata,mp=_model_metadata(case['model_in'],base,context,case['recipe'])
    td._unchanged(context)
    return metadata,context,mp


def preflight(plan_path,output_dir):
    report.verify_sources(LOADED_SOURCES)
    request,source=report.read_plan_source(plan_path);validate_request(request)
    output=report.safe_path(output_dir,exists=False);destination(output)
    resolved=json.loads(json.dumps(request,allow_nan=False))
    metadata=[]
    for case in resolved['cases']:
        for key in ('config','design','model_in'):
            p=Path(case[key]);case[key]=str(report.safe_path(p if p.is_absolute() else Path(source['path']).parent/p))
        meta,context,_=_case_context(case,output)
        if resolved['tmm'] is not None:
            t=resolved['tmm'];segments=context['design'].segments
            count=sum(max(1 if s.is_uniform else 2,math.ceil(s.length_cm/t['h_cm'])) for s in segments)
            if count>4096: raise ValueError('TMM mesh budget before allocation')
            air,_,_,_=models(context,t)
            radius=segments[-1].d_out_cm/200
            if 2*math.pi*t['frequency_hz'][1]*radius/air.c>t['max_ka']:
                raise ValueError('TMM declared ka budget')
        metadata.append(dict(id=case['id'],**meta))
    max_states=max(c['states'] for c in metadata);b=resolved['budgets']
    # Roots are repeated in checkpoint and final unit evidence. Reject oversized plans early.
    if b['evaluations_per_unit']*max_states*80>3*1024**2:
        raise ValueError('Per-unit JSON storage budget; reduce evaluation quota')
    estimated=b['total_evaluations']*max_states*160+len(resolved['cases'])*len(resolved['scenarios'])*100000
    if estimated>b['output_mib']*1024**2-1024**2: raise ValueError('Declared evaluation/root storage budget')
    p=dict(schema='dcalc.paired_onset.execution_plan.v1',request=resolved,request_source=source,
           output=str(output),cases=metadata,producer=report.provenance(),estimated_bytes=estimated)
    p['context_sha256']=digest(p)
    if report.file_sha256(source['path'])!=source['sha256']: raise ValueError('Plan changed during preflight')
    return Plan(json.dumps(p,allow_nan=False,sort_keys=True))


def _check_plan(p):
    Plan(json.dumps(p,allow_nan=False)).as_dict()
    if report.file_sha256(p['request_source']['path'])!=p['request_source']['sha256']:
        raise ValueError('Plan bytes changed')
    original,_=report.read_plan_source(p['request_source']['path']);validate_request(original)
    for case in original['cases']:
        for key in ('config','design','model_in'):
            path=Path(case[key]);case[key]=str(report.safe_path(path if path.is_absolute() else Path(p['request_source']['path']).parent/path))
    if digest(original)!=digest(p['request']): raise ValueError('Plan interpreted values changed')
    report.verify_sources(p['producer']['loaded_sources_sha256'])
    if report.versions()!=p['producer']['versions']: raise ValueError('Worker versions mismatch')


def _tmm(model,params,rho,candidate,context,spec):
    air,loss,radiation,effective=models(context,spec)
    mesh=GeometryDiscretizer().discretize(context['design'],max_segment_cm=spec['h_cm'])
    radius=context['design'].segments[-1].d_out_cm/200
    def evaluate(f):
        f=real(f,'TMM real Hz')
        if not spec['frequency_hz'][0]<f<spec['frequency_hz'][1] or 2*math.pi*f*radius/air.c>spec['max_ka']:
            raise ValueError('TMM real-frequency/ka domain')
        return input_impedance([f],mesh,context['material_db'],air,exit_radius_m=radius,
                               loss_model=loss,radiation_model=radiation)[0]
    root=candidate['root']
    result=core.marginal_axis(model,params,rho,[root['pressure_pa'],root['discrete_frequency_hz']],evaluate,
        pressure_bounds=spec['pressure_pa'],frequency_bounds=spec['frequency_hz'],
        max_iterations=spec['max_iterations'],max_evaluations=spec['max_evaluations'])
    result.update(recipe=spec,effective=effective,physical_exit_radius_m=radius)
    return result


def worker(task):
    exact(task,{'plan','case','scenario','evaluation_allowance'},'worker task')
    p=task['plan'];_check_plan(p);r=p['request'];b=r['budgets']
    ci=integer(task['case'],'case index',0,len(r['cases'])-1)
    si=integer(task['scenario'],'scenario index',0,len(r['scenarios'])-1)
    allowance=integer(task['evaluation_allowance'],'allowance',2,min(b['evaluations_per_unit'],b['total_evaluations']))
    case=r['cases'][ci];scenario=r['scenarios'][si];out=Path(p['output']);unit=f'{ci:02d}-{si:02d}'
    # td.preflight needs a fresh destination; this path is never created.
    meta,context,_=_case_context(case,out/'unused-preflight-destination')
    if meta!= {k:v for k,v in p['cases'][ci].items() if k!='id'}: raise ValueError('Worker case context changed')
    model=PassiveResonator.load(case['model_in'],expected_sha256=meta['model_sha256'])  # once, reused
    params=DimensionedLipParameters(**{**r['reference_lips'],**scenario['changes']})
    limit=b['output_mib']*1024**2-1024**2;sequence=0;seen_grid=0;seen_candidates=0
    def acquired(result):
        nonlocal sequence,seen_grid,seen_candidates
        rows=result['grid'][seen_grid:];candidates=result['candidates'][seen_candidates:]
        if not rows and not candidates: return
        report.checkpoint(out,sequence,unit,dict(grid=rows,candidates=candidates),result['counters'],
                          p['context_sha256'],budget_bytes=limit)
        sequence+=1;seen_grid=len(result['grid']);seen_candidates=len(result['candidates'])
    result=core.analyze(model,params,r['rho_kg_m3'],r['pressure_grid'],refinements=b['refinements'],
        max_evaluations=allowance,seconds=max(.01,b['child_seconds']-3),after_unit=acquired,
        qualification='historical_saved_fit')
    result.update(case=case['id'],scenario=scenario['id'],context_sha256=p['context_sha256'],tmm=[])
    result['counters']['rational_evaluations']=result['counters']['evaluations']
    result['counters']['tmm_evaluations']=0
    if r['tmm'] is not None:
        for c in result['candidates']:
            if c['status']=='local_crossing_verified':
                remaining=allowance-result['counters']['evaluations']
                if remaining<1:
                    auxiliary=dict(status='not_resolved',reason='shared_evaluation_budget',evaluations=0)
                else:
                    spec=dict(r['tmm'],max_evaluations=min(remaining,r['tmm']['max_evaluations']))
                    auxiliary=_tmm(model,params,r['rho_kg_m3'],c,context,spec)
                result['tmm'].append(dict(candidate=c['id'],**auxiliary))
                result['counters']['tmm_evaluations']+=auxiliary['evaluations']
                result['counters']['evaluations']+=auxiliary['evaluations']
                report.checkpoint(out,sequence,unit,dict(tmm=[result['tmm'][-1]]),result['counters'],p['context_sha256'],budget_bytes=limit)
                sequence+=1
    model.verify_file_unchanged();td._unchanged(context);_check_plan(p)
    name=f'unit-{unit}.json';report.write_json(out/name,result,budget_bytes=limit)
    return dict(ok=result['status']=='complete',case=case['id'],scenario=scenario['id'],file=name,
                status=result['status'],counters=result['counters'])


def _matched(units,case,scenario,window):
    unit=units.get((case,scenario))
    if unit is None: return dict(status='unavailable',candidate=None,reason='unit_unavailable')
    return core.match_candidate(unit,window)


def paired_value(units,a,b,scenario,window,compatible=True):
    ma=_matched(units,a,scenario,window);mb=_matched(units,b,scenario,window)
    row=dict(case_a=a,case_b=b,scenario=scenario,window=window['id'],status='unavailable',reason=None,
        candidate_a=None,candidate_b=None,pressure_difference_pa=None,frequency_difference_hz=None,
        pressure_ratio=None,frequency_ratio=None,pressure_difference_bounds_pa=None,
        frequency_difference_bounds_hz=None,pressure_ratio_bounds=None,frequency_ratio_bounds=None)
    if not compatible: row['reason']='incompatible_domain_or_acoustic_conditions';return row
    if ma['candidate'] is None or mb['candidate'] is None:
        row['reason']=ma['reason'] or mb['reason'];row['status']='ambiguous' if 'ambiguous' in (ma['status'],mb['status']) else 'unavailable';return row
    ca,cb=ma['candidate'],mb['candidate'];ra,rb=ca['root'],cb['root']
    row.update(status='paired',candidate_a=ca['id'],candidate_b=cb['id'],
        pressure_difference_pa=rb['pressure_pa']-ra['pressure_pa'],
        frequency_difference_hz=rb['discrete_frequency_hz']-ra['discrete_frequency_hz'],
        pressure_ratio=rb['pressure_pa']/ra['pressure_pa'],
        frequency_ratio=rb['discrete_frequency_hz']/ra['discrete_frequency_hz'])
    for prefix,field in [('pressure','bracket_pa'),('frequency','frequency_bracket_hz')]:
        aa,bb=ca[field],cb[field]
        row[prefix+'_difference_bounds_'+('pa' if prefix=='pressure' else 'hz')]=[bb[0]-aa[1],bb[1]-aa[0]]
        row[prefix+'_ratio_bounds']=[bb[0]/aa[1],bb[1]/aa[0]] if aa[0]>0 else None
    if a==b or digest(ca)==digest(cb):
        row.update(pressure_difference_bounds_pa=[0.,0.],frequency_difference_bounds_hz=[0.,0.],
                   pressure_ratio_bounds=[1.,1.],frequency_ratio_bounds=[1.,1.])
    return row


def compare(p,units):
    """Pure pairing and hard/observe evaluation; priorities never become scores."""
    r=p['request'];windows={w['id']:w for w in r['windows']};cases={c['id']:c for c in r['cases']}
    meta={c['id']:c for c in p['cases']}
    def compatible(a,b):
        # Geometry and fitted coefficients may differ; conditions/coordinate domain may not.
        keys=('sample_rate_hz','loss_model','radiation_model','air_reference','h_cm','basis_completion')
        return (all(cases[a]['recipe'][k]==cases[b]['recipe'][k] for k in keys)
                and meta[a]['effective']==meta[b]['effective'])
    differentials=[paired_value(units,a,b,s['id'],w,compatible(a,b))
        for a,b in itertools.combinations(cases,2) for s in r['scenarios'] for w in r['windows']]
    evaluated=[]
    for c in r['criteria']:
        row=dict(c,status='unsupported',value_si=None,unit_si=None,reason='observable_not_covered',
                 uncertainty_bounds_si=None)
        observable=c['observable']
        if observable not in OBSERVABLES: evaluated.append(row);continue
        dim,unit=OBSERVABLES[observable];target=target_quantity(c['target'],dim);tol,tdim=quantity(c['tolerance'])
        row.update(unit_si=unit,target_si=target,tolerance_si=tol,tolerance_dimension=tdim)
        if 'case' in c:
            match=_matched(units,c['case'],c['scenario'],windows[c['window']]);candidate=match['candidate']
            if candidate is None: row.update(status='unresolved',reason=match['reason']);evaluated.append(row);continue
            key='pressure_pa' if observable=='onset_pressure' else 'discrete_frequency_hz'
            value=candidate['root'][key]
            interval=candidate['bracket_pa'] if observable=='onset_pressure' else candidate['frequency_bracket_hz']
        else:
            a,b=c['pair'];pair=paired_value(units,a,b,c['scenario'],windows[c['window']],compatible(a,b))
            if pair['status']!='paired': row.update(status='unresolved',reason=pair['reason']);evaluated.append(row);continue
            key={'pressure_difference':'pressure_difference_pa','frequency_difference':'frequency_difference_hz',
                 'pressure_ratio':'pressure_ratio','frequency_ratio':'frequency_ratio'}[observable]
            value=pair[key]
            ikey={'pressure_difference':'pressure_difference_bounds_pa','frequency_difference':'frequency_difference_bounds_hz',
                  'pressure_ratio':'pressure_ratio_bounds','frequency_ratio':'frequency_ratio_bounds'}[observable]
            interval=pair[ikey]
        lower,upper=(target*2**(-tol/1200),target*2**(tol/1200)) if tdim=='cent' else (target-tol,target+tol)
        status=('satisfied' if interval is not None and lower<=interval[0] and interval[1]<=upper else
                'violated' if interval is not None and (interval[1]<lower or interval[0]>upper) else 'unresolved')
        row.update(status=status,value_si=value,uncertainty_bounds_si=interval,
                   reason='bracket_overlaps_requirement_boundary' if status=='unresolved' else None)
        evaluated.append(row)
    hard=all(c['status']=='satisfied' for c in evaluated if c['role']=='hard')
    return dict(differentials=differentials,criteria=evaluated,hard_conforming=hard,
                selection_scope=core.SELECTION_SCOPE,
                coverage='complete' if all(c['status'] in ('satisfied','violated') for c in evaluated) else 'partial')


def _limits():
    child_limits()
    resource.setrlimit(resource.RLIMIT_FSIZE,(4*1024**2,4*1024**2))


def _run(plan_path,output_dir,*,dry_run=False):
    plan=preflight(plan_path,output_dir);p=plan.as_dict()
    if dry_run: return dict(ok=True,status='planned',plan=p)
    execution_ready();_check_plan(p)
    out=Path(p['output']);out.mkdir(parents=False,exist_ok=False)
    b=p['request']['budgets'];limit=b['output_mib']*1024**2
    report.write_json(out/'plan.json',p,budget_bytes=limit)
    started=time.monotonic();children=[];unit_refs=[];units={};used=0;active=None;cancelled=False;error=None
    old={}
    def stop(signum,frame):
        nonlocal cancelled
        cancelled=True
    try:
        for sig in (signal.SIGINT,signal.SIGTERM): old[sig]=signal.signal(sig,stop)
        for ci,case in enumerate(p['request']['cases']):
            for si,scenario in enumerate(p['request']['scenarios']):
                remaining=b['orchestrator_seconds']-(time.monotonic()-started)
                allowance=min(b['evaluations_per_unit'],b['total_evaluations']-used)
                if cancelled or remaining<=0 or allowance<2:
                    raise core.BudgetExhausted('cancelled_or_orchestrator_budget')
                task=dict(plan=p,case=ci,scenario=si,evaluation_allowance=allowance)
                env=dict(os.environ)
                env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
                # Parent drains a pipe into a bounded private log; never unbounded communicate().
                log=out/f'worker-{ci:02d}-{si:02d}.log'
                with log.open('xb') as stream:
                    active=subprocess.Popen([sys.executable,'-m','tools.paired_onset_reference','--worker'],
                        cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                        preexec_fn=_limits)
                    active.stdin.write(json.dumps(task,allow_nan=False).encode());active.stdin.close()
                    deadline=time.monotonic()+min(b['child_seconds'],remaining)
                    reason=None;log_bytes=0
                    os.set_blocking(active.stdout.fileno(),False)
                    def drain():
                        nonlocal log_bytes
                        try: chunk=os.read(active.stdout.fileno(),65536)
                        except BlockingIOError: return True
                        log_bytes+=len(chunk)
                        if log_bytes>65536: return False
                        stream.write(chunk);return True
                    while active.poll() is None:
                        if not drain(): reason='worker_log_budget'

                        if cancelled or time.monotonic()>=deadline or reason:
                            reason=reason or ('cancelled' if cancelled else 'timeout');active.terminate()
                            try: active.wait(timeout=2)
                            except subprocess.TimeoutExpired: active.kill();active.wait()
                            break
                        time.sleep(.05)
                    code=active.wait()
                    if not drain(): reason='worker_log_budget'
                    active.stdout.close()
                    children.append(dict(case=case['id'],scenario=scenario['id'],
                        exit_code=code,reaped=True,reason=reason));active=None
                name=f'unit-{ci:02d}-{si:02d}.json'
                if (out/name).is_file():
                    value=report.read_json(out/name)
                    used+=value['counters']['evaluations'];units[(case['id'],scenario['id'])]=value
                    unit_refs.append(dict(case=case['id'],scenario=scenario['id'],file=name,status=value['status'],counters=value['counters']))
                else:
                    acquired=sorted(out.glob(f'checkpoint-{ci:02d}-{si:02d}-*.json'))
                    if acquired:
                        last=report.read_json(acquired[-1])
                        if last['sha256']!=digest(last['payload']): raise ValueError('Partial checkpoint identity')
                        used+=last['payload']['counters']['evaluations']
                if code!=0 or reason: raise core.BudgetExhausted(reason or 'child_failed')
        _check_plan(p)
    except Exception as exc:
        if isinstance(exc,core.BudgetExhausted) and str(exc)=='orchestrator_wall_budget': raise
        error=f'{type(exc).__name__}: {exc}'
    finally:
        # Reap only children created here, before assembling terminal evidence.
        if active is not None:
            if active.poll() is None: active.terminate()
            try: active.wait(timeout=2)
            except subprocess.TimeoutExpired: active.kill();active.wait()
            children.append(dict(exit_code=active.returncode,reaped=True,reason='supervisor_failure'))
        # No terminal marker exists through ANY fallible output or restoration.
        for sig,handler in old.items(): signal.signal(sig,handler)
    ok=error is None and not cancelled and len(unit_refs)==len(p['request']['cases'])*len(p['request']['scenarios'])
    result=dict(schema=report.SCHEMA,plan=p,status='complete' if ok else 'partial',reason=error,
                units=unit_refs,counters=dict(evaluations=used),**compare(p,units))
    report.export(out,result,budget_bytes=limit)
    execution=dict(ok=ok,status=result['status'],children=children,reason=error,
                   manifest_sha256=report.file_sha256(out/'manifest.json'))
    report.prepare_completion(out,execution,budget_bytes=limit)
    response=dict(ok=ok,status=result['status'],output=str(out),hard_conforming=result['hard_conforming'],reason=error)
    return response


def run(plan_path,output_dir,*,dry_run=False):
    if dry_run: return _run(plan_path,output_dir,dry_run=True)
    # One wall deadline covers preflight, ALL sequential children, exports and closure.
    request,_=report.read_plan_source(plan_path);validate_request(request)
    execution_ready();started=time.monotonic()
    previous_handler=signal.getsignal(signal.SIGALRM);previous_timer=signal.getitimer(signal.ITIMER_REAL)
    def deadline(signum,frame): raise core.BudgetExhausted('orchestrator_wall_budget')
    signal.signal(signal.SIGALRM,deadline)
    allowance=request['budgets']['orchestrator_seconds']
    if previous_timer[0]>0: allowance=min(allowance,previous_timer[0])
    response=None
    try:
        signal.setitimer(signal.ITIMER_REAL,allowance)
        response=_run(plan_path,output_dir)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous_handler)
        remaining=max(.001,previous_timer[0]-(time.monotonic()-started)) if previous_timer[0]>0 else 0
        signal.setitimer(signal.ITIMER_REAL,remaining,previous_timer[1])
    report.publish_completion(output_dir)
    return response


# Fingerprints captured after imports, checked again before accepting a plan.
LOADED_SOURCES=report.sources()
