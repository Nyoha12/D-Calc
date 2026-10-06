"""Bounded assembly design using native R41 criteria, search and passive TMM.

``run`` is the supervised public API. ``execute`` is synchronous: its caller
must enforce POSIX 768 MiB / CPU175 s / wall180 s and BLAS1 before importing
NumPy. It publishes data only; only the supervising caller closes execution.
"""
from __future__ import annotations

import copy
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import json

from ..geometry.assemblies import Assembly, field_info, set_field
from ..optimization.assembly_contract import AssemblyContract, native_contract
from ..optimization.design_contract import read_request, InvalidRequest
from ..optimization.constrained_search import search, preference_key
from ..reporting import assembly_path as report
from .fixed_design import load_analysis_context, _file_source
from .design_pitch import models, strict_input, execution_ready, child_limits, RunInterrupted, interrupt_run
from .constrained_design import (Evaluator, mesh_for, extract_peaks, assign_modes, criterion_rows,
                                 input_impedance)

ROOT = Path(__file__).resolve().parents[2]
LOADED_SOURCES = report.source_files()
PUBLIC_WALL_SECONDS = 200.


class BudgetExhausted(Exception):
    pass


class Budget:
    def __init__(self, limits):
        self.limits = limits; self.start = time.monotonic()
        self.counts = {k:0 for k in ('candidates', 'evaluations', 'projections', 'spectral_calls',
                                    'frequencies', 'frequency_segment_product', 'segments')}

    def check(self):
        if time.monotonic() - self.start >= self.limits['seconds']:
            raise BudgetExhausted('temps global épuisé')
        # ru_maxrss is KiB on the supported Linux POSIX runner.
        import resource
        rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != 'darwin' else 1024**2)
        if rss_mib > self.limits['memory_mib']:
            raise BudgetExhausted('mémoire globale épuisée')

    def consume(self, **amounts):
        self.check()
        for k, v in amounts.items():
            if self.counts[k] + v > self.limits[k]:
                raise BudgetExhausted('budget global ' + k)
        for k, v in amounts.items():
            self.counts[k] += v

    def snapshot(self):
        return dict(self.counts, elapsed_seconds=time.monotonic() - self.start)


def track_modes(peaks, modes, previous=None):
    """Ordered native references; high-band neighbours do not rename low modes.

    Only the counting domain through each requested mode's upper window is
    compared during refinement. A low-domain count change remains ambiguous.
    """
    selected = assign_modes(peaks, modes)
    for selection in selected.values():
        if selection['peak'] is not None and selection['peak']['status'] != 'resolved':
            selection.update(status='unresolved', reason=selection['peak'].get('reason') or 'maximum non résolu')
    if previous is not None:
        for ident, mode in modes.items():
            order = mode['order']
            old = previous[order-1] if len(previous) >= order else None
            now = selected[ident]['peak']
            if old is None or now is None:
                selected[ident].update(status='unresolved', reason='mode absent au niveau précédent')
                continue
            lo, hi = old['basin_hz']
            before = sum(p['frequency_hz'] < lo for p in previous)
            after = sum(p['frequency_hz'] < lo for p in peaks)
            if before != after or not lo < now['frequency_hz'] < hi:
                selected[ident].update(status='unresolved', reason='entrée/perte/ambiguïté dans le domaine du mode demandé')
    return selected


class ProjectionEvaluator(Evaluator):
    def __init__(self, contract, budget):
        super().__init__(contract); self.budget = budget

    def spectrum(self, design, step, h, subdivide=False):
        c = self.contract
        count = sum(1 if s.is_uniform and not subdivide else
                    max(1 if s.is_uniform else 2, math.ceil(s.length_cm/h)) for s in design.segments)
        n = math.ceil((c.spectrum['max_hz'] - c.spectrum['min_hz'])/step) + 1
        if count > c.budgets['segments'] or n > c.budgets['frequencies'] or n*count > c.budgets['frequency_segment_product']:
            raise ValueError('budget maillage/fréquences avant allocation')
        self.budget.check()
        # Preflight the first grid before allocating even the mesh.
        for key, amount in [('frequencies', n), ('frequency_segment_product', n*count)]:
            if self.budget.counts[key] + amount > self.budget.limits[key]:
                raise BudgetExhausted('budget global avant maillage: ' + key)
        self.budget.consume(segments=count)
        mesh = mesh_for(design, h, subdivide)
        radius = design.segments[-1].d_out_cm/200
        def evaluate(frequencies):
            if len(frequencies) > c.budgets['frequencies'] or len(frequencies)*len(mesh.segments) > c.budgets['frequency_segment_product']:
                raise ValueError('budget raffinement avant TMM')
            self.budget.consume(spectral_calls=1, frequencies=len(frequencies),
                                frequency_segment_product=len(frequencies)*len(mesh.segments))
            return input_impedance(frequencies, mesh, c.context['material_db'], self.air,
                exit_radius_m=radius, loss_model=self.loss, radiation_model=self.radiation)
        return extract_peaks(evaluate, c.spectrum, step, c.budgets['frequencies']), mesh

    def evaluate_design(self, design, *, verify=False):
        c = self.contract; selected = {}; peaks = []; levels = []; estimates = {}; error = None
        acoustic = any(k['observable'] in ('resonance_frequency', 'resonance_ratio') and
                       not k['unsupported_reason'] for k in c.criteria)
        if acoustic:
            try:
                peaks, _ = self.spectrum(design, c.spectrum['step_hz'], c.spectrum['h_cm'])
                selected = track_modes(peaks, c.modes)
                if verify:
                    series = [selected]
                    for h, subdivision in ((c.spectrum['h_cm'], False), (c.spectrum['h_cm']/2, True)):
                        refined, mesh = self.spectrum(design, c.spectrum['final_step_hz'], h, subdivision)
                        local = track_modes(refined, c.modes, peaks)
                        series.append(local)
                        levels.append(dict(h_cm=h, subdivision_uniform=subdivision, peaks=refined,
                            selected_modes=local, segment_count=len(mesh.segments), mesh_sha256=report.fingerprint(mesh.as_dict())))
                    selected = copy.deepcopy(series[-1])
                    for ident in c.modes:
                        seq = [s[ident] for s in series]
                        if any(m['status'] != 'resolved' for m in seq):
                            selected[ident].update(status='unresolved', reason='mode absent/ambigu au raffinement')
                        else:
                            fs = [m['peak']['frequency_hz'] for m in seq]
                            estimates[ident] = max(abs(fs[1]-fs[0]), abs(fs[2]-fs[1])) + sum(m['peak']['frequency_estimate_hz'] for m in seq)
            except (ValueError, ArithmeticError) as exc:
                error = str(exc)
        rows = criterion_rows(c, design, selected, estimates if verify else None, error)
        for row, criterion in zip(rows, c.criteria):
            row.update(target_si=criterion['target_si'], lower_si=criterion['lower_si'],
                upper_si=criterion['upper_si'], tolerance_si=criterion['tolerance_si'],
                tolerance_dimension=criterion['tolerance_dimension'])
        if c.options['radiation_model'] != 'legacy' and 2*math.pi*c.spectrum['max_hz']*(design.segments[-1].d_out_cm/200)/self.air.c > 2:
            for row in rows:
                if row['observable'] in ('resonance_frequency', 'resonance_ratio') and row['status'] != 'unsupported':
                    row.update(status='out_of_domain', reason='radiation Silva: domaine déclaré dépasse |ka|=2')
        return dict(criteria=rows, peaks=peaks, selected_modes=selected, refinement_levels=levels,
                    effective_models=self.effective, physical_exit_radius_m=design.segments[-1].d_out_cm/200)


def _unresolved(contract, reason, status='unresolved'):
    return [dict(id=c['id'], projection=c['projection'], observable=c['observable'], role=c['role'],
        status='unsupported' if c['unsupported_reason'] else status, reason=c['unsupported_reason'] or reason,
        value_si=None, unit_si=None, error_cents=None, residual_normalized=None) for c in contract.criteria]


def load_inputs(config, assembly, request):
    report.verify_sources(LOADED_SOURCES)
    config_path = Path(config).resolve(strict=True)
    strict_input(config_path)
    context = load_analysis_context(config_path)
    for label, key, default in [('materials', 'database_file', 'materials_base_v1.yaml'),
                                ('variant_rules', 'variant_rules_file', 'wood_variant_rules_v1.yaml')]:
        p = Path(context['config'].get('materials', {}).get(key, default))
        expected = p.resolve() if p.is_absolute() else (config_path.parent/p).resolve()
        if expected != Path(context['provenance']['files'][label]['path']):
            raise InvalidRequest('chemin DB ambigu: ' + key)
    raw, assembly_source = read_request(assembly); req, request_source = read_request(request)
    physical = Assembly(raw, context['material_db'], context['config'])
    contract = AssemblyContract(req, physical, context)
    files = {k:dict(name=Path(v['path']).name, sha256=v['sha256'], read=v['read'])
             for k, v in context['provenance']['files'].items()}
    files.update(assembly=assembly_source, request=request_source)
    contract.input_files = files
    contract.source_paths = {k:Path(v['path']) for k,v in context['provenance']['files'].items()}
    contract.source_paths.update(assembly=Path(assembly).resolve(), request=Path(request).resolve())
    # Bound the worst possible physical lengths before any acoustic mesh allocation.
    max_mesh = 0
    for alt in contract.catalogue:
        candidate = copy.deepcopy(alt['base'])
        for v in contract.variables + contract.derived:
            for f in v.get('fields', [v.get('field')]):
                set_field(candidate, f, v['high'])
        # Mechanical impossibility is retained as a candidate rejection, not clamped.
        total = 0.
        for piece in candidate['pieces'].values():
            if piece['kind'] == 'tube':
                total += quantity_length(piece['length'])*100
            else:
                total += sum(quantity_length(s['length'])*100 for s in piece['segments'])
        for p in contract.projections:
            native = p['template']; h = native.spectrum['h_cm']/2
            count = math.ceil(total/h) + 2*sum(len(v.get('segments', [None])) for v in candidate['pieces'].values())
            n = math.ceil((native.spectrum['max_hz']-native.spectrum['min_hz'])/native.spectrum['final_step_hz'])+1
            if count > native.budgets['segments'] or n*count > native.budgets['frequency_segment_product']:
                raise InvalidRequest('pire maillage dépasse budget avant allocation')
            max_mesh = max(max_mesh, count)
    effective = [models(context, p['template'].options)[3] for p in contract.projections]
    plan = dict(contract.plan(), input_files=files, assembly=physical.raw, effective_models=effective,
                max_mesh_segments=max_mesh, provenance=report.provenance(),
                calculation_context=report.calculation_context(contract),
                limits=['Modèle nominal statique; aucune validation physique.',
                        'Continuum acoustique, jeu et incertitudes non certifiés.',
                        'Dimensions/liaisons physiques communes; projections R41 nominales tracées.'])
    json.dumps(plan, allow_nan=False)
    return contract, plan


def quantity_length(value):
    from ..optimization.design_contract import quantity
    return quantity(value, 'length')[0]


def execute(contract, plan, output):
    """Synchronous data producer; external supervision required (module docstring)."""
    output = Path(output); budget = Budget(contract.budgets); plan = copy.deepcopy(plan)
    calculation = report.calculation_context(contract)
    if plan['calculation_context'] != calculation:
        raise ValueError('contexte différent du plan')
    producer = plan['provenance']; history = []; alternatives = []; best = None; checkpoint_count = 0
    def check_sources():
        if report.calculation_context(contract) != calculation:
            raise ValueError('contexte modifié pendant calcul')
        if report.source_files() != producer['loaded_sources_sha256']:
            raise ValueError('sources chargées modifiées pendant calcul')
        report.verify_sources(plan.get('parent_sources_checked_sha256', {}))
        for name, path in contract.source_paths.items():
            current = _file_source(path, optional=name == 'variant_rules')
            if current['sha256'] != contract.input_files[name]['sha256']:
                raise ValueError('entrée modifiée pendant calcul: ' + name)
    def ranking(row):
        if row['search_feasible']:
            key = preference_key(contract, row)
            return (0, key is None, key or ())
        hard = [r for r in row['criteria'] if r['role'] == 'hard']
        unresolved = sum(r['status'] not in ('satisfied', 'violated') for r in hard)
        residual = max([abs(r['residual_normalized']) for r in hard if r.get('residual_normalized') is not None] or [0.])
        return (1, unresolved, residual)
    def remember(row):
        nonlocal best, checkpoint_count
        row['global_evaluation'] = len(history)+1
        row['evaluation'] = len(history)+1
        history.append(row)
        if best is None or ranking(row) < ranking(best) or (
                ranking(row) == ranking(best) and row.get('verified') and not best.get('verified')):
            best = copy.deepcopy(row)
            check_sources(); checkpoint_count += 1
            report.write_json(output / f'checkpoint_{checkpoint_count:04d}.json',
                report.checkpoint_record(contract, best, producer, budget.snapshot()),
                budget_bytes=contract.budgets['output_mib']*1024**2)
    def evaluate(values, alternative, verify=False):
        budget.consume(evaluations=1)
        row = dict(alternative=alternative['id'], variables_si=[float(x) for x in values],
            assembly=None, projections=[], bom=[], positions=[], criteria=_unresolved(contract, 'non calculé'),
            search_feasible=False, verified=False, geometry_certificate=None)
        try:
            assembly = contract.generate(values, alternative); row['assembly'] = assembly.raw
            # Every configuration's geometry is checked before preference/acoustics.
            generated = {p['configuration']:assembly.generate(p['configuration']) for p in contract.projections}
            row['bom'] = next(iter(generated.values()))['bom']
            row['geometry_certificate'] = dict(configurations=[g['geometry_certificate'] for g in generated.values()], paths=assembly.certify_paths())
            all_rows = []
            for p in contract.projections:
                budget.consume(projections=1)
                g = generated[p['configuration']]; design = g['design']
                native = native_contract(p['request'], dict(contract.context, design=design))
                evaluated = ProjectionEvaluator(native, budget).evaluate_design(design, verify=verify)
                for criterion in evaluated['criteria']:
                    criterion.update(id=p['id']+':'+criterion['id'], projection=p['id'])
                all_rows.extend(evaluated.pop('criteria'))
                row['criteria'] = all_rows + [r for r in _unresolved(contract, 'non calculé')
                                              if r['projection'] not in {x['id'] for x in row['projections']} | {p['id']}]
                projection = dict(evaluated, id=p['id'], configuration=p['configuration'],
                    physical_design=design.as_dict(), profile_sha256=report.fingerprint(design.as_dict()),
                    effective_request=copy.deepcopy(p['request']), parent_sha256=contract.parent_sha256,
                    projection_version='dcalc.assembly_projection.v1', component_ids=sorted(assembly.raw['pieces']),
                    geometry=g['geometry_certificate'], spans=g['spans'], lumen_volume_m3=g['lumen_volume_m3'],
                    lumen_volume_method=g['lumen_volume_method'])
                row['projections'].append(projection)
                row['positions'].extend(dict(position, configuration=p['configuration'], deployed_length_m=g['deployed_length_m']) for position in g['positions'])
                if not g['positions']:
                    row['positions'].append(dict(configuration=p['configuration'], block_id=None, q_m=None, overlap_m=None, deployed_length_m=g['deployed_length_m']))
            row['search_feasible'] = all(r['status'] == 'satisfied' for r in row['criteria'] if r['role'] == 'hard') and any(
                r['status'] in ('satisfied', 'violated') for r in row['criteria'])
            row['verified'] = verify
            budget.check()
        except BudgetExhausted:
            row['partial'] = True; remember(row)
            raise
        except (ValueError, ArithmeticError) as exc:
            row['criteria'] = _unresolved(contract, str(exc), 'out_of_domain'); row['reason'] = str(exc)
            row['bom'] = []; row['positions'] = []; row['projections'] = []
        remember(row)
        return row
    check_sources(); termination = 'catalogue_exhausted'
    try:
        for alternative in contract.catalogue:
            budget.consume(candidates=1)
            before = len(history)
            adapter = contract.search_adapter(contract.budgets['evaluations']-budget.counts['evaluations'],
                                              contract.budgets['seconds']-(time.monotonic()-budget.start))
            if adapter.budgets['evaluations'] <= 0:
                raise BudgetExhausted('évaluations épuisées')
            result = search(adapter, lambda values:evaluate(values, alternative))
            alternatives.append(dict(id=alternative['id'], termination=result['termination'],
                evaluations=len(history)-before, candidate_best=result['best'],
                status='admissible_witness' if result['best']['search_feasible'] else 'rejected_or_unresolved'))
        if best and best.get('assembly') and not best.get('reason'):
            alternative = next(a for a in contract.catalogue if a['id'] == best['alternative'])
            search_witness = copy.deepcopy(best)
            verified = evaluate(best['variables_si'], alternative, verify=True)
            # Final refinement owns conformity; retain the search witness separately.
            best = copy.deepcopy(verified)
        else:
            search_witness = best
    except BudgetExhausted as exc:
        termination = 'partial_budget: ' + str(exc); search_witness = best
    check_sources()
    sampled = bool(best and best.get('verified') and best['search_feasible'])
    fully_covered = bool(sampled and contract.coverage['kind'] == 'discrete' and
                         all(r['status'] in ('satisfied', 'violated') for r in best['criteria']))
    violated = bool(best and any(r['role'] == 'hard' and r['status'] == 'violated' for r in best['criteria']))
    partial = termination.startswith('partial')
    status = 'partial' if partial else 'conforming' if sampled and fully_covered else 'hard_conforming_partial_coverage' if sampled and contract.coverage['kind'] == 'discrete' else 'violated' if violated else 'not_fully_covered'
    result = dict(schema_version='dcalc.assembly_result.v1', plan=plan, status=status,
        best=best, search_witness=search_witness,
        history=[{k:v for k,v in row.items() if k not in ('assembly','projections','bom','positions','geometry_certificate')} for row in history],
        alternatives=[dict(a, candidate_best={k:v for k,v in a['candidate_best'].items() if k not in ('assembly','projections','bom','positions','geometry_certificate')}) for a in alternatives],
        termination=termination, counters=budget.snapshot(), sampled_hard_conforming=sampled,
        request_fully_covered=fully_covered, hard_conforming=sampled and contract.coverage['kind'] == 'discrete', continuous_acoustics_certified=False,
        discrete_catalogue_exhausted=len(alternatives) == len(contract.catalogue),
        continuous_design_impossibility_proven=False, global_optimum_proven=False)
    report.export_result(output, result, contract)
    return result


def worker(task):
    contract, plan = load_inputs(task['config'], task['assembly'], task['request'])
    parent = task['plan']
    report.verify_sources(parent['provenance']['loaded_sources_sha256'])
    if plan['calculation_context'] != parent['calculation_context'] or plan['input_files'] != parent['input_files']:
        raise ValueError('entrées/contexte modifiés depuis plan')
    plan['parent_sources_checked_sha256'] = parent['provenance']['loaded_sources_sha256']
    result = execute(contract, plan, Path(task['output']))
    return dict(ok=True, status=result['status'], sampled_hard_conforming=result['sampled_hard_conforming'],
                request_fully_covered=result['request_fully_covered'], hard_conforming=result['hard_conforming'], conforming=result['status'] == 'conforming')


def _run(config, assembly, request, output_dir, *, dry_run=False):
    output = report.new_destination(output_dir)
    contract, plan = load_inputs(config, assembly, request)
    if dry_run:
        return dict(ok=True, dry_run=True, output_created=False, plan=plan)
    execution_ready(); output.mkdir(parents=True, exist_ok=False)
    report.write_json(output/'plan.json', plan)
    task = dict(config=str(Path(config).resolve()), assembly=str(Path(assembly).resolve()),
                request=str(Path(request).resolve()), output=str(output), plan=plan)
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    handlers = {}; child = None; response = None; started = time.monotonic()
    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.signal(signum, interrupt_run)
        child = subprocess.Popen([sys.executable, '-B', '-m', 'tools.assembly_path', '--worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=ROOT, env=env, preexec_fn=child_limits)
        stdout, stderr = child.communicate(json.dumps(task, allow_nan=False), timeout=180)
        response = json.loads(stdout)
        response.update(child=dict(exit_code=child.returncode, reaped=True),
                        wall_seconds=time.monotonic()-started)
        response['ok'] = response.get('ok') is True and child.returncode == 0
        if response['ok']:
            response['manifest_sha256'] = report.file_sha256(output/'manifest.json')
        if stderr:
            response['diagnostic'] = stderr[-4000:]
        response['completion_protocol'] = report.COMPLETION_PROTOCOL
        report.write_json(output/'execution.json', response)
        report.write_json(output/'execution.closed.json',
                          dict(execution_sha256=report.file_sha256(output/'execution.json'), cancelled=False))
        # These synchronized files are only candidates. The public caller still
        # has fallible signal/alarm restoration to finish before committing.
        report.write_json(output/report.PREPARED_COMPLETION, report.completion_record(output))
        return response
    except (BaseException,) as exc:
        if child is not None and child.poll() is None:
            child.kill(); child.communicate()
        interrupted = isinstance(exc, (KeyboardInterrupt, RunInterrupted, subprocess.TimeoutExpired))
        response = dict(ok=False, status='interrupted' if interrupted else 'failed', reason=str(exc),
                        child=dict(exit_code=None if child is None else child.returncode, reaped=True),
                        wall_seconds=time.monotonic()-started)
        try:
            if (output/'execution.json').exists():
                report.write_json(output/'execution.cancelled.json',
                                  dict(response, execution_sha256=report.file_sha256(output/'execution.json')))
            else:
                report.write_json(output/'execution.json', response)
        except (OSError, ValueError):
            # Absence of terminal authority is sufficient, even on a full disk.
            # A failed diagnostic must not replace the original interruption.
            pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return response
    finally:
        try:
            if child is not None and child.poll() is None:
                child.kill(); child.communicate()
        finally:
            restore_error = None
            for signum, handler in handlers.items():
                try:
                    signal.signal(signum, handler)
                except BaseException as exc:
                    if restore_error is None:
                        restore_error = exc
            if restore_error is not None:
                raise restore_error


def run(config, assembly, request, output_dir, *, dry_run=False):
    """Public POSIX supervision: the whole call is bounded, including preflight.

    POSIX callers use the main thread with no pre-existing real-time alarm;
    execute() remains available under an independently supervised API host.
    Portable dry-run does not require the POSIX execution machinery.
    """
    if os.name != 'posix':
        if not dry_run:
            execution_ready()
        return _run(config, assembly, request, output_dir, dry_run=dry_run)
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('API supervisée: thread principal requis; execute exige supervision externe')
    if signal.getitimer(signal.ITIMER_REAL) != (0., 0.):
        raise ValueError('API supervisée: alarme active; utiliser execute sous supervision externe')
    previous = signal.signal(signal.SIGALRM, interrupt_run)
    started = time.monotonic()
    try:
        signal.setitimer(signal.ITIMER_REAL, PUBLIC_WALL_SECONDS)
        response = _run(config, assembly, request, output_dir, dry_run=dry_run)
    except RunInterrupted as exc:
        # Preflight can expire before a destination or child exists.
        return dict(ok=False, status='interrupted', reason=str(exc),
                    wall_seconds=time.monotonic()-started)
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0.)
        finally:
            signal.signal(signal.SIGALRM, previous)
    if response.get('ok') and not dry_run:
        output = Path(output_dir)
        # Terminal commit: all writes/fsyncs, child cleanup and handler/alarm
        # restorations have finished. No fallible cleanup or cancellation follows
        # this single no-overwrite link. Later events cannot undo its meaning.
        # The final directory entry has no power-loss durability guarantee.
        os.link(output/report.PREPARED_COMPLETION, output/'execution.completed.json',
                follow_symlinks=False)
    return response
