"""Synthetic spectra through the native extractor; no instrument measurements."""
import ast
import math
from pathlib import Path
import subprocess

import numpy as np
import pytest

from didgeridoo_optimizer.pipeline import constrained_design as pipeline


BASE = '07b00cda1de260ac366062f0e56fa9af2f7180a7'
SOURCE = 'didgeridoo_optimizer/pipeline/constrained_design.py'
SPEC = dict(min_hz=0., max_hz=4., refinement_hz=1.e-6)


@pytest.fixture(scope='module')
def legacy_extract():
    """Execute the original function body from Git, not a rewritten oracle."""
    root = Path(__file__).resolve().parents[2]
    source = subprocess.check_output(['git', 'show', f'{BASE}:{SOURCE}'], cwd=root, text=True)
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'extract_peaks')
    namespace = dict(np=np, math=math, find_peaks=pipeline.find_peaks)
    exec(compile(ast.Module(body=[function], type_ignores=[]), f'{BASE}:{SOURCE}', 'exec'), namespace)
    return namespace['extract_peaks']


def recorded(function):
    calls = []

    def evaluate(frequencies):
        calls.append(frequencies.copy())
        return function(frequencies)

    return evaluate, calls


def assert_contains(peak, lo, hi):
    left, right = peak['refinement_interval_hz']
    assert left <= lo <= hi <= right
    assert peak['frequency_estimate_hz'] == (right-left)/2


def test_synthetic_exact_parabola_counterexample(legacy_extract):
    truth = 97/64
    function = lambda f: 10-(f-truth)**2
    evaluate, calls = recorded(function)
    old = legacy_extract(function, SPEC, .5)
    new = pipeline.extract_peaks(evaluate, SPEC, .5)
    assert len(new) == len(old) == 1
    assert old[0]['status'] == 'unresolved'
    assert new[0]['status'] == 'resolved'
    assert_contains(new[0], truth, truth)
    first = calls[1]
    assert first[16] == 1.5 and first[17] == 1.53125
    assert function(first)[16] == function(first)[17] == 9.999755859375
    # Both equal samples and both outside neighbours survive the next grid.
    assert calls[2][0] == first[15] and calls[2][-1] == first[18]


@pytest.mark.parametrize('lo,hi,count', [
    (1.5, 1.53125, 2), (1.46875, 1.53125, 3), (1.375, 1.625, 9),
])
def test_synthetic_true_flat_top_keeps_entire_interval(lo, hi, count):
    def flat(f):
        distance = np.maximum(np.maximum(lo-f, f-hi), 0.)
        return 10-distance**2

    evaluate, calls = recorded(flat)
    peak, = pipeline.extract_peaks(evaluate, SPEC, .5)
    first = calls[1]
    tops = np.flatnonzero(flat(first) == 10.)
    assert len(tops) == count
    assert calls[2][0] == first[tops[0]-1]
    assert calls[2][-1] == first[tops[-1]+1]
    for grid in calls[1:-3]:
        assert grid[0] <= lo <= hi <= grid[-1]
    assert_contains(peak, lo, hi)
    assert peak['frequency_estimate_hz'] >= (hi-lo)/2
    assert peak['status'] == 'unresolved'
    assert 'courbure' in peak['reason']
    assert len(calls[1:-3]) == 12  # unchanged iteration budget
    assert all(len(grid) == 33 for grid in calls[1:-3])


def interpolated_basin(tops):
    """Fixed piecewise-linear synthetic spectrum, independent of call order."""
    grid = np.linspace(1., 2., 33)
    values = 10-(grid-1.5)**2
    for indices, height in tops:
        values[indices] = height
    knots = np.r_[0., grid, 4.]
    heights = np.r_[1., values, 1.]
    return lambda f: np.interp(f, knots, heights)


@pytest.mark.parametrize('tops', [
    [([12], 11.), ([20], 11.)],
    [([12, 13], 11.), ([20, 21], 11.)],
    [([12], 11.), ([20, 21], 10.8)],
    [([12, 13], 11.), ([20, 21], 10.8)],
])
def test_synthetic_separate_maxima_and_disjoint_plateaus_stay_ambiguous(tops):
    peak, = pipeline.extract_peaks(interpolated_basin(tops), SPEC, .5)
    assert peak['status'] == 'unresolved'
    if peak['relative_curvature_hz_minus2'] < -1.e-10:
        assert 'plusieurs maxima' in peak['reason']


def test_synthetic_prior_ambiguity_survives_later_unique_plateau():
    function = interpolated_basin([([12, 13], 11.), ([20], 10.8)])
    evaluate, calls = recorded(function)
    peak, = pipeline.extract_peaks(evaluate, SPEC, .5)
    # First local grid has distinct maxima; later grids retain only the higher run.
    assert calls[1][0] < 1.625 < calls[1][-1]
    assert calls[2][-1] < 1.625
    assert_contains(peak, 1.375, 1.40625)
    assert peak['status'] == 'unresolved'
    # Flat curvature also vetoes this peak; neither veto can be cleared.
    assert peak['relative_curvature_hz_minus2'] >= -1.e-10


def test_synthetic_prior_ambiguity_survives_later_strict_peak():
    function = interpolated_basin([([12], 11.), ([20, 21], 10.8)])
    evaluate, calls = recorded(function)
    peak, = pipeline.extract_peaks(evaluate, SPEC, .5)
    assert calls[2][-1] < calls[1][20]
    assert peak['relative_curvature_hz_minus2'] < -1.e-10
    assert peak['status'] == 'unresolved'
    assert 'plusieurs maxima' in peak['reason']


@pytest.mark.parametrize('side', ['left', 'right'])
def test_synthetic_plateau_reaching_local_edge_stays_unresolved(side):
    # Deliberately prescribed callback exercises a lost local basin, not physics.
    calls = []

    def evaluate(f):
        calls.append(f.copy())
        if len(calls) == 1:
            return 10-(f-1.5)**2
        if side == 'left':
            return 10-np.maximum(f-1.25, 0.)**2
        return 10-np.maximum(1.75-f, 0.)**2

    peak, = pipeline.extract_peaks(evaluate, SPEC, .5)
    first = calls[1]
    tops = np.flatnonzero(evaluate(first) == 10.)
    assert calls[2][0] == first[max(0, tops[0]-1)]
    assert calls[2][-1] == first[min(32, tops[-1]+1)]
    assert peak['status'] == 'unresolved'


@pytest.mark.parametrize('edge_width', [1, 2])
def test_synthetic_interior_plateau_tied_with_edge_stays_ambiguous(edge_width):
    # Prescribed first local row reproduces an equal boundary maximum. Later
    # rows have negative curvature so that the sticky ambiguity is observable.
    calls = 0

    def evaluate(f):
        nonlocal calls
        calls += 1
        values = 10-(f-97/64)**2
        if calls == 2:
            values[-edge_width:] = values[16]
        return values

    peak, = pipeline.extract_peaks(evaluate, SPEC, .5)
    assert peak['relative_curvature_hz_minus2'] < -1.e-10
    assert peak['status'] == 'unresolved'
    assert 'plusieurs maxima' in peak['reason']


@pytest.mark.parametrize('function', [
    lambda f: 10-f, lambda f: 10-np.maximum(f-.25, 0.),
    lambda f: np.full_like(f, 10.),
])
def test_synthetic_initial_edge_or_constant_has_no_invented_peak(function):
    assert pipeline.extract_peaks(function, SPEC, .5) == []


def test_synthetic_initial_plateau_detection_is_unchanged(legacy_extract):
    function = lambda f: 10-(f-1.75)**2
    assert legacy_extract(function, SPEC, .5) == []
    assert pipeline.extract_peaks(function, SPEC, .5) == []


@pytest.mark.parametrize('bad', [np.nan, np.inf])
@pytest.mark.parametrize('stage', ['grid', 'refinement', 'curvature'])
def test_synthetic_nonfinite_guards(bad, stage):
    def evaluate(f):
        sizes = {'grid': 9, 'refinement': 33, 'curvature': 1}
        return np.full(f.shape, bad) if len(f) == sizes[stage] else 10-(f-97/64)**2

    with pytest.raises(ArithmeticError, match='non fini'):
        pipeline.extract_peaks(evaluate, SPEC, .5)


@pytest.mark.parametrize('budget,expected_calls', [(8, 0), (32, 1)])
def test_synthetic_frequency_budgets_precede_allocation(budget, expected_calls):
    evaluate, calls = recorded(lambda f: 10-(f-97/64)**2)
    with pytest.raises(ValueError, match='avant allocation'):
        pipeline.extract_peaks(evaluate, SPEC, .5, max_frequencies=budget)
    assert len(calls) == expected_calls


def test_synthetic_mode_budget_unchanged():
    evaluate, calls = recorded(lambda f: 2+np.cos(2*np.pi*f))
    with pytest.raises(ArithmeticError, match='plus de 32 pics'):
        pipeline.extract_peaks(evaluate, dict(SPEC, max_hz=40.), .25)
    assert len(calls) == 1


def test_synthetic_precision_floor_retains_interval(legacy_extract):
    function = lambda f: 1+1.e-14*(10-(f-1.5)**2)
    old = legacy_extract(function, SPEC, .5)
    new = pipeline.extract_peaks(function, SPEC, .5)
    assert new == old
    peak, = new
    assert peak['floating_point_limited']
    assert peak['refinement_interval_hz'] == peak['basin_hz'] == [1., 2.]
    assert peak['status'] == 'unresolved'


@pytest.mark.parametrize('function', [
    lambda f: 10-(f-1.37123)**2,
    lambda f: 12-1.7*(f-2.13789)**2,
    lambda f: 1+3/(1+((f-1.271828)/.31)**2),
    lambda f: 2+5/(1+((f-2.314159)/.17)**2),
    lambda f: 1+2/(1+((f-1.12345)/.08)**2)+3/(1+((f-2.87321)/.12)**2),
    # Repeated valley values alone must not alter a strict maximum's path.
    lambda f: np.maximum(1., 4-3*(f-1.37123)**2),
])
def test_synthetic_strict_numerical_and_grid_parity_with_git(legacy_extract, function):
    old_callback, old_grids = recorded(function)
    new_callback, new_grids = recorded(function)
    spec = dict(SPEC, refinement_hz=1.e-4)
    old = legacy_extract(old_callback, spec, .5)
    new = pipeline.extract_peaks(new_callback, spec, .5)
    assert old and new == old  # exact fields, floats and statuses
    assert len(old_grids) == len(new_grids)
    for before, after in zip(old_grids, new_grids):
        np.testing.assert_array_equal(after, before)


@pytest.mark.parametrize('refinement', [1.e-4, 1.e-6])
def test_synthetic_unaffected_row_keeps_legacy_numerics(legacy_extract, refinement):
    # One exact two-sample top and one unrelated strict top in a vectorized batch.
    def function(f):
        return np.maximum(10-(f-97/64)**2, 10-(f-3.17231)**2)

    spec = dict(SPEC, max_hz=5., refinement_hz=refinement)
    old_callback, old_grids = recorded(function)
    new_callback, new_grids = recorded(function)
    old = legacy_extract(old_callback, spec, .5)
    new = pipeline.extract_peaks(new_callback, spec, .5)
    assert len(new) == len(old) == 2
    assert old[0]['status'] == 'unresolved' and new[0]['status'] == 'resolved'
    if refinement == 1.e-4:
        assert new[1] == old[1]
    else:
        # The existing all-row contrast check runs once more for the corrected
        # neighbour. An inactive row can acquire this flag without moving.
        assert old[1]['floating_point_limited'] is False
        assert new[1]['floating_point_limited'] is True
        assert new[1] == dict(old[1], floating_point_limited=True)
    old_local = [grid.reshape(2, 33)[1] for grid in old_grids if len(grid) == 66]
    new_local = [grid.reshape(2, 33)[1] for grid in new_grids if len(grid) == 66]
    assert len(new_local) >= len(old_local)
    for before, after in zip(old_local, new_local):
        np.testing.assert_array_equal(after, before)
    for extra in new_local[len(old_local):]:
        assert [extra[0], extra[-1]] == old[1]['refinement_interval_hz']
