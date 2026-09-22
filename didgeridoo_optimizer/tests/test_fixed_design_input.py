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
