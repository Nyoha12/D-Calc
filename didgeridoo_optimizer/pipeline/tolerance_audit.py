"""Finite manufacturing scenarios on one supplied instrument; no search or repair.

run() owns one resource-limited child. load_inputs() validates without acoustics.
execute() requires the same externally imposed resource limits as the worker.
"""
from __future__ import annotations

import copy
import cProfile
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from ..geometry.assemblies import Assembly, InvalidAssembly
from ..geometry.tolerance_scenarios import parse_scenarios, perturb, check_mask, ScenarioUnavailable
from ..optimization.design_contract import Contract, read_request, obj, integer, InvalidRequest
from ..optimization.assembly_contract import AssemblyContract, native_contract
from .design_input import validate_design
from .fixed_design import load_analysis_context, load_fixed_context, _file_source
from .design_pitch import strict_input, models, execution_ready, child_limits, RunInterrupted, interrupt_run
from .assembly_path import ProjectionEvaluator, Budget, BudgetExhausted
from .constrained_design import criterion_rows
from ..reporting import tolerance_audit as report

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'dcalc.tolerance_audit.v1'
LIMITS = dict(uncertainties=8, scenarios=65, fields=64, scenario_fields=4160,
              projections=4096, spectral_calls=10000, frequencies=2000000,
              frequency_segment_product=200000000, segments=200000,
              seconds=165, memory_mib=700, output_mib=100)
PUBLIC_WALL_SECONDS = 200.
COMPUTE_WALL_SECONDS = 180.
COLLECTION_WALL_SECONDS = 10.
CLOSURE_RESERVE_SECONDS = 2.
LOADED_SOURCES = report.source_files()


def fixed_contract(request, context):
    """Use the native fixed-obligation deferral, retaining historical link groups.

    Assembly's native adapter can replace variables during its validation-only
    deferral. Recover those groups through a second native validation with the
    geometry obligations observed; all evaluated criteria remain the originals.
    """
    contract = native_contract(request, context)
    if len(contract.variables) != len(request['variables']):
        validation = copy.deepcopy(request)
        for criterion in validation['criteria']:
            if criterion['role'] == 'hard' and criterion['observable'] == 'geometry':
                criterion['role'] = 'observe'
        historical = Contract(validation, context)
        for key in ('variables', 'derived', 'fields', 'lock_mask', 'unrepresented_fields'):
            setattr(contract, key, getattr(historical, key))
    return contract


def _reference(parent, value):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise InvalidRequest('référence relative au JOB requise')
    return (parent / value).resolve(strict=True)


def _physical_violation(exc):
    # These native diagnostics establish a dimensional/geometric contradiction.
    # Any other ValueError (including budgets, unknown capacity) remains unresolved.
    text = str(exc)
    return (text.startswith('design.geometry:') or
            ('expected a finite strictly positive number' in text and text.startswith('design.segments[')) or
            isinstance(exc, InvalidAssembly) and any(part in text for part in (
                '0 < diamètre intérieur < diamètre extérieur', 'géométrie incompatible; marges SI exactes',
                'stock insuffisant pour le profil fixe entier', 'longueur positive finie requise')))


def geometries(kind, raw, context, projections):
    """Yield independent native projections; one common raw assembly per scenario."""
    if kind == 'fixed':
        try:
            design = validate_design(raw, context['material_db'], context['config'])
            yield projections[0], design, {}, None
        except (ValueError, ArithmeticError) as exc:
            yield projections[0], None, {}, dict(status='geometry_violated' if _physical_violation(exc)
                                                  else 'unresolved', reason=str(exc))
        return
    try:
        assembly = Assembly(raw, context['material_db'], context['config'])
    except (ValueError, ArithmeticError) as exc:
        for p in projections:
            yield p, None, {}, dict(status='geometry_violated' if _physical_violation(exc)
                                   else 'unresolved', reason=str(exc))
        return
    for p in projections:
        try:
            generated = assembly.generate(p['configuration'])
            design = generated.pop('design')
            yield p, design, generated, None
        except (ValueError, ArithmeticError) as exc:
            yield p, None, {}, dict(status='geometry_violated' if _physical_violation(exc)
                                   else 'unresolved', reason=str(exc))


def _mesh_bound(design, contract):
    s = contract.spectrum
    counts = [sum(1 if segment.is_uniform and not subdivide else
                  max(1 if segment.is_uniform else 2, math.ceil(segment.length_cm/h))
                  for segment in design.segments)
              for h, subdivide in ((s['h_cm'], False), (s['h_cm'], False), (s['h_cm']/2, True))]
    grids = [math.ceil((s['max_hz']-s['min_hz'])/step)+1
             for step in (s['step_hz'], s['final_step_hz'], s['final_step_hz'])]
    for count, n in zip(counts, grids):
        if (count > contract.budgets['segments'] or n > contract.budgets['frequencies'] or
                count*n > contract.budgets['frequency_segment_product']):
            raise InvalidRequest('budget natif maillage/fréquences avant allocation')
    return dict(segments=sum(counts), frequencies=sum(grids),
                frequency_segment_product=sum(n*c for n,c in zip(grids, counts)), spectral_calls=3)


def load_inputs(job):
    report.verify_sources(LOADED_SOURCES)
    job_path = Path(job).resolve(strict=True)
    raw, job_source = read_request(job_path)
    obj(raw, {'schema_version','input','uncertainties','scenarios','budgets','coverage'},
        'JOB', {'schema_version','input','uncertainties','scenarios','budgets','coverage'})
    if raw['schema_version'] != SCHEMA:
        raise InvalidRequest('version JOB inconnue')
    obj(raw['budgets'], LIMITS, 'budgets', LIMITS)
    limits = {k:integer(raw['budgets'][k], 1, cap, 'budgets.'+k) for k,cap in LIMITS.items()}
    obj(raw['coverage'], {'kind'}, 'coverage', {'kind'})
    if raw['coverage']['kind'] not in ('finite', 'continuous'):
        raise InvalidRequest('coverage.kind: finite ou continuous')
    inp = raw['input']
    obj(inp, {'kind','config','design','request','assembly','assembly_request'}, 'input', {'kind','config'})
    kind = inp['kind']
    expected = {'kind','config','design','request'} if kind == 'fixed' else {'kind','config','assembly','assembly_request'}
    if kind not in ('fixed','assembly') or set(inp) != expected:
        raise InvalidRequest('input: fixed DESIGN+REQUEST ou ASSEMBLY+ASSEMBLY_REQUEST')
    # Reject cardinality products before native context and geometry allocation.
    us, ss = raw['uncertainties'], raw['scenarios']
    if not isinstance(us,list) or not 1 <= len(us) <= limits['uncertainties']:
        raise InvalidRequest('budget uncertainties')
    if not isinstance(ss,list) or not 1 <= len(ss) <= limits['scenarios']:
        raise InvalidRequest('budget scenarios')
    fields = sum(len(u.get('fields',[])) if isinstance(u,dict) and isinstance(u.get('fields'),list)
                 else limits['fields']+1 for u in us)
    if fields > limits['fields'] or fields*len(ss) > limits['scenario_fields']:
        raise InvalidRequest('budget produit scénario-champs avant allocation')
    paths = {k:_reference(job_path.parent,v) for k,v in inp.items() if k != 'kind'}
    if len(set(paths.values()) | {job_path}) != len(paths)+1:
        raise InvalidRequest('références de fichiers répétées ou cycliques')
    strict_input(paths['config'])
    request_key = 'request' if kind == 'fixed' else 'assembly_request'
    request, request_source = read_request(paths[request_key])
    projection_count = 1 if kind == 'fixed' else len(request.get('projections',[]))
    if projection_count*len(ss) > limits['projections']:
        raise InvalidRequest('budget produit scénarios-projections avant contexte natif')
    # Conservative representation budget before native copies/projections. Acoustic
    # arrays are additionally gated by ProjectionEvaluator immediately before TMM.
    file_bytes = sum(p.stat().st_size for p in paths.values()) + job_path.stat().st_size
    representation_bytes = file_bytes*(16+4*projection_count) + fields*len(ss)*128
    if representation_bytes > limits['memory_mib']*1024**2//2:
        raise InvalidRequest('budget mémoire des représentations avant allocation native')
    if kind == 'fixed':
        physical_raw = strict_input(paths['design'])
        if not isinstance(physical_raw,dict) or not isinstance(physical_raw.get('segments'),list) or not 1 <= len(physical_raw['segments']) <= 128:
            raise InvalidRequest('DESIGN: 1..128 segments physiques')
        context = load_fixed_context(paths['config'], paths['design'])
        contract = fixed_contract(request, context)
        nominal = context['design'].as_dict()
        projections = [dict(id='fixed', configuration='nominal', request=request, template=contract)]
    else:
        nominal, _ = read_request(paths['assembly'])
        context = load_analysis_context(paths['config'])
        assembly = Assembly(nominal, context['material_db'], context['config'])
        contract = AssemblyContract(request, assembly, context)
        projections = contract.projections
    if len(projections)*len(ss) > limits['projections']:
        raise InvalidRequest('budget produit scénarios-projections avant allocation')
    for label,key,default in [('materials','database_file','materials_base_v1.yaml'),
                              ('variant_rules','variant_rules_file','wood_variant_rules_v1.yaml')]:
        p = Path(context['config'].get('materials',{}).get(key,default))
        expected_path = p.resolve() if p.is_absolute() else (paths['config'].parent/p).resolve()
        if expected_path != Path(context['provenance']['files'][label]['path']):
            raise InvalidRequest('chemin DB ambigu: '+key)
        if expected_path.exists(): strict_input(expected_path)
    parsed = parse_scenarios(raw, kind, nominal, contract)
    # Validate every finite geometry and required native mesh bound with no TMM/mesh.
    geometry_plan = []; minimum = dict(segments=0, frequencies=0, frequency_segment_product=0, spectral_calls=0)
    for scenario in parsed['scenarios']:
        try:
            effective = perturb(nominal, parsed, scenario, kind)
        except ScenarioUnavailable as exc:
            for p in projections:
                geometry_plan.append(dict(scenario=scenario['id'],projection=p['id'],
                                          issue=dict(status='unresolved',reason=str(exc))))
            continue
        check_mask(nominal, effective, parsed, kind)
        for p, design, metadata, issue in geometries(kind, effective, context, projections):
            if design is not None and kind == 'fixed':
                check_mask(nominal, design.as_dict(), parsed, kind)
            geometry_plan.append(dict(scenario=scenario['id'], projection=p['id'], issue=issue,
                geometry_sha256=report.fingerprint(effective),
                piece_order=list(effective['pieces']) if kind == 'assembly' else None,
                profile_sha256=report.fingerprint(design.as_dict()) if design is not None else None,
                metadata_sha256=report.fingerprint(metadata)))
            if design is not None and any(c['observable'] in ('resonance_frequency','resonance_ratio') and
                                         not c['unsupported_reason'] for c in p['template'].criteria):
                estimate = _mesh_bound(design,p['template'])
                for k,v in estimate.items(): minimum[k] += v
    for k,v in minimum.items():
        if v > limits[k]: raise InvalidRequest('budget cumulé des grilles minimales: '+k)
    sources = dict(context['provenance']['files'])
    sources.update({k:_file_source(v) for k,v in paths.items()})
    sources['job'] = _file_source(job_path)
    if sources['job']['sha256'] != job_source['sha256'] or sources[request_key]['sha256'] != request_source['sha256']:
        raise InvalidRequest('entrée changée pendant lecture')
    files = {k:dict(name=Path(v['path']).name,sha256=v['sha256'],read=v['read']) for k,v in sources.items()}
    uncovered = []
    if raw['coverage']['kind'] == 'continuous': uncovered.append('JOB: continuum dimensionnel non couvert')
    if kind == 'assembly' and contract.coverage['kind'] != 'discrete':
        uncovered.append('ASSEMBLY_REQUEST: continuum de configurations non couvert')
    for p in projections:
        for c in p['template'].criteria:
            if c['unsupported_reason']: uncovered.append(p['id']+':'+c['id']+': '+c['unsupported_reason'])
    plan = dict(schema_version=SCHEMA, job=raw, nominal=nominal, native_request=request,
                original_config=context['config'], input_files=files, parsed=parsed,
                geometry=geometry_plan, budgets=limits, minimum_grid_cost=minimum,
                uncovered=uncovered, effective_models=[models(context,p['template'].options)[3] for p in projections],
                expected_hard={p['id']:[c['id'] for c in p['template'].criteria if c['role']=='hard'] for p in projections},
                provenance=report.provenance(), continuous_robustness_certified=False,
                metadata_rule='Positions et total_length_cm recalculés par la chaîne native; annotations libres conservées.',
                design_variables_policy='REQUEST historique validé au nominal; aucune optimisation ni borne de recherche imposée aux écarts.')
    # A bounded plan must leave room for immutable observations and terminal closure.
    plan_bytes = len(json.dumps(plan,allow_nan=False).encode())
    if plan_bytes > min(2*1024**2, limits['output_mib']*1024**2//8) or report._limit(plan) < 2*plan_bytes:
        raise InvalidRequest('budget plan/output avant allocation des résultats')
    report.verify_sources(LOADED_SOURCES)
    return dict(kind=kind, nominal=nominal, context=context, contract=contract,
                projections=projections, parsed=parsed, sources=sources, job_path=job_path), plan


summarize = report.summarize

class TracedProjectionEvaluator(ProjectionEvaluator):
    """Native evaluator with immutable completed-level evidence, no new solver."""
    def __init__(self,contract,budget,save):
        super().__init__(contract,budget)
        self.save = save
        self.completed_spectra = []

    def spectrum(self,design,step,h,subdivide=False):
        peaks,mesh = super().spectrum(design,step,h,subdivide)
        trace = dict(step_hz=step,h_cm=h,subdivision_uniform=subdivide,peaks=peaks,
                     segment_count=len(mesh.segments),mesh_sha256=report.fingerprint(mesh.as_dict()),
                     counters=self.budget.snapshot(),verified_criterion=False)
        self.completed_spectra.append(trace)
        self.save(trace,len(self.completed_spectra))
        return peaks,mesh


def execute(prepared, plan, output):
    """Synchronous worker body; immutable observations survive budget/interruption."""
    limits = dict(plan['budgets'], candidates=65, evaluations=4096)
    budget = Budget(limits); observations = []; interrupted = False
    expected = len(prepared['parsed']['scenarios'])*len(prepared['projections'])
    for scenario in prepared['parsed']['scenarios']:
        try:
            effective = perturb(prepared['nominal'],prepared['parsed'],scenario,prepared['kind'])
        except ScenarioUnavailable as exc:
            reason = str(exc)
            report.write_unavailable_scenario(output,scenario,reason,plan)
            generated = [(p,None,{},dict(status='unresolved',reason=reason)) for p in prepared['projections']]
        else:
            check_mask(prepared['nominal'],effective,prepared['parsed'],prepared['kind'])
            report.write_geometry(output,scenario['id'],effective,prepared['kind'],plan)
            generated = geometries(prepared['kind'],effective,prepared['context'],prepared['projections'])
        for p, design, metadata, issue in generated:
            contract = p['template']; payload = {}; reason = None
            if issue is not None:
                reason = issue['reason']
                rows = report.unavailable_rows(contract.criteria,reason)
            else:
                if prepared['kind'] == 'fixed':
                    check_mask(prepared['nominal'],design.as_dict(),prepared['parsed'],'fixed')
                evaluator = None
                try:
                    budget.consume(projections=1)
                    evaluator = TracedProjectionEvaluator(contract,budget,
                        lambda trace,level: report.write_spectral_trace(output,len(observations)+1,level,trace,plan))
                    payload = evaluator.evaluate_design(design,verify=True)
                    rows = payload.pop('criteria')
                except (BudgetExhausted, ValueError, ArithmeticError, MemoryError) as exc:
                    reason = type(exc).__name__+': '+str(exc)
                    interrupted = interrupted or isinstance(exc,(BudgetExhausted,MemoryError))
                    # Geometry obligations remain calculable when acoustic work stops.
                    rows = criterion_rows(contract,design,{},None,reason)
                    if evaluator is not None:
                        payload = dict(completed_spectra=evaluator.completed_spectra,
                                       effective_models=evaluator.effective,verified=False)
                report.write_profile(output,scenario['id'],p['id'],design.as_dict(),plan)
            observation = dict(scenario=scenario['id'],projection=p['id'],configuration=p['configuration'],
                nominal=scenario['nominal'],coefficients=scenario['coefficients'],
                geometry_status=issue['status'] if issue else 'valid',reason=reason,
                criteria=rows,acoustics=payload,geometry=metadata,effective_request=p['request'])
            observations.append(observation)
            report.write_observation(output,observation,len(observations),plan)
    result = dict(schema_version=SCHEMA,plan=plan,observations=observations,counters=budget.snapshot(),
                  **summarize(observations,plan,len(observations)==expected and not interrupted))
    return result


def worker(task):
    import resource
    execution_ready()
    if any(os.environ.get(k) != '1' for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')):
        raise ValueError('worker exige BLAS1 avant import')
    if (not 0 < resource.getrlimit(resource.RLIMIT_AS)[0] <= 768*1024**2 or
            not 0 < resource.getrlimit(resource.RLIMIT_CPU)[0] <= 175):
        raise ValueError('worker exige limites mémoire/CPU imposées par le parent')
    baseline = report.source_files()
    report.verify_sources(task['sources'])
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        prepared, plan = load_inputs(task['job'])
        if report.fingerprint(report.identity_plan(plan)) != task['identity']:
            raise ValueError('entrées/plan changés depuis préflight')
        result = execute(prepared,plan,Path(task['output']))
    finally:
        profiler.disable()
    root_prefix = str(ROOT) + os.sep
    executed = {'tools/tolerance_audit.py'}
    for entry in profiler.getstats():
        filename = getattr(entry.code,'co_filename','')
        if filename.startswith(root_prefix) and filename.endswith('.py'):
            executed.add(filename[len(root_prefix):])
    report.verify_sources(baseline)
    report.verify_sources(task['sources'])
    for label,source in prepared['sources'].items():
        if _file_source(Path(source['path']),optional=label=='variant_rules') != source:
            raise ValueError('entrée changée pendant calcul: '+label)
    result['plan']['provenance'] = report.provenance(executed)
    report.export_result(task['output'],result)
    return dict(ok=result['execution_complete'],status=result['status'],execution_complete=result['execution_complete'],
                sampled_hard_conforming=result['sampled_hard_conforming'],
                request_fully_covered=result['request_fully_covered'],counterexample_found=result['counterexample_found'])


def _collect_child(child, collected, deadline):
    """Reap only this Popen child; every retry shares one absolute deadline.

    Draining pipes and reaping the process are separate confirmations. A killed
    process can have exited while inherited pipe ends still prevent collection.
    """
    errors = []; reaped = False; closed = True; late = False
    if child is None:
        return dict(created=False,exit_code=None,reaped=False,collected=True,
                    pipes_closed=True), errors, False
    try:
        if child.poll() is None:
            child.kill()
        if not collected:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired('collect owned child',0.)
            # Keep half of the remaining collection budget for a final wait if
            # the killed child exits but an inherited pipe stays open.
            drain_timeout = remaining/2.
            drain_deadline = time.monotonic()+drain_timeout
            child.communicate(timeout=drain_timeout)
            collected = True
            if time.monotonic() >= drain_deadline:
                late = True
                errors.append('retour tardif de collecte des canaux')
        reaped = child.returncode is not None and child.poll() is not None
    except BaseException as exc:
        errors.append(type(exc).__name__+': '+str(exc))
    finally:
        for name in ('stdin','stdout','stderr'):
            stream = getattr(child,name,None)
            if stream is not None:
                try:
                    stream.close()
                    if not stream.closed:
                        raise OSError(name+' non fermé')
                except BaseException as exc:
                    closed = False
                    errors.append(name+': '+type(exc).__name__+': '+str(exc))
        if not reaped:
            remaining = deadline-time.monotonic()
            if remaining > 0:
                try:
                    code = child.wait(timeout=remaining)
                    reaped = code is not None and child.returncode == code and child.poll() == code
                except BaseException as exc:
                    errors.append(type(exc).__name__+': '+str(exc))
        late = late or time.monotonic() >= deadline
        if late:
            errors.append('budget cumulé de collecte/fermeture dépassé')
    return dict(created=True,exit_code=child.returncode,reaped=reaped,collected=collected,
                pipes_closed=closed), errors, late


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
    child = None; handlers = {}; response = None; started = time.monotonic(); stdout = stderr = ''
    collected = False; collection_signals = []
    try:
        for signum in (signal.SIGINT,signal.SIGTERM): handlers[signum] = signal.signal(signum,interrupt_run)
        timeout = min(COMPUTE_WALL_SECONDS,deadline-time.monotonic()-COLLECTION_WALL_SECONDS-CLOSURE_RESERVE_SECONDS)
        if timeout <= 0:
            raise subprocess.TimeoutExpired('budget public avant lancement',0.)
        child = subprocess.Popen([sys.executable,'-B','-m','tools.tolerance_audit','--worker'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            cwd=ROOT,env=env,preexec_fn=child_limits)
        compute_deadline = min(time.monotonic()+timeout,deadline-COLLECTION_WALL_SECONDS-CLOSURE_RESERVE_SECONDS)
        remaining = compute_deadline-time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired('budget public après lancement',0.)
        stdout,stderr = child.communicate(json.dumps(task,allow_nan=False),timeout=remaining)
        collected = True
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
