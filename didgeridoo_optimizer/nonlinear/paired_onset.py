"""Bounded local stability of the existing inward V2/conjugate equations.

No fit, trajectory, physical parameter identification, or played-regime claim.
The realization coordinate s and the bilinear frequency are distinct outputs.
"""
from __future__ import annotations

from dataclasses import replace
import math
import copy
import time
import numpy as np

from .lips import DimensionedLipParameters, dimensioned_stiffness_n_per_m, dimensioned_damping_n_s_per_m
from .onset_stability import validate_parameters, equilibria
from .passive_resonator import PassiveResonator, real, integer, digest

SCOPE = ('Dominant equilibrium loss only, within the declared regular free grid; '
         'no enumeration of higher unstable branches, global minimum, played frequency or toot.')
AXIS_TOLERANCE_S = 1e-8
SELECTION_SCOPE = 'whole_case_scenario_unit'


class BudgetExhausted(ValueError):
    pass


def coefficients(params):
    return dimensioned_stiffness_n_per_m(params), dimensioned_damping_n_s_per_m(params)


def pressure_limit(params):
    k, _ = coefficients(params)
    return k * (params.rest_opening_m - params.min_opening_m) / params.effective_area_m2


def validate_domain(params, rho, port_model='conjugate'):
    validate_parameters(params, rho)
    if port_model != 'conjugate':
        raise ValueError('unsupported port model: only tested conjugate algebra')
    if params.pressure_force_sign != -1 or params.rest_opening_m <= params.min_opening_m:
        raise ValueError('Only inward sign=-1, positive regular free opening supported')
    if not math.isfinite(pressure_limit(params)) or pressure_limit(params) <= 0:
        raise ValueError('Unrepresentable contact protocol bound')


def validate_model(model):
    if not isinstance(model, PassiveResonator) or len(model.a) > 192:
        raise ValueError('PassiveResonator with at most 192 terms / 386 states required')
    integer(model.sample_rate_hz, 'fs', 1000, 12000)
    model.verify_file_unchanged()


def scales(model, params):
    return np.r_[params.rest_opening_m, params.rest_opening_m*2*np.pi*params.resonance_hz,
                 3e-4/model.omega**2, 3e-4/model.omega]


def equilibrium(model, pressure, rho, params):
    validate_model(model); validate_domain(params, rho)
    pressure = real(pressure, 'pressure_pa')
    if not 0 < pressure < pressure_limit(params):
        raise ValueError('Outside regular free protocol: require 0 < Pu < Pcontact')
    k, _ = coefficients(params); area = params.effective_area_m2
    cdw = params.flow_coefficient * params.lip_width_m
    lower = 1-model.R0*cdw*area/k*math.sqrt(2*pressure/rho)
    if lower <= 0:
        raise ValueError('not_resolved: no sufficient positive uniqueness bound')
    def jet(delta):
        return cdw*(params.rest_opening_m-area*delta/k)*math.sqrt(2*delta/rho)
    lo, hi = 0., pressure
    for _ in range(90):
        mid = (lo+hi)/2
        if mid+model.R0*jet(mid) > pressure: hi = mid
        else: lo = mid
    delta = (lo+hi)/2; flow = jet(delta); downstream = model.R0*flow
    x = -area*delta/k
    native = equilibria(params, rho, pressure, closure='fir_dc', dc=model.R0)
    if native['status'] != 'equilibrium_solved' or native['branch_count'] != 1:
        raise ValueError('not_resolved: native equilibrium ambiguity')
    branch = native['branches'][0]
    if (not branch['regular_free'] or abs(branch['x_m']-x) > 1e-12
            or abs(branch['downstream_pa']-downstream) > 1e-8):
        raise ValueError('not_resolved: native equilibrium parity')
    state = np.r_[x, 0., flow/model.omega**2, np.zeros(len(model.a))]
    return state, dict(pressure_pa=pressure, effective_mouth_pressure_kpa=pressure/1000,
        delta_pa=delta, downstream_pa=downstream, opening_m=params.rest_opening_m+x,
        flow_m3_s=flow, static_residual_pa=delta+downstream-pressure,
        native_residuals=branch['residuals'], monotonicity_lower_bound=lower,
        B=cdw*math.sqrt(2*delta/rho), C=flow/(2*delta))


def matrix(model, pressure, rho, params):
    state, e = equilibrium(model, pressure, rho, params)
    n = len(model.a); k, r = coefficients(params); area = params.effective_area_m2
    dp = np.r_[model.R0*e['B'], -model.R0*area, np.zeros(n), model.a]/(1+e['C']*model.R0)
    du = np.r_[e['B'], -area, np.zeros(2*n)]-e['C']*dp
    j = np.zeros((2+2*n, 2+2*n)); j[0, 1] = 1
    j[1] = -np.r_[k, r, np.zeros(2*n)]/params.mass_kg+area*dp/params.mass_kg
    j[2:2+n, 2+n:] = np.eye(n); j[2+n:] = du[None, :]
    j[2+n:, 2:2+n] -= np.diag(model.omega**2)
    j[2+n:, 2+n:] -= np.diag(model.gamma)
    scale = scales(model, params)
    scaled = j*scale[None, :]/scale[:, None]
    if not np.all(np.isfinite(scaled)):
        raise ValueError('Unrepresentable scaled Jacobian')
    return scaled, state, e


def impedance(model, s):
    return model.R0+np.sum(model.a*s/(s*s+model.gamma*s+model.omega**2))


def characteristic(model, s, pressure, rho, params, z_override=None):
    _, e = equilibrium(model, pressure, rho, params)
    k, r = coefficients(params); area = params.effective_area_m2
    z = impedance(model, s) if z_override is None else z_override
    dl = params.mass_kg*s*s+r*s+k
    terms = (dl, dl*e['C']*z, -area*e['B']*z, area*area*s*z)
    return sum(terms), max(sum(abs(t) for t in terms), k)


def eigen_row(model, pressure, rho, params):
    j, _, e = matrix(model, pressure, rho, params)
    roots = np.linalg.eigvals(j)
    if not np.all(np.isfinite(roots)):
        raise ValueError('Nonfinite roots')
    eligible = roots[roots.imag >= -1e-9]
    root = eligible[np.argmax(eligible.real)]
    distances = abs(roots-root); own = int(np.argmin(distances))
    partner = int(np.argmin(abs(roots-root.conjugate())))
    other = np.delete(roots, sorted({own, partner}))
    separation = min((abs(v-root) for i, v in enumerate(roots) if i != own), default=1e100)
    fs = model.sample_rate_hz; z = (1+root/(2*fs))/(1-root/(2*fs))
    value, scale = characteristic(model, root, pressure, rho, params)
    marginal, marginal_scale = characteristic(model, 1j*root.imag, pressure, rho, params)
    if not np.isfinite(z) or abs(z)==0:
        raise ValueError('not_resolved: singular bilinear coordinate')
    ordered = sorted(roots, key=lambda v: (float(v.real), float(v.imag)))
    return dict(**e, status='evaluated', root_real_s=float(root.real), root_imag_s=float(root.imag),
        realization_frequency_hz=float(root.imag/(2*np.pi)),
        discrete_frequency_hz=float(np.angle(z)*fs/(2*np.pi)),
        discrete_growth_per_s=float(np.log(abs(z))*fs),
        roots_s=[[float(v.real), float(v.imag)] for v in ordered],
        unstable_roots=int(np.sum(roots.real > AXIS_TOLERANCE_S)),
        axis_tolerance_s=AXIS_TOLERANCE_S,
        axis_indeterminate=bool(abs(root.real) <= AXIS_TOLERANCE_S),
        near_axis_roots=int(np.sum(abs(roots.real) <= AXIS_TOLERANCE_S)),
        other_root_max_real_s=float(max(other.real)) if len(other) else None,
        pair_separation_s=float(separation),
        characteristic_relative_residual=float(abs(value)/scale),
        marginal_relative_residual=float(abs(marginal)/marginal_scale))


def pressures(spec, params):
    if type(spec) is not dict or len(spec) != 1 or not set(spec) <= {'fractions', 'pa'}:
        raise ValueError('Pressure grid: exactly fractions or pa')
    key = next(iter(spec)); values = spec[key]
    if type(values) is not list or not 2 <= len(values) <= 33:
        raise ValueError('Require 2..33 pressure nodes')
    values = [real(v, 'pressure node') for v in values]
    if any(a >= b for a, b in zip(values, values[1:])):
        raise ValueError('Pressure nodes must be strictly increasing')
    limit = pressure_limit(params)
    if key == 'fractions':
        if not all(0 < v < 1 for v in values): raise ValueError('Fractions in (0,1) required')
        values = [v*limit for v in values]
    if any(a>=b for a,b in zip(values,values[1:])):
        raise ValueError('Effective pressures not representably increasing')
    if not all(0 < v < limit for v in values):
        raise ValueError('Pressure grid outside regular free protocol')
    return values


def crossing_verified(root, sides, trace):
    left, right = sides
    if any(r.get('status') != 'evaluated' for r in [root, left, right]): return False
    if root['marginal_relative_residual'] >= 1e-7: return False
    other = root['other_root_max_real_s']
    if not (left['root_real_s'] < -AXIS_TOLERANCE_S
            and right['root_real_s'] > AXIS_TOLERANCE_S and left['unstable_roots'] == 0
            and right['unstable_roots'] == 2 and root['root_imag_s'] > 1e-6
            and (other is None or other < -AXIS_TOLERANCE_S)):
        return False
    rows = [root, left, right, *trace]
    if any(r.get('status') != 'evaluated' for r in rows): return False
    if any(r['other_root_max_real_s'] is not None
           and r['other_root_max_real_s'] >= -AXIS_TOLERANCE_S for r in rows): return False
    if max(r['characteristic_relative_residual'] for r in rows) >= 1e-7: return False
    if any(r['pair_separation_s'] < 1e-5*max(1., abs(r['root_imag_s'])) for r in rows): return False
    # Only a locally continuous dominant pair is accepted, never a nearby target.
    ordered = sorted(rows, key=lambda r: r['pressure_pa'])
    for a, b in zip(ordered, ordered[1:]):
        ra = complex(a['root_real_s'], a['root_imag_s'])
        rb = complex(b['root_real_s'], b['root_imag_s'])
        competitors = [complex(*v) for v in b['roots_s'] if v[1] > 1e-6]
        rivals = [v for v in competitors if abs(v-rb)>1e-7]
        reverse = [complex(*v) for v in a['roots_s'] if v[1]>1e-6 and abs(complex(*v)-ra)>1e-7]
        if (abs(ra-rb) > 2*np.pi*2
                or any(abs(ra-v)<=abs(ra-rb)+1e-7 for v in rivals)
                or any(abs(rb-v)<=abs(rb-ra)+1e-7 for v in reverse)):
            return False
    return True


def analyze(model, params, rho, pressure_grid, *, refinements, max_evaluations,
            seconds=175., stop=lambda: False, after_unit=None, qualification='synthetic', port_model='conjugate'):
    """General API. Synthetic realizations are labelled, never CLI fit certificates."""
    validate_model(model); validate_domain(params, rho, port_model)
    integer(refinements, 'refinements', 0, 32)
    integer(max_evaluations, 'max_evaluations', 2, 1200)
    seconds = real(seconds, 'seconds', minimum=0.)
    if not 0 < seconds <= 180 or qualification not in ('synthetic', 'historical_saved_fit'):
        raise ValueError('Explicit bounded duration and qualification required')
    nodes = pressures(pressure_grid, params)  # quotas precede matrices
    before = digest(model.parameters()); requested = params.as_dict(); started = time.monotonic()
    result = dict(status='partial', scope=SCOPE, qualification=qualification,
        coefficient_domain=model.parameters()['domain'], sample_rate_hz=model.sample_rate_hz,
        requested_parameters=requested, effective_parameters=requested.copy(), rho_kg_m3=rho,
        reference_mouth_pressure_kpa=params.mouth_pressure_kpa,
        pressure_semantics='Each evaluation pressure_pa replaces reference mouth_pressure_kpa',
        pressure_contact_bound_pa=pressure_limit(params), pressure_grid_requested=copy.deepcopy(pressure_grid),
        pressure_contact_scope='Conservative protocol bound, not physical playing limit',
        port_model=port_model, grid=[], candidates=[], counters=dict(evaluations=0),
        limits=dict(refinements=refinements, max_evaluations=max_evaluations, seconds=seconds), reason=None)
    def evaluate(p):
        if stop(): raise BudgetExhausted('interrupted')
        if time.monotonic()-started >= seconds: raise BudgetExhausted('time_budget')
        if result['counters']['evaluations'] >= max_evaluations: raise BudgetExhausted('evaluation_budget')
        result['counters']['evaluations'] += 1
        try: return eigen_row(model, p, rho, params)
        except (ValueError, ArithmeticError, np.linalg.LinAlgError) as exc:
            return dict(pressure_pa=p, status='not_resolved', reason=str(exc))
    def checkpoint():
        if after_unit: after_unit(result)
    try:
        for pressure in nodes:
            result['grid'].append(evaluate(pressure)); checkpoint()
        for index, (left, right) in enumerate(zip(result['grid'], result['grid'][1:])):
            if left['status'] != 'evaluated' or right['status'] != 'evaluated': continue
            if left['root_real_s']*right['root_real_s'] > 0: continue
            candidate = dict(id=f'crossing-{index:02d}', status='not_resolved', reason=None,
                direction='loss' if left['root_real_s'] < right['root_real_s'] else 'restabilization',
                bracket_pa=[left['pressure_pa'], right['pressure_pa']], root=None, sides=[left, right], trace=[])
            result['candidates'].append(candidate)
            lo, hi = left, right
            for _ in range(refinements):
                row = evaluate((lo['pressure_pa']+hi['pressure_pa'])/2)
                candidate['trace'].append(row)
                if row['status'] != 'evaluated': break
                if (row['root_real_s'] > 0) == (hi['root_real_s'] > 0): hi = row
                else: lo = row
                candidate['bracket_pa'] = [lo['pressure_pa'], hi['pressure_pa']]
            # Signed bisection endpoints may lie inside the axis diagnostic band.
            # Stability counts are evidence at the separated sides, not here.
            candidate['bracket_signs'] = [int(np.sign(r['root_real_s'])) for r in (lo, hi)]
            candidate['bracket_axis_indeterminate'] = any(
                abs(r['root_real_s']) <= AXIS_TOLERANCE_S for r in (lo, hi))
            refinement_failed = any(r['status'] != 'evaluated' for r in candidate['trace'])
            root = evaluate(sum(candidate['bracket_pa'])/2); candidate['root'] = root
            if root['status'] == 'evaluated':
                d = min(10., .01*pressure_limit(params), (root['pressure_pa']-nodes[0])/2,
                        (nodes[-1]-root['pressure_pa'])/2)
                candidate['sides'] = [evaluate(root['pressure_pa']-d), evaluate(root['pressure_pa']+d)]
                # Original broad bracket can contain switching branches; assess the final local trace.
                local = [r for r in candidate['trace'] if r['status']=='evaluated'
                         and abs(r['pressure_pa']-root['pressure_pa']) <= d]
                if (not refinement_failed and candidate['direction']=='loss'
                        and crossing_verified(root, candidate['sides'], [*local, lo, hi])
                        and lo['status']=='evaluated' and hi['status']=='evaluated'
                        and lo['root_real_s'] <= 0 < hi['root_real_s']):
                    candidate['status'] = 'local_crossing_verified'
                else: candidate['reason'] = ('refinement_evaluation_failed' if refinement_failed
                                            else 'restabilization_multiple_or_ambiguous_pair')
            else: candidate['reason'] = root.get('reason')
            candidate['uncertainty_pressure_pa'] = (candidate['bracket_pa'][1]-candidate['bracket_pa'][0])/2
            candidate['frequency_bracket_hz'] = sorted([lo.get('discrete_frequency_hz', 0.), hi.get('discrete_frequency_hz', 0.)])
            checkpoint()
        result['status'] = 'complete'
        if any(r['status']!='evaluated' for r in result['grid']): result['status']='not_resolved'
    except BudgetExhausted as exc:
        result['reason'] = str(exc)
    model.verify_file_unchanged()
    if digest(model.parameters()) != before or params.as_dict() != requested:
        raise ValueError('Model/parameter mutation')
    checkpoint()
    return result


def match_candidate(result, window):
    """Conservative selection: unresolved work anywhere in this unit blocks it.

    Acquired candidates remain in result; no sub-interval coverage is inferred.
    Completion refers to the declared finite search, not all physical branches.
    """
    def selection(status, candidate=None, reason=None):
        return dict(status=status, candidate=candidate, reason=reason, selection_scope=SELECTION_SCOPE)
    if (result.get('status') != 'complete'
            or any(r.get('status') != 'evaluated' for r in result.get('grid', []))):
        return selection('not_resolved', reason='unit_search_incomplete')
    if any(c.get('status') != 'local_crossing_verified' or not c.get('root')
           or any(r.get('status') != 'evaluated' for r in
                  [c['root'], *c.get('sides', []), *c.get('trace', [])])
           for c in result['candidates']):
        return selection('not_resolved', reason='unit_has_unresolved_candidates')
    matches = [c for c in result['candidates'] if c.get('root') and c['root'].get('status')=='evaluated'
        and window['pressure_pa'][0] <= c['root']['pressure_pa'] <= window['pressure_pa'][1]
        and window['frequency_hz'][0] <= c['root']['discrete_frequency_hz'] <= window['frequency_hz'][1]]
    if len(matches) != 1:
        return selection('ambiguous' if matches else 'unavailable',
                         reason='multiple_window_matches' if matches else 'no_candidate_in_declared_window')
    c = matches[0]
    if (c.get('uncertainty_pressure_pa') is not None and
            not (window['pressure_pa'][0]<=c['bracket_pa'][0]<=c['bracket_pa'][1]<=window['pressure_pa'][1])):
        return selection('not_resolved', reason='window_intersects_pressure_bracket')
    if (c.get('frequency_bracket_hz') and not
            window['frequency_hz'][0]<=c['frequency_bracket_hz'][0]<=c['frequency_bracket_hz'][1]<=window['frequency_hz'][1]):
        return selection('not_resolved', reason='window_intersects_frequency_bracket')
    return selection(c['status'], candidate=c, reason=c.get('reason'))


def marginal_axis(model, params, rho, seed, evaluate, *, pressure_bounds, frequency_bounds,
                  max_iterations, max_evaluations):
    """Real-axis auxiliary marginal only; R0 remains the historical DC closure."""
    validate_model(model); validate_domain(params,rho)
    for values,name in ((pressure_bounds,'pressure bounds'),(frequency_bounds,'frequency bounds'),(seed,'seed')):
        if not isinstance(values,(list,tuple)) or len(values)!=2:
            raise ValueError(name+': two finite values required')
        for v in values: real(v,name)
    if not (0<pressure_bounds[0]<pressure_bounds[1]<pressure_limit(params)
            and 0<frequency_bounds[0]<frequency_bounds[1]<model.sample_rate_hz/2):
        raise ValueError('TMM domain bounds')
    integer(max_iterations, 'Newton iterations', 1, 16)
    integer(max_evaluations, 'TMM evaluations', 1, 256)
    k, _ = coefficients(params); fs = model.sample_rate_hz; count = 0; trace = []
    def residual(x):
        nonlocal count
        p, f = map(float, x)
        if not (pressure_bounds[0] < p < pressure_bounds[1] < pressure_limit(params)
                and 0 < frequency_bounds[0] < f < frequency_bounds[1] < fs/2):
            raise ValueError('TMM Newton outside declared real-frequency/free-pressure domain')
        if count >= max_evaluations: raise BudgetExhausted('TMM evaluation budget')
        count += 1
        z = evaluate(f)  # always real Hz, never complex s
        s = 2j*fs*math.tan(math.pi*f/fs)
        value, _ = characteristic(model, s, p, rho, params, z_override=z)
        return np.array([value.real, value.imag])/k
    x = np.array(seed, dtype=float)
    result = dict(status='not_resolved', scope='Real-axis TMM marginal candidate, not a TMM crossing or trajectory',
                  dc_closure='Historical loaded R0', trace=trace, reason=None)
    try:
        for _ in range(max_iterations):
            rr = residual(x); norm = float(np.linalg.norm(rr))
            trace.append(dict(pressure_pa=float(x[0]), frequency_hz=float(x[1]), relative_equation_residual=norm))
            if norm < 1e-10: break
            jac = np.column_stack([(residual(x+[.05, 0])-residual(x-[.05, 0]))/.1,
                                   (residual(x+[0, 1e-4])-residual(x-[0, 1e-4]))/2e-4])
            change = np.linalg.solve(jac, -rr); accepted = False
            for n in range(10):
                trial = x+change/2**n
                if (pressure_bounds[0] < trial[0] < pressure_bounds[1]
                        and frequency_bounds[0] < trial[1] < frequency_bounds[1]
                        and np.linalg.norm(residual(trial)) < norm):
                    x = trial; accepted = True; break
            if not accepted: raise ValueError('TMM Newton did not decrease')
        norm = float(np.linalg.norm(residual(x)))
        if norm >= 1e-8: raise ValueError('TMM Newton not converged')
        result.update(status='marginal_candidate', pressure_pa=float(x[0]), frequency_hz=float(x[1]),
                      relative_equation_residual=norm)
    except (ValueError, ArithmeticError, np.linalg.LinAlgError) as exc:
        result['reason'] = str(exc)
    result['evaluations'] = count
    return result
