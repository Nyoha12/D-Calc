"""SOURCE-02 real CLI, source schema, offline comparison and v1 separation."""
from collections import Counter
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from tools import forced_response_compare as cli
from tools.compare_forced_response import comparison as cmp
from didgeridoo_optimizer.acoustics import forced_response as fr
from didgeridoo_optimizer.acoustics.source_impedance import apply_thevenin_source
from didgeridoo_optimizer.reporting import forced_response as report
from didgeridoo_optimizer.tests.test_forced_response_comparison import payload as legacy_payload, complex_curve

ROOT = Path(__file__).resolve().parents[2]


def model(data):
    return data['cases'][0]['models'][0]


def fixture(ps=1., zs=2.):
    data = legacy_payload()
    tr = fr.transfer_from_slices([70., 1000., 1500.], [], 1., zref=1.)
    response = apply_thevenin_source(tr, ps, zs)
    m = model(data)
    m.update(source=response['source'], ports={k:v.payload() for k,v in response['ports'].items()},
             powers=response['powers'], source_powers=response['source_powers'], propagation_seconds=0.)
    m['radiation']['source'] = {'kind': 'synthetic fixture'}
    data.update(schema=report.SCHEMA_V2, units=report.UNITS | report.SOURCE_UNITS)
    return data


def loaded(data):
    cmp.validate_export(data)
    return dict(payload=data, file=dict(path='fixture', bytes=1, sha256='fixture'))


def compare(a, b):
    return cmp.compare_exports(loaded(a), loaded(b))


def command(out, *extra):
    return [sys.executable, '-B', '-m', 'tools.forced_response_compare', '--case', 'cylinder',
            '--points', '3', '--f-min', '70', '--f-max', '72', '--h-cm', '10',
            '--output-dir', str(out), *extra]


def run(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=45)
    data = json.loads(result.stdout)
    assert 'Traceback' not in result.stderr
    return result, data


def test_real_cli_v2_json_csv_markdown_and_overwrite(tmp_path):
    out = tmp_path/'v2'
    cmd = command(out, '--thevenin-pressure-peak-pa', '2', '--source-resistance-pa-s-m3', '300000',
                  '--loss-model', 'zk', '--air-reference', 'ck_dry20', '--radiation-model', 'silva_unflanged')
    result, summary = run(cmd)
    assert result.returncode == 0, result.stdout+result.stderr
    assert summary['schema'] == report.SCHEMA_V2
    data = json.loads((out/'forced_response.json').read_text())
    cmp.validate_export(data)
    m = model(data)
    assert m['source']['kind'] == 'thevenin_pressure'
    assert m['source']['impedance']['value'] == [dict(real=300000., imag=0.)]*3
    assert m['source']['amplitude']['value'] == [dict(real=2., imag=0.)]*3
    rows = list(csv.DictReader((out/'forced_response.csv').open()))
    for i, row in enumerate(rows):
        assert float(row['source_impedance_real']) == 300000.
        assert row['source_impedance_units'] == 'Pa.s/m^3'
        for name, curve in m['source_powers'].items():
            assert row[name+'_units'] == report.SOURCE_UNITS[name]
            assert float(row[name]) == curve['value'][i]
            assert row[name+'_status'] == curve['status'][i]
            assert row[name+'_reason'] == (curve['reason'][i] or '')
    counts = Counter(s for group in ('transfers', 'ports', 'powers', 'source_powers')
                     for curve in m[group].values() for s in curve['status'])
    assert m['observable_status_counts'] == dict(counts)
    assert m['numerically_complete'] == all(s in {'ok', 'analytic_zero'} for s in counts)
    text = (out/'forced_response.txt').read_text()
    assert 'Ps (Pa peak)' in text and 'Zs (Pa.s/m^3)' in text
    assert all(name in text for name in report.SOURCE_UNITS)
    before = {p.name:p.read_bytes() for p in out.iterdir()}
    again, error = run(cmd)
    assert again.returncode == 1 and error['ok'] is False and 'overwrite' in error['error']
    assert before == {p.name:p.read_bytes() for p in out.iterdir()}
    # Actual offline CLI (stdlib only), v2 powers are namespaced in comparison.
    dest = tmp_path/'comparison'
    args = [sys.executable, '-B', '-S', '-m', 'tools.compare_forced_response',
            '--baseline', str(out/'forced_response.json'), '--candidate', str(out/'forced_response.json'),
            '--output-dir', str(dest)]
    checked, info = run(args+['--dry-run'])
    assert checked.returncode == 0 and info['exports'] == {} and not dest.exists()
    checked, info = run(args)
    assert checked.returncode == 0, checked.stdout+checked.stderr
    comp = json.loads((dest/'comparison.json').read_text())
    assert comp['points'][0]['observables']['source_powers.Psupply']['metrics']['delta']['value'] == 0
    rows = list(csv.DictReader((dest/'comparison.csv').open()))
    power = next(row for row in rows if row['observable'] == 'source_powers.Psupply')
    assert power['units'] == 'W' and power['baseline_status'] == 'ok'
    assert 'source_powers.eta_source' in (dest/'comparison.md').read_text()


@pytest.mark.parametrize('options', [
    ['--thevenin-pressure-peak-pa', '1'],
    ['--pressure-peak-pa', '1', '--source-resistance-pa-s-m3', '0'],
    ['--flow-peak-m3-s', '1e-6', '--source-resistance-pa-s-m3', '2'],
    *[['--thevenin-pressure-peak-pa', '1', '--source-resistance-pa-s-m3', x] for x in ('-1', 'nan', 'inf', 'true')],
    *[['--thevenin-pressure-peak-pa', x, '--source-resistance-pa-s-m3', '0'] for x in ('0', '-1', 'nan', 'inf')],
    ['--thevenin-pressure-peak-pa', '1', '--pressure-peak-pa', '1', '--source-resistance-pa-s-m3', '0']])
def test_real_cli_errors_json_and_no_output(options, tmp_path):
    out = tmp_path/'forbidden'
    result, data = run(command(out, *options))
    assert result.returncode == 1 and data['ok'] is False and data['error']
    assert not out.exists()


def test_preflight_no_propagation_and_boolean_resistance(monkeypatch, tmp_path):
    def forbidden(*a, **k):
        raise AssertionError('preflight propagated')
    monkeypatch.setattr(fr, 'loaded_transfer', forbidden)
    out = tmp_path/'none'
    args = cli.parser().parse_args(command(out, '--thevenin-pressure-peak-pa', '1',
        '--source-resistance-pa-s-m3', '0', '--dry-run')[4:])
    assert cli.run(args)['output_created'] is False
    assert not out.exists()
    args.source_resistance_pa_s_m3 = True
    with pytest.raises(ValueError, match='boolean'):
        cli.run(args)
    args.source_resistance_pa_s_m3 = -1
    with pytest.raises(ValueError):
        cli.run(args)


def test_same_source_vector_exact_and_comparison_metrics():
    data = fixture(ps=[1+1j, 2-3j, 4j], zs=[0, 2+3j, -2j])
    result = compare(data, deepcopy(data))
    observables = result['points'][1]['observables']
    assert 'Pin' in observables and 'source_powers.Psupply' in observables
    assert observables['source_powers.Pinternal']['metrics']['power_ratio_db']['value'] == 0
    assert 'power_ratio_db' not in observables['source_powers.eta_source']['metrics']
    assert 'source_powers.Psupply.delta' in result['coverage']
    rendered = cmp.render_bundle(result)
    assert all(name in rendered['comparison.md'] for name in report.SOURCE_UNITS)
    for key in ('impedance', 'amplitude'):
        other = deepcopy(data)
        source = model(other)['source']
        old = [complex(x['real'], x['imag']) for x in source[key]['value']]
        old[-1] += 1e-12j
        source[key] = complex_curve(old)
        with pytest.raises(ValueError, match='source_impedance' if key == 'impedance' else 'source_amplitude'):
            compare(data, other)
    with pytest.raises(ValueError, match='familles.*Zs=0'):
        compare(legacy_payload(), fixture(zs=0))


@pytest.mark.parametrize('broken', ['impedance', 'impedance_units', 'source_powers', 'kind', 'amplitude',
                                   'wrong_schema', 'wrong_kind', 'units', 'negative', 'shape', 'collision'])
def test_schema_metadata_required_and_consistent(broken):
    data = fixture()
    m = model(data)
    if broken in ('impedance', 'impedance_units', 'kind', 'amplitude'):
        del m['source'][broken]
    elif broken == 'source_powers':
        del m[broken]
    elif broken == 'wrong_schema':
        data['schema'] = report.SCHEMA
    elif broken == 'wrong_kind':
        m['source']['kind'] = 'pressure'
    elif broken == 'units':
        m['source']['impedance_units'] = 'ohm'
    elif broken == 'negative':
        m['source']['impedance'] = complex_curve([-1.]*3)
    elif broken == 'shape':
        m['source']['impedance']['value'].pop()
    else:
        m['source_powers']['Pin'] = m['powers']['Pin']
    with pytest.raises(ValueError):
        cmp.validate_export(data)
    with pytest.raises(ValueError):
        report.render_bundle(data)


@pytest.mark.parametrize('malformed', [None, [], 7, {'schema':report.SCHEMA_V2},
                                     {'schema':report.SCHEMA_V2, 'cases':[None]}])
def test_malformed_json_is_value_error_not_attribute_error(malformed):
    for validate in (cmp.validate_export, report.render_bundle):
        with pytest.raises(ValueError):
            validate(malformed)


@pytest.mark.parametrize('amplitude', [0., 1e-300, 1e300])
def test_extreme_export_null_zero_status_logs_and_ratios(amplitude):
    data = fixture(ps=amplitude, zs=1.)
    rendered = report.render_bundle(data)
    restored = json.loads(rendered['forced_response.json'], parse_constant=lambda x:pytest.fail(x))
    cmp.validate_export(restored)
    rows = list(csv.DictReader(io.StringIO(rendered['forced_response.csv'])))
    if amplitude == 0:
        assert rows[0]['Psupply'] == '0.0' and rows[0]['eta_source'] == ''
        assert model(restored)['source_powers']['eta_source']['value'] == [None]*3
    else:
        assert rows[0]['Psupply'] == ''
        assert rows[0]['Psupply_log_abs'] and rows[0]['Psupply_reason']
        assert float(rows[0]['eta_source']) == pytest.approx(.5, rel=2e-12)
        result = compare(restored, restored)
        power = result['points'][0]['observables']['source_powers.Psupply']['metrics']
        assert power['delta']['value'] is None and power['power_ratio_db']['value'] == 0


def test_v1_never_gains_finite_source_fields(tmp_path):
    for option, amplitude in [('--pressure-peak-pa', '1'), ('--flow-peak-m3-s', '1e-6')]:
        out = tmp_path/option
        result, data = run(command(out, option, amplitude))
        assert result.returncode == 0
        data = json.loads((out/'forced_response.json').read_text())
        assert data['schema'] == report.SCHEMA and data['units'] == report.UNITS
        assert 'source_powers' not in model(data)
        assert 'impedance' not in model(data)['source']
        assert 'source_impedance' not in (out/'forced_response.csv').read_text()
        cmp.validate_export(data)
