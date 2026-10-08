"""Finite prescribed-scenario feasibility, with no physical model or player tuning."""
from __future__ import annotations

import copy
import math

from .design_contract import obj, number, integer, identifier
from ..nonlinear import register_target as rt
from ..nonlinear.passive_fit import quality_gates, completion_spec
from ..pipeline.paired_onset import RECIPE

SCHEMA = 'dcalc.played_search.job.v1'
LIMITS = dict(candidates=16, scenarios=8, fits=32, trajectories=64, steps=2_000_000,
              seconds=3600, memory_mib=768, output_mib=800, child_seconds=180)
FIT_KEYS = RECIPE | {'gates', 'mesh_gate', 'seconds', 'fit_seconds', 'max_passes',
                     'signal_samples', 'flow_peak_m3_s'}


def validate_job(raw):
    obj(raw, {'schema', 'config', 'input', 'fit', 'scenarios', 'search', 'budgets', 'scope', 'metadata'},
        'JOB', {'schema', 'config', 'input', 'fit', 'scenarios', 'search', 'budgets', 'scope'})
    if raw['schema'] != SCHEMA:
        raise ValueError('Unsupported JOB version')
    entry = raw['input']
    obj(entry, {'kind', 'design', 'assembly', 'request'}, 'input', {'kind', 'request'})
    if entry['kind'] not in ('fixed', 'assembly'):
        raise ValueError('input.kind must be fixed or assembly')
    if set(entry) != {'kind', 'request', 'design' if entry['kind'] == 'fixed' else 'assembly'}:
        raise ValueError('Exactly one native geometry input required')
    b = obj(raw['budgets'], LIMITS, 'budgets', LIMITS)
    for k, hi in LIMITS.items():
        integer(b[k], 1, hi, k)
    if b['memory_mib'] != 768:
        raise ValueError('v1 child memory is explicitly 768 MiB')
    s = obj(raw['search'], {'normalized_steps', 'directions', 'stop_on_witness'}, 'search',
            {'normalized_steps', 'directions', 'stop_on_witness'})
    steps = s['normalized_steps']
    if type(steps) is not list or not 1 <= len(steps) <= 24:
        raise ValueError('1..24 normalized steps required')
    for x in steps:
        if not 0 < number(x, 'normalized step') <= 1:
            raise ValueError('Normalized step outside (0,1]')
    if any(a <= b for a, b in zip(steps, steps[1:])):
        raise ValueError('Steps must strictly decrease')
    if s['directions'] not in ([-1, 1], [1, -1]) or any(type(x) is not int for x in s['directions']):
        raise ValueError('Explicit direction order required')
    if type(s['stop_on_witness']) is not bool:
        raise ValueError('stop_on_witness boolean required')
    scope = obj(raw['scope'], {'coverage', 'player_controls', 'objective', 'physiological_guarantee'},
                'scope', {'coverage', 'player_controls', 'objective', 'physiological_guarantee'})
    for k, choices in [('coverage', ('finite', 'continuous')), ('player_controls', ('prescribed', 'optimized')),
                       ('objective', ('feasibility', 'preference'))]:
        if scope[k] not in choices:
            raise ValueError('Unknown scope.' + k)
    if type(scope['physiological_guarantee']) is not bool:
        raise ValueError('Explicit physiological_guarantee boolean')
    unsupported = [k for k, expected in [('coverage', 'finite'), ('player_controls', 'prescribed'),
                   ('objective', 'feasibility'), ('physiological_guarantee', False)] if scope[k] != expected]
    scenarios = raw['scenarios']
    if type(scenarios) is not list or not 1 <= len(scenarios) <= b['scenarios']:
        raise ValueError('1..scenarios budget prescribed scenarios required')
    ids = set()
    for scenario in scenarios:
        obj(scenario, {'id', 'configuration', 'template'}, 'scenario', {'id', 'configuration', 'template'})
        ident = identifier(scenario['id'], 'scenario.id')
        identifier(scenario['configuration'], 'configuration')
        if ident in ids:
            raise ValueError('Duplicate scenario')
        ids.add(ident)
    validate_fit(raw['fit'])
    return unsupported


def validate_fit(fit):
    """Scalar native option domains only; actual CONFIG/mesh preflight follows."""
    obj(fit, FIT_KEYS, 'fit', FIT_KEYS)
    for k, lo, hi in [('sample_rate_hz', 1000, 12000), ('fit_points', 32, 8192),
                     ('guard_points', 32, 2048), ('audit_points', 32, 4096),
                     ('max_passes', 1, 10), ('signal_samples', 32, 12000)]:
        integer(fit[k], lo, hi, k)
    for k in ('fit_min_hz', 'fit_max_hz', 'guard_max_hz', 'h_cm', 'seconds', 'mesh_gate'):
        number(fit[k], k, True)
    if not 0 < fit['fit_min_hz'] < fit['fit_max_hz'] < fit['guard_max_hz'] < fit['sample_rate_hz']/2:
        raise ValueError('Native fit/guard/Nyquist domain')
    if not 0 < fit['h_cm'] <= 2 or not 0 < fit['seconds'] <= 180:
        raise ValueError('Native fit mesh/time domain')
    if not 0 <= number(fit['fit_seconds'], 'fit_seconds') <= fit['seconds']:
        raise ValueError('Native fit time domain')
    if number(fit['flow_peak_m3_s'], 'flow_peak_m3_s') < 0:
        raise ValueError('Nonnegative prescribed passive flow')
    quality_gates(fit['gates'])
    completion_spec(fit['basis_completion'], fit_max_hz=fit['fit_max_hz'],
                    fs=fit['sample_rate_hz'], domain='discrete_prewarped')
    if fit['R0'] is None:
        if fit['loss_model'] != 'zk' or fit['dc_origin'] is not None:
            raise ValueError('Native ZK DC or explicit R0/origin required')
    elif number(fit['R0'], 'R0') < 0 or not isinstance(fit['dc_origin'], str) or not fit['dc_origin'].strip():
        raise ValueError('Explicit nonnegative R0 with origin required')


def recipe(fit):
    return {k: copy.deepcopy(fit[k]) for k in sorted(RECIPE)}


def played_rows(template, native_rows, configuration, scenario):
    """Export scalar margins; native verdict stays authoritative (including null)."""
    definitions = {c['id']: c for c in template['criteria']}
    rows = []
    for native in native_rows:
        c = definitions[native['id']]
        dim = rt.DIMENSIONS.get(c['observable'], ('scalar', '1'))[0]
        values = native['values_si']
        windows = ['/'.join(c['windows'])] if c['observable'] == 'frequency_ratio' else c['windows']
        if len(values) != len(windows):
            values = [None] * len(windows)
        for window, value in zip(windows, values):
            row = dict(id=c['id'], kind='played', configuration=configuration, scenario=scenario,
                       window=window, observable=c['observable'], role=c['role'], status=native['status'],
                       value_si=value, unit_si=native['unit_si'], target_si=None, residual_si=None,
                       error_cents=None, margin=None, margin_unit=None, normalization_scale=None,
                       violation_normalized=None, reason=native.get('reason'))
            if 'target' in c:
                row['target_si'] = rt.metric_target(c['target'], dim)
            # An unresolved aggregate may still expose scalar evidence in acquired windows.
            if value is not None:
                margins = []
                if 'target' in c:
                    target = row['target_si']; tol, tdim = rt.metric_quantity(c['tolerance'])
                    row['residual_si'] = value-target
                    if value > 0 and target > 0 and dim in ('frequency', 'scalar'):
                        row['error_cents'] = 1200*math.log2(value/target)
                    residual = row['error_cents'] if tdim == 'cent' else row['residual_si']
                    if residual is not None:
                        margins.append((tol-abs(residual), tol, 'cent' if tdim == 'cent' else row['unit_si']))
                for key, sign in [('minimum', 1), ('maximum', -1)]:
                    if key in c:
                        bound = rt.metric_quantity(c[key], dim)[0]
                        # Explicit dimensional normalization: |bound|, or one SI unit for zero.
                        margins.append((sign*(value-bound), abs(bound) or 1., row['unit_si']))
                if margins:
                    # A zero tolerance is an exact condition: do not invent a smooth penalty.
                    ranked = [(max(0., -m)/scale if scale > 0 else None, m, scale, unit)
                              for m, scale, unit in margins]
                    usable = [r for r in ranked if r[0] is not None]
                    if usable:
                        violation, margin, scale, unit = max(usable, key=lambda x: x[0])
                        row.update(margin=margin, margin_unit=unit, normalization_scale=scale,
                                   violation_normalized=violation)
                    if any(r[0] is None for r in ranked):
                        row['violation_normalized'] = None
            rows.append(row)
    return rows


def static_rows(rows, definitions, configuration):
    result = []
    for r, c in zip(rows, definitions):
        row = dict(r, kind='static', configuration=configuration, scenario=None, window=None,
                   target_si=c['target_si'], residual_si=None, normalization_scale=c['tolerance_si'],
                   violation_normalized=None)
        if r['value_si'] is not None and c['target_si'] is not None:
            row['residual_si'] = r['value_si']-c['target_si']
        if r.get('margin') is not None and c['tolerance_si'] is not None and c['tolerance_si'] > 0:
            row['violation_normalized'] = max(0., -r['margin'])/c['tolerance_si']
        result.append(row)
    return result


def verdict(rows, *, units_complete, unsupported=()):
    hard = [r for r in rows if r['role'] == 'hard']
    failed = any(r['status'] in ('fail', 'violated', 'out_of_domain') for r in hard)
    known = all(r['status'] in ('pass', 'satisfied', 'fail', 'violated') and
                r.get('violation_normalized') is not None for r in hard)
    score = max((r['violation_normalized'] for r in hard), default=0.) if known and units_complete else None
    conforming = bool(hard) and units_complete and not unsupported and all(r['status'] in ('pass', 'satisfied') for r in hard)
    comparable = [r for r in hard if r['status'] in ('pass','satisfied','fail','violated') and r.get('violation_normalized') is not None]
    lower = max((r['violation_normalized'] for r in comparable), default=None)
    signature = sorted([r.get('kind',''),r.get('configuration',''),r.get('scenario') or '',r.get('window') or '',r['id']] for r in comparable)
    return dict(conforming=conforming, max_violation_normalized=score,
                observed_violation_lower_bound=lower, comparable_hard_rows=signature,
                status='conforming' if conforming else 'violated' if failed else
                       'unsupported' if unsupported or any(r['status']=='unsupported' for r in hard) else
                       'partial' if not units_complete else 'descriptive' if not hard else 'unresolved')


def better(a, b):
    if b is None:
        return True
    if a['conforming'] != b['conforming']:
        return a['conforming']
    x, y = a['max_violation_normalized'], b['max_violation_normalized']
    if x is not None or y is not None:
        return x is not None and (y is None or x < y)
    # Compare the same acquired obligations only. This lower bound cannot certify feasibility.
    ax, bx = a.get('observed_violation_lower_bound'), b.get('observed_violation_lower_bound')
    return (a.get('comparable_hard_rows') == b.get('comparable_hard_rows') and
            ax is not None and bx is not None and ax < bx)


def poll(variables, initial, alternatives, search, maximum, evaluate, *, descriptive=False, checkpoint=None):
    """Unique coordinate poll. Unknown evidence has no invented numeric penalty."""
    history, decisions, seen = [], [], set()
    best = witness = None
    termination = 'poll_exhausted'

    def call(point, alternative, stage):
        nonlocal best, witness
        key = (alternative, tuple(point))
        if key in seen:
            decisions.append(dict(action='skip_duplicate', alternative=alternative, variables_si=list(point)))
            return None
        if len(history) >= maximum:
            raise StopIteration('candidate_budget')
        seen.add(key)
        row = evaluate(list(point), alternative, len(history)+1, stage)
        row.update(candidate=len(history)+1, variables_si=list(point), alternative=alternative, stage=stage)
        history.append(row)
        selected = better(row, best)
        if selected: best = row
        if row['conforming'] and witness is None: witness = row
        decisions.append(dict(candidate=row['candidate'], action='select' if selected else 'retain_incumbent',
                              score=row['max_violation_normalized'], status=row['status']))
        if checkpoint: checkpoint(history, best, witness, decisions)
        if row.get('stop_reason'): raise StopIteration(row['stop_reason'])
        if row['conforming'] and search['stop_on_witness']: raise StopIteration('feasible_witness_found')
        return row

    try:
        for alternative in alternatives:
            center = list(initial)
            current = call(center, alternative, 'initial')
            if descriptive or not variables: continue
            for step in search['normalized_steps']:
                # Fixed anchor for this poll; no target-dependent direction generation.
                anchor = list(center); winner = current
                for j, variable in enumerate(variables):
                    for direction in search['directions']:
                        point = list(anchor)
                        point[j] += direction*step*(variable['high']-variable['low'])
                        if not variable['low'] <= point[j] <= variable['high']:
                            decisions.append(dict(action='skip_bounds', alternative=alternative,
                                                  variables_si=point, variable=variable['id'], step=step))
                            continue
                        row = call(point, alternative, 'coordinate_poll')
                        if row is not None and better(row, winner): winner = row
                if winner is not None:
                    current = winner; center = list(winner['variables_si'])
        if not variables: termination = 'evaluation_only'
        if descriptive: termination = 'descriptive_initials_only'
    except StopIteration as exc:
        termination = str(exc)
    return dict(history=history, best=best, witness=witness, decisions=decisions, termination=termination,
                global_optimum_proven=False, infeasibility_proven=False)
