from __future__ import annotations

from typing import Any

import numpy as np

from ..materials.models import Material
from .air import AirProperties


DEFAULT_AIR = AirProperties(rho=1.204, c=343.0, temperature_c=20.0, humidity_percent=50.0)


def effective_beta(material: Material) -> float:
    return float(material.beta.nominal)


def attenuation_alpha(omega: float | np.ndarray, diameter_m: float, material: Material) -> np.ndarray:
    diameter_m = max(float(diameter_m), 1e-9)
    omega_arr = np.asarray(omega, dtype=float)
    beta = effective_beta(material)
    alpha = 1e-5 * beta * np.sqrt(np.maximum(omega_arr, 0.0)) / diameter_m
    alpha_eff = alpha * (1.0 + float(material.wall_loss.nominal) + float(material.porosity_leak.nominal))
    return np.nan_to_num(alpha_eff, nan=0.0, posinf=0.0, neginf=0.0)


def complex_wavenumber(
    omega: float | np.ndarray,
    diameter_m: float,
    material: Material,
    air: AirProperties | None = None,
) -> np.ndarray:
    air = air or DEFAULT_AIR
    omega_arr = np.asarray(omega, dtype=float)
    alpha_eff = attenuation_alpha(omega_arr, diameter_m, material)
    return omega_arr / float(air.c) - 1j * alpha_eff
