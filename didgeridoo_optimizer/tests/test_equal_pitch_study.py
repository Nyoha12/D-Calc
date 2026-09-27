"""Scoped equal-pitch contracts; no global replay or material calibration."""
import json
from pathlib import Path

import numpy as np
import pytest

from tools import equal_pitch_study as tool


def test_scalar_nonlinear_frequency_not_inverse_length():
    result = tool.solve_factor(lambda x: 50+7*x*x, 78., (.5, 3.))
    assert abs(result['factor']-2.) < .0001
    assert abs(result['error_hz']) <= .002
    assert len(result['history']) > 2
    for row in result['history']:
        assert row['frequency_hz'] == 50+7*row['factor']*row['factor']
        assert row['error_hz'] == row['frequency_hz']-78


def test_scalar_failures_and_trace():
    with pytest.raises(tool.StudyError, match='bracket') as caught:
        tool.solve_factor(lambda x: 80+x, 70, (.5, 2))
    assert len(caught.value.history) == 2
    with pytest.raises(tool.StudyError, match='iterations'):
        tool.solve_factor(lambda x: x*x, 2, (.1, 3), max_iterations=1, tolerance=1e-12)
    with pytest.raises(tool.StudyError, match='measured'):
        tool.solve_factor(lambda x: float('nan'), 70, (.5, 2))
    def ambiguous(x):
        raise tool.StudyError('ambiguous mode')
    with pytest.raises(tool.StudyError, match='ambiguous') as caught:
        tool.solve_factor(ambiguous, 70, (.5, 2))
    assert caught.value.history[0]['frequency_hz'] is None


@pytest.mark.parametrize('kwargs', [dict(bracket=(0,1)), dict(bracket=(2,1)),
    dict(bracket=(1,float('inf'))), dict(bracket=(True,2)), dict(max_iterations=0),
    dict(max_iterations=True), dict(tolerance=-1), dict(target=float('nan'))])
def test_scalar_parameters(kwargs):
    arguments = dict(target=70, bracket=(.5,2)) | kwargs
    with pytest.raises(ValueError):
        tool.solve_factor(lambda x: 70/x, **arguments)


@pytest.mark.parametrize('target', [49.9,90.1,0,True,float('inf'),float('nan')])
def test_target_bounds(target, tmp_path):
    with pytest.raises(ValueError):
        tool.run(tmp_path/'new', target, dry_run=True)
    assert not (tmp_path/'new').exists()


@pytest.mark.parametrize('name', tool.NAMES)
def test_scale_preserves_all_relative_geometry_and_materials(name):
    original = tool.builtin_design(name)
    before = original.as_dict()
    scaled = tool.scale_design(original, .923)
    assert original.as_dict() == before
    for a,b in zip(original.segments, scaled.segments, strict=True):
        assert b.length_cm/a.length_cm == pytest.approx(.923)
        assert (a.kind,a.d_in_cm,a.d_out_cm,a.material_id,a.profile_params) == (b.kind,b.d_in_cm,b.d_out_cm,b.material_id,b.profile_params)
    assert sum(s.length_cm for s in scaled.segments) == pytest.approx(.923*sum(s.length_cm for s in original.segments))
    assert scaled.segments[-1].position_end_cm == pytest.approx(sum(s.length_cm for s in scaled.segments))


def test_dry_run_no_acoustics_no_output_known_destinations(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, 'evaluator', lambda *a: pytest.fail('acoustics in dry-run'))
    monkeypatch.setattr(tool, 'bounded_task', lambda *a: pytest.fail('worker in dry-run'))
    monkeypatch.setattr(tool, 'provenance', lambda: pytest.fail('provenance work in dry-run'))
    output = tmp_path/'new'
    result = tool.run(output, dry_run=True)
    assert not output.exists() and result['output_created'] is False
    assert len(result['plan']['destinations']) == 20
    assert result['plan']['source_resistances_pa_s_m3'] == [0,tool.ZREF_COMMON,10*tool.ZREF_COMMON]
    assert tool.main(['--output-dir',str(output),'--dry-run']) == 0


def test_exclusive_output_and_strict_json(tmp_path):
    existing = tmp_path/'previous'
    existing.mkdir()
    marker = existing/'run.json'
    marker.write_text('old')
    with pytest.raises(FileExistsError):
        tool.run(existing, dry_run=True)
    assert marker.read_text() == 'old'
    with pytest.raises(ValueError):
        tool.write_json(tmp_path/'nan.json', dict(value=float('nan')))
    assert not (tmp_path/'nan.json').exists()
    with pytest.raises(FileExistsError):
        tool.write_json(marker, {})
    assert marker.read_text() == 'old'


def test_first_peak_not_tallest_and_missing_ambiguous():
    def response(f):
        return (1+4*np.exp(-((f-70.1)/.5)**2)+20*np.exp(-((f-210)/2)**2)).astype(complex)
    peak = tool.first_peak(response)
    assert peak['frequency_hz'] == pytest.approx(70.1, abs=.001)
    assert peak['survey_peak_count'] == 2
    with pytest.raises(tool.StudyError, match='No first'):
        tool.first_peak(lambda f: np.ones(len(f), complex))
    def split(f):
        if len(f) == len(tool.SURVEY):
            return 1/(1+1j*(f-70))
        return (1+np.exp(-((f-69.7)/.1)**2)+np.exp(-((f-70.3)/.1)**2)).astype(complex)
    with pytest.raises(tool.StudyError, match='ambiguous'):
        tool.first_peak(split)


def test_common_absolute_sources_without_propagation(monkeypatch):
    calls = []
    monkeypatch.setattr(tool.fr, 'apply_source', lambda transfer, kind, amplitude: calls.append((kind,amplitude)))
    monkeypatch.setattr(tool, 'apply_thevenin_source', lambda transfer, ps, rs: calls.append(('thevenin',ps,rs)))
    for name in tool.NAMES:
        tool.apply_sources({'design':name})
    for start in range(0,20,5):
        assert calls[start:start+5] == [('pressure',1.),('volume_flow',1e-6),('thevenin',1.,0.),
                                      ('thevenin',1.,tool.ZREF_COMMON),('thevenin',1.,10*tool.ZREF_COMMON)]


def test_common_frequencies_never_divide_own_peaks():
    frequencies, groups = tool.frequency_points(70, 69.999)
    assert [f for f,g in zip(frequencies,groups) if g=='common_harmonic'] == [70,140,210,280,350,420,490]
    assert [f for f,g in zip(frequencies,groups) if g=='own_peak'] == [69.999]
    duplicate, _ = tool.frequency_points(70,70)
    assert len(duplicate)==11 and len(set(duplicate))==11
    rows = []
    for case,value in [('cylinder',2.),('expansion',3.)]:
        for group,frequency in [('common_harmonic',70.),('own_peak',69.9 if case=='cylinder' else 70.1)]:
            rows.append(dict(case=case,geometry_group='common_3cm_ports',source='pressure',frequency_group=group,
                frequency_hz=frequency,observable='Pload',value=value,real=None,imag=None,status='ok'))
    ratios = tool.ratios_against_cylinder(rows)
    assert len(ratios) == 2 and ratios[1]['ratio'] == 1.5
    rows[0]['status'] = 'roundoff_limited'
    assert all(row['ratio'] is None for row in tool.ratios_against_cylinder(rows))


def test_real_small_source_smoke_single_propagation(monkeypatch):
    # Twelve frequencies only, retained engine slices and actual boundary law.
    calls = []
    original = tool.fr.loaded_transfer
    def count(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(tool.fr, 'loaded_transfer', count)
    result = tool.sources('body_bell', 1., 70., 67.)
    assert len(calls) == 1 and calls[0]['exit_radius_m'] == .06
    assert calls[0]['zref'] == tool.ZREF_COMMON
    assert max(result['rs0_pressure_relative_errors'].values()) < 1e-8
    assert set(result['responses']) == set(tool.SOURCE_NAMES)
    assert result['frequency_groups'].count('own_peak') == 1
    json.dumps(result, allow_nan=False)
    rows = list(tool.source_rows(result))
    assert any(row['observable']=='Psupply' for row in rows)
    assert any(row['observable']=='Zin' and row['unit']=='Pa.s/m^3' for row in rows)


def test_failed_jobs_preserved_no_retry(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tool, 'provenance', lambda: {'head_sha':'synthetic test'})
    def failed(task, timeout):
        calls.append(task)
        return dict(ok=False, error='mode ambiguous', exit_code=1, wall_seconds=.1)
    monkeypatch.setattr(tool, 'bounded_task', failed)
    out = tmp_path/'partial'
    result = tool.run(out)
    assert result['status']=='partial' and len(calls)==4
    payload = json.loads((out/'run.json').read_text())
    assert len(payload['jobs'])==4 and all(j['exit_code']==1 for j in payload['jobs'])
    assert all((out/f'tune_{name}.json').exists() for name in tool.NAMES)


def test_unsupported_runner_refuses_before_output_but_dry_run_works(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, 'resource', None)
    path = tmp_path/'not_created'
    assert tool.run(path, dry_run=True)['ok']
    with pytest.raises(tool.StudyError, match='POSIX'):
        tool.run(path)
    with pytest.raises(tool.StudyError, match='POSIX'):
        tool.bounded_task({}, 1)
    assert not path.exists()


def test_module_import_without_resource_keeps_helpers_available(monkeypatch):
    import builtins
    import importlib.util
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == 'resource':
            raise ImportError('simulated Windows resource absence')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    spec = importlib.util.spec_from_file_location('_equal_pitch_no_resource', tool.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.resource is None
    assert module.target_value(70) == 70


def test_validation_retains_existing_survey_without_new_evaluations(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def evaluator(design, h):
        def evaluate(freq):
            calls.append(h)
            return np.full(len(freq), 2+3j)
        return evaluate, SimpleNamespace(segments=[1])
    def modes(*args, **kwargs):
        return [dict(status='resolved', mode_ordinal=i+1, frequency_max_abs_hz=f,
                     q_half_power=40., frequency_refinement=[dict(frequency_max_abs_hz=f, q_half_power=40.)]*2)
                for i,f in enumerate((70.,210.,350.))]
    monkeypatch.setattr(tool, 'evaluator', evaluator)
    monkeypatch.setattr(tool, 'extract_modes', modes)
    monkeypatch.setattr(tool, 'first_peak', lambda evaluate: {'frequency_hz':70.})
    result = tool.validate('cylinder', 1., 70.)
    assert calls == [.5,.25]
    for level in result['levels']:
        assert level['survey_frequency_hz'] == tool.SURVEY.tolist()
        assert level['survey_zin_real'] == [2.]*len(tool.SURVEY)
        assert level['survey_zin_imag'] == [3.]*len(tool.SURVEY)
