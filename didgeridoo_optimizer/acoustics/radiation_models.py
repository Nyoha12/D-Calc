"""Explicit radiation boundaries, exp(+j omega t), p/U toward the outlet.

Silva's rounded published Padé fit is implemented independently from the
supplied equations, with the 2015 sign correction. No propagation coefficients
or geometric end length enter this boundary normalization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Real
from typing import Protocol

import numpy as np

from .air import AirProperties
from .radiation import radiation_impedance

NAMES = ('legacy', 'silva_unflanged', 'silva_flanged')
_TABLE = {'silva_unflanged': (.167, 1.393, .457), 'silva_flanged': (.182, 1.825, .649)}


def _real_array(value, name):
    try:
        raw = np.asarray(value, dtype=object)
        if raw.ndim > 1 or (raw.ndim == 1 and not raw.size):
            raise ValueError(f'{name}: nonempty scalar/1D real input required')
        if any(isinstance(x, (bool, np.bool_)) or not isinstance(x, Real) for x in raw.flat):
            raise ValueError(f'{name}: real numbers required, not bool/complex/string')
        result = np.asarray(value, dtype=float)
    except (TypeError, OverflowError) as exc:
        raise ValueError(f'{name}: real scalar/1D input outside supported range') from exc
    if not np.all(np.isfinite(result)):
        raise ValueError(f'{name}: finite numbers required')
    return result


def _positive_scalar(value, name):
    result = _real_array(value, name)
    if result.ndim or result <= 0:
        raise ValueError(f'{name}: finite positive real scalar required')
    return float(result)


def normalized_impedance_and_reflection(ka, variant):
    """Return z and R, shape preserved; signed real ka is allowed for symmetry.

    Reciprocal polynomials for |ka|>1 avoid unbounded powers of ka. Tiny nonzero
    coefficients that cannot be represented are refused, never called zero.
    """
    if variant not in _TABLE:
        raise ValueError('Expected silva_unflanged or silva_flanged')
    x = _real_array(ka, 'ka')
    n1, d1, d2 = _TABLE[variant]
    low = abs(x) <= 1
    # Indexing also works for 0D arrays; results keep the input shape.
    re, im, reflection = np.empty_like(x), np.empty_like(x), np.empty(x.shape, complex)
    with np.errstate(over='ignore', under='ignore', invalid='ignore', divide='ignore'):
        t = x[low]; t2 = t*t
        denominator = (2-d2*t2)**2+(d1+n1)**2*t2
        re[low] = ((d1*d1-n1*n1-2*d2)*t2+d2*d2*t2*t2)/denominator
        im[low] = t*(2*(d1-n1)+2*n1*d2*t2)/denominator
        reflection[low] = -(1+1j*n1*t)/(1-d2*t2+1j*d1*t)
        y = 1/x[~low]; y2 = y*y
        denominator = (2*y2-d2)**2+(d1+n1)**2*y2
        re[~low] = ((d1*d1-n1*n1-2*d2)*y2+d2*d2)/denominator
        im[~low] = y*(2*(d1-n1)*y2+2*n1*d2)/denominator
        reflection[~low] = -(y2+1j*n1*y)/(y2-d2+1j*d1*y)
    if (not np.all(np.isfinite(re) & np.isfinite(im) & np.isfinite(reflection))
            or np.any((x != 0) & ((re == 0) | (im == 0)))):
        raise ValueError('Radiation coefficients outside representable numerical range')
    return np.asarray(re+1j*im), reflection


@dataclass(frozen=True)
class RadiationResult:
    impedance: np.ndarray
    metadata: dict


class RadiationModel(Protocol):
    def evaluate(self, omega, radius_m, air: AirProperties) -> RadiationResult: ...


@dataclass(frozen=True)
class LegacyRadiationModel:
    name: str = field(default='legacy', init=False)

    def describe(self):
        return dict(name=self.name, variant='existing_low_frequency', version='dcalc.legacy.v1',
                    coefficients=None, source='D-Calc acoustics/radiation.py unchanged',
                    category='low-frequency approximation; not measurement',
                    reference_band=dict(abs_ka_max=None, description='low-frequency asymptote; no quantified precision threshold'),
                    assumptions=['existing 0.613*a reactive term; no added length',
                                 'historical helper radius floor 1e-9 m retained; ka_out describes the physical radius',
                                 'exterior environment and wall thickness not established'])

    def metadata(self, ka, radius_m):
        return dict(**self.describe(), radius_m=radius_m, ka_out=np.asarray(ka).tolist(),
                    normalization_radius_m=max(float(radius_m), 1e-9),
                    model_status=np.full(np.shape(ka), 'low_frequency_asymptote', dtype=object).tolist(),
                    model_reason=np.full(np.shape(ka), 'No quantified precision band established', dtype=object).tolist(),
                    warnings=[])

    def evaluate(self, omega, radius_m, air):
        # Preserve the historical helper's operations and protections exactly.
        impedance = radiation_impedance(omega, radius_m, air)
        ka = np.asarray(omega)*radius_m/air.c
        return RadiationResult(impedance, self.metadata(ka, radius_m))


@dataclass(frozen=True)
class SilvaRadiationModel:
    name: str = 'silva_unflanged'

    def __post_init__(self):
        if self.name not in _TABLE:
            raise ValueError('Expected silva_unflanged or silva_flanged')

    def describe(self):
        return dict(name=self.name, variant=self.name.removeprefix('silva_'), version='silva.pade12.table1.rounded.2009-corrected2015',
                    coefficients=dict(zip(('n1','d1','d2'), _TABLE[self.name])),
                    source=dict(article_doi='10.1016/j.jsv.2008.11.008', corrigendum_doi='10.1016/j.jsv.2014.10.001',
                                equations='Eq16, corrected/rededuced impedance; Table1 rounded'),
                    category='published numerical fit; not measurement or exact diffraction solution',
                    reference_band=dict(abs_ka_max=2., description='preferred studied band of causal Pade fit'),
                    assumptions=['rigid circular cylindrical termination; resting air; no mean flow or vortex',
                                 'thin sharp unflanged wall' if self.name == 'silva_unflanged' else 'infinite planar flange',
                                 'real rho*c/(pi*a^2) normalization; no added end length',
                                 'finite flange, wall thickness and exterior environment not established'])

    def metadata(self, ka, radius_m):
        beyond = abs(np.asarray(ka)) > 2
        return dict(**self.describe(), radius_m=radius_m, ka_out=np.asarray(ka).tolist(),
                    normalization_radius_m=radius_m,
                    model_status=np.where(beyond, 'extrapolation', 'within_reference_band').tolist(),
                    model_reason=np.where(beyond, '|ka|>2: model extrapolation, not a numerical failure', None).tolist(),
                    warnings=['Radiation extrapolated beyond |ka|=2'] if np.any(beyond) else [])

    def evaluate(self, omega, radius_m, air):
        w = _real_array(omega, 'omega')
        radius = _positive_scalar(radius_m, 'radius_m')
        if not isinstance(air, AirProperties):
            raise ValueError('air: AirProperties required')
        rho, c = _positive_scalar(air.rho, 'air.rho'), _positive_scalar(air.c, 'air.c')
        with np.errstate(over='ignore', under='ignore', invalid='ignore', divide='ignore'):
            ka = w*radius/c
            nominal = np.float64(rho)*c/(np.pi*np.float64(radius)*radius)
        if not np.all(np.isfinite(ka)) or np.any((w != 0) & (ka == 0)) or not np.isfinite(nominal) or nominal <= 0:
            raise ValueError('Radiation normalization outside finite numerical range')
        z, _ = normalized_impedance_and_reflection(ka, self.name)
        with np.errstate(over='ignore', under='ignore', invalid='ignore'):
            impedance = nominal*z
        if (not np.all(np.isfinite(impedance)) or
                np.any((ka != 0) & ((impedance.real == 0) | (impedance.imag == 0)))):
            raise ValueError('Radiation impedance outside representable numerical range')
        return RadiationResult(np.asarray(impedance), self.metadata(ka, radius))


def get_radiation_model(name):
    if name not in NAMES:
        raise ValueError('Unknown radiation model')
    return LegacyRadiationModel() if name == 'legacy' else SilvaRadiationModel(name)
