from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np


def analyze(simulation_result: Mapping[str, Any], config: Mapping[str, Any] | None) -> dict[str, Any]:
    nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
    fs_hz = int(simulation_result.get("sample_rate_hz") or nonlinear_cfg.get("sample_rate_hz", 44100))
    pressure = np.asarray(simulation_result.get("pressure_signal", []), dtype=float)
    flow = np.asarray(simulation_result.get("flow_signal", []), dtype=float)
    reference_freq_hz = float(simulation_result.get("reference_freq_hz") or 0.0) or None

    stability = detect_stability(pressure, fs_hz)
    subharmonic = detect_subharmonics(pressure, fs_hz, reference_freq_hz=reference_freq_hz)
    extinction = detect_extinction(pressure)
    regime_switch = detect_regime_switch(pressure, fs_hz)
    rms_pressure = float(np.sqrt(np.mean(pressure**2))) if pressure.size else 0.0
    rms_flow = float(np.sqrt(np.mean(flow**2))) if flow.size else 0.0
    dominant_freq_hz = _dominant_frequency(pressure, fs_hz)

    return {
        "stability_score": float(stability["stability_score"]),
        "is_stable": bool(stability["is_stable"]),
        "amplitude_cv": float(stability["amplitude_cv"]),
        "dominant_freq_hz": float(dominant_freq_hz or 0.0),
        "has_subharmonics": bool(subharmonic["has_subharmonics"]),
        "subharmonic_ratio": subharmonic["subharmonic_ratio"],
        "extinction_detected": bool(extinction["extinction_detected"]),
        "tail_rms": float(extinction["tail_rms"]),
        "regime_switch_detected": bool(regime_switch["regime_switch_detected"]),
        "regime_switch_frequency_delta_hz": float(regime_switch["frequency_delta_hz"]),
        "rms_pressure": rms_pressure,
        "rms_flow": rms_flow,
    }


def detect_stability(signal: Sequence[float] | np.ndarray, fs_hz: int) -> dict[str, float | bool]:
    del fs_hz
    arr = np.asarray(signal, dtype=float)
    if arr.size < 16:
        return {"is_stable": False, "stability_score": 0.0, "amplitude_cv": 1.0}
    window = max(8, arr.size // 20)
    envelopes = [float(np.sqrt(np.mean(arr[i : i + window] ** 2))) for i in range(0, arr.size - window + 1, window)]
    env = np.asarray(envelopes, dtype=float)
    mean_env = float(np.mean(env)) if env.size else 0.0
    std_env = float(np.std(env)) if env.size else 0.0
    cv = std_env / max(mean_env, 1e-9)
    stability_score = float(max(0.0, min(1.0, 1.0 - cv / 0.5)))
    return {"is_stable": bool(stability_score >= 0.55 and mean_env > 1e-6), "stability_score": stability_score, "amplitude_cv": cv}


def detect_subharmonics(
    signal: Sequence[float] | np.ndarray,
    fs_hz: int,
    reference_freq_hz: float | None = None,
) -> dict[str, float | bool | None]:
    arr = np.asarray(signal, dtype=float)
    dominant = _dominant_frequency(arr, fs_hz)
    if dominant is None or dominant <= 0.0:
        return {"has_subharmonics": False, "subharmonic_ratio": None}
    if reference_freq_hz is None or reference_freq_hz <= 0.0:
        return {"has_subharmonics": False, "subharmonic_ratio": None}
    ratio = dominant / max(reference_freq_hz, 1e-9)
    has_sub = abs(ratio - 0.5) < 0.12 or abs(ratio - 1.5) < 0.12
    return {"has_subharmonics": bool(has_sub), "subharmonic_ratio": float(ratio)}


def detect_extinction(signal: Sequence[float] | np.ndarray) -> dict[str, float | bool]:
    arr = np.asarray(signal, dtype=float)
    if arr.size == 0:
        return {"extinction_detected": True, "tail_rms": 0.0}
    tail = arr[int(0.8 * arr.size) :]
    tail_rms = float(np.sqrt(np.mean(tail**2))) if tail.size else 0.0
    return {"extinction_detected": bool(tail_rms < 1e-4), "tail_rms": tail_rms}


def detect_regime_switch(signal: Sequence[float] | np.ndarray, fs_hz: int) -> dict[str, float | bool]:
    arr = np.asarray(signal, dtype=float)
    if arr.size < 32:
        return {"regime_switch_detected": False, "frequency_delta_hz": 0.0}
    first = arr[: arr.size // 2]
    second = arr[arr.size // 2 :]
    f1 = _dominant_frequency(first, fs_hz) or 0.0
    f2 = _dominant_frequency(second, fs_hz) or 0.0
    delta = abs(f2 - f1)
    return {"regime_switch_detected": bool(delta > 0.15 * max(f1, f2, 1.0)), "frequency_delta_hz": float(delta)}


def _dominant_frequency(signal: np.ndarray, fs_hz: int) -> float | None:
    if signal.size < 16:
        return None
    win = np.hanning(signal.size)
    spectrum = np.fft.rfft(signal * win)
    freqs = np.fft.rfftfreq(signal.size, d=1.0 / fs_hz)
    if freqs.size <= 1:
        return None
    spectrum[0] = 0.0
    idx = int(np.argmax(np.abs(spectrum)))
    if idx <= 0 or idx >= freqs.size:
        return None
    return float(freqs[idx])
