"""Bounded orchestration of native contracts, fresh fits and REGISTER-TARGET."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import threading
import time

from ..optimization import played_search as core
from ..optimization.assembly_contract import native_contract
from ..optimization.design_contract import read_request
from ..geometry.assemblies import set_field, InvalidAssembly
from ..optimization.design_contract import InvalidRequest
from ..nonlinear import register_target as rtcore
from ..nonlinear.passive_resonator import digest, _pairs, _constant
from ..reporting import played_search as report
from ..reporting import register_target as rtreport
from ..reporting import time_domain_reference as tdreport
from . import constrained_design as fixed
from . import assembly_path as assembly
from . import time_domain_reference as td
from . import register_target as rt
from .paired_onset import _case_context
from .design_pitch import models, execution_ready, child_limits

ROOT = Path(__file__).resolve().parents[2]
SLOTS = dict(config='@CONFIG', design='@DESIGN', model_in='@MODEL')
MIB = 1024**2


class BudgetStop(Exception):
    pass


class Budget:
    """Reserve full possible child expenditure before launch; never refund failures."""
    def __init__(self, limits, out, stop=lambda: False):
        self.limits = limits; self.out = Path(out); self.stop = stop; self.start = time.monotonic()
        self.counts = dict(candidates=0, fits=0, trajectories=0, steps_charged=0,
                           children=0, accepted_steps=0, static_units=0, reader_units=0)
        self.reserved = dict(fits=0, trajectories=0, steps_charged=0, children=0)
        self.witness = None

    def check(self, *, storage=True):
        if self.stop(): raise BudgetStop('signal_interrupted')
        if time.monotonic()-self.start >= self.limits['seconds']: raise BudgetStop('total_seconds')
        if storage and report.size(self.out) > self.limits['output_mib']*MIB: raise BudgetStop('total_output')

    def reserve(self, kind, seconds, output_bytes, steps=0):
        self.check()
        if seconds > self.limits['child_seconds'] or seconds+15 > self.limits['seconds']-(time.monotonic()-self.start):
            raise BudgetStop('child_does_not_fit_remaining_wall_budget')
        if report.size(self.out)+output_bytes+2*MIB > self.limits['output_mib']*MIB:
            raise BudgetStop('child_does_not_fit_remaining_storage')
        increments = {'fits': int(kind=='fit'), 'trajectories': int(kind=='trajectory'), 'steps_charged': steps}
        for key, amount in increments.items():
            cap = self.limits['steps' if key=='steps_charged' else key]
            if self.reserved[key]+amount > cap: raise BudgetStop('total_'+key)
        for key, amount in increments.items(): self.reserved[key] += amount
        self.reserved['children'] += 1

    def launched(self, kind, steps):
        self.counts['children'] += 1
        if kind=='fit': self.counts['fits'] += 1
        if kind=='trajectory': self.counts['trajectories'] += 1
        self.counts['steps_charged'] += steps
        if kind=='static': self.counts['static_units'] += 1
        if kind=='read': self.counts['reader_units'] += 1

    def snapshot(self):
        return dict(self.counts, reserved=dict(self.reserved), finalization_reserve_seconds=15, elapsed_seconds=time.monotonic()-self.start,
                    artifact_bytes=report.size(self.out),
                    step_accounting='full launched trajectory quota charged, including failed/uncommitted attempts; accepted steps separate')


def _source(path):
    p = report.safe_path(path)
    value, source = read_request(p)
    if report.file_sha256(p, maximum=2*MIB) != source['sha256']: raise ValueError('Input changed while reading')
    return value, dict(path=str(p.resolve()), sha256=source['sha256'])


def _relative(base, value):
    if type(value) is not str or not value.strip() or Path(value).is_absolute():
        raise ValueError('JOB references must be nonempty paths relative to JOB')
    return report.safe_path(base/value).resolve()


def load_inputs(job_path):
    """Pure plan/preflight: no TMM, fit, eigensolve, simulation or destination."""
    report.verify_sources(LOADED_SOURCES)
    raw, job_source = _source(job_path)
    unsupported = core.validate_job(raw)
    base = Path(job_source['path']).parent
    paths = dict(config=_relative(base, raw['config']), request=_relative(base, raw['input']['request']))
    kind = raw['input']['kind']; key = 'design' if kind=='fixed' else 'assembly'
    paths[key] = _relative(base, raw['input'][key])
    inputs = {'job': job_source}
    for name, path in paths.items():
        _, inputs[name] = _source(path)
    if kind=='fixed':
        contract, native_plan = fixed.load_inputs(paths['config'], paths['design'], paths['request'])
        configurations = ['nominal']; alternatives = ['nominal']
        initial_designs = [contract.generate(contract.initial)]
        max_length_cm = sum(s.length_cm for s in initial_designs[0].segments)
        for v in contract.variables+contract.derived:
            for f in v.get('fields', [v.get('field')]):
                if f.endswith('.length_cm'):
                    max_length_cm += v['high']*100-initial_designs[0].segments[int(f.split('.')[1])].length_cm
    else:
        contract, native_plan = assembly.load_inputs(paths['config'], paths['assembly'], paths['request'])
        configurations = list(contract.base['configurations'])
        alternatives = [a['id'] for a in contract.catalogue]
        initial_designs = [contract._assembly.generate(cid)['design'] for cid in configurations]
        max_length_cm = 0.
        for alt in contract.catalogue:
            possible = copy.deepcopy(alt['base'])
            for v in contract.variables+contract.derived:
                for f in v.get('fields', [v.get('field')]): set_field(possible, f, v['high'])
            length = 0.
            for piece in possible['pieces'].values():
                length += (assembly.quantity_length(piece['length']) if piece['kind']=='tube' else
                           sum(assembly.quantity_length(s['length']) for s in piece['segments']))*100
            max_length_cm = max(max_length_cm, length)
        if contract.coverage['kind'] != 'discrete': unsupported.append('assembly continuous coverage')
    for name, item in contract.context['provenance']['files'].items():
        if name=='design' and kind=='assembly': continue
        p = report.safe_path(item['path'], exists=item['read'])
        if item['read']:
            _, got = _source(p)
            if got['sha256'] != item['sha256']: raise ValueError('Native context bytes differ')
        inputs['native_'+name] = dict(path=str(p), sha256=item['sha256'])
    if any(c['role']=='preference' for c in contract.criteria): unsupported.append('static preferences')
    # Native option interpretation; scalar validation was done before geometry.
    _, _, _, effective = models(contract.context, raw['fit'])
    h = raw['fit']['h_cm']/2
    mesh_bound = math.ceil(max_length_cm/h)+2*sum(len(d.segments) for d in initial_designs)
    if mesh_bound > 4096 or mesh_bound*max(raw['fit']['fit_points'], raw['fit']['audit_points']) > 8_000_000:
        raise ValueError('Worst fit mesh/frequency budget before allocation')
    if raw['fit']['seconds'] > raw['budgets']['child_seconds']:
        raise ValueError('Fit child exceeds JOB child ceiling')
    scenarios = []; scalar_rows = len(contract.criteria)
    for s in raw['scenarios']:
        if s['configuration'] not in configurations: raise ValueError('Unknown generated configuration')
        path = _relative(base, s['template']); template, source = _source(path)
        rtcore.validate_request(template)
        if template['case']['kind'] != 'saved' or any(template['case'][k] != v for k,v in SLOTS.items()):
            raise ValueError('Native saved template requires explicit @CONFIG/@DESIGN/@MODEL binding slots')
        if digest(template['case']['recipe']) != digest(core.recipe(raw['fit'])):
            raise ValueError('Template/model recipe differs from declared fresh fit')
        if template['budgets']['child_seconds'] > raw['budgets']['child_seconds']:
            raise ValueError('Trajectory child exceeds JOB ceiling')
        if sum(x['steps'] for x in template['plateaus']) > raw['budgets']['steps']:
            raise ValueError('One trajectory exceeds total steps budget')
        inputs['template_'+s['id']] = source
        scenarios.append(dict(s, template=template, source=source,
            validation_context='native structure/protocol and recipe; generated CONFIG/DESIGN/model association pending fresh fit'))
        scalar_rows += sum(len(c['windows']) for c in template['criteria'])
    if scalar_rows > 512: raise ValueError('v1 <=512 scalar criterion rows per candidate')
    covered = {s['configuration'] for s in scenarios}
    if covered != set(configurations): raise ValueError('Each generated configuration requires a prescribed played scenario')
    plan = dict(schema='dcalc.played_search.plan.v1', job=raw, job_source=job_source, paths={k:str(v) for k,v in paths.items()},
                inputs=inputs, kind=kind, configurations=configurations, alternatives=alternatives,
                variables=contract.variables, initial=contract.initial, native_plan=native_plan,
                scenarios=scenarios, fit_effective=effective, max_fit_mesh_segments=mesh_bound,
                producer=report.provenance(), unsupported=unsupported,
                limits=['finite declared scenarios only; no continuous/physiological certificate',
                        'static preferences retained unsupported; feasibility only',
                        'model-dependent PHASE/state and native storage checks repeated after each fresh fit',
                        'global checkpoint is witness only; global resume unsupported',
                        'coordinate poll: declared variable/catalogue/step/direction order, no seed, no clamp',
                        'fit cache: exact generated DESIGN bytes, CONFIG/DB, entire fit recipe, producing sources within this execution'])
    plan['context_sha256'] = digest(plan)
    check_plan(plan)
    return contract, plan


def preflight(job_path, output_dir=None):
    if output_dir is not None:
        out = report.safe_path(output_dir, exists=False)
        if out.exists(): raise FileExistsError('Existing output refused, including dry-run')
    return load_inputs(job_path)[1]


def check_plan(plan):
    if plan['context_sha256'] != digest({k:v for k,v in plan.items() if k!='context_sha256'}):
        raise ValueError('Plan context changed')
    if plan['producer']['versions'] != report.versions(): raise ValueError('Versions changed')
    manifest = plan['producer']['loaded_sources_sha256']
    if not set(report.NEW_SOURCES) <= manifest.keys(): raise ValueError('Incomplete producer sources')
    report.verify_sources(manifest)
    for source in plan['inputs'].values():
        path = report.safe_path(source['path'], exists=source['sha256'] is not None)
        current = report.file_sha256(path, maximum=2*MIB) if path.is_file() else None
        if current != source['sha256']: raise ValueError('JOB/context input bytes changed')


def generated(contract, kind, values, alternative):
    if kind=='fixed':
        design = contract.generate(values); contract.check_locks(design.as_dict())
        return None, {'nominal': dict(design=design)}
    alt = next(a for a in contract.catalogue if a['id']==alternative)
    physical = contract.generate(values, alt)
    # Full mechanics, BOM, native profiles and locks, for every configuration.
    items = {cid: physical.generate(cid) for cid in physical.configurations}
    physical.certify_paths(); contract.check_locks(physical.raw, alt)
    return physical, items


def static_unit(task):
    check_plan(task['plan'])
    contract, _ = load_inputs(task['plan']['job_source']['path'])
    try:
        physical, items = generated(contract, task['plan']['kind'], task['values'], task['alternative'])
    except (InvalidAssembly, InvalidRequest) as exc:
        check_plan(task['plan'])
        return dict(ok=True,geometry_valid=False,rows=[dict(id='native_geometry',kind='static',
            configuration='all',scenario=None,window=None,observable='mechanics',role='hard',
            status='violated',value_si=None,unit_si=None,residual_si=None,error_cents=None,margin=None,
            margin_unit=None,violation_normalized=None,reason=str(exc))],details={},counts={},
            configurations={},assembly=None)
    rows = []; details = {}; counts = {}
    if physical is None:
        result = fixed.Evaluator(contract).verify(task['values'])
        rows = core.static_rows(result['criteria'], contract.criteria, 'nominal')
        details['nominal'] = result
    else:
        budget = assembly.Budget(contract.budgets)
        for p in contract.projections:
            budget.consume(projections=1, evaluations=1)
            cid = p['configuration']; design = items[cid]['design']
            local = native_contract(p['request'], dict(contract.context, design=design))
            result = assembly.ProjectionEvaluator(local, budget).evaluate_design(design, verify=True)
            rows.extend(core.static_rows(result['criteria'], local.criteria, cid))
            details[p['id']] = result
        counts = budget.snapshot()
    check_plan(task['plan'])
    return dict(ok=True, rows=rows, details=details, counts=counts,
                assembly=physical.raw if physical else None,
                configurations={cid:{**{k:v for k,v in item.items() if k!='design'},
                                     'design':item['design'].as_dict()} for cid,item in items.items()})


def worker(task):
    if task['kind']=='static': return static_unit(task)
    if task['kind']=='read':
        closed = rtreport.read_result(task['output'])
        if 'result' not in closed: return closed
        result = closed['result']
        return dict(ok=closed['ok'], status=closed['status'], criteria=result['analysis']['criteria'],
                    accepted_steps=result['accepted_steps'], context_sha256=result['plan']['context_sha256'])
    raise ValueError('Unknown worker unit')


def _limits():
    child_limits()
    resource.setrlimit(resource.RLIMIT_FSIZE, (8*MIB, 8*MIB))


def _input_chunks(value, active=None):
    """The default JSON wire format, without allocating an unbounded string."""
    if active is None: active = set()
    if isinstance(value, str):
        yield b'"'
        for i in range(0, len(value), 8192):
            yield json.dumps(value[i:i+8192], allow_nan=False)[1:-1].encode('ascii')
        yield b'"'
    elif isinstance(value, (dict, list, tuple)):
        ident = id(value)
        if ident in active: raise ValueError('Circular worker input')
        active.add(ident)
        try:
            mapping = isinstance(value, dict)
            yield b'{' if mapping else b'['
            for index, item in enumerate(value.items() if mapping else value):
                if index: yield b', '
                if mapping:
                    key, item = item
                    if not isinstance(key, str):
                        if key is not None and not isinstance(key, (int, float, bool)):
                            raise TypeError('Worker input keys must be JSON keys')
                        key = json.dumps(key, allow_nan=False)
                    yield from _input_chunks(key, active)
                    yield b': '
                yield from _input_chunks(item, active)
            yield b'}' if mapping else b']'
        finally:
            active.remove(ident)
    else:
        yield json.dumps(value, allow_nan=False).encode('ascii')


def launch(kind, task, out, budget, children, *, seconds, output_bytes, steps=0):
    """One direct child, no daemon or descendants. All numerical work is serial."""
    start = time.monotonic()
    def check(*, storage=True):
        budget.check(storage=storage)
        if time.monotonic()-start >= seconds: raise BudgetStop('child_timeout')

    # Count before reserving/allocating the input file. Even a single enormous
    # JSON string is escaped in bounded chunks. No full raw copy is needed.
    check()
    available = budget.limits['output_mib']*MIB-report.size(budget.out)-output_bytes-2*MIB
    input_bytes = 0
    for chunk in _input_chunks(task):
        check(storage=False)
        input_bytes += len(chunk)
        if input_bytes > 4*MIB: raise ValueError('Worker input quota')
        if input_bytes > available: raise BudgetStop('child_does_not_fit_remaining_storage')
    budget.reserve(kind, seconds, output_bytes+input_bytes, steps)
    index = budget.reserved['children']
    out = report.safe_path(out)
    log = out/f'child-{index:04d}.log'
    module = {'fit':'tools.time_domain_reference', 'trajectory':'tools.register_target'}.get(kind, 'tools.played_search')
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    child = None; reason = None; collection_seconds = 0.; operation_seconds = 0.
    try:
        # Both streams are files: a child that never reads cannot block sending.
        # Only this uniquely created temporary is removed by its context manager.
        with tempfile.NamedTemporaryFile(mode='w+b', dir=out, prefix='.worker-input-') as source, log.open('xb') as stream:
            try:
                written = 0
                for chunk in _input_chunks(task):
                    check(storage=False)
                    written += len(chunk)
                    if written > input_bytes: raise ValueError('Worker input changed during preparation')
                    source.write(chunk)
                if written != input_bytes: raise ValueError('Worker input changed during preparation')
                source.flush(); source.seek(0)
                check()
                # The input is now counted by size(); do not charge it twice.
                if report.size(budget.out)+output_bytes+2*MIB > budget.limits['output_mib']*MIB:
                    raise BudgetStop('child_does_not_fit_remaining_storage')
                child = subprocess.Popen([sys.executable, '-B', '-m', module, '--worker'], cwd=ROOT, env=env,
                                         stdin=source, stdout=stream, stderr=subprocess.STDOUT, preexec_fn=_limits)
                budget.launched(kind,steps)
                while True:
                    # A late successful exit never outranks a deadline or stop.
                    check()
                    if child.poll() is not None: break
                    time.sleep(min(.02, max(0., seconds-(time.monotonic()-start))))
            except BaseException as exc:
                reason = str(exc) if isinstance(exc, BudgetStop) else type(exc).__name__+': '+str(exc)
                raise
            finally:
                operation_seconds = time.monotonic()-start
                collected = time.monotonic()
                try:
                    if child is not None:
                        try:
                            if child.poll() is None: child.terminate()
                            child.wait(timeout=2)
                        except BaseException as exc:
                            # Even a deadline signal during collection must
                            # first kill/reap the owned child. Never wait forever.
                            try:
                                child.kill()
                                child.wait(timeout=max(0., 4-(time.monotonic()-collected)))
                            except BaseException as cleanup:
                                raise RuntimeError('Owned child reap not confirmed') from cleanup
                            if not isinstance(exc, subprocess.TimeoutExpired): raise
                finally:
                    collection_seconds = time.monotonic()-collected
            stream.flush(); os.fsync(stream.fileno())
    except BudgetStop as exc:
        reason = str(exc)
    except BaseException as exc:
        if reason is None: reason = type(exc).__name__+': '+str(exc)
        raise
    finally:
        receipt = dict(kind=kind, pid=child.pid if child else None,
                       exit_code=child.returncode if child else None,
                       launched=child is not None, reaped=child is None or child.returncode is not None,
                       seconds=time.monotonic()-start, operation_seconds=operation_seconds,
                       collection_seconds=collection_seconds, collection_limit_seconds=4,
                       timeout_or_error=reason, log=log.name, input_bytes=input_bytes,
                       steps_charged=steps if child else 0)
        children.append(receipt)
    try:
        if not log.exists(): raise ValueError('Child not launched')
        if log.stat().st_size > 8*MIB: raise ValueError('Child output quota')
        content = log.read_bytes()
        if len(content) > 8*MIB: raise ValueError('Child output quota')
        response = json.loads(content.decode().splitlines()[-1], object_pairs_hook=_pairs, parse_constant=_constant)
        json.dumps(response, allow_nan=False)
    except (ValueError, IndexError, UnicodeError) as exc:
        response = dict(ok=False, status='partial', reason='child_receipt_unavailable: '+str(exc))
    if reason is None:
        try: check()
        except BudgetStop as exc: reason = str(exc)
    receipt['timeout_or_error'] = reason
    if receipt['exit_code'] != 0 or reason:
        response.update(ok=False, status='partial', reason=reason or 'child_failed')
    return response, receipt


def read_fit(directory, fit, design, config):
    """TD has no bundle reader; verify its native receipt, then native association."""
    directory = Path(directory); value = report.read_json(directory/'reference.json')
    receipt = report.read_json(directory/'child.json')
    if (receipt['exit_code'] != 0 or receipt['reaped'] is not True or not value.get('ok') or
            value['status'] != 'numerically_accepted'):
        raise ValueError('Fresh native fit not accepted')
    if any(value['statuses'].get(k) != v for k,v in
           [('passivity','structural'),('fitter','converged'),('fidelity','accepted'),('mesh','accepted')]):
        raise ValueError('Fresh native fit gates failed')
    if any(value['options'].get(k) != v for k,v in fit.items()) or value['options'].get('model_in') is not None:
        raise ValueError('Fresh fit recipe changed or model reused externally')
    provenance = value['provenance']
    if provenance.get('inputs_and_sources_unchanged') is not True:
        raise ValueError('Fit input/source closure missing')
    report.verify_sources(provenance['sources_sha256'])
    if report.file_sha256(directory/'model.json') != provenance['executed_model_sha256']:
        raise ValueError('Fit executed model changed')
    meta, _, _ = _case_context(dict(id='generated', config=str(config), design=str(design),
        model_in=str(directory/'model.json'), recipe=core.recipe(fit)), directory/'unused-validation')
    return meta


def _unplayed(plan):
    rows = []
    for s in plan['scenarios']:
        native = rtcore.evaluate_criteria(s['template'], {})['criteria']
        for r in core.played_rows(s['template'], native, s['configuration'], s['id']):
            if r['status'] != 'unsupported': r.update(status='not_evaluated', reason='scenario_not_acquired')
            rows.append(r)
    return rows


def verify_candidate(plan, out, row, *, native_read):
    """Rebuild shared geometry and compare authoritative native scenario rows."""
    contract, _ = load_inputs(plan['job_source']['path'])
    if row.get('geometry_valid'):
        physical, items = generated(contract, plan['kind'], row['variables_si'], row['alternative'])
        for cid, info in row['configurations'].items():
            if report.read_json(report.local(out, info['design'])) != items[cid]['design'].as_dict():
                raise ValueError('Generated geometry/locks changed')
        if physical and report.read_json(report.local(out,row['assembly_file'])) != physical.raw:
            raise ValueError('Shared physical assembly changed')
    if len({u['scenario'] for u in row['units']}) != len(row['units']):
        raise ValueError('Duplicate scenario unit')
    observed = [r for r in row['criteria'] if r['kind']=='static']
    static_file = Path(out)/Path(row['file']).parent/'static.json'
    if row.get('geometry_valid'):
        static = report.read_json(static_file)
        if observed != static['rows']:
            raise ValueError('Static criteria evidence differs')
        definitions = ([("nominal",c['id'],c['role']) for c in contract.criteria] if plan['kind']=='fixed' else
                       [(p['configuration'],c['id'],c['role']) for p in contract.projections for c in p['template'].criteria])
        if [(r['configuration'],r['id'],r['role']) for r in observed] != definitions:
            raise ValueError('Missing static obligation')
    for s in plan['scenarios']:
        unit = next((u for u in row['units'] if u['scenario']==s['id']), None)
        if unit and unit['verified']:
            design = report.local(out,row['configurations'][s['configuration']]['design'])
            read_fit(report.local(out,unit['fit']), plan['job']['fit'], design, plan['paths']['config'])
            native_plan = report.read_json(report.local(out,unit['output'])/'plan.json')
            expected = instantiate(s['template'], plan['paths']['config'], design,
                                   report.local(out,unit['fit'])/'model.json')
            if native_plan['request'] != expected: raise ValueError('Scenario template/conditions changed')
            if native_read:
                closed = rtreport.read_result(report.local(out,unit['output']), unit['context_sha256'])
                if not closed['ok']: raise ValueError('Native scenario bundle unconfirmed')
                native = closed['result']['analysis']['criteria']
                expected_rows = core.played_rows(s['template'],native,s['configuration'],s['id'])
                if expected_rows != [r for r in row['criteria'] if r['scenario']==s['id']]:
                    raise ValueError('Native criterion rows differ')
        observed.extend(r for r in row['criteria'] if r['scenario']==s['id'])
    if observed != row['criteria']: raise ValueError('Criteria order/identity differs')
    expected = core.verdict(row['criteria'], units_complete=row['units_complete'], unsupported=plan['unsupported'])
    if any(row[k] != v for k,v in expected.items()): raise ValueError('Candidate verdict mismatch')
    if row['units_complete'] and (len(row['units']) != len(plan['scenarios']) or not all(u['verified'] for u in row['units']) or not row.get('geometry_valid') or
            {u['scenario'] for u in row['units'] if u['verified']} != {s['id'] for s in plan['scenarios']}):
        raise ValueError('Missing required acquired unit')


def instantiate(template, config, design, model):
    value = copy.deepcopy(template)
    value['case'].update(config=str(Path(config).resolve()), design=str(Path(design).resolve()), model_in=str(Path(model).resolve()))
    return value


def execute(plan, out, budget, children):
    out = Path(out); kw = dict(root=out, limit=plan['job']['budgets']['output_mib']*MIB)
    contract, _ = load_inputs(plan['job_source']['path']); cache = {}; acquired = []; sequence = 0
    current = None; best_saved = None; witness_saved = None
    def save(history=(), best=None, witness=None, decisions=()):
        nonlocal sequence, best_saved, witness_saved
        if history:
            budget.witness = report.summary(witness)
            best_saved = report.summary(best); witness_saved = report.summary(witness)
        sequence += 1
        report.checkpoint(out,sequence,dict(plan=plan, producer=plan['producer'], current=report.summary(current),
            best=best_saved, witness=witness_saved, history=[report.summary(r) for r in history],
            counters=budget.snapshot(), units=acquired, decisions=list(decisions)), **kw)

    def evaluate(values, alternative, index, stage):
        nonlocal current
        try: budget.check()
        except BudgetStop as exc: raise StopIteration(str(exc)) from exc
        budget.counts['candidates'] += 1
        folder = out/f'candidate-{index:03d}'; folder.mkdir()
        row = dict(candidate=index, stage=stage, variables_si=values, alternative=alternative, criteria=[], units=[],
                   configurations={}, geometry_valid=False, units_complete=False, errors=[], file=f'candidate-{index:03d}/candidate.json')
        current = row; save()
        try:
            check_plan(plan)
            seconds = min(plan['job']['budgets']['child_seconds'], contract.budgets['seconds'])
            static, receipt = launch('static', dict(kind='static', plan=plan, values=values, alternative=alternative),
                out, budget, children, seconds=seconds, output_bytes=8*MIB)
            report.write_json(folder/'static.json', static, **kw)
            if not static.get('ok'):
                row['errors'].append(static.get('reason', 'static child refused'))
                row['criteria'] = [dict(id=c['id'], kind='static', configuration=c.get('projection','nominal'),
                    scenario=None, role=c['role'], status='unresolved', reason='static_not_acquired',
                    violation_normalized=None) for c in contract.criteria]+_unplayed(plan)
            else:
                row['geometry_valid'] = static.get('geometry_valid',True); row['criteria'] = static['rows']+_unplayed(plan)
                if static['assembly'] is not None:
                    report.write_json(folder/'assembly.json', static['assembly'], **kw)
                    row['assembly_file'] = f'{folder.name}/assembly.json'
                for cid, item in static['configurations'].items():
                    name=f'design-{cid}.json'; report.write_json(folder/name,item['design'], **kw)
                    row['configurations'][cid] = dict(design=f'{folder.name}/{name}')
                    if static['assembly'] is not None:
                        report.write_json(folder/f'geometry-{cid}.json',{k:v for k,v in item.items() if k!='design'}, **kw)
                        report.table(folder/f'bom-{cid}.csv',item['bom'],
                            ['piece_id','kind','material_id','stock_length_m','outer_diameter_m','inner_diameter_m','material_volume_m3','mass_kg'], **kw)
                # Only a proved static hard violation screens expensive trajectories.
                screen = any(r['role']=='hard' and r['status'] in ('violated','out_of_domain') for r in static['rows'])
                if screen: row['errors'].append('proved_static_violation; played_not_evaluated')
                else:
                    for s in plan['scenarios']:
                        cid=s['configuration']; design=report.local(out,row['configurations'][cid]['design'])
                        key=digest(dict(design_bytes=report.file_sha256(design), fit=plan['job']['fit'],
                            context={k:v['sha256'] for k,v in plan['inputs'].items() if k.startswith('native_') and k!='native_design'},
                            sources=plan['producer']['loaded_sources_sha256']))
                        fit_info=cache.get(key)
                        if fit_info is None:
                            fit_dir=folder/f'fit-{cid}'
                            fp, ctx=td.preflight(plan['paths']['config'], design, fit_dir, **plan['job']['fit'])
                            # Child is the actual native CLI worker, producing its native files.
                            fit_dir.mkdir()
                            tdreport.write_json(fit_dir/'plan.json',fp)
                            value, child=launch('fit',fp,out,budget,children,seconds=fp['options']['seconds'],output_bytes=64*MIB)
                            value['child']=child
                            value.setdefault('statuses',tdreport.statuses())
                            value.setdefault('options',fp['options'])
                            value.setdefault('basis_completion',fp['basis_completion'])
                            report.write_json(fit_dir/'child.json',child, **kw)
                            tdreport.export(fit_dir,value)
                            check_plan(plan)
                            fit_info=dict(output=f'{folder.name}/{fit_dir.name}', ok=False, key=key)
                            try:
                                meta=read_fit(fit_dir,plan['job']['fit'],design,plan['paths']['config'])
                                fit_info.update(ok=True, model_sha256=meta['model_sha256'])
                            except (ValueError,OSError,KeyError) as exc:
                                fit_info['reason']=str(exc)
                            cache[key]=fit_info
                            acquired.append(dict(kind='fit',file=f'{fit_info["output"]}/child.json',
                                sha256=report.file_sha256(fit_dir/'child.json'), status='accepted' if fit_info['ok'] else 'rejected'))
                            save()
                        unit=dict(scenario=s['id'],configuration=cid,fit=fit_info['output'],verified=False)
                        row['units'].append(unit)
                        if not fit_info['ok']:
                            unit.update(status='fit_rejected',reason=fit_info.get('reason')); continue
                        fit_dir=report.local(out,fit_info['output'])
                        # Exact bytes/context/recipe are rechecked even on a cache hit.
                        read_fit(fit_dir,plan['job']['fit'],design,plan['paths']['config'])
                        experiment=instantiate(s['template'],plan['paths']['config'],design,fit_dir/'model.json')
                        ep=folder/f'plan-{s["id"]}.json'; report.write_json(ep,experiment, **kw)
                        dest=folder/f'experiment-{s["id"]}'
                        rp=rt.preflight(ep,dest).as_dict(); b=experiment['budgets']
                        steps=sum(p['steps'] for p in experiment['plateaus'])
                        dest.mkdir(); rtreport.write_json(dest/'plan.json',rp,budget_bytes=b['output_mib']*MIB)
                        value, child=launch('trajectory',dict(plan=rp),out,budget,children,
                            seconds=b['child_seconds'],output_bytes=b['output_mib']*MIB,steps=steps)
                        check_plan(plan)
                        budget.counts['accepted_steps'] += value.get('accepted_steps',0)
                        unit.update(output=f'{folder.name}/{dest.name}',status=value.get('status','partial'),
                                    context_sha256=rp['context_sha256'],child=child)
                        if (dest/'result.json').is_file():
                            rtreport.prepare_completion(dest,dict(ok=value.get('ok') is True and child['exit_code']==0,
                                status=value.get('status','partial'),children=[child],reason=value.get('reason')),b['output_mib']*MIB)
                            rtreport.publish_completion(dest)
                            closed,_=launch('read',dict(kind='read',output=str(dest)),out,budget,children,
                                            seconds=min(30,plan['job']['budgets']['child_seconds']),output_bytes=MIB)
                            unit.update(verified=closed.get('ok') is True,reader_status=closed.get('status'))
                            if 'criteria' in closed:
                                replacement=core.played_rows(s['template'],closed['criteria'],cid,s['id'])
                                row['criteria']=[r for r in row['criteria'] if r['scenario']!=s['id']]+replacement
                            acquired.append(dict(kind='trajectory',file=f'{unit["output"]}/execution.completed.json',
                                sha256=report.file_sha256(dest/'execution.completed.json'),status=unit['status']))
                        save()
                    # Put criteria back in declared order, independent of partial execution.
                    row['criteria']=[r for r in row['criteria'] if r['kind']=='static']+[
                        r for s in plan['scenarios'] for r in row['criteria'] if r['scenario']==s['id']]
                    row['units_complete']=(len(row['units'])==len(plan['scenarios']) and all(u['verified'] for u in row['units']))
        except BudgetStop as exc:
            row['errors'].append(str(exc)); row['stop_reason']=str(exc)
        except (ValueError,OSError,ArithmeticError,KeyError) as exc:
            row['errors'].append(type(exc).__name__+': '+str(exc))
        if not row['criteria']: row['criteria']=_unplayed(plan)
        # This also runs after a budget/error between scenarios.
        row['criteria']=[r for r in row['criteria'] if r['kind']=='static']+[
            r for s in plan['scenarios'] for r in row['criteria'] if r['scenario']==s['id']]
        row.update(core.verdict(row['criteria'],units_complete=row['units_complete'],unsupported=plan['unsupported']))
        # Static screened candidates retain a proved violation and no invented played penalty.
        report.write_json(folder/'candidate.json',row, **kw)
        current=None
        return row

    no_hard=not any(c['role']=='hard' for c in contract.criteria) and not any(
        c['role']=='hard' for s in plan['scenarios'] for c in s['template']['criteria'])
    search=core.poll(plan['variables'],plan['initial'],plan['alternatives'],plan['job']['search'],
                     plan['job']['budgets']['candidates'],evaluate,descriptive=no_hard,checkpoint=save)
    check_plan(plan)
    # Verify locks and native associations without additional fits or trajectory steps.
    for row in search['history']:
        budget.check()
        verify_candidate(plan,out,row,native_read=False)
    complete=search['termination'] in ('poll_exhausted','evaluation_only','descriptive_initials_only','feasible_witness_found','candidate_budget')
    if any(r.get('stop_reason') or (not r['units_complete'] and
           'proved_static_violation; played_not_evaluated' not in r['errors']) for r in search['history']): complete=False
    result=dict(schema=report.SCHEMA,plan=plan,status='complete' if complete else 'partial',
                conforming=search['witness'] is not None,search=search,counters=budget.snapshot(),
                checkpoint='witness',global_resume='unsupported',physical_validation=False)
    result['request_fully_covered'] = (bool(search['witness']) and not plan['unsupported'] and
        all(r['status'] in ('pass','satisfied','fail','violated') for r in search['witness']['criteria']))
    budget.check()
    return report.export(out,result, **kw)


def run(job_path, output_dir, *, dry_run=False):
    started=time.monotonic()
    plan=preflight(job_path,output_dir)
    if dry_run: return dict(ok=True,status='planned',output_created=False,plan=plan)
    execution_ready()
    if threading.current_thread() is not threading.main_thread(): raise ValueError('Main-thread supervision required')
    out=report.safe_path(output_dir,exists=False); out.mkdir(parents=True,exist_ok=False)
    kw=dict(root=out,limit=plan['job']['budgets']['output_mib']*MIB)
    report.write_json(out/'plan.json',plan, **kw)
    flags=[]; old={}; children=[]; result=None
    budget=Budget(plan['job']['budgets'],out,lambda:bool(flags)); budget.start=started
    alarm_handler=signal.getsignal(signal.SIGALRM); alarm_timer=signal.getitimer(signal.ITIMER_REAL)
    try:
        for sig in (signal.SIGINT,signal.SIGTERM): old[sig]=signal.signal(sig,lambda s,f:flags.append(s))
        def deadline(sig,frame):
            flags.append(sig)
            raise BudgetStop('total_seconds')
        signal.signal(signal.SIGALRM,deadline)
        remaining=max(.001,plan['job']['budgets']['seconds']-(time.monotonic()-started))
        signal.setitimer(signal.ITIMER_REAL,min(remaining,alarm_timer[0]) if alarm_timer[0]>0 else remaining)
        result=execute(plan,out,budget,children)
        check_plan(plan)
        budget.check()
        ok=result['status']=='complete' and not flags
        response=dict(ok=ok,status=result['status'],conforming=result['conforming'],output=str(out),
                      termination=result['search']['termination'],counters=result['counters'])
        if any(not child['reaped'] for child in children):
            raise RuntimeError('Owned child reap not confirmed; completion refused')
        report.prepare_completion(out,dict(ok=ok,children=children,status=result['status'],cancelled=bool(flags)), **kw)
    except BudgetStop as exc:
        response=dict(ok=False,status='partial',conforming=bool((result and result.get('conforming')) or budget.witness),output=str(out),reason=str(exc),
                      checkpoint='witness; inspect acquired checkpoint',counters=budget.snapshot())
        flags.append(signal.SIGALRM)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,alarm_handler)
        signal.setitimer(signal.ITIMER_REAL,max(.001,alarm_timer[0]-(time.monotonic()-started)) if alarm_timer[0]>0 else 0,alarm_timer[1])
        for sig, handler in old.items(): signal.signal(sig,handler)
    if flags:
        return dict(response,ok=False,status='partial',reason='signal_interrupted_no_terminal_commit')
    report.publish_completion(out)
    return response


LOADED_SOURCES=report.sources()
