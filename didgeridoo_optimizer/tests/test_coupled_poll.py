"""Exact secants and native scheduling; scored examples are synthetic, not music."""
import ast
import copy
import json
import math
import signal
import subprocess
import time

import pytest

from didgeridoo_optimizer.optimization import played_search as core
from didgeridoo_optimizer.pipeline import played_search as flow
from didgeridoo_optimizer.reporting import played_search as report
from didgeridoo_optimizer.tests.test_seeded_played_search import job, change, spec, ROOT

BASE = '08f06a2b17d9d15063b7e60c79285fe02f412b7b'
VARIABLES = [dict(id='x', low=0, high=1), dict(id='y', low=0, high=1)]


def coupled(points=((.25, .25), (.5, .5)), *, initial=False, stop=False):
    starts, search = spec(points, initial=initial, stop=stop)
    search.update(coupled_directions='start_secants_v1', normalized_steps=[.5, .25], directions=[1, -1])
    return starts, search


def observe(values, alternative, index, stage, **kw):
    """Synthetic hard corridor x=y and hard target x+y=1.5, fixed in both modes."""
    x, y = values
    rows = [dict(id='corridor', kind='static', role='hard', status='pass' if x == y else 'fail',
                 value_si=x-y, violation_normalized=abs(x-y)/.01),
            dict(id='target', kind='static', role='hard', status='pass' if x+y == 1.5 else 'fail',
                 value_si=x+y, violation_normalized=abs(x+y-1.5)/.1)]
    return dict(criteria=rows, units_complete=True, file=f'candidate-{index}.json',
                **core.verdict(rows, units_complete=True))


def test_constructor_normalization_units_orientation_order_and_exact_dedup():
    starts, _ = coupled([(30, 3000), (10, 1000), (10., 1000.), (20, 2000), (30, 1000)])
    variables = [dict(id='metres', low=0, high=40), dict(id='millimetres', low=0, high=4000)]
    original = copy.deepcopy(starts)
    registry = core.build_coupled_directions(variables, starts)
    assert starts == original
    assert registry == dict(method='start_secants_v1', variable_order=['metres', 'millimetres'], directions=[
        dict(id='secant-001', source_pair=['s0', 's1'], components_normalized=[1., 1.]),
        dict(id='secant-002', source_pair=['s3', 's4'], components_normalized=[1., -1.])])
    # Units disappear only through each declared width; no raw-SI Euclidean norm.
    variables[0].update(low=0., high=4.)
    for s in starts['selected']: s['variables_si'][0] /= 10
    assert core.build_coupled_directions(variables, starts) == registry


def test_selected_only_initial_inclusion_signed_zero_and_no_rounding():
    starts, _ = coupled([(.25, .75), (.75, .75)])
    with pytest.raises(ValueError, match='no coupled'): core.build_coupled_directions(VARIABLES, starts)
    starts['selected'].insert(0, starts['initial'])
    registry = core.build_coupled_directions(VARIABLES, starts)
    assert registry['directions'][0]['source_pair'] == ['initial', 's0']
    three = VARIABLES + [dict(id='z', low=-1, high=1)]
    starts, _ = coupled([(0., 0., -0.), (1., 1., 0.)])
    d = core.build_coupled_directions(three, starts)['directions'][0]['components_normalized']
    assert d == [1., 1., 0.] and math.copysign(1, d[2]) == 1
    starts, _ = coupled([(0., 0.), (.5, .5), (1., math.nextafter(1., 0.))])
    ds = core.build_coupled_directions(VARIABLES, starts)['directions']
    assert len(ds) == 3 and ds[0]['components_normalized'] != ds[1]['components_normalized']


def test_integer_subtractions_do_not_round_before_normalization():
    # Pure arithmetic boundary, not a proposed physical geometry.
    base = 2**53
    starts, _ = coupled([(base+1, base+2), (base+2, base+4)])
    variables = [dict(id=k, low=base, high=base+8) for k in ('x', 'y')]
    registry = core.build_coupled_directions(variables, starts)
    assert registry['directions'][0]['components_normalized'] == [.5, 1.]


@pytest.mark.parametrize('points', [[(0, 0), (0, 0)], [(0, 0), (1, 0)], [(0,), (1,)], [(), ()]])
def test_no_coupled_direction_is_explicit_refusal(points):
    starts, _ = coupled(points)
    with pytest.raises(ValueError, match='no coupled'):
        core.build_coupled_directions(VARIABLES[:len(points[0])], starts)


@pytest.mark.parametrize('value', [True, False, '0', float('nan'), float('inf'), -float('inf')])
@pytest.mark.parametrize('field', ['low', 'high', 'value'])
def test_constructor_strict_finite_numeric_types(value, field):
    starts, _ = coupled(); variables = copy.deepcopy(VARIABLES)
    if field == 'value': starts['selected'][0]['variables_si'][0] = value
    else: variables[0][field] = value
    with pytest.raises(ValueError): core.build_coupled_directions(variables, starts)


@pytest.mark.parametrize('low,high', [(1, 1), (1, 0), (-1e308, 1e308)])
def test_degenerate_or_overflowing_scale(low, high):
    starts, _ = coupled(); variables = copy.deepcopy(VARIABLES); variables[0].update(low=low, high=high)
    with pytest.raises(ValueError, match='scale'): core.build_coupled_directions(variables, starts)


def forbid_science(monkeypatch):
    from didgeridoo_optimizer.nonlinear import scalar_fit
    from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
    from didgeridoo_optimizer.acoustics import transfer_matrix
    def no(*args, **kwargs): pytest.fail('unexpected science')
    monkeypatch.setattr(scalar_fit, 'fit', no)
    monkeypatch.setattr(SimultaneousCoupling, 'step', no)
    monkeypatch.setattr(transfer_matrix, 'input_impedance', no)
    monkeypatch.setattr(flow.td, 'input_impedance', no)
    monkeypatch.setattr(flow.td, 'fit_passive', no)


@pytest.mark.parametrize('mutation', [
    lambda r: r['search'].update(coupled_directions=True),
    lambda r: r['search'].update(coupled_directions=1),
    lambda r: r['search'].update(coupled_directions=None),
    lambda r: r['search'].update(coupled_directions=[]),
    lambda r: r['search'].update(coupled_directions={}),
    lambda r: r['search'].update(coupled_directions='adaptive'),
    lambda r: r['search'].update(coupled_directions=float('nan')),
    lambda r: r['search'].pop('starts'),
    lambda r: r['search']['starts'].update(mode='evaluate_only'),
    lambda r: r.update(input=dict(kind='assembly', assembly='missing', request='missing')),
    lambda r: r['search']['starts'].update(profiles=r['search']['starts']['profiles'][:1]),
])
@pytest.mark.parametrize('session', [False, True])
def test_preflight_refusal_before_science_or_destination(job, monkeypatch, mutation, session):
    change(job, lambda r: (r['search'].update(coupled_directions='start_secants_v1'),
                          r['search']['starts'].update(mode='poll')))
    change(job, mutation); forbid_science(monkeypatch)
    monkeypatch.setattr(report, 'write_json', lambda *a, **k: pytest.fail('write before refusal'))
    out = job.parent/'never'
    with pytest.raises(ValueError): flow.run(job, out, session=session, dry_run=session)
    assert not out.exists()


def assert_session_refusal(result, out, reason):
    assert result['ok'] is False and result['status'] == 'partial'
    assert result['resumable'] is False and reason in result['reason']
    assert result['children'] == []
    for key in ('candidates', 'fits', 'trajectories', 'steps_charged', 'children',
                'accepted_steps', 'static_units', 'reader_units'):
        assert result['counters'][key] == 0
    assert all(v == 0 for v in result['counters']['reserved'].values())
    assert result['counters']['elapsed_seconds'] > 0
    assert report.read_json(out/'failure-00001.json') == result
    assert (out/'invocation-00001.json').is_file()
    assert not list(out.glob('boundary-*'))
    assert not list(out.glob('session-*.json'))
    assert not list(out.glob('candidate*'))
    assert not (out/'plan.json').exists()


def test_real_session_constructs_after_journal_under_deadline(job, monkeypatch):
    change(job, lambda r: (r['search'].update(coupled_directions='start_secants_v1'),
                          r['search']['starts'].update(mode='poll')))
    forbid_science(monkeypatch)
    monkeypatch.setattr(flow, 'launch', lambda *a, **k: pytest.fail('unexpected launch'))
    monkeypatch.setattr(flow, 'execute', lambda *a, **k: pytest.fail('unexpected execution'))
    out = job.parent/'session'; observed = []
    def refuse(variables, starts):
        invocation = report.read_json(out/'invocation-00001.json')
        remaining, interval = signal.getitimer(signal.ITIMER_REAL)
        assert 0 < remaining <= 900 and interval == 0
        assert signal.getsignal(signal.SIGALRM).__name__ == 'deadline'
        assert invocation['generation'] == 1
        assert invocation['started_monotonic'] <= time.monotonic()
        observed.append(time.monotonic()-invocation['started_monotonic'])
        raise ValueError('injected coupled admission refusal')
    monkeypatch.setattr(core, 'build_coupled_directions', refuse)
    result = flow.run(job, out, session=True)
    assert len(observed) == 1
    assert_session_refusal(result, out, 'injected coupled admission refusal')
    assert result['counters']['elapsed_seconds'] >= observed[0]


def test_real_session_no_coupled_direction_journaled_nonresumable(job, monkeypatch):
    change(job, lambda r: (r['search'].update(coupled_directions='start_secants_v1'),
                          r['search']['starts'].update(mode='poll', include_initial=False,
                              profiles=r['search']['starts']['profiles'][:1])))
    inputs = {p: p.read_bytes() for p in job.parent.iterdir() if p.is_file()}
    forbid_science(monkeypatch)
    monkeypatch.setattr(flow, 'launch', lambda *a, **k: pytest.fail('unexpected launch'))
    monkeypatch.setattr(flow, 'execute', lambda *a, **k: pytest.fail('unexpected execution'))
    out = job.parent/'session'
    result = flow.run(job, out, session=True)
    assert_session_refusal(result, out, 'no coupled')
    assert {p: p.read_bytes() for p in inputs} == inputs


def test_real_session_bootstrap_refusal_keeps_destination_absent(job, monkeypatch):
    change(job, lambda r: (r['search'].update(coupled_directions='start_secants_v1'),
                          r['search']['starts'].update(mode='poll'),
                          r['budgets'].update(seconds=True)))
    forbid_science(monkeypatch)
    monkeypatch.setattr(flow, 'launch', lambda *a, **k: pytest.fail('unexpected launch'))
    out = job.parent/'never'
    with pytest.raises(ValueError, match='bootstrap budget scalar'):
        flow.run(job, out, session=True)
    assert not out.exists()


def test_preflight_registry_reconstruction_tampering_without_science(job, monkeypatch):
    change(job, lambda r: (r['search'].update(coupled_directions='start_secants_v1'),
                          r['search']['starts'].update(mode='poll')))
    forbid_science(monkeypatch)
    p = flow.preflight(job, job.parent/'never')
    assert p['coupled_directions'] == core.build_coupled_directions(p['variables'], p['starts'])
    for edit in (lambda r: r.update(method='forged'), lambda r: r['directions'][0].update(id='forged'),
                 lambda r: r['directions'][0]['source_pair'].reverse(),
                 lambda r: r['directions'][0]['components_normalized'].__setitem__(0, 1.),
                 lambda r: r['variable_order'].reverse(), lambda r: r.update(directions=[])):
        forged = copy.deepcopy(p); edit(forged['coupled_directions'])
        forged['context_sha256'] = flow.digest({k:v for k,v in forged.items() if k != 'context_sha256'})
        with pytest.raises(ValueError, match='Reconstructed'): flow.check_plan(forged)
    assert not (job.parent/'never').exists()


def test_synthetic_corridor_gain_with_same_native_verdict_and_budget(record_property):
    starts, search = coupled(stop=True)
    def run(s): return core.poll(VARIABLES, [.5, .5], ['nominal'], s, 16, observe, starts=starts)
    axis = run({k:v for k,v in search.items() if k != 'coupled_directions'})
    together = run(search)
    assert axis['witness'] is None
    assert together['witness']['variables_si'] == [.75, .75]
    assert together['witness']['candidate'] == 3 and together['termination'] == 'feasible_witness_found'
    assert together['witness']['stage'] == 'coupled_poll'
    assert together['witness']['start_origin'] == starts['selected'][0]['origin']
    record_property('synthetic_gain', 'same hard x=y / x+y=1.5, budget16: axes no witness; coupled candidate3 [.75,.75]')


def test_coupled_fixed_anchor_multiple_steps_bounds_duplicates_budget_and_pause():
    starts, search = coupled(points=[(.25, .25), (.5, .5), (.25, .25)])
    def run(evaluate, maximum=16):
        return core.poll(VARIABLES, [.5, .5], ['nominal'], search, maximum, evaluate, starts=starts)
    whole = run(observe)
    assert whole['termination'] == 'candidate_budget'
    assert len(whole['history']) == 16
    assert whole['history'][2]['variables_si'] == [.75, .75]
    assert whole['history'][3]['variables_si'] == [.75, .25]  # axis uses old .25/.25 anchor
    assert any(d['action'] == 'skip_bounds' and d['stage'] == 'coupled_poll' for d in whole['decisions'])
    assert any(d['action'] == 'skip_duplicate' and d.get('stage') == 'coupled_poll' for d in whole['decisions'])
    assert {d['coupled_direction']['step'] for d in whole['decisions'] if 'coupled_direction' in d} == {.5, .25}
    assert len({tuple(r['variables_si']) for r in whole['history']}) == len(whole['history'])
    assert all(0 <= x <= 1 for r in whole['history'] for x in r['variables_si'])
    prefix = []; fresh = []
    for pause in [2] + [1]*16:
        before = len(prefix)
        def resumed(x, a, i, stage, **kw):
            if i <= before: return copy.deepcopy(prefix[i-1])
            if i > before+pause: raise StopIteration('session_paused')
            fresh.append(i); return observe(x, a, i, stage, **kw)
        part = run(resumed); prefix = part['history']
        if part['termination'] != 'session_paused': break
    assert part == whole and fresh == list(range(1, 17))
    assert run(observe, maximum=2)['termination'] == 'candidate_budget'


def frozen_functions(path, names, globals_):
    source = subprocess.run(['git', 'show', BASE+':'+path], cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=5).stdout
    nodes = [n for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    namespace = dict(globals_)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), BASE+':'+path, 'exec'), namespace)
    return namespace


@pytest.mark.parametrize('seeded,mode,descriptive', [(False, 'poll', False), (False, 'poll', True),
    (True, 'poll', False), (True, 'poll', True), (True, 'evaluate_only', False)])
@pytest.mark.parametrize('initial,stop,maximum', [(False, False, 16), (True, False, 8), (True, True, 16)])
def test_absent_option_exact_base_function_fields_tables_and_calls(seeded, mode, descriptive, initial, stop, maximum):
    old = frozen_functions('didgeridoo_optimizer/optimization/played_search.py', {'poll', 'better'}, vars(core))
    starts, search = spec([[.25, .25], [.5, .5], [.25, .25]], mode, initial, stop)
    search.update(normalized_steps=[.5, .25], directions=[1, -1])
    if not seeded: search.pop('starts')
    def run(fn):
        calls = []
        def evaluate(*args, **kw): calls.append(copy.deepcopy((args, kw))); return observe(*args, **kw)
        result = fn(VARIABLES, [.5, .5], ['nominal'], search, maximum, evaluate,
                    starts=starts if seeded else None, descriptive=descriptive)
        return result, calls
    current, calls = run(core.poll); baseline, old_calls = run(old['poll'])
    assert flow.digest(current) == flow.digest(baseline) and calls == old_calls
    assert all('coupled_direction' not in r for r in current['history']+current['decisions'])
    # Use the real BASE export function, not a copy of the updated implementation.
    prior = frozen_functions('didgeridoo_optimizer/reporting/played_search.py', {'export_tables'}, vars(report))
    plan = dict(variables=VARIABLES)
    if seeded:
        plan['starts'] = starts
        for row in current['history']:
            row['start_origin'].update(source={'sha256':'synthetic'}, variables_si=[.5,.5], generated_sha256='synthetic', mode=mode)
    result = dict(plan=plan, search=current)
    assert report.export_tables(result) == prior['export_tables'](result)


def test_protected_functions_source_identical_to_base():
    for module, names in [(core, ['recipe','played_rows','static_rows','verdict','better']),
                          (flow, ['Budget','launch','state_trajectory','run','run_session','verify_session_state'])]:
        path = str(module.__file__).removeprefix(str(ROOT)+'/')
        old = subprocess.run(['git','show',BASE+':'+path], cwd=ROOT, check=True, capture_output=True, text=True, timeout=5).stdout
        new = (ROOT/path).read_text()
        def segments(source):
            return {n.name:ast.get_source_segment(source,n) for n in ast.parse(source).body
                    if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names}
        assert segments(new) == segments(old)
