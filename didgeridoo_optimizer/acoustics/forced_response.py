"""Loaded two-port response, exp(+j omega t), complex PEAK p/volume flow.

Normalized backward state propagation uses O(Nf) storage. It does not invoke
input_impedance, scores, or a player model. Logarithms are natural logarithms.
"""
from __future__ import annotations

from dataclasses import dataclass
from numbers import Complex, Real

import numpy as np

from .air import AirProperties
from .losses import LegacyBetaLossModel
from .radiation import radiation_impedance
from .radiation_models import LegacyRadiationModel
from .thermoviscous import positive_vector
from .transfer_matrix import DEFAULT_AIR, area_from_diameter, characteristic_impedance, _resolve_material
from ..geometry.discretization import GeometryDiscretizer
from ..geometry.models import Design
from ..materials.database import MaterialDatabase

MAX_FREQUENCIES = 20000
MAX_SEGMENTS = 10000
MAX_CELLS = 2_000_000
EPS = np.finfo(float).eps
LOG_MIN = float(np.log(np.nextafter(0., 1.)))
LOG_MAX = float(np.log(np.finfo(float).max))


def real_positive(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f'{name}: finite positive real scalar required')
    result = float(value)
    if not np.isfinite(result) or result <= 0:
        raise ValueError(f'{name}: finite positive real scalar required')
    return result


def frequencies(value):
    freq = positive_vector(value, 'frequency_hz')
    if freq.ndim != 1 or len(freq) > MAX_FREQUENCIES or np.any(np.diff(freq) <= 0):
        raise ValueError('frequency_hz: strictly increasing nonempty 1D grid, at most 20000 points')
    with np.errstate(over='ignore'):
        angular = 2*np.pi*freq
    if not np.all(np.isfinite(angular)):
        raise ValueError('frequency_hz: angular frequency outside finite range')
    return freq


def complex_vector(value, count, name):
    raw = np.asarray(value, dtype=object)
    if raw.shape not in ((), (count,)):
        raise ValueError(f'{name}: scalar or exactly {count} aligned values required')
    for item in raw.flat:
        if isinstance(item, (bool, np.bool_)) or not isinstance(item, Complex):
            raise ValueError(f'{name}: finite complex numbers required, not booleans')
    array = np.asarray(value, dtype=complex)
    if not np.all(np.isfinite(array)):
        raise ValueError(f'{name}: finite complex numbers required')
    return np.full(count, array.item(), dtype=complex) if array.ndim == 0 else array.copy()


def check_budget(count, segments):
    if not 1 <= count <= MAX_FREQUENCIES or not 1 <= segments <= MAX_SEGMENTS or count*segments > MAX_CELLS:
        raise ValueError('Budget exceeded: Nf<=20000, segments<=10000, Nf*segments<=2000000')


def prepare_mesh(physical: Design, h_cm, count):
    h = real_positive(h_cm, 'h_cm')
    if not isinstance(physical, Design) or not physical.segments or physical.metadata.get('is_discretized'):
        raise ValueError('Expected a nonempty physical Design, not a discretized design')
    total = 0
    for segment in physical.segments:
        for field in ('length_cm', 'd_in_cm', 'd_out_cm'):
            real_positive(getattr(segment, field), f'segment.{field}')
        ratio = segment.length_cm/h
        if not np.isfinite(ratio) or ratio > MAX_SEGMENTS:
            raise ValueError('Budget exceeded before mesh allocation: segment length/h')
        total += max(1 if segment.is_uniform else 2, int(np.ceil(ratio)))
        check_budget(count, total)
    return GeometryDiscretizer().discretize(physical, h)


@dataclass(frozen=True)
class ComplexCurve:
    """Log-domain complex observable; projection may be unavailable, not zero.

    Internal -inf denotes a proven analytic zero only. An unresolved component
    retains a reason. payload() never emits a nonfinite number.
    """
    log_abs: np.ndarray
    phase: np.ndarray
    reason: np.ndarray

    def payload(self):
        values, statuses, reasons, logs, phases = [], [], [], [], []
        for log, phase, reason in zip(self.log_abs, self.phase, self.reason, strict=True):
            value, status = None, 'unavailable'
            if reason:
                logs.append(None); phases.append(None)
            elif log == -np.inf:
                value, status = 0j, 'analytic_zero'
                logs.append(None); phases.append(None)
            else:
                logs.append(float(log)); phases.append(float(phase))
                if log < LOG_MIN:
                    status, reason = 'underflow', 'Complex magnitude below binary64 subnormal range; use log_abs/phase'
                elif log > LOG_MAX:
                    # A complex magnitude can exceed float.max while its two
                    # Cartesian components are still individually representable.
                    direction = (np.cos(phase), np.sin(phase))
                    component_logs = [log+np.log(abs(x)) if x != 0 else -np.inf for x in direction]
                    if max(component_logs) > LOG_MAX:
                        status, reason = 'overflow', 'Complex component above binary64 range; use log_abs/phase'
                    else:
                        parts = [float(np.sign(x)*np.exp(l)) for x,l in zip(direction,component_logs)]
                        value, status = complex(*parts), 'ok'
                else:
                    value = complex(np.exp(log)*np.exp(1j*phase))
                    status = 'subnormal' if log < np.log(np.finfo(float).tiny) else 'ok'
            values.append(None if value is None else {'real': value.real, 'imag': value.imag})
            statuses.append(status); reasons.append(reason or None)
        return dict(value=values, status=statuses, reason=reasons, log_abs=logs, phase_rad=phases)

    def values(self):
        """Convenience for representable results; refuses unavailable projections."""
        data = self.payload()['value']
        if any(item is None for item in data):
            raise ArithmeticError('Observable has unavailable complex projections; inspect payload/logs')
        return np.array([complex(item['real'], item['imag']) for item in data])


def _curve(log, phase, reason):
    log, phase = np.asarray(log, dtype=float), np.asarray(phase, dtype=float)
    reason = np.asarray(reason, dtype=object).copy()
    invalid = (~np.isfinite(log) & (log != -np.inf)) | ~np.isfinite(phase)
    reason[invalid] = 'Nonfinite log-domain operation'
    # Keep a finite placeholder internally only where the reason masks it.
    return ComplexCurve(np.where(reason != '', 0., log),
                        np.where(reason != '', 0., np.angle(np.exp(1j*phase))), reason)


def _from_complex(values):
    # hypot/log avoids overflow of abs(complex) near the binary64 boundary.
    scale = np.maximum(abs(values.real), abs(values.imag))
    with np.errstate(divide='ignore', invalid='ignore'):
        logs = np.log(scale)+.5*np.log((values.real/scale)**2+(values.imag/scale)**2)
    logs = np.where(scale == 0, -np.inf, logs)
    return _curve(logs, np.angle(values), np.full(len(values), '', dtype=object))


def _multiply(left, right):
    reason = np.where(left.reason != '', left.reason, right.reason).copy()
    # A known zero boundary/source is not an underflowed estimate.
    zero = ((left.log_abs == -np.inf) & (left.reason == '')) | ((right.log_abs == -np.inf) & (right.reason == ''))
    reason[zero] = ''
    return _curve(np.where(zero, -np.inf, left.log_abs+right.log_abs), left.phase+right.phase, reason)


def transfer_from_slices(freq_hz, slices_from_outlet, load, *, zref):
    """Low-level passive finite load, streamed (length_m, k, Zc) from outlet.

    No retained slice matrices. High-level callers preflight the complete mesh
    budget; this iterator also enforces the budget as slices arrive.
    """
    freq = frequencies(freq_hz)
    n = len(freq)
    reference = real_positive(zref, 'zref')
    zr = complex_vector(load, n, 'load')
    if np.any(zr.real < 0):
        raise ValueError('load: passive finite load requires Re(Zr)>=0')
    with np.errstate(over='ignore', under='ignore', invalid='ignore', divide='ignore'):
        p, u = zr/reference, np.ones(n, dtype=complex)
        m = np.maximum(abs(p), abs(u))
        scale = np.log(m)
        p, u = p/m, u/m
    bad = ~np.isfinite(scale) | ~np.isfinite(p) | ~np.isfinite(u)
    ep, eu = 8*EPS*abs(p), 8*EPS*abs(u)
    count = 0
    for count, (length, wave, impedance) in enumerate(slices_from_outlet, 1):
        check_budget(n, count)
        length = real_positive(length, 'length_m')
        k = complex_vector(wave, n, 'k')
        zc = complex_vector(impedance, n, 'Zc')
        if np.any(k.imag > 0) or np.any(zc.real <= 0):
            raise ValueError('Unsupported passive convention: Im(k)<=0 and Re(Zc)>0 required')
        with np.errstate(over='ignore', under='ignore', invalid='ignore', divide='ignore'):
            theta = k*length
            b, phase = -theta.imag, theta.real
            eplus, eminus = np.exp(1j*phase), np.exp(-2*b-1j*phase)
            co, si = (eplus+eminus)/2, (eplus-eminus)/2
            r, ri = zc/reference, reference/zc
            p1, p2, u1, u2 = co*p, r*si*u, ri*si*p, co*u
            pn, un = p1+p2, u1+u2
            m = np.maximum(abs(pn), abs(un))
            # Conservative accumulated arithmetic scale, not a physical loss.
            epn = abs(co)*ep+abs(r*si)*eu+8*EPS*(abs(p1)+abs(p2))
            eun = abs(ri*si)*ep+abs(co)*eu+8*EPS*(abs(u1)+abs(u2))
            # co/si themselves contain sums/differences of exponentials. Their
            # absolute arithmetic/argument scale matters near cos/sin zeros.
            coefficient_error = 8*EPS*(1+abs(phase))*(abs(eplus)+abs(eminus))/2
            epn += coefficient_error*(abs(p)+abs(r)*abs(u))
            eun += coefficient_error*(abs(ri)*abs(p)+abs(u))
            p, u = pn/m, un/m
            ep, eu = epn/m+4*EPS*abs(p), eun/m+4*EPS*abs(u)
            scale = scale+b+np.log(m)
        bad |= (m == 0) | ~np.isfinite(scale) | ~np.isfinite(p) | ~np.isfinite(u) | ~np.isfinite(ep) | ~np.isfinite(eu)
    p_reason = np.full(n, '', dtype=object)
    u_reason = p_reason.copy()
    p_reason[abs(p) <= ep] = 'Input pressure component cancelled or below accumulated roundoff resolution'
    u_reason[abs(u) <= eu] = 'Input flow component cancelled or below accumulated roundoff resolution'
    p_reason[bad] = u_reason[bad] = 'Normalized propagation outside finite numerical range'
    # Empty identity chain is useful for boundary tests; p=0 is known here.
    if count == 0:
        p_reason[zr == 0] = ''
    with np.errstate(divide='ignore', invalid='ignore'):
        lp, lu = np.log(abs(p)), np.log(abs(u))
        combined = np.where(p_reason != '', p_reason, u_reason)
        zin = _curve(np.log(reference)+lp-lu, np.angle(p)-np.angle(u), combined)
        yin_reason = combined.copy()
        yin_reason[(p == 0) & (p_reason == '')] = 'Zero input pressure: input admittance singular'
        yin = _curve(lu-np.log(reference)-lp, np.angle(u)-np.angle(p), yin_reason)
        hu = _curve(-scale-lu, -np.angle(u), u_reason)
        yt_reason = p_reason.copy()
        yt_reason[(p == 0) & (p_reason == '')] = 'Zero input pressure: pressure-driven transmission singular'
        yt = _curve(-scale-np.log(reference)-lp, -np.angle(p), yt_reason)
    load_curve = _from_complex(zr)
    return dict(frequency_hz=freq, load=zr, zref=reference, segment_count=count,
                log_scale=scale, normalized_pressure=p, normalized_flow=u,
                pressure_roundoff_bound=ep, flow_roundoff_bound=eu,
                transfers=dict(Zin=zin, Yin=yin, Hu=hu, Yt=yt,
                               Zt=_multiply(load_curve, hu), Hp=_multiply(load_curve, yt)))


def loaded_transfer(freq_hz, mesh, materials, air=None, *, exit_radius_m, loss_model=None, zref=None, radiation_model=None):
    """Response of a uniform analysis mesh with a separately supplied physical outlet.

    Use prepare_mesh on a physical design first. No geometric/radiation epsilon
    is added here; existing coefficient helpers retain their own protections.
    """
    freq = frequencies(freq_hz)
    air = DEFAULT_AIR if air is None else air
    if not isinstance(air, AirProperties):
        raise ValueError('air: AirProperties required')
    real_positive(air.rho, 'air.rho'); real_positive(air.c, 'air.c')
    radius = real_positive(exit_radius_m, 'exit_radius_m')
    if not isinstance(mesh, Design) or not mesh.segments:
        raise ValueError('A nonempty uniform analysis Design is required')
    check_budget(len(freq), len(mesh.segments))
    for segment in mesh.segments:
        if segment.kind not in {'cylinder', 'mouthpiece'} or not segment.is_uniform:
            raise ValueError('Discretize the physical design before loaded_transfer')
        for field in ('length_cm', 'd_in_cm', 'd_out_cm'):
            real_positive(getattr(segment, field), f'segment.{field}')
    model = LegacyBetaLossModel() if loss_model is None else loss_model
    omega = 2*np.pi*freq
    if radiation_model is None:
        zr = radiation_impedance(omega, radius, air)
        radiation = LegacyRadiationModel().metadata(omega*radius/air.c, radius)
    else:
        boundary = radiation_model.evaluate(omega, radius, air)
        zr, radiation = boundary.impedance, boundary.metadata
    if zref is None:
        zref = characteristic_impedance(air.rho, air.c, area_from_diameter(mesh.segments[0].d_in_cm/100))
    warnings = set(radiation['warnings'])
    def slices():
        for segment in reversed(mesh.segments):
            material = materials.get(segment.material_id) if isinstance(materials, MaterialDatabase) else _resolve_material(segment, materials)
            diameter = max(float(segment.average_diameter_cm)/100, 1e-9)
            length = max(float(segment.length_cm)/100, 1e-12)
            nominal = characteristic_impedance(air.rho, air.c, area_from_diameter(diameter))
            loss = model.evaluate(omega, diameter, material, nominal, air)
            warnings.update(loss.warnings)
            yield length, loss.k_complex, loss.zc_complex
    result = transfer_from_slices(freq, slices(), zr, zref=zref)
    result.update(exit_radius_m=radius, ka_out=omega*radius/air.c, model=model.name,
                  model_version='repository source revision', warnings=sorted(warnings),
                  radiation=radiation,
                  load_type=('D-Calc existing low-frequency radiation; no extra length' if radiation['name'] == 'legacy'
                             else radiation['name']+' published Pade(1,2) fit; no extra length'))
    return result


def validate_source(kind, amplitude, count):
    if kind not in {'volume_flow', 'pressure'}:
        raise ValueError('source kind must be volume_flow or pressure')
    return complex_vector(amplitude, count, 'source amplitude')


def _real_payload(logs, signs, reasons, *, limited=None):
    """Project signed logs; sign=0 is an explicitly established zero marker.

    A nonfinite logarithm alone never establishes an analytic zero.
    """
    result = dict(value=[], log_abs=[], sign=[], status=[], reason=[])
    for i, (log, sign, reason) in enumerate(zip(logs, signs, reasons, strict=True)):
        value, status = None, 'unavailable'
        available_log = float(log) if np.isfinite(log) and not reason else None
        if not reason:
            if sign == 0:
                value, status = 0., 'analytic_zero'
            elif not np.isfinite(log):
                reason = 'Nonfinite power log'
            elif log < LOG_MIN:
                status, reason = 'underflow', 'Below binary64 range; log_abs retained'
            elif log > LOG_MAX:
                status, reason = 'overflow', 'Above binary64 range; log_abs retained'
            else:
                value, status = float(sign*np.exp(log)), 'ok'
            if limited is not None and limited[i] and value is not None:
                status, reason = 'roundoff_limited', 'Signed result at cancellation/roundoff scale; not clamped'
        result['value'].append(value); result['log_abs'].append(available_log)
        result['sign'].append(None if value is None and available_log is None else float(sign))
        result['status'].append(status); result['reason'].append(reason or None)
    return result


def apply_source(transfer, kind, amplitude):
    """Apply a peak source without any new propagation or loss evaluation."""
    n = len(transfer['frequency_hz'])
    source = validate_source(kind, amplitude, n)
    s = _from_complex(source)
    tr = transfer['transfers']
    if kind == 'volume_flow':
        p1, u1, u2 = _multiply(tr['Zin'], s), s, _multiply(tr['Hu'], s)
    else:
        p1, u1, u2 = s, _multiply(tr['Yin'], s), _multiply(tr['Yt'], s)
    p2 = _multiply(_from_complex(transfer['load']), u2)
    reasons = np.where(p1.reason != '', p1.reason, u1.reason)
    angle = p1.phase-u1.phase
    cosine = np.cos(angle)
    zero_source = source == 0
    # Detect arithmetic overflow independently of an underflowed projection.
    # Only the explicit masks below may establish a zero power.
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        pinlog = np.log(.5)+p1.log_abs+u1.log_abs+np.log(abs(cosine))
        loadreal = transfer['load'].real
        plog = np.log(.5)+np.log(loadreal)+2*u2.log_abs
    pinzero = zero_source | (((p1.log_abs == -np.inf) | (u1.log_abs == -np.inf)) & (reasons == ''))
    pinlog[pinzero] = -np.inf
    reasons[zero_source] = ''
    load_reasons = u2.reason.copy()
    loadzero = (loadreal == 0) | zero_source
    plog[loadzero] = -np.inf; load_reasons[loadzero] = ''
    reasons[~np.isfinite(pinlog) & ~pinzero & (reasons == '')] = 'Input power logarithm outside finite numerical range'
    load_reasons[~np.isfinite(plog) & ~loadzero & (load_reasons == '')] = 'Load power logarithm outside finite numerical range'
    tol = 64*EPS*(transfer['segment_count']+1)
    # Re(p*conj(U)) may be unresolved long before either component vanishes.
    # Bound its absolute error on the |p||U| scale, using the propagated bounds.
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        rp = transfer['pressure_roundoff_bound']/abs(transfer['normalized_pressure'])
        ru = transfer['flow_roundoff_bound']/abs(transfer['normalized_flow'])
        product_error = rp+ru+rp*ru+tol
        scale_error = tol+16*EPS*(1+abs(transfer['log_scale']))
        load_error = 2*(ru if kind == 'volume_flow' else rp)+scale_error
        pin_error_log = np.log(.5)+p1.log_abs+u1.log_abs+np.log(product_error)
        load_error_log = plog+np.log(load_error)
    def error_bound(log, zero):
        if zero:
            return 0.
        if not np.isfinite(log) or log > LOG_MAX:
            return None
        # An upper resolution bound, never a substituted physical value.
        return float(np.exp(max(log, LOG_MIN)))
    pin_bounds = [error_bound(log, zero) for log,zero in zip(pin_error_log,pinzero)]
    load_bounds = [error_bound(log, zero) for log,zero in zip(load_error_log,loadzero)]
    pin = _real_payload(pinlog, np.where(pinzero, 0, np.sign(cosine)), reasons,
                        limited=((abs(cosine) <= product_error) | ~np.isfinite(product_error)) & ~pinzero)
    pin['roundoff_tolerance_w'] = pin_bounds
    positive_pin_resolved = (np.isfinite(pinlog) & (reasons == '') & (cosine > product_error)
                             & np.isfinite(product_error) & ~pinzero)
    pin['positive_log_resolved'] = positive_pin_resolved.tolist()
    pload = _real_payload(plog, np.where(loadzero, 0., 1.), load_reasons)
    pload['roundoff_tolerance_w'] = load_bounds
    diss = dict(value=[], status=[], reason=[], roundoff_tolerance_w=[])
    eta = dict(value=[], status=[], reason=[], log_abs=[])
    for i, (a, b) in enumerate(zip(pin['value'], pload['value'], strict=True)):
        threshold = None if pin_bounds[i] is None or load_bounds[i] is None else pin_bounds[i]+load_bounds[i]
        if threshold is not None and not np.isfinite(threshold):
            threshold = None
        difference = None if a is None or b is None else a-b
        status, reason = 'ok', None
        if difference is None or not np.isfinite(difference):
            difference, status, reason = None, 'unavailable', 'Representable port powers required for signed difference'
            missing = [f'{name}: {power["reason"][i]}' for name, power in (('Pin', pin), ('Pload', pload))
                       if power['value'][i] is None]
            if missing:
                reason += '; '+ '; '.join(missing)
        elif pin['status'][i] == pload['status'][i] == 'analytic_zero':
            status = 'analytic_zero'
        elif threshold is None or (abs(difference) <= threshold and not zero_source[i]):
            status, reason = 'roundoff_limited', 'Signed residual within accumulated floating-point scale; not clamped'
        elif difference < -threshold:
            status, reason = 'passivity_violation', 'Negative dissipation exceeds numerical scale; signed value retained'
        diss['value'].append(difference); diss['status'].append(status)
        diss['reason'].append(reason); diss['roundoff_tolerance_w'].append(threshold)
        if not positive_pin_resolved[i] or load_reasons[i]:
            eta['value'].append(None); eta['log_abs'].append(None); eta['status'].append('unavailable')
            eta['reason'].append('Efficiency unavailable: '+load_reasons[i] if load_reasons[i] else
                                 'Efficiency requires a nonzero source and positive numerically resolved input power')
        else:
            # Even two finite power logs can have a nonfinite difference.
            # _real_payload checks this before projecting the ratio.
            with np.errstate(over='ignore', invalid='ignore'):
                eta_log = plog[i]-pinlog[i]
            e = _real_payload(np.array([eta_log]), np.array([0. if loadzero[i] else 1.]), np.array([''], dtype=object))
            for key in eta:
                eta[key].append(e[key][0])
    return dict(source=dict(kind=kind, amplitude=s.payload(), units='m^3/s peak' if kind == 'volume_flow' else 'Pa peak'),
                ports=dict(p1=p1, U1=u1, p2=p2, U2=u2),
                powers=dict(Pin=pin, Pload=pload, Pdiss=diss, eta=eta))
