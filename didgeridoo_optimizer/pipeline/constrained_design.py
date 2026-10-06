"""Static contract orchestration using the native physical DESIGN and TMM APIs."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import numpy as np

from ..acoustics.peaks import find_peaks
from ..acoustics.transfer_matrix import input_impedance
from ..geometry.models import Design
from ..geometry.discretization import GeometryDiscretizer
from ..geometry.builders import DesignBuilder
from ..optimization.design_contract import Contract, InvalidRequest, read_request, expression
from ..optimization.constrained_search import search, preference_key
from ..reporting import constrained_design as report
from .fixed_design import load_fixed_context
from .design_pitch import models, strict_input, child_limits, execution_ready, RunInterrupted, interrupt_run

ROOT=Path(__file__).resolve().parents[2]


def load_inputs(config,design,request):
    # Native strict preliminary read bounds allocation; CONFIG/DESIGN interpretation
    # remains exclusively in load_fixed_context/validate_design.
    config_path=Path(config).resolve(strict=True); design_path=Path(design).resolve(strict=True)
    strict_input(config_path); raw=strict_input(design_path)
    if not isinstance(raw,dict) or not isinstance(raw.get('segments'),list) or not 1<=len(raw['segments'])<=128:
        raise InvalidRequest('DESIGN: 1..128 segments physiques requis avant allocation')
    context=load_fixed_context(config_path,design_path)
    for label,key,default in [('materials','database_file','materials_base_v1.yaml'),
                              ('variant_rules','variant_rules_file','wood_variant_rules_v1.yaml')]:
        p=Path(context['config'].get('materials',{}).get(key,default))
        expected=p.resolve() if p.is_absolute() else (config_path.parent/p).resolve()
        if expected!=Path(context['provenance']['files'][label]['path']):
            raise InvalidRequest('chemin DB ambigu selon CONFIG: '+key)
    r,source=read_request(request)
    contract=Contract(r,context)
    _,_,_,effective=models(context,contract.options)
    # Worst-case mesh bound before allocating a mesh, including derived lengths.
    lengths=[s.length_cm for s in context['design'].segments]
    for v in contract.variables+contract.derived:
        for f in v.get('fields',[v.get('field')]):
            if f and f.endswith('.length_cm'): lengths[int(f.split('.')[1])]=v['high']*100
    count=sum(max(2,math.ceil(length/(contract.spectrum['h_cm']/2))) for length in lengths)
    n=math.ceil((contract.spectrum['max_hz']-contract.spectrum['min_hz'])/contract.spectrum['final_step_hz'])+1
    b=contract.budgets
    if count>b['segments'] or n*count>b['frequency_segment_product']:
        raise InvalidRequest('budget maillage/fréquences dépassé avant allocation')
    files={k:{'name':Path(v['path']).name,'sha256':v['sha256'],'read':v['read']}
           for k,v in context['provenance']['files'].items()}
    files['request']=source
    plan=dict(contract.plan(),effective_models=effective,max_mesh_segments=count,
              input_files=files,request=r,original_config=context['config'],
              physical_design=context['design'].as_dict(),provenance=report.provenance(),
              limits=['Scénario nominal CONFIG unique; instrument fixe.',
                      'Pas de fréquence jouée, accessibilité toot, seuil, régime, couverture continue ou robustesse.',
                      'h/h2 et grille/refinement: estimations, pas bornes mathématiques uniformes.',
                      'Préférences lexicographiques classent uniquement les témoins finaux conformes trouvés.'])
    json.dumps(plan,allow_nan=False)
    return contract,plan


def mesh_for(design,h,subdivide_uniform=False):
    discretizer=GeometryDiscretizer(); parts=[]
    for s in design.segments:
        if s.is_uniform and not subdivide_uniform:
            parts.append(copy.deepcopy(s))
        else:
            parts.extend(discretizer.discretize(Design(design.id,[s]),h).segments)
    mesh=Design(design.id,parts,dict(is_discretized=True,source_segment_count=len(design.segments),h_cm=h))
    return DesignBuilder().assign_positions(mesh)


def extract_peaks(evaluate,spectrum,step,max_frequencies=10000):
    """Frequency-ordered native local |Zin| maxima; vectorized golden refinement."""
    n=math.ceil((spectrum['max_hz']-spectrum['min_hz'])/step)+1
    if n>max_frequencies: raise ValueError('budget grille avant allocation')
    grid=np.linspace(spectrum['min_hz'],spectrum['max_hz'],n)
    mag=np.abs(evaluate(grid))
    if not np.all(np.isfinite(mag)): raise ArithmeticError('impédance non finie')
    peaks=find_peaks(grid,mag,{'frequency_analysis':{'peak_detection':{
        'min_prominence':0.,'min_distance_hz':step,'max_number_of_peaks':64}}})
    if len(peaks)>32: raise ArithmeticError('plus de 32 pics: domaine ambigu ou budget modes dépassé')
    if not peaks: return []
    a=np.array([grid[p['index']-1] for p in peaks]); b=np.array([grid[p['index']+1] for p in peaks])
    brackets=list(zip(a.tolist(),b.tolist()))
    ambiguous=np.zeros(len(peaks),dtype=bool)
    precision_limited=np.zeros(len(peaks),dtype=bool)
    # Repeated local grids check uniqueness instead of assuming a unimodal basin.
    # This is still a sampled convergence estimate, not a uniform spectral proof.
    for _ in range(12):
        active=(b-a>spectrum['refinement_hz']) & ~precision_limited
        if not np.any(active): break
        if len(peaks)*33>max_frequencies: raise ValueError('budget raffinement avant allocation')
        local=a[:,None]+(b-a)[:,None]*np.linspace(0.,1.,33)[None,:]
        values=np.abs(evaluate(local.ravel())).reshape(local.shape)
        if not np.all(np.isfinite(values)): raise ArithmeticError('raffinement non fini')
        # Do not mistake floating-point noise at the top of a smooth peak for
        # additional modes. Retain the entire interval when contrast is lost.
        precision_limited |= (np.ptp(values,axis=1)<=1.e-12*np.max(values,axis=1))
        active &= ~precision_limited
        maxima=(values[:,1:-1]>values[:,:-2]) & (values[:,1:-1]>values[:,2:])
        ambiguous |= active & (np.sum(maxima,axis=1)!=1)
        best=np.argmax(values,axis=1)
        ambiguous |= active & ((best==0)|(best==32))
        j=np.clip(best,1,31); rows=np.arange(len(peaks))
        a=np.where(active,local[rows,j-1],a); b=np.where(active,local[rows,j+1],b)
    f=(a+b)/2
    eps=np.maximum(.002,f*1.e-5)
    center=np.abs(evaluate(f)); lm=np.abs(evaluate(f-eps)); rm=np.abs(evaluate(f+eps))
    if not np.all(np.isfinite(np.r_[center,lm,rm])): raise ArithmeticError('courbure non finie')
    curvature=(lm+rm-2*center)/(eps**2*np.maximum(center,1.e-300))
    result=[]
    for i,p in enumerate(peaks):
        reason=None
        if ambiguous[i]: reason='plusieurs maxima ou maximum perdu dans le bassin raffiné'
        if p['index'] in (1,n-2): reason='pic trop près du bord spectral'
        if curvature[i]>=-1.e-10: reason='courbure trop faible ou maximum perdu'
        if not brackets[i][0]<f[i]<brackets[i][1]: reason='pic hors bassin initial'
        result.append(dict(order=i+1,frequency_hz=float(f[i]),magnitude=float(center[i]),
            prominence=p['prominence'],relative_curvature_hz_minus2=float(curvature[i]),
            basin_hz=list(brackets[i]),refinement_interval_hz=[float(a[i]),float(b[i])],
            floating_point_limited=bool(precision_limited[i]),
            frequency_estimate_hz=float((b[i]-a[i])/2),status='unresolved' if reason else 'resolved',reason=reason))
    return result


def assign_modes(peaks,modes,expected_count=None):
    selected={}
    for ident,mode in modes.items():
        order=mode['order']; reason=None
        peak=peaks[order-1] if order<=len(peaks) else None
        if peak is None: reason='mode absent; aucun zéro substitué'
        elif expected_count is not None and expected_count!=len(peaks): reason='nombre de pics changé: identité de branche ambiguë'
        elif peak['status']!='resolved': reason=peak['reason']
        elif not mode['window_hz'][0]<peak['frequency_hz']<mode['window_hz'][1]: reason='mode ordonné hors fenêtre; aucun remplacement par le pic voisin'
        selected[ident]=dict(status='unresolved' if reason else 'resolved',reason=reason,peak=peak)
    return selected


def criterion_rows(contract,design,selected,estimates=None,acoustic_error=None):
    rows=[]
    for c in contract.criteria:
        row=dict(id=c['id'],observable=c['observable'],role=c['role'],status='unresolved',
                 value_si=None,unit_si={'length':'m','frequency':'Hz','scalar':'1','pressure':'Pa','cent':'cent'}[c['dimension']],
                 error_cents=None,margin=None,margin_unit=None,convergence_estimate=None,
                 certified_bound=None,reason=None,residual_normalized=None)
        reason=c['unsupported_reason']; value=None; uncertainty=0.
        if reason:
            row.update(status='unsupported',reason=reason); rows.append(row); continue
        try:
            if c['observable']=='geometry': value=expression(c['expression'],design.as_dict())[0]
            else:
                if acoustic_error: raise ArithmeticError(acoustic_error)
                refs=[c['mode']] if c['observable']=='resonance_frequency' else [c['numerator'],c['denominator']]
                modes=[selected[k] for k in refs]
                if any(m['status']!='resolved' for m in modes):
                    raise ArithmeticError('; '.join(str(m['reason']) for m in modes if m['status']!='resolved'))
                frequencies=[m['peak']['frequency_hz'] for m in modes]
                value=frequencies[0] if len(refs)==1 else frequencies[0]/frequencies[1]
                if estimates is not None:
                    uncertainty=(estimates[refs[0]] if len(refs)==1 else
                        abs(value)*(estimates[refs[0]]/frequencies[0]+estimates[refs[1]]/frequencies[1]))
            if value is None or not math.isfinite(value): raise ArithmeticError('observable absente/non finie')
            row['value_si']=float(value)
            target=c['target_si']; tol=c['tolerance_si']
            if c['tolerance_dimension']=='cent':
                if value<=0: raise ArithmeticError('valeur non positive pour cents')
                error=1200*math.log2(value/target)
                margin=tol-abs(error); uncertainty=1200*uncertainty/(math.log(2)*value)
                row['error_cents']=error; unit='cent'; residual=error/tol
            elif target is not None:
                error=value-target; margin=tol-abs(error); residual=error/tol; unit=row['unit_si']
                if c['dimension']=='frequency' and value>0 and target>0: row['error_cents']=1200*math.log2(value/target)
            else:
                margin=min(value-c['lower_si'],c['upper_si']-value)
                low=c['lower_si']+tol; high=c['upper_si']-tol
                residual=(value-low)/tol if value<low else (value-high)/tol if value>high else 0.
                unit=row['unit_si']
            row.update(margin=float(margin),margin_unit=unit,residual_normalized=float(residual),
                       convergence_estimate=float(uncertainty) if estimates is not None else None,
                       status='satisfied' if margin>=0 else 'violated')
            if estimates is not None and uncertainty>0 and abs(margin)<=uncertainty:
                row.update(status='unresolved',reason='frontière trop proche de l’estimation de convergence')
        except (ValueError,ArithmeticError,KeyError) as exc:
            row.update(status='unresolved',reason=str(exc))
        rows.append(row)
    return rows


class Evaluator:
    def __init__(self,contract):
        self.contract=contract; self.expected_count=None
        self.air,self.loss,self.radiation,self.effective=models(contract.context,contract.options)

    def spectrum(self,design,step,h,subdivide=False):
        c=self.contract
        count=sum(1 if s.is_uniform and not subdivide else max(1 if s.is_uniform else 2,math.ceil(s.length_cm/h)) for s in design.segments)
        n=math.ceil((c.spectrum['max_hz']-c.spectrum['min_hz'])/step)+1
        if count>c.budgets['segments'] or max(n,33*len(c.modes))*count>c.budgets['frequency_segment_product']:
            raise ValueError('budget maillage avant allocation')
        mesh=mesh_for(design,h,subdivide)
        if len(mesh.segments)>c.budgets['segments']: raise ValueError('budget segments')
        radius=design.segments[-1].d_out_cm/200
        def evaluate(freq):
            if len(freq)>c.budgets['frequencies'] or len(freq)*len(mesh.segments)>c.budgets['frequency_segment_product']:
                raise ValueError('budget fréquence/segment avant TMM')
            return input_impedance(freq,mesh,c.context['material_db'],self.air,
                exit_radius_m=radius,loss_model=self.loss,radiation_model=self.radiation)
        peaks=extract_peaks(evaluate,c.spectrum,step,c.budgets['frequencies'])
        return peaks,mesh

    def __call__(self,values):
        c=self.contract
        try: design=c.generate(values)
        except (ValueError,ArithmeticError) as exc:
            return dict(physical_design=None,criteria=[dict(id=k['id'],observable=k['observable'],role=k['role'],
                status='out_of_domain',value_si=None,unit_si=None,error_cents=None,margin=None,
                margin_unit=None,convergence_estimate=None,certified_bound=None,residual_normalized=None,reason=str(exc)) for k in c.criteria],
                search_feasible=False,peaks=[],selected_modes={})
        peaks=[]; selected={}; error=None
        if any(k['observable'] in ('resonance_frequency','resonance_ratio') and not k['unsupported_reason'] for k in c.criteria):
            try:
                peaks,_=self.spectrum(design,c.spectrum['step_hz'],c.spectrum['h_cm'])
                if self.expected_count is None: self.expected_count=len(peaks)
                selected=assign_modes(peaks,c.modes,self.expected_count)
            except (ValueError,ArithmeticError) as exc: error=str(exc)
        rows=criterion_rows(c,design,selected,acoustic_error=error)
        return dict(physical_design=design.as_dict(),criteria=rows,peaks=peaks,selected_modes=selected,
                    search_feasible=all(r['status']=='satisfied' for r in rows if r['role']=='hard'),
                    final_verified=False)

    def verify(self,values):
        c=self.contract; base=self(values); design=c.generate(values)
        levels=[]; estimates={}; error=None; selected=base['selected_modes']
        acoustic=any(k['observable'] in ('resonance_frequency','resonance_ratio') and not k['unsupported_reason'] for k in c.criteria)
        if acoustic:
            try:
                # Frequency then spatial refinement, never retuning the design.
                for h,subdivide in ((c.spectrum['h_cm'],False),(c.spectrum['h_cm']/2,True)):
                    peaks,mesh=self.spectrum(design,c.spectrum['final_step_hz'],h,subdivide)
                    selected=assign_modes(peaks,c.modes,len(base['peaks']))
                    levels.append(dict(h_cm=h,step_hz=c.spectrum['final_step_hz'],
                        subdivision_uniform=subdivide,peaks=peaks,selected_modes=selected,
                        analysis_mesh=dict(segment_count=len(mesh.segments),sha256=report.fingerprint(mesh.as_dict()),
                                           metadata=mesh.metadata)))
                for ident in c.modes:
                    seq=[base['selected_modes'][ident]]+[l['selected_modes'][ident] for l in levels]
                    if any(m['status']!='resolved' for m in seq): raise ArithmeticError('mode perdu/ambigu lors du raffinement: '+ident)
                    fs=[m['peak']['frequency_hz'] for m in seq]
                    estimates[ident]=max(abs(fs[1]-fs[0]),abs(fs[2]-fs[1]))+sum(m['peak']['frequency_estimate_hz'] for m in seq)
            except (ValueError,ArithmeticError,KeyError) as exc: error=str(exc)
        rows=criterion_rows(c,design,selected,estimates if acoustic and not error else {},error)
        # Radiation validity is assessed over the declared domain, not silently extrapolated.
        if c.options['radiation_model']!='legacy' and 2*math.pi*c.spectrum['max_hz']*(design.segments[-1].d_out_cm/200)/self.air.c>2:
            for row in rows:
                if row['observable'] in ('resonance_frequency','resonance_ratio') and row['status']!='unsupported':
                    row.update(status='out_of_domain',reason='radiation Silva: domaine déclaré dépasse |ka|=2')
        c.check_locks(design.as_dict())
        conforming=(all(r['status']=='satisfied' for r in rows if r['role']=='hard') and
                    any(r['status'] in ('satisfied','violated') for r in rows))
        # Unresolved observations do not veto obligations; unsupported hard obligations do.
        return dict(physical_design=design.as_dict(),criteria=rows,selected_modes=selected,
            refinement_levels=levels,convergence_estimates_hz=estimates,certified_uniform_bound=None,
            final_verified=conforming,conforming=conforming,
            request_fully_covered=all(r['status'] in ('satisfied','violated') for r in rows),
            physical_exit_radius_m=design.segments[-1].d_out_cm/200)


def execute(contract,plan,output):
    evaluator=Evaluator(contract); count=0
    def checkpoint(row):
        nonlocal count
        if row['physical_design'] is None: return
        count+=1
        report.write_json(output/f'checkpoint_{count:04d}.json',dict(request_sha256=report.fingerprint(contract.request),
            context_sha256=report.context_identity(contract),candidate_sha256=report.fingerprint(row),
            status='search_witness_not_final_conformity',candidate=row))
    result=search(contract,evaluator,checkpoint)
    # No finite-difference witness is discarded merely because it was not an
    # accepted optimizer step. Final verification is separately bounded to four.
    candidates=result['accepted'] or [result['best']]
    if candidates:
        candidates=sorted(candidates,key=lambda c:(preference_key(contract,c) is None,preference_key(contract,c) or ()))
    unique=[]; seen=set()
    for candidate in candidates:
        key=tuple(candidate['variables_si'])
        if key not in seen: unique.append(candidate);seen.add(key)
    verified=[evaluator.verify(c['variables_si']) for c in unique[:4]]
    solutions=[v for v in verified if v['conforming']]
    if solutions:
        solutions.sort(key=lambda c:(preference_key(contract,c) is None,preference_key(contract,c) or ()))
    final=solutions[0] if solutions else verified[0]
    preference_rows=[r for r in final['criteria'] if r['role']=='preference']
    optimization_status=('no_preferences' if not preference_rows else
        'unsupported' if any(r['status']=='unsupported' for r in preference_rows) else
        'unresolved' if any(r['status'] not in ('satisfied','violated') for r in preference_rows) else 'feasible_witness_ranking_only')
    if report.source_files()!=plan['provenance']['loaded_sources_sha256']:
        raise ValueError('sources chargées modifiées pendant calcul')
    status=('conforming' if final['request_fully_covered'] else 'hard_feasible_partial_coverage') if solutions else 'no_conforming_solution_found'
    payload=dict(schema_version='dcalc.constrained_design.result.v1',status=status,
        request=contract.request,plan=plan,search=result,final=final,solutions=solutions,
        final_candidates_verified=len(verified),final_verification_limit=4,
        request_fully_covered=final['request_fully_covered'],
        optimization_status=optimization_status,global_optimum_proven=False,infeasibility_proven=False,
        materials=(dict(acoustic_properties='omitted_by_zk_not_measured_zero',ids=contract.context['design'].material_ids)
                   if contract.options['loss_model']=='zk' else contract.context['materials_used']),
        relaxation_suggestions=[],scope='CONFIG nominal',physical_validation=False)
    return report.export_result(output,payload,contract)


def worker(task):
    contract,plan=load_inputs(task['config'],task['design'],task['request'])
    # An API host can have additional D-Calc modules loaded. Check their exact
    # bytes, without pretending that the worker executed those extra modules.
    parent_sources=task['plan']['provenance']['loaded_sources_sha256']
    if plan['input_files']!=task['plan']['input_files'] or any(
        hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=sha for name,sha in parent_sources.items()):
        raise ValueError('entrées/sources modifiées depuis plan')
    plan['parent_sources_checked_sha256']=parent_sources
    result=execute(contract,plan,Path(task['output']))
    return dict(ok=True,status=result['status'],conforming=bool(result['solutions']) and result['request_fully_covered'],
                hard_conforming=bool(result['solutions']),request_fully_covered=result['request_fully_covered'],output_dir=task['output'])


def run(config,design,request,output_dir,*,dry_run=False):
    output=report.new_destination(output_dir)
    contract,plan=load_inputs(config,design,request)
    if dry_run: return dict(ok=True,dry_run=True,output_created=False,plan=plan)
    execution_ready()
    output.mkdir(parents=True,exist_ok=False)
    report.write_json(output/'plan.json',plan)
    task=dict(config=str(Path(config).resolve()),design=str(Path(design).resolve()),request=str(Path(request).resolve()),
              output=str(output),plan=plan)
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    handlers={}; started=time.monotonic(); response=None
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT,signal.SIGTERM): handlers[signum]=signal.signal(signum,interrupt_run)
    try:
        child=subprocess.run([sys.executable,'-B','-m','tools.constrained_design','--worker'],
            input=json.dumps(task,allow_nan=False),capture_output=True,text=True,cwd=ROOT,env=env,
            preexec_fn=child_limits,timeout=180)
        response=json.loads(child.stdout)
        response.update(child_exit_code=child.returncode,child_reaped=True)
        response['ok']=response.get('ok') is True and child.returncode==0
        if child.stderr: response['diagnostic']=child.stderr[-4000:]
    except (subprocess.TimeoutExpired,RunInterrupted,KeyboardInterrupt) as exc:
        response=dict(ok=False,status='interrupted',reason=str(exc),child_reaped=True)
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        response=dict(ok=False,status='failed',reason=str(exc),child_reaped=True)
    finally:
        for signum,handler in handlers.items(): signal.signal(signum,handler)
    response['wall_seconds']=time.monotonic()-started
    report.write_json(output/'execution.json',response)
    return response
