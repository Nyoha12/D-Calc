"""Additive SI equilibrium/stability diagnostics for the existing V2/FIR code.

No trajectory, player score or physical onset prediction is produced here.
The sampled continuous impedance is used only on real frequencies. The exact
discrete characteristic can also be evaluated off the unit circle, with bounds.
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, replace
from typing import Callable

import numpy as np

from ..acoustics.air import AirProperties
from ..pipeline.design_input import finite_real
from .lips import DimensionedLipParameters, LipModelV2


def validate_parameters(params: DimensionedLipParameters, rho: float) -> None:
    if not isinstance(params, DimensionedLipParameters):
        raise ValueError("Explicit dimensioned_v2 parameters required")
    positive = {"mass_kg", "resonance_hz", "effective_area_m2", "lip_width_m", "flow_coefficient"}
    for key, value in params.as_dict().items():
        value = finite_real(value, key, positive=key in positive)
        if key != "pressure_force_sign" and value < 0:
            raise ValueError(f"{key}: negative value is not supported")
    if params.pressure_force_sign not in (-1., 1.):
        raise ValueError("pressure_force_sign: use the explicit existing sign -1 or +1")
    if params.mass_kg < 1e-12 or params.resonance_hz < 1e-6:
        raise ValueError("Parameters below the existing V2 numerical floors")
    if finite_real(rho, "rho", positive=True) < 1e-9:
        raise ValueError("rho below the existing V2 numerical floor")


def mechanics(params: DimensionedLipParameters) -> tuple[float, float, float, float]:
    model = LipModelV2(params)
    au = params.pressure_force_sign * params.effective_area_m2
    return model.stiffness_n_per_m(), model.damping_n_s_per_m(), au, -au


def _position(params: DimensionedLipParameters, delta: float) -> float:
    k, _, au, _ = mechanics(params)
    xt = params.min_opening_m - params.rest_opening_m
    free = au * delta / k
    return free if free >= xt else (au * delta + params.contact_stiffness_n_per_m * xt) / (k + params.contact_stiffness_n_per_m)


def _equilibrium(params, rho, pressure, downstream, flow, *, closure, dc, multiplicity=1):
    active = replace(params, mouth_pressure_kpa=pressure / 1000.)
    model = LipModelV2(active)
    delta = pressure - downstream
    x = _position(active, delta)
    h = params.rest_opening_m + x
    k, _, au, _ = mechanics(params)
    force_residual = float(model.derivatives(0., [x, 0.], active, downstream)[1] * params.mass_kg)
    expected_flow = model.flow([x, 0.], active, downstream, AirProperties(rho=rho, c=343.))
    flow_residual = float(flow - expected_flow)
    pressure_residual = float(downstream - dc * flow) if closure == "fir_dc" else float(downstream)
    scale_force = max(k * params.rest_opening_m, abs(au) * (pressure + abs(downstream)), 1e-12)
    scale_flow = max(abs(flow), abs(expected_flow), 1e-12)
    scaled = max(abs(force_residual) / scale_force, abs(flow_residual) / scale_flow,
                 abs(pressure_residual) / max(pressure, abs(downstream), 1.))
    htol = 1e-11 * max(params.rest_opening_m, params.min_opening_m, 1e-6)
    contact_boundary = abs(h - params.min_opening_m) <= htol
    flow_boundary = abs(h) <= htol
    contact = h < params.min_opening_m - htol
    reasons = []
    if contact_boundary:
        reasons.append("contact_boundary")
    if flow_boundary:
        reasons.append("flow_closure_boundary")
    if abs(delta) <= 1e-10 and h > htol:
        reasons.append("bernoulli_delta_zero")
    if contact and params.contact_damping_n_s_per_m > 0:
        reasons.append("contact_velocity_one_sided")
    regular_free = not reasons and h > params.min_opening_m and h > 0 and delta > 0
    return {
        "status": "equilibrium_solved" if scaled < 1e-8 else "not_resolved",
        "closure": closure, "pressure_pa": float(pressure), "downstream_pa": float(downstream),
        "delta_pa": float(delta), "x_m": float(x), "h_m": float(h), "flow_m3_s": float(flow),
        "mechanical_branch": "contact" if contact else "free",
        "flow_open": bool(h > htol), "regular_free": bool(regular_free),
        "boundary_status": "contact_boundary" if contact_boundary else ("non_regular_boundary" if reasons else None),
        "non_regular_reasons": reasons, "algebraic_multiplicity_t": multiplicity,
        "residuals": {"force_n": force_residual, "flow_m3_s": flow_residual,
                      "closure_pa": pressure_residual, "scaled_max": float(scaled)},
        "stability": "not_resolved",
    }


def equilibria(params: DimensionedLipParameters, rho: float, pressure_pa: float, *,
               closure: str, dc: float = 0.) -> dict:
    """Enumerate both cubic branches in t=sqrt(Pu-Pd), plus clipped zero flow.

    Roots of squared flow equations are never accepted without checking the
    unsquared production laws. Near multiple roots, numerical ambiguities are
    reported, not converted into a proof of uniqueness or absence.
    """
    validate_parameters(params, rho)
    pressure = finite_real(pressure_pa, "pressure_pa")
    dc = finite_real(dc, "dc")
    if pressure < 0 or closure not in {"reference_pd_zero", "fir_dc"}:
        raise ValueError("Nonnegative pressure and explicit reference_pd_zero/fir_dc closure required")
    if closure == "reference_pd_zero":
        if dc != 0:
            raise ValueError("reference_pd_zero requires dc=0")
        active = replace(params, mouth_pressure_kpa=pressure / 1000.)
        x = _position(active, pressure)
        flow = LipModelV2(active).flow([x, 0], active, 0., AirProperties(rho=rho, c=343.))
        row = _equilibrium(params, rho, pressure, 0., flow, closure=closure, dc=0.)
        row["branch_id"] = row["mechanical_branch"] + ":0"
        return {"status": row["status"], "closure": closure, "dc_pa_s_m3": 0.,
                "branches": [row], "branch_count": 1, "rejected": [], "ambiguous_roots": []}
    k, _, au, _ = mechanics(params)
    kc = params.contact_stiffness_n_per_m
    d = params.flow_coefficient * params.lip_width_m * math.sqrt(2. / rho)
    tscale = math.sqrt(max(pressure, k * max(params.rest_opening_m, 1e-6) / abs(au), 1.))
    candidates, rejected, ambiguous = [], [], []
    # Zero-flow branch includes P=0 and the strictly closed aperture.
    zero = _equilibrium(params, rho, pressure, 0., 0., closure=closure, dc=dc)
    if zero["status"] == "equilibrium_solved":
        candidates.append(zero)
    for name, b, c in (
        ("free", params.rest_opening_m, au / k),
        ("contact", (k * params.rest_opening_m + kc * params.min_opening_m) / (k + kc), au / (k + kc)),
    ):
        coefficients = np.array([dc * d * c * tscale**3, tscale**2, dc * d * b * tscale, -pressure])
        roots = np.roots(np.trim_zeros(coefficients / max(np.max(np.abs(coefficients)), 1.), "f"))
        clusters = []
        for root in roots:
            group = next((group for group in clusters if abs(root-group[0]) < 1e-6 * max(1., abs(root))), None)
            if group is None:
                clusters.append([root])
            else:
                group.append(root)
        for group in clusters:
            root = sum(group)/len(group)
            if abs(root.imag) > 1e-7 * max(1., abs(root.real)):
                if abs(root.imag) < 1e-4 * max(1., abs(root.real)):
                    ambiguous.append({"branch": name, "t_scaled_real": float(root.real), "t_scaled_imag": float(root.imag)})
                continue
            if root.real < -1e-10:
                continue
            t = max(float(root.real), 0.) * tscale
            h = b + c * t * t
            if h < -1e-14 or (name == "free" and h < params.min_opening_m - 1e-14) or (name == "contact" and h > params.min_opening_m + 1e-14):
                continue
            flow = d * max(h, 0.) * t
            mult = len(group)
            if mult > 1 and abs(root) > 1e-10:
                ambiguous.append({"branch": name, "reason": "near_multiple_algebraic_root",
                                  "multiplicity_cluster": mult, "t_scaled_real": float(root.real),
                                  "root_spread": float(max(abs(other-root) for other in group))})
            row = _equilibrium(params, rho, pressure, dc * flow, flow, closure=closure, dc=dc, multiplicity=int(mult))
            if row["status"] != "equilibrium_solved":
                rejected.append(row)
                continue
            duplicate = next((r for r in candidates if abs(r["flow_m3_s"] - flow) <= 1e-12 * max(1e-3, abs(flow))
                              and abs(r["x_m"] - row["x_m"]) <= 1e-12), None)
            if duplicate is None:
                candidates.append(row)
            else:
                duplicate["algebraic_multiplicity_t"] = max(mult, duplicate["algebraic_multiplicity_t"])
    candidates.sort(key=lambda row: row["flow_m3_s"])
    counts = {}
    for row in candidates:
        name = row["mechanical_branch"]
        row["branch_id"] = f"{name}:{counts.get(name, 0)}"
        counts[name] = counts.get(name, 0) + 1
    return {"status": "equilibrium_solved" if candidates and not ambiguous and not rejected else "not_resolved",
            "closure": closure, "dc_pa_s_m3": dc, "branches": candidates, "branch_count": len(candidates),
            "absence_in_algebraic_domain": not candidates and not ambiguous and not rejected,
            "rejected": rejected, "ambiguous_roots": ambiguous,
            "enumeration": "two cubic branches in sqrt(delta_pa), original-law residual checks; floating point"}


def free_tangent(params: DimensionedLipParameters, rho: float, equilibrium: dict) -> tuple[float, float]:
    validate_parameters(params, rho)
    if equilibrium["status"] != "equilibrium_solved" or not equilibrium["regular_free"]:
        raise ValueError("No regular free Jacobian at this equilibrium")
    delta = equilibrium["delta_pa"]
    return params.flow_coefficient * params.lip_width_m * math.sqrt(2. * delta / rho), equilibrium["flow_m3_s"] / (2. * delta)


def continuous_characteristic(s: complex, params, b: float, c: float, zd: complex, zu: complex = 0j) -> complex:
    k, r, au, ad = mechanics(params)
    return (params.mass_kg * s * s + r * s + k) * (1. + c * (zu + zd)) - b * (ad * zd - au * zu)


def free_step(params: DimensionedLipParameters, fs_hz: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Exact derivative of existing RK4/substeps with pressure held constant."""
    validate_parameters(params, 1.204)
    if isinstance(fs_hz, bool) or not isinstance(fs_hz, (int, np.integer)) or not 1 <= fs_hz <= 12000:
        raise ValueError("fs_hz: integer in [1, 12000] required")
    k, r, _, ad = mechanics(params)
    n = LipModelV2(params).integration_substeps(1. / fs_hz, params)
    # Augment by p'=0: the RK polynomial then includes the held-input integral.
    a = np.array([[0., 1., 0.], [-k / params.mass_kg, -r / params.mass_kg, ad / params.mass_kg], [0., 0., 0.]])
    ah = a / (fs_hz * n)
    step = np.eye(3) + ah + ah @ ah / 2. + np.linalg.matrix_power(ah, 3) / 6. + np.linalg.matrix_power(ah, 4) / 24.
    full = np.linalg.matrix_power(step, n)
    return full[:2, :2], full[:2, 2], n


def _kernel(kernel) -> np.ndarray:
    if np.iscomplexobj(kernel):
        raise ValueError("FIR samples must be real")
    h = np.asarray(kernel, dtype=float)
    if h.ndim != 1 or not 1 <= h.size <= 24000 or not np.all(np.isfinite(h)):
        raise ValueError("FIR must contain 1..24000 finite real samples")
    return h


def discrete_characteristic(z: complex, phi, g, b: float, c: float, kernel) -> tuple[complex, complex, float]:
    h = _kernel(kernel)
    if not np.isfinite(z) or abs(z) == 0 or -len(h) * math.log(abs(z)) > 500:
        raise ValueError("z outside the bounded FIR evaluation domain")
    j = np.arange(len(h))
    powers = np.exp(-j * np.log(complex(z)))
    hz = np.dot(h, powers)
    dh = np.dot(-j * h, powers) / z
    d = z*z - np.trace(phi)*z + np.linalg.det(phi)
    dd = 2*z - np.trace(phi)
    q = (z - phi[1, 1]) * g[0] + phi[0, 1] * g[1]
    coupling = c*d - b*z*q
    value = z*d + coupling*hz
    derivative = d + z*dd + (c*dd - b*(q + z*g[0]))*hz + coupling*dh
    scale = max(abs(z*d), abs(c*d*hz), abs(b*z*q*hz), 1e-30)
    return complex(value), complex(derivative), float(scale)


def short_fir_matrix(phi, g, b: float, c: float, kernel) -> np.ndarray:
    h = _kernel(kernel)
    if h.size > 64:
        raise ValueError("Dense matrix restricted to small FIR witnesses (<=64)")
    # State [x_n,v_n,U_n,...,U_(n-L+1)]; p_n=h dot flow_history.
    matrix = np.zeros((2 + len(h), 2 + len(h)))
    matrix[:2, :2] = phi
    matrix[:2, 2:] = np.outer(g, h)
    matrix[2, :2] = b * phi[0]
    matrix[2, 2:] = (b * g[0] - c) * h
    if len(h) > 1:
        matrix[3:, 2:-1] = np.eye(len(h) - 1)
    return matrix


def fir_audit(kernel, fs_hz: int, frequencies_hz, documented_hz, documented_z) -> dict:
    h = _kernel(kernel)
    freq = np.asarray(frequencies_hz, dtype=float)
    source_f = np.asarray(documented_hz, dtype=float)
    source_z = np.asarray(documented_z, dtype=complex)
    if not np.isfinite(fs_hz) or fs_hz <= 0 or freq.ndim != 1 or not len(freq) or len(freq) > 2048 or not np.all(np.isfinite(freq)) or np.any(freq < 0) or np.any(freq > fs_hz / 2):
        raise ValueError("FIR audit frequencies must lie in [0, Nyquist], <=2048 points")
    if source_f.ndim != 1 or len(source_f) < 2 or source_z.shape != source_f.shape or not np.all(np.isfinite(source_f)) or not np.all(np.isfinite(source_z)) or np.any(np.diff(source_f) <= 0):
        raise ValueError("Strictly increasing documented frequency samples required")
    rows = []
    j = np.arange(len(h))
    for f in freq:
        actual = complex(np.dot(h, np.exp(-2j * np.pi * f * j / fs_hz)))
        inside = source_f[0] <= f <= source_f[-1]
        target = complex(np.interp(f, source_f, source_z.real), np.interp(f, source_f, source_z.imag)) if inside else None
        rows.append({"frequency_hz": float(f), "real_pa_s_m3": actual.real, "imag_pa_s_m3": actual.imag,
                     "phase_rad": float(np.angle(actual)), "magnitude_pa_s_m3": abs(actual),
                     "documented": bool(inside), "target_real": target.real if target is not None else None,
                     "target_imag": target.imag if target is not None else None,
                     "relative_complex_error": abs(actual-target)/abs(target) if target is not None and abs(target) > 0 else None,
                     "phase_error_rad": float(np.angle(actual / target)) if target is not None and abs(target) > 0 else None,
                     "negative_real": actual.real < 0})
    return {"status": "numerical_model_only", "sample_rate_hz": int(fs_hz), "length": len(h),
            "duration_s": len(h) / fs_hz, "dc_pa_s_m3": float(np.sum(h)),
            "sha256_float64_le": hashlib.sha256(h.astype("<f8").tobytes()).hexdigest(),
            "documented_band_hz": [float(source_f[0]), float(source_f[-1])],
            "passivity": "refuted_at_sample" if any(row["negative_real"] for row in rows) else "not_certified_by_grid",
            "response_method": "direct complex DTFT; no mean/gain/passivity correction", "samples": rows}


@dataclass
class SearchBudget:
    max_evaluations: int
    seconds: float
    evaluations: int = 0

    def __post_init__(self):
        if isinstance(self.max_evaluations, bool) or not isinstance(self.max_evaluations, int) or not 1 <= self.max_evaluations <= 20000:
            raise ValueError("max_evaluations must be an integer in [1, 20000]")
        if not 0 < finite_real(self.seconds, "seconds") <= 180:
            raise ValueError("seconds must lie in (0, 180]")
        self.deadline = time.monotonic() + self.seconds

    def tick(self):
        if self.evaluations >= self.max_evaluations or time.monotonic() >= self.deadline:
            raise TimeoutError("Explicit diagnostic search budget exhausted")
        self.evaluations += 1


def _newton_box(fun: Callable, seed, lower, upper, max_iterations):
    span = upper - lower
    q = (np.asarray(seed) - lower) / span
    residual = None
    for iteration in range(max_iterations):
        value = fun(*(lower + span * q))
        residual = float(abs(value))
        if residual < 1e-8:
            return lower + span*q, residual, iteration + 1
        columns = []
        for axis in range(2):
            lo, hi = q.copy(), q.copy()
            lo[axis] = max(0., q[axis]-1e-5)
            hi[axis] = min(1., q[axis]+1e-5)
            deriv = (fun(*(lower+span*hi))-fun(*(lower+span*lo))) / (hi[axis]-lo[axis])
            columns.append([deriv.real, deriv.imag])
        jac = np.array(columns).T
        if np.linalg.cond(jac) > 1e12:
            break
        delta = np.linalg.solve(jac, [value.real, value.imag])
        accepted = False
        for factor in (1., .5, .25, .125, .0625):
            trial = q - factor*delta
            if np.all(trial >= 0) and np.all(trial <= 1) and abs(fun(*(lower+span*trial))) < residual:
                q = trial
                accepted = True
                break
        if not accepted:
            break
    return None, residual, iteration + 1


def search_marginals(params, rho, *, closure, dc, pressure_bounds, frequency_bounds,
                     pressure_seeds, frequency_seeds, max_iterations, budget: SearchBudget,
                     impedance: Callable[[float], complex] | None = None, kernel=None, fs_hz=None,
                     impedance_label="sampled_continuous_real_axis") -> dict:
    """Deterministic bounded candidates, not a complete root census.

    Branch IDs are ordered by flow within each mechanical branch. Newton trials
    crossing a change in the branch inventory are rejected and traced.
    """
    lower = np.array([pressure_bounds[0], frequency_bounds[0]], dtype=float)
    upper = np.array([pressure_bounds[1], frequency_bounds[1]], dtype=float)
    if not np.all(np.isfinite([lower, upper])) or np.any(upper <= lower) or lower[0] < 0 or lower[1] <= 0:
        raise ValueError("Finite increasing pressure/frequency domain required")
    if not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 32 for n in (pressure_seeds, frequency_seeds, max_iterations)):
        raise ValueError("Seed counts and iterations must be integers in [1,32]")
    discrete = kernel is not None
    if discrete:
        h = _kernel(kernel)
        phi, g, _ = free_step(params, fs_hz)
        if upper[1] >= fs_hz/2:
            raise ValueError("Discrete search must be strictly below Nyquist")
    elif impedance is None:
        raise ValueError("Real-frequency impedance required for continuous search")
    rows, traces = [], []
    completed_regular_trials = 0
    exhausted = False
    pgrid = np.linspace(*pressure_bounds, pressure_seeds)
    fgrid = np.linspace(*frequency_bounds, frequency_seeds)
    try:
        for p in pgrid:
            budget.tick()
            eqs = equilibria(params, rho, float(p), closure=closure, dc=dc)
            signature = [(r["branch_id"], r["regular_free"]) for r in eqs["branches"]]
            for eq in eqs["branches"]:
                if not eq["regular_free"]:
                    traces.append({"seed_pressure_pa": float(p), "branch_id": eq["branch_id"], "status": "non_regular_boundary" if eq["non_regular_reasons"] else "not_resolved"})
                    continue
                branch = eq["branch_id"]
                def evaluate(ptrial, ftrial):
                    budget.tick()
                    current = equilibria(params, rho, float(ptrial), closure=closure, dc=dc)
                    if current["status"] != "equilibrium_solved" or [(r["branch_id"], r["regular_free"]) for r in current["branches"]] != signature:
                        raise ValueError("branch inventory/regularity changed; continuation unresolved")
                    row = next(r for r in current["branches"] if r["branch_id"] == branch)
                    b, c = free_tangent(params, rho, row)
                    if discrete:
                        v, _, scale = discrete_characteristic(np.exp(2j*np.pi*ftrial/fs_hz), phi, g, b, c, h)
                    else:
                        zd = impedance(float(ftrial))  # Never a complex TMM frequency.
                        v = continuous_characteristic(2j*np.pi*ftrial, params, b, c, zd)
                        k, r, au, _ = mechanics(params)
                        dl = params.mass_kg*(2j*np.pi*ftrial)**2+r*2j*np.pi*ftrial+k
                        scale = max(abs(dl), abs(dl*c*zd), abs(au*b*zd), 1e-30)
                    return v / scale
                for f in fgrid:
                    trace = {"seed_pressure_pa": float(p), "seed_frequency_hz": float(f), "branch_id": branch}
                    try:
                        root, residual, iterations = _newton_box(evaluate, (p, f), lower, upper, max_iterations)
                        completed_regular_trials += 1
                        trace.update(status="marginal_candidate" if root is not None else "not_resolved", residual=residual, iterations=iterations)
                        if root is not None and not any(r["branch_id"] == branch and abs(r["pressure_pa"]-root[0]) < 1e-3 and abs(r["frequency_hz"]-root[1]) < 1e-4 for r in rows):
                            rows.append({"status": "marginal_candidate", "pressure_pa": float(root[0]), "frequency_hz": float(root[1]),
                                         "branch_id": branch, "scaled_residual": residual, "crossing": "not_resolved"})
                    except ValueError as exc:
                        trace.update(status="not_resolved", reason=str(exc))
                    traces.append(trace)
    except (TimeoutError, KeyboardInterrupt, MemoryError) as exc:
        exhausted = True
        traces.append({"status": "not_resolved", "reason": type(exc).__name__ + ": " + str(exc)})
    return {"status": "not_resolved" if exhausted or not completed_regular_trials else ("marginal_candidate" if rows else "no_candidate_in_tested_domain"),
            "representation": "exact_discrete_fir" if discrete else impedance_label,
            "closure": closure, "candidates": rows, "trace": traces, "budget_exhausted": exhausted,
            "evaluations_total": budget.evaluations, "complete_root_census": False,
            "domain": {"pressure_pa": list(pressure_bounds), "frequency_hz": list(frequency_bounds)},
            "absence_is_proof": False}


def verify_discrete_crossing(params, rho, kernel, fs_hz, candidate, *, pressure_step_pa, budget: SearchBudget,
                             frequency_bounds=None) -> dict:
    """Follow one simple FIR root on both sides; says nothing about other roots."""
    h = _kernel(kernel)
    dc = float(h.sum())
    phi, g, _ = free_step(params, fs_hz)
    p0, f0 = candidate["pressure_pa"], candidate["frequency_hz"]
    step = finite_real(pressure_step_pa, "pressure_step_pa", positive=True)
    result = {"status": "not_resolved", "scope": "one local discrete root pair; not whole-loop stability", "tracks": [], "budget_exhausted": False}
    if p0 <= step:
        result["reason"] = "pressure continuation outside nonnegative domain"
        return result
    center = equilibria(params, rho, p0, closure="fir_dc", dc=dc)
    signature = [(r["branch_id"], r["regular_free"]) for r in center["branches"]]
    z0 = np.exp(2j*np.pi*f0/fs_hz)
    frequency_bounds = frequency_bounds or (0., fs_hz/2)
    local_radius = min(.01, abs(z0.imag)/8., np.pi/(4*len(h)))
    result["frequency_domain_hz"] = list(frequency_bounds)
    result["local_radius_z"] = local_radius
    try:
        if center["status"] != "equilibrium_solved" or not 0 < f0 < fs_hz/2:
            raise ValueError("invalid center equilibrium/frequency")
        eq0 = next(r for r in center["branches"] if r["branch_id"] == candidate["branch_id"])
        b0, c0 = free_tangent(params, rho, eq0)
        budget.tick()
        value0, derivative0, scale0 = discrete_characteristic(z0, phi, g, b0, c0, h)
        if abs(value0)/scale0 > 1e-7 or abs(derivative0)/scale0 < 1e-8:
            raise ValueError("center is not a resolved simple marginal root")
        result["center_scaled_residual"] = abs(value0)/scale0
        for side in (-1., 1.):
            z = complex(z0)
            for offset in (step/4., step/2., step):
                p = p0 + side*offset
                eqs = equilibria(params, rho, p, closure="fir_dc", dc=dc)
                if eqs["status"] != "equilibrium_solved" or [(r["branch_id"], r["regular_free"]) for r in eqs["branches"]] != signature:
                    raise ValueError("equilibrium branch inventory changed")
                eq = next(r for r in eqs["branches"] if r["branch_id"] == candidate["branch_id"])
                b, c = free_tangent(params, rho, eq)
                for _ in range(24):
                    budget.tick()
                    value, derivative, scale = discrete_characteristic(z, phi, g, b, c, h)
                    frequency = float(np.angle(z)*fs_hz/(2*np.pi))
                    if abs(z-z0) > local_radius or not frequency_bounds[0] <= frequency <= frequency_bounds[1]:
                        raise ValueError("root continuation left its local frequency/domain neighborhood")
                    if abs(derivative)/scale < 1e-8:
                        raise ValueError("multiple/ill-conditioned root")
                    if abs(value)/scale < 1e-9:
                        break
                    dz = value/derivative
                    if abs(dz) > local_radius:
                        raise ValueError("root continuation jump")
                    z -= dz
                else:
                    raise ValueError("root continuation did not converge")
                if abs(z.imag) < 1e-7:
                    raise ValueError("root pair meets real axis")
                result["tracks"].append({"pressure_pa": p, "z_real": z.real, "z_imag": z.imag,
                                         "modulus": abs(z), "log_modulus": math.log(abs(z)), "scaled_residual": abs(value)/scale})
        low, high = result["tracks"][:3], result["tracks"][3:]
        changes = all(a["log_modulus"]*b["log_modulus"] < 0 for a, b in zip(low, high))
        slopes = [(b["log_modulus"]-a["log_modulus"])/(b["pressure_pa"]-a["pressure_pa"]) for a,b in zip(low, high)]
        if changes and min(abs(s) for s in slopes) > 1e-10 and max(abs(s) for s in slopes)/min(abs(s) for s in slopes) < 2:
            result.update(status="local_crossing_verified", direction="outward" if slopes[-1] > 0 else "inward", slope_log_modulus_per_pa=slopes[-1])
    except (ValueError, TimeoutError, KeyboardInterrupt, MemoryError, StopIteration) as exc:
        result["reason"] = str(exc)
        result["budget_exhausted"] = isinstance(exc, (TimeoutError, KeyboardInterrupt, MemoryError))
    return result


def modal_roots(params, rho, pressure_pa, *, a, gamma, omega_a):
    """Synthetic rational witness only; not a fit to the TMM or a vocal model."""
    for name, value in (("a", a), ("gamma", gamma), ("omega_a", omega_a)):
        finite_real(value, name, positive=True)
    eq = equilibria(params, rho, pressure_pa, closure="reference_pd_zero")["branches"][0]
    b, c = free_tangent(params, rho, eq)
    k, r, au, _ = mechanics(params)
    coefficients = np.polymul([params.mass_kg, r, k], [1., gamma + c*a, omega_a**2])
    coefficients[-2] += au*b*a
    return np.roots(coefficients)


def modal_crossing(params, rho, *, pressure_bounds, a, gamma, omega_a, iterations=48):
    lo, hi = map(float, pressure_bounds)
    if lo < 0 or hi <= lo or not 1 <= iterations <= 64:
        raise ValueError("Bounded modal witness bracket/iterations required")
    def roots(p):
        return modal_roots(params, rho, p, a=a, gamma=gamma, omega_a=omega_a)
    flo = float(max(roots(lo).real))
    fhi = float(max(roots(hi).real))
    if flo*fhi >= 0:
        return {"status": "no_candidate_in_tested_domain", "representation": "synthetic_modal"}
    for _ in range(iterations):
        mid = (lo+hi)/2
        fm = float(max(roots(mid).real))
        if fm*flo > 0:
            lo, flo = mid, fm
        else:
            hi, fhi = mid, fm
    p = (lo+hi)/2
    values = roots(p)
    root = max(values, key=lambda z: (z.real, z.imag))
    offset = min(10., (pressure_bounds[1]-pressure_bounds[0])/10)
    below, above = roots(p-offset), roots(p+offset)
    simple = min(abs(root-other) for other in values if other != root) > 1e-5
    resolved = abs(root.real) < 1e-7
    crossing = resolved and simple and root.imag != 0 and max(below.real)*max(above.real) < 0
    return {"status": "not_resolved" if not resolved else ("local_crossing_verified" if crossing else "marginal_candidate"),
            "representation": "synthetic_modal", "pressure_pa": p, "frequency_hz": abs(root.imag)/(2*np.pi),
            "offset_pa": offset, "max_real_below_per_s": float(max(below.real)),
            "max_real_above_per_s": float(max(above.real)), "root_real_per_s": float(root.real)}
