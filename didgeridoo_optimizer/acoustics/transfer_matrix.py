from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np

from ..geometry.models import Design, Segment
from ..materials.database import MaterialDatabase
from ..materials.models import Material
from .air import AirProperties
from .losses import attenuation_alpha, complex_wavenumber
from .radiation import radiation_impedance


DEFAULT_AIR = AirProperties(rho=1.204, c=343.0, temperature_c=20.0, humidity_percent=50.0)


def area_from_diameter(diameter_m: float) -> float:
    radius_m = max(float(diameter_m), 0.0) / 2.0
    return math.pi * radius_m * radius_m


def characteristic_impedance(rho: float, c: float, area_m2: float) -> float:
    area_m2 = max(float(area_m2), 1e-12)
    return float(rho) * float(c) / area_m2


def lossy_characteristic_impedance(
    zc: float | np.ndarray,
    omega: float | np.ndarray,
    alpha: float | np.ndarray,
    air: AirProperties | None = None,
) -> np.ndarray:
    air = air or DEFAULT_AIR
    zc_arr = np.asarray(zc, dtype=complex)
    omega_arr = np.asarray(omega, dtype=float)
    alpha_arr = np.asarray(alpha, dtype=float)
    k0 = np.maximum(omega_arr / max(float(air.c), 1e-9), 1e-12)
    loss_ratio = alpha_arr / k0
    return zc_arr * (1.0 + 1j * loss_ratio)


def segment_matrix(zc: float | np.ndarray, k: np.ndarray, length_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    phase = k * float(length_m)
    zc_arr = np.asarray(zc, dtype=complex)
    a = np.cos(phase)
    b = 1j * zc_arr * np.sin(phase)
    c = 1j * (1.0 / zc_arr) * np.sin(phase)
    d = np.cos(phase)
    return a, b, c, d


def propagate_impedance_uniform_segment(
    z_load: complex | np.ndarray,
    zc: float | np.ndarray,
    k: np.ndarray,
    length_m: float,
) -> np.ndarray:
    z_load_arr = np.asarray(z_load, dtype=complex)
    zc_arr = np.asarray(zc, dtype=complex)
    tan_term = np.tan(k * float(length_m))
    z_ratio = z_load_arr / zc_arr
    return zc_arr * (1j * tan_term + z_ratio) / (1.0 + 1j * z_ratio * tan_term)


def input_impedance(
    freq_hz: Sequence[float] | np.ndarray,
    design: Design,
    materials: MaterialDatabase | dict[str, Material],
    air: AirProperties | None = None,
) -> np.ndarray:
    air = air or DEFAULT_AIR
    freq = np.asarray(freq_hz, dtype=float)
    omega = 2.0 * np.pi * freq
    if freq.ndim != 1:
        raise ValueError("freq_hz must be a 1D sequence.")
    if not design.segments:
        return np.zeros_like(freq, dtype=complex)

    material_lookup = materials.materials if isinstance(materials, MaterialDatabase) else materials
    exit_radius_m = max(float(design.segments[-1].d_out_cm) / 200.0, 1e-9)
    z_load = radiation_impedance(omega, exit_radius_m, air)

    for segment in reversed(design.segments):
        material = materials.get(segment.material_id) if isinstance(materials, MaterialDatabase) else _resolve_material(segment, material_lookup)
        diameter_m = max(float(segment.average_diameter_cm) / 100.0, 1e-9)
        length_m = max(float(segment.length_cm) / 100.0, 1e-12)
        area_m2 = area_from_diameter(diameter_m)
        zc_nominal = characteristic_impedance(air.rho, air.c, area_m2)
        alpha = attenuation_alpha(omega, diameter_m, material)
        zc = lossy_characteristic_impedance(zc_nominal, omega, alpha, air)
        k = complex_wavenumber(omega, diameter_m, material, air)
        z_load = propagate_impedance_uniform_segment(z_load, zc, k, length_m)

    return np.asarray(z_load, dtype=complex)


def _resolve_material(segment: Segment, materials: dict[str, Material]) -> Material:
    try:
        return materials[segment.material_id]
    except KeyError as exc:
        known = ", ".join(sorted(materials.keys())[:10])
        raise KeyError(f"Unknown material_id {segment.material_id!r} on segment; known examples: {known}") from exc
