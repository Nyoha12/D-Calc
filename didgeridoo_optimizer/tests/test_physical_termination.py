"""Boundary contract and numerical references, not experimental validation."""
from __future__ import annotations

import copy
import inspect
import unittest
from unittest.mock import patch

import numpy as np

from didgeridoo_optimizer.acoustics import transfer_matrix as tm
from didgeridoo_optimizer.acoustics.air import AirProperties
from didgeridoo_optimizer.geometry.builders import DesignBuilder
from didgeridoo_optimizer.geometry.discretization import GeometryDiscretizer
from didgeridoo_optimizer.materials.models import AcousticParameter, Material
from didgeridoo_optimizer.pipeline import evaluate_linear as linear


AIR = AirProperties(rho=1.204, c=343.0, temperature_c=20.0, humidity_percent=50.0)


def fixture_materials(beta: float = 0.0) -> dict[str, Material]:
    def parameter(value):
        return AcousticParameter(value, value, value, "inferred", "low")

    return {"test_only": Material(
        id="test_only", base_material="test_only", family="test_only",
        subtype="test_only", variant=None, beta=parameter(beta),
        wall_loss=parameter(0.0), porosity_leak=parameter(0.0),
        manufacturability="test_only", cost_level="test_only", mass_level="test_only",
        recommended_for_mouthpiece=True, recommended_for_body=True, recommended_for_bell=True,
        notes="Synthetic fixture, not a calibrated or promoted material.",
    )}


def fixture_design(kind: str):
    def segment(shape, length, inlet, outlet):
        return {"kind": shape, "length_cm": length, "d_in_cm": inlet,
                "d_out_cm": outlet, "material_id": "test_only"}

    if kind == "cylinder":
        segments = [segment("cylinder", 100.0, 3.0, 3.0)]
    elif kind == "cone":
        segments = [segment("cone", 140.0, 3.8, 12.0)]
    else:
        segments = [segment("cylinder", 120.0, 3.8, 3.8), segment(kind, 20.0, 3.8, 12.0)]
        if kind == "flare_exponential":
            segments[-1]["profile_params"] = {"flare_parameter": 3.0}
    return DesignBuilder().build({"id": kind, "segments": segments})


def fixture_config(h: float = 1.0):
    return {
        "environment": {"air_density_kg_m3": AIR.rho, "sound_speed_m_s": AIR.c},
        "frequency_analysis": {"f_min_hz": 40.0, "f_max_hz": 600.0,
                               "n_points": 128, "discretization_max_segment_cm": h},
        "objectives": {},
    }


def reference_radiation(freq, radius):
    """Frozen load equation, independent of production radiation helpers."""
    omega = 2.0 * np.pi * np.asarray(freq)
    return AIR.rho * omega**2 / (4.0 * np.pi * AIR.c) + 1j * 0.613 * AIR.rho * omega / (np.pi * radius)


def reference_cylinder(freq, length, radius, load, beta=0.0):
    """Analytic ABCD matrix; no production propagation/loss helper is used."""
    omega = 2.0 * np.pi * np.asarray(freq)
    k0 = omega / AIR.c
    alpha = 1.0e-5 * beta * np.sqrt(omega) / (2.0 * radius)
    k = k0 - 1j * alpha
    zc = AIR.rho * AIR.c / (np.pi * radius**2) * (1.0 + 1j * alpha / k0)
    cosine, sine = np.cos(k * length), np.sin(k * length)
    return (cosine * load + 1j * zc * sine) / (1j * sine * load / zc + cosine)


def reference_cone(freq, length, r_in, r_out, load):
    """Lossless Webster solution q=x*p propagated from outlet to inlet."""
    if r_in == r_out:
        return reference_cylinder(freq, length, r_in, load)
    omega = 2.0 * np.pi * np.asarray(freq)
    k0 = omega / AIR.c
    m = (r_out - r_in) / length
    x1, x2 = r_in / m, r_out / m
    s1, s2 = np.pi * r_in**2, np.pi * r_out**2
    q2 = x2 * load
    q2prime = load - 1j * omega * AIR.rho * x2 / s2  # U2=1 normalization
    cosine, sine = np.cos(k0 * length), np.sin(k0 * length)
    q1 = cosine * q2 - sine * q2prime / k0
    q1prime = k0 * sine * q2 + cosine * q2prime
    p1 = q1 / x1
    u1 = -s1 / (1j * omega * AIR.rho) * (q1prime / x1 - q1 / x1**2)
    return p1 / u1


def fixture_reference(freq, kind):
    if kind == "cylinder":
        return reference_cylinder(freq, 1.0, 0.015, reference_radiation(freq, 0.015))
    length = 1.4 if kind == "cone" else 0.2
    cone = reference_cone(freq, length, 0.019, 0.06, reference_radiation(freq, 0.06))
    if kind == "cone":
        return cone
    if kind != "flare_conical":
        raise ValueError("The exponential profile has no Webster-cone reference.")
    return reference_cylinder(freq, 1.2, 0.019, cone)


def refined_modes(evaluate, brackets):
    """Bounded local searches; Q uses absolute half-power, not API half-prominence.

    Brackets identify the same modes before refinement. 17-point local grids
    reduce peak brackets by eight each iteration. Width roots are bisected,
    so a broad discovery grid is never used to estimate a narrow peak's Q.
    """
    modes = []
    for left, right in brackets:
        lo, hi = float(left), float(right)
        for _ in range(10):
            grid = np.linspace(lo, hi, 17)
            values = evaluate(grid)
            index = int(np.argmax(np.abs(values)))
            if not 0 < index < 16:
                raise AssertionError("Mode maximum escaped its tracking bracket")
            lo, hi = grid[index - 1], grid[index + 1]
        frequency = (lo + hi) / 2.0
        value = evaluate(np.array([frequency]))[0]
        target = abs(value) / np.sqrt(2.0)
        outer = np.array([left, right], dtype=float)
        if np.any(np.abs(evaluate(outer)) >= target):
            raise AssertionError("Mode bracket does not enclose both half-power crossings")
        inner = np.array([frequency, frequency])
        for _ in range(36):
            mid = (outer + inner) / 2.0
            above = np.abs(evaluate(mid)) > target
            inner = np.where(above, mid, inner)
            outer = np.where(above, outer, mid)
        roots = (outer + inner) / 2.0
        width = roots[1] - roots[0]
        modes.append({"frequency_hz": float(frequency), "magnitude": float(abs(value)),
                      "phase_rad": float(np.angle(value)), "width_hz": float(width),
                      "q_half_power": float(frequency / width),
                      "peak_bracket_hz": float(hi - lo)})
    return modes


class PhysicalTerminationTests(unittest.TestCase):
    def test_pipeline_passes_physical_radius_to_solver_and_metrics(self):
        """Same real-pipeline spy must fail before and pass after the boundary fix."""
        for kind in ("cylinder", "cone", "flare_conical", "flare_exponential"):
            for h in (1.0, 0.5, 0.25):
                with self.subTest(kind=kind, h=h):
                    design = fixture_design(kind)
                    # An annotative user radius is deliberately not authoritative.
                    design.metadata["exit_radius_m"] = 999.0
                    physical_radius = design.segments[-1].d_out_cm / 200.0
                    with patch.object(linear, "input_impedance", wraps=tm.input_impedance) as solver:
                        with patch.object(tm, "radiation_impedance", wraps=tm.radiation_impedance) as load:
                            with patch.object(linear, "radiation_impedance", wraps=linear.radiation_impedance) as metrics:
                                result = linear.LinearEvaluationPipeline().evaluate(
                                    design, fixture_config(h), fixture_materials(2.4))
                    self.assertEqual(result["errors"], [])
                    midpoint_radius = result["analysis_design"].segments[-1].d_out_cm / 200.0
                    print(f"boundary {kind} h={h}: physical={physical_radius:.12g} "
                          f"midpoint={midpoint_radius:.12g} solver_load={load.call_args.args[1]:.12g} "
                          f"metrics={metrics.call_args.args[1]:.12g}")
                    self.assertEqual(load.call_args.args[1], physical_radius)
                    self.assertEqual(metrics.call_args.args[1], physical_radius)
                    self.assertEqual(solver.call_args.kwargs.get("exit_radius_m"), physical_radius)

    def test_radius_is_keyword_only_and_physical_fallback_is_unchanged(self):
        signature = inspect.signature(tm.input_impedance)
        self.assertEqual(list(signature.parameters)[:4], ["freq_hz", "design", "materials", "air"])
        self.assertEqual(signature.parameters["exit_radius_m"].kind, inspect.Parameter.KEYWORD_ONLY)
        for kind in ("cylinder", "cone", "flare_conical"):
            design = fixture_design(kind)
            freq = np.array([40.0, 70.0, 170.0, 500.0])
            implicit = tm.input_impedance(freq, design, fixture_materials(2.4), AIR)
            explicit = tm.input_impedance(freq, design, fixture_materials(2.4), AIR,
                                          exit_radius_m=design.segments[-1].d_out_cm / 200.0)
            np.testing.assert_array_equal(implicit, explicit)

    def test_rejects_invalid_explicit_radius(self):
        invalid = [True, False, np.bool_(True), 0.0, -0.01, np.nan, np.inf, -np.inf,
                   "0.06", 0.06 + 0j, [0.06], np.array(0.06), np.array([0.06]), {}, 10**400]
        for value in invalid:
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "exit_radius_m"):
                    tm.input_impedance([100.0], fixture_design("cylinder"), fixture_materials(),
                                       exit_radius_m=value)

    def test_accepts_real_scalar_radius_and_uses_only_it_for_load(self):
        design = fixture_design("cylinder")
        for radius in (0.06, np.float32(0.06), np.float64(0.06), 1, np.int64(1)):
            with patch.object(tm, "radiation_impedance", wraps=tm.radiation_impedance) as load:
                tm.input_impedance([100.0], design, fixture_materials(), exit_radius_m=radius)
            self.assertEqual(load.call_args.args[1], float(radius))

    def test_known_mesh_requires_radius_including_rediscretization(self):
        mesh = GeometryDiscretizer().discretize(fixture_design("flare_conical"), 1.0)
        for current in (mesh, GeometryDiscretizer().discretize(mesh, 0.5)):
            with self.assertRaisesRegex(ValueError, "discretized.*exit_radius_m"):
                tm.input_impedance([100.0], current, fixture_materials())
            explicit = tm.input_impedance([100.0], current, fixture_materials(), exit_radius_m=0.06)
            self.assertTrue(np.isfinite(explicit).all())
        # Lost provenance cannot be inferred: this is a caller responsibility.
        unmarked = mesh.copy(metadata={})
        np.testing.assert_array_equal(
            tm.input_impedance([100.0], unmarked, fixture_materials()),
            tm.input_impedance([100.0], mesh, fixture_materials(),
                               exit_radius_m=mesh.segments[-1].d_out_cm / 200.0))

    def test_radius_does_not_change_mesh_or_propagation_arrays(self):
        mesh = GeometryDiscretizer().discretize(fixture_design("flare_conical"), 1.0)
        before = copy.deepcopy(mesh.as_dict())
        original = tm.DEFAULT_LOSS_MODEL.evaluate
        traces = []
        for radius in (mesh.segments[-1].d_out_cm / 200.0, 0.06):
            trace = []

            def record(*args, **kwargs):
                result = original(*args, **kwargs)
                trace.append((args[1], result.alpha_total.copy(), result.k_complex.copy(), result.zc_complex.copy()))
                return result

            with patch.object(tm.DEFAULT_LOSS_MODEL, "evaluate", side_effect=record):
                tm.input_impedance([40.0, 70.0, 170.0, 500.0], mesh, fixture_materials(2.4),
                                   AIR, exit_radius_m=radius)
            traces.append(trace)
            self.assertEqual(mesh.as_dict(), before)
        self.assertEqual(len(traces[0]), len(mesh.segments))
        for old, new in zip(*traces, strict=True):
            for old_array, new_array in zip(old, new, strict=True):
                np.testing.assert_array_equal(old_array, new_array)

    def test_loaded_cylinder_matches_independent_analytic_matrix(self):
        freq = np.array([40.0, 55.0, 70.0, 100.0, 135.0, 180.0, 240.0, 350.0, 500.0, 600.0])
        design = fixture_design("cylinder")
        for beta in (0.0, 2.4):
            reference = reference_cylinder(freq, 1.0, 0.015, reference_radiation(freq, 0.015), beta)
            for h in (1.0, 0.5, 0.25):
                mesh = GeometryDiscretizer().discretize(design, h)
                actual = tm.input_impedance(freq, mesh, fixture_materials(beta), AIR, exit_radius_m=0.015)
                # Summing at most 400 uniform matrices: roundoff, not empirical error.
                np.testing.assert_allclose(actual, reference, rtol=2e-11, atol=2e-6)

    def test_cone_cylindrical_limit_is_analytic(self):
        freq = np.array([40.0, 70.0, 170.0, 500.0])
        load = reference_radiation(freq, 0.015)
        np.testing.assert_array_equal(reference_cone(freq, 1.0, 0.015, 0.015, load),
                                      reference_cylinder(freq, 1.0, 0.015, load))

    def test_complex_impedance_converges_to_webster_at_fixed_physical_border(self):
        freq = np.array([40.0, 55.0, 70.0, 100.0, 135.0, 180.0, 240.0, 350.0, 500.0, 600.0])
        for kind in ("cone", "flare_conical"):
            expected = fixture_reference(freq, kind)
            # A characteristic impedance floor avoids division by impedance zeros.
            scale = np.maximum(np.abs(expected), AIR.rho * AIR.c / (np.pi * 0.019**2))
            errors = []
            for h in (1.0, 0.5, 0.25):
                mesh = GeometryDiscretizer().discretize(fixture_design(kind), h)
                actual = tm.input_impedance(freq, mesh, fixture_materials(), AIR, exit_radius_m=0.06)
                errors.append(float(np.max(np.abs(actual - expected) / scale)))
            print(f"Webster {kind}: normalized_complex_errors={errors}")
            # Midpoint discretization is second order: allow a margin around 1/4.
            self.assertLess(errors[1], 0.35 * errors[0])
            self.assertLess(errors[2], 0.35 * errors[1])
            self.assertLess(errors[2], 0.002)

    def test_frequency_resolution_tracks_same_narrow_modes_at_fixed_mesh(self):
        mesh = GeometryDiscretizer().discretize(fixture_design("flare_conical"), 0.25)
        materials = fixture_materials()

        def evaluate(freq):
            return tm.input_impedance(freq, mesh, materials, AIR, exit_radius_m=0.06)

        modes = refined_modes(evaluate, [(60.0, 80.0), (190.0, 220.0)])
        for mode in modes:
            center, width = mode["frequency_hz"], mode["width_hz"]
            self.assertLess(mode["peak_bracket_hz"], 1e-5 * width)
            errors = []
            magnitude_errors = []
            phase_errors = []
            # Offset the grid to avoid an artificial exact hit at the peak.
            for bins_per_width in (16, 32, 64):
                step = width / bins_per_width
                freq = center + (np.arange(-3 * bins_per_width, 3 * bins_per_width + 1) + 0.37) * step
                zin = evaluate(freq)
                mag = np.abs(zin)
                peak = int(np.argmax(mag))
                level = mag[peak] / np.sqrt(2.0)
                left = np.interp(level, mag[:peak + 1], freq[:peak + 1])
                right = np.interp(level, mag[peak:][::-1], freq[peak:][::-1])
                q = freq[peak] / (right - left)
                error = abs(q / mode["q_half_power"] - 1.0)
                errors.append(error)
                magnitude_errors.append(abs(mag[peak] / mode["magnitude"] - 1.0))
                phase_errors.append(abs(np.angle(zin[peak]) - mode["phase_rad"]))
                self.assertLess(abs(freq[peak] - center), step)
            print(f"resolved mode={mode}, sampled_q_relative_errors={errors}, "
                  f"sampled_amplitude_errors={magnitude_errors}, sampled_phase_errors_rad={phase_errors}")
            self.assertLess(errors[-1], 0.002)
            self.assertLess(errors[-1], errors[0])
            self.assertLess(magnitude_errors[-1], 0.001)
            self.assertLess(magnitude_errors[-1], magnitude_errors[0])
            self.assertLess(phase_errors[-1], 0.02)
            self.assertLess(phase_errors[-1], phase_errors[0])

    def test_tapered_mesh_self_convergence_with_and_without_synthetic_loss(self):
        freq = np.array([40.0, 55.0, 70.0, 100.0, 135.0, 180.0, 240.0, 350.0, 500.0, 600.0])
        scale = AIR.rho * AIR.c / (np.pi * 0.019**2)
        for kind in ("cone", "flare_conical", "flare_exponential"):
            for beta in (0.0, 2.4):
                values = []
                for h in (1.0, 0.5, 0.25):
                    mesh = GeometryDiscretizer().discretize(fixture_design(kind), h)
                    values.append(tm.input_impedance(freq, mesh, fixture_materials(beta), AIR,
                                                     exit_radius_m=0.06))
                coarse_delta = float(np.max(np.abs(values[1] - values[0])) / scale)
                fine_delta = float(np.max(np.abs(values[2] - values[1])) / scale)
                print(f"self convergence {kind} beta={beta}: {coarse_delta} -> {fine_delta}")
                self.assertLess(fine_delta, 0.35 * coarse_delta)


if __name__ == "__main__":
    unittest.main()
