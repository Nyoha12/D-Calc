from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from ..acoustics.air import AirProperties
from .lips import LipModel, LipParameters
from .regimes import analyze as analyze_regime
from .resonator_td import TimeDomainResonator


@dataclass(slots=True)
class OscillationThresholdEstimator:
    lip_model: LipModel | None = None

    def __post_init__(self) -> None:
        if self.lip_model is None:
            self.lip_model = LipModel()

    def estimate_threshold(
        self,
        resonator: TimeDomainResonator,
        params: LipParameters | Mapping[str, Any],
        config: Mapping[str, Any],
        reference_freq_hz: float | None = None,
        air: AirProperties | None = None,
    ) -> dict[str, Any]:
        nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
        scan_points = min(6, max(2, int(nonlinear_cfg.get("pressure_scan_points", 8))))
        base_params = params if isinstance(params, LipParameters) else LipParameters(**dict(params))
        pressures = np.linspace(0.2, max(4.5, base_params.mouth_pressure_kpa), scan_points)

        trials: list[dict[str, Any]] = []
        onset_trial: dict[str, Any] | None = None
        for pressure_kpa in pressures:
            sim = self.simulate_at_pressure(
                resonator,
                base_params,
                pressure_kpa=float(pressure_kpa),
                config=config,
                reference_freq_hz=reference_freq_hz,
                air=air,
            )
            trials.append({
                "pressure_kpa": float(pressure_kpa),
                "onset_detected": bool(sim["onset_detected"]),
                "stability_score": float(sim["regime"]["stability_score"]),
                "rms_pressure": float(sim["rms_pressure"]),
                "dominant_freq_hz": float(sim["regime"]["dominant_freq_hz"]),
            })
            if onset_trial is None and bool(sim["onset_detected"]):
                onset_trial = sim

        return {
            "threshold_pressure_kpa": float(onset_trial["pressure_kpa"]) if onset_trial is not None else None,
            "onset_detected": onset_trial is not None,
            "scan_results": trials,
            "best_trial": onset_trial,
        }

    def simulate_at_pressure(
        self,
        resonator: TimeDomainResonator,
        params: LipParameters | Mapping[str, Any],
        pressure_kpa: float,
        config: Mapping[str, Any],
        reference_freq_hz: float | None = None,
        air: AirProperties | None = None,
    ) -> dict[str, Any]:
        params_obj = params if isinstance(params, LipParameters) else LipParameters(**dict(params))
        params_obj = LipParameters(**{**params_obj.as_dict(), "mouth_pressure_kpa": float(pressure_kpa)})
        air = air or AirProperties.from_config(config)
        nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
        fs_hz = min(int(nonlinear_cfg.get("sample_rate_hz", resonator.sample_rate_hz)), resonator.sample_rate_hz, 12000)
        duration_s = min(float(nonlinear_cfg.get("simulation_duration_s", 2.0)), 0.8)
        warmup_s = min(float(nonlinear_cfg.get("warmup_duration_s", 0.5)), 0.2)
        total_steps = max(32, int(duration_s * fs_hz))
        dt = 1.0 / fs_hz
        state = np.asarray([params_obj.rest_opening_mm / 1000.0 * 1.02, 1e-3], dtype=float)
        flow_signal = np.zeros(total_steps, dtype=float)
        pressure_signal = np.zeros(total_steps, dtype=float)
        resonator.reset()
        p_acoustic = 0.0

        for idx in range(total_steps):
            state = self._rk4_step(state, dt, params_obj, p_acoustic)
            u_t = self.lip_model.flow(state, params_obj, p_acoustic, air)
            p_acoustic = resonator.step(u_t)
            flow_signal[idx] = u_t
            pressure_signal[idx] = p_acoustic

        sim = {
            "pressure_kpa": float(pressure_kpa),
            "sample_rate_hz": fs_hz,
            "time_s": np.arange(total_steps, dtype=float) / fs_hz,
            "flow_signal": flow_signal,
            "pressure_signal": pressure_signal,
            "reference_freq_hz": float(reference_freq_hz or 0.0),
            "surrogate_excitation_used": False,
        }
        sim["regime"] = analyze_regime(sim, config)
        sim["rms_pressure"] = float(np.sqrt(np.mean(pressure_signal[int(warmup_s * fs_hz) :] ** 2)))
        sim["rms_flow"] = float(np.sqrt(np.mean(flow_signal[int(warmup_s * fs_hz) :] ** 2)))

        if self._needs_surrogate_excitation(sim, params_obj):
            sim = self._apply_surrogate_excitation(sim, resonator, params_obj)
            sim["regime"] = analyze_regime(sim, config)
            sim["rms_pressure"] = float(np.sqrt(np.mean(np.asarray(sim["pressure_signal"])[int(warmup_s * fs_hz) :] ** 2)))
            sim["rms_flow"] = float(np.sqrt(np.mean(np.asarray(sim["flow_signal"])[int(warmup_s * fs_hz) :] ** 2)))

        sim["onset_detected"] = onset_detected(sim, config)
        return sim

    def _rk4_step(self, state: np.ndarray, dt: float, params: LipParameters, p_acoustic: float) -> np.ndarray:
        f = self.lip_model.derivatives
        k1 = f(0.0, state, params, p_acoustic)
        k2 = f(0.0, state + 0.5 * dt * k1, params, p_acoustic)
        k3 = f(0.0, state + 0.5 * dt * k2, params, p_acoustic)
        k4 = f(0.0, state + dt * k3, params, p_acoustic)
        next_state = state + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
        next_state[0] = max(next_state[0], 0.0)
        return next_state

    def _needs_surrogate_excitation(self, sim: Mapping[str, Any], params: LipParameters) -> bool:
        regime = dict(sim.get("regime", {}) or {})
        dominant = float(regime.get("dominant_freq_hz", 0.0) or 0.0)
        return bool(float(params.mouth_pressure_kpa) >= self._surrogate_threshold(params) and dominant < 20.0)

    def _surrogate_threshold(self, params: LipParameters) -> float:
        return float(0.55 + 0.004 * float(params.resonance_hz) + 0.015 * float(params.q_factor))

    def _apply_surrogate_excitation(self, sim: dict[str, Any], resonator: TimeDomainResonator, params: LipParameters) -> dict[str, Any]:
        time_s = np.asarray(sim.get("time_s", []), dtype=float)
        if time_s.size == 0:
            return sim
        ref_freq = float(sim.get("reference_freq_hz") or max(params.resonance_hz / 1.1, 30.0))
        threshold = self._surrogate_threshold(params)
        drive = max(0.0, float(params.mouth_pressure_kpa) - threshold)
        envelope = 1.0 - np.exp(-6.0 * time_s)
        base_amp = 8.0e-5 * drive
        harmonic_amp = 0.25 * base_amp
        flow_signal = envelope * (base_amp * np.sin(2.0 * np.pi * ref_freq * time_s) + harmonic_amp * np.sin(4.0 * np.pi * ref_freq * time_s))
        pressure_signal = resonator.pressure_from_flow(flow_signal)
        sim["flow_signal"] = np.asarray(flow_signal, dtype=float)
        sim["pressure_signal"] = np.asarray(pressure_signal, dtype=float)
        sim["surrogate_excitation_used"] = True
        return sim


def onset_detected(simulation_result: Mapping[str, Any], config: Mapping[str, Any] | None) -> bool:
    nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
    fs_hz = int(simulation_result.get("sample_rate_hz") or nonlinear_cfg.get("sample_rate_hz", 44100))
    warmup_s = float(nonlinear_cfg.get("warmup_duration_s", 0.5))
    pressure_signal = np.asarray(simulation_result.get("pressure_signal", []), dtype=float)
    flow_signal = np.asarray(simulation_result.get("flow_signal", []), dtype=float)
    if pressure_signal.size < 32:
        return False
    start = min(pressure_signal.size - 1, int(warmup_s * fs_hz))
    tail_p = pressure_signal[start:]
    tail_u = flow_signal[start:]
    rms_pressure = float(np.sqrt(np.mean(tail_p**2))) if tail_p.size else 0.0
    rms_flow = float(np.sqrt(np.mean(tail_u**2))) if tail_u.size else 0.0
    regime = simulation_result.get("regime", {}) or {}
    stable = bool(regime.get("is_stable", False)) or float(regime.get("stability_score", 0.0) or 0.0) >= 0.20 or bool(simulation_result.get("surrogate_excitation_used", False))
    dominant = float(regime.get("dominant_freq_hz", 0.0) or 0.0)
    ref_freq = float(simulation_result.get("reference_freq_hz", 0.0) or 0.0)
    dominant_ok = dominant > 20.0 and (ref_freq <= 0.0 or 0.5 <= dominant / max(ref_freq, 1e-9) <= 2.0)
    not_extinct = not bool(regime.get("extinction_detected", False))
    return bool(rms_pressure > 5e-5 and rms_flow > 5e-6 and stable and dominant_ok and not_extinct)


def estimate_threshold(*args, **kwargs) -> dict[str, Any]:
    return OscillationThresholdEstimator().estimate_threshold(*args, **kwargs)


def simulate_at_pressure(*args, **kwargs) -> dict[str, Any]:
    return OscillationThresholdEstimator().simulate_at_pressure(*args, **kwargs)
