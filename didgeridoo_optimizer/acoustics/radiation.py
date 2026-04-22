from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from .air import AirProperties


DEFAULT_BANDS = (
    {"name": "low", "f_min_hz": 50.0, "f_max_hz": 300.0},
    {"name": "mid", "f_min_hz": 300.0, "f_max_hz": 1000.0},
    {"name": "high", "f_min_hz": 1000.0, "f_max_hz": 3000.0},
)


def end_correction_m(radius_m: float) -> float:
    return 0.613 * max(float(radius_m), 0.0)


def radiation_impedance(
    omega: float | np.ndarray,
    radius_m: float,
    air: AirProperties,
) -> np.ndarray:
    radius_m = max(float(radius_m), 1e-9)
    omega_arr = np.asarray(omega, dtype=float)
    k = omega_arr / float(air.c)
    area = np.pi * radius_m**2
    zc = float(air.rho) * float(air.c) / area
    zr = zc * (((k * radius_m) ** 2) / 4.0 + 1j * k * end_correction_m(radius_m))
    return np.asarray(zr, dtype=complex)


def radiation_proxy_metrics(
    freq_hz: Sequence[float] | np.ndarray,
    zr: Sequence[complex] | np.ndarray,
    bands: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    freq = np.asarray(freq_hz, dtype=float)
    zr_arr = np.asarray(zr, dtype=complex)
    bands = tuple(bands or DEFAULT_BANDS)

    yr_arr = np.reciprocal(zr_arr, where=np.abs(zr_arr) > 0.0, out=np.zeros_like(zr_arr, dtype=complex))

    stats: dict[str, Any] = {"bands": {}}
    for band in bands:
        name = str(band["name"])
        f_min = float(band["f_min_hz"])
        f_max = float(band["f_max_hz"])
        mask = (freq >= f_min) & (freq <= f_max)
        if np.any(mask):
            real_part = np.real(zr_arr[mask])
            magnitude = np.abs(zr_arr[mask])
            real_admittance = np.real(yr_arr[mask])
            admittance_magnitude = np.abs(yr_arr[mask])
            stats["bands"][name] = {
                "mean_real": float(np.mean(real_part)),
                "max_real": float(np.max(real_part)),
                "mean_magnitude": float(np.mean(magnitude)),
                "max_magnitude": float(np.max(magnitude)),
                "mean_real_admittance": float(np.mean(real_admittance)),
                "max_real_admittance": float(np.max(real_admittance)),
                "mean_admittance_magnitude": float(np.mean(admittance_magnitude)),
                "max_admittance_magnitude": float(np.max(admittance_magnitude)),
            }
        else:
            stats["bands"][name] = {
                "mean_real": 0.0,
                "max_real": 0.0,
                "mean_magnitude": 0.0,
                "max_magnitude": 0.0,
                "mean_real_admittance": 0.0,
                "max_real_admittance": 0.0,
                "mean_admittance_magnitude": 0.0,
                "max_admittance_magnitude": 0.0,
            }

    low = stats["bands"].get("low", {"mean_real_admittance": 0.0})["mean_real_admittance"]
    high = stats["bands"].get("high", {"mean_real_admittance": 0.0})["mean_real_admittance"]
    stats["brightness_proxy"] = float(high / max(low, 1e-18))
    stats["hf_mean_real"] = float(stats["bands"].get("high", {"mean_real": 0.0})["mean_real"])
    stats["hf_mean_real_admittance"] = float(high)
    stats["total_mean_real"] = float(np.mean(np.real(zr_arr))) if zr_arr.size else 0.0
    stats["total_mean_real_admittance"] = float(np.mean(np.real(yr_arr))) if yr_arr.size else 0.0
    return stats
