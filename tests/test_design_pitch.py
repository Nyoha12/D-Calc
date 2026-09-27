from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
import yaml

from didgeridoo_optimizer.pipeline import design_pitch as dp
from didgeridoo_optimizer.pipeline.design_input import load_design
from didgeridoo_optimizer.reporting import design_pitch as report
from didgeridoo_optimizer.reporting import forced_response_comparison as comparison
from tools import design_pitch_compare as cli

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT/'project_specs/examples/design_pitch'


@pytest.fixture
def inputs(tmp_path):
    config = yaml.safe_load((EXAMPLES/'config.yaml').read_text())
    for key, name in [('database_file', 'materials_base_v1.yaml'), ('variant_rules_file', 'wood_variant_rules_v1.yaml')]:
        config['materials'][key] = str(ROOT/'project_specs'/name)
    config_path = tmp_path/'config.yaml'
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    paths = []
    for name in ('cylinder.json', 'cone.yaml', 'exponential.yaml'):
        path = tmp_path/name
        path.write_bytes((EXAMPLES/name).read_bytes())
        paths.append(path)
    return config_path, paths, tmp_path/'result'


def plan(inputs, **options):
    config, paths, output = inputs
    return dp.preflight(config, paths, output, pressure_peak_pa=1., **options)


def test_real_multifile_preflight_no_calculation_or_output(inputs, monkeypatch):
    before = {p: p.read_bytes() for p in inputs[0].parent.rglob('*') if p.is_file()}
    monkeypatch.setattr(dp, 'input_impedance', lambda *a, **k: pytest.fail('propagation'))
    monkeypatch.setattr(dp.fr, 'loaded_transfer', lambda *a, **k: pytest.fail('forced propagation'))
    p, contexts = plan(inputs)
    assert len(p['mapping']) == 3
    assert p['mapping'][0]['design_id'] == 'PVC / référence Ø30'
    assert p['mapping'][0]['tuned_design'] == 'tuned_design_001.json'
    assert contexts[0]['materials_used']['pvc_pressure']['acoustic_model']['beta_status']
    assert not inputs[2].exists()
    assert {p: p.read_bytes() for p in inputs[0].parent.rglob('*') if p.is_file()} == before
    assert 'io_test' not in json.dumps(p)


def test_scale_keeps_metadata_materials_diameters_kinds_and_profiles(inputs):
    _, contexts = plan(inputs)
    for context in contexts:
        context['design'].metadata['longitudinal_factor'] = {'user_annotation': 'preserve'}
        original = copy.deepcopy(context['design'].as_dict())
        out = dp.scaled(context, 1.25).as_dict()
        assert out['id'] == original['id']
        assert {k: v for k, v in out['metadata'].items() if k != 'total_length_cm'} == {k: v for k, v in original['metadata'].items() if k != 'total_length_cm'}
        assert out['metadata']['total_length_cm'] == sum(seg['length_cm'] for seg in out['segments'])
        for a, b in zip(original['segments'], out['segments'], strict=True):
            assert b['length_cm'] == a['length_cm']*1.25
            for key in ('kind', 'material_id', 'd_in_cm', 'd_out_cm', 'profile_params'):
                assert b[key] == a[key]
        assert context['design'].as_dict() == original


@pytest.mark.parametrize('options', [
    {'scale_min': 0}, {'scale_max': -1}, {'scale_min': .1}, {'scale_max': 5},
    {'scale_min': 1, 'scale_max': 1}, {'scale_min': float('nan')}, {'scale_max': float('inf')},
    {'target_hz': 49}, {'target_hz': 91}, {'target_hz': True},
    {'h_cm': 0}, {'h_cm': 3}, {'h_cm': .00001},
    {'max_iterations': 0}, {'max_iterations': 65}, {'max_iterations': True},
    {'loss_model': 'zk'}, {'air_reference': 'ck_dry20'},
])
def test_parameter_limits_before_calculation(inputs, options, monkeypatch):
    monkeypatch.setattr(dp, 'input_impedance', lambda *a, **k: pytest.fail('propagation'))
    with pytest.raises(ValueError):
        plan(inputs, **options)
    assert not inputs[2].exists()


@pytest.mark.parametrize('options', [
    {}, {'pressure_peak_pa': 0}, {'flow_peak_m3_s': -1}, {'pressure_peak_pa': float('nan')},
    {'pressure_peak_pa': True}, {'pressure_peak_pa': 1, 'flow_peak_m3_s': 1},
    {'thevenin_pressure_peak_pa': 1}, {'thevenin_pressure_peak_pa': 1, 'source_resistance_pa_s_m3': -1},
    {'thevenin_pressure_peak_pa': 1, 'source_resistance_pa_s_m3': float('inf')},
    {'pressure_peak_pa': 1, 'source_resistance_pa_s_m3': 0},
])
def test_invalid_sources(options):
    with pytest.raises(ValueError):
        dp.source_options(options)


def test_rs_zero_allowed_and_absolute():
    assert dp.source_options(dict(thevenin_pressure_peak_pa=1, source_resistance_pa_s_m3=0)) == dict(
        kind='thevenin_pressure', amplitude=1., resistance_pa_s_m3=0.)


@pytest.mark.parametrize('field', ['total', 'mouthpiece', 'bell'])
def test_endpoint_constraints_never_relaxed(inputs, field):
    config_path, paths, output = inputs
    config = yaml.safe_load(config_path.read_text())
    if field == 'total':
        config['geometry_constraints']['total_length_cm']['min'] = 110
    elif field == 'mouthpiece':
        raw = json.loads(paths[0].read_text())
        raw['segments'][0]['kind'] = 'mouthpiece'
        config['geometry_constraints']['body_segments']['min_count'] = 0
        config['mouthpiece'] = {'geometry_constraints': {'total_length_cm': {'min': 110, 'max': 150}}}
        paths[0].write_text(json.dumps(raw))
    else:
        config['bell']['geometry_constraints']['length_cm']['max'] = 25
    config_path.write_text(yaml.safe_dump(config))
    before = config_path.read_bytes()
    with pytest.raises(ValueError, match='scale=.*bounds/geometry/budget incompatible'):
        plan(inputs)
    assert config_path.read_bytes() == before and not output.exists()


@pytest.mark.parametrize('suffix', ['json', 'yaml'])
def test_duplicate_design_keys_rejected(inputs, suffix):
    path = inputs[0].parent/f'duplicate.{suffix}'
    path.write_text('{"id":"a", "id":"b", "segments": []}' if suffix == 'json' else 'id: a\nid: b\nsegments: []\n')
    with pytest.raises(ValueError, match='duplicate'):
        dp.preflight(inputs[0], [path], inputs[2], pressure_peak_pa=1)


def test_duplicate_config_keys_rejected(inputs):
    inputs[0].write_text(inputs[0].read_text()+'\nenvironment: {}\n')
    with pytest.raises(ValueError, match='duplicate'):
        plan(inputs)


@pytest.mark.parametrize('alias', ['literal', 'symlink', 'hardlink'])
def test_repeated_design_paths_rejected(inputs, alias):
    first = inputs[1][0]
    second = first
    if alias != 'literal':
        second = first.parent/'alias.json'
        if alias == 'symlink':
            second.symlink_to(first)
        else:
            second.hardlink_to(first)
    with pytest.raises(ValueError, match='Repeated DESIGN path'):
        dp.preflight(inputs[0], [first, second], inputs[2], pressure_peak_pa=1)


def test_duplicate_ids_rejected_but_same_basename_distinct_ids_allowed(inputs):
    first = inputs[1][0]
    other = first.parent/'other'
    other.mkdir()
    second = other/first.name
    second.write_bytes(first.read_bytes())
    with pytest.raises(ValueError, match='Repeated DESIGN id'):
        dp.preflight(inputs[0], [first, second], inputs[2], pressure_peak_pa=1)
    raw = json.loads(second.read_text())
    raw['id'] = '../../escape / été'
    second.write_text(json.dumps(raw))
    result, _ = dp.preflight(inputs[0], [first, second], inputs[2], pressure_peak_pa=1)
    assert result['mapping'][1]['tuned_design'] == 'tuned_design_002.json'
    assert not inputs[2].exists()


def test_missing_design_never_uses_basename_fallback(inputs):
    with pytest.raises(FileNotFoundError):
        dp.preflight(inputs[0], [inputs[0].parent/'missing'/'cylinder.json'], inputs[2], pressure_peak_pa=1)


def test_ambiguous_material_resolution_rejected(inputs, monkeypatch):
    config = yaml.safe_load(inputs[0].read_text())
    config['materials']['database_file'] = 'materials_base_v1.yaml'
    inputs[0].write_text(yaml.safe_dump(config))
    monkeypatch.chdir(ROOT/'project_specs')
    with pytest.raises(ValueError, match='ambiguous historical path resolution'):
        plan(inputs)


@pytest.mark.parametrize('kind', ['directory', 'file', 'dangling', 'parent_file'])
def test_destination_refusal(inputs, kind):
    output = inputs[2]
    if kind == 'directory':
        output.mkdir()
    elif kind == 'file':
        output.write_text('keep')
    elif kind == 'dangling':
        output.symlink_to(output.parent/'missing-target')
    else:
        output.parent.joinpath('parent-file').write_text('keep')
        output = output.parent/'parent-file'/'child'
    with pytest.raises((ValueError, OSError)):
        dp.preflight(inputs[0], inputs[1], output, pressure_peak_pa=1)


def test_portable_dry_run_and_unsupported_backend_before_writes(inputs, monkeypatch):
    monkeypatch.setattr(dp, 'resource', None)
    result = dp.run(inputs[0], inputs[1], inputs[2], pressure_peak_pa=1, dry_run=True)
    assert result['ok'] and not inputs[2].exists()
    with pytest.raises(ValueError, match='POSIX'):
        dp.run(inputs[0], inputs[1], inputs[2], pressure_peak_pa=1)
    assert not inputs[2].exists()


def test_evaluator_uses_exact_air_models_outlet_and_budget(inputs, monkeypatch):
    p, contexts = plan(inputs)
    context = contexts[1]
    calls = []
    def impedance(freq, mesh, materials, air, **kwargs):
        calls.append((freq, mesh, materials, air, kwargs))
        return np.ones(len(freq), dtype=complex)
    monkeypatch.setattr(dp, 'input_impedance', impedance)
    evaluate, mesh = dp.evaluator(context, context['design'], .5, p['options'])
    evaluate(np.array([60., 70.]))
    _, actual_mesh, materials, air, kwargs = calls[0]
    assert actual_mesh is mesh and materials is context['material_db']
    assert air.rho == 1.204 and air.c == 343 and air.humidity_percent == 50
    assert kwargs['exit_radius_m'] == .02
    assert kwargs['loss_model'].name == 'legacy_beta'
    assert kwargs['radiation_model'].describe()['name'] == 'legacy'
    with pytest.raises(ValueError, match='Budget'):
        evaluate(np.arange(1., 20002.))
    assert len(calls) == 1


@pytest.mark.parametrize('source', [dict(pressure_peak_pa=1), dict(flow_peak_m3_s=1e-6),
    dict(thevenin_pressure_peak_pa=1, source_resistance_pa_s_m3=0),
    dict(thevenin_pressure_peak_pa=1, source_resistance_pa_s_m3=500000)])
def test_one_transfer_one_source_compatible_bundle(inputs, monkeypatch, source):
    p, contexts = dp.preflight(inputs[0], inputs[1][:1], inputs[2], **source)
    real = dp.fr.loaded_transfer
    calls = []
    def transfer(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(dp.fr, 'loaded_transfer', transfer)
    payload = dp.forced_bundle(contexts[0], contexts[0]['design'], p['options'], {})
    assert len(calls) == 1
    comparison.validate_export(payload)
    assert len(payload['cases'][0]['models']) == 1
    assert payload['effective_parameters']['n_points'] == 11
    assert payload['schema'] == ('dcalc.forced_response.v2' if 'thevenin_pressure_peak_pa' in source else 'dcalc.forced_response.v1')
    if 'thevenin_pressure_peak_pa' in source:
        assert all(z['real'] == source['source_resistance_pa_s_m3'] for z in payload['cases'][0]['models'][0]['source']['impedance']['value'])


def test_zk_explicit_air_keeps_material_status(inputs):
    p, contexts = plan(inputs, loss_model='zk', air_reference='ck_dry25', radiation_model='silva_flanged')
    _, loss, _, effective = dp.models(contexts[0], p['options'])
    assert loss.name == 'zwikker_kosten_circular'
    assert effective['air']['temperature_c'] == 25
    assert effective['original_air']['temperature_c'] == 20
    assert 'explicit' in effective['air_substitution']
    assert contexts[0]['materials_used']['pvc_pressure'] == contexts[0]['material_db'].get('pvc_pressure').as_dict()


def test_no_mode_and_ambiguous_first_mode():
    with pytest.raises(dp.StudyError, match='No first'):
        dp.first_peak(lambda f: np.ones(len(f), dtype=complex))
    def ambiguous(f):
        if len(f) == len(dp.SURVEY):
            return 1 + 1/(1+(f-70)**2)
        return 1 + np.exp(-((f-69.7)/.08)**2) + np.exp(-((f-70.3)/.08)**2)
    with pytest.raises(dp.StudyError, match='ambiguous'):
        dp.first_peak(ambiguous)


def test_first_peak_is_not_largest():
    result = dp.first_peak(lambda f: 1+1/(1+((f-70)/.5)**2)+10/(1+((f-210)/.5)**2))
    assert abs(result['frequency_hz']-70) < .002


def _fake_profile(monkeypatch, context, *, fine_error=0, measure_error=None):
    def inspect(ctx, design, h, options):
        f = 70+fine_error if h == .25 else 70
        modes = [dict(mode_ordinal=i, frequency_max_abs_hz=f*i, status='resolved', q_half_power=None,
                      q_unavailable_reason='Half-power crossings not bracketed') for i in range(1, 4)]
        return dict(verified=True, reasons=[], modes=modes, first_peak=dict(frequency_hz=f),
                    physical_sha256=report.fingerprint(design.as_dict()), h_cm=h)
    monkeypatch.setattr(dp, 'inspect_modes', inspect)
    monkeypatch.setattr(dp, 'forced_bundle', lambda *a: {'cases': [{'models': [{'numerically_complete': True}]}]})
    monkeypatch.setattr(dp.export_fr, 'export_bundle', lambda *a: {})
    monkeypatch.setattr(dp, 'evaluator', lambda c, d, h, o: (d.total_length_cm, d))
    def peak(length):
        if measure_error:
            raise measure_error
        return dict(frequency_hz=8400/length)
    monkeypatch.setattr(dp, 'first_peak', peak)


@pytest.mark.parametrize('fine_error', [0, .01])
def test_verification_no_retune_and_unverified_target_keeps_design(inputs, monkeypatch, fine_error):
    p, contexts = plan(inputs)
    context = contexts[0]
    _fake_profile(monkeypatch, context, fine_error=fine_error)
    inputs[2].mkdir()
    result = dp.profile(context, p['options'], inputs[2], 1, {})
    assert result['ok'] is (fine_error == 0)
    validation = json.loads((inputs[2]/'validation_001.json').read_text())
    assert validation['fixed_design_no_retune']
    assert validation['levels'][0]['physical_sha256'] == validation['levels'][1]['physical_sha256']
    assert (inputs[2]/'tuned_design_001.json').exists()
    if fine_error:
        assert any('Target not verified' in reason for reason in result['reasons'])
    assert validation['levels'][0]['modes'][0]['q_half_power'] is None


@pytest.mark.parametrize('error', [dp.StudyError('Missing or ambiguous first local maximum'), RuntimeError('injected'), MemoryError('quota')])
def test_exceptions_preserve_original_and_trace(inputs, monkeypatch, error):
    p, contexts = plan(inputs)
    _fake_profile(monkeypatch, contexts[0], measure_error=error)
    inputs[2].mkdir()
    result = dp.profile(contexts[0], p['options'], inputs[2], 1, {})
    assert not result['ok'] and result['stage'] == 'tuning'
    assert (inputs[2]/'original_001.json').exists()
    assert 'failure' in (inputs[2]/'trace_001.jsonl').read_text()
    assert not (inputs[2]/'tuned_design_001.json').exists()


def test_bracket_and_iteration_failures_keep_solver_history():
    with pytest.raises(dp.StudyError) as exc:
        dp.solve_factor(lambda factor: 100, 70, (.8, 1.4))
    assert len(exc.value.history) == 2
    with pytest.raises(dp.StudyError, match='Maximum scalar') as exc:
        dp.solve_factor(lambda factor: 50+factor**3*20, 70, (.8, 1.4), max_iterations=1)
    assert len(exc.value.history) == 3


def test_each_solver_proposal_revalidated(inputs, monkeypatch):
    p, contexts = plan(inputs)
    context = contexts[0]
    _fake_profile(monkeypatch, context)
    real = dp.scaled
    seen = []
    def check(ctx, factor):
        seen.append(factor)
        if factor not in (.8, 1.4):
            raise ValueError('injected interior geometry violation')
        return real(ctx, factor)
    monkeypatch.setattr(dp, 'scaled', check)
    inputs[2].mkdir()
    result = dp.profile(context, p['options'], inputs[2], 1, {})
    assert not result['ok'] and 'interior geometry violation' in result['error']
    assert len(seen) >= 3


def test_timeout_captures_child_output_and_nonzero(monkeypatch):
    def timeout(*a, **kw):
        assert kw['timeout'] <= 180
        assert kw['env']['OPENBLAS_NUM_THREADS'] == '1'
        raise subprocess.TimeoutExpired(a[0], kw['timeout'], output=b'partial', stderr=b'evidence')
    monkeypatch.setattr(dp.subprocess, 'run', timeout)
    result = dp.bounded_task({}, 999)
    assert result['timeout'] and not result['ok'] and result['stdout'] == 'partial' and result['stderr'] == 'evidence'


@pytest.mark.parametrize('returned', [{'ok': True, 'verified': True, 'exit_code': 0},
    {'ok': False, 'timeout': True, 'error': 'deadline reached', 'exit_code': None}])
def test_no_success_without_acquired_verified_artifacts(inputs, monkeypatch, returned):
    monkeypatch.setattr(dp, 'bounded_task', lambda *a: dict(returned))
    result = dp.run(inputs[0], inputs[1][:1], inputs[2], pressure_peak_pa=1)
    assert not result['ok'] and result['status'] == 'partial'
    summary = json.loads((inputs[2]/'summary.json').read_text())
    assert summary['profiles'][0]['status'] == 'partial'
    assert (inputs[2]/'report.md').exists()


def test_cli_single_json_and_structured_error(capsys):
    assert cli.main(['--config', 'not-there', '--design', 'not-there', '--pressure-peak-pa', '0', '--output-dir', 'unused']) == 1
    out = capsys.readouterr()
    assert json.loads(out.out)['error']['type'] == 'ValueError'
    assert len(out.out.splitlines()) == 1


def test_real_legacy_complete_reloads_inputs_and_exports(inputs):
    config, paths, output = inputs
    files = [config, *paths, ROOT/'project_specs/materials_base_v1.yaml', ROOT/'project_specs/wood_variant_rules_v1.yaml']
    before = {p: p.read_bytes() for p in files}
    result = dp.run(config, paths[:1], output, pressure_peak_pa=1)
    assert result['ok'], (output/'summary.json').read_text()
    summary = json.loads((output/'summary.json').read_text())
    assert summary['profiles'][0]['status'] == 'verified'
    assert abs(summary['profiles'][0]['error_hz']) <= .002
    assert summary['comparisons'][0]['status'] == 'complete'
    _, context = dp.preflight(config, paths[:1], output.parent/'unused', pressure_peak_pa=1)
    _, tuned = load_design(output/'tuned_design_001.json', context[0]['material_db'], context[0]['config'])
    for key, value in json.loads(paths[0].read_text())['metadata'].items():
        assert tuned.metadata[key] == value
    assert tuned.metadata['total_length_cm'] == tuned.total_length_cm
    for side in ('original', 'tuned'):
        loaded = comparison.load_export(output/f'response_{side}_001/forced_response.json')
        assert loaded['payload']['schema'] == 'dcalc.forced_response.v1'
    assert {p: p.read_bytes() for p in files} == before


def test_mixed_material_segments_preserved(inputs):
    raw = yaml.safe_load(inputs[1][1].read_text())
    raw['segments'][1]['material_id'] = 'plywood_varnished'
    inputs[1][1].write_text(yaml.safe_dump(raw))
    _, contexts = plan(inputs)
    design = dp.scaled(contexts[1], 1.1)
    assert design.material_ids == ['pvc_pressure', 'plywood_varnished']
    assert set(contexts[1]['materials_used']) == {'pvc_pressure', 'plywood_varnished'}
    for name, record in contexts[1]['materials_used'].items():
        assert record == contexts[1]['material_db'].get(name).as_dict()


@pytest.mark.parametrize('number', [0, 5])
def test_profile_count_bounded(inputs, number):
    with pytest.raises(ValueError, match='1..4'):
        dp.preflight(inputs[0], inputs[1][:1]*number, inputs[2], pressure_peak_pa=1)


def test_segment_and_config_frequency_budgets(inputs):
    raw = json.loads(inputs[1][0].read_text())
    raw['segments'] *= 129
    inputs[1][0].write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='physical segment count'):
        plan(inputs)
    inputs[1][0].write_bytes((EXAMPLES/'cylinder.json').read_bytes())
    config = yaml.safe_load(inputs[0].read_text())
    config['frequency_analysis']['n_points'] = 20001
    inputs[0].write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='frequency budget'):
        plan(inputs)


def test_interrupt_retains_partial_and_does_not_launch_next(inputs, monkeypatch):
    calls = []
    def interrupt(task, timeout):
        calls.append(task)
        output = Path(task['output'])
        # Simulate a child killed during its next checkpoint write.
        (output/'original_001.json').write_text('{"modes": [')
        raise dp.RunInterrupted('test parent termination')
    monkeypatch.setattr(dp, 'bounded_task', interrupt)
    result = dp.run(inputs[0], inputs[1], inputs[2], pressure_peak_pa=1)
    assert not result['ok'] and len(calls) == 1
    summary = json.loads((inputs[2]/'summary.json').read_text())
    assert len(summary['profiles']) == 3 and summary['artifact_errors']
    assert (inputs[2]/'original_001.json').read_text() == '{"modes": ['
    assert 'not launched' in summary['jobs'][1]['error']


def test_total_budget_exhausted_does_not_launch(inputs, monkeypatch):
    monkeypatch.setattr(dp, 'TOTAL_SECONDS', 0)
    monkeypatch.setattr(dp, 'bounded_task', lambda *a: pytest.fail('budget exhausted'))
    result = dp.run(inputs[0], inputs[1], inputs[2], pressure_peak_pa=1)
    assert not result['ok']
    assert len(json.loads((inputs[2]/'summary.json').read_text())['jobs']) == 3


def test_inputs_changed_since_preflight_worker_refuses(inputs, monkeypatch, capsys):
    import io
    p, contexts = plan(inputs)
    files = contexts[0]['provenance']['files']
    changed = json.loads(inputs[1][0].read_text())
    changed['metadata']['changed'] = True
    inputs[1][0].write_text(json.dumps(changed))
    task = dict(config=str(inputs[0]), design=str(inputs[1][0]), input_files=files)
    monkeypatch.setattr(dp.sys, 'stdin', io.StringIO(json.dumps(task)))
    monkeypatch.setattr(dp, 'profile', lambda *a: pytest.fail('changed input propagated'))
    assert dp.worker() == 1
    assert 'Inputs changed' in json.loads(capsys.readouterr().out)['error']


def test_material_missing_and_nonfinite_annotations_refused(inputs):
    raw = json.loads(inputs[1][0].read_text())
    raw['segments'][0]['material_id'] = 'nonexistent_material'
    inputs[1][0].write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='material_id'):
        plan(inputs)
    inputs[1][0].write_text((EXAMPLES/'cylinder.json').read_text().replace('null', 'NaN'))
    with pytest.raises(ValueError, match='JSON constant'):
        plan(inputs)


def test_config_json_is_strict_even_for_uninterpreted_fields(inputs):
    config = yaml.safe_load(inputs[0].read_text())
    config['project'] = {'name': float('nan')}
    path = inputs[0].with_suffix('.json')
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='JSON constant'):
        dp.preflight(path, inputs[1], inputs[2], pressure_peak_pa=1)


def test_original_and_tuned_grid_is_exactly_common(inputs):
    p, _ = plan(inputs)
    assert p['common_frequency_hz'] == [65, 69, 70, 71, 75, 140, 210, 280, 350, 420, 490]


def test_misleading_output_path_cannot_hide_existing_destination(inputs):
    existing = inputs[2]
    existing.mkdir()
    misleading = existing.parent/'missing'/'..'/existing.name
    with pytest.raises(FileExistsError, match='existing'):
        dp.preflight(inputs[0], inputs[1], misleading, pressure_peak_pa=1)
    assert list(existing.iterdir()) == []
