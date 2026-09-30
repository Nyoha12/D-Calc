"""Deterministic bounded positive modal fitting, with a full-dictionary KKT audit."""
from __future__ import annotations

import time
from numbers import Number
import numpy as np

from .passive_resonator import PassiveResonator, real, integer, vector, digest

KKT_LIMIT = 5e-10
DEFAULT_GATES = dict(complex_nrmse=.005, relative_max=.08, phase_rms_deg=.3)


class FitBudget:
    def __init__(self, seconds=120.):
        self.seconds = real(seconds, 'seconds', minimum=0.)
        if self.seconds > 180:
            raise ValueError('Fit child budget exceeds 180 seconds')
        self.started = time.monotonic()

    def check(self):
        if time.monotonic()-self.started >= self.seconds:
            raise TimeoutError('Fit budget exhausted; partial coefficients are not certified')


def spectrum(f, z, *, fs=None):
    f = vector(f, 'frequency')
    typed = np.asarray(z,dtype=object)
    if any(isinstance(x,(bool,np.bool_)) or not isinstance(x,Number) for x in typed.flat):
        raise ValueError('Impedance requires numeric scalars, not bool or string')
    raw = np.asarray(z)
    if raw.dtype.kind not in 'fciu' or raw.shape != f.shape or not np.all(np.isfinite(raw)):
        raise ValueError('Finite complex spectrum required')
    if len(f) > 20000 or np.any(f <= 0) or np.any(np.diff(f) <= 0) or (fs is not None and np.any(f >= fs/2)):
        raise ValueError('Frequency grid must be ordered, unique, positive and inside Nyquist')
    if not np.any(abs(raw) > 0):
        raise ValueError('Zero spectrum cannot define relative fidelity')
    return f, np.asarray(raw, complex)


def spectrum_identity(f, z):
    return digest(dict(frequency_hz=f.tolist(), real=z.real.tolist(), imag=z.imag.tolist(), units='Pa.s/m^3'))


def kkt_certificate(A, b, x):
    norms = np.linalg.norm(A, axis=0)
    g = (A/norms).T@(A@x-b)
    active = x > 0
    normalizer = max(float(np.linalg.norm(b)), 1.)
    if not np.isfinite(normalizer) or not np.all(np.isfinite(g)) or not np.all(np.isfinite(x)):
        raise ValueError('Unrepresentable KKT normalization or iterate')
    stat = float(np.max(abs(g[active])))/normalizer if np.any(active) else 0.
    dual = max(0., float(-np.min(g[~active])))/normalizer if np.any(~active) else 0.
    kkt = max(stat, dual)
    return dict(converged=bool(kkt <= KKT_LIMIT and np.all(x >= 0)), global_kkt=kkt,
                stationarity=stat, dual_violation=dual, tolerance=KKT_LIMIT,
                normalization='columns A/||A_j||2; gradient / max(||b||2,1)',
                residual_norm=float(np.linalg.norm(A@x-b))), g


def nnls(A, b, *, budget=None, max_iterations=2000):
    """Lawson-Hanson active set; no clipped unconstrained least squares."""
    A = np.asarray(A)
    b = vector(b, 'b')
    integer(max_iterations, 'max_iterations', 1, 10000)
    if A.dtype.kind not in 'fiu' or A.ndim != 2 or A.shape[0] != len(b) or not 1 <= A.shape[1] <= 192 or not np.all(np.isfinite(A)):
        raise ValueError('Finite matrix with 1..192 columns required')
    norms = np.linalg.norm(A, axis=0)
    if np.any(norms == 0) or not np.all(np.isfinite(norms)):
        raise ValueError('Zero or unrepresentable NNLS column')
    budget = budget or FitBudget()
    M = A/norms
    x = np.zeros(A.shape[1]); active = np.zeros(len(x), bool)
    threshold = 1e-10*max(np.linalg.norm(b), 1.)
    iterations = 0; reason = None
    try:
        while iterations < max_iterations:
            budget.check()
            w = M.T@(b-M@x)
            eligible = np.where(~active, w, -np.inf)
            if np.max(eligible) <= threshold:
                break
            active[int(np.argmax(eligible))] = True
            while iterations < max_iterations:
                budget.check()
                iterations += 1
                z = np.zeros_like(x)
                z[active] = np.linalg.lstsq(M[:, active], b, rcond=1e-12)[0]
                if np.all(z[active] > 0):
                    x = z
                    break
                bad = active & (z <= 0)
                moving = bad & (x-z > 0)
                alpha = float(np.min(x[moving]/(x[moving]-z[moving]))) if np.any(moving) else 0.
                x += alpha*(z-x)
                leaving = active & (x <= 1e-14*max(1., np.max(x)))
                active[leaving] = False; x[leaving] = 0.
    except TimeoutError as exc:
        reason = str(exc)
    coefficients = x/norms
    cert, _ = kkt_certificate(A, b, coefficients)
    cert.update(iterations=iterations, reason=reason, local_columns=A.shape[1])
    if reason is not None:
        cert['converged'] = False
    return coefficients, cert


def column_nnls(A, b, *, budget=None, max_passes=10, working_limit=192, progress=None):
    A = np.asarray(A)
    b = vector(b, 'b')
    integer(max_passes, 'max_passes', 1, 10)
    integer(working_limit, 'working_limit', 1, 192)
    if A.dtype.kind not in 'fiu' or A.ndim != 2 or A.shape[0] != len(b) or not 1 <= A.shape[1] <= 256 or not np.all(np.isfinite(A)):
        raise ValueError('Full dictionary must contain 1..256 finite columns')
    norms = np.linalg.norm(A, axis=0)
    if np.any(norms <= 0) or not np.all(np.isfinite(norms)):
        raise ValueError('Invalid full dictionary norms')
    budget = budget or FitBudget()
    subset = np.arange(min(working_limit, A.shape[1]))
    x = np.zeros(A.shape[1]); trace = []; reason = None
    cert, _ = kkt_certificate(A, b, x)
    for iteration in range(max_passes):
        try:
            budget.check()
        except TimeoutError as exc:
            reason = str(exc); break
        values, local = nnls(A[:, subset], b, budget=budget)
        x[:] = 0.; x[subset] = values
        cert, gradient = kkt_certificate(A, b, x)
        trace.append(dict(pass_index=iteration+1, working_columns=len(subset),
                          active_columns=int(np.count_nonzero(x)), local=local,
                          global_kkt=cert['global_kkt']))
        if progress is not None:
            progress(x.copy(),dict(cert,trace=trace.copy(),local=local))
        if not local['converged']:
            reason = local['reason'] or 'Local NNLS did not converge'; break
        if cert['converged']:
            break
        active = np.flatnonzero(x > 0)
        eligible = np.flatnonzero((x == 0) & (gradient < -1e-10*max(np.linalg.norm(b), 1.)))
        ordered = eligible[np.argsort(gradient[eligible], kind='stable')]
        if len(active) >= working_limit or not len(ordered):
            reason = 'Working-column budget cannot certify full dictionary'; break
        subset = np.sort(np.r_[active, ordered[:working_limit-len(active)]])
    if not cert['converged'] and reason is None:
        reason = 'Column generation pass budget exhausted'
    cert.update(converged=bool(cert['converged'] and reason is None), reason=reason,
                trace=trace, candidate_count=A.shape[1], max_passes=max_passes)
    return x, cert


def seeds(f, z, *, single_mode=False):
    f, z = spectrum(f, z)
    mag = abs(z)
    indices = np.flatnonzero((mag[1:-1] > mag[:-2]) & (mag[1:-1] >= mag[2:]))+1
    rows = []
    for j in indices:
        y = np.log(mag[j-1:j+2])
        # Fit a quadratic in physical frequency, also on nonuniform source grids.
        c = np.polyfit(f[j-1:j+2]-f[j], y, 2)
        fp = f[j]-c[1]/(2*c[0])
        level = mag[j]/np.sqrt(2); left = right = j
        while left > 0 and mag[left] > level:
            left -= 1
        while right < len(f)-1 and mag[right] > level:
            right += 1
        complete = mag[left] <= level and mag[right] <= level
        if not complete:
            rows.append(dict(frequency_hz=float(fp), width_hz=None, status='unresolved_half_power'))
            continue
        flo = np.interp(level, mag[left:left+2], f[left:left+2])
        fhi = np.interp(level, mag[right-1:right+1][::-1], f[right-1:right+1][::-1])
        rows.append(dict(frequency_hz=float(fp), width_hz=float(fhi-flo), status='resolved',
                         method='quadratic log magnitude and interpolated half-power'))
    if single_mode:
        if len(rows) != 1 or rows[0]['status'] != 'resolved':
            raise ValueError('Single-mode assumption requires one resolved peak')
        w = 2*np.pi*f; inv = 1/z
        c = np.linalg.lstsq(np.column_stack((w, 1/w)), inv.imag, rcond=None)[0]
        if c[0] <= 0 or c[1] >= 0:
            raise ValueError('Single-mode reciprocal structure not supported')
        rows[0].update(frequency_hz=float(np.sqrt(-c[1]/c[0])/(2*np.pi)),
                       width_hz=float(inv.real.mean()/c[0]/(2*np.pi)),
                       method='explicit single-mode reciprocal structural assumption')
    return rows


def metrics(target, actual):
    target, actual = np.asarray(target), np.asarray(actual)
    if target.shape != actual.shape or not target.size or not np.all(np.isfinite(target)) or not np.all(np.isfinite(actual)) or np.any(abs(target) == 0):
        raise ValueError('Metrics require finite matching nonzero targets')
    error = actual-target
    relative = abs(error)/abs(target)
    phase = np.angle(actual*np.conj(target))*180/np.pi
    phase_defined = bool(np.all(abs(actual) > 0))
    return dict(complex_nrmse=float(np.linalg.norm(error)/np.linalg.norm(target)),
                relative_rms=float(np.sqrt(np.mean(relative**2))), relative_p95=float(np.quantile(relative, .95)),
                relative_max=float(np.max(relative)), phase_rms_deg=float(np.sqrt(np.mean(phase**2))) if phase_defined else None,
                phase_max_deg=float(np.max(abs(phase))) if phase_defined else None,
                phase_status='defined' if phase_defined else 'undefined_zero_response')


def quality_gates(gates):
    if not isinstance(gates, dict) or set(gates) != set(DEFAULT_GATES):
        raise ValueError('Declare NRMSE, maximum relative and phase RMS gates before fitting')
    return {k: real(v, k, minimum=0.) for k, v in gates.items()}


def audit(model, frequency_hz, target, *, fit_frequencies, gates):
    gates = quality_gates(gates)
    f, z = spectrum(frequency_hz, target, fs=model.sample_rate_hz)
    fit = vector(fit_frequencies, 'fit frequencies')
    if np.intersect1d(f, fit).size or f[0] < min(fit) or f[-1] > max(fit):
        raise ValueError('Audit grid must be disjoint and within fitted domain')
    before = digest(model.parameters())
    actual = model.discrete_response(f) if model.metadata['coefficient_domain'] == 'discrete_prewarped' else model.continuous_response(f)
    values = metrics(z, actual)
    accepted = all(values[k] is not None and values[k] <= limit for k, limit in gates.items())
    maxima = np.flatnonzero((abs(z)[1:-1] > abs(z)[:-2]) & (abs(z)[1:-1] >= abs(z)[2:]))+1
    peaks = [dict(target_peak_hz=float(f[j]), grid_resolution_hz=float(max(f[j]-f[j-1], f[j+1]-f[j])),
                  complex_relative_error=float(abs(actual[j]-z[j])/abs(z[j])),
                  meaning='local maximum on reserved audit grid; not native pipeline metadata') for j in maxima]
    if digest(model.parameters()) != before:
        raise ValueError("Model parameters changed during audit")
    return dict(status='accepted' if accepted else 'not_accepted', gates=gates, metrics=values,
                domain_hz=[float(f[0]), float(f[-1])], points=len(f), peaks=peaks,
                spectrum_sha256=spectrum_identity(f, z), frozen_parameters_sha256=before)


def candidate_dictionary(f, z, *, R0, fs, domain, single_mode=False, guard_frequency_hz=None, guard_impedance=None):
    rows = [dict(row, source='fit') for row in seeds(f, z-R0, single_mode=single_mode)]
    guard_identity = None
    if (guard_frequency_hz is None) != (guard_impedance is None):
        raise ValueError('Guard frequency and impedance must be supplied together')
    if guard_frequency_hz is not None:
        fg, zg = spectrum(guard_frequency_hz, guard_impedance, fs=fs)
        if fg[0] < f[-1] or (np.intersect1d(fg, f).size and fg[0] != f[-1]):
            raise ValueError('Guard band must start at or above useful band')
        rows += [dict(row, source='guard') for row in seeds(fg, zg-R0)]
        guard_identity = spectrum_identity(fg, zg)
    W = (lambda x: 2*fs*np.tan(np.pi*np.asarray(x)/fs)) if domain == 'discrete_prewarped' else (lambda x: 2*np.pi*np.asarray(x))
    omega, gamma = [], []
    for row in rows:
        if row['status'] != 'resolved':
            continue
        for offset in (-.2, 0., .2):
            center = row['frequency_hz']+offset*row['width_hz']
            if not 0 < center < fs/2:
                raise ValueError('Candidate center outside sampled domain')
            derivative = 1/np.cos(np.pi*center/fs)**2 if domain == 'discrete_prewarped' else 1.
            for damping in (.6, 1., 1.6):
                omega.append(float(W(center)))
                gamma.append(float(2*np.pi*row['width_hz']*derivative*damping))
    if not 1 <= len(omega) <= 256:
        raise ValueError('Resolved candidate count outside [1,256]; no invented guard modes')
    omega, gamma = np.array(omega), np.array(gamma)
    return omega, gamma, rows, guard_identity


def fit_passive(frequency_hz, impedance, *, R0, dc_origin, sample_rate_hz,
                gates, guard_frequency_hz=None, guard_impedance=None,
                domain='discrete_prewarped', single_mode=False, seconds=120., max_passes=10,
                provenance=None, progress=None):
    """Fit only the useful spectrum; the separate guard spectrum supplies pole seeds."""
    fs = integer(sample_rate_hz, 'sample_rate_hz', 1, 192000)
    gates = quality_gates(gates)
    R0 = real(R0, 'R0', minimum=0.)
    if domain not in {'continuous', 'discrete_prewarped'}:
        raise ValueError('Invalid fit domain')
    f, z = spectrum(frequency_hz, impedance, fs=fs)
    budget = FitBudget(seconds)
    omega, gamma, rows, guard_identity = candidate_dictionary(f,z,R0=R0,fs=fs,domain=domain,
        single_mode=single_mode,guard_frequency_hz=guard_frequency_hz,guard_impedance=guard_impedance)
    W = (lambda x: 2*fs*np.tan(np.pi*np.asarray(x)/fs)) if domain == 'discrete_prewarped' else (lambda x: 2*np.pi*np.asarray(x))
    s = 1j*W(f)
    basis = s[:, None]/(s[:, None]**2+gamma*s[:, None]+omega**2)
    weight = 1/np.maximum(abs(z), .01*np.max(abs(z)))
    A = np.vstack(((basis*weight[:, None]).real, (basis*weight[:, None]).imag))
    b = (z-R0)*weight
    a, certificate = column_nnls(A, np.r_[b.real, b.imag], budget=budget, max_passes=max_passes,
        progress=(lambda a,cert: progress(dict(a=a.tolist(),gamma=gamma.tolist(),omega=omega.tolist(),
            certificate=cert,accepted=False,status='partial_fit',fidelity='not_audited'))) if progress is not None else None)
    info = dict(status='converged' if certificate['converged'] else 'not_converged',
                certificate=certificate, gates_declared_before_fit=gates, seeds=rows,
                guard_spectrum_sha256=guard_identity, fit_spectrum_sha256=spectrum_identity(f, z),
                fit_frequency_hz=f.tolist(), domain=domain,
                objective='real/imag stacked; 1/max(abs(Z),0.01*max_fit(abs(Z))); fixed R0',
                candidate_inventory=dict(a=a.tolist(), gamma=gamma.tolist(), omega=omega.tolist()),
                elapsed_seconds=time.monotonic()-budget.started,
                fidelity='not_audited', passivity='structural', mesh='not_checked', physical='not_validated')
    active = a > 0
    model = PassiveResonator(a[active], gamma[active], omega[active], R0=R0,
        sample_rate_hz=fs, dc_origin=dc_origin, domain=domain,
        provenance=dict(provenance or {}, fit_spectrum_sha256=info['fit_spectrum_sha256'],
                        guard_spectrum_sha256=guard_identity), quality=info)
    return model, info
