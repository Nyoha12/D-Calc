"""Optional acoustic Thevenin closure, complex peak amplitudes, exp(+j omega t).

The derivation is algebraic (inferred), not an empirical player model. A passive
pointwise impedance does not establish causality of an arbitrary spectrum.
No propagation, losses, radiation or ideal-source helpers are evaluated here.
"""
from __future__ import annotations

from numbers import Complex, Integral, Real

import numpy as np

from .forced_response import (EPS, LOG_MAX, LOG_MIN, _curve, _from_complex,
                              _multiply, _real_payload, complex_vector,
                              frequencies, real_positive)


def _state(transfer):
    """Validate the normalized frame without interpreting masked placeholders."""
    if not isinstance(transfer, dict):
        raise ValueError('transfer: mapping required')
    required = {'frequency_hz', 'normalized_pressure', 'normalized_flow',
                'pressure_roundoff_bound', 'flow_roundoff_bound', 'log_scale',
                'load', 'zref', 'segment_count', 'propagation_invalid'}
    if not required <= transfer.keys():
        raise ValueError('transfer missing fields: '+', '.join(sorted(required-transfer.keys())))
    n = len(frequencies(transfer['frequency_hz']))
    ref = real_positive(transfer['zref'], 'transfer.zref')
    count = transfer['segment_count']
    if isinstance(count, (bool, np.bool_)) or not isinstance(count, Integral) or count < 0:
        raise ValueError('transfer.segment_count: nonnegative integer required')
    raw = np.asarray(transfer['propagation_invalid'], dtype=object)
    if raw.shape != (n,) or any(not isinstance(x, (bool, np.bool_)) for x in raw):
        raise ValueError('propagation_invalid: exactly aligned boolean mask required')
    bad = np.asarray(raw, dtype=bool)
    arrays = []
    for name in ('normalized_pressure', 'normalized_flow', 'pressure_roundoff_bound',
                 'flow_roundoff_bound', 'log_scale'):
        raw = np.asarray(transfer[name], dtype=object)
        complex_type = name.startswith('normalized_')
        if raw.shape != (n,) or any(isinstance(x, (bool, np.bool_)) or
                not isinstance(x, Complex if complex_type else Real) for x in raw.flat):
            raise ValueError('transfer.'+name+': exactly aligned numeric vector required')
        arrays.append(np.asarray(raw, dtype=complex if complex_type else float))
    q, v, ep, eu, length = arrays
    load = complex_vector(transfer['load'], n, 'transfer.load')
    if np.asarray(transfer['load']).shape != (n,) or np.any(load.real < 0):
        raise ValueError('transfer.load: exactly aligned passive load required')
    bad |= (~np.isfinite(q) | ~np.isfinite(v) | ~np.isfinite(ep) | ~np.isfinite(eu)
            | ~np.isfinite(length) | (ep < 0) | (eu < 0))
    return n, ref, count, q, v, ep, eu, length, load, bad


def _bounds(logs, zeros):
    values, retained = [], []
    for log, zero in zip(logs, zeros, strict=True):
        values.append(0. if zero else None if not np.isfinite(log) or log > LOG_MAX
                      else float(np.exp(max(log, LOG_MIN))))
        retained.append(None if zero or not np.isfinite(log) else float(log))
    return values, retained


def _product_power(left, right, rl, rr, zero):
    reason = np.where(left.reason != '', left.reason, right.reason).copy()
    cosine = np.cos(left.phase-right.phase)
    with np.errstate(all='ignore'):
        envelope = np.log(.5)+left.log_abs+right.log_abs
        error = rl+rr+rl*rr+32*EPS*(1+abs(envelope))
        logs = envelope+np.log(abs(cosine))
        boundlogs = envelope+np.log(error)
    zero = zero | (((left.log_abs == -np.inf) | (right.log_abs == -np.inf)) & (reason == ''))
    logs[zero] = -np.inf; reason[zero] = ''
    reason[~np.isfinite(logs) & ~zero & (reason == '')] = 'Nonfinite power log'
    limited = ((abs(cosine) <= error) | ~np.isfinite(error)) & ~zero
    data = _real_payload(logs, np.where(zero, 0., np.sign(cosine)), reason, limited=limited)
    for i in np.flatnonzero(limited & (reason == '')):
        data['status'][i] = 'roundoff_limited'
        data['reason'][i] = 'Signed result at cancellation/roundoff scale; not clamped'
    data['roundoff_tolerance_w'], data['roundoff_tolerance_log_w'] = _bounds(boundlogs, zero)
    positive = (reason == '') & ~zero & np.isfinite(logs) & np.isfinite(error) & (cosine > error)
    data['positive_log_resolved'] = positive.tolist()
    return data, logs, reason, zero


def _resistive_power(resistance, flow, relative, zero, closure_resolved):
    reason = flow.reason.copy()
    # An exactly reactive element dissipates zero even when a local numerator
    # is unresolved, provided the closure itself bounds a finite solution.
    zero = (zero | ((resistance == 0) & closure_resolved)
            | ((flow.log_abs == -np.inf) & (reason == '')))
    with np.errstate(all='ignore'):
        logs = np.log(.5)+np.log(resistance)+2*flow.log_abs
        error = 2*relative+relative*relative+32*EPS*(1+abs(logs))
        boundlogs = logs+np.log(error)
    logs[zero] = -np.inf; reason[zero] = ''
    reason[~np.isfinite(logs) & ~zero & (reason == '')] = 'Nonfinite power log'
    data = _real_payload(logs, np.where(zero, 0., 1.), reason,
                         limited=((error >= 1) | ~np.isfinite(error)) & ~zero)
    # Projection overflow must not hide an unresolved numerical bound.
    for i in np.flatnonzero(((error >= 1) | ~np.isfinite(error)) & ~zero & (reason == '')):
        data['status'][i] = 'roundoff_limited'
        data['reason'][i] = 'Power uncertainty reaches its magnitude; use no ratio'
    data['roundoff_tolerance_w'], data['roundoff_tolerance_log_w'] = _bounds(boundlogs, zero)
    return data, logs, reason, zero


def _efficiency(load, loadlogs, loadreason, loadzero, incoming, inlogs):
    result = dict(value=[], status=[], reason=[], log_abs=[])
    for i in range(len(inlogs)):
        if (not incoming['positive_log_resolved'][i] or loadreason[i]
                or load['status'][i] in {'unavailable', 'roundoff_limited'}):
            point = dict(value=None, log_abs=None, status='unavailable',
                         reason='Efficiency requires positive numerically resolved supply/input and resolved load power')
        else:
            with np.errstate(all='ignore'):
                log = loadlogs[i]-inlogs[i]
            data = _real_payload([log], [0. if loadzero[i] else 1.], [''])
            point = {key: data[key][0] for key in result}
        for key in result:
            result[key].append(point[key])
    return result


def _dissipation(pin, load):
    data = dict(value=[], status=[], reason=[], roundoff_tolerance_w=[])
    for i, (a, b) in enumerate(zip(pin['value'], load['value'], strict=True)):
        ba, bb = pin['roundoff_tolerance_w'][i], load['roundoff_tolerance_w'][i]
        bound = None if ba is None or bb is None else ba+bb
        if bound is not None and not np.isfinite(bound):
            bound = None
        difference = None if a is None or b is None else a-b
        status, reason = 'ok', None
        if difference is None or not np.isfinite(difference):
            difference, status = None, 'unavailable'
            reason = 'Representable port powers required for signed difference'
        elif pin['status'][i] == load['status'][i] == 'analytic_zero':
            status = 'analytic_zero'
        elif bound is None or abs(difference) <= bound:
            status, reason = 'roundoff_limited', 'Signed residual within accumulated floating-point scale; not clamped'
        elif difference < -bound:
            status, reason = 'passivity_violation', 'Negative dissipation exceeds numerical scale; signed value retained'
        for key, value in dict(value=difference, status=status, reason=reason, roundoff_tolerance_w=bound).items():
            data[key].append(value)
    return data


def _source_payload(curve, values):
    """Retain the exact supplied Cartesian source, not a log/phase round trip."""
    data = curve.payload()
    data['value'] = [dict(real=float(x.real), imag=float(x.imag)) for x in values]
    data['status'] = ['analytic_zero' if x == 0 else 'subnormal' if
                      max(abs(x.real), abs(x.imag)) < np.finfo(float).tiny else 'ok'
                      for x in values]
    data['reason'] = [None]*len(values)
    return data


def _closed_curve(logs, phases, reasons):
    # A failed closure can generate NaN intermediates. Preserve its specific
    # structural/cancellation diagnosis rather than replacing it with that NaN.
    return _curve(np.where(reasons == '', logs, 0.),
                  np.where(reasons == '', phases, 0.), reasons)


def apply_thevenin_source(transfer, pressure, impedance):
    """Solve Ps=p1+Zs U1 using normalized q,v,L and propagated error bounds.

    Ps and Zs are finite complex scalars or exactly aligned vectors; Re(Zs)>=0.
    Ps=0 selects the forced zero response even at a singular denominator; it does
    not solve or rule out a homogeneous mode. No implicit source normalization.
    """
    n, ref, count, q, v, ep, eu, length, zr, bad = _state(transfer)
    ps = complex_vector(pressure, n, 'source pressure')
    zs = complex_vector(impedance, n, 'source impedance')
    if np.any(zs.real < 0):
        raise ValueError('source impedance: Re(Zs)>=0 required')
    s, z, qc, vc = map(_from_complex, (ps, zs, q, v))
    with np.errstate(all='ignore'):
        lr = np.log(ref)
        aq, av = lr+qc.log_abs, z.log_abs+vc.log_abs
        eq, ev = lr+np.log(ep), z.log_abs+np.log(eu)
        scale = np.maximum.reduce([aq, av, eq, ev])
        # Each product and uncertainty is formed in the SAME logarithmic frame.
        tq = np.exp(aq-scale)*np.exp(1j*qc.phase)
        tv = np.exp(av-scale)*np.exp(1j*(z.phase+vc.phase))
        den = tq+tv
        arithmetic = 64*EPS*(1+abs(lr)+abs(scale)+np.where(np.isfinite(z.log_abs), abs(z.log_abs), 0.))
        error = ((np.exp(eq-scale)+np.exp(ev-scale))*(1+arithmetic)
                 + arithmetic*(abs(tq)+abs(tv))+16*np.nextafter(0., 1.))
        magnitude = abs(den)
        resolved = np.isfinite(error) & np.isfinite(scale) & (magnitude > error) & ~bad
        logden = scale+np.log(magnitude)
        rd = error/(magnitude-error)
        # Division: (1+rn)/(1-ed/|d|)-1, plus log/phase arithmetic.
        rq, rv = np.exp(np.log(ep)-qc.log_abs), np.exp(np.log(eu)-vc.log_abs)
        logmargin = 64*EPS*(1+abs(scale)+abs(logden)+abs(s.log_abs))
        rp = rq+rd+rq*rd+logmargin
        ru = rv+rd+rv*rd+logmargin
        rout = rd+logmargin+32*EPS*(1+abs(length))
    reason = np.full(n, '', dtype=object)
    reason[~resolved] = 'Thevenin denominator cancelled or below accumulated roundoff resolution'
    reason[bad] = 'Normalized propagation outside finite numerical range'
    preason, ureason = reason.copy(), reason.copy()
    # Only the empty zero-load identity establishes q=0 analytically.
    qzero = (count == 0) & (zr == 0) & (q == 0) & ~bad
    preason[(abs(q) <= ep) & ~qzero & (reason == '')] = 'Input pressure component cancelled or below accumulated roundoff resolution'
    ureason[(abs(v) <= eu) & (reason == '')] = 'Input flow component cancelled or below accumulated roundoff resolution'
    with np.errstate(all='ignore'):
        p1 = _closed_curve(s.log_abs+lr+qc.log_abs-logden, s.phase+qc.phase-np.angle(den), preason)
        u1 = _closed_curve(s.log_abs+vc.log_abs-logden, s.phase+vc.phase-np.angle(den), ureason)
        u2 = _closed_curve(s.log_abs-length-logden, s.phase-np.angle(den), reason)
    zero = (ps == 0) & ~bad
    # Explicit zero forcing, not a numerical inference about the denominator.
    def forced_zero(curve):
        return _curve(np.where(zero, -np.inf, curve.log_abs), np.where(zero, 0., curve.phase),
                      np.where(zero, '', curve.reason))
    p1, u1, u2 = map(forced_zero, (p1, u1, u2))
    p2 = _multiply(_from_complex(zr), u2)
    # Even a known boundary zero cannot certify an invalid propagation frame.
    p2 = _curve(p2.log_abs, p2.phase, np.where(bad, reason, p2.reason))
    pin, pinlogs, _, _ = _product_power(p1, u1, rp, ru, zero)
    load, loadlogs, loadreason, loadzero = _resistive_power(zr.real, u2, rout, zero, resolved)
    supply, supplylogs, _, _ = _product_power(s, u1, np.zeros(n), ru, zero)
    internal, _, _, _ = _resistive_power(zs.real, u1, ru, zero, resolved)
    return dict(source=dict(kind='thevenin_pressure', amplitude=_source_payload(s, ps), units='Pa peak',
                            impedance=_source_payload(z, zs), impedance_units='Pa.s/m^3'),
                ports=dict(p1=p1, U1=u1, p2=p2, U2=u2),
                powers=dict(Pin=pin, Pload=load, Pdiss=_dissipation(pin, load),
                            eta=_efficiency(load, loadlogs, loadreason, loadzero, pin, pinlogs)),
                source_powers=dict(Psupply=supply, Pinternal=internal,
                                   eta_source=_efficiency(load, loadlogs, loadreason, loadzero, supply, supplylogs)))
