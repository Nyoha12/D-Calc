from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import yaml

from ..pipeline.evaluate_linear import evaluate
from .validation_cases import validation_cases


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return dict(data or {})


def run_validation_bench(
    config_path: str | Path = "/mnt/data/CONFIG_TEMPLATE_V1.yaml",
    materials_path: str | Path = "/mnt/data/materials_base_v1.yaml",
    *,
    materials: Any | None = None,
) -> dict[str, Any]:
    config = load_yaml(config_path)
    bundle = validation_cases()

    evaluated: dict[str, dict[str, Any]] = {}
    for case_name, case_designs in bundle.items():
        evaluated[case_name] = {}
        for variant_name, design in case_designs.items():
            evaluated[case_name][variant_name] = evaluate(design, config, materials if materials is not None else materials_path)

    case_results = {
        "A": _check_case_a(evaluated["A"]),
        "B": _check_case_b(evaluated["B"]),
        "C": _check_case_c(evaluated["C"]),
        "D": _check_case_d(evaluated["D"]),
        "E": _check_case_e(evaluated["E"]),
    }
    all_passed = all(result["passed"] for result in case_results.values())
    return {
        "all_passed": all_passed,
        "case_results": case_results,
        "evaluated": evaluated,
    }


def _check_case_a(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    ref = results["reference"]
    longer = results["longer"]
    shorter = results["shorter"]
    peaks = ref["peaks"][:3]
    expected = [_open_closed_frequency(343.0, 1.40, n) for n in range(1, 4)]
    relative_errors = [abs(p["frequency_hz"] - target) / target for p, target in zip(peaks, expected)]
    checks = {
        "f0_exists": ref["features"].get("f0_hz") is not None,
        "peak_count_gt_2": int(ref["features"].get("peak_count", 0)) > 2,
        "length_trend": float(longer["features"].get("f0_hz") or 0.0) < float(ref["features"].get("f0_hz") or 0.0) < float(shorter["features"].get("f0_hz") or 0.0),
        "analytic_match_first_peaks": max(relative_errors or [1.0]) < 0.03,
        "good_model_confidence": float(ref["features"].get("model_confidence", 0.0)) >= 0.85,
    }
    metrics = {
        "f0_ref_hz": ref["features"].get("f0_hz"),
        "f0_longer_hz": longer["features"].get("f0_hz"),
        "f0_shorter_hz": shorter["features"].get("f0_hz"),
        "analytic_relative_errors_first3": relative_errors,
        "model_confidence_ref": ref["features"].get("model_confidence"),
    }
    return _case_result(checks, metrics)


def _check_case_b(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    cone = results["reference"]
    cyl = results["cylinder_reference"]
    toot_ratio = cone["features"].get("toot_ratio")
    odd_only_cone = cone["features"].get("odd_only_score")
    odd_only_cyl = cyl["features"].get("odd_only_score")
    checks = {
        "toot_ratio_exists": toot_ratio is not None,
        "toot_ratio_plausible": toot_ratio is not None and 1.5 <= float(toot_ratio) <= 4.5,
        "odd_only_score_exists": odd_only_cone is not None,
        "harmonicity_error_exists": cone["features"].get("harmonicity_error") is not None,
        "several_peaks": int(cone["features"].get("peak_count", 0)) >= 4,
        "cylinder_more_odd_only_than_cone": odd_only_cone is not None and odd_only_cyl is not None and float(odd_only_cyl) > float(odd_only_cone),
    }
    metrics = {
        "toot_ratio": toot_ratio,
        "odd_only_cone": odd_only_cone,
        "odd_only_cylinder": odd_only_cyl,
        "harmonicity_error": cone["features"].get("harmonicity_error"),
        "peak_count": cone["features"].get("peak_count"),
    }
    return _case_result(checks, metrics)


def _check_case_c(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    ref = results["reference"]
    bell = results["with_bell"]
    ref_rad = ref["features"].get("radiation_metrics") or {}
    bell_rad = bell["features"].get("radiation_metrics") or {}
    ref_hf = float(ref_rad.get("hf_mean_real_admittance", 0.0))
    bell_hf = float(bell_rad.get("hf_mean_real_admittance", 0.0))
    checks = {
        "brightness_higher_with_bell": float(bell["features"].get("brightness_proxy", 0.0)) > float(ref["features"].get("brightness_proxy", 0.0)),
        "hf_radiation_higher_with_bell": bell_hf > ref_hf,
        "model_confidence_lower_with_large_bell": float(bell["features"].get("model_confidence", 1.0)) < float(ref["features"].get("model_confidence", 1.0)),
    }
    metrics = {
        "brightness_ref": ref["features"].get("brightness_proxy"),
        "brightness_bell": bell["features"].get("brightness_proxy"),
        "hf_admittance_ref": ref_hf,
        "hf_admittance_bell": bell_hf,
        "confidence_ref": ref["features"].get("model_confidence"),
        "confidence_bell": bell["features"].get("model_confidence"),
    }
    return _case_result(checks, metrics)


def _check_case_d(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    stepped = results["stepped"]
    smoothed = results["smoothed"]
    freq = np.asarray(stepped["freq_hz"], dtype=float)
    z1 = np.asarray(stepped["zin_mag"], dtype=float)
    z2 = np.asarray(smoothed["zin_mag"], dtype=float)
    mask = (freq >= 50.0) & (freq <= 1000.0)
    zn1 = (z1[mask] - np.mean(z1[mask])) / (np.std(z1[mask]) + 1e-12)
    zn2 = (z2[mask] - np.mean(z2[mask])) / (np.std(z2[mask]) + 1e-12)
    profile_corr = float(np.corrcoef(zn1, zn2)[0, 1]) if np.count_nonzero(mask) > 3 else 1.0
    profile_l1 = float(np.mean(np.abs(zn1 - zn2))) if np.count_nonzero(mask) > 0 else 0.0
    harmonicity_delta = abs(float(stepped["features"].get("harmonicity_error") or 0.0) - float(smoothed["features"].get("harmonicity_error") or 0.0))
    checks = {
        "several_peaks": int(stepped["features"].get("peak_count", 0)) >= 4,
        "local_peak_structure_differs": profile_corr < 0.85 and profile_l1 > 0.2,
        "harmonicity_differs": harmonicity_delta > 0.001,
    }
    metrics = {
        "profile_corr_50_1000": profile_corr,
        "profile_l1_50_1000": profile_l1,
        "harmonicity_stepped": stepped["features"].get("harmonicity_error"),
        "harmonicity_smoothed": smoothed["features"].get("harmonicity_error"),
        "harmonicity_delta": harmonicity_delta,
    }
    return _case_result(checks, metrics)


def _check_case_e(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    diss = results["dissipative"]
    epoxy = results["epoxy_lined"]
    q_diss = float(diss["features"].get("fundamental_q") or 0.0)
    q_epoxy = float(epoxy["features"].get("fundamental_q") or 0.0)
    mag_diss = float(diss["features"].get("fundamental_peak_magnitude") or 0.0)
    mag_epoxy = float(epoxy["features"].get("fundamental_peak_magnitude") or 0.0)
    checks = {
        "q_lower_for_dissipative": q_diss < q_epoxy,
        "peak_count_not_higher": int(diss["features"].get("peak_count", 0)) <= int(epoxy["features"].get("peak_count", 0)),
        "peak_sharpness_effect_visible": mag_diss < mag_epoxy,
    }
    metrics = {
        "fundamental_q_dissipative": q_diss,
        "fundamental_q_epoxy": q_epoxy,
        "fundamental_mag_dissipative": mag_diss,
        "fundamental_mag_epoxy": mag_epoxy,
        "peak_count_dissipative": diss["features"].get("peak_count"),
        "peak_count_epoxy": epoxy["features"].get("peak_count"),
    }
    return _case_result(checks, metrics)


def _open_closed_frequency(c_sound: float, length_m: float, mode_index: int) -> float:
    return ((2 * mode_index) - 1) * c_sound / (4.0 * length_m)


def _case_result(checks: Mapping[str, bool], metrics: Mapping[str, Any]) -> dict[str, Any]:
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "passed": not failed,
        "checks": dict(checks),
        "failed_checks": failed,
        "metrics": dict(metrics),
    }


def main() -> int:
    result = run_validation_bench()
    summary = {
        "all_passed": result["all_passed"],
        "case_results": {name: {"passed": data["passed"], "failed_checks": data["failed_checks"]} for name, data in result["case_results"].items()},
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
