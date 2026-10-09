"""Independent finite dimensional arithmetic and immutable physical identities."""
import copy
import json
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from didgeridoo_optimizer.geometry.assemblies import Assembly, InvalidAssembly
from didgeridoo_optimizer.geometry.tolerance_scenarios import (
    ScenarioUnavailable, check_mask, parse_scenarios, perturb,
)
from didgeridoo_optimizer.materials.database import MaterialDatabase
from didgeridoo_optimizer.optimization.design_contract import InvalidRequest, read_request
from didgeridoo_optimizer.pipeline.design_input import validate_design


@pytest.fixture
def db():
    return MaterialDatabase.from_yaml(Path(__file__).parents[2] / 'project_specs/materials_base_v1.yaml')


def fixed():
    return {'id': 'fixed', 'metadata': {'note': 'unchanged', 'total_length_cm': 60.},
            'segments': [{'kind': 'cylinder', 'length_cm': 60., 'd_in_cm': 3.,
                          'd_out_cm': 3., 'material_id': 'pvc_pressure', 'profile_params': {},
                          'position_start_cm': 0., 'position_end_cm': 60.}]}


def q(value, unit='m'):
    return {'value': value, 'unit': unit}


def assembly():
    return {'schema_version': 'dcalc.assembly.v1', 'id': 'shared',
        'annotations': {'source': 'synthetic'},
        'pieces': {
            'inner': {'kind': 'tube', 'material_id': 'pvc_pressure',
                      'length': q(60, 'cm'), 'inner_diameter': q(30, 'mm'), 'outer_diameter': q(.032)},
            'outer': {'kind': 'tube', 'material_id': 'pvc_pressure',
                      'length': q(.5), 'inner_diameter': q(.04), 'wall': q(.002)}},
        'blocks': [{'id': 'slide', 'kind': 'telescope', 'inner': 'inner', 'outer': 'outer',
                    'orientation': 'outer_first', 'seal': 'inner_tip_isolated', 'min_overlap': q(.08)}],
        'configurations': {'short': {'q': {'slide': q(.05)}}, 'long': {'q': {'slide': q(.4)}}}}


def job(fields=None, delta=None):
    return {'uncertainties': [{'id': 'size', 'fields': fields or ['segments.0.length_cm'],
                              'delta': delta or q(2, 'mm'),
                              'origin': 'Scénario synthétique prescrit, sans qualification industrielle.'}],
            'scenarios': [{'id': 'nominal', 'coefficients': {'size': 0}},
                          {'id': 'minus', 'coefficients': {'size': -1}},
                          {'id': 'plus', 'coefficients': {'size': 1}}]}


def test_fixed_plus_minus_nominal_and_independent_arithmetic(db):
    raw = fixed(); original = copy.deepcopy(raw)
    parsed = parse_scenarios(job(), 'fixed', raw)
    nominal, minus, plus = [perturb(raw, parsed, s, 'fixed') for s in parsed['scenarios']]
    assert json.dumps(nominal) == json.dumps(original)
    assert nominal is not raw and nominal['segments'][0] is not raw['segments'][0]
    assert minus['segments'][0]['length_cm'] == pytest.approx(59.8, abs=1e-12)
    assert plus['segments'][0]['length_cm'] == pytest.approx(60.2, abs=1e-12)
    assert raw == original
    for changed in (minus, plus):
        for key in ('d_in_cm', 'd_out_cm', 'profile_params', 'kind', 'material_id'):
            assert changed['segments'][0][key] == raw['segments'][0][key]
        native = validate_design(changed, db, {}).as_dict()
        assert native['segments'][0]['position_end_cm'] == native['segments'][0]['length_cm']
        check_mask(raw, native, parsed, 'fixed')


@pytest.mark.parametrize('delta', [q(.002), q(.2, 'cm'), q(2, 'mm')])
def test_explicit_units_same_physical_shift(delta):
    raw = fixed(); parsed = parse_scenarios(job(delta=delta), 'fixed', raw)
    assert perturb(raw, parsed, parsed['scenarios'][2], 'fixed')['segments'][0]['length_cm'] == pytest.approx(60.2)


def test_cylinder_ends_linked_same_shift_without_retuning(db):
    raw = fixed(); fields = ['segments.0.d_in_cm', 'segments.0.d_out_cm']
    native = SimpleNamespace(base=raw, variables=[{'id': 'old_bore', 'fields': fields, 'low': .0299, 'high': .0301}], derived=[])
    parsed = parse_scenarios(job(fields, q(.5, 'mm')), 'fixed', raw, native)
    altered = perturb(raw, parsed, parsed['scenarios'][2], 'fixed')
    assert altered['segments'][0]['d_in_cm'] == altered['segments'][0]['d_out_cm'] == pytest.approx(3.05)
    assert altered['segments'][0]['length_cm'] == 60.
    # The manufacturing deviation deliberately lies beyond the former search box.
    assert validate_design(altered, db, {}).segments[0].d_in_cm / 100 > native.variables[0]['high']


def test_linked_native_fields_cannot_use_separate_uncertainties():
    raw = fixed(); fields = ['segments.0.d_in_cm', 'segments.0.d_out_cm']
    c = SimpleNamespace(base=raw, variables=[{'id': 'bore', 'fields': fields}], derived=[])
    with pytest.raises(InvalidRequest, match='liaison'):
        parse_scenarios(job([fields[0]]), 'fixed', raw, c)
    data = job([fields[0]])
    other = copy.deepcopy(data['uncertainties'][0]); other.update(id='other', fields=[fields[1]])
    data['uncertainties'].append(other)
    for scenario in data['scenarios']:
        scenario['coefficients']['other'] = scenario['coefficients']['size']
    with pytest.raises(InvalidRequest, match='liaison'):
        parse_scenarios(data, 'fixed', raw, c)


def test_derived_relation_checked_not_repaired():
    raw = fixed(); fields = ['segments.0.d_in_cm', 'segments.0.d_out_cm']
    c = SimpleNamespace(base=raw, variables=[], derived=[{'id': 'equal_ends', 'field': fields[1],
                        'expression': {'field': fields[0]}, 'low': .0299, 'high': .0301}])
    parse_scenarios(job(fields), 'fixed', raw, c)
    with pytest.raises(InvalidRequest, match='relation dérivée incompatible'):
        parse_scenarios(job([fields[0]]), 'fixed', raw, c)
    assert raw == fixed()


def test_assembly_stable_piece_common_to_two_configurations_and_q_constant(db):
    raw = assembly(); original = copy.deepcopy(raw)
    parsed = parse_scenarios(job(['pieces.inner.length']), 'assembly', raw)
    modified = perturb(raw, parsed, parsed['scenarios'][2], 'assembly')
    assert modified['configurations'] == original['configurations']
    generated = Assembly(modified, db, {})
    short, long = generated.generate('short'), generated.generate('long')
    assert short['deployed_length_m'] == pytest.approx(.652)
    assert long['deployed_length_m'] == pytest.approx(1.002)
    assert short['bom'] == long['bom']
    assert short['positions'][0]['q_m'] == .05
    assert long['positions'][0]['q_m'] == .4
    assert raw == original
    assert perturb(raw, parsed, parsed['scenarios'][0], 'assembly') == original


def test_group_different_native_units_uses_identical_exact_shift(db):
    raw = assembly()
    raw['pieces']['inner']['outer_diameter'] = q(3.2, 'cm')
    parsed = parse_scenarios(job(['pieces.inner.inner_diameter', 'pieces.inner.outer_diameter'], q(.3, 'mm')), 'assembly', raw)
    modified = perturb(raw, parsed, parsed['scenarios'][2], 'assembly')
    inner = modified['pieces']['inner']
    # Native setter may change notation; an independent rational conversion
    # checks the two deltas and preserved physical wall thickness.
    factors = {'m': Fraction(1), 'cm': Fraction(1, 100), 'mm': Fraction(1, 1000)}
    a = Fraction(str(inner['inner_diameter']['value'])) * factors[inner['inner_diameter']['unit']]
    b = Fraction(str(inner['outer_diameter']['value'])) * factors[inner['outer_diameter']['unit']]
    assert a == Fraction(303, 10000) and b == Fraction(323, 10000)
    assert b - a == Fraction(2, 1000)
    Assembly(modified, db, {}).generate('short')


def test_mechanical_counterexample_is_not_repaired(db):
    raw = assembly()
    parsed = parse_scenarios(job(['pieces.inner.outer_diameter'], q(10, 'mm')), 'assembly', raw)
    modified = perturb(raw, parsed, parsed['scenarios'][2], 'assembly')
    assert modified['pieces']['inner']['outer_diameter'] == q(.042)
    with pytest.raises(InvalidAssembly, match='géométrie incompatible'):
        Assembly(modified, db, {}).generate('short')


@pytest.mark.parametrize('path', ['configurations.long.q.slide', 'blocks.slide.min_overlap',
    'pieces.inner.material_id', 'pieces.0.length', 'pieces.outer.profile_params.power',
    'pieces.inner.stock.length', 'segments.0.length_cm'])
def test_assembly_rejects_q_unstable_and_nonphysical_fields(path):
    with pytest.raises(ValueError):
        parse_scenarios(job([path]), 'assembly', assembly())


@pytest.mark.parametrize('path', ['segments.00.length_cm', 'segments.1.length_cm',
    'segments.0.position_end_cm', 'segments.0.material_id', 'metadata.total_length_cm',
    'segments.0.profile_params.power', 'segments.0.profile_params.throat_diameter_cm'])
def test_fixed_rejects_nonphysical_ineffective_and_absent_fields(path):
    raw = fixed(); raw['segments'][0]['profile_params'].update(power=2, throat_diameter_cm=3)
    with pytest.raises(ValueError):
        parse_scenarios(job([path]), 'fixed', raw)


@pytest.mark.parametrize('bad', [True, False, float('nan'), float('inf'), -float('inf'), 1.01, -1.01, '1'])
def test_coefficients_are_explicit_bounded_finite_reals(bad):
    data = job(); data['scenarios'][2]['coefficients']['size'] = bad
    with pytest.raises(ValueError): parse_scenarios(data, 'fixed', fixed())


@pytest.mark.parametrize('change', [
    lambda j: j['scenarios'][2]['coefficients'].clear(),
    lambda j: j['scenarios'][2]['coefficients'].update(extra=0),
    lambda j: j['scenarios'][2].update(id='nominal'),
    lambda j: j['scenarios'][2]['coefficients'].update(size=-1),
    lambda j: j['scenarios'].pop(0),
    lambda j: j['uncertainties'][0]['fields'].append('segments.0.length_cm'),
    lambda j: j['uncertainties'][0].update(origin=' '),
    lambda j: j['uncertainties'][0].update(delta=q(0)),
    lambda j: j['uncertainties'][0].update(delta=q(True)),
    lambda j: j['uncertainties'][0].update(delta=q(2, 'Hz')),
    lambda j: j['uncertainties'][0].update(unknown='no'),
])
def test_malformed_scenario_rejected_before_perturb(change):
    data = job(); change(data)
    with pytest.raises(ValueError): parse_scenarios(data, 'fixed', fixed())


def test_cycles_and_limits_before_product_allocation():
    data = job(); data['loop'] = data
    with pytest.raises(ValueError, match='cycle|cyclic'): parse_scenarios(data, 'fixed', fixed())
    data = job(); data['uncertainties'] *= 9
    with pytest.raises(ValueError, match='1..8'): parse_scenarios(data, 'fixed', fixed())
    data = job(); data['scenarios'] *= 22
    with pytest.raises(ValueError, match='1..65'): parse_scenarios(data, 'fixed', fixed())
    data = job(); data['uncertainties'][0]['fields'] *= 65
    with pytest.raises(ValueError, match='64 champs'): parse_scenarios(data, 'fixed', fixed())


def test_independent_lock_mask_keeps_annotations_q_and_unlisted_dimensions():
    for kind, raw, field, corrupt in [
        ('fixed', fixed(), 'segments.0.length_cm', lambda r: r['metadata'].update(note='changed')),
        ('fixed', fixed(), 'segments.0.length_cm', lambda r: r['segments'][0].update(d_in_cm=3.1)),
        ('assembly', assembly(), 'pieces.inner.length', lambda r: r['configurations']['long']['q'].update(slide=q(.3))),
        ('assembly', assembly(), 'pieces.inner.length', lambda r: r['annotations'].update(source='changed')),
    ]:
        parsed = parse_scenarios(job([field]), kind, raw)
        effective = perturb(raw, parsed, parsed['scenarios'][2], kind)
        corrupt(effective)
        with pytest.raises(ValueError, match='masque'): check_mask(raw, effective, parsed, kind)


def test_stable_fixed_piece_segment_id_without_acoustic_slice_index(db):
    raw = {'schema_version': 'dcalc.assembly.v1', 'id': 'body',
           'pieces': {'wood': {'kind': 'fixed', 'material_id': 'pvc_pressure', 'segments': [
               {'id': 'bore', 'kind': 'cylinder', 'length': q(.6),
                'diameter_in': q(.03), 'diameter_out': q(.03)}]}},
           'blocks': [{'id': 'body', 'kind': 'fixed', 'piece': 'wood'}],
           'configurations': {'nominal': {}}}
    parsed = parse_scenarios(job(['pieces.wood.segments.bore.length']), 'assembly', raw)
    changed = perturb(raw, parsed, parsed['scenarios'][1], 'assembly')
    assert Assembly(changed, db, {}).generate('nominal')['deployed_length_m'] == pytest.approx(.598)
    with pytest.raises(ValueError):
        parse_scenarios(job(['pieces.wood.segments.0.length']), 'assembly', raw)


def test_native_reader_rejects_duplicate_job_keys_and_yaml_alias_cycle(tmp_path):
    duplicate = tmp_path / 'duplicate.json'; duplicate.write_text('{"uncertainties": [], "uncertainties": []}')
    cycle = tmp_path / 'cycle.yaml'; cycle.write_text('uncertainties: &u [*u]\nscenarios: []\n')
    for path in (duplicate, cycle):
        with pytest.raises(ValueError): read_request(path)


def test_untouched_dimensionless_derived_parameter_remains_native():
    raw = fixed(); raw['segments'][0].update(kind='flare_powerlaw', profile_params={'power': 2.})
    c = SimpleNamespace(base=raw, variables=[], derived=[{'id': 'power_constant',
        'field': 'segments.0.profile_params.power', 'expression': {'constant': q(2, '1')}}])
    parsed = parse_scenarios(job(), 'fixed', raw, c)
    changed = perturb(raw, parsed, parsed['scenarios'][2], 'fixed')
    assert changed['segments'][0]['profile_params']['power'] == 2.


def test_assembly_derived_q_stays_constant_and_incompatible_link_is_rejected():
    raw = assembly()
    constant = {'id': 'q_constant', 'field': 'configurations.short.q.slide',
                'expression': {'constant': q(.05)}}
    c = SimpleNamespace(base=raw, variables=[], derived=[constant])
    parse_scenarios(job(['pieces.inner.length']), 'assembly', raw, c)
    constant['expression'] = {'affine': [{'coefficient': 1,
        'expression': {'field': 'pieces.inner.length'}}], 'offset': q(-.55)}
    with pytest.raises(InvalidRequest, match='relation dérivée incompatible'):
        parse_scenarios(job(['pieces.inner.length']), 'assembly', raw, c)



def test_unrepresentable_exact_shift_is_unavailable_not_physical_failure():
    raw = assembly(); original = copy.deepcopy(raw)
    data = job(['pieces.inner.length'], q(1, 'mm'))
    data['scenarios'][2]['coefficients']['size'] = .3333333333333333
    # Independent rational oracle: none of the native m/cm/mm float notations
    # represents this prescribed decimal result exactly.
    expected = Fraction(3, 5) + Fraction('0.3333333333333333') / 1000
    for factor in (Fraction(1), Fraction(1, 100), Fraction(1, 1000)):
        assert Fraction(str(float(expected / factor))) * factor != expected
    parsed = parse_scenarios(data, 'assembly', raw)
    assert perturb(raw, parsed, parsed['scenarios'][0], 'assembly') == original
    minus = perturb(raw, parsed, parsed['scenarios'][1], 'assembly')
    assert minus['pieces']['inner']['length'] == q(.599)
    with pytest.raises(ScenarioUnavailable, match='non représentable') as failure:
        perturb(raw, parsed, parsed['scenarios'][2], 'assembly')
    assert not isinstance(failure.value, InvalidAssembly)
    assert raw == original


def test_unavailable_scenario_does_not_hide_other_derived_incompatibility():
    raw = assembly()
    data = job(['pieces.inner.length'], q(1, 'mm'))
    data['scenarios'][1]['coefficients']['size'] = .3333333333333333
    constant = {'id': 'q_constant', 'field': 'configurations.short.q.slide',
                'expression': {'constant': q(.05)}}
    contract = SimpleNamespace(base=raw, variables=[], derived=[constant])
    parsed = parse_scenarios(data, 'assembly', raw, contract)
    with pytest.raises(ScenarioUnavailable):
        perturb(raw, parsed, parsed['scenarios'][1], 'assembly')
    constant['expression'] = {'affine': [{'coefficient': 1,
        'expression': {'field': 'pieces.inner.length'}}], 'offset': q(-.55)}
    # The unrepresentable middle scenario is skipped as unresolved. The next
    # representable scenario still proves this declared link incompatible.
    with pytest.raises(InvalidRequest, match='relation dérivée incompatible'):
        parse_scenarios(data, 'assembly', raw, contract)
