from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from didgeridoo_optimizer.materials import MaterialDatabase
from didgeridoo_optimizer.pipeline.design_input import load_design, validate_analysis, validate_design
from didgeridoo_optimizer.tests import test_fixed_design_internal as internal_fixture

REPO_ROOT = Path(__file__).resolve().parents[2]


def minimal_design():
    return internal_fixture.FixedDesignInternalTests()._minimal_design_mapping()


def minimal_config():
    config = internal_fixture.FixedDesignInternalTests()._minimal_linear_config()
    config["materials"].update({
        "database_file": str(REPO_ROOT / "project_specs/materials_base_v1.yaml"),
        "variant_rules_file": str(REPO_ROOT / "project_specs/wood_variant_rules_v1.yaml"),
    })
    return config


@pytest.fixture(scope="module")
def materials():
    return MaterialDatabase.from_yaml(REPO_ROOT / "project_specs/materials_base_v1.yaml")


def test_yaml_json_equivalent_positions_and_annotations(tmp_path, materials):
    raw = minimal_design()
    raw.pop("id")
    raw["metadata"] = {"note": "physical specimen", "exit_radius_m": 500}
    raw["segments"][0].update(position_start_cm=8, position_end_cm=9)
    before = copy.deepcopy(raw)
    designs = []
    for suffix, text in (("yaml", yaml.safe_dump(raw)), ("json", json.dumps(raw))):
        path = tmp_path / f"design.{suffix}"
        path.write_text(text, encoding="utf-8")
        resolved, design = load_design(path, materials, minimal_config())
        assert resolved == path.resolve()
        assert path.read_text(encoding="utf-8") == text
        designs.append(design.as_dict())
    assert raw == before
    assert designs[0] == designs[1]
    assert designs[0]["id"] == "fixed_design"
    assert designs[0]["segments"][0]["position_start_cm"] == 0
    assert designs[0]["segments"][0]["position_end_cm"] == 100
    assert designs[0]["metadata"]["exit_radius_m"] == 500


@pytest.mark.parametrize("value", [None, True, "3", 0, -1, float("nan"), float("inf"), -float("inf"), 10**400])
@pytest.mark.parametrize("field", ["length_cm", "d_in_cm", "d_out_cm"])
def test_reject_invalid_dimensions(materials, field, value):
    raw = minimal_design()
    raw["segments"][0][field] = value
    with pytest.raises(ValueError, match=rf"segments\[0\].{field}"):
        validate_design(raw, materials, minimal_config())


@pytest.mark.parametrize("raw,match", [
    (None, "design: expected a mapping"), ([], "mapping"),
    ({"segments": []}, "non-empty list"), ({"segments": {}}, "non-empty list"),
    ({"segments": [None]}, r"segments\[0\]"),
    ({"segments": [{}]}, "missing required"),
    ({"segments": [], "analysis_design": {}}, "unknown fields"),
])
def test_reject_non_schema_documents(materials, raw, match):
    with pytest.raises(ValueError, match=match):
        validate_design(raw, materials, minimal_config())


@pytest.mark.parametrize("patch,match", [
    ({"kind": "branch"}, "not implemented"), ({"kind": "helmholtz_neck"}, "not implemented"),
    ({"kind": []}, "unsupported kind"), ({"kind": "con"}, "unsupported kind"),
    ({"material_id": "absent_material"}, "material_id"), ({"material_id": True}, "material_id"),
    ({"lenght_cm": 12}, "unknown fields"), ({"profile_params": None}, "profile_params"),
    ({"profile_params": {"flarre_parameter": 3}}, "unknown fields"),
    ({"kind": "flare_exponential", "profile_params": {"flare_parameter": float("nan")}}, "flare_parameter"),
    ({"kind": "flare_powerlaw", "profile_params": {"power": True}}, "power"),
    ({"kind": "mouthpiece", "profile_params": {"throat_diameter_cm": 0}}, "throat_diameter_cm"),
    ({"kind": "mouthpiece", "profile_params": {"throat_diameter_cm": -1}}, "throat_diameter_cm"),
    ({"position_start_cm": None}, "position_start_cm"),
])
def test_segment_errors_have_field_paths(materials, patch, match):
    raw = minimal_design()
    raw["segments"][0].update(patch)
    with pytest.raises(ValueError, match=match):
        validate_design(raw, materials, minimal_config())


@pytest.mark.parametrize("patch,match", [
    ({"id": True}, "design.id"), ({"id": ""}, "design.id"),
    ({"metadata": None}, "metadata"), ({"metadata": {"is_discretized": True}}, "physical design"),
    ({"metadata": {"value": float("nan")}}, "finite, JSON-compatible"),
    ({"x": [1, 2]}, "unknown fields"),
])
def test_top_level_errors(materials, patch, match):
    raw = minimal_design()
    raw.update(patch)
    with pytest.raises(ValueError, match=match):
        validate_design(raw, materials, minimal_config())


def test_uses_existing_geometry_policy(materials):
    config = minimal_config()
    config["geometry_constraints"]["total_length_cm"]["max"] = 80
    with pytest.raises(ValueError, match="design.geometry: Total length"):
        validate_design(minimal_design(), materials, config)


@pytest.mark.parametrize("suffix,text", [
    ("yaml", "segments: ["), ("json", '{"segments": ['),
    ("yaml", "!!python/object/apply:os.system ['echo invalid']"),
    ("json", '{"segments": [], "x": NaN}'),
    ("txt", "{}"),
])
def test_syntax_and_safe_loading(tmp_path, materials, suffix, text):
    path = tmp_path / f"bad.{suffix}"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_design(path, materials, minimal_config())


def test_explicit_missing_design_no_homonym_fallback(tmp_path, monkeypatch, materials):
    config_folder = tmp_path / "config"
    config_folder.mkdir()
    (config_folder / "design.yaml").write_text(yaml.safe_dump(minimal_design()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_design("design.yaml", materials, minimal_config())


@pytest.mark.parametrize("section,key,value", [
    ("frequency_analysis", "f_min_hz", 0), ("frequency_analysis", "f_min_hz", True),
    ("frequency_analysis", "f_max_hz", 40), ("frequency_analysis", "f_max_hz", float("inf")),
    ("frequency_analysis", "n_points", True), ("frequency_analysis", "n_points", 2.0),
    ("frequency_analysis", "n_points", 1), ("frequency_analysis", "n_points", None),
    ("frequency_analysis", "discretization_max_segment_cm", 0),
    ("frequency_analysis", "discretization_max_segment_cm", float("nan")),
    ("environment", "air_density_kg_m3", -1), ("environment", "sound_speed_m_s", None),
    ("environment", "sound_speed_m_s", True), ("environment", "sound_speed_m_s", "343"),
])
def test_analysis_validation(section, key, value):
    config = minimal_config()
    config[section][key] = value
    with pytest.raises(ValueError, match=rf"config.{section}.{key}"):
        validate_analysis(config)


def test_analysis_defaults_and_explicit_values():
    assert validate_analysis({})["frequency_analysis"]["n_points"] == 4096
    effective = validate_analysis(minimal_config())
    assert effective["frequency_analysis"]["n_points"] == 128
    assert effective["environment"]["air_density_kg_m3"] == 1.204


def ambiguous_design_text(suffix, level="metadata"):
    """Raw text keeps duplicate/nontext keys intact until the DESIGN reader."""
    segment = '{"kind":"cylinder","length_cm":100,"d_in_cm":3,"d_out_cm":3,"material_id":"pvc_pressure"}'
    if suffix == "json":
        if level == "top":
            return '{"segments":[' + segment + '],"id":"first","id":"second"}'
        if level == "segment":
            return '{"segments":[' + segment.replace('"length_cm":100', '"length_cm":100,"length_cm":140') + ']}'
        if level == "profile_params":
            segment = segment.replace('"cylinder"', '"flare_exponential"').replace('}', ',"profile_params":{"flare_parameter":2,"flare_parameter":3}}')
            return '{"segments":[' + segment + ']}'
        return '{"segments":[' + segment + '],"metadata":{"nested":[{"note":"first","note":"second"}]}}'
    base = yaml.safe_dump(minimal_design(), sort_keys=False)
    if level == "top":
        return base + "id: second\n"
    if level == "segment":
        return base.replace("  length_cm: 100.0", "  length_cm: 100.0\n  length_cm: 140.0")
    if level == "profile_params":
        return base.replace("kind: cylinder", "kind: flare_exponential") + "  profile_params:\n    flare_parameter: 2\n    flare_parameter: 3\n"
    return base + "metadata:\n  nested:\n    - note: first\n      note: second\n"


@pytest.mark.parametrize("suffix", ["yaml", "json"])
@pytest.mark.parametrize("level,key", [("top", "id"), ("segment", "length_cm"), ("profile_params", "flare_parameter"), ("metadata", "note")])
def test_r7_explicit_duplicate_keys_rejected(tmp_path, materials, suffix, level, key):
    path = tmp_path / f"duplicate.{suffix}"
    text = ambiguous_design_text(suffix, level)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_design(path, materials, minimal_config())
    message = str(caught.value)
    assert str(path) in message and key in message and "duplicate" in message.lower()
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("metadata,field", [
    ({"observations": {1: "first", "1": "second"}}, "design.metadata.observations"),
    ({"nested": [{"inside": {False: "boolean key"}}]}, "design.metadata.nested[0].inside"),
    ({"nested": [None, float("nan")]}, "design.metadata.nested[1]"),
    ({"nested": [{"value": float("inf")}]}, "design.metadata.nested[0].value"),
    ({"nested": [set([1])]}, "design.metadata.nested[0]"),
    ({"nested": [b"bytes"]}, "design.metadata.nested[0]"),
    ({"nested": [(1, 2)]}, "design.metadata.nested[0]"),
])
def test_r7_recursive_annotation_errors(materials, metadata, field):
    raw = minimal_design()
    raw["metadata"] = metadata
    with pytest.raises(ValueError) as caught:
        validate_design(raw, materials, minimal_config())
    assert field in str(caught.value)


def test_r7_cycles_rejected_but_shared_aliases_preserved(tmp_path, materials):
    base = yaml.safe_dump(minimal_design())
    path = tmp_path / "aliases.yaml"
    path.write_text(base + "metadata:\n  shared: &shared [null, {note: intact}]\n  again: *shared\n", encoding="utf-8")
    _, design = load_design(path, materials, minimal_config())
    assert design.metadata["shared"] == design.metadata["again"] == [None, {"note": "intact"}]
    path.write_text(base + "metadata:\n  cycle: &cycle [*cycle]\n", encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_design(path, materials, minimal_config())
    assert "design.metadata.cycle[0]" in str(caught.value) and "cycl" in str(caught.value).lower()
    recursive = {}
    recursive["self"] = recursive
    raw = minimal_design()
    raw["metadata"] = {"mapping": recursive}
    with pytest.raises(ValueError) as caught:
        validate_design(raw, materials, minimal_config())
    assert "design.metadata.mapping.self" in str(caught.value) and "cycl" in str(caught.value).lower()


@pytest.mark.parametrize("annotation", [
    "  observations:\n    1: first\n    true: second\n",
    "  observations:\n    - {1: first, '1': second}\n",
    "  base: &base {note: first}\n  observations: {<<: *base, note: second}\n",
])
def test_r7_yaml_rejects_nontext_keys_before_collapse_and_merge_keys(tmp_path, materials, annotation):
    path = tmp_path / "ambiguous.yaml"
    path.write_text(yaml.safe_dump(minimal_design()) + "metadata:\n" + annotation, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_design(path, materials, minimal_config())
    assert str(path) in str(caught.value)
    assert "key" in str(caught.value).lower() or "merge" in str(caught.value).lower()


@pytest.mark.parametrize("key", ["!!str [a, b]", "!!str {a: b}"])
def test_r7_yaml_nonscalar_string_tagged_key_reports_field(tmp_path, materials, key):
    path = tmp_path / "invalid_key.yaml"
    path.write_text(yaml.safe_dump(minimal_design()) + f"metadata:\n  ? {key}\n  : annotation\n", encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_design(path, materials, minimal_config())
    assert str(path) in str(caught.value)
    assert "design.metadata" in str(caught.value) and "key" in str(caught.value).lower()


def test_r7_quoted_yaml_merge_spelling_is_an_ordinary_annotation(tmp_path, materials):
    path = tmp_path / "quoted_key.yaml"
    path.write_text(yaml.safe_dump(minimal_design()) + "metadata: {'<<': literal text}\n", encoding="utf-8")
    _, design = load_design(path, materials, minimal_config())
    assert design.metadata["<<"] == "literal text"
