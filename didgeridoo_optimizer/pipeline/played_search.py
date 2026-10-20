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
from ..optimization.design_contract import read_request, field_info
from .design_input import validate_design
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


def admit_starts(raw, base, contract, inputs):
    """Represent explicit profiles in the original contract, without repair."""
    spec = raw['search']['starts']

    def record(ident, source, supplied, native, values, stage):
        generated_design = contract.generate(values).as_dict()
        if digest(generated_design) != digest(native):
            raise ValueError('Start profile is not exactly representable by original Contract: '+ident)
        origin = dict(id=ident, source=source, variables_si=list(values),
                      generated_sha256=digest(generated_design), mode=spec['mode'])
        return dict(origin=origin, variables_si=list(values), stage=stage,
                    supplied_design=supplied, native_design=generated_design)

    supplied, source = _source(_relative(base, raw['input']['design']))
    initial = record('initial', source, supplied, contract.generate(contract.initial).as_dict(),
                     contract.initial, 'initial')
    profiles = []
    for item in spec['profiles']:
        supplied, source = _source(_relative(base, item['design']))
        if type(supplied) is not dict or type(supplied.get('segments')) is not list or not 1 <= len(supplied['segments']) <= 128:
            raise ValueError('Start DESIGN requires 1..128 physical sections')
        native = validate_design(supplied, contract.context['material_db'], contract.context['config']).as_dict()
        contract.check_locks(native)
        values = [field_info(native, v['fields'][0])[0] for v in contract.variables]
        profiles.append(record(item['id'], source, supplied, native, values, 'explicit_start'))
        inputs['start_'+item['id']] = source
    return dict(initial=initial, selected=([initial] if spec['include_initial'] else [])+profiles)


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
    scalar = 'backend' in raw['fit']
    h = raw['fit']['h_cm']/2
    mesh_bound = math.ceil(max_length_cm/h)+2*sum(len(d.segments) for d in initial_designs)
    if mesh_bound > 4096 or mesh_bound*(6528 if scalar else max(raw['fit']['fit_points'], raw['fit']['audit_points'])) > 8_000_000:
        raise ValueError('Worst fit mesh/frequency budget before allocation')
    if raw['fit']['seconds'] > raw['budgets']['child_seconds']:
        raise ValueError('Fit child exceeds JOB child ceiling')
    scenarios = []; scalar_rows = len(contract.criteria)
    for s in raw['scenarios']:
        if s['configuration'] not in configurations: raise ValueError('Unknown generated configuration')
        path = _relative(base, s['template']); template, source = _source(path)
        rtcore.validate_request(template)
        core.validate_steps_per_call(s, template)
        slots = dict(config='@CONFIG',design='@DESIGN',realization='@REALIZATION',evidence='@EVIDENCE') if scalar else SLOTS
        if template['case']['kind'] != ('saved_state' if scalar else 'saved') or any(template['case'][k] != v for k,v in slots.items()):
            raise ValueError('Native saved template requires explicit @CONFIG/@DESIGN/@MODEL binding slots')
        if digest(template['case']['recipe']) != digest(core.recipe(raw['fit'])):
            raise ValueError('Template/model recipe differs from declared fresh fit')
        if scalar and template['budgets']['child_seconds']>175: raise ValueError('Scalar trajectory worker ceiling 175 seconds')
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
                producer=report.provenance(scalar=True) if scalar else report.provenance(), unsupported=unsupported,
                limits=['finite declared scenarios only; no continuous/physiological certificate',
                        'static preferences retained unsupported; feasibility only',
                        'model-dependent PHASE/state and native storage checks repeated after each fresh fit',
                        'global checkpoint is witness only; global resume unsupported',
                        'coordinate poll: declared variable/catalogue/step/direction order, no seed, no clamp',
                        'fit cache: exact generated DESIGN bytes, CONFIG/DB, entire fit recipe, producing sources within this execution'])
    if 'starts' in raw['search']:
        plan['starts'] = admit_starts(raw, base, contract, inputs)
        plan['limits'][4] = 'coordinate poll: declared variable/step/direction order; no implicit starts or directions, no clamp'
        plan['limits'].append('explicit fixed starts first; shared coordinate polls in declared start/step/variable/direction order')
    if 'coupled_directions' in raw['search']:
        plan['coupled_directions'] = core.build_coupled_directions(contract.variables, plan['starts'])
        plan['limits'][4] = 'start_secants_v1: coupled directions first, then coordinates from the same fixed anchor; no clamp'
        plan['limits'][-1] = 'explicit fixed starts first; shared polls in declared start/step/secant/sign then variable/sign order'
    plan['context_sha256'] = digest(plan)
    if 'starts' in plan and sum(map(len, _input_chunks(plan))) > 3*MIB:
        raise ValueError('Seeded plan exceeds 3 MiB before destination/science')
    if 'starts' in plan and len((json.dumps(plan, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)+'\n').encode()) > 4*MIB:
        raise ValueError('Seeded plan JSON record exceeds 4 MiB before destination/science')
    check_plan(plan, reconstruct=False)
    return contract, plan


def preflight(job_path, output_dir=None):
    if output_dir is not None:
        out = report.safe_path(output_dir, exists=False)
        if out.exists(): raise FileExistsError('Existing output refused, including dry-run')
    return load_inputs(job_path)[1]


def batched(plan):
    return any('steps_per_call' in s for s in plan['job']['scenarios'])


def check_plan(plan, *, reconstruct=True):
    report.verify_sources(LOADED_SOURCES)
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
    raw, source = _source(plan['job_source']['path'])
    if digest(raw) != digest(plan['job']) or source != plan['job_source']:
        raise ValueError('Plan differs from original JOB bytes')
    if reconstruct and ('starts' in raw['search'] or 'starts' in plan or batched(plan)):
        _, fresh = load_inputs(source['path'])
        # Producer inventories record ambient imports, not the reader's imports.
        # Keep their evidence and verify it, but compare calculation inputs only.
        def calculation(value):
            value = copy.deepcopy(value)
            value.pop('producer'); value.pop('context_sha256')
            value['native_plan'].pop('provenance')
            return value
        native_producer = plan['native_plan']['provenance']
        report.verify_sources(native_producer['loaded_sources_sha256'])
        if native_producer['versions'] != report.versions(): raise ValueError('Native versions changed')
        if digest(calculation(plan)) != digest(calculation(fresh)):
            raise ValueError('Reconstructed starts/context differs from original sources')
    report.verify_sources(manifest)


def verify_search(plan, search, *, prefix=False, current=None, session=False):
    """Replay only scheduling/selection using recorded rows; no scientific work."""
    if 'starts' not in plan:
        return
    history = search['history']
    next_call = None

    def replay(values, alternative, index, stage, *, start_origin, coupled_direction=None):
        nonlocal next_call
        identity = dict(candidate=index, variables_si=values, alternative=alternative,
                        stage=stage, start_origin=start_origin)
        if coupled_direction is not None: identity['coupled_direction'] = coupled_direction
        if index > len(history):
            next_call = identity
            raise StopIteration('recorded_prefix')
        row = history[index-1]
        if ('coupled_direction' in row) != (coupled_direction is not None):
            raise ValueError('Candidate coupled direction presence differs')
        if digest({k:row.get(k) for k in identity}) != digest(identity):
            raise ValueError('Candidate start origin/order/vector differs from native sequence')
        return copy.deepcopy(row)

    no_hard = not any(c['role']=='hard' for c in plan['native_plan']['request']['criteria']) and not any(
        c['role']=='hard' for s in plan['scenarios'] for c in s['template']['criteria'])
    rebuilt = core.poll(plan['variables'], plan['initial'], plan['alternatives'], plan['job']['search'],
                        plan['job']['budgets']['candidates'], replay, descriptive=no_hard, starts=plan['starts'])
    if any(digest(rebuilt[k]) != digest(search[k]) for k in ('history', 'best', 'witness')):
        raise ValueError('Search acquired history/selection differs')
    if prefix:
        decisions = search['decisions']
        minimum = max((i+1 for i,d in enumerate(rebuilt['decisions']) if d.get('candidate') == len(history)), default=0)
        if len(decisions) < minimum or digest(decisions) != digest(rebuilt['decisions'][:len(decisions)]):
            raise ValueError('Checkpoint decisions differ from native sequence')
        if current is not None and (next_call is None or digest({k:current.get(k) for k in next_call}) != digest(next_call)):
            raise ValueError('Checkpoint current start differs from native sequence')
        if current is not None and ('coupled_direction' in current) != ('coupled_direction' in next_call):
            raise ValueError('Checkpoint current coupled direction presence differs')
    elif digest(rebuilt) != digest(search):
        # A native budget check may stop before the next evaluation is created.
        # Its reason is recorded by the scheduler, never replaced by a row.
        reason = search['termination']
        if rebuilt['termination'] != 'recorded_prefix' or reason not in (
                'signal_interrupted', 'total_seconds', 'total_output') + (('session_paused',) if session else ()):
            raise ValueError('Search decisions/termination differ from native sequence')
        for decision in rebuilt['decisions']:
            if decision.get('reason') == 'recorded_prefix': decision['reason'] = reason
        rebuilt['termination'] = reason
        if digest(rebuilt) != digest(search):
            raise ValueError('Search budget sequence differs')


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


def recorded_evaluator(base, *args):
    class Recording(base):
        def spectrum(self, design, step, h, subdivide=False):
            peaks,mesh=super().spectrum(design,step,h,subdivide)
            self.acquired_spectra.append(dict(peaks=copy.deepcopy(peaks),step_hz=step,h_cm=h,subdivision_uniform=subdivide))
            return peaks,mesh
    value=Recording(*args);value.acquired_spectra=[]
    return value


def verify_static_detail(detail,contract,design,kind):
    """Recompute native assignments and criterion arithmetic from acquired peaks."""
    record=detail['geometry_state_acquisition']
    if record['design_sha256']!=digest(design.as_dict()): raise ValueError('Static acquisition geometry differs')
    acoustic=any(c['observable'] in ('resonance_frequency','resonance_ratio') and not c['unsupported_reason'] for c in contract.criteria)
    spectra=record['spectra'];selected={};estimates={}
    complete=not acoustic
    if acoustic and len(spectra)==3:
        expected=[(contract.spectrum['step_hz'],contract.spectrum['h_cm'],False),
                  (contract.spectrum['final_step_hz'],contract.spectrum['h_cm'],False),
                  (contract.spectrum['final_step_hz'],contract.spectrum['h_cm']/2,True)]
        if [(x['step_hz'],x['h_cm'],x['subdivision_uniform']) for x in spectra]!=expected:
            raise ValueError('Static refinement protocol differs')
        initial=spectra[0]['peaks']
        series=[fixed.assign_modes(x['peaks'],contract.modes,len(initial)) if kind=='fixed' else
                assembly.track_modes(x['peaks'],contract.modes,None if i==0 else initial) for i,x in enumerate(spectra)]
        if len(detail['refinement_levels'])!=2: raise ValueError('Static refinement count')
        for level,acquired,modes in zip(detail['refinement_levels'],spectra[1:],series[1:]):
            if level['peaks']!=acquired['peaks'] or level['selected_modes']!=modes: raise ValueError('Static acquired peak assignment changed')
        selected=copy.deepcopy(series[-1])
        for ident in contract.modes:
            seq=[x[ident] for x in series];failures=[m for m in seq if m['status']!='resolved']
            if failures:
                reason=('mode perdu/ambigu lors du raffinement: '+ident+'; '+'; '.join(str(m['reason']) for m in failures)
                        if kind=='fixed' else 'mode absent/ambigu au raffinement')
                selected[ident].update(status='unresolved',reason=reason)
            else:
                fs=[m['peak']['frequency_hz'] for m in seq]
                estimates[ident]=max(abs(fs[1]-fs[0]),abs(fs[2]-fs[1]))+sum(m['peak']['frequency_estimate_hz'] for m in seq)
        if selected!=detail['selected_modes']: raise ValueError('Static final modes changed')
        if kind=='fixed' and estimates!=detail['convergence_estimates_hz']: raise ValueError('Static convergence changed')
        complete=True
    if len(spectra)>3 or (not acoustic and spectra): raise ValueError('Static acquisition count')
    native=fixed.criterion_rows(contract,design,selected,estimates)
    if kind=='assembly':
        for row,c in zip(native,contract.criteria):
            row.update(target_si=c['target_si'],lower_si=c['lower_si'],upper_si=c['upper_si'],tolerance_si=c['tolerance_si'],tolerance_dimension=c['tolerance_dimension'])
    if contract.options['radiation_model']!='legacy':
        air=models(contract.context,contract.options)[0]
        if 2*math.pi*contract.spectrum['max_hz']*(design.segments[-1].d_out_cm/200)/air.c>2:
            for row in native:
                if row['observable'] in ('resonance_frequency','resonance_ratio') and row['status']!='unsupported':
                    row.update(status='out_of_domain',reason='radiation Silva: domaine déclaré dépasse |ka|=2')
    if len(native)!=len(detail['criteria']): raise ValueError('Static criterion inventory')
    for expected,actual in zip(native,detail['criteria']):
        if (complete or expected['observable']=='geometry') and expected!=actual: raise ValueError('Static criterion arithmetic changed')
        if not complete and expected['observable']!='geometry' and actual['status'] in ('satisfied','violated'):
            raise ValueError('Static acoustic result without complete acquisition')


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
        evaluator=recorded_evaluator(fixed.Evaluator,contract) if 'backend' in task['plan']['job']['fit'] else fixed.Evaluator(contract)
        result = evaluator.verify(task['values'])
        if 'backend' in task['plan']['job']['fit']:
            result['geometry_state_acquisition']=dict(design_sha256=digest(items['nominal']['design'].as_dict()),spectra=evaluator.acquired_spectra)
        rows = core.static_rows(result['criteria'], contract.criteria, 'nominal')
        details['nominal'] = result
    else:
        budget = assembly.Budget(contract.budgets)
        for p in contract.projections:
            budget.consume(projections=1, evaluations=1)
            cid = p['configuration']; design = items[cid]['design']
            local = native_contract(p['request'], dict(contract.context, design=design))
            evaluator=recorded_evaluator(assembly.ProjectionEvaluator,local,budget) if 'backend' in task['plan']['job']['fit'] else assembly.ProjectionEvaluator(local,budget)
            result=evaluator.evaluate_design(design,verify=True)
            if 'backend' in task['plan']['job']['fit']:
                result['geometry_state_acquisition']=dict(design_sha256=digest(design.as_dict()),spectra=evaluator.acquired_spectra)
            rows.extend(core.static_rows(result['criteria'], local.criteria, cid))
            details[p['id']] = result
        counts = budget.snapshot()
    check_plan(task['plan'])
    return dict(ok=True, rows=rows, details=details, counts=counts,
                assembly=physical.raw if physical else None,
                configurations={cid:{**{k:v for k,v in item.items() if k!='design'},
                                     'design':item['design'].as_dict()} for cid,item in items.items()})


def worker(task):
    if task['kind']=='read_session_prefix':
        out=report.safe_path(task['output']); path=report.local(out,task['reference']['file'])
        if report.file_sha256(path)!=task['reference']['sha256']: raise ValueError('Session point changed')
        point=report.read_json(path)['payload']; plan=point['result']['plan']
        check_plan(plan); admitted=load_inputs(plan['job_source']['path']); rows=[]
        for row in point['result']['search']['history']:
            full=report.read_json(report.local(out,row['file']))
            if digest(report.summary(full))!=digest(row): raise ValueError('Session candidate summary differs')
            verify_candidate(plan,out,full,native_read=True,admitted=admitted,session=True)
            rows.append(full)
        verify_session_state(dict(plan=plan,point=point),out,rows);check_plan(plan)
        return dict(ok=True,status='prefix_verified',prefix_sha256=task['reference']['sha256'],candidates=len(rows))
    if task['kind']=='read_geometry':
        from ..reporting.geometry_state import read_result,read_payload
        reader=read_payload if task.get('pending') is True else read_result
        return reader(task['output'],config=task['config'],design=task['design'],recipe=task['recipe'])
    if task['kind']=='geometry':
        from .geometry_state import worker as geometry_worker
        return geometry_worker(task['plan'])
    if task['kind']=='static': return static_unit(task)
    if task['kind']=='read':
        closed = rtreport.read_result(task['output'])
        if 'result' not in closed: return closed
        result = closed['result']
        value=dict(ok=closed['ok'], status=closed['status'], criteria=result['analysis']['criteria'],
                    accepted_steps=result['accepted_steps'], context_sha256=result['plan']['context_sha256'])
        if result['plan']['request']['case']['kind']=='saved_state': value['read_confirmed']=True
        return value
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
    if task.get('kind')=='geometry': module='tools.played_search'
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    child = None; reason = None; collection_seconds = 0.; operation_seconds = 0.
    source = stream = None
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
                                         stdin=source, stdout=stream, stderr=subprocess.STDOUT, preexec_fn=(__import__('didgeridoo_optimizer.pipeline.geometry_state',fromlist=['limits']).limits if task.get('kind')=='geometry' else _limits))
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
        if task.get('kind') in ('geometry','read_geometry'):
            receipt.update(limit_seconds=seconds,collected=child is not None and child.returncode is not None,
                           pipes_closed=bool(source is not None and stream is not None and source.closed and stream.closed),
                           log_bytes=log.stat().st_size if log.exists() else 0)
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
    if 'backend' in fit:
        from .state_register_context import case_context
        from .geometry_state import verify_plan
        fp=report.read_json(Path(directory)/'plan.json');verify_plan(fp)
        if fp['recipe']!=fit: raise ValueError('Scalar recipe changed')
        for key,path in [('config',config),('design',design)]:
            if report.file_sha256(path)!=fp['inputs'][key]['sha256']: raise ValueError('Scalar geometry/context mismatch')
        case=dict(kind='saved_state',id='geometry',config=str(config),design=str(design),realization=str(Path(directory)/'realization'),evidence=str(Path(directory)/'evidence.json'),recipe=core.recipe(fit))
        return case_context(case)[0]
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


def batch_receipt(receipt, kind, quota=0):
    """Strict receipt types for the opt-in path, including unlaunched attempts."""
    if (receipt['kind'] != kind or type(receipt['launched']) is not bool or
            receipt['reaped'] is not True or type(receipt['steps_charged']) is not int or
            receipt['steps_charged'] != (quota if receipt['launched'] else 0) or
            (type(receipt['exit_code']) is not int if receipt['launched'] else receipt['exit_code'] is not None)):
        raise ValueError('Batch child/reader receipt types or charge differ')


def batch_reader_response(out, reader):
    """Read at most the native 8 MiB log ceiling, including a growing file."""
    path = report.local(out, reader['log'])
    if path.stat().st_size > 8*MIB:
        raise ValueError('Batch reader log quota')
    with path.open('rb') as stream:
        content = stream.read(8*MIB+1)
    if len(content) > 8*MIB:
        raise ValueError('Batch reader log quota')
    try:
        response = json.loads(content.decode().splitlines()[-1], object_pairs_hook=_pairs, parse_constant=_constant)
        json.dumps(response, allow_nan=False)
    except (ValueError, IndexError, UnicodeError, RecursionError) as exc:
        raise ValueError('Batch reader log malformed or empty') from exc
    if type(response) is not dict:
        raise ValueError('Batch reader response must be an object')
    return response


def verify_batch_unit(scenario, unit, out, expected, *, native_read):
    """Tie explicit call quotas to native evidence, including partial attempts."""
    core.validate_steps_per_call(scenario, expected)
    total = sum(p['steps'] for p in expected['plateaus'])
    accepted = 0; previous = None; stopped = False; last = None; last_output = None
    segments = unit.get('segments', []); pins = {}; closed = None
    for segment in segments:
        if stopped or accepted == total:
            raise ValueError('Batch segment after terminal/failed attempt')
        quota = min(scenario.get('steps_per_call',expected['budgets']['chunk_steps']), total-accepted)
        for key, lo, hi in [('quota', quota, quota), ('accepted_before', accepted, accepted),
                            ('accepted_after', accepted, accepted+quota)]:
            rtcore.integer(segment[key], key, lo, hi)
        local = report.local(out, segment['output'])
        pins[str(local/'plan.json')] = rtreport.file_sha256(local/'plan.json')
        rp = report.read_json(local/'plan.json')
        rt.check_inputs(rp)
        rtcore.integer(rp['steps_this_run'], 'native quota', quota, quota)
        rtcore.integer(rp['accepted_before'], 'native before', accepted, accepted)
        if (digest(rp['request']) != digest(expected) or rp['steps_this_run'] != quota or
                rp['accepted_before'] != accepted or rp['resume'] != previous):
            raise ValueError('Batch native plan/quota/resume differs')
        child = segment['child']
        batch_receipt(child, 'trajectory', quota)
        stopped = child['exit_code'] != 0 or bool(child['timeout_or_error']) or child['launched'] is not True
        after = segment['accepted_after']
        if not (local/'result.json').is_file():
            if after != accepted or 'reader' in segment or unit['verified']:
                raise ValueError('Batch progress without native result')
            stopped = True
            continue
        result = report.read_json(local/'result.json')
        if child['launched'] is not True:
            raise ValueError('Batch native result without launched child')
        pins[str(local/'result.json')] = rtreport.file_sha256(local/'result.json')
        rtcore.integer(result['accepted_steps'], 'native after', after, after)
        rtcore.integer(result['new_steps_this_run'], 'native new steps', after-accepted, after-accepted)
        if (digest(result['plan']) != digest(rp) or result['accepted_steps'] != after or
                result['new_steps_this_run'] != after-accepted):
            raise ValueError('Batch result/segment bounds differ')
        chain = rtreport.inspect_checkpoint(rtreport.verify_reference(result['checkpoint']), expected_request=expected)
        if (chain[-1][1]['context_sha256'] != rp['context_sha256'] or
                chain[-1][1]['experiment']['payload']['accepted_steps'] != after):
            raise ValueError('Batch checkpoint bounds differ')
        tip = chain[-1][1]['experiment']['payload']
        if digest(result['sums']) != digest(tip['sums']) or digest(result['events']) != digest(tip['events']):
            raise ValueError('Batch result/checkpoint ledger differs')
        if (local/'execution.completed.json').exists() or 'reader' in segment:
            pins.update(report.batch_artifact_pins(local, result, child))
        reader = segment.get('reader')
        if reader is None:
            if unit['verified']: raise ValueError('Batch reader missing')
            stopped = True
        else:
            batch_receipt(reader, 'read')
            stopped = (stopped or reader['exit_code'] != 0 or bool(reader['timeout_or_error']) or
                       reader['launched'] is not True)
            if not stopped:
                response = batch_reader_response(out, reader)
                if response.get('status') == 'unconfirmed':
                    stopped = True
                elif (response.get('read_confirmed') is not True or type(response.get('accepted_steps')) is not int or
                        response.get('accepted_steps') != after or
                        response.get('context_sha256') != rp['context_sha256']):
                    raise ValueError('Batch fresh reader proof differs')
        stopped = stopped or after <= accepted or result.get('reason') is not None
        if after != accepted+quota and not stopped:
            raise ValueError('Batch unexplained loss of progress')
        accepted = after; previous = result['checkpoint']; last = rp; last_output = segment['output']
    if unit['verified'] and (not segments or accepted != total or stopped):
        raise ValueError('Batch unit falsely complete')
    if unit.get('output') and (not segments or unit['output'] != last_output or
            last is None or unit['context_sha256'] != last['context_sha256']):
        raise ValueError('Batch final locator/context differs')
    if native_read and last_output is not None:
        closed = rtreport.read_result(report.local(out, last_output), last['context_sha256'])
        if 'result' not in closed:
            raise ValueError('Batch native evidence unconfirmed')
        if unit['verified'] and not closed['ok']:
            raise ValueError('Batch final native completion unconfirmed')
    for path, sha in pins.items():
        if rtreport.file_sha256(path) != sha:
            raise ValueError('Batch artifact changed during reading')
    return closed


def verify_batch_counters(plan, rows, counters, children, *, force=False):
    """Reconcile common expenditure with receipts, without resetting per start."""
    if not batched(plan) and not force: return
    expected = dict(candidates=len(rows), children=0, fits=0, trajectories=0,
                    steps_charged=0, accepted_steps=0, static_units=0, reader_units=0)
    receipts = []
    for row in rows:
        for unit in row['units']:
            for segment in unit.get('segments', []):
                receipts.append(segment['child'])
                expected['accepted_steps'] += segment['accepted_after']-segment['accepted_before']
                if 'reader' in segment and segment['reader'] not in children:
                    raise ValueError('Batch reader absent from execution')
    trajectories = [c for c in children if c['kind'] == 'trajectory']
    if sorted(map(digest, receipts)) != sorted(map(digest, trajectories)):
        raise ValueError('Batch trajectory receipts differ from execution')
    for child in children:
        rtcore.integer(child['steps_charged'], 'charged receipt', 0, rtcore.MAX_STEPS)
        if child['launched'] is not True: continue
        expected['children'] += 1
        expected['steps_charged'] += child['steps_charged']
        key = dict(fit='fits', trajectory='trajectories', static='static_units', read='reader_units')[child['kind']]
        expected[key] += 1
    for key, value in expected.items():
        if type(counters[key]) is not int or counters[key] != value:
            raise ValueError('Batch counter differs: '+key)
    reserved = dict(fits=sum(c['kind']=='fit' for c in children), trajectories=len(trajectories),
                    children=len(children), steps_charged=sum(s['quota'] for r in rows
                    for u in r['units'] for s in u.get('segments', [])))
    for key in ('fits', 'trajectories', 'steps_charged', 'children'):
        value = counters['reserved'][key]
        cap = plan['job']['budgets'].get('steps' if key == 'steps_charged' else key)
        if type(value) is not int or value != reserved[key] or (cap is not None and value > cap):
            raise ValueError('Batch reserved counter differs: '+key)


def verify_candidate(plan, out, row, *, native_read, admitted=None, session=False):
    """Rebuild shared geometry and compare authoritative native scenario rows."""
    if 'starts' in plan:
        if admitted is None: check_plan(plan)
        origins = [s['origin'] for s in plan['starts']['selected']]
        if digest(row.get('start_origin')) not in [digest(origin) for origin in origins]:
            raise ValueError('Candidate has no declared start origin')
    contract, fresh = load_inputs(plan['job_source']['path']) if admitted is None else admitted
    if 'backend' in plan['job']['fit']:
        for key in ('job','job_source','paths','inputs','kind','configurations','alternatives','variables','initial','scenarios','fit_effective','max_fit_mesh_segments','unsupported'):
            if plan[key]!=fresh[key]: raise ValueError('Reconstructed search context differs: '+key)
    if row.get('geometry_valid'):
        physical, items = generated(contract, plan['kind'], row['variables_si'], row['alternative'])
        for cid, info in row['configurations'].items():
            saved_design = report.read_json(report.local(out, info['design']))
            expected_design = items[cid]['design'].as_dict()
            if (digest(saved_design) != digest(expected_design) if 'starts' in plan else saved_design != expected_design):
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
        if 'backend' in plan['job']['fit']:
            recomputed=[]
            projections=[('nominal',contract,items['nominal']['design'])] if plan['kind']=='fixed' else [
                (p['id'],native_contract(p['request'],dict(contract.context,design=items[p['configuration']]['design'])),items[p['configuration']]['design']) for p in contract.projections]
            for ident,local,design_obj in projections:
                detail=static['details'][ident]
                verify_static_detail(detail,local,design_obj,plan['kind'])
                cid='nominal' if plan['kind']=='fixed' else next(p['configuration'] for p in contract.projections if p['id']==ident)
                recomputed.extend(core.static_rows(detail['criteria'],local.criteria,cid))
            if recomputed!=observed: raise ValueError('Static criterion export changed')

        definitions = ([("nominal",c['id'],c['role']) for c in contract.criteria] if plan['kind']=='fixed' else
                       [(p['configuration'],c['id'],c['role']) for p in contract.projections for c in p['template'].criteria])
        if [(r['configuration'],r['id'],r['role']) for r in observed] != definitions:
            raise ValueError('Missing static obligation')
    for s in plan['scenarios']:
        batch_closed = None
        unit = next((u for u in row['units'] if u['scenario']==s['id']), None)
        if unit and (batched(plan) or session):
            design = report.local(out,row['configurations'][s['configuration']]['design'])
            expected = instantiate(s['template'],plan['paths']['config'],design,
                                   report.local(out,unit['fit'])/'model.json')
            batch_closed = verify_batch_unit(s,unit,out,expected,native_read=native_read)
        if unit and unit['verified']:
            design = report.local(out,row['configurations'][s['configuration']]['design'])
            read_fit(report.local(out,unit['fit']), plan['job']['fit'], design, plan['paths']['config'])
            native_plan = report.read_json(report.local(out,unit['output'])/'plan.json')
            expected = instantiate(s['template'], plan['paths']['config'], design,
                                   report.local(out,unit['fit'])/'model.json')
            if native_plan['request'] != expected: raise ValueError('Scenario template/conditions changed')
            if native_read:
                if 'backend' in plan['job']['fit'] and not session:
                    from ..reporting.geometry_state import read_result as read_geometry
                    read_geometry(report.local(out,unit['fit']),config=plan['paths']['config'],design=design,recipe=plan['job']['fit'])
                closed = batch_closed if batched(plan) or session else rtreport.read_result(report.local(out,unit['output']), unit['context_sha256'])
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
    if 'starts' in plan and admitted is None: check_plan(plan)


def instantiate(template, config, design, model):
    value = copy.deepcopy(template)
    if value['case']['kind']=='saved_state':
        base=Path(model).resolve().parent
        value['case'].update(config=str(Path(config).resolve()),design=str(Path(design).resolve()),realization=str(base/'realization'),evidence=str(base/'evidence.json'))
    else:
        value['case'].update(config=str(Path(config).resolve()), design=str(Path(design).resolve()), model_in=str(Path(model).resolve()))
    return value


def fit_cache_key(plan, design):
    return digest(dict(design_bytes=report.file_sha256(design), fit=plan['job']['fit'],
        context={k:v['sha256'] for k,v in plan['inputs'].items() if k.startswith('native_') and k!='native_design'},
        sources=plan['producer']['loaded_sources_sha256']))


def state_trajectory(ep,dest,experiment,out,budget,children,plan,*,steps_per_call=None,progress=None):
    """Explicit native partial calls, preserving request and accepted checkpoints."""
    b=experiment['budgets'];total=sum(p['steps'] for p in experiment['plateaus'])
    accepted=0;resume=None;segments=[];unit=dict(verified=False,status='partial')
    if steps_per_call is not None:
        core.validate_steps_per_call(dict(steps_per_call=steps_per_call),experiment)
        if progress is not None:
            unit=progress
            unit.update(verified=False,status='partial')
        unit['segments']=segments
    while accepted<total:
        local=dest if not segments else dest.with_name(dest.name+f'-resume-{len(segments):03d}')
        quota=min(b['chunk_steps'] if steps_per_call is None else steps_per_call,total-accepted)
        rp=rt.preflight(ep,local,resume=resume,steps_this_run=quota).as_dict()
        if rp['accepted_before']!=accepted: raise ValueError('Native resume counter mismatch')
        local.mkdir();rtreport.write_json(local/'plan.json',rp,budget_bytes=b['output_mib']*MIB)
        launched_before=len(children)
        try:
            value,child=launch('trajectory',dict(plan=rp),out,budget,children,seconds=min(175,b['child_seconds']),output_bytes=b['output_mib']*MIB,steps=quota)
        except (BudgetStop,ValueError,OSError) as exc:
            if steps_per_call is not None and len(children)>launched_before:
                got=accepted
                if (local/'result.json').is_file():
                    got=rtreport.read_json(local/'result.json')['accepted_steps']
                    rtcore.integer(got,'partial accepted',accepted,accepted+quota)
                    unit.update(output=str(local.relative_to(out)),context_sha256=rp['context_sha256'])
                budget.counts['accepted_steps']+=got-accepted
                segments.append(dict(output=str(local.relative_to(out)),accepted_before=accepted,
                    accepted_after=got,quota=quota,child=children[-1]))
                unit['reason']=str(exc)
            raise
        segment=dict(output=str(local.relative_to(out)),accepted_before=accepted,accepted_after=accepted,quota=quota,child=child)
        if steps_per_call is not None:
            segments.append(segment)
            batch_receipt(child, 'trajectory', quota)
        check_plan(plan)
        if steps_per_call is not None and not (local/'result.json').is_file():
            unit.update(reason='No collected native result');break
        got=value.get('accepted_steps',accepted)
        if type(got) is not int or not accepted<=got<=accepted+quota: raise ValueError('Native accepted counter outside charged quota')
        budget.counts['accepted_steps']+=got-accepted
        segment['accepted_after']=got
        if not (local/'result.json').is_file():
            unit.update(reason='No collected native result');break
        result=rtreport.read_json(local/'result.json')
        if steps_per_call is not None:
            rtcore.integer(result['accepted_steps'], 'native accepted result', got, got)
        rtreport.prepare_completion(local,dict(ok=value.get('ok') is True and child['exit_code']==0,
            status=value.get('status','partial'),children=[child],reason=value.get('reason')),b['output_mib']*MIB)
        rtreport.publish_completion(local)
        if steps_per_call is not None:
            unit.update(output=str(local.relative_to(out)),context_sha256=rp['context_sha256'],status=value.get('status','partial'))
        readers_before=len(children)
        try:
            closed,reader=launch('read',dict(kind='read',output=str(local)),out,budget,children,
                seconds=min(175,plan['job']['budgets']['child_seconds']),output_bytes=MIB)
        except (BudgetStop,ValueError,OSError) as exc:
            if steps_per_call is not None:
                if len(children)>readers_before: segment['reader']=children[-1]
                unit['reason']=str(exc)
            raise
        segment['reader']=reader
        if steps_per_call is not None:
            batch_receipt(reader, 'read')
        if steps_per_call is None: segments.append(segment)
        unit.update(output=str(local.relative_to(out)),context_sha256=rp['context_sha256'],status=value.get('status','partial'),segments=segments)
        if child['exit_code']!=0 or child['timeout_or_error'] or reader['exit_code']!=0 or reader['timeout_or_error'] or closed.get('status')=='unconfirmed':
            unit.update(reason='Native child/reader unconfirmed');break
        # Partial native read is confirmed by its full semantic result, despite ok=False.
        if steps_per_call is not None and type(closed.get('accepted_steps')) is not int:
            raise ValueError('Batch reader accepted_steps must be an integer')
        if closed.get('accepted_steps')!=got or got<=accepted:
            unit.update(reason='No confirmed native progress');break
        if steps_per_call is not None and got!=accepted+quota and result.get('reason') is None:
            unit.update(reason='Native unexplained loss of progress');break
        accepted=got
        if accepted==total:
            unit.update(verified=closed.get('ok') is True,criteria=closed.get('criteria',[]));break
        if result.get('reason') is not None:
            unit.update(reason='Native interruption/budget: '+str(result['reason']));break
        resume=result['checkpoint']['path']
    return unit


def execute(plan, out, budget, children, *, session=None):
    out = Path(out); kw = dict(root=out, limit=plan['job']['budgets']['output_mib']*MIB)
    contract, _ = load_inputs(plan['job_source']['path']); cache = {}; acquired = []; sequence = 0
    prefix = [] if session is None else session.get('rows', [])
    replay_started = time.monotonic()
    if session is not None:
        cache = copy.deepcopy(session.get('cache', {}))
        acquired = copy.deepcopy(session.get('acquired', []))
        session.update(cache=cache, acquired=acquired)
    current = None; best_saved = None; witness_saved = None
    saved_history = []; saved_decisions = []
    def save(history=(), best=None, witness=None, decisions=()):
        nonlocal sequence, best_saved, witness_saved, saved_history, saved_decisions
        if session is not None:
            # Invocation journal, not legacy witness checkpoints, owns recovery.
            # A started invocation with no closed point is never resumable.
            return
        if history:
            budget.witness = report.summary(witness)
            best_saved = report.summary(best); witness_saved = report.summary(witness)
        if 'starts' in plan or batched(plan):
            if history or current is None:
                saved_history = [report.summary(r) for r in history]
                saved_decisions = copy.deepcopy(list(decisions))
            history = saved_history; decisions = saved_decisions
        sequence += 1
        report.checkpoint(out,sequence,dict(plan=plan, producer=plan['producer'], current=report.summary(current),
            best=best_saved, witness=witness_saved, history=[report.summary(r) for r in history],
            counters=budget.snapshot(), units=acquired, decisions=list(decisions),
            **(dict(children=copy.deepcopy(children)) if batched(plan) else {})), **kw)

    def evaluate(values, alternative, index, stage, *, start_origin=None, coupled_direction=None):
        nonlocal current
        if session is not None:
            budget.check()
            check_plan(plan, reconstruct=False)
            if index <= len(prefix):
                identity = dict(candidate=index, variables_si=values, alternative=alternative,
                                stage=stage, start_origin=start_origin)
                if coupled_direction is not None: identity['coupled_direction'] = coupled_direction
                row = prefix[index-1]
                if ('coupled_direction' in row) != (coupled_direction is not None):
                    raise ValueError('Session prefix coupled direction presence differs')
                if digest({k:row.get(k) for k in identity}) != digest(identity):
                    raise ValueError('Session prefix identity/order/vector/origin differs')
                return copy.deepcopy(row)
            session.setdefault('reconstruction_seconds', time.monotonic()-replay_started)
            if session.get('pause_after') is not None and index-len(prefix) > session['pause_after']:
                raise StopIteration('session_paused')
        try: budget.check()
        except BudgetStop as exc: raise StopIteration(str(exc)) from exc
        budget.counts['candidates'] += 1
        folder = out/f'candidate-{index:03d}'; folder.mkdir()
        row = dict(candidate=index, stage=stage, variables_si=values, alternative=alternative, criteria=[], units=[],
                   configurations={}, geometry_valid=False, units_complete=False, errors=[], file=f'candidate-{index:03d}/candidate.json')
        if start_origin is not None: row['start_origin'] = copy.deepcopy(start_origin)
        if coupled_direction is not None: row['coupled_direction'] = copy.deepcopy(coupled_direction)
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
                        key=fit_cache_key(plan, design)
                        fit_info=cache.get(key)
                        if fit_info is None:
                            if 'backend' in plan['job']['fit']: budget.counts['fit_attempts']=budget.counts.get('fit_attempts',0)+1
                            fit_dir=folder/f'fit-{cid}'
                            if 'backend' in plan['job']['fit']:
                                from . import geometry_state as geometry
                                from ..reporting import geometry_state as geometry_report
                                fp=geometry.preflight(plan['paths']['config'],design,plan['job']['fit'],fit_dir)
                                fit_dir.mkdir();geometry_report.json_new(fit_dir/'plan.json',fp)
                                value,child=launch('fit',dict(kind='geometry',plan=fp),out,budget,children,
                                    seconds=fp['recipe']['seconds'],output_bytes=32*MIB)
                                report.write_json(fit_dir/'child.json',child,**kw)
                                if value.get('ok') and child['exit_code']==0 and child['reaped']:
                                    geometry_report.close(fit_dir,child)
                            else:
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
                                sha256=report.file_sha256(fit_dir/'child.json'), status=('computed_pending_reader' if 'backend' in plan['job']['fit'] else 'accepted') if fit_info['ok'] else 'rejected'))
                            save()
                        unit=dict(scenario=s['id'],configuration=cid,fit=fit_info['output'],verified=False)
                        row['units'].append(unit)
                        if not fit_info['ok']:
                            unit.update(status='fit_rejected',reason=fit_info.get('reason')); continue
                        fit_dir=report.local(out,fit_info['output'])
                        # Exact bytes/context/recipe are rechecked even on a cache hit.
                        read_fit(fit_dir,plan['job']['fit'],design,plan['paths']['config'])
                        if 'backend' in plan['job']['fit'] and (session is None or 'reader' not in fit_info):
                            checked,reader=launch('read',dict(kind='read_geometry',pending=not (fit_dir/'complete.json').exists(),output=str(fit_dir),config=plan['paths']['config'],design=str(design),recipe=plan['job']['fit']),out,budget,children,seconds=min(175,plan['job']['budgets']['child_seconds']),output_bytes=MIB)
                            if not checked.get('ok'):
                                fit_info.update(ok=False,reason=checked.get('reason','Geometry read unconfirmed'))
                                unit.update(status='fit_reader_unconfirmed',reason=fit_info['reason']);continue
                            if not (fit_dir/'complete.json').exists():
                                from ..reporting.geometry_state import publish_complete
                                publish_complete(fit_dir,reader)
                            if session is not None: fit_info['reader'] = copy.deepcopy(reader)
                            for item in acquired:
                                if item['kind']=='fit' and item['file']==fit_info['output']+'/child.json': item['status']='accepted'
                        if session is not None: unit['fit_reader'] = copy.deepcopy(fit_info['reader'])
                        experiment=instantiate(s['template'],plan['paths']['config'],design,fit_dir/'model.json')
                        ep=folder/f'plan-{s["id"]}.json'; report.write_json(ep,experiment, **kw)
                        dest=folder/f'experiment-{s["id"]}'
                        if 'backend' in plan['job']['fit']:
                            options = (dict(steps_per_call=s.get('steps_per_call',experiment['budgets']['chunk_steps']),progress=unit)
                                       if batched(plan) else {})
                            unit.update(state_trajectory(ep,dest,experiment,out,budget,children,plan,**options))
                            if unit.get('verified'):
                                replacement=core.played_rows(s['template'],unit.pop('criteria'),cid,s['id'])
                                row['criteria']=[r for r in row['criteria'] if r['scenario']!=s['id']]+replacement
                            if unit.get('output'):
                                acquired.append(dict(kind='trajectory',file=f'{unit["output"]}/execution.completed.json',
                                    sha256=report.file_sha256(report.local(out,unit['output'])/'execution.completed.json'),status=unit['status']))
                        else:
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
                                                seconds=min(175 if 'backend' in plan['job']['fit'] else 30,plan['job']['budgets']['child_seconds']),output_bytes=MIB)
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
        if session is not None and not session_candidate_complete(row):
            row.setdefault('stop_reason', 'session_candidate_incomplete')
        # Static screened candidates retain a proved violation and no invented played penalty.
        report.write_json(folder/'candidate.json',row, **kw)
        current=None
        return row

    no_hard=not any(c['role']=='hard' for c in contract.criteria) and not any(
        c['role']=='hard' for s in plan['scenarios'] for c in s['template']['criteria'])
    search=core.poll(plan['variables'],plan['initial'],plan['alternatives'],plan['job']['search'],
                     plan['job']['budgets']['candidates'],evaluate,descriptive=no_hard,checkpoint=save,
                     starts=plan.get('starts'))
    if 'starts' in plan or batched(plan):
        verify_search(plan, search, session=session is not None)
        save(search['history'], search['best'], search['witness'], search['decisions'])
    check_plan(plan)
    # Verify locks and native associations without additional fits or trajectory steps.
    for row in search['history'][len(prefix):]:
        budget.check()
        verify_candidate(plan,out,row,native_read=False)
    complete=search['termination'] in ('poll_exhausted','evaluation_only','descriptive_initials_only','feasible_witness_found','candidate_budget')
    if any(r.get('stop_reason') or (not r['units_complete'] and
           'proved_static_violation; played_not_evaluated' not in r['errors']) for r in search['history']): complete=False
    result=dict(schema=report.SCHEMA,plan=plan,status='complete' if complete else 'partial',
                conforming=search['witness'] is not None,search=search,counters=budget.snapshot(),
                checkpoint='witness',global_resume='unsupported',physical_validation=False)
    verify_batch_counters(plan, search['history'], result['counters'], children)
    result['request_fully_covered'] = (bool(search['witness']) and not plan['unsupported'] and
        all(r['status'] in ('pass','satisfied','fail','violated') for r in search['witness']['criteria']))
    budget.check()
    if session is not None:
        session.setdefault('reconstruction_seconds', time.monotonic()-replay_started)
        if search['termination'] == 'session_paused': result['status'] = 'paused'
        result.update(checkpoint='candidate_boundary', global_resume='explicit_session')
        return result
    return report.export(out,result, **kw)


def run(job_path, output_dir, *, dry_run=False, session=False, resume=None, pause_after=None):
    if type(session) is not bool: raise ValueError('session must be a boolean')
    if session or resume is not None or pause_after is not None:
        return run_session(job_path, output_dir, dry_run=dry_run, session=session,
                           resume=resume, pause_after=pause_after)
    started=time.monotonic()
    plan=preflight(job_path,output_dir)
    if dry_run: return dict(ok=True,status='planned',output_created=False,plan=plan)
    execution_ready()
    if threading.current_thread() is not threading.main_thread(): raise ValueError('Main-thread supervision required')
    out=report.safe_path(output_dir,exists=False); out.mkdir(parents=True,exist_ok=False)
    kw=dict(root=out,limit=plan['job']['budgets']['output_mib']*MIB)
    report.write_json(out/'plan.json',plan, **kw)
    if 'starts' in plan:
        check_plan(plan)
        report.export_starts(out, plan, **kw)
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


def session_scope(plan):
    if (plan['kind'] != 'fixed' or 'starts' not in plan or
            plan['job']['fit'].get('backend') != 'certified_scalar_v1' or
            any(s['template']['case']['kind'] != 'saved_state' for s in plan['scenarios'])):
        raise ValueError('Session requires new fixed / explicit starts / certified_scalar_v1 / saved_state')


def session_candidate_complete(row):
    screened = row.get('errors') == ['proved_static_violation; played_not_evaluated']
    return row.get('geometry_valid') is True and not row.get('stop_reason') and (row.get('units_complete') is True or screened)


def verify_session_counters(plan, rows, counters, children):
    verify_batch_counters(plan, rows, counters, children, force=True)
    if counters.get('fit_attempts', 0) != counters['reserved']['fits'] or type(counters.get('fit_attempts',0)) is not int:
        raise ValueError('Session fit_attempts differs')
    if counters['static_units'] != len(rows): raise ValueError('Session static acquisitions differ')
    fits = {}; readers = []
    for row in rows:
        for unit in row['units']:
            reader = unit['fit_reader']
            if unit['fit'] in fits and fits[unit['fit']] != reader:
                raise ValueError('Session shared fit reader differs')
            fits[unit['fit']] = reader
            readers.extend(segment['reader'] for segment in unit.get('segments', []))
    readers.extend(fits.values())
    admissions = [c for c in children if 'session_generation' in c]
    if [c['session_generation'] for c in admissions] != list(range(2, 2+len(admissions))):
        raise ValueError('Session admission generation sequence differs')
    readers.extend(admissions)
    if (len({digest(c) for c in readers}) != len(readers) or
            sorted(map(digest, readers)) != sorted(digest(c) for c in children if c['kind']=='read')):
        raise ValueError('Session reader acquisitions differ')
    for index, child in enumerate(children, 1):
        if 'session_generation' in child and (child['kind']!='read' or type(child['session_generation']) is not int or not 2<=child['session_generation']<=17):
            raise ValueError('Session admission reader receipt differs')
        batch_receipt(child, child['kind'], child['steps_charged'])
        if child['launched'] is not True or child['exit_code'] != 0 or child['timeout_or_error']:
            raise ValueError('Session boundary has an unsuccessful operation')
        if child['log'] != f'child-{index:04d}.log': raise ValueError('Session child order differs')
        if type(child['seconds']) not in (int,float) or not math.isfinite(child['seconds']) or child['seconds'] < 0:
            raise ValueError('Session child duration differs')
    for name, key in [('candidates','candidates'),('fits','fits'),('trajectories','trajectories'),('steps_charged','steps')]:
        if counters[name] > plan['job']['budgets'][key]: raise ValueError('Session cumulative budget exceeded')
    if counters['elapsed_seconds'] < sum(c['seconds'] for c in children):
        raise ValueError('Session time cannot refund acquired children')


def verify_session_state(value, out, rows):
    plan = value['plan']; point = value['point']; result = point['result']
    if not all(session_candidate_complete(r) for r in rows): raise ValueError('Incomplete candidate is not a resumable boundary')
    if result['conforming'] is not (result['search']['witness'] is not None): raise ValueError('Session conformity differs')
    cache = {}; acquired = []; fits = set()
    statics = iter(c for c in point['children'] if c['kind']=='static')
    for row in rows:
        receipt = next(statics)
        if digest(batch_reader_response(out, receipt)) != digest(report.read_json(Path(out)/Path(row['file']).parent/'static.json')):
            raise ValueError('Session static child evidence differs')
        for unit in row['units']:
            fit = unit['fit']; design = report.local(out,row['configurations'][unit['configuration']]['design'])
            key = fit_cache_key(plan,design)
            if fit not in fits:
                fits.add(fit)
                child_path = report.local(out, fit)/'child.json'
                child = report.read_json(child_path)
                if child not in point['children']: raise ValueError('Session fit receipt absent')
                meta = read_fit(report.local(out,fit),plan['job']['fit'],design,plan['paths']['config'])
                if key in cache: raise ValueError('Session duplicate fit cache identity')
                from ..reporting.geometry_state import read_result as read_geometry
                checked = read_geometry(report.local(out,fit),config=plan['paths']['config'],design=design,recipe=plan['job']['fit'])
                if not checked.get('ok'): raise ValueError('Session native fit unconfirmed')
                if report.read_json(report.local(out,fit)/'reader.json') != unit['fit_reader']:
                    raise ValueError('Session fit reader association differs')
                cache[key] = dict(output=fit,ok=True,key=key,model_sha256=meta['model_sha256'],reader=unit['fit_reader'])
                acquired.append(dict(kind='fit',file=fit+'/child.json',sha256=report.file_sha256(child_path),status='accepted'))
            if cache.get(key, {}).get('output') != fit: raise ValueError('Session fit cache association differs')
            acquired.append(dict(kind='trajectory',file=unit['output']+'/execution.completed.json',
                sha256=report.file_sha256(report.local(out,unit['output'])/'execution.completed.json'),status=unit['status']))
    if digest(cache) != digest(point['cache']) or digest(acquired) != digest(point['acquired']):
        raise ValueError('Session cache/acquisition association differs')
    if len(fits) != result['counters']['fits']: raise ValueError('Session fit inventory differs')
    verify_session_counters(plan, rows, result['counters'], point['children'])


def run_session(job_path, output_dir, *, dry_run=False, session=False, resume=None, pause_after=None):
    """Opt-in continuation at a confirmed candidate boundary; one native poll."""
    from datetime import datetime, timezone
    started=time.monotonic()
    for name in report.NEW_SOURCES:
        if report.file_sha256(ROOT/name, maximum=2*MIB) != LOADED_SOURCES[name]:
            raise ValueError('Source changed after import: '+name)
    if type(dry_run) is not bool or type(session) is not bool: raise ValueError('Session flags must be booleans')
    if pause_after is not None and (type(pause_after) is not int or not 1<=pause_after<=16):
        raise ValueError('pause_after must be an integer in 1..16')
    if (resume is not None and session) or (resume is None and not session):
        raise ValueError('Choose new session=True or an explicit resume point')
    if resume is not None and (not isinstance(resume,(str,Path)) or not str(resume)):
        raise ValueError('Explicit resume point path required')
    # Minimal bounded bootstrap: no inventories, provenance subprocesses or
    # scientific admission before the durable invocation and its deadline.
    raw, job_source = _source(job_path)
    if (raw.get('input', {}).get('kind') != 'fixed' or type(raw.get('fit')) is not dict or
            raw['fit'].get('backend') != 'certified_scalar_v1' or
            type(raw.get('search', {}).get('starts')) is not dict):
        raise ValueError('Session requires fixed / explicit starts / certified_scalar_v1')
    limits = raw['budgets']
    if type(limits) is not dict or set(limits) != set(core.LIMITS):
        raise ValueError('Session bootstrap budget fields differ')
    for key, maximum in core.LIMITS.items():
        if type(limits[key]) is not int or not 1 <= limits[key] <= maximum:
            raise ValueError('Session bootstrap budget scalar differs: '+key)
    if limits['memory_mib'] != 768: raise ValueError('Session memory budget differs')
    out=report.safe_path(output_dir,exists=resume is not None)
    if resume is None:
        if out.exists(): raise FileExistsError('Existing output refused')
        if dry_run:
            plan=preflight(job_path,out);session_scope(plan)
            return dict(ok=True,status='planned',output_created=False,plan=plan,session=True)
        out.mkdir(parents=True,exist_ok=False)
    with report.session_lock(out,shared=dry_run):
        prior=None; previous=None; generation=1; prior_seconds=0.; plan=None
        if resume is not None:
            names=[]
            with os.scandir(out) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names)>16000: raise ValueError('Session bootstrap directory limit')
            paths=sorted(out/name for name in names if name.startswith('session-') and name.endswith('.json'))
            starts=sorted(out/name for name in names if name.startswith('invocation-') and name.endswith('.json'))
            if len(paths)>17 or len(starts)>17: raise ValueError('Session bootstrap generation limit')
            if not paths or len(paths)!=len(starts):raise ValueError('Unclosed invocation; no older boundary fallback')
            if report.safe_path(resume)!=paths[-1]:raise ValueError('Stale session generation')
            for path in (paths[-1],out/'plan.json'):
                if path.stat().st_size>4*MIB: raise ValueError('Session bootstrap record limit')
            previous=report.read_json(paths[-1])['payload'];generation=len(paths)+1
            if type(previous['generation']) is not int or previous['generation'] != len(paths):
                raise ValueError('Session bootstrap generation differs')
            if report.file_sha256(out/f'boundary-{len(paths):05d}.json',maximum=4*MIB)!=report.file_sha256(paths[-1],maximum=4*MIB):
                raise ValueError('Unconfirmed boundary; no older fallback')
            if previous['result']['status']!='paused':raise ValueError('Terminal session cannot be reopened')
            plan=report.read_json(out/'plan.json')
            if plan['job_source'] != job_source or digest(plan['job']) != digest(raw):
                raise ValueError('Session bootstrap differs from original JOB')
            prior=dict(file=paths[-1].name,sha256=report.file_sha256(paths[-1],maximum=4*MIB))
            prior_seconds=previous['result']['counters']['elapsed_seconds']
            if type(prior_seconds) not in (int,float) or not math.isfinite(prior_seconds) or not 0<=prior_seconds<=limits['seconds']:
                raise ValueError('Invalid prior active time')
            if dry_run:
                report.inspect_session(out,resume=resume)
                return dict(ok=True,status='planned',output_created=False,plan=plan,resume=prior,
                    remaining_seconds=limits['seconds']-prior_seconds,inspection_seconds=time.monotonic()-started)
        if threading.current_thread() is not threading.main_thread():raise ValueError('Main-thread supervision required')
        kw=dict(root=out,limit=limits['output_mib']*MIB)
        flags=[];children=[];state=dict(pause_after=pause_after);result=None
        budget=Budget(limits,out,lambda:bool(flags));budget.start=started-prior_seconds
        if previous is not None:
            counts=previous['result']['counters']
            for key in (*budget.counts, 'fit_attempts'):
                value=counts.get(key,0)
                if type(value) is not int or not 0<=value<=2_000_000:
                    raise ValueError('Invalid prior counter: '+key)
            budget.counts={k:counts[k] for k in budget.counts}
            if 'fit_attempts' in counts:budget.counts['fit_attempts']=counts['fit_attempts']
            budget.reserved=copy.deepcopy(counts['reserved']);children=copy.deepcopy(previous['children'])
            if set(budget.reserved) != {'fits','trajectories','steps_charged','children'} or any(
                    type(v) is not int or not 0<=v<=2_000_000 for v in budget.reserved.values()):
                raise ValueError('Invalid prior reservations')
        invocation=dict(schema=report.SESSION_SCHEMA,generation=generation,previous=prior,
            job_source=job_source,pause_after=pause_after,started_monotonic=started,
            started_utc=datetime.now(timezone.utc).isoformat(),
            producer=dict(loaded_sources_sha256=LOADED_SOURCES,versions=report.versions()))
        start_path=out/f'invocation-{generation:05d}.json'
        alarm_handler=signal.getsignal(signal.SIGALRM);alarm_timer=signal.getitimer(signal.ITIMER_REAL)
        timer_captured=time.monotonic();old={}
        phases={};failure=None;point_path=None;accounted=None
        try:
            for sig in (signal.SIGINT,signal.SIGTERM):old[sig]=signal.signal(sig,lambda s,f:flags.append(s))
            def deadline(sig,frame):
                flags.append(sig);raise BudgetStop('total_seconds')
            signal.signal(signal.SIGALRM,deadline)
            remaining=max(.001,limits['seconds']-(time.monotonic()-budget.start))
            signal.setitimer(signal.ITIMER_REAL,min(remaining,alarm_timer[0]) if alarm_timer[0]>0 else remaining)
            # The journal itself is deadline-bound. A failed write stays visible.
            report.session_write(start_path,invocation,**kw)
            budget.check()
            if remaining<=15:raise BudgetStop('Session admission does not fit remaining budget and closure reserve')
            execution_ready();report.verify_sources(LOADED_SOURCES)
            if previous is None:
                plan=load_inputs(job_path)[1];session_scope(plan)
                if plan['job_source'] != job_source: raise ValueError('JOB changed during admission')
                report.write_json(out/'plan.json',plan,**kw);report.export_starts(out,plan,**kw)
                report.session_write(out/'session.json',dict(schema=report.SESSION_SCHEMA,output=str(out),context_sha256=plan['context_sha256']),**kw)
            phase=time.monotonic()
            if previous is not None:
                value=report.inspect_session(out,resume=resume,_active_start=start_path.name)
                # Fresh bounded native reader; no fit, TMM or trajectory step.
                checked,reader=launch('read',dict(kind='read_session_prefix',output=str(out),reference=prior),
                    out,budget,children,seconds=min(175,plan['job']['budgets']['child_seconds']),output_bytes=MIB)
                reader['session_generation']=generation
                if not checked.get('ok') or checked.get('prefix_sha256')!=prior['sha256']:
                    raise ValueError('Session native prefix reader refused: '+str(checked.get('reason')))
                rows=[report.read_json(report.local(out,r['file'])) for r in previous['result']['search']['history']]
                expected=dict(previous['files'], **{prior['file']:prior['sha256'],f'boundary-{generation-1:05d}.json':prior['sha256'],
                    start_path.name:report.file_sha256(start_path),reader['log']:report.file_sha256(out/reader['log'])})
                if report.session_inventory(out)!=expected:raise ValueError('Session changed during admission')
                state.update(rows=rows,cache=previous['cache'],acquired=previous['acquired'])
            phases['native_read_and_inspection_seconds']=time.monotonic()-phase
            phases['admission_seconds']=time.monotonic()-started
            budget.check();phase=time.monotonic()
            result=execute(plan,out,budget,children,session=state)
            phases['execute_and_reconstruct_seconds']=time.monotonic()-phase
            phases['reconstruction_seconds']=state['reconstruction_seconds'];phases['replayed_candidates']=len(state.get('rows',[]))
            check_plan(plan);budget.check()
            if result['status'] not in ('paused','complete') or flags:
                raise ValueError('Session stopped without a healthy boundary: '+result['search']['termination'])
            verify_session_counters(plan,result['search']['history'],result['counters'],children)
            if not all(session_candidate_complete(row) for row in result['search']['history']):
                raise ValueError('Session incomplete candidate cannot be resumed')
            accounted=time.monotonic();active=accounted-started;charged=active+15
            if prior_seconds+charged>=plan['job']['budgets']['seconds']:raise BudgetStop('Session finalization reserve exhausted')
            # Freeze a conservative upper bound, never refund closure reservations.
            result['counters']=dict(budget.snapshot(),elapsed_seconds=prior_seconds+charged)
            signal.setitimer(signal.ITIMER_REAL,min(15,max(.001,signal.getitimer(signal.ITIMER_REAL)[0])))
            if result['status']=='complete':
                saved=report.export(out,result,**kw)
                report.prepare_completion(out,dict(ok=True,children=children,status='complete',cancelled=False),**kw)
                report.publish_completion(out)
            else:
                saved=dict(result,search=dict(result['search'],history=[report.summary(r) for r in result['search']['history']],
                    best=report.summary(result['search']['best']),witness=report.summary(result['search']['witness'])))
            point=dict(generation=generation,previous=prior,invocation_sha256=report.file_sha256(start_path),
                active_seconds=active,charged_seconds=charged,closure_reserved_seconds=15,accounted_monotonic=accounted,
                phases=phases,result=saved,children=children,cache=state['cache'],acquired=state['acquired'],
                files=report.session_inventory(out),publication='final' if result['status']=='complete' else 'paused')
            check_plan(plan,reconstruct=False);budget.check()
            point_path=out/f'session-{generation:05d}.json'
            report.session_write(point_path,dict(payload=point,sha256=digest(point)),**kw)
            budget.check()
            if any(c['reaped'] is not True for c in children): raise ValueError('Unreaped session child')
        except (BudgetStop,ValueError,OSError,KeyError,ArithmeticError) as exc:
            failure=type(exc).__name__+': '+str(exc)
        finally:
            signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,alarm_handler)
            signal.setitimer(signal.ITIMER_REAL,max(.001,alarm_timer[0]-(time.monotonic()-timer_captured)) if alarm_timer[0]>0 else 0,alarm_timer[1])
            for sig,handler in old.items():signal.signal(sig,handler)
        if failure or flags or accounted is None or time.monotonic()-accounted>=15:
            response=dict(ok=False,status='partial',resumable=False,reason=failure or 'interrupted_or_closure_timeout',
                counters=budget.snapshot(),children=children)
            report.session_write(out/f'failure-{generation:05d}.json',response,**kw)
            return response
        # Like the native completion marker: last fallible publication, after
        # every owned child, stream and signal handler is closed/restored.
        os.link(point_path,out/f'boundary-{generation:05d}.json',follow_symlinks=False)
        return dict(ok=True,status=result['status'],resumable=result['status']=='paused',conforming=result['conforming'],
            output=str(out),resume_point=str(point_path),generation=generation,counters=saved['counters'])


LOADED_SOURCES=report.sources()
