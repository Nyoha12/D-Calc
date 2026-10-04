"""Explicit V2 ports; conjugate means lambda=1 for the declared mechanics.

No calibrated area ratio, upstream impedance or signed Bernoulli jet is added.
These stateless adapters do not advance lips, contact or the passive backend.
"""
from __future__ import annotations

import math


def validate_port_model(value, *, schedule='simultaneous'):
    if not isinstance(value, str) or value not in ('jet-only', 'conjugate'):
        raise ValueError('v2_port_model must be jet-only or conjugate')
    if value == 'conjugate' and schedule != 'simultaneous':
        raise ValueError('conjugate requires simultaneous and PassiveResonator')
    return value


def history_pressure(ph, D, Ad, vm, port_model):
    # Preserve the exact historical arithmetic when the option is absent.
    return ph if port_model == 'jet-only' else ph-D*Ad*vm


def flows(jet, vm, Au, Ad, port_model):
    iu = 0. if port_model == 'jet-only' else Au*vm
    idown = 0. if port_model == 'jet-only' else -Ad*vm
    return dict(jet_m3_s=jet, upstream_flow_m3_s=jet+iu,
                downstream_flow_m3_s=jet+idown,
                induced_upstream_m3_s=iu, induced_downstream_m3_s=idown)


def solve_port(vm, x0, ph, *, Pu, D, k, tau, h0, Ad, port_model):
    """Stable elimination of the unchanged one-way jet at fixed midpoint v."""
    opening = h0+x0+tau*vm
    ph = history_pressure(ph, D, Ad, vm, port_model)
    B = Pu-ph
    if B <= 0 or opening <= 0:
        return ph, 0., max(B, 0.), opening
    b = D*k*opening
    if not math.isfinite(b):
        raise ValueError('Unrepresentable Bernoulli coefficient')
    t = B/(math.hypot(b, 2*math.sqrt(B))/2+b/2)
    u = k*opening*t
    return ph+D*u, u, t*t, opening


def uniqueness_feedback(x0, ph, *, Pu, D, k, tau, h0, Ad, port_model):
    """Sufficient monotonicity bound, not an interval/root-exclusion oracle."""
    if port_model == 'jet-only':
        return tau**2*max(Ad, 0.)*D*k*math.sqrt(max(Pu-ph, 0.))
    if Ad <= 0:
        return 0.
    edge = Pu-ph-D*Ad*(h0+x0)/tau
    return tau*Ad*D*max(k*tau*math.sqrt(max(edge, 0.))-Ad, 0.)
