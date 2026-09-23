"""Opt-in cylindrical Zwikker--Kosten air losses; exp(+j omega t).

No material loss coefficients are used. This is a rigid, smooth, sealed-wall
reference, not a calibrated model of an actual instrument wall.
"""
from __future__ import annotations

from dataclasses import dataclass
from numbers import Real

import numpy as np

from .air import AirProperties
from .losses import LossComponent, LossResult


CF_RTOL = 2e-13
MIN_STOKES = 1e-5
MAX_STOKES = 1e4
MAX_DEPTH = 8192
MATERIAL_WARNING = (
    "Only air thermoviscous losses at a rigid smooth sealed wall are represented; "
    "material effects are omitted, not measured zero. Application to real walls "
    "is inferred/to_calibrate, not experimental validation or material promotion."
)


def _real(value: object, name: str, *, positive: bool = True) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name}: expected a finite real scalar, not bool or complex")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name}: outside finite floating-point range") from exc
    if not np.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name}: expected a finite {'positive ' if positive else ''}real")
    return result


def positive_vector(value: object, name: str) -> np.ndarray:
    """Validate before float conversion, including booleans hidden in lists."""
    if isinstance(value, np.ndarray) and value.dtype.kind in 'fiu':
        if value.ndim > 1 or value.size == 0 or not np.all(np.isfinite(value)) or np.any(value <= 0):
            raise ValueError(f"{name}: expected a positive finite scalar or nonempty 1D vector")
        return np.asarray(value, dtype=float)
    raw = np.asarray(value, dtype=object)
    if raw.ndim > 1 or raw.size == 0:
        raise ValueError(f"{name}: expected a scalar or nonempty 1D vector")
    values = [_real(item, f"{name}[{i}]") for i, item in enumerate(raw.flat)]
    return np.asarray(values, dtype=float).reshape(raw.shape)


@dataclass(frozen=True, slots=True)
class ThermoviscousAir:
    rho: float
    c: float
    mu: float
    kappa: float
    cp: float
    gamma: float
    temperature_c: float
    humidity_percent: float
    identifier: str
    provenance: str

    def __post_init__(self) -> None:
        for field in ("rho", "c", "mu", "kappa", "cp", "gamma"):
            object.__setattr__(self, field, _real(getattr(self, field), field))
        for field in ("temperature_c", "humidity_percent"):
            object.__setattr__(self, field, _real(getattr(self, field), field, positive=False))
        if self.gamma <= 1 or self.temperature_c <= -273.15 or not 0 <= self.humidity_percent <= 100:
            raise ValueError("air requires gamma > 1, T > 0 K and 0 <= RH <= 100 percent")
        for field in ("identifier", "provenance"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                raise ValueError(f"{field}: a nonempty explicit string is required")

    def as_air_properties(self) -> AirProperties:
        return AirProperties(self.rho, self.c, self.temperature_c, self.humidity_percent)


def _ck_reference(temperature_c: float) -> ThermoviscousAir:
    # Independently implemented published equations, not copied Openwind code.
    temperature_k = 273.15 + temperature_c
    return ThermoviscousAir(
        rho=1.2929 * 273.15 / temperature_k,
        c=331.45 * np.sqrt(temperature_k / 273.15),
        mu=1.708e-5 * (1 + 0.0029 * temperature_c),
        kappa=0.00577 * (1 + 0.0033 * temperature_c) * 4.184,
        cp=1004.16, gamma=1.402, temperature_c=temperature_c, humidity_percent=0.,
        identifier=f"CK_DRY_{temperature_c:g}C",
        provenance="sourced nominal equations: Openwind Physics.Chaigne_Kergomard_expressions documentation; humidity/CO2 ignored; documentation SHA unspecified",
    )


CK_DRY_20C = _ck_reference(20.)
CK_DRY_25C = _ck_reference(25.)


def bessel_factors(stokes: object, *, max_depth: int = MAX_DEPTH) -> tuple[np.ndarray, np.ndarray]:
    """Return F and G=1-F on q=exp(-j*pi/4)*St without cancellation.

    Independent backward evaluation of DLMF 10.10.1, depths 16,32,...8192.
    Both successive relative errors must be <=2e-13; no legacy fallback.
    Only the tested interval 1e-5 <= St <= 1e4 is supported.
    """
    st = positive_vector(stokes, "stokes")
    if np.any(st < MIN_STOKES) or np.any(st > MAX_STOKES):
        raise ValueError("stokes: supported numerical interval is [1e-5, 1e4]")
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 32 or max_depth > MAX_DEPTH or max_depth & (max_depth - 1):
        raise ValueError("max_depth: expected a power of two from 32 through 8192")
    # q squared is exactly -j St squared; avoids unnecessary square-root error.
    q2 = -1j * st**2
    previous = None
    depth = 16
    while depth <= max_depth:
        denominator = np.full(st.shape, 2 * depth, dtype=complex)
        for n in range(depth - 1, 1, -1):
            denominator = 2 * n - q2 / denominator
        t = -q2 / denominator
        f, g = 2 / (2 + t), t / (2 + t)
        if not (np.all(np.isfinite(f)) and np.all(np.isfinite(g))):
            raise ArithmeticError("Bessel continued fraction produced nonfinite coefficients")
        if previous is not None and all(
            np.all(np.abs(new - old) <= CF_RTOL * np.abs(new))
            for new, old in zip((f, g), previous)
        ):
            return f, g
        previous = f, g
        depth *= 2
    raise ArithmeticError(f"Bessel continued fraction did not converge by depth {max_depth}")


@dataclass(frozen=True)
class ZKCoefficients:
    k: np.ndarray
    zc: np.ndarray
    alpha: np.ndarray
    zprime: np.ndarray
    yprime: np.ndarray


def zk_coefficients(omega: object, diameter_m: float, state: ThermoviscousAir) -> ZKCoefficients:
    """Angular frequency in rad/s, diameter in m; scalar shape is preserved."""
    if not isinstance(state, ThermoviscousAir):
        raise ValueError("state: explicit ThermoviscousAir required")
    w = positive_vector(omega, "omega")
    diameter = _real(diameter_m, "diameter_m")
    a = diameter / 2
    with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
        try:
            area = np.pi * a**2
            _, gv = bessel_factors(a * np.sqrt(w * state.rho / state.mu))
            ft, _ = bessel_factors(a * np.sqrt(w * state.rho * state.cp / state.kappa))
            zv = 1 / gv
            yt = 1 + (state.gamma - 1) * ft
            root = np.sqrt(zv * yt)  # shared branch, no conjugation/sign repair
            k = w / state.c * root
            zc = state.rho * state.c / area * zv / root
            zprime = 1j * w * state.rho * zv / area
            yprime = 1j * w * area * yt / (state.rho * state.c**2)
        except (FloatingPointError, OverflowError, ZeroDivisionError) as exc:
            raise ArithmeticError("ZK coefficients outside finite numerical range") from exc
    if not all(np.all(np.isfinite(x)) for x in (k, zc, zprime, yprime)):
        raise ArithmeticError("nonfinite ZK coefficients")
    if np.any(k.real <= 0) or np.any(k.imag >= 0) or np.any(zc.real <= 0):
        raise ArithmeticError("ZK branch failed the passive propagation convention")
    return ZKCoefficients(k, zc, -k.imag, zprime, yprime)


@dataclass(frozen=True)
class ZwikkerKostenLossModel:
    state: ThermoviscousAir
    name = "zwikker_kosten_circular"

    def evaluate(self, omega, diameter_m, material, zc_nominal, air=None) -> LossResult:
        if not isinstance(self.state, ThermoviscousAir) or not isinstance(air, AirProperties):
            raise ValueError("ZK requires explicit ThermoviscousAir and matching AirProperties")
        # 1e-12 admits binary64 arithmetic/decimal round trips, not different air.
        for field in ("rho", "c", "temperature_c", "humidity_percent"):
            observed = _real(getattr(air, field), f"air.{field}", positive=False)
            if not np.isclose(observed, getattr(self.state, field), rtol=1e-12, atol=1e-12):
                raise ValueError(f"air.{field}: inconsistent with explicit ZK state")
        diameter = _real(diameter_m, "diameter_m")
        nominal = positive_vector(zc_nominal, "zc_nominal")
        w = positive_vector(omega, "omega")
        if nominal.shape not in ((), w.shape):
            raise ValueError("zc_nominal: expected scalar or same shape as omega")
        expected = self.state.rho * self.state.c / (np.pi * (diameter / 2)**2)
        if not np.allclose(nominal, expected, rtol=1e-12, atol=0):
            raise ValueError("zc_nominal: inconsistent with rho*c/S of explicit ZK state")
        result = zk_coefficients(w, diameter, self.state)
        return LossResult(
            alpha_total=result.alpha, k_complex=result.k, zc_complex=result.zc,
            components=(LossComponent(
                "air_thermoviscous", result.alpha, "inferred",
                notes=("Formula: Ernoult & Kergomard 2020 section 3.1; " + self.state.provenance,),
                warnings=(MATERIAL_WARNING,),
            ),), provenance_status="inferred", warnings=(MATERIAL_WARNING,),
        )
