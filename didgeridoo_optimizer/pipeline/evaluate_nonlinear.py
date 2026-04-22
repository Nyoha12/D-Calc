from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from ..acoustics.air import AirProperties
from ..nonlinear import LipParameters, OscillationThresholdEstimator, TimeDomainResonator
from ..player import PlayerProfileSampler


class NonlinearPipeline:
    def __init__(self) -> None:
        self._thresholds = OscillationThresholdEstimator()
        self._profiles = PlayerProfileSampler()

    def evaluate(
        self,
        design_result: Mapping[str, Any],
        config: Mapping[str, Any],
        materials: str | Path | None = None,
    ) -> dict[str, Any]:
        nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
        if not bool(nonlinear_cfg.get("enabled", True)):
            out = dict(design_result)
            out["nonlinear"] = {"enabled": False, "status": "disabled"}
            return out

        features = dict(design_result.get("features", {}) or {})
        resonator = TimeDomainResonator.from_linear_model(design_result, config, linear_pipeline=None, materials=materials)
        air = AirProperties.from_config(config)
        lip_params = self._default_lip_params(features)
        threshold = self._thresholds.estimate_threshold(
            resonator,
            lip_params,
            config,
            reference_freq_hz=features.get("f0_hz"),
            air=air,
        )

        sim_result = None
        simulation_pressure_kpa = None
        if threshold.get("threshold_pressure_kpa") is not None:
            simulation_pressure_kpa = max(float(threshold["threshold_pressure_kpa"]) * 1.15, 0.25)
            sim_result = self._thresholds.simulate_at_pressure(
                resonator,
                lip_params,
                pressure_kpa=simulation_pressure_kpa,
                config=config,
                reference_freq_hz=features.get("f0_hz"),
                air=air,
            )
        else:
            simulation_pressure_kpa = max(lip_params.mouth_pressure_kpa, 0.25)
            sim_result = self._thresholds.simulate_at_pressure(
                resonator,
                lip_params,
                pressure_kpa=simulation_pressure_kpa,
                config=config,
                reference_freq_hz=features.get("f0_hz"),
                air=air,
            )

        nonlinear = {
            "enabled": True,
            "threshold": {
                "threshold_pressure_kpa": threshold.get("threshold_pressure_kpa"),
                "onset_detected": bool(threshold.get("onset_detected", False)),
                "scan_results": threshold.get("scan_results", []),
            },
            "simulation_pressure_kpa": float(simulation_pressure_kpa),
            "regime": dict(sim_result.get("regime", {}) or {}),
            "rms_pressure": float(sim_result.get("rms_pressure", 0.0)),
            "rms_flow": float(sim_result.get("rms_flow", 0.0)),
            "onset_detected": bool(sim_result.get("onset_detected", False)),
            "impulse_kernel_length": int(resonator.impulse_kernel.size),
            "reference_f0_hz": float(features.get("f0_hz") or 0.0),
        }

        out = dict(design_result)
        out["nonlinear"] = nonlinear
        objective_scores = dict(out.get("objective_scores", {}) or {})
        objective_scores["nonlinear_threshold"] = self._score_threshold(nonlinear)
        objective_scores["nonlinear_stability"] = self._score_stability(nonlinear)
        out["objective_scores"] = objective_scores
        return out

    def evaluate_batch(
        self,
        design_results: Sequence[Mapping[str, Any]],
        config: Mapping[str, Any],
        materials: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
        optimization_cfg = dict((config or {}).get("optimization", {}) or {})
        top_n = int(nonlinear_cfg.get("run_only_for_top_n", optimization_cfg.get("top_n_for_nonlinear", len(design_results))))
        ordered = sorted(design_results, key=lambda item: float(item.get("aggregate_score", float("-inf"))), reverse=True)
        selected = ordered[: max(0, top_n)]
        return [self.evaluate(item, config, materials) for item in selected]

    def _default_lip_params(self, features: Mapping[str, Any]) -> LipParameters:
        f0 = float(features.get("f0_hz") or 70.0)
        q = float(features.get("fundamental_q") or 8.0)
        profile = self._profiles.expert_preset() if q >= 10.0 else self._profiles.beginner_preset()
        return LipParameters(
            mouth_pressure_kpa=max(profile.mouth_pressure_kpa, 0.5),
            resonance_hz=max(1.1 * f0, 40.0),
            q_factor=max(2.5, min(12.0, q)),
            rest_opening_mm=1.1 if profile.skill_level == "expert" else 1.3,
            width_mm=12.0,
            mass_kg=3.0e-4,
            damping_ratio=0.10 if profile.skill_level == "expert" else 0.16,
            spring_bias_mm=0.0,
            coupling_pa_per_m=9.0e5,
            flow_coefficient=0.72,
        )

    def _score_threshold(self, nonlinear: Mapping[str, Any]) -> float:
        thr = nonlinear.get("threshold", {}) or {}
        pressure = thr.get("threshold_pressure_kpa")
        if pressure is None:
            return 0.0
        pressure = float(pressure)
        return float(max(0.0, min(1.0, 1.0 - (pressure - 0.3) / 3.5)))

    def _score_stability(self, nonlinear: Mapping[str, Any]) -> float:
        regime = nonlinear.get("regime", {}) or {}
        score = 0.0
        score += 0.55 * float(regime.get("stability_score", 0.0))
        score += 0.20 * (1.0 if bool(nonlinear.get("onset_detected", False)) else 0.0)
        score += 0.15 * (0.0 if bool(regime.get("extinction_detected", False)) else 1.0)
        score += 0.10 * (0.0 if bool(regime.get("regime_switch_detected", False)) else 1.0)
        return float(max(0.0, min(1.0, score)))


def evaluate(
    design_result: Mapping[str, Any],
    config: Mapping[str, Any],
    materials: str | Path | None = None,
) -> dict[str, Any]:
    return NonlinearPipeline().evaluate(design_result, config, materials)


def evaluate_batch(
    design_results: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    materials: str | Path | None = None,
) -> list[dict[str, Any]]:
    return NonlinearPipeline().evaluate_batch(design_results, config, materials)
