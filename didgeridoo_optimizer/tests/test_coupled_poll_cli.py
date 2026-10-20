"""Real CLI/native static screening and fresh-process continuation: zero music claims."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from didgeridoo_optimizer.tests.test_seeded_played_search import ROOT, write
from didgeridoo_optimizer.tests.test_seeded_played_search_cli import ENV, rehash
from didgeridoo_optimizer.tests.test_coupled_poll import forbid_science
from didgeridoo_optimizer.pipeline import played_search as flow
from didgeridoo_optimizer.reporting import played_search as report

EXAMPLE = ROOT/'project_specs/examples/coupled_poll/job-filter.yaml'


def cli(root, name, *args, expected=0):
    command = [sys.executable, '-B', '-m', 'tools.played_search', *map(str, args)]
    started = time.monotonic()
    proc = subprocess.run(command, cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=90)
    (root/(name+'.stdout.json')).write_text(proc.stdout)
    (root/(name+'.stderr.txt')).write_text(proc.stderr)
    write(root/(name+'.receipt.json'), dict(command=command, exit_code=proc.returncode, seconds=time.monotonic()-started))
    assert proc.returncode == expected, proc.stdout+proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope='module')
def screened(tmp_path_factory):
    root = tmp_path_factory.mktemp('coupled-cli')
    dry = cli(root, 'dry', '--job', EXAMPLE, '--output-dir', root/'never', '--dry-run')
    assert dry['ok'] and not (root/'never').exists()
    assert dry['plan']['coupled_directions']['method'] == 'start_secants_v1'
    out = root/'ordinary'
    result = cli(root, 'ordinary', '--job', EXAMPLE, '--output-dir', out)
    assert result['ok'] and not result['conforming']
    assert result['counters']['candidates'] == result['counters']['static_units'] == 3
    assert result['counters']['fits'] == result['counters']['trajectories'] == result['counters']['steps_charged'] == 0
    return root, out


def test_real_cli_native_registry_tables_criteria_and_distinct_acquisition(screened, monkeypatch):
    root, out = screened
    read = cli(root, 'reader', '--read-result', out)
    assert read['ok']
    forbid_science(monkeypatch)
    checked = report.read_result(out)
    assert checked['ok'], checked
    result = checked['result']; plan = result['plan']; rows = result['search']['history']
    assert plan['coupled_directions'] == flow.core.build_coupled_directions(plan['variables'], plan['starts'])
    assert [r['stage'] for r in rows] == ['explicit_start', 'explicit_start', 'coupled_poll']
    assert rows[2]['start_origin'] == rows[0]['start_origin']
    assert rows[2]['coupled_direction']['source_pair'] == ['short_wide', 'long_wide']
    assert all(a != b for a, b in zip(rows[2]['variables_si'], rows[0]['variables_si']))
    assert 'start_secants_v1' in (out/'summary.txt').read_text()
    designs = []
    full = []
    for r in rows:
        row = report.read_json(out/r['file']); full.append(row)
        assert row['status'] == 'violated' and not row['conforming']
        assert row['errors'] == ['proved_static_violation; played_not_evaluated']
        assert all(c['value_si'] is None and c['status'] == 'not_evaluated' for c in row['criteria'] if c['kind'] == 'played')
        static = report.read_json(out/Path(r['file']).parent/'static.json')
        assert static['details']['nominal']['geometry_state_acquisition']['spectra'] == []
        designs.append(report.file_sha256(out/row['configurations']['nominal']['design']))
    assert len(set(designs)) == 3
    for name, table_rows, fields in report.export_tables(dict(result, search=dict(result['search'], history=full))):
        assert (out/name).read_bytes() == report.table_bytes(table_rows, fields)
    assert all(c['reaped'] and c['exit_code'] == 0 for c in checked['execution']['children'])


def test_real_fresh_process_session_before_first_coupled_preserves_prefix(screened):
    root, ordinary = screened
    whole = root/'whole-session'; split = root/'split-session'
    full = cli(root, 'whole', '--job', EXAMPLE, '--output-dir', whole, '--session')
    assert full['status'] == 'complete'
    paused = cli(root, 'pause', '--job', EXAMPLE, '--output-dir', split, '--session', '--pause-after', 2, expected=3)
    prefix = report.read_json(paused['resume_point'])['payload']
    assert [r['stage'] for r in prefix['result']['search']['history']] == ['explicit_start']*2
    pins = dict(prefix['files'])
    resumed = cli(root, 'resume', '--job', EXAMPLE, '--output-dir', split, '--resume', paused['resume_point'])
    assert resumed['status'] == 'complete'
    inventory = report.session_inventory(split)
    assert all(inventory[name] == sha for name, sha in pins.items())
    read = cli(root, 'split-reader', '--read-result', split)
    assert read['ok']
    expected = report.read_json(whole/'result.json')
    actual = report.read_json(split/'result.json')
    normal = report.read_json(ordinary/'result.json')
    assert actual['search'] == expected['search'] == normal['search']
    assert actual['plan']['coupled_directions'] == expected['plan']['coupled_directions']
    for row in actual['search']['history']:
        assert report.read_json(split/row['file']) == report.read_json(whole/row['file'])
    counters = resumed['counters']
    assert counters['candidates'] == counters['static_units'] == 3
    assert counters['fits'] == counters['trajectories'] == counters['accepted_steps'] == counters['steps_charged'] == 0
    last = report.read_json(resumed['resume_point'])['payload']
    children = last['children'][len(prefix['children']):]
    assert [c['kind'] for c in children] == ['read', 'static']
    assert last['phases']['replayed_candidates'] == 2
    assert actual['search']['history'][2]['stage'] == 'coupled_poll'
    assert last['result']['conforming'] is False


@pytest.mark.parametrize('attack', ['registry', 'direction', 'pair', 'decision', 'candidate_direction',
                                   'candidate_pair', 'directions_csv', 'missing_csv', 'candidates_csv'])
def test_rehashed_registry_decision_and_csv_attacks_refused(screened, tmp_path, attack):
    _, source = screened
    out = tmp_path/attack; shutil.copytree(source, out)
    result = report.read_json(out/'result.json'); plan = result['plan']
    if attack in ('registry', 'direction', 'pair'):
        registry = plan['coupled_directions']
        if attack == 'registry': registry['method'] = 'forged'
        if attack == 'direction': registry['directions'][0]['components_normalized'][0] = .75
        if attack == 'pair': registry['directions'][0]['source_pair'].reverse()
        plan['context_sha256'] = flow.digest({k:v for k,v in plan.items() if k != 'context_sha256'})
        write(out/'plan.json', plan)
    elif attack == 'decision':
        next(d for d in result['search']['decisions'] if d.get('stage') == 'coupled_poll')['coupled_direction']['source_pair'].reverse()
    elif attack in ('candidate_direction', 'candidate_pair'):
        r = result['search']['history'][2]; row = report.read_json(out/r['file'])
        if attack == 'candidate_direction': row['coupled_direction']['id'] = 'forged'
        else: row['coupled_direction']['source_pair'].reverse()
        write(out/r['file'], row); result['search']['history'][2] = report.summary(row)
    elif attack == 'missing_csv': (out/'directions.csv').unlink()
    else:
        path = out/('directions.csv' if attack == 'directions_csv' else 'candidates.csv')
        path.write_text(path.read_text().replace('secant-001', 'secant-forged'))
    write(out/'result.json', result); rehash(out)
    got = report.read_result(out)
    assert got['status'] == 'unconfirmed' and not got['ok'], got


def test_rehashed_current_and_decisions_checkpoint_refused(screened, tmp_path):
    _, source = screened
    records = [(p, report.read_json(p)) for p in sorted(source.glob('checkpoint-*.json'))]
    checkpoint, original = next((p, r) for p, r in records if (r['payload'].get('current') or {}).get('stage') == 'coupled_poll')
    for attack in ('direction', 'pair', 'decision'):
        out = tmp_path/attack; shutil.copytree(source, out)
        for p in out.glob('checkpoint-*.json'):
            if p.name != checkpoint.name: p.unlink()
        forged = copy.deepcopy(original)
        if attack == 'direction': forged['payload']['current']['coupled_direction']['id'] = 'forged'
        elif attack == 'pair': forged['payload']['current']['coupled_direction']['source_pair'].reverse()
        else: forged['payload']['decisions'][0]['reason'] = 'forged'
        forged['sha256'] = flow.digest(forged['payload']); write(out/checkpoint.name, forged)
        with pytest.raises(ValueError): report.read_checkpoint(out)
