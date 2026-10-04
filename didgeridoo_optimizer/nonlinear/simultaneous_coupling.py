"""Opt-in simultaneous V2 midpoint port with native-law segment contact.

The default jet-only path is retained. Optional conjugate ports balance the
declared lip/contact and resonator energy. Contact flags describe segments,
not localized continuous events. See project_specs/NL_PORTS_01.md.
"""
from __future__ import annotations

import copy
import math
import time

import numpy as np

from ..acoustics.air import AirProperties
from .lips import DimensionedLipParameters, LipModelV2
from .onset_stability import validate_parameters
from .lip_ports import validate_port_model, solve_port, uniqueness_feedback, history_pressure, flows
from .passive_resonator import PassiveResonator, integer, real, vector

EPS = np.finfo(float).eps


def contact_forces(x0, x1, dt, *, threshold, stiffness, damping):
    """Factorized negative discrete gradient and native closing-loss average.

    No division by displacement on same-side or stationary segments. At the
    boundary, an entering segment has positive contact measure; a stationary
    segment has zero damping. No smoothing or rounding snap is applied.
    """
    y0, y1 = threshold-x0, threshold-x1
    if y0 <= 0 and y1 <= 0:
        fc = 0.
    elif y0 >= 0 and y1 >= 0:
        fc = stiffness*(y0+y1)/2
    elif y0 > 0:
        fc = .5*stiffness*y0*(y0/(y0-y1))
    else:
        fc = .5*stiffness*y1*(y1/(y1-y0))
    vm = (x1-x0)/dt
    fd = damping*max(-vm-max(x0-threshold, 0.)/dt, 0.)
    return fc, fd


def _private_state(model, q, v, loss):
    """One controlled adapter: native recurrence, independent writable state.

    Shallow coefficient sharing is safe: PassiveResonator coefficients are
    read-only and its step replaces (never edits) state arrays. Metadata and
    file guards are retained. This is the only protected-state initialization.
    """
    q, v = vector(q, 'q', empty=True), vector(v, 'resonator v', empty=True)
    if q.shape != model.a.shape or v.shape != q.shape:
        raise ValueError('Complete resonator state shape required')
    loss = real(loss, 'last_dissipation_w', minimum=0.)
    trial = copy.copy(model)
    trial._q, trial._v, trial.last_dissipation_w = q.copy(), v.copy(), loss
    if not np.isfinite(trial.energy()):
        raise ValueError('Unrepresentable resonator energy')
    return trial


def _residual(value, *terms):
    scale = max(*(abs(float(x)) for x in terms), np.finfo(float).tiny)
    return dict(absolute=abs(float(value)), normalized=abs(float(value))/scale)


class SimultaneousCoupling:
    """Independent complete state [x,v,q...,resonator_v...], one native fs.

    Default initialization is the historical V2 normal start (0.02*h0, .001)
    with the resonator at rest. Explicit state overrides every physical state.
    A refused step leaves state, time, losses and last diagnostics untouched.
    """
    def __init__(self, model, *, params, rho, initial_state=None,
                 max_extensions=32, max_iterations=80, v2_port_model='jet-only'):
        self.v2_port_model = validate_port_model(v2_port_model)
        if not isinstance(model, PassiveResonator):
            raise ValueError('Simultaneous schedule requires PassiveResonator; no FIR fallback')
        validate_parameters(params, rho)
        model.verify_file_unchanged()
        self.params = params
        self.rho = real(rho, 'rho', minimum=1e-9)
        self.lip = LipModelV2(params)
        self.air = AirProperties(rho=self.rho, c=343.)
        self.sample_rate_hz = model.sample_rate_hz
        self.dt = 1/self.sample_rate_hz
        self.tau = self.dt/2
        self.max_extensions = integer(max_extensions, 'max_extensions', 0, 64)
        self.max_iterations = integer(max_iterations, 'max_iterations', 1, 100)
        self._source = model
        self._backend = model.copy()
        self.a, self.gamma, self.omega = model.a, model.gamma, model.omega
        self.n = len(self.a)
        self.m = params.mass_kg
        self.K = self.lip.stiffness_n_per_m()
        self.r = self.lip.damping_n_s_per_m()
        self.Pu = 1000*params.mouth_pressure_kpa
        self.Au = params.pressure_force_sign*params.effective_area_m2
        self.Ad = -self.Au
        self.xt = params.min_opening_m-params.rest_opening_m
        with np.errstate(over='raise', invalid='raise', divide='raise'):
            self.den = 1+self.tau*self.gamma+self.tau**2*self.omega**2
            self.D = float(model.R0+np.sum(self.a*self.tau/self.den))
            self.k = params.flow_coefficient*params.lip_width_m*math.sqrt(2/self.rho)
            self.M = self.m+self.tau*self.r+self.tau**2*self.K
        if not np.all(np.isfinite([self.Pu, self.M, self.D, self.k])) or self.M <= 0:
            raise ValueError('Unrepresentable coupling coefficients')
        normal = np.r_[.02*params.rest_opening_m, .001, np.zeros(2*self.n)]
        self.initialize(normal if initial_state is None else initial_state)
        self._initial = self.state

    @property
    def state(self):
        return np.r_[self._lip_state, *self._backend.state]

    @property
    def diagnostics(self):
        return copy.deepcopy(self._diagnostics)

    @property
    def last_dissipation_w(self):
        return self._backend.last_dissipation_w

    def snapshot(self):
        return dict(state=self.state.tolist(), time_s=self.time_s,
                    last_dissipation_w=self.last_dissipation_w,
                    diagnostics=self.diagnostics)

    def potential(self, x):
        return .5*self.params.contact_stiffness_n_per_m*max(self.xt-x, 0.)**2

    def lip_energy(self, state):
        x, v = state[:2]
        return float((self.m*v*v+self.K*x*x)/2+self.potential(x))

    def initialize(self, state, *, time_s=0., last_dissipation_w=0.):
        state = vector(state, 'complete state')
        if state.shape != (2+2*self.n,):
            raise ValueError('Complete state shape required')
        clock = real(time_s, 'time_s', minimum=0.)
        with np.errstate(over='raise', invalid='raise'):
            backend = _private_state(self._backend, state[2:2+self.n], state[2+self.n:], last_dissipation_w)
            if not np.isfinite(self.lip_energy(state)):
                raise ValueError('Unrepresentable lip energy')
        self._backend, self._lip_state = backend, state[:2].copy()
        self.time_s, self._diagnostics = clock, None

    def reset(self):
        self.initialize(self._initial)

    def _port(self, vm, x0, ph):
        return solve_port(vm, x0, ph, Pu=self.Pu, D=self.D, k=self.k,
                          tau=self.tau, h0=self.params.rest_opening_m,
                          Ad=self.Ad, port_model=self.v2_port_model)

    def _feedback(self, x0, ph):
        return uniqueness_feedback(x0, ph, Pu=self.Pu, D=self.D, k=self.k,
            tau=self.tau, h0=self.params.rest_opening_m, Ad=self.Ad,
            port_model=self.v2_port_model)

    def _boundaries(self, x0, x1, vm, delta, ph):
        # These guards affect labels only, never the force, opening or flow.
        tol = 8*EPS*max(abs(x0), abs(x1), self.params.rest_opening_m,
                        self.params.min_opening_m, np.finfo(float).tiny)
        reasons = []
        for label, edge in [('contact', self.xt), ('flow_closure', -self.params.rest_opening_m)]:
            if min(x0, x1)-tol <= edge <= max(x0, x1)+tol:
                reasons.append(label+'_segment_boundary_or_roundoff')
        if delta <= 8*EPS*max(abs(self.Pu), abs(ph), 1.):
            reasons.append('bernoulli_delta_nonpositive_or_roundoff')
        if min(x0, x1) <= self.xt+tol and abs(vm) <= 8*EPS*max(abs(x0)/self.dt, 1.):
            reasons.append('contact_velocity_zero_or_roundoff')
        if x0 == x1:
            fraction = float(x0 < self.xt)
        elif max(x0, x1) <= self.xt:
            fraction = 1.
        elif min(x0, x1) >= self.xt:
            fraction = 0.
        else:
            fraction = (self.xt-min(x0, x1))/abs(x1-x0)
        return dict(non_regular_reasons=reasons, opening_roundoff_m=tol,
                    contact_segment_fraction=fraction,
                    contact_transition=('contact' if x0 < self.xt else 'free')+'->'+('contact' if x1 < self.xt else 'free'),
                    event_localization='not_performed; flags describe discrete segment only')

    def step(self):
        try:
            with np.errstate(over='raise', invalid='raise', divide='raise'):
                return self._step()
        except (ValueError, ArithmeticError, FloatingPointError, OSError, MemoryError) as exc:
            return dict(ok=False, status='non_resolu', reason=type(exc).__name__+': '+str(exc),
                        time_s=self.time_s, state_unchanged=True, v2_port_model=self.v2_port_model)

    def _step(self):
        self._source.verify_file_unchanged()
        x0, v0 = self._lip_state
        q0, rv0 = self._backend.state
        ph = float(np.sum(self.a*(rv0-self.tau*self.omega**2*q0)/self.den))
        feedback = self._feedback(x0, ph)
        margin = self.M-feedback
        # Account for products/subtraction/sum rounding; conservative sufficient
        # certificate only. Failure says nothing about physical root existence.
        guard = 64*EPS*(self.M+abs(feedback))
        if not np.isfinite(margin+guard) or margin <= guard:
            raise ValueError('sufficient_uniqueness_certificate_not_established')
        rhs = self.m*v0-self.tau*self.K*x0+self.tau*self.Au*self.Pu
        def evaluate(vm):
            x1 = x0+self.dt*vm
            fc, fd = contact_forces(x0, x1, self.dt, threshold=self.xt,
                stiffness=self.params.contact_stiffness_n_per_m,
                damping=self.params.contact_damping_n_s_per_m)
            # Use solved velocity, avoiding displacement subtraction at rest.
            fd = self.params.contact_damping_n_s_per_m*max(-vm-max(x0-self.xt, 0.)/self.dt, 0.)
            p, u, delta, h = self._port(vm, x0, ph)
            value = self.M*vm-self.tau*(fc+fd)-rhs-self.tau*self.Ad*p
            if not np.all(np.isfinite([value, p, u, delta, h, fc, fd, x1])):
                raise ValueError('Nonfinite scalar trial')
            return value, p, u, delta, h, fc, fd
        radius = max(abs(v0), self.params.rest_opening_m/self.dt, abs(rhs)/self.M, 1e-3)
        lo, hi = -radius, radius
        for extensions in range(self.max_extensions+1):
            flo, fhi = evaluate(lo)[0], evaluate(hi)[0]
            if flo <= 0 <= fhi:
                break
            lo *= 2; hi *= 2
        else:
            raise ValueError('deterministic_bracket_budget_exhausted')
        for iterations in range(1, self.max_iterations+1):
            vm = lo+(hi-lo)/2
            trial_values = evaluate(vm)
            f, trial_p, _, _, _, trial_fc, trial_fd = trial_values
            scale = max(abs(self.M*vm), abs(self.m*v0), abs(self.tau*self.K*x0),
                        abs(self.tau*self.Au*self.Pu), abs(self.tau*self.Ad*trial_p),
                        abs(self.tau*(trial_fc+trial_fd)), np.finfo(float).tiny)
            if abs(f) <= 4*EPS*scale or vm == lo or vm == hi:
                break
            if f > 0:
                hi = vm
            else:
                lo = vm
        else:
            raise ValueError('scalar_iteration_budget_exhausted')
        f, p, u, delta, h, fc, fd = evaluate(vm)
        x1, v1 = x0+self.dt*vm, 2*vm-v0
        lip1 = np.array([x1, v1])
        # Native backend commits only to a private candidate. No recurrence copy.
        trial = copy.copy(self._backend)
        port = flows(u, vm, self.Au, self.Ad, self.v2_port_model)
        ud, uu = port['downstream_flow_m3_s'], port['upstream_flow_m3_s']
        native_p = trial.step(ud)
        er0, er1 = self._backend.energy(), trial.energy()
        el0, el1 = self.lip_energy(self._lip_state), self.lip_energy(lip1)
        pressure_work = self.dt*(self.Au*self.Pu+self.Ad*p)*vm
        lip_loss = self.dt*self.r*vm*vm
        contact_loss = -self.dt*fd*vm
        resonator_work = self.dt*native_p*ud
        resonator_loss = self.dt*trial.last_dissipation_w
        # Native flow is also evaluated. Near delta=0, its subtractive pressure
        # input is uncertain; the retained quadratic delta is the primary oracle.
        native_u = self.lip.flow([x0+self.tau*vm, vm], self.params, p, self.air)
        ph_eff = history_pressure(ph, self.D, self.Ad, vm, self.v2_port_model)
        residuals = dict(
            mechanical=_residual(f, self.M*vm, self.tau*(fc+fd), rhs, self.m*v0, self.tau*self.K*x0, self.tau*self.Au*self.Pu, self.tau*self.Ad*p, self.m*1e-12),
            pressure=_residual(p-native_p, p, native_p, ph, self.D*ud, 1.),
            bernoulli=_residual(delta+self.D*u-(self.Pu-ph_eff), delta, self.D*u, self.Pu-ph_eff, 1.) if self.Pu > ph_eff and h > 0 else _residual(0., 1.),
            flow=_residual(u-self.k*max(h, 0.)*math.sqrt(delta), u, 1e-30) if self.Pu > ph_eff else _residual(u, 1e-30),
            lip_energy=_residual(el1-el0-pressure_work+lip_loss+contact_loss, el0, el1, pressure_work, lip_loss, contact_loss),
            resonator_energy=_residual(er1-er0-resonator_work+resonator_loss, er0, er1, resonator_work, resonator_loss))
        # Extra diagnostics must not introduce new refusals in the frozen
        # jet-only algorithm. Unrepresentable optional values remain unavailable.
        with np.errstate(over='ignore', invalid='ignore'):
            source_work = self.dt*self.Pu*uu
            jet_loss = self.dt*(self.Pu-p)*u
            total_energy = el1+er1
            total_change = el1-el0+er1-er0
        if self.v2_port_model == 'jet-only':
            source_work, jet_loss, total_energy, total_change = (
                float(value) if np.isfinite(value) else None
                for value in (source_work, jet_loss, total_energy, total_change))
        balance = None
        if self.v2_port_model == 'conjugate':
            power_defect = self.Pu*uu-p*ud-(self.Pu-p)*u-(self.Au*self.Pu+self.Ad*p)*vm
            residuals['port_power'] = _residual(power_defect, self.Pu*uu, p*ud,
                (self.Pu-p)*u, (self.Au*self.Pu+self.Ad*p)*vm, 1e-30)
            balance = _residual(el1-el0+er1-er0-source_work+jet_loss+lip_loss+contact_loss+resonator_loss,
                el0+er0, el1+er1, source_work, jet_loss, lip_loss, contact_loss, resonator_loss)
            residuals['total_energy'] = balance
        if any(not np.isfinite(v['absolute']+v['normalized']) or v['normalized'] > 2e-10 for v in residuals.values()):
            raise ValueError('scaled_residual_check_failed: '+str(residuals))
        clock = self.time_s+self.dt
        diag = dict(ok=True, status='resolved', time_s=clock, midpoint_time_s=self.time_s+self.tau,
            pressure_pa=p, flow_m3_s=ud, **port, v2_port_model=self.v2_port_model,
            source_work_j=source_work, jet_loss_j=jet_loss, total_energy_j=total_energy,
            total_energy_change_j=total_change, total_energy_residual=balance,
            energy_balance_status=('conjugate_reduced_model' if balance is not None else 'unavailable_nonconjugate'),
            delta_pa=delta, delta_clipped_pa=delta,
            signed_pressure_difference_pa=self.Pu-p,
            bernoulli_branch='active' if self.Pu > ph_eff and h > 0 else ('closed' if h <= 0 else 'nonpositive_pressure_difference'),
            opening_mid_m=h,
            opening_m=self.params.rest_opening_m+x1, velocity_m_s=v1, velocity_mid_m_s=vm,
            lip_energy_j=el1, resonator_energy_j=er1,
            lip_energy_change_j=el1-el0, resonator_energy_change_j=er1-er0,
            pressure_work_j=pressure_work, mechanical_pressure_term_w=self.Au*(self.Pu-p)*vm,
            lip_loss_j=lip_loss, contact_loss_j=contact_loss,
            resonator_work_j=resonator_work, resonator_loss_j=resonator_loss,
            native_flow_difference_m3_s=u-native_u,
            pressure_subtraction_uncertainty_pa=8*EPS*max(abs(self.Pu), abs(p), 1.),
            residuals=residuals, uniqueness_margin=margin, uniqueness_roundoff_guard=guard,
            bracket_extensions=extensions, iterations=iterations,
            **self._boundaries(x0, x1, vm, delta, ph_eff))
        finite = [clock, *lip1, *trial.state[0], *trial.state[1], trial.last_dissipation_w]
        finite += [v for v in diag.values() if isinstance(v, (float, np.floating))]
        if not np.all(np.isfinite(finite)) or min(lip_loss, contact_loss, resonator_loss) < 0 or (self.v2_port_model == 'conjugate' and jet_loss < 0) or clock <= self.time_s:
            raise ValueError('Nonfinite/unrepresentable candidate state or diagnostics')
        self._source.verify_file_unchanged()
        result = copy.deepcopy(diag)
        self._backend, self._lip_state = trial, lip1
        self.time_s, self._diagnostics = clock, diag
        return result

    def simulate(self, duration_s, *, max_steps=2400, seconds=30.):
        duration = real(duration_s, 'duration_s', minimum=0.)
        if not 0 < duration <= .2:
            raise ValueError('Simulation duration must lie in (0,.2] seconds')
        max_steps = integer(max_steps, 'max_steps', 0, 38400)
        seconds = real(seconds, 'seconds', minimum=0.)
        if seconds > 180:
            raise ValueError('Simulation wall budget <=180 seconds')
        requested_steps = int(math.floor(duration*self.sample_rate_hz+8*EPS*duration*self.sample_rate_hz))
        if requested_steps < 1:
            raise ValueError('Duration shorter than one native sample')
        initial = self.snapshot()
        states = [self.state.tolist()]
        times = [self.time_s]
        rows = []
        refusal = None
        started = time.monotonic()
        for _ in range(requested_steps):
            if len(rows) >= max_steps or time.monotonic()-started >= seconds:
                refusal = dict(ok=False, status='non_resolu', reason='simulation_budget_exhausted', time_s=self.time_s)
                break
            try:
                result = self.step()
            except (KeyboardInterrupt, MemoryError) as exc:
                refusal = dict(ok=False, status='non_resolu', reason=type(exc).__name__, time_s=self.time_s)
                break
            if not result['ok']:
                refusal = result
                break
            rows.append(result)
            states.append(self.state.tolist())
            times.append(self.time_s)
            if time.monotonic()-started >= seconds:
                refusal = dict(ok=False, status='non_resolu', reason='simulation_wall_budget_exhausted', time_s=self.time_s)
                break
        return dict(ok=refusal is None, status='resolved' if refusal is None else 'non_resolu',
            reason=None if refusal is None else refusal['reason'], refusal=refusal,
            requested_duration_s=duration, duration_s=len(rows)/self.sample_rate_hz,
            requested_sample_rate_hz=self.sample_rate_hz, sample_rate_hz=self.sample_rate_hz,
            requested_pressure_pa=self.Pu, pressure_pa=self.Pu,
            initial=initial, final=self.snapshot(), time_s=times, states=states,
            midpoint_time_s=[r['midpoint_time_s'] for r in rows],
            midpoint_pressure_pa=[r['pressure_pa'] for r in rows],
            flow_m3_s=[r['flow_m3_s'] for r in rows],
            **{key: [r[key] for r in rows] for key in ('jet_m3_s', 'upstream_flow_m3_s',
                'downstream_flow_m3_s', 'induced_upstream_m3_s', 'induced_downstream_m3_s')},
            v2_port_model=self.v2_port_model, flow_m3_s_meaning='signed total downstream port',
            energy_balance_status=('conjugate_reduced_model' if self.v2_port_model == 'conjugate' else 'unavailable_nonconjugate'),
            energy_balance_available=self.v2_port_model == 'conjugate',
            physical_validation='not_validated', parameters_calibration='to_calibrate',
            units=dict(flow='m^3/s', pressure='Pa', energy='J', time='s'),
            opening_m=[self.params.rest_opening_m+s[0] for s in states],
            velocity_m_s=[s[1] for s in states],
            lip_energy_j=[self.lip_energy(s) for s in states],
            resonator_energy_j=[float(np.sum(self.a*(np.asarray(s[2+self.n:])**2+self.omega**2*np.asarray(s[2:2+self.n])**2))/2) for s in states],
            step_statuses=[r['status'] for r in rows], steps=rows,
            contact_fraction=float(np.mean([r['contact_segment_fraction'] for r in rows])) if rows else None,
            event_localization='not_performed; discrete segments are not continuous event certificates',
            schedule='simultaneous', pressure_port='midpoint', parameters=self.params.as_dict(), rho_kg_m3=self.rho,
            surrogate_excitation_used=False, global_passivity_claimed=False)
