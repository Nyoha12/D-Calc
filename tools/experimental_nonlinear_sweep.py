from __future__ import annotations

"""Experimental offline LipModelV2 nonlinear sweep tool.

This is a research/diagnostic helper, not a product CLI. It does not change
LipModel/LipModelV2 equations, resonator equations, onset criteria, objective
scoring, defaults, or public targets. All physical coefficients swept here are
to_calibrate. Onsets reported by this tool are diagnostic observations only,
not player validation. The second-peak probe is deliberately explicit and does
not imply a public toot score or a validated toot regime.
"""

import argparse
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from didgeridoo_optimizer.acoustics.air import AirProperties
from didgeridoo_optimizer.materials.models import AcousticParameter, Material
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters
from didgeridoo_optimizer.nonlinear.resonator_td import TimeDomainResonator
from didgeridoo_optimizer.nonlinear.thresholds import OscillationThresholdEstimator
from didgeridoo_optimizer.pipeline.evaluate_linear import LinearEvaluationPipeline


DEFAULT_DESIGNS = ("cylinder_control", "conical_bell_9", "conical_bell_12")
DEFAULT_EFFECTIVE_AREAS_M2 = (1.0e-6, 3.0e-6, 1.0e-5)
DEFAULT_DAMPING_RATIOS = (0.05, 0.10, 0.20, 0.40)
DEFAULT_REST_OPENINGS_M = (4.0e-4, 8.0e-4, 1.2e-3)
DEFAULT_MASSES_KG = (5.0e-5, 1.0e-4, 3.0e-4)
DEFAULT_PRESSURES_KPA = (1.0, 2.0, 4.0, 6.0)
SECOND_PEAK_RESONANCE_FACTORS = (0.8, 1.0, 1.2)

FIT_METADATA_KEYS = (
    "resonator_model_type",
    "resonator_scaling_mode",
    "kernel_duration_s",
    "kernel_length",
    "frequency_response_fit_error_40_1000",
    "frequency_response_fit_error_40_3000",
    "max_over_response",
    "max_under_response",
    "scaling_reference_points_hz",
    "experimental",
)


@dataclass(frozen=True)
class SweepOptions:
    design_names: tuple[str, ...] = DEFAULT_DESIGNS
    sample_rate_hz: int = 4000
    simulation_duration_s: float = 0.55
    warmup_duration_s: float = 0.18
    confirmation_duration_s: float = 0.8
    confirmation_warmup_s: float = 0.2
    resonator_kernel_duration_s: float = 1.0
    include_positive_sign_sanity: bool = False
    include_second_peak_probe: bool = False
    confirm_top_global: int = 5
    confirm_top_per_design: int = 3
    second_probe_top: int = 5
    quick: bool = False


@dataclass(frozen=True)
class LipSweepCase:
    effective_area_m2: float
    damping_ratio: float
    rest_opening_m: float
    mass_kg: float
    pressure_force_sign: float = -1.0
    sign_scope: str = "negative_grid"


def built_in_designs() -> dict[str, dict[str, Any]]:
    material_id = "test_controlled_loss"
    return {
        "cylinder_control": {
            "id": "cylinder_control",
            "segments": [
                {
                    "kind": "cylinder",
                    "length_cm": 140.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 3.8,
                    "material_id": material_id,
                }
            ],
        },
        "conical_bell_9": {
            "id": "conical_bell_9",
            "segments": [
                {
                    "kind": "cylinder",
                    "length_cm": 120.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 3.8,
                    "material_id": material_id,
                },
                {
                    "kind": "flare_conical",
                    "length_cm": 20.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 9.0,
                    "material_id": material_id,
                },
            ],
        },
        "conical_bell_12": {
            "id": "conical_bell_12",
            "segments": [
                {
                    "kind": "cylinder",
                    "length_cm": 120.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 3.8,
                    "material_id": material_id,
                },
                {
                    "kind": "flare_conical",
                    "length_cm": 20.0,
                    "d_in_cm": 3.8,
                    "d_out_cm": 12.0,
                    "material_id": material_id,
                },
            ],
        },
    }


def test_only_materials() -> dict[str, Material]:
    material = Material(
        id="test_controlled_loss",
        base_material="test_controlled_loss",
        family="test_only",
        subtype="test_only",
        variant=None,
        beta=AcousticParameter(1.0, 1.0, 1.0, "sourced", "high"),
        wall_loss=AcousticParameter(0.0, 0.0, 0.0, "sourced", "high"),
        porosity_leak=AcousticParameter(0.0, 0.0, 0.0, "sourced", "high"),
        manufacturability="test_only",
        cost_level="test_only",
        mass_level="test_only",
        recommended_for_mouthpiece=True,
        recommended_for_body=True,
        recommended_for_bell=True,
        notes="Test-only controlled-loss fixture; not a calibration or validation claim.",
    )
    return {material.id: material}


def experimental_config(options: SweepOptions, *, confirmation: bool = False) -> dict[str, Any]:
    duration_s = options.confirmation_duration_s if confirmation else options.simulation_duration_s
    warmup_s = options.confirmation_warmup_s if confirmation else options.warmup_duration_s
    return {
        "environment": {
            "air_density_kg_m3": 1.204,
            "sound_speed_m_s": 343.0,
            "air_temperature_c": 20.0,
            "relative_humidity_percent": 50.0,
        },
        "geometry_constraints": {
            "total_length_cm": {"min": 50.0, "max": 200.0},
            "body_segments": {
                "min_count": 1,
                "max_count": 2,
                "min_length_cm": 10.0,
                "max_length_cm": 200.0,
            },
            "diameter_cm": {"min": 1.0, "max": 12.0},
            "allow_steps": True,
            "allow_reverse_taper": True,
            "allow_local_constrictions": True,
            "allow_local_expansions": True,
        },
        "topology": {
            "allow_bell": True,
            "allow_bell_types": ["conical"],
        },
        "materials": {
            "complexity_penalty": {"enabled": False},
        },
        "objectives": {},
        "frequency_analysis": {
            "f_min_hz": 40.0,
            "f_max_hz": 3000.0,
            "n_points": 8192,
            "discretization_max_segment_cm": 1.0,
            "peak_detection": {
                "min_prominence": 0.01,
                "min_distance_hz": 5.0,
                "max_number_of_peaks": 30,
            },
        },
        "nonlinear_simulation": {
            "enabled": True,
            "sample_rate_hz": int(options.sample_rate_hz),
            "simulation_duration_s": float(duration_s),
            "warmup_duration_s": float(warmup_s),
            "pressure_scan_points": 5,
            "lip_model_type": "dimensioned_v2",
            "resonator_model_type": "fir_long_logfit",
            "resonator_kernel_duration_s": float(options.resonator_kernel_duration_s),
            "resonator_max_kernel_duration_s": float(options.resonator_kernel_duration_s),
        },
    }


def default_lip_cases(*, include_positive_sign_sanity: bool = False, quick: bool = False) -> list[LipSweepCase]:
    if quick:
        return [
            LipSweepCase(
                effective_area_m2=3.0e-6,
                damping_ratio=0.20,
                rest_opening_m=8.0e-4,
                mass_kg=1.0e-4,
                pressure_force_sign=-1.0,
                sign_scope="quick_negative",
            )
        ]

    cases = [
        LipSweepCase(
            effective_area_m2=area,
            damping_ratio=damping,
            rest_opening_m=rest_opening,
            mass_kg=mass,
            pressure_force_sign=-1.0,
            sign_scope="negative_grid",
        )
        for area in DEFAULT_EFFECTIVE_AREAS_M2
        for damping in DEFAULT_DAMPING_RATIOS
        for rest_opening in DEFAULT_REST_OPENINGS_M
        for mass in DEFAULT_MASSES_KG
    ]
    if include_positive_sign_sanity:
        cases.extend(
            [
                LipSweepCase(
                    effective_area_m2=3.0e-6,
                    damping_ratio=damping,
                    rest_opening_m=8.0e-4,
                    mass_kg=1.0e-4,
                    pressure_force_sign=1.0,
                    sign_scope="positive_sanity_subset",
                )
                for damping in (0.10, 0.20)
            ]
        )
    return cases


def pressure_grid(*, quick: bool = False) -> tuple[float, ...]:
    return (2.0,) if quick else DEFAULT_PRESSURES_KPA


def build_run_specs(
    design_names: Sequence[str],
    cases: Sequence[LipSweepCase],
    pressures_kpa: Sequence[float],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for design_name in design_names:
        for case in cases:
            for pressure_kpa in pressures_kpa:
                spec = asdict(case)
                spec["design"] = design_name
                spec["pressure_kpa"] = float(pressure_kpa)
                specs.append(spec)
    return specs


def classify_run(row: Mapping[str, Any]) -> tuple[str, list[str]]:
    labels: list[str] = []
    dominant_hz = float(row.get("dominant_freq_hz") or 0.0)
    dominant_f0_ratio = row.get("dominant_f0_ratio")
    rms_pressure = float(row.get("rms_pressure") or 0.0)
    rms_flow = float(row.get("rms_flow") or 0.0)
    contact_fraction = float(row.get("contact_fraction") or 0.0)
    stability_score = float(row.get("stability_score") or 0.0)
    onset_detected = bool(row.get("onset_detected", False))
    surrogate_used = bool(row.get("surrogate_excitation_used", False))
    regime_switch = bool(row.get("regime_switch_detected", False))
    extinction = bool(row.get("extinction_detected", False))
    near_f0 = dominant_f0_ratio is not None and 0.90 <= float(dominant_f0_ratio) <= 1.10

    acceptable = bool(
        onset_detected
        and not surrogate_used
        and near_f0
        and 20.0 <= rms_pressure <= 1000.0
        and rms_flow > 1.0e-8
        and contact_fraction < 0.20
        and stability_score >= 0.20
        and not regime_switch
        and not extinction
    )
    if acceptable:
        return "acceptable", ["acceptable"]

    if dominant_hz < 20.0:
        labels.append("quasi_dc")
    if contact_fraction >= 0.20:
        labels.append("high_contact")
    if rms_pressure > 3000.0:
        labels.append("explosive")
    if rms_flow <= 1.0e-8:
        labels.append("zero_or_tiny_flow")
    if not near_f0:
        labels.append("far_from_f0")
    if regime_switch:
        labels.append("regime_switch")
    if not onset_detected:
        labels.append("failed_onset")
    if rms_pressure < 20.0 or rms_pressure > 1000.0:
        labels.append("outside_rms_pressure_range")
    if stability_score < 0.20:
        labels.append("low_stability")
    if surrogate_used:
        labels.append("uses_surrogate")
    if extinction:
        labels.append("extinction")
    if not labels:
        labels.append("rejected")
    return labels[0], labels


def quality_score(row: Mapping[str, Any]) -> float:
    dominant_f0_ratio = row.get("dominant_f0_ratio")
    rms_pressure = float(row.get("rms_pressure") or 0.0)
    contact_fraction = float(row.get("contact_fraction") or 0.0)
    stability_score = float(row.get("stability_score") or 0.0)
    score = 0.0
    if dominant_f0_ratio is not None:
        score += max(0.0, 1.0 - abs(float(dominant_f0_ratio) - 1.0) / 0.10) * 35.0
    score += max(0.0, min(stability_score, 1.0)) * 25.0
    if rms_pressure > 0.0:
        score += max(0.0, 1.0 - abs(math.log10(rms_pressure / 250.0)) / 1.0) * 15.0
    score += max(0.0, 1.0 - contact_fraction / 0.20) * 15.0
    score += 5.0 if bool(row.get("onset_detected", False)) else 0.0
    score += 5.0 if not bool(row.get("regime_switch_detected", False)) else -10.0
    return float(score)


def second_peak_hz(linear_result: Mapping[str, Any]) -> float | None:
    peaks = list(linear_result.get("peaks", []) or [])
    if len(peaks) < 2:
        return None
    return float(dict(peaks[1]).get("frequency_hz"))


def linear_references(linear_result: Mapping[str, Any], design_name: str) -> dict[str, Any]:
    features = dict(linear_result.get("features", {}) or {})
    return {
        "design": design_name,
        "valid": bool(linear_result.get("valid", False)),
        "errors": list(linear_result.get("errors", []) or []),
        "warnings": list(linear_result.get("warnings", []) or []),
        "f0_hz": float(features.get("f0_hz") or 0.0),
        "second_peak_hz": second_peak_hz(linear_result),
        "fundamental_q": float(features.get("fundamental_q") or 0.0),
    }


def simulate_run(
    *,
    spec: Mapping[str, Any],
    resonator: TimeDomainResonator,
    f0_hz: float,
    second_peak: float | None,
    config: Mapping[str, Any],
    air: AirProperties,
    estimator: OscillationThresholdEstimator,
    reference_freq_hz: float,
    resonance_hz: float,
    phase: str,
) -> dict[str, Any]:
    params = DimensionedLipParameters(
        mouth_pressure_kpa=1.5,
        resonance_hz=float(resonance_hz),
        effective_area_m2=float(spec["effective_area_m2"]),
        damping_ratio=float(spec["damping_ratio"]),
        rest_opening_m=float(spec["rest_opening_m"]),
        mass_kg=float(spec["mass_kg"]),
        pressure_force_sign=float(spec["pressure_force_sign"]),
    )
    simulation = estimator.simulate_at_pressure(
        resonator,
        params,
        pressure_kpa=float(spec["pressure_kpa"]),
        config=config,
        reference_freq_hz=float(reference_freq_hz),
        air=air,
    )
    regime = dict(simulation.get("regime", {}) or {})
    dominant_freq_hz = float(regime.get("dominant_freq_hz") or 0.0)
    row: dict[str, Any] = {
        "phase": phase,
        "design": str(spec["design"]),
        "f0_hz": float(f0_hz),
        "second_peak_hz": second_peak,
        "effective_area_m2": float(spec["effective_area_m2"]),
        "damping_ratio": float(spec["damping_ratio"]),
        "rest_opening_m": float(spec["rest_opening_m"]),
        "mass_kg": float(spec["mass_kg"]),
        "pressure_force_sign": float(spec["pressure_force_sign"]),
        "sign_scope": str(spec.get("sign_scope", "")),
        "pressure_kpa": float(spec["pressure_kpa"]),
        "resonance_hz": float(resonance_hz),
        "reference_freq_hz": float(reference_freq_hz),
        "onset_detected": bool(simulation.get("onset_detected", False)),
        "dominant_freq_hz": dominant_freq_hz,
        "dominant_f0_ratio": dominant_freq_hz / max(float(f0_hz), 1.0e-9),
        "dominant_second_peak_ratio": dominant_freq_hz / max(float(second_peak or 0.0), 1.0e-9) if second_peak else None,
        "rms_pressure": float(simulation.get("rms_pressure") or 0.0),
        "rms_flow": float(simulation.get("rms_flow") or 0.0),
        "opening_min_m": _nullable_float(simulation.get("opening_min_m")),
        "opening_max_m": _nullable_float(simulation.get("opening_max_m")),
        "contact_fraction": float(simulation.get("contact_fraction") or 0.0),
        "surrogate_excitation_used": bool(simulation.get("surrogate_excitation_used", False)),
        "stability_score": float(regime.get("stability_score") or 0.0),
        "is_stable": bool(regime.get("is_stable", False)),
        "extinction_detected": bool(regime.get("extinction_detected", False)),
        "regime_switch_detected": bool(regime.get("regime_switch_detected", False)),
        "regime_switch_frequency_delta_hz": float(regime.get("regime_switch_frequency_delta_hz") or 0.0),
    }
    for key in FIT_METADATA_KEYS:
        if key in resonator.metadata:
            output_key = "experimental_resonator" if key == "experimental" else key
            row[output_key] = _json_safe(resonator.metadata[key])
    primary, labels = classify_run(row)
    row["primary_classification"] = primary
    row["classification"] = labels
    row["quality_score"] = quality_score(row)
    return row


def run_experiment(options: SweepOptions) -> dict[str, Any]:
    design_names = tuple(options.design_names)
    available_designs = built_in_designs()
    unknown = sorted(set(design_names) - set(available_designs))
    if unknown:
        raise ValueError(f"Unknown built-in design(s): {', '.join(unknown)}")

    sweep_config = experimental_config(options, confirmation=False)
    confirmation_config = experimental_config(options, confirmation=True)
    materials = test_only_materials()
    linear_pipeline = LinearEvaluationPipeline()
    estimator = OscillationThresholdEstimator()
    air = AirProperties.from_config(sweep_config)
    confirmation_air = AirProperties.from_config(confirmation_config)

    linear_results: dict[str, Mapping[str, Any]] = {}
    resonators: dict[str, TimeDomainResonator] = {}
    confirmation_resonators: dict[str, TimeDomainResonator] = {}
    refs: dict[str, dict[str, Any]] = {}

    for design_name in design_names:
        linear_result = linear_pipeline.evaluate(available_designs[design_name], sweep_config, materials)
        linear_results[design_name] = linear_result
        resonators[design_name] = TimeDomainResonator.from_linear_result(linear_result, sweep_config)
        confirmation_resonators[design_name] = TimeDomainResonator.from_linear_result(linear_result, confirmation_config)
        refs[design_name] = linear_references(linear_result, design_name)

    cases = default_lip_cases(include_positive_sign_sanity=options.include_positive_sign_sanity, quick=options.quick)
    specs = build_run_specs(design_names, cases, pressure_grid(quick=options.quick))
    results: list[dict[str, Any]] = []
    for spec in specs:
        ref = refs[str(spec["design"])]
        f0_hz = float(ref["f0_hz"])
        results.append(
            simulate_run(
                spec=spec,
                resonator=resonators[str(spec["design"])],
                f0_hz=f0_hz,
                second_peak=ref["second_peak_hz"],
                config=sweep_config,
                air=air,
                estimator=estimator,
                reference_freq_hz=f0_hz,
                resonance_hz=max(1.1 * f0_hz, 40.0),
                phase="sweep",
            )
        )

    confirmation_candidates = select_confirmation_candidates(
        results,
        top_global=max(0, int(options.confirm_top_global)),
        top_per_design=max(0, int(options.confirm_top_per_design)),
    )
    confirmations: list[dict[str, Any]] = []
    for rank, candidate in enumerate(confirmation_candidates, start=1):
        ref = refs[str(candidate["design"])]
        f0_hz = float(ref["f0_hz"])
        row = simulate_run(
            spec=candidate,
            resonator=confirmation_resonators[str(candidate["design"])],
            f0_hz=f0_hz,
            second_peak=ref["second_peak_hz"],
            config=confirmation_config,
            air=confirmation_air,
            estimator=estimator,
            reference_freq_hz=f0_hz,
            resonance_hz=max(1.1 * f0_hz, 40.0),
            phase="confirmation",
        )
        row["rank"] = rank
        row["confirmed_long_run"] = row["primary_classification"] == "acceptable"
        row["source_quality_score"] = float(candidate.get("quality_score") or 0.0)
        confirmations.append(row)

    second_probe_results: list[dict[str, Any]] = []
    if options.include_second_peak_probe:
        second_probe_results = run_second_peak_probe(
            confirmed_candidates=confirmations,
            refs=refs,
            resonators=confirmation_resonators,
            config=confirmation_config,
            air=confirmation_air,
            estimator=estimator,
            pressures_kpa=pressure_grid(quick=options.quick),
            max_candidates=max(0, int(options.second_probe_top)),
        )

    report = {
        "notice": {
            "experimental": True,
            "scope": "offline LipModelV2 diagnostic sweep",
            "coefficients": "to_calibrate",
            "player_validation": False,
            "objective_scoring_changed": False,
            "toot_score": False,
        },
        "repo": repo_metadata(),
        "config": {
            "design_names": list(design_names),
            "sample_rate_hz": int(options.sample_rate_hz),
            "simulation_duration_s": float(options.simulation_duration_s),
            "warmup_duration_s": float(options.warmup_duration_s),
            "confirmation_duration_s": float(options.confirmation_duration_s),
            "confirmation_warmup_s": float(options.confirmation_warmup_s),
            "resonator_kernel_duration_s": float(options.resonator_kernel_duration_s),
            "lip_model_type": "dimensioned_v2",
            "resonator_model_type": "fir_long_logfit",
            "include_positive_sign_sanity": bool(options.include_positive_sign_sanity),
            "include_second_peak_probe": bool(options.include_second_peak_probe),
            "quick": bool(options.quick),
        },
        "linear_references": [refs[name] for name in design_names],
        "grid": {
            "case_count": len(cases),
            "run_count": len(specs),
            "pressure_kpa": list(pressure_grid(quick=options.quick)),
            "effective_area_m2": list(DEFAULT_EFFECTIVE_AREAS_M2 if not options.quick else [3.0e-6]),
            "damping_ratio": list(DEFAULT_DAMPING_RATIOS if not options.quick else [0.20]),
            "rest_opening_m": list(DEFAULT_REST_OPENINGS_M if not options.quick else [8.0e-4]),
            "mass_kg": list(DEFAULT_MASSES_KG if not options.quick else [1.0e-4]),
            "pressure_force_sign": [-1.0] + ([1.0] if options.include_positive_sign_sanity and not options.quick else []),
        },
        "sweep_summary": summarize_results(results),
        "sweep_results": results,
        "confirmation_summary": summarize_results(confirmations),
        "confirmation_results": confirmations,
        "second_peak_probe_summary": summarize_second_probe(second_probe_results),
        "second_peak_probe_results": second_probe_results,
    }
    return _json_safe(report)


def select_confirmation_candidates(
    results: Sequence[Mapping[str, Any]],
    *,
    top_global: int,
    top_per_design: int,
) -> list[dict[str, Any]]:
    acceptable = [dict(row) for row in results if row.get("primary_classification") == "acceptable"]
    ordered = sorted(
        acceptable,
        key=lambda row: (
            float(row.get("quality_score") or 0.0),
            float(row.get("stability_score") or 0.0),
            -abs(float(row.get("dominant_f0_ratio") or 0.0) - 1.0),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def add(row: Mapping[str, Any]) -> None:
        key = (
            row.get("design"),
            row.get("effective_area_m2"),
            row.get("damping_ratio"),
            row.get("rest_opening_m"),
            row.get("mass_kg"),
            row.get("pressure_force_sign"),
            row.get("pressure_kpa"),
        )
        if key in seen:
            return
        seen.add(key)
        selected.append(dict(row))

    for row in ordered[:top_global]:
        add(row)
    by_design: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ordered:
        by_design[str(row["design"])].append(row)
    for design_rows in by_design.values():
        for row in design_rows[:top_per_design]:
            add(row)
    return selected


def run_second_peak_probe(
    *,
    confirmed_candidates: Sequence[Mapping[str, Any]],
    refs: Mapping[str, Mapping[str, Any]],
    resonators: Mapping[str, TimeDomainResonator],
    config: Mapping[str, Any],
    air: AirProperties,
    estimator: OscillationThresholdEstimator,
    pressures_kpa: Sequence[float],
    max_candidates: int,
) -> list[dict[str, Any]]:
    confirmed = [dict(row) for row in confirmed_candidates if bool(row.get("confirmed_long_run", False))]
    ordered = sorted(
        confirmed,
        key=lambda row: (float(row.get("quality_score") or 0.0), float(row.get("stability_score") or 0.0)),
        reverse=True,
    )[:max_candidates]
    rows: list[dict[str, Any]] = []
    for candidate in ordered:
        design_name = str(candidate["design"])
        second_peak = refs[design_name].get("second_peak_hz")
        if second_peak is None:
            continue
        f0_hz = float(refs[design_name]["f0_hz"])
        for factor in SECOND_PEAK_RESONANCE_FACTORS:
            for pressure_kpa in pressures_kpa:
                spec = dict(candidate)
                spec["pressure_kpa"] = float(pressure_kpa)
                row = simulate_run(
                    spec=spec,
                    resonator=resonators[design_name],
                    f0_hz=f0_hz,
                    second_peak=float(second_peak),
                    config=config,
                    air=air,
                    estimator=estimator,
                    reference_freq_hz=float(second_peak),
                    resonance_hz=float(factor * float(second_peak)),
                    phase="second_peak_probe",
                )
                row["source_confirmation_rank"] = candidate.get("rank")
                row["resonance_factor_second_peak"] = float(factor)
                near_second = bool(
                    row["onset_detected"]
                    and not row["surrogate_excitation_used"]
                    and row["dominant_second_peak_ratio"] is not None
                    and 0.90 <= float(row["dominant_second_peak_ratio"]) <= 1.10
                    and 20.0 <= float(row["rms_pressure"]) <= 1000.0
                    and float(row["contact_fraction"]) < 0.20
                    and float(row["stability_score"]) >= 0.20
                    and not row["regime_switch_detected"]
                    and not row["extinction_detected"]
                )
                row["near_second_probe"] = near_second
                rows.append(row)
    return rows


def summarize_results(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    classifications = Counter(str(row.get("primary_classification", "unknown")) for row in rows)
    labels = Counter(label for row in rows for label in row.get("classification", []) or [])
    by_design: dict[str, dict[str, Any]] = {}
    for design_name in sorted({str(row.get("design")) for row in rows}):
        design_rows = [row for row in rows if str(row.get("design")) == design_name]
        by_design[design_name] = {
            "total": len(design_rows),
            "acceptable": sum(str(row.get("primary_classification")) == "acceptable" for row in design_rows),
            "onset": sum(bool(row.get("onset_detected")) for row in design_rows),
            "near_f0": sum(0.90 <= float(row.get("dominant_f0_ratio") or 0.0) <= 1.10 for row in design_rows),
            "quasi_dc": sum("quasi_dc" in (row.get("classification", []) or []) for row in design_rows),
            "high_contact": sum("high_contact" in (row.get("classification", []) or []) for row in design_rows),
            "explosive": sum("explosive" in (row.get("classification", []) or []) for row in design_rows),
            "surrogate": sum(bool(row.get("surrogate_excitation_used")) for row in design_rows),
        }
    return {
        "total": len(rows),
        "acceptable": sum(str(row.get("primary_classification")) == "acceptable" for row in rows),
        "onset": sum(bool(row.get("onset_detected")) for row in rows),
        "near_f0": sum(0.90 <= float(row.get("dominant_f0_ratio") or 0.0) <= 1.10 for row in rows),
        "surrogate": sum(bool(row.get("surrogate_excitation_used")) for row in rows),
        "primary_classification_counts": dict(sorted(classifications.items())),
        "classification_label_counts": dict(sorted(labels.items())),
        "by_design": by_design,
    }


def summarize_second_probe(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(rows),
        "onset": sum(bool(row.get("onset_detected")) for row in rows),
        "near_second_probe": sum(bool(row.get("near_second_probe")) for row in rows),
        "surrogate": sum(bool(row.get("surrogate_excitation_used")) for row in rows),
        "quasi_dc": sum(float(row.get("dominant_freq_hz") or 0.0) < 20.0 for row in rows),
        "max_dominant_second_peak_ratio": max((float(row.get("dominant_second_peak_ratio") or 0.0) for row in rows), default=0.0),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Experimental LipModelV2 Nonlinear Sweep",
        "",
        "Experimental offline diagnostic only. Coefficients are to_calibrate; results are not player validation, not scoring, and not a toot claim.",
        "",
    ]
    repo = dict(report.get("repo", {}) or {})
    if repo:
        lines.extend(
            [
                "## Repo",
                "",
                f"- branch: `{repo.get('branch', 'unknown')}`",
                f"- head: `{repo.get('head_short', 'unknown')}` {repo.get('head_subject', '')}".rstrip(),
                f"- tracked_clean: `{repo.get('tracked_clean', None)}`",
                "",
            ]
        )
    lines.extend(["## Linear References", "", "| design | f0_hz | second_peak_hz | warnings |", "|---|---:|---:|---|"])
    for ref in report.get("linear_references", []) or []:
        warnings = ", ".join(ref.get("warnings", []) or [])
        second_peak = ref.get("second_peak_hz")
        lines.append(f"| {ref.get('design')} | {_fmt(ref.get('f0_hz'))} | {_fmt(second_peak)} | {warnings} |")
    lines.append("")

    sweep_summary = dict(report.get("sweep_summary", {}) or {})
    lines.extend(
        [
            "## Sweep Summary",
            "",
            f"- total runs: `{sweep_summary.get('total', 0)}`",
            f"- acceptable: `{sweep_summary.get('acceptable', 0)}`",
            f"- onset_detected: `{sweep_summary.get('onset', 0)}`",
            f"- near_f0: `{sweep_summary.get('near_f0', 0)}`",
            f"- surrogate_used: `{sweep_summary.get('surrogate', 0)}`",
            "",
            "| classification | count |",
            "|---|---:|",
        ]
    )
    for label, count in dict(sweep_summary.get("primary_classification_counts", {}) or {}).items():
        lines.append(f"| {label} | {count} |")
    lines.append("")

    confirmations = list(report.get("confirmation_results", []) or [])
    if confirmations:
        lines.extend(
            [
                "## Confirmations",
                "",
                "| rank | design | area | damping | opening | mass | pressure | dom/f0 | rms_pressure | contact | stability | confirmed |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in confirmations:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row.get("rank", "")),
                        str(row.get("design", "")),
                        _fmt(row.get("effective_area_m2")),
                        _fmt(row.get("damping_ratio")),
                        _fmt(row.get("rest_opening_m")),
                        _fmt(row.get("mass_kg")),
                        _fmt(row.get("pressure_kpa")),
                        _fmt(row.get("dominant_f0_ratio")),
                        _fmt(row.get("rms_pressure")),
                        _fmt(row.get("contact_fraction")),
                        _fmt(row.get("stability_score")),
                        str(bool(row.get("confirmed_long_run", False))).lower(),
                    ]
                )
                + " |"
            )
        lines.append("")

    second_summary = dict(report.get("second_peak_probe_summary", {}) or {})
    if second_summary.get("total", 0):
        lines.extend(
            [
                "## Second Peak Probe",
                "",
                "Probe only; this is not a toot validation.",
                "",
                f"- total: `{second_summary.get('total', 0)}`",
                f"- onset: `{second_summary.get('onset', 0)}`",
                f"- near_second_probe: `{second_summary.get('near_second_probe', 0)}`",
                f"- max dominant/second_peak: `{_fmt(second_summary.get('max_dominant_second_peak_ratio'))}`",
                "",
            ]
        )
    return "\n".join(lines)


def repo_metadata() -> dict[str, Any]:
    return {
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]),
        "head": _git(["rev-parse", "HEAD"]),
        "head_short": _git(["rev-parse", "--short", "HEAD"]),
        "head_subject": _git(["log", "-1", "--pretty=%s"]),
        "tracked_clean": _git(["status", "--short", "--untracked-files=no"]) == "",
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", action="append", choices=DEFAULT_DESIGNS, help="Built-in design to include; repeatable.")
    parser.add_argument("--quick", action="store_true", help="Run a tiny smoke grid instead of the default medium grid.")
    parser.add_argument("--include-positive-sign-sanity", action="store_true", help="Add the bounded pressure_force_sign=+1 sanity subset.")
    parser.add_argument("--include-second-peak-probe", action="store_true", help="Run the explicit second-peak probe on confirmed drone candidates.")
    parser.add_argument("--sample-rate-hz", type=int, default=4000)
    parser.add_argument("--duration-s", type=float, default=0.55)
    parser.add_argument("--warmup-s", type=float, default=0.18)
    parser.add_argument("--confirmation-duration-s", type=float, default=0.8)
    parser.add_argument("--confirmation-warmup-s", type=float, default=0.2)
    parser.add_argument("--resonator-kernel-duration-s", type=float, default=1.0)
    parser.add_argument("--confirm-top-global", type=int, default=5)
    parser.add_argument("--confirm-top-per-design", type=int, default=3)
    parser.add_argument("--second-probe-top", type=int, default=5)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--stdout-format", choices=("markdown", "json"), default="markdown")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    options = SweepOptions(
        design_names=tuple(args.design or DEFAULT_DESIGNS),
        sample_rate_hz=args.sample_rate_hz,
        simulation_duration_s=args.duration_s,
        warmup_duration_s=args.warmup_s,
        confirmation_duration_s=args.confirmation_duration_s,
        confirmation_warmup_s=args.confirmation_warmup_s,
        resonator_kernel_duration_s=args.resonator_kernel_duration_s,
        include_positive_sign_sanity=bool(args.include_positive_sign_sanity),
        include_second_peak_probe=bool(args.include_second_peak_probe),
        confirm_top_global=args.confirm_top_global,
        confirm_top_per_design=args.confirm_top_per_design,
        second_probe_top=args.second_probe_top,
        quick=bool(args.quick),
    )
    report = run_experiment(options)
    json_text = json.dumps(report, indent=2, sort_keys=True)
    markdown_text = render_markdown(report)
    if args.json_output is not None:
        args.json_output.write_text(json_text + "\n", encoding="utf-8")
    if args.markdown_output is not None:
        args.markdown_output.write_text(markdown_text + "\n", encoding="utf-8")
    if args.json_output is None and args.markdown_output is None:
        print(json_text if args.stdout_format == "json" else markdown_text)
    return 0


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _git(args: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(["git", *args], check=False, capture_output=True, text=True)
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric == 0.0:
        return "0"
    if abs(numeric) < 1.0e-3 or abs(numeric) >= 1.0e4:
        return f"{numeric:.3g}"
    return f"{numeric:.3f}".rstrip("0").rstrip(".")


if __name__ == "__main__":
    raise SystemExit(main())
