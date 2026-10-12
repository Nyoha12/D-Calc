"""Supervised command policies over a native tolerance-audit input.

execute requires external BLAS1 / AS768MiB / CPU175s / wall180s limits.
run is the supervised public API. No production monkeypatch of native workers.
"""
from __future__ import annotations

import copy
import cProfile
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from . import tolerance_audit as audit
from .assembly_path import Budget, BudgetExhausted
from .constrained_design import criterion_rows
from .design_pitch import execution_ready, child_limits, RunInterrupted, interrupt_run
from .fixed_design import _file_source
from ..geometry.tolerance_scenarios import perturb, check_mask, ScenarioUnavailable
from ..optimization.design_contract import read_request, InvalidRequest
from ..optimization.constrained_search import search
from ..optimization.adjustment_policy import (SCHEMA, LIMITS, job_contract, bind_policy,
    groups, commands, apply_commands, adapter, candidate_status)
from ..reporting import adjustment_policy as report

ROOT=Path(__file__).resolve().parents[2]
PUBLIC_WALL_SECONDS=200.
COMPUTE_WALL_SECONDS=180.
COLLECTION_WALL_SECONDS=10.
CLOSURE_RESERVE_SECONDS=2.
_collect_child=audit._collect_child
LOADED_SOURCES=report.source_files()


def load_inputs(job):
    """Strict native loading and bounded geometry preflight; no mesh/search/TMM."""
    report.verify_sources(LOADED_SOURCES)
    path=Path(job).resolve(strict=True); raw,source=read_request(path)
    policy,limits=job_contract(raw)
    audit_path=audit._reference(path.parent,raw['audit_job'])
    if audit_path==path: raise InvalidRequest('cycle audit_job')
    # Read bounded native documents only to count products before the native
    # loader allocates projections/scenario geometry. No alternative parser.
    preview,audit_source=read_request(audit_path)
    if not isinstance(preview,dict) or not isinstance(preview.get('scenarios'),list) or not isinstance(preview.get('input'),dict):
        raise InvalidRequest('JOB audit natif requis')
    count=len(preview['scenarios']);inp=preview['input']
    if inp.get('kind')=='assembly':
        request_path=audit._reference(audit_path.parent,inp.get('assembly_request'))
        request_preview,_=read_request(request_path)
        declared=request_preview.get('projections') if isinstance(request_preview,dict) else None
        if not isinstance(declared,list) or any(not isinstance(p,dict) or not isinstance(p.get('configuration'),str) for p in declared):
            raise InvalidRequest('projections natives requises')
        projection_count=len(declared)
        group_count=len({p['configuration'] for p in declared}) if policy['kind']=='per_configuration' else 1
    else: projection_count=group_count=1
    if count*projection_count>limits['projections'] or count*group_count>min(limits['candidates'],limits['evaluations']):
        raise InvalidRequest('budget produit scénarios/projections/groupes avant allocation native')
    prepared,original=audit.load_inputs(audit_path)
    if prepared['sources']['job']['sha256']!=audit_source['sha256']:
        raise InvalidRequest('JOB audit modifié depuis préflight')
    if path in {Path(s['path']) for s in prepared['sources'].values()}:
        raise InvalidRequest('référence adjustment_job conflictuelle')
    policy=bind_policy(policy,prepared['kind'],prepared['nominal'],original['native_request'],prepared['projections'])
    units=groups(policy,prepared['projections'])
    count=len(prepared['parsed']['scenarios'])
    if count*len(units)>limits['candidates'] or count*len(units)>limits['evaluations']:
        raise InvalidRequest('budget sous-problèmes avant allocation')
    if count*len(prepared['projections'])>limits['projections']:
        raise InvalidRequest('budget projections avant allocation')
    for k,v in original['minimum_grid_cost'].items():
        if v>limits[k]: raise InvalidRequest('budget minimal des grilles: '+k)
    # Conservative history representation bound before trial/profile copies.
    size=len(json.dumps(report.identity_plan(original),allow_nan=False).encode())
    estimate=size*(4+limits['evaluations'])
    if estimate>limits['memory_mib']*1024**2//2:
        raise InvalidRequest('budget mémoire historique avant allocation')
    context=prepared['context']; db=context['material_db']
    snapshot=dict(materials=copy.deepcopy(db.raw),
        variant_rules=copy.deepcopy(db.variant_generator.rules) if db.variant_generator else None)
    plan=dict(schema_version=SCHEMA,job=raw,audit_plan=original,policy=policy,groups=units,
        budgets=limits,geometry=original['geometry'],parsed=original['parsed'],
        expected_hard=original['expected_hard'],uncovered=original['uncovered'],
        material_snapshot=snapshot,provenance=report.provenance(),
        adjustment_job_source=source,continuous_robustness_certified=False,
        information_statement=('Existence conditionnelle: fabrication connue du calcul; ni identification ni contrôleur.'
            if policy['kind']!='fixed' else 'Commandes fournies fixes, aucune liberté supplémentaire.'))
    if len(json.dumps(plan,allow_nan=False).encode())>min(2*1024**2,limits['output_mib']*1024**2//8):
        raise InvalidRequest('budget plan/output')
    prepared.update(job_path=path,policy=policy,groups=units)
    prepared['sources']['adjustment_job']=_file_source(path)
    if prepared['sources']['adjustment_job']['sha256']!=source['sha256']:
        raise InvalidRequest('JOB modifié pendant lecture')
    return prepared,plan


def execute(prepared,plan,output):
    """All calls, including probes and refusals, share one native Budget."""
    output=Path(output); limits=plan['budgets']; budget=Budget(limits)
    policy=plan['policy']; records=[]; final=[]; trials=[]; interrupted=False
    for scenario in prepared['parsed']['scenarios']:
        try:
            fabricated=perturb(prepared['nominal'],prepared['parsed'],scenario,prepared['kind'])
            check_mask(prepared['nominal'],fabricated,prepared['parsed'],prepared['kind'])
            unavailable=None
        except ScenarioUnavailable as exc:
            fabricated=None; unavailable=str(exc)
        chosen=[]; scenario_groups=[]
        for group in prepared['groups']:
            selected=[p for p in prepared['projections'] if p['id'] in group['projections']]
            local=[]; stop_reason=None; search_result=None
            def evaluate(values):
                nonlocal interrupted
                budget.consume(evaluations=1)
                sequence=len(trials)+1; observations=[]; command_rows=[]; candidate=fabricated
                refusal=None
                try:
                    command_rows=commands(policy,group['configurations'],values)
                    if fabricated is not None: candidate=apply_commands(fabricated,command_rows)
                except (ValueError,ArithmeticError) as exc:
                    refusal=str(exc)
                if unavailable or refusal:
                    generated=[(p,None,{},dict(status='unresolved',reason=unavailable or refusal)) for p in selected]
                else:
                    generated=audit.geometries(prepared['kind'],candidate,prepared['context'],selected)
                for p,design,metadata,issue in generated:
                    payload={}; reason=issue['reason'] if issue else None
                    evaluator=None
                    try:
                        budget.consume(projections=1)
                        if issue is not None:
                            rows=report.unavailable_rows(p['template'].criteria,reason)
                        else:
                            evaluator=audit.TracedProjectionEvaluator(p['template'],budget,
                                lambda trace,level: report.write_json(output/f'spectrum_{sequence:04d}_{p["id"]}_{level}.json',
                                    dict(trace,scenario=scenario['id'],projection=p['id'],trial=sequence),
                                    budget_bytes=report._limit(plan)))
                            payload=evaluator.evaluate_design(design,verify=True)
                            rows=payload.pop('criteria')
                            payload.update(verified=True,completed_spectra=evaluator.completed_spectra)
                    except (BudgetExhausted,MemoryError) as exc:
                        interrupted=True; reason=type(exc).__name__+': '+str(exc)
                        rows=(criterion_rows(p['template'],design,{},None,reason) if design is not None
                              else report.unavailable_rows(p['template'].criteria,reason))
                        payload=dict(verified=False,completed_spectra=evaluator.completed_spectra if evaluator is not None else [])
                    observation=dict(scenario=scenario['id'],projection=p['id'],configuration=p['configuration'],
                        nominal=scenario['nominal'],coefficients=scenario['coefficients'],
                        geometry_status=issue['status'] if issue else 'valid',reason=reason,
                        criteria=rows,acoustics=payload,geometry=metadata,effective_request=p['request'],
                        profile=design.as_dict() if design is not None else None,
                        policy=policy['kind'],information=policy['information'],trial=sequence)
                    observations.append(observation)
                status=candidate_status(observations)
                row=dict(trial=sequence,scenario=scenario['id'],group=group['id'],
                    variables_si=[float(x) for x in values],commands=command_rows,refusal=refusal,
                    criteria=[dict(c,id=o['projection']+':'+c['id']) for o in observations for c in o['criteria']],
                    observations=observations,search_feasible=status=='witness_found',
                    verified=status=='witness_found',status=status,counters=budget.snapshot())
                # Persist every verified probe before the search can exhaust its budget.
                report.write_json(output/f'trial_{sequence:04d}.json',row,budget_bytes=report._limit(plan))
                local.append(row);trials.append(dict(trial=sequence,scenario=scenario['id'],group=group['id']))
                return row
            try:
                budget.consume(candidates=1)
                remaining=limits['evaluations']-budget.counts['evaluations']
                if remaining<=0: raise BudgetExhausted('évaluations globales épuisées')
                contract=adapter(policy,group,prepared['projections'],limits,remaining,
                                 limits['seconds']-(time.monotonic()-budget.start))
                search_result=search(contract,evaluate)
                stop_reason=search_result['termination']
            except (BudgetExhausted,MemoryError) as exc:
                interrupted=True;stop_reason=type(exc).__name__+': '+str(exc)
            witnesses=[r for r in local if r['verified']]
            # The native search ranks preferences, but a later failed poll cannot
            # erase a verified witness, including finite-difference probes.
            best=search_result['best'] if search_result else None
            retained=(best if best is not None and best['verified'] else witnesses[0] if witnesses
                      else best if best is not None else local[-1] if local else None)
            status=('witness_found' if witnesses else
                'candidate_violated' if policy['kind']=='fixed' and retained and retained['status']=='candidate_violated'
                else 'unsupported' if retained and retained['status']=='unsupported'
                else 'out_of_domain' if retained and retained['status']=='out_of_domain' else 'search_not_resolved')
            record=dict(scenario=scenario['id'],group=group['id'],status=status,verified=bool(witnesses),
                selected_trial=retained['trial'] if retained else None,termination=stop_reason,
                trials=[r['trial'] for r in local],infeasibility_proven=False,global_optimum_proven=False,
                search_trace=[dict(trial=r['trial'],stage=r.get('stage'),evaluation=r.get('evaluation')) for r in local])
            records.append(record);scenario_groups.append(record)
            if retained: chosen.append(retained)
        all_commands=[r for candidate in chosen for r in candidate['commands']]
        effective=apply_commands(fabricated,all_commands) if fabricated is not None else None
        if effective is None: report.write_unavailable_scenario(output,scenario,unavailable,plan)
        else: report.write_geometry(output,scenario['id'],effective,prepared['kind'],plan)
        chosen_by_projection={o['projection']:o for r in chosen for o in r['observations']}
        generated=(audit.geometries(prepared['kind'],effective,prepared['context'],prepared['projections'])
            if effective is not None else [(p,None,{},dict(status='unresolved',reason=unavailable)) for p in prepared['projections']])
        for p,design,metadata,issue in generated:
            previous=chosen_by_projection.get(p['id'])
            if previous is None:
                previous=dict(scenario=scenario['id'],projection=p['id'],configuration=p['configuration'],
                    nominal=scenario['nominal'],coefficients=scenario['coefficients'],reason='aucun essai disponible',
                    criteria=report.unavailable_rows(p['template'].criteria,'aucun essai disponible'),
                    acoustics={},effective_request=p['request'],policy=policy['kind'],information=policy['information'],trial=None)
                if design is not None:
                    for row,c in zip(previous['criteria'],p['template'].criteria):
                        row['unit_si']={'length':'m','frequency':'Hz','scalar':'1','pressure':'Pa','cent':'cent'}[c['dimension']]
            observation=copy.deepcopy(previous); observation.pop('profile',None)
            if previous.get('profile') is not None and design is not None:
                # Combining independent q changes assembly identity metadata only.
                if previous['profile']['segments']!=design.as_dict()['segments']:
                    raise ValueError('raccord final modifie le profil acoustique vérifié')
            observation.update(geometry=metadata,geometry_status=issue['status'] if issue else 'valid')
            if design is not None: report.write_profile(output,scenario['id'],p['id'],design.as_dict(),plan)
            final.append(observation);report.write_observation(output,observation,len(final),plan)
        report.write_json(output/f'commands_{scenario["id"]}.json',all_commands,budget_bytes=report._limit(plan))
    result=dict(schema_version=SCHEMA,plan=plan,observations=final,groups=records,trials=trials,counters=budget.snapshot())
    result.update(report.summarize(final,plan,records,not interrupted))
    return result


def worker(task):
    import resource
    execution_ready()
    if any(os.environ.get(k)!='1' for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')):
        raise ValueError('worker exige BLAS1 avant import')
    if not 0<resource.getrlimit(resource.RLIMIT_AS)[0]<=768*1024**2 or not 0<resource.getrlimit(resource.RLIMIT_CPU)[0]<=175:
        raise ValueError('worker exige limites mémoire/CPU imposées par le parent')
    baseline=report.source_files();report.verify_sources(task['sources'])
    profiler=cProfile.Profile();profiler.enable()
    try:
        prepared,plan=load_inputs(task['job'])
        if report.fingerprint(report.identity_plan(plan))!=task['identity']:
            raise ValueError('entrées/plan changés depuis préflight')
        result=execute(prepared,plan,Path(task['output']))
    finally: profiler.disable()
    prefix=str(ROOT)+os.sep;executed={'tools/adjustment_policy.py'}
    for entry in profiler.getstats():
        filename=getattr(entry.code,'co_filename','')
        if filename.startswith(prefix) and filename.endswith('.py'): executed.add(filename[len(prefix):])
    report.verify_sources(baseline);report.verify_sources(task['sources'])
    for label,source in prepared['sources'].items():
        if _file_source(Path(source['path']),optional=label=='variant_rules')!=source:
            raise ValueError('entrée changée pendant calcul: '+label)
    result['plan']['provenance']=report.provenance(executed)
    report.export_result(task['output'],result)
    return {k:result[k] for k in ('status','execution_complete','sampled_policy_conforming','request_fully_covered','counterexample_found')} | {'ok':result['execution_complete']}


def _worker_limits():
    child_limits()
    import resource
    # Native JSON artifacts already cap each file at 4 MiB; bound log files too.
    resource.setrlimit(resource.RLIMIT_FSIZE, (4*1024**2, 4*1024**2))


def _run(job, output_dir, dry_run, *, deadline=None):
    if deadline is None:
        deadline = time.monotonic()+PUBLIC_WALL_SECONDS
    prepared, plan = load_inputs(job)
    if dry_run: return dict(ok=True,dry_run=True,output_created=False,plan=plan)
    execution_ready()
    output = report.new_destination(output_dir)
    output.mkdir(parents=True,exist_ok=False)
    report.write_json(output/'plan.json',plan,budget_bytes=plan['budgets']['output_mib']*1024**2)
    task = dict(job=str(prepared['job_path']),output=str(output),identity=report.fingerprint(report.identity_plan(plan)),
                sources=report.source_files())
    env = dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    child = None; handlers = {}; response = None; started = time.monotonic(); stdout = stderr = ''; logs = []
    collected = False; collection_signals = []
    try:
        for signum in (signal.SIGINT,signal.SIGTERM): handlers[signum] = signal.signal(signum,interrupt_run)
        timeout = min(COMPUTE_WALL_SECONDS,deadline-time.monotonic()-COLLECTION_WALL_SECONDS-CLOSURE_RESERVE_SECONDS)
        if timeout <= 0:
            raise subprocess.TimeoutExpired('budget public avant lancement',0.)
        logs = [open(output/name, 'x+', encoding='utf-8') for name in ('.worker-stdout.log','.worker-stderr.log')]
        child = subprocess.Popen([sys.executable,'-B','-m','tools.adjustment_policy','--worker'],
            stdin=subprocess.PIPE,stdout=logs[0],stderr=logs[1],text=True,
            cwd=ROOT,env=env,preexec_fn=_worker_limits)
        compute_deadline = min(time.monotonic()+timeout,deadline-COLLECTION_WALL_SECONDS-CLOSURE_RESERVE_SECONDS)
        remaining = compute_deadline-time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired('budget public après lancement',0.)
        child.communicate(json.dumps(task,allow_nan=False),timeout=remaining)
        collected = True
        for stream in logs: stream.flush(); stream.seek(0)
        stdout,stderr = [stream.read(65536) for stream in logs]
        if time.monotonic() >= compute_deadline:
            raise subprocess.TimeoutExpired('retour tardif du calcul',timeout)
        response = json.loads(stdout)
        response['ok'] = response.get('ok') is True and child.returncode == 0
        if not response['ok']:
            response['execution_complete'] = False
        if stderr: response['diagnostic'] = stderr[-2000:]
    except BaseException as exc:
        response = dict(ok=False,status='interrupted' if isinstance(exc,(RunInterrupted,KeyboardInterrupt,subprocess.TimeoutExpired))
                        else 'failed',reason=str(exc),execution_complete=False)
        if stdout or stderr:
            response['diagnostic'] = dict(stdout=stdout[-2000:],stderr=stderr[-4000:])
    finally:
        # Repeated INT/TERM must not interrupt the bounded reaping attempt. They
        # still make the command unsuccessful and are reported after cleanup.
        for signum in handlers:
            signal.signal(signum,lambda received,frame: collection_signals.append(received))
        collection_deadline = min(time.monotonic()+COLLECTION_WALL_SECONDS,
                                  deadline-CLOSURE_RESERVE_SECONDS)
        child_state, errors, late = _collect_child(child,collected,collection_deadline)
        for stream in logs:
            try: stream.close()
            except BaseException as exc: errors.append('fermeture log: '+str(exc))
        late = late or time.monotonic() >= collection_deadline
        for signum,handler in handlers.items(): signal.signal(signum,handler)
    response.update(child=child_state,wall_seconds=time.monotonic()-started)
    confirmed = (not child_state['created'] or child_state['reaped']) and child_state['collected'] and child_state['pipes_closed'] and not late
    if errors or collection_signals or not confirmed:
        response.update(ok=False,execution_complete=False,status='interrupted' if collection_signals else 'failed')
        response['cleanup_errors'] = errors
        if collection_signals: response['cleanup_signals'] = collection_signals
    response['closure_confirmed'] = confirmed
    if confirmed:
        report.close_prepared(output,response,plan)
        if time.monotonic() >= deadline:
            response.update(ok=False,execution_complete=False,closure_confirmed=False,status='interrupted',
                            reason='retour tardif de clôture')
    return response


def run(job, output_dir, *, dry_run=False):
    """Public bounded API; dry-run reads/validates only and creates no output."""
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('API supervisée: thread principal requis')
    if os.name != 'posix':
        if not dry_run: execution_ready()
        return _run(job,output_dir,dry_run)
    if signal.getitimer(signal.ITIMER_REAL) != (0.,0.):
        raise ValueError('API supervisée: alarme préexistante refusée')
    deadline = time.monotonic()+PUBLIC_WALL_SECONDS
    old = signal.signal(signal.SIGALRM,interrupt_run)
    try:
        signal.setitimer(signal.ITIMER_REAL,PUBLIC_WALL_SECONDS)
        response = _run(job,output_dir,dry_run,deadline=deadline)
    finally:
        try: signal.setitimer(signal.ITIMER_REAL,0.)
        finally: signal.signal(signal.SIGALRM,old)
    if not dry_run and response.get('closure_confirmed'):
        # All cleanup/restoration precedes this no-overwrite terminal commit.
        if time.monotonic() >= deadline:
            response.update(ok=False,execution_complete=False,closure_confirmed=False,status='interrupted',
                            reason='budget public épuisé avant reçu terminal')
        else:
            terminal_error = None
            try:
                report.commit_terminal(output_dir)
                if time.monotonic() >= deadline:
                    terminal_error = 'retour tardif de publication terminale'
            except BaseException as exc:
                terminal_error = type(exc).__name__+': '+str(exc)
            if terminal_error is not None:
                response.update(ok=False,execution_complete=False,closure_confirmed=False,status='interrupted',
                                reason=terminal_error)
                out = Path(output_dir)
                cancellation = dict(response,execution_sha256=report.file_sha256(out/'execution.json'))
                try:
                    report.write_json(out/'execution.cancelled.json',cancellation,
                        budget_bytes=report.read_json(out/'plan.json')['budgets']['output_mib']*1024**2)
                except BaseException:
                    # If a failed disk cannot publish cancellation, revoke only
                    # the marker created by this call; preserve every observation.
                    (out/'execution.completed.json').unlink(missing_ok=True)
                    raise
    return response
