from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import yaml

from didgeridoo_optimizer.pipeline import run_optimizer
from didgeridoo_optimizer.pipeline.evaluate_linear import LinearEvaluationPipeline
from didgeridoo_optimizer.pipeline.fixed_design import load_fixed_context, run_fixed_design
from didgeridoo_optimizer.reporting.fixed_design import prepare_payload
from didgeridoo_optimizer.reporting.export import _to_builtin
from didgeridoo_optimizer.tests.test_fixed_design_input import REPO_ROOT, ambiguous_design_text, minimal_config, minimal_design


@pytest.fixture
def inputs(tmp_path):
    config = minimal_config()
    config["nonlinear_simulation"] = {"enabled": True}
    config["optimization"] = {"linear_budget": 1000}
    config["project"] = {"output_dir": str(tmp_path / "configured_output")}
    config_path = tmp_path / "config.yaml"
    design_path = tmp_path / "design.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    design_path.write_text(yaml.safe_dump(minimal_design()), encoding="utf-8")
    return config_path, design_path


def forbidden_phases():
    stack = ExitStack()
    for name in ("load_context", "run", "finalize", "estimate_runtime", "run_linear_phase", "run_robustness_phase", "run_nonlinear_phase"):
        stack.enter_context(patch.object(run_optimizer.OptimizerRunner, name, side_effect=AssertionError(f"forbidden: {name}")))
    for name in ("ParetoOptimizer", "SearchSpace", "FinalSelector", "RuntimeEstimator", "NonlinearPipeline", "RobustnessPipeline", "rank", "plot_pareto", "export_best_design_bundle"):
        stack.enter_context(patch.object(run_optimizer, name, side_effect=AssertionError(f"forbidden: {name}")))
    return stack


def call_cli(inputs, *args):
    out, err = io.StringIO(), io.StringIO()
    code = run_optimizer.main(["--config", str(inputs[0]), "--design", str(inputs[1]), *map(str, args)], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_dry_run_no_acoustics_no_writes_and_absolute_paths(inputs, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    with forbidden_phases(), patch.object(LinearEvaluationPipeline, "evaluate", side_effect=AssertionError("acoustics")):
        result = run_fixed_design("config.yaml", "design.yaml", dry_run=True, output_dir_override="nested/output")
    assert result["ok"] and result["geometry_valid"] and not result["output_dir_created"]
    assert result["design_path"] == str(inputs[1].resolve())
    assert result["output_dir"] == str(tmp_path / "nested/output")
    assert "nonlinear" in result["not_executed"]
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("error", ["material", "geometry", "analysis", "syntax"])
def test_invalid_input_no_calculation_no_artifacts(inputs, tmp_path, error, dry_run):
    config = yaml.safe_load(inputs[0].read_text(encoding="utf-8"))
    design = minimal_design()
    if error == "material":
        design["segments"][0]["material_id"] = "no_such_material"
    elif error == "geometry":
        config["geometry_constraints"]["total_length_cm"]["max"] = 90
    elif error == "analysis":
        config["frequency_analysis"]["n_points"] = True
    inputs[0].write_text(yaml.safe_dump(config), encoding="utf-8")
    inputs[1].write_text("segments: [" if error == "syntax" else yaml.safe_dump(design), encoding="utf-8")
    before = set(tmp_path.rglob("*"))
    with forbidden_phases(), patch.object(LinearEvaluationPipeline, "evaluate", side_effect=AssertionError("acoustics")):
        code, out, err = call_cli(inputs, *(["--dry-run"] if dry_run else []))
    assert code == 1 and not out and "error:" in err
    assert set(tmp_path.rglob("*")) == before


def test_missing_optional_variant_rules_and_unresolvable_variant(inputs, tmp_path):
    config = yaml.safe_load(inputs[0].read_text(encoding="utf-8"))
    config["materials"]["variant_rules_file"] = str(tmp_path / "missing_rules.yaml")
    inputs[0].write_text(yaml.safe_dump(config), encoding="utf-8")
    result = run_fixed_design(*inputs, dry_run=True)
    assert "Variant rules file absent" in result["warnings"][0]
    assert result["provenance"]["files"]["variant_rules"]["read"] is False
    design = minimal_design()
    design["segments"][0]["material_id"] = "oak__airdry__raw__clear__medium"
    inputs[1].write_text(yaml.safe_dump(design), encoding="utf-8")
    with pytest.raises(ValueError, match="material_id"):
        run_fixed_design(*inputs, dry_run=True)


def test_generated_variant_actually_resolves_with_rules(inputs):
    design = minimal_design()
    material_id = "oak__airdry__raw__clear__medium"
    design["segments"][0]["material_id"] = material_id
    inputs[1].write_text(yaml.safe_dump(design), encoding="utf-8")
    context = load_fixed_context(*inputs)
    assert material_id in context["material_db"].materials
    assert context["materials_used"][material_id]["base_material"] == "oak"
    assert context["provenance"]["files"]["variant_rules"]["read"] is True


@pytest.mark.parametrize("case,match", [("missing_database", "missing_materials"), ("non_mapping", "mapping"), ("version", "schema_version")])
def test_fixed_config_errors_use_existing_helpers(inputs, tmp_path, case, match):
    config = yaml.safe_load(inputs[0].read_text(encoding="utf-8"))
    if case == "missing_database":
        config["materials"]["database_file"] = str(tmp_path / "missing_materials.yaml")
    elif case == "version":
        config["schema_version"] = "unsupported"
    else:
        config = ["not a mapping"]
    inputs[0].write_text(yaml.safe_dump(config), encoding="utf-8")
    before = set(tmp_path.rglob("*"))
    with patch.object(LinearEvaluationPipeline, "evaluate", side_effect=AssertionError("acoustics")):
        code, out, err = call_cli(inputs, "--dry-run")
    assert code == 1 and not out and match in err
    assert set(tmp_path.rglob("*")) == before


def test_software_revision_is_null_if_unavailable(inputs):
    with patch("didgeridoo_optimizer.pipeline.fixed_design.subprocess.run", side_effect=OSError("git unavailable")):
        context = load_fixed_context(*inputs)
    assert context["provenance"]["software"]["sha"] is None
    assert "could not be established" in context["provenance"]["software"]["origin"]


@pytest.mark.parametrize("tapered", [False, True], ids=["cylinder", "cone"])
def test_one_evaluation_and_full_api_parity_no_forbidden_phases(inputs, tmp_path, tapered):
    if tapered:
        raw = minimal_design()
        raw["segments"][0].update(kind="cone", d_out_cm=8.0)
        inputs[1].write_text(yaml.safe_dump(raw), encoding="utf-8")
    context = load_fixed_context(*inputs)
    expected = LinearEvaluationPipeline().evaluate(context["design"], context["config"], context["material_db"])
    before = {path: path.read_bytes() for path in (inputs[0], inputs[1], REPO_ROOT / "project_specs/materials_base_v1.yaml", REPO_ROOT / "project_specs/wood_variant_rules_v1.yaml")}
    real_evaluate = LinearEvaluationPipeline.evaluate
    calls = []

    def once(self, *args, **kwargs):
        calls.append((args, kwargs))
        return real_evaluate(self, *args, **kwargs)

    with forbidden_phases(), patch.object(LinearEvaluationPipeline, "evaluate", once):
        code, out, err = call_cli(inputs, "--output-dir", tmp_path / "output")
    assert code == 0, err
    assert len(calls) == 1
    response = json.loads(out)
    output = Path(response["output_dir"])
    assert {path.name for path in output.iterdir()} == {"evaluated_design_result.json", "evaluated_design_result.yaml", "evaluated_design_summary.txt"}
    text = (output / "evaluated_design_result.json").read_text(encoding="utf-8")
    payload = json.loads(text, parse_constant=lambda value: pytest.fail(value))
    assert payload == yaml.safe_load((output / "evaluated_design_result.yaml").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "dcalc.fixed_design.result.v1"
    assert payload["workflow"] == "linear_fixed_design"
    assert payload["result"] == _to_builtin(expected)
    assert len(payload["result"]["design"]["segments"]) == 1
    assert len(payload["result"]["analysis_design"]["segments"]) == 20
    for field in ("freq_hz", "zin", "zin_mag"):
        assert len(payload["result"][field]) == 128
    assert set(payload["result"]["zin"][0]) == {"real", "imag"}
    assert "result.features.transient_proxy" in payload["unavailable_values"]
    assert payload["materials_used"]["pvc_pressure"]["acoustic_model"]["beta_status"] == context["material_db"].get("pvc_pressure").beta.status
    assert payload["provenance"]["software"]["sha"]
    for source in payload["provenance"]["files"].values():
        assert source["sha256"] == hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest()
    for path, content in before.items():
        assert path.read_bytes() == content
    summary = (output / "evaluated_design_summary.txt").read_text(encoding="utf-8")
    assert "not experimental validation" in summary and "not a toot threshold" in summary


def test_completed_acoustics_with_hard_constraint_failure_is_exportable(inputs, tmp_path):
    config = yaml.safe_load(inputs[0].read_text(encoding="utf-8"))
    config["objectives"] = {"drone_f0": {"enabled": True, "hard_constraint": True, "target_range_hz": [500, 600]}}
    inputs[0].write_text(yaml.safe_dump(config), encoding="utf-8")
    code, out, err = call_cli(inputs, "--output-dir", tmp_path / "invalid_constraints")
    assert code == 0, err
    response = json.loads(out)
    assert response["ok"] is True and response["valid"] is False
    payload = json.loads(Path(response["exports"]["result_json"]).read_text(encoding="utf-8"))
    assert payload["valid"] is False and len(payload["result"]["zin"]) == 128


@pytest.mark.parametrize("field,value", [("zin", complex(float("nan"), 1)), ("zin_mag", float("inf")), ("freq_hz", float("nan"))])
def test_nonfinite_curve_is_calculation_failure_without_output(inputs, tmp_path, field, value):
    context = load_fixed_context(*inputs)
    result = LinearEvaluationPipeline().evaluate(context["design"], context["config"], context["material_db"])
    result[field][4] = value
    with patch.object(LinearEvaluationPipeline, "evaluate", return_value=result):
        code, out, err = call_cli(inputs, "--output-dir", tmp_path / "bad_curve")
    assert code == 1 and not out and field in err
    assert not (tmp_path / "bad_curve").exists()


def test_missing_diagnostic_keeps_list_alignment_and_reason(inputs):
    context = load_fixed_context(*inputs)
    result = LinearEvaluationPipeline().evaluate(context["design"], context["config"], context["material_db"])
    result["features"]["extra"] = np.array([1, np.nan, 3])
    result["features"]["complex"] = 2 + 3j
    result["features"]["complex_numpy"] = np.complex128(4 + 5j)
    payload = prepare_payload(result, context)
    assert payload["result"]["features"]["extra"] == [1, None, 3]
    assert "result.features.extra[1]" in payload["unavailable_values"]
    assert payload["result"]["features"]["complex"] == {"real": 2, "imag": 3}
    assert payload["result"]["features"]["complex_numpy"] == {"real": 4, "imag": 5}
    assert np.isnan(result["features"]["extra"][1])


def test_misaligned_curves_rejected(inputs):
    context = load_fixed_context(*inputs)
    result = LinearEvaluationPipeline().evaluate(context["design"], context["config"], context["material_db"])
    result["zin"] = result["zin"][:-1]
    with pytest.raises(ValueError, match="aligned"):
        prepare_payload(result, context)


@pytest.mark.parametrize("failure", ["calculation", "serialization", "io"])
def test_cli_errors_are_nonzero_and_never_announce_success(inputs, tmp_path, failure):
    if failure == "calculation":
        target, effect = "didgeridoo_optimizer.pipeline.fixed_design.LinearEvaluationPipeline.evaluate", RuntimeError("calculation fault")
    elif failure == "serialization":
        target, effect = "didgeridoo_optimizer.pipeline.fixed_design.prepare_payload", ValueError("serialization fault")
    else:
        target, effect = "didgeridoo_optimizer.reporting.fixed_design.export_json", OSError("I/O fault")
    with patch(target, side_effect=effect):
        code, out, err = call_cli(inputs, "--output-dir", tmp_path / "fault")
    assert code == 1 and not out and "fault" in err


def test_serialization_preflight_creates_nothing(inputs, tmp_path):
    context = load_fixed_context(*inputs)
    result = LinearEvaluationPipeline().evaluate(context["design"], context["config"], context["material_db"])
    result["features"]["unsupported"] = object()
    with patch.object(LinearEvaluationPipeline, "evaluate", return_value=result):
        code, out, err = call_cli(inputs, "--output-dir", tmp_path / "unsupported")
    assert code == 1 and not out and "serializable" in err
    assert not (tmp_path / "unsupported").exists()


def test_existing_bundle_is_not_overwritten(inputs, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    original = output / "evaluated_design_result.yaml"
    original.write_text("keep me", encoding="utf-8")
    code, out, err = call_cli(inputs, "--output-dir", output)
    assert code == 1 and not out and "overwrite" in err
    assert original.read_text(encoding="utf-8") == "keep me"
    assert len(list(output.iterdir())) == 1


@pytest.mark.parametrize("suffix", ["yaml", "json"])
def test_real_module_cli_end_to_end(inputs, tmp_path, suffix):
    design_path = tmp_path / f"physical.{suffix}"
    raw = minimal_design()
    design_path.write_text(json.dumps(raw) if suffix == "json" else yaml.safe_dump(raw), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "didgeridoo_optimizer.pipeline.run_optimizer", "--config", str(inputs[0]), "--design", str(design_path), "--output-dir", str(tmp_path / f"e2e_{suffix}")],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    response = json.loads(proc.stdout)
    assert response["ok"] and response["workflow"] == "linear_fixed_design"
    assert Path(response["exports"]["result_json"]).is_file()
    assert "RuntimeWarning" not in proc.stderr


def test_r7_f1_real_material_database_api_no_annotation_loss(inputs):
    """Before the fix, exercise the real API and expose two keys becoming one."""
    text = yaml.safe_dump(minimal_design()) + "metadata:\n  observations:\n    1: premiere observation\n    '1': seconde observation\n"
    inputs[1].write_text(text, encoding="utf-8")
    try:
        context = load_fixed_context(*inputs)
    except ValueError as exc:
        assert "observations" in str(exc) and "key" in str(exc).lower()
        return  # Corrected boundary rejects before any acoustic call.
    from didgeridoo_optimizer.materials import MaterialDatabase
    assert isinstance(context["material_db"], MaterialDatabase)
    result = LinearEvaluationPipeline().evaluate(context["design"], context["config"], context["material_db"])
    original = result["design"].metadata["observations"]
    exported = prepare_payload(result, context)["result"]["design"]["metadata"]["observations"]
    assert len(original) == 2
    assert exported == original, f"real MaterialDatabase/API: accepted {original!r}, exported {exported!r}"


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("suffix,case", [
    ("yaml", "nontext"), ("yaml", "cycle"), ("yaml", "nonfinite"), ("yaml", "unsupported"),
    ("yaml", "merge"),
    *[(suffix, level) for suffix in ("yaml", "json") for level in ("top", "segment", "profile_params", "metadata")],
])
def test_r7_ambiguous_input_cli_fails_before_acoustics_or_output(inputs, tmp_path, suffix, case, dry_run):
    base = yaml.safe_dump(minimal_design())
    annotations = {
        "nontext": "observations: {1: first, '1': second}",
        "cycle": "cycle: &cycle [*cycle]",
        "nonfinite": "values: [null, .inf]",
        "unsupported": "date: 2026-09-23",
        "merge": "base: &base {note: first}\n  copy: {<<: *base, note: second}",
    }
    text = base + "metadata:\n  " + annotations[case] + "\n" if case in annotations else ambiguous_design_text(suffix, case)
    design_path = tmp_path / f"invalid.{suffix}"
    design_path.write_text(text, encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with forbidden_phases(), patch.object(LinearEvaluationPipeline, "evaluate", side_effect=AssertionError("acoustics")) as evaluate:
        code, out, err = call_cli((inputs[0], design_path), "--output-dir", tmp_path / "new/output", *(["--dry-run"] if dry_run else []))
    assert code == 1 and not out and "error:" in err
    if case in {"top", "segment", "profile_params", "metadata"}:
        assert "duplicate" in err.lower()
    evaluate.assert_not_called()
    assert not (tmp_path / "new").exists()
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("suffix", ["yaml", "json"])
def test_r7_valid_annotations_exactly_preserved_in_both_exports(inputs, tmp_path, suffix):
    raw = minimal_design()
    metadata = {"observations": {"1": "first", "01": "second"}, "nested": [None, [True, 3, 1.25, {"note": "intact"}]]}
    raw["metadata"] = metadata
    path = tmp_path / f"annotated.{suffix}"
    text = yaml.safe_dump(raw) if suffix == "yaml" else json.dumps(raw)
    path.write_text(text, encoding="utf-8")
    result = run_fixed_design(inputs[0], path, output_dir_override=tmp_path / "annotations")
    for key in ("result_json", "result_yaml"):
        exported = yaml.safe_load(Path(result["exports"][key]).read_text(encoding="utf-8"))
        for design_key in ("design", "analysis_design"):
            annotations = exported["result"][design_key]["metadata"]
            for name, value in metadata.items():
                assert annotations[name] == value
    assert path.read_text(encoding="utf-8") == text


def _fixture_git(root, *args):
    proc = subprocess.run(["git", "-c", "core.longpaths=true", "-C", str(root), *args], capture_output=True, text=True, check=True, timeout=15)
    return proc.stdout.strip()


def _copy_fixture_package(root):
    for source in (REPO_ROOT / "didgeridoo_optimizer").rglob("*.py"):
        if source.name.startswith("test_"):
            continue  # Retain tests.validation_cases used by the runtime imports.
        destination = root / source.relative_to(REPO_ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _init_fixture_repository(root):
    root.mkdir()
    _fixture_git(root, "init")
    (root / "README").write_text("Local test repository only\n", encoding="utf-8")
    _fixture_git(root, "add", "README")


def _fixture_commit(root):
    _fixture_git(root, "-c", "user.name=R7 Test", "-c", "user.email=r7-test@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m", "local provenance fixture")
    return _fixture_git(root, "rev-parse", "HEAD")


def _source_from_copied_package(root):
    proc = subprocess.run(
        [sys.executable, "-c", "import json; from didgeridoo_optimizer.pipeline.fixed_design import _software_source; print(json.dumps(_software_source()))"],
        cwd=root, capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(proc.stdout)


def test_r7_software_clean_dirty_and_real_worktree(tmp_path):
    root = tmp_path / "tracked_repo"
    _init_fixture_repository(root)
    _copy_fixture_package(root)
    _fixture_git(root, "add", "didgeridoo_optimizer")
    head = _fixture_commit(root)
    clean = _source_from_copied_package(root)
    assert clean["sha"] == head and clean["working_tree_dirty"] is False
    source = root / "didgeridoo_optimizer/pipeline/design_input.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# uncommitted fixture change\n", encoding="utf-8")
    dirty = _source_from_copied_package(root)
    assert dirty["sha"] == head and dirty["working_tree_dirty"] is True
    worktree = tmp_path / "fixture_worktree"
    _fixture_git(root, "worktree", "add", "--detach", str(worktree), head)
    assert (worktree / ".git").is_file()
    provenance = _source_from_copied_package(worktree)
    assert provenance["sha"] == head and provenance["working_tree_dirty"] is False


@pytest.mark.parametrize("placement", ["nested_ignored", "root_untracked", "root_ignored", "partly_tracked"])
def test_r7_software_rejects_foreign_parent_or_untracked_source(tmp_path, placement):
    root = tmp_path / "foreign_repo"
    _init_fixture_repository(root)
    package_root = root / "copied-package" if placement == "nested_ignored" else root
    _copy_fixture_package(package_root)
    if placement in {"nested_ignored", "root_ignored"}:
        (root / ".gitignore").write_text("copied-package/\ndidgeridoo_optimizer/\n", encoding="utf-8")
        _fixture_git(root, "add", ".gitignore")
    if placement == "partly_tracked":
        _fixture_git(root, "add", "didgeridoo_optimizer/pipeline/fixed_design.py")
    head = _fixture_commit(root)
    provenance = _source_from_copied_package(package_root)
    assert provenance["sha"] is None, f"Unrelated HEAD {head} was attributed to {placement}: {provenance}"
    assert provenance["working_tree_dirty"] is None
    assert "could not be established" in provenance["origin"]


@pytest.mark.parametrize("error", [OSError("missing git"), subprocess.CalledProcessError(128, ["git"]), subprocess.TimeoutExpired(["git"], 5)])
def test_r7_software_git_failure_is_nonfatal(inputs, error):
    with patch("didgeridoo_optimizer.pipeline.fixed_design.subprocess.run", side_effect=error):
        response = run_fixed_design(*inputs, dry_run=True)
    assert response["ok"] and response["provenance"]["software"]["sha"] is None


def test_r7_unavailable_git_does_not_prevent_real_evaluation(inputs, tmp_path):
    with patch("didgeridoo_optimizer.pipeline.fixed_design.subprocess.run", side_effect=OSError("git unavailable")):
        response = run_fixed_design(*inputs, output_dir_override=tmp_path / "without_git")
    assert response["ok"]
    payload = json.loads(Path(response["exports"]["result_json"]).read_text(encoding="utf-8"))
    assert len(payload["result"]["zin"]) == 128
    assert payload["provenance"]["software"]["sha"] is None
