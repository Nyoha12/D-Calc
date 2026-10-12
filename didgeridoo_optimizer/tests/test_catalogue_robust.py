"""Decision oracles and native semantic binding; no R55 acoustic recomputation."""
import copy
import itertools
import json
from pathlib import Path

import pytest

from didgeridoo_optimizer.optimization import catalogue_robust as contract
from didgeridoo_optimizer.reporting import catalogue_robust as report
from didgeridoo_optimizer.optimization.design_contract import read_request
from didgeridoo_optimizer.tests.test_adjustment_policy import (
    make_job as native_job, seal as seal_native,
)


def write(path, value):
    path.write_bytes(contract.encoded(value))
    return path


def make_job(tmp_path, *, preference=True, policy="fixed", assembly=False):
    native = native_job(tmp_path, policy, fixed=not assembly, one=True)
    raw = read_request(native)[0]
    raw["budgets"]["output_mib"] = 8
    write(native, raw)
    geometry = tmp_path / ("assembly.json" if assembly else "design.json")
    base = read_request(geometry)[0]
    entries = []
    lengths = [59.95, 60.0, 60.05] if not assembly else [0.4]
    for index, length in enumerate(lengths):
        physical = copy.deepcopy(base)
        if not assembly:
            physical["segments"][0]["length_cm"] = length
        name = "candidate_" + str(index) + ".json"
        write(tmp_path / name, physical)
        entries.append(dict(id="c" + str(index), geometry=name))
    field = "pieces.inner.length" if assembly else "segments.0.length_cm"
    job = dict(
        schema_version=contract.SCHEMA, adjustment_job=native.name,
        catalogue=entries,
        nominal_mask=[] if assembly else [
            dict(field=field, unit="cm", bounds=[50, 70])],
        budgets=dict(seconds=90, output_mib=100))
    if preference:
        job["preferences"] = [
            dict(field=field, unit="m", direction="min", priority=1)]
    return write(tmp_path / "catalogue.json", job)


def oracle_plan(ids=("a", "b"), *, policy="fixed", preferences=True):
    return dict(
        candidates=[dict(id=i, preflight="ready", reason=None,
                         preference_values=[str(n + 1)])
                    for n, i in enumerate(ids)],
        preferences=[dict(direction="min")] if preferences else [],
        adjustment_job={"policy": {"kind": policy}},
        common={"parsed": {"scenarios": [{"id": "s0"}, {"id": "s1"}]}})


def evidence(ident, statuses, *, adaptive=False, terminal=True, optional=True):
    return dict(
        id=ident, attempted=True, clean_exit=terminal,
        native=dict(
            semantic_verified=True, execution_complete=terminal,
            request_fully_covered=terminal and optional,
            sampled_policy_conforming=all(s == "satisfied" for s in statuses),
            counterexample_found=not adaptive and "violated" in statuses,
            status="oracle", counters={}))


def test_no_scenario_winner_mixture_exhaustively():
    values = ("satisfied", "violated", "unsupported")
    for matrix in itertools.product(values, repeat=4):
        rows = [evidence("a", matrix[:2]), evidence("b", matrix[2:])]
        result = contract.summarize(oracle_plan(), rows)
        expected = [i for i, row in zip(("a", "b"), (matrix[:2], matrix[2:]))
                    if all(s == "satisfied" for s in row)]
        assert result["admissible_candidates"] == expected
        reversed_result = contract.summarize(
            oracle_plan(("b", "a")), list(reversed(rows)))
        assert set(reversed_result["admissible_candidates"]) == set(expected)


def test_r55_prescribed_reference_is_not_a_measurement():
    # Prescribed decision fixture, not regenerated frequencies or an R55 replay.
    lengths_cm = [120.11, 120.16, 120.21]
    plan = oracle_plan(("short", "centre", "long"))
    rows = [
        evidence("short", ["satisfied", "violated"]),
        evidence("centre", ["satisfied", "satisfied"]),
        evidence("long", ["violated", "satisfied"]),
    ]
    result = contract.summarize(plan, rows)
    assert lengths_cm == [120.11, 120.16, 120.21]
    assert result["admissible_candidates"] == ["centre"]
    assert result["best_known"] == ["centre"]
    without_centre = oracle_plan(("short", "long"))
    result = contract.summarize(without_centre, [rows[0], rows[2]])
    assert result["admissible_candidates"] == []


def test_adaptive_unknown_competitor_and_partial_catalogue():
    plan = oracle_plan(policy="shared_offset")
    plan["candidates"][0]["preference_values"] = ["2"]
    plan["candidates"][1]["preference_values"] = ["1"]
    rows = [evidence("a", ["satisfied"] * 2),
            evidence("b", ["violated"] * 2, adaptive=True)]
    result = contract.summarize(plan, rows)
    assert result["catalogue_complete"] and result["execution_complete"]
    assert not result["candidates_decided"] and not result["selection_complete"]
    assert result["best_known"] == ["a"]
    rows[1] = dict(id="b", attempted=False, clean_exit=False, native={})
    result = contract.summarize(plan, rows)
    assert not result["catalogue_complete"]
    assert result["admissible_candidates"] == ["a"]


@pytest.mark.parametrize("terminal,optional", [(False, True), (True, False)])
def test_witness_survives_missing_closure_or_optional_observation(terminal, optional):
    plan = oracle_plan(("a",))
    result = contract.summarize(plan, [
        evidence("a", ["satisfied"] * 2, terminal=terminal, optional=optional)])
    assert result["admissible_candidates"] == ["a"]
    assert not result["request_fully_covered"]
    assert not result["selection_complete"]


def test_unsupported_hard_is_not_a_passive_peak():
    result = contract.summarize(oracle_plan(("a",)), [
        evidence("a", ["satisfied", "unsupported"])])
    assert not result["admissible_candidates"]
    assert result["unresolved_candidates"] == ["a"]


def test_no_preference_missing_preference_ties_and_priority():
    plan = oracle_plan()
    rows = [evidence(i, ["satisfied"] * 2) for i in ("a", "b")]
    plan["preferences"] = []
    result = contract.summarize(plan, rows)
    assert result["admissible_candidates"] == ["a", "b"]
    assert result["best_known"] == [] and not result["selection_complete"]
    plan["preferences"] = [dict(direction="min")]
    plan["candidates"][1]["preference_values"] = [None]
    result = contract.summarize(plan, rows)
    assert result["admissible_candidates"] == ["a", "b"]
    assert not result["selection_complete"]
    plan["candidates"][1]["preference_values"] = ["1"]
    assert contract.summarize(plan, rows)["best_known"] == ["a", "b"]
    plan["preferences"].append(dict(direction="max"))
    plan["candidates"][0]["preference_values"].append("2")
    plan["candidates"][1]["preference_values"].append("3")
    assert contract.summarize(plan, rows)["best_known"] == ["b"]


@pytest.mark.parametrize("attack", ["mask", "material", "unit", "position", "bounds"])
def test_input_refusals(tmp_path, attack):
    job = make_job(tmp_path)
    raw = read_request(job)[0]
    physical = read_request(tmp_path / "candidate_0.json")[0]
    if attack == "mask":
        physical["segments"][0]["d_in_cm"] = 3.1
    elif attack == "material":
        physical["segments"][0]["material_id"] = "another_material"
    elif attack == "unit":
        raw["preferences"][0]["unit"] = "cent"
    elif attack == "position":
        physical["segments"][0]["position_end_cm"] = 999
    else:
        raw["nominal_mask"][0]["bounds"] = [60, 61]
    write(job, raw)
    write(tmp_path / "candidate_0.json", physical)
    with pytest.raises(ValueError):
        contract.load_inputs(job)


@pytest.mark.parametrize("syntax", [
    '{"schema_version":1,"schema_version":2}', "x: &cycle [*cycle]", "x: .nan",
])
def test_strict_native_reader(tmp_path, syntax):
    path = tmp_path / ("bad.json" if syntax.startswith("{") else "bad.yaml")
    path.write_text(syntax)
    with pytest.raises(ValueError):
        contract.load_inputs(path)


def produce_fixture(tmp_path, job):
    """Native geometry execution; synthetic controller receipts are test fixtures."""
    from didgeridoo_optimizer.pipeline import adjustment_policy as native
    prepared, plan = contract.load_inputs(job)
    output = tmp_path / "bundle"
    output.mkdir()
    limit = plan["budgets"]["output_mib"] * contract.MIB
    report.put(output, "plan.json", plan, limit)
    for candidate in plan["candidates"]:
        assert candidate["preflight"] == "ready", candidate
        directory = output / candidate["id"]
        for name, value in contract.native_documents(
                prepared, plan, candidate, directory).items():
            report.put(output, candidate["id"] + "/" + name, value, limit)
        native_prepared, native_plan = native.load_inputs(directory / "policy.json")
        assert native.report.identity_plan(native_plan) == contract.expected_native_plan(
            prepared, plan, candidate, directory)
        destination = directory / "native"
        destination.mkdir()
        native.report.write_json(destination / "plan.json", native_plan)
        result = native.execute(native_prepared, native_plan, destination)
        seal_native(destination, result)
        report.put(output, "attempt_" + candidate["id"] + ".json",
                   dict(id=candidate["id"], collected=True, clean_exit=True,
                        exit_code=0, signals=[], reason=None), limit)
    result = report.export(output, prepared, plan)
    receipt = dict(ok=result["execution_complete"],
                   execution_complete=result["execution_complete"],
                   closure_confirmed=True,
                   attempted_candidates=[c["id"] for c in plan["candidates"]],
                   manifest_sha256=report.native.file_sha256(output / "manifest.json"))
    report.put(output, "receipt.json", receipt, limit)
    report.put(output, "completed.json", {
        "receipt_sha256": report.native.file_sha256(output / "receipt.json")}, limit)
    return output


def reseal_catalogue(output):
    # Adversarial relative hashes: not external authentication.
    write(output / "manifest.json", report.inventory(output, 200 * contract.MIB))
    receipt = report.read(output / "receipt.json")
    receipt["manifest_sha256"] = report.native.file_sha256(output / "manifest.json")
    write(output / "receipt.json", receipt)
    write(output / "completed.json", {
        "receipt_sha256": report.native.file_sha256(output / "receipt.json")})


def test_native_binding_selection_and_unconfirmed_terminal(tmp_path):
    job = make_job(tmp_path)
    output = produce_fixture(tmp_path, job)
    result = report.read_result(output, job)
    assert result["ok"] and result["admissible_candidates"] == ["c0", "c1", "c2"]
    assert result["best_known"] == ["c0"]
    (output / "completed.json").unlink()
    result = report.read_result(output, job)
    assert not result["ok"] and not result["selection_complete"]
    assert result["admissible_candidates"] == ["c0", "c1", "c2"]


@pytest.mark.parametrize("attack", ["selection", "receipt", "candidate", "target"])
def test_coherently_rehashed_catalogue_tampering(tmp_path, attack):
    job = make_job(tmp_path)
    output = produce_fixture(tmp_path, job)
    if attack == "selection":
        result = report.read(output / "result.json")
        result["best_known"] = ["c2"]
        write(output / "result.json", result)
    elif attack == "receipt":
        receipt = report.read(output / "receipt.json")
        receipt["execution_complete"] = False
        write(output / "receipt.json", receipt)
    elif attack == "candidate":
        physical = report.read(output / "c0/nominal.json")
        physical["segments"][0]["length_cm"] = 60.05
        write(output / "c0/nominal.json", physical)
    else:
        native_plan = report.read(output / "c0/native/plan.json")
        native_plan["audit_plan"]["native_request"]["criteria"][0]["target"]["value"] = 99
        write(output / "c0/native/plan.json", native_plan)
    reseal_catalogue(output)
    with pytest.raises(ValueError):
        report.read_result(output, job)


@pytest.mark.parametrize("policy", ["fixed", "per_configuration", "shared_offset"])
def test_assembly_native_plan_binding(tmp_path, policy):
    job = make_job(tmp_path, policy=policy, assembly=True)
    output = produce_fixture(tmp_path, job)
    assert report.read_result(output, job)["semantic_verified"]


def test_shared_offset_native_reader_rejects_mixed_commands(tmp_path):
    job = make_job(tmp_path, policy="shared_offset", assembly=True)
    output = produce_fixture(tmp_path, job)
    bundle = output / "c0/native"
    commands = report.read(bundle / "commands_nominal.json")
    commands[0]["q_si"] += 0.001
    write(bundle / "commands_nominal.json", commands)
    from didgeridoo_optimizer.tests.test_adjustment_policy import rewrite
    rewrite(bundle, report.read(bundle / "result.json"))
    reseal_catalogue(output)
    with pytest.raises(ValueError):
        report.read_result(output, job)


# Pilot review regressions; original proposed tests above remain unchanged.
@pytest.mark.parametrize("removed", [
    ("result.json",), ("comparison.csv",), ("summary.txt",), ("manifest.json",),
    ("result.json", "comparison.csv", "summary.txt"),
    ("comparison.csv", "summary.txt"),
])
def test_review_missing_delivery_never_becomes_complete(tmp_path, removed):
    job = make_job(tmp_path)
    output = produce_fixture(tmp_path, job)
    assert report.read_result(output, job)["ok"]
    for name in removed:
        (output / name).unlink()
    if "manifest.json" not in removed:
        reseal_catalogue(output)
    result = report.read_result(output, job)
    assert not result["ok"] and not result["closure_confirmed"]
    assert not result["execution_complete"] and not result["selection_complete"]
    assert not result["delivery_files_complete"]
    assert set(result["missing_delivery_files"]) == set(removed)
    assert result["admissible_candidates"] == ["c0", "c1", "c2"]


def test_review_proved_nominal_exclusion_does_not_invent_unknown_rival():
    plan = oracle_plan()
    plan["candidates"][0]["preflight"] = "nominal_geometry_violated"
    rows = [dict(id="a", attempted=False, clean_exit=False, native={}),
            evidence("b", ["satisfied"] * 2)]
    result = contract.summarize(plan, rows)
    assert result["admissible_candidates"] == ["b"]
    assert result["candidates_decided"] and result["execution_complete"]
    assert not result["request_fully_covered"]
    assert result["comparison_fully_covered"] and result["selection_complete"]


def test_review_unknown_nominal_still_blocks_complete_selection():
    plan = oracle_plan()
    plan["candidates"][0]["preflight"] = "input_unresolved"
    rows = [dict(id="a", attempted=False, clean_exit=False, native={}),
            evidence("b", ["satisfied"] * 2)]
    result = contract.summarize(plan, rows)
    assert result["admissible_candidates"] == ["b"]
    assert result["unresolved_candidates"] == ["a"]
    assert not result["comparison_fully_covered"] and not result["selection_complete"]


def test_review_fixed_example_preserves_fabricated_envelope():
    request = read_request(contract.ROOT / "project_specs/examples/catalogue_robust/fixed_request.json")[0]
    criteria = {row["id"]: row for row in request["criteria"]}
    envelope = criteria["envelope"]
    assert envelope["role"] == "hard" and envelope["level"] == "geometry"
    assert envelope["expression"] == {"total_length": True}
    assert envelope["bounds"] == [{"value": 1.19, "unit": "m"}, {"value": 1.21, "unit": "m"}]
    assert envelope["tolerance"] == {"value": 1e-6, "unit": "m"}
    assert criteria["f1"]["tolerance"] == {"value": 1, "unit": "cent"}
