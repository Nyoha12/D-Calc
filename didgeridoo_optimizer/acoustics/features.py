from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

from ..geometry.models import Design
from .air import AirProperties
from .radiation import DEFAULT_BANDS, radiation_proxy_metrics


def extract(
    freq_hz: Sequence[float] | np.ndarray,
    zin: Sequence[complex] | np.ndarray,
    peaks: list[dict[str, Any]],
    design: Design,
    air: AirProperties,
    zr: Sequence[complex] | np.ndarray | None = None,
    bands: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    freq = np.asarray(freq_hz, dtype=float)
    zin_arr = np.asarray(zin, dtype=complex)
    zin_mag = np.abs(zin_arr)
    first_peak = first_playable_peak(peaks)
    f0_hz = float(first_peak["frequency_hz"]) if first_peak else None
    fundamental_q = first_peak.get("q") if first_peak else None
    fundamental_mag = float(first_peak["magnitude"]) if first_peak else None

    band_stats = band_statistics(freq, zin_mag, bands=bands)
    rad_metrics = radiation_proxy_metrics(freq, zr, bands=bands) if zr is not None else None
    brightness_proxy = (
        float(rad_metrics["hf_mean_real_admittance"])
        if rad_metrics is not None
        else float(band_stats.get("high", {}).get("mean", 0.0))
    )

    ratio, toot_quality = _toot_metrics(peaks)
    model_conf = model_confidence(design, air, float(freq.max()) if freq.size else 0.0)

    return {
        "f0_hz": f0_hz,
        "fundamental_peak_magnitude": fundamental_mag,
        "fundamental_q": float(fundamental_q) if fundamental_q is not None else None,
        "peak_count": len(peaks),
        "peaks": peaks,
        "harmonicity_error": harmonicity_error(peaks, f0_hz),
        "odd_only_score": odd_only_score(peaks, f0_hz),
        "backpressure_proxy": float(fundamental_mag or 0.0),
        "band_stats": band_stats,
        "brightness_proxy": brightness_proxy,
        "toot_ratio": ratio,
        "toot_quality": toot_quality,
        "model_confidence": model_conf,
        "radiation_metrics": rad_metrics,
        "vocal_control_proxy": None,
        "transient_proxy": None,
    }


def first_playable_peak(peaks: list[dict[str, Any]]) -> dict[str, Any] | None:
    return peaks[0] if peaks else None


def harmonicity_error(peaks: list[dict[str, Any]], f0_hz: float | None) -> float | None:
    if not peaks or f0_hz is None or f0_hz <= 0.0:
        return None
    errors = []
    for idx, peak in enumerate(peaks[1:8], start=2):
        ratio = float(peak["frequency_hz"]) / f0_hz
        nearest = max(1, round(ratio))
        errors.append(abs(ratio - nearest) / nearest)
    if not errors:
        return None
    return float(np.mean(errors))


def odd_only_score(peaks: list[dict[str, Any]], f0_hz: float | None) -> float | None:
    if not peaks or f0_hz is None or f0_hz <= 0.0:
        return None
    odd_hits = 0.0
    even_hits = 0.0
    for peak in peaks[1:12]:
        ratio = float(peak["frequency_hz"]) / f0_hz
        nearest = max(1, round(ratio))
        closeness = math.exp(-((ratio - nearest) / 0.2) ** 2)
        if nearest % 2 == 1:
            odd_hits += closeness
        else:
            even_hits += closeness
    total = odd_hits + even_hits
    if total <= 0.0:
        return None
    return float((odd_hits - even_hits) / total)


def local_slope(
    freq_hz: Sequence[float] | np.ndarray,
    values: Sequence[float] | np.ndarray,
    f_min_hz: float,
    f_max_hz: float,
) -> float | None:
    freq = np.asarray(freq_hz, dtype=float)
    vals = np.asarray(values, dtype=float)
    mask = (freq >= f_min_hz) & (freq <= f_max_hz)
    if np.count_nonzero(mask) < 2:
        return None
    x = freq[mask]
    y = vals[mask]
    coeffs = np.polyfit(x, y, 1)
    return float(coeffs[0])


def band_statistics(
    freq_hz: Sequence[float] | np.ndarray,
    values: Sequence[float] | np.ndarray,
    bands: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, float]]:
    freq = np.asarray(freq_hz, dtype=float)
    vals = np.asarray(values, dtype=float)
    out: dict[str, dict[str, float]] = {}
    for band in bands or DEFAULT_BANDS:
        name = str(band["name"])
        f_min = float(band["f_min_hz"])
        f_max = float(band["f_max_hz"])
        mask = (freq >= f_min) & (freq <= f_max)
        if np.any(mask):
            out[name] = {
                "mean": float(np.mean(vals[mask])),
                "max": float(np.max(vals[mask])),
                "slope": float(local_slope(freq, vals, f_min, f_max) or 0.0),
            }
        else:
            out[name] = {"mean": 0.0, "max": 0.0, "slope": 0.0}
    return out


def model_confidence(design: Design, air: AirProperties, f_max_hz: float) -> float:
    if not design.segments or f_max_hz <= 0.0:
        return 1.0
    max_diameter_m = max(segment.d_out_cm for segment in design.segments) / 100.0
    a = max(max_diameter_m / 2.0, 1e-9)
    f10 = 1.84 * float(air.c) / (2.0 * math.pi * a)
    return float(max(0.0, min(1.0, f10 / f_max_hz)))


def _toot_metrics(peaks: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    if len(peaks) < 2:
        return None, None
    f0 = float(peaks[0]["frequency_hz"])
    f1 = float(peaks[1]["frequency_hz"])
    if f0 <= 0.0:
        return None, None
    ratio = f1 / f0
    quality = max(0.0, 1.0 - abs(ratio - 3.0) / 2.0)
    return float(ratio), float(quality)
