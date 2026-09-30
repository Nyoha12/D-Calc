"""Diagnostic additionnel V2/FIR sur un CONFIG/DESIGN réel, sans trajectoire jouée."""
from __future__ import annotations

# These settings precede numpy imports, including in CLI child invocations.
import os
for _thread_variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_thread_variable] = "1"

import argparse
import hashlib
import math
import signal
import time
from pathlib import Path

import numpy as np

from didgeridoo_optimizer.acoustics.air import AirProperties
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters
from didgeridoo_optimizer.nonlinear.onset_stability import (
    SearchBudget, equilibria, fir_audit, free_step, search_marginals,
    validate_parameters, verify_discrete_crossing,
)
from didgeridoo_optimizer.nonlinear.resonator_td import TimeDomainResonator
from didgeridoo_optimizer.pipeline.design_input import finite_real
from didgeridoo_optimizer.pipeline.evaluate_linear import LinearEvaluationPipeline
from didgeridoo_optimizer.pipeline.evaluate_nonlinear import NonlinearPipeline
from didgeridoo_optimizer.pipeline.fixed_design import load_fixed_context
from didgeridoo_optimizer.reporting.nonlinear_onset import (
    Progress, check_output, export_bundle, source_fingerprints, strict_json,
)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    p = Parser(description=__doc__, allow_abbrev=False)
    p.add_argument("--config", required=True)
    p.add_argument("--design", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--upstream", choices=["ideal"], required=True)
    p.add_argument("--parameter-source", choices=["explicit", "pipeline_defaults"], required=True)
    p.add_argument("--pressure-min-pa", type=float, required=True)
    p.add_argument("--pressure-max-pa", type=float, required=True)
    p.add_argument("--frequency-min-hz", type=float, required=True)
    p.add_argument("--frequency-max-hz", type=float, required=True)
    p.add_argument("--pressure-seeds", type=int, default=5)
    p.add_argument("--frequency-seeds", type=int, default=8)
    p.add_argument("--max-iterations", type=int, default=16)
    p.add_argument("--max-evaluations", type=int, default=6000)
    p.add_argument("--seconds", type=float, default=120.)
    p.add_argument("--memory-mib", type=int, default=768)
    p.add_argument("--dry-run", action="store_true")
    return p


def _finite_int(value, name, lower, upper):
    n = finite_real(value, name)
    if n != int(n) or not lower <= n <= upper:
        raise ValueError(f"{name}: integer in [{lower}, {upper}] required")
    return int(n)


def _settings(context, args):
    cfg = context["config"].get("nonlinear_simulation")
    if not isinstance(cfg, dict) or cfg.get("lip_model_type") != "dimensioned_v2":
        raise ValueError("CONFIG must explicitly select nonlinear_simulation.lip_model_type=dimensioned_v2")
    allowed = set(DimensionedLipParameters.__dataclass_fields__)
    controls = {"enabled", "lip_model_type", "sample_rate_hz", "resonator_model_type", "resonator_scaling_mode",
                "resonator_kernel_duration_s", "kernel_duration_s", "resonator_max_kernel_duration_s",
                "simulation_duration_s", "warmup_duration_s", "pressure_scan_points", "run_only_for_top_n"}
    unknown = set(cfg) - allowed - controls
    if unknown:
        raise ValueError(f"Unsupported nonlinear keys: {sorted(unknown)}")
    if cfg.get("enabled", True) is not True:
        raise ValueError("nonlinear_simulation.enabled must be true")
    direct = {key: cfg[key] for key in sorted(allowed & set(cfg))}
    if args.parameter_source == "explicit" and set(direct) != allowed:
        raise ValueError(f"Explicit V2 parameters missing: {sorted(allowed-set(direct))}")
    # Validate overrides against the actual dataclass; defaults depending on
    # acoustic features remain unresolved in a dry-run, and are reported so.
    air = AirProperties.from_config(context["config"])
    for name, value in air.__dict__.items() if hasattr(air, "__dict__") else (("rho", air.rho), ("c", air.c)):
        finite_real(value, name)
    validate_parameters(DimensionedLipParameters(**direct), air.rho)
    requested_fs = _finite_int(cfg.get("sample_rate_hz", 44100), "sample_rate_hz", 1, 192000)
    fs = min(requested_fs, 12000)
    model = cfg.get("resonator_model_type", cfg.get("resonator_scaling_mode", "legacy"))
    if model not in {"fir_long_logfit", "legacy", "normalized_legacy", "legacy_current"}:
        raise ValueError("Unsupported existing FIR model")
    duration = finite_real(cfg.get("resonator_kernel_duration_s", cfg.get("kernel_duration_s", 1.)), "kernel_duration_s", positive=True)
    maximum = finite_real(cfg.get("resonator_max_kernel_duration_s", 2.), "resonator_max_kernel_duration_s", positive=True)
    n = max(64, round(fs * max(.02, min(duration, maximum)))) if model == "fir_long_logfit" else min(256, max(64, int(fs * .01)))
    if n > 24000:
        raise ValueError("FIR exceeds the bounded 24000-coefficient diagnostic budget")
    for name in ("pressure_min_pa", "pressure_max_pa", "frequency_min_hz", "frequency_max_hz", "seconds"):
        finite_real(getattr(args, name), name)
    if not 0 <= args.pressure_min_pa < args.pressure_max_pa or not 0 < args.frequency_min_hz < args.frequency_max_hz < fs/2:
        raise ValueError("Increasing nonnegative pressure bounds and positive frequency bounds below Nyquist required")
    frequency = context["config"].get("frequency_analysis", {})
    flo, fhi = frequency.get("f_min_hz", 10.), frequency.get("f_max_hz", 5000.)
    if args.frequency_min_hz < flo or args.frequency_max_hz > fhi:
        raise ValueError("Search frequencies must lie inside the documented linear frequency band")
    points = _finite_int(frequency.get("n_points", 4096), "n_points diagnostic budget", 2, 16384)
    mesh = float(frequency.get("discretization_max_segment_cm", 1.))
    estimated_segments = sum(max(1, math.ceil(segment.length_cm / mesh)) for segment in context["design"].segments)
    if estimated_segments > 2048 or estimated_segments * points > 8_000_000:
        raise ValueError("Linear evaluation exceeds the diagnostic mesh/frequency work budget")
    for name in ("pressure_seeds", "frequency_seeds", "max_iterations"):
        _finite_int(getattr(args, name), name, 1, 32)
    _finite_int(args.max_evaluations, "max_evaluations", 1, 20000)
    _finite_int(args.memory_mib, "memory_mib", 128, 768)
    if not 0 < args.seconds <= 180:
        raise ValueError("seconds must lie in (0,180]")
    return cfg, air, direct, {"requested_sample_rate_hz": requested_fs, "effective_sample_rate_hz": fs,
                             "requested_model": model, "expected_kernel_length": n,
                             "requested_duration_s": duration, "maximum_duration_s": maximum}


def _input_hashes(context):
    return {label: {"sha256": entry["sha256"], "read": entry["read"]}
            for label, entry in context["provenance"]["files"].items()}


def _check_inputs(context):
    for label, entry in context["provenance"]["files"].items():
        path = Path(entry["path"])
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current != entry["sha256"]:
            raise ValueError(f"Input changed during diagnostic: {label}")


def run(args) -> dict:
    started = time.monotonic()
    context = load_fixed_context(args.config, args.design, output_dir_override=args.output_dir)
    cfg, air, direct, fir_settings = _settings(context, args)
    output = Path(context["output_dir"])
    check_output(output)
    source_before = source_fingerprints()
    payload = {
        "schema_version": "dcalc.nonlinear_onset.v1", "ok": True, "status": "preflight_valid",
        "dry_run": args.dry_run, "design_id": context["design"].id,
        "assumptions": {"upstream": "ideal", "zu": 0., "state": "SI; x positive opens; h=h0+x",
                        "closures": ["reference_pd_zero", "fir_dc"], "parameter_status": "to_calibrate",
                        "physiological_identification": None, "surrogate": False},
        "parameters": {"source_policy": args.parameter_source, "direct": direct,
                       "pipeline_defaults_chosen": {}, "pending_pipeline_defaults": sorted(set(DimensionedLipParameters.__dataclass_fields__)-set(direct))},
        "fir_settings": fir_settings, "air": {"rho_kg_m3": air.rho, "c_m_s": air.c},
        "materials_used": context["materials_used"],
        "provenance": {"inputs": _input_hashes(context), "software": context["provenance"]["software"], "sources_sha256": source_before},
        "budget": {"seconds": args.seconds, "memory_mib": args.memory_mib, "blas_threads": 1,
                   "max_evaluations_total": args.max_evaluations, "pressure_seeds": args.pressure_seeds,
                   "frequency_seeds": args.frequency_seeds, "max_iterations": args.max_iterations,
                   "orchestration": "one sequential diagnostic; shared search budget; no subprocess or global sweep"},
        "domain": {"pressure_pa": [args.pressure_min_pa, args.pressure_max_pa], "frequency_hz": [args.frequency_min_hz, args.frequency_max_hz]},
        "not_executed": ["played_time_signal", "player_scores", "optimization", "calibration", "A-E", "material_promotion"],
    }
    if args.dry_run:
        _check_inputs(context)
        payload["not_executed"].extend(["acoustics", "fir_construction", "stability_search", "exports"])
        return payload
    payload.update(status="not_resolved", equilibria={"reference_pd_zero": [], "fir_dc": []}, searches=[])
    progress = Progress(output)
    progress.save(payload, "preflight_complete")
    try:
        result = LinearEvaluationPipeline().evaluate(context["design"], context["config"], context["material_db"])
        if result.get("errors"):
            raise ValueError("Linear evaluation failed geometry checks")
        frequencies = np.asarray(result["freq_hz"], dtype=float)
        impedance = np.asarray(result["zin"], dtype=complex)
        if not np.all(np.isfinite(impedance)) or len(impedance) < 2:
            raise ValueError("Non-finite or empty linear impedance")
        params = NonlinearPipeline()._default_dimensioned_lip_params(result["features"], cfg)
        validate_parameters(params, air.rho)
        payload["parameters"].update(effective=params.as_dict(), pending_pipeline_defaults=[],
                                     pipeline_defaults_chosen={k:v for k,v in params.as_dict().items() if k not in direct})
        progress.save(payload, "linear_complete")
        resonator = TimeDomainResonator.from_linear_result(result, context["config"])
        kernel, fs = resonator.impulse_kernel, resonator.sample_rate_hz
        points = np.unique(np.r_[0., np.linspace(args.frequency_min_hz, args.frequency_max_hz, 65), fs/2,
                                 [f for f in resonator.metadata.get("scaling_reference_points_hz", []) if 0 <= f <= fs/2]])
        payload["fir"] = fir_audit(kernel, fs, points, frequencies, impedance)
        payload["fir"]["production_metadata"] = resonator.metadata
        payload["linear"] = {"documented_samples": len(frequencies), "f0_hz": result["features"].get("f0_hz"),
                             "peak_frequencies_hz": [float(p["frequency_hz"]) for p in result.get("peaks", [])],
                             "geometry_valid": not result["errors"], "objective_constraints_valid": bool(result["valid"])}
        pressures = np.linspace(args.pressure_min_pa, args.pressure_max_pa, args.pressure_seeds)
        dc = float(np.sum(kernel))
        for p in pressures:
            for closure, resistance in (("reference_pd_zero", 0.), ("fir_dc", dc)):
                payload["equilibria"][closure].append(equilibria(params, air.rho, float(p), closure=closure, dc=resistance))
        phi, g, substeps = free_step(params, fs)
        payload["discrete_step"] = {"phi": phi.tolist(), "g": g.tolist(), "substeps": substeps,
                                    "order": "previous pressure held through all RK4 substeps, then flow, then FIR"}
        progress.save(payload, "equilibria_and_fir_complete")
        remaining = args.seconds - (time.monotonic()-started)
        if remaining <= 0:
            raise TimeoutError("Diagnostic deadline reached before search")
        budget = SearchBudget(args.max_evaluations, remaining)
        def zd(f):
            if not frequencies[0] <= f <= frequencies[-1]:
                raise ValueError("continuous frequency outside documented band")
            return complex(np.interp(f, frequencies, impedance.real), np.interp(f, frequencies, impedance.imag))
        indices = np.arange(len(kernel))
        def fir_response(f):
            return complex(np.dot(kernel, np.exp(-2j*np.pi*f*indices/fs)))
        for closure, resistance, discrete in (("reference_pd_zero", 0., False), ("fir_dc", dc, False), ("fir_dc", dc, True)):
            search = search_marginals(params, air.rho, closure=closure, dc=resistance,
                                     pressure_bounds=(args.pressure_min_pa, args.pressure_max_pa),
                                     frequency_bounds=(args.frequency_min_hz, args.frequency_max_hz),
                                     pressure_seeds=args.pressure_seeds, frequency_seeds=args.frequency_seeds,
                                     max_iterations=args.max_iterations, budget=budget,
                                     impedance=zd if closure == "reference_pd_zero" else fir_response,
                                     impedance_label="sampled_impedance_reference_pd_zero" if closure == "reference_pd_zero" else "continuous_lips_fir_delay_real_axis",
                                     kernel=kernel if discrete else None, fs_hz=fs)
            payload["searches"].append(search)
            progress.save(payload, "search_" + search["representation"])
            if discrete:
                for candidate in search["candidates"]:
                    margin = min(candidate["pressure_pa"]-args.pressure_min_pa, args.pressure_max_pa-candidate["pressure_pa"])
                    if margin <= 0:
                        continue
                    crossing = verify_discrete_crossing(params, air.rho, kernel, fs, candidate,
                                                       pressure_step_pa=min(10., margin/2), budget=budget,
                                                       frequency_bounds=(args.frequency_min_hz, args.frequency_max_hz))
                    candidate["crossing"] = crossing["status"]
                    candidate["continuation"] = crossing
                    if crossing["budget_exhausted"]:
                        raise TimeoutError("Budget/interruption during discrete continuation")
            if search["budget_exhausted"]:
                raise TimeoutError("Shared search budget exhausted; partial results retained")
        payload["budget"]["evaluations_used"] = budget.evaluations
        payload.update(status="numerical_model_only")
    except (TimeoutError, KeyboardInterrupt, MemoryError) as exc:
        payload.update(ok=False, status="not_resolved", partial=True, error=type(exc).__name__ + ": " + str(exc))
    progress.save(payload, "calculation_complete" if payload["ok"] else "interrupted")
    _check_inputs(context)
    sources_after = source_fingerprints()
    if any(sources_after.get(path) != value for path, value in source_before.items()):
        raise ValueError("Loaded source changed during diagnostic")
    payload["provenance"]["sources_sha256"] = sources_after
    payload["provenance"]["inputs_and_sources_unchanged"] = True
    payload["elapsed_seconds"] = time.monotonic()-started
    progress.save(payload, "provenance_verified")
    payload["exports"] = export_bundle(payload, output, own_progress=True)
    return payload


def main(argv=None):
    previous_handler = None
    old_limit = None
    termination_handlers = {}
    try:
        args = parser().parse_args(argv)
        # Hard memory ceiling is installed before acoustic construction. POSIX
        # alarm interrupts only this process; this command creates no children.
        if not args.dry_run:
            import resource
            _finite_int(args.memory_mib, "memory_mib", 128, 768)
            if not 0 < finite_real(args.seconds, "seconds") <= 180:
                raise ValueError("seconds must lie in (0,180]")
            old_limit = resource.getrlimit(resource.RLIMIT_AS)
            ceiling = args.memory_mib*1024*1024
            resource.setrlimit(resource.RLIMIT_AS, (min(ceiling, old_limit[0]) if old_limit[0] >= 0 else ceiling, old_limit[1]))
            def interrupted(signum, frame):
                raise TimeoutError("Diagnostic wall-clock limit reached")
            previous_handler = signal.signal(signal.SIGALRM, interrupted)
            for signum in (signal.SIGTERM, signal.SIGINT):
                termination_handlers[signum] = signal.signal(signum, interrupted)
            signal.setitimer(signal.ITIMER_REAL, args.seconds)
        result = run(args)
    except (Exception, KeyboardInterrupt) as exc:
        result = {"ok": False, "status": "not_resolved", "partial": isinstance(exc, (TimeoutError, KeyboardInterrupt, MemoryError)),
                  "error": {"type": type(exc).__name__, "message": str(exc)},
                  "recovery": "If preflight completed, onset_partial.json and onset_trace.jsonl retain the latest durable stage."}
    finally:
        if previous_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        if old_limit is not None:
            resource.setrlimit(resource.RLIMIT_AS, old_limit)
        for signum, handler in termination_handlers.items():
            signal.signal(signum, handler)
    print(strict_json(result), end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
