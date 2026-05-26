from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from ..acoustics.air import AirProperties
from .lips import DimensionedLipParameters, LipModel, LipModelV2, LipParameters, dimensioned_opening
from .regimes import analyze as analyze_regime
from .resonator_td import TimeDomainResonator

LIP_MODEL_LEGACY = "legacy"
LIP_MODEL_DIMENSIONED_V2 = "dimensioned_v2"
LipParameterSet = LipParameters | DimensionedLipParameters


@dataclass(slots=True)
class OscillationThresholdEstimator:
    lip_model: LipModel | LipModelV2 | None = None

    def __post_init__(self) -> None:
        if self.lip_model is None:
            self.lip_model = LipModel()

    def estimate_threshold(
        self,
        resonator: TimeDomainResonator,
        params: LipParameterSet | Mapping[str, Any],
        config: Mapping[str, Any],
        reference_freq_hz: float | None = None,
        air: AirProperties | None = None,
    ) -> dict[str, Any]:
        nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
        lip_model_type = self._lip_model_type(config)
        scan_points = min(6, max(2, int(nonlinear_cfg.get("pressure_scan_points", 8))))
        base_params = self._coerce_params(params, lip_model_type)
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
        params: LipParameterSet | Mapping[str, Any],
        pressure_kpa: float,
        config: Mapping[str, Any],
        reference_freq_hz: float | None = None,
        air: AirProperties | None = None,
    ) -> dict[str, Any]:
        lip_model_type = self._lip_model_type(config)
        params_obj = self._coerce_params(params, lip_model_type)
        params_obj = self._replace_mouth_pressure(params_obj, float(pressure_kpa))
        lip_model = self._model_for_params(params_obj, lip_model_type)
        air = air or AirProperties.from_config(config)
        nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
        fs_hz = min(int(nonlinear_cfg.get("sample_rate_hz", resonator.sample_rate_hz)), resonator.sample_rate_hz, 12000)
        duration_s = min(float(nonlinear_cfg.get("simulation_duration_s", 2.0)), 0.8)
        warmup_s = min(float(nonlinear_cfg.get("warmup_duration_s", 0.5)), 0.2)
        total_steps = max(32, int(duration_s * fs_hz))
        dt = 1.0 / fs_hz
        state = self._initial_state(params_obj, lip_model_type)
        flow_signal = np.zeros(total_steps, dtype=float)
        pressure_signal = np.zeros(total_steps, dtype=float)
        opening_signal = np.zeros(total_steps, dtype=float)
        contact_signal = np.zeros(total_steps, dtype=bool)
        resonator.reset()
        p_acoustic = 0.0

        for idx in range(total_steps):
            state = self._rk4_step(
                state,
                dt,
                params_obj,
                p_acoustic,
                lip_model=lip_model,
                clip_opening=lip_model_type == LIP_MODEL_LEGACY,
            )
            u_t = lip_model.flow(state, params_obj, p_acoustic, air)
            p_acoustic = resonator.step(u_t)
            flow_signal[idx] = u_t
            pressure_signal[idx] = p_acoustic
            opening_m, contact_active = self._opening_and_contact(state, params_obj, lip_model, lip_model_type)
            opening_signal[idx] = opening_m
            contact_signal[idx] = contact_active

        sim = {
            "pressure_kpa": float(pressure_kpa),
            "sample_rate_hz": fs_hz,
            "time_s": np.arange(total_steps, dtype=float) / fs_hz,
            "flow_signal": flow_signal,
            "pressure_signal": pressure_signal,
            "reference_freq_hz": float(reference_freq_hz or 0.0),
            "surrogate_excitation_used": False,
        }
        if lip_model_type == LIP_MODEL_DIMENSIONED_V2 and isinstance(params_obj, DimensionedLipParameters):
            sim.update(self._dimensioned_lip_metadata(params_obj, opening_signal, contact_signal, int(warmup_s * fs_hz)))
        sim["regime"] = analyze_regime(sim, config)
        sim["rms_pressure"] = float(np.sqrt(np.mean(pressure_signal[int(warmup_s * fs_hz) :] ** 2)))
        sim["rms_flow"] = float(np.sqrt(np.mean(flow_signal[int(warmup_s * fs_hz) :] ** 2)))

        if self._needs_surrogate_excitation(sim, params_obj, lip_model_type):
            sim = self._apply_surrogate_excitation(sim, resonator, params_obj)
            sim["regime"] = analyze_regime(sim, config)
            sim["rms_pressure"] = float(np.sqrt(np.mean(np.asarray(sim["pressure_signal"])[int(warmup_s * fs_hz) :] ** 2)))
            sim["rms_flow"] = float(np.sqrt(np.mean(np.asarray(sim["flow_signal"])[int(warmup_s * fs_hz) :] ** 2)))

        sim["onset_detected"] = onset_detected(sim, config)
        return sim

    def _rk4_step(
        self,
        state: np.ndarray,
        dt: float,
        params: LipParameterSet,
        p_acoustic: float,
        lip_model: LipModel | LipModelV2 | None = None,
        clip_opening: bool = True,
    ) -> np.ndarray:
        active_model = lip_model or self.lip_model
        if active_model is None:
            active_model = LipModel()
        if isinstance(active_model, LipModelV2) and isinstance(params, DimensionedLipParameters):
            substeps = active_model.integration_substeps(dt, params)
            sub_dt = dt / substeps
            next_state = np.asarray(state, dtype=float)
            for _ in range(substeps):
                next_state = self._rk4_step_once(next_state, sub_dt, params, p_acoustic, active_model, clip_opening=False)
            return next_state
        return self._rk4_step_once(state, dt, params, p_acoustic, active_model, clip_opening=clip_opening)

    def _rk4_step_once(
        self,
        state: np.ndarray,
        dt: float,
        params: LipParameterSet,
        p_acoustic: float,
        lip_model: LipModel | LipModelV2,
        *,
        clip_opening: bool,
    ) -> np.ndarray:
        f = lip_model.derivatives
        k1 = f(0.0, state, params, p_acoustic)
        k2 = f(0.0, state + 0.5 * dt * k1, params, p_acoustic)
        k3 = f(0.0, state + 0.5 * dt * k2, params, p_acoustic)
        k4 = f(0.0, state + dt * k3, params, p_acoustic)
        next_state = state + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
        if clip_opening:
            next_state[0] = max(next_state[0], 0.0)
        return next_state

    def _needs_surrogate_excitation(self, sim: Mapping[str, Any], params: LipParameterSet, lip_model_type: str = LIP_MODEL_LEGACY) -> bool:
        if lip_model_type == LIP_MODEL_DIMENSIONED_V2:
            return False
        regime = dict(sim.get("regime", {}) or {})
        dominant = float(regime.get("dominant_freq_hz", 0.0) or 0.0)
        return bool(float(params.mouth_pressure_kpa) >= self._surrogate_threshold(params) and dominant < 20.0)

    def _surrogate_threshold(self, params: LipParameterSet) -> float:
        q_factor = float(getattr(params, "q_factor", 5.0))
        return float(0.55 + 0.004 * float(params.resonance_hz) + 0.015 * q_factor)

    def _apply_surrogate_excitation(self, sim: dict[str, Any], resonator: TimeDomainResonator, params: LipParameterSet) -> dict[str, Any]:
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

    def _lip_model_type(self, config: Mapping[str, Any] | None) -> str:
        nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
        lip_model_type = str(nonlinear_cfg.get("lip_model_type", LIP_MODEL_LEGACY))
        if lip_model_type not in {LIP_MODEL_LEGACY, LIP_MODEL_DIMENSIONED_V2}:
            raise ValueError(f"Unknown nonlinear_simulation.lip_model_type={lip_model_type!r}.")
        return lip_model_type

    def _coerce_params(self, params: LipParameterSet | Mapping[str, Any], lip_model_type: str) -> LipParameterSet:
        if lip_model_type == LIP_MODEL_DIMENSIONED_V2:
            if isinstance(params, DimensionedLipParameters):
                return params
            if isinstance(params, LipParameters):
                return self._dimensioned_params_from_legacy(params)
            data = dict(params)
            if "rest_opening_mm" in data and "rest_opening_m" not in data:
                data["rest_opening_m"] = float(data["rest_opening_mm"]) / 1000.0
            if "width_mm" in data and "lip_width_m" not in data:
                data["lip_width_m"] = float(data["width_mm"]) / 1000.0
            allowed = set(DimensionedLipParameters.__dataclass_fields__)
            return DimensionedLipParameters(**{key: value for key, value in data.items() if key in allowed})
        if isinstance(params, LipParameters):
            return params
        if isinstance(params, DimensionedLipParameters):
            return LipParameters(
                mouth_pressure_kpa=params.mouth_pressure_kpa,
                resonance_hz=params.resonance_hz,
                rest_opening_mm=1000.0 * params.rest_opening_m,
                width_mm=1000.0 * params.lip_width_m,
                mass_kg=params.mass_kg,
                damping_ratio=params.damping_ratio,
                flow_coefficient=params.flow_coefficient,
            )
        return LipParameters(**dict(params))

    def _dimensioned_params_from_legacy(self, params: LipParameters) -> DimensionedLipParameters:
        return DimensionedLipParameters(
            mouth_pressure_kpa=params.mouth_pressure_kpa,
            resonance_hz=params.resonance_hz,
            mass_kg=params.mass_kg,
            rest_opening_m=params.rest_opening_mm / 1000.0,
            lip_width_m=params.width_mm / 1000.0,
            damping_ratio=params.damping_ratio,
            flow_coefficient=params.flow_coefficient,
        )

    def _replace_mouth_pressure(self, params: LipParameterSet, pressure_kpa: float) -> LipParameterSet:
        return type(params)(**{**params.as_dict(), "mouth_pressure_kpa": pressure_kpa})

    def _model_for_params(self, params: LipParameterSet, lip_model_type: str) -> LipModel | LipModelV2:
        if lip_model_type == LIP_MODEL_DIMENSIONED_V2:
            return LipModelV2(params if isinstance(params, DimensionedLipParameters) else None)
        if isinstance(self.lip_model, LipModel):
            return self.lip_model
        return LipModel(params if isinstance(params, LipParameters) else None)

    def _initial_state(self, params: LipParameterSet, lip_model_type: str) -> np.ndarray:
        if lip_model_type == LIP_MODEL_DIMENSIONED_V2 and isinstance(params, DimensionedLipParameters):
            return np.asarray([max(float(params.rest_opening_m), 0.0) * 0.02, 1e-3], dtype=float)
        legacy_params = params if isinstance(params, LipParameters) else self._coerce_params(params, LIP_MODEL_LEGACY)
        assert isinstance(legacy_params, LipParameters)
        return np.asarray([legacy_params.rest_opening_mm / 1000.0 * 1.02, 1e-3], dtype=float)

    def _opening_and_contact(
        self,
        state: Sequence[float] | np.ndarray,
        params: LipParameterSet,
        lip_model: LipModel | LipModelV2,
        lip_model_type: str,
    ) -> tuple[float, bool]:
        if lip_model_type == LIP_MODEL_DIMENSIONED_V2 and isinstance(params, DimensionedLipParameters):
            opening_m = dimensioned_opening(state, params)
            return opening_m, bool(opening_m < max(float(params.min_opening_m), 0.0))
        opening_m = lip_model.opening(state)
        return opening_m, bool(opening_m <= 0.0)

    def _dimensioned_lip_metadata(
        self,
        params: DimensionedLipParameters,
        opening_signal: Sequence[float] | np.ndarray,
        contact_signal: Sequence[bool] | np.ndarray,
        warmup_index: int,
    ) -> dict[str, Any]:
        opening_arr = np.asarray(opening_signal, dtype=float)
        contact_arr = np.asarray(contact_signal, dtype=bool)
        start = min(max(int(warmup_index), 0), max(opening_arr.size - 1, 0))
        tail_opening = opening_arr[start:]
        tail_contact = contact_arr[start:]
        finite_opening = tail_opening[np.isfinite(tail_opening)]
        opening_min = float(np.min(finite_opening)) if finite_opening.size else 0.0
        opening_max = float(np.max(finite_opening)) if finite_opening.size else 0.0
        contact_fraction = float(np.mean(tail_contact)) if tail_contact.size else 0.0
        return {
            "lip_model_type": LIP_MODEL_DIMENSIONED_V2,
            "lip_effective_area_m2": float(params.effective_area_m2),
            "lip_mass_kg": float(params.mass_kg),
            "lip_damping_ratio": float(params.damping_ratio),
            "lip_rest_opening_m": float(params.rest_opening_m),
            "lip_pressure_force_sign": float(params.pressure_force_sign),
            "opening_min_m": opening_min,
            "opening_max_m": opening_max,
            "contact_fraction": contact_fraction,
            "experimental_lip_model": True,
        }


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
