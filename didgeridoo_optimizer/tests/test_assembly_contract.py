"""Shared locks and native R41 semantics; no new cent or acoustic model."""
import copy
import json
from pathlib import Path

import pytest
import yaml

from didgeridoo_optimizer.geometry.assemblies import Assembly, field_info, set_field
from didgeridoo_optimizer.optimization.assembly_contract import AssemblyContract, physical_expression
from didgeridoo_optimizer.optimization.design_contract import read_request
from didgeridoo_optimizer.pipeline.assembly_path import load_inputs
from didgeridoo_optimizer.pipeline.fixed_design import load_analysis_context, load_fixed_context
from didgeridoo_optimizer.tests.test_assemblies import fixed, q

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT/'project_specs/examples/assembly_path'


@pytest.fixture
def inputs():
    context = load_analysis_context(EXAMPLE/'config.yaml')
    raw = yaml.safe_load((EXAMPLE/'assembly.yaml').read_text())
    request = yaml.safe_load((EXAMPLE/'request.yaml').read_text())
    return context, raw, request


def make(inputs):
    context, raw, request = inputs
    return AssemblyContract(request, Assembly(raw, context['material_db'], context['config']), context)


def geometry_request(target=.6, role='hard'):
    base = yaml.safe_load((EXAMPLE/'request.yaml').read_text())['projections'][0]['request']
    base['criteria'] = [dict(id='length', observable='geometry', expression={'total_length':True},
        target=q(target), unit='m', tolerance=q(.001), level='geometry', scope={}, role=role)]
    return base


def fixed_inputs():
    context = load_analysis_context(EXAMPLE/'config.yaml')
    raw = fixed()
    request = dict(schema_version='dcalc.assembly_request.v1', variables=[],
        budgets=yaml.safe_load((EXAMPLE/'request.yaml').read_text())['budgets'],
        projections=[dict(id='nominal', configuration='nominal', request=geometry_request())])
    return context, raw, request


def test_fixed_complete_without_q_and_zero_preferences():
    c = make(fixed_inputs())
    assert c.initial == [] and c.plan()['degrees_of_freedom'] == 0
    assert c.generate([], c.catalogue[0]).raw == c.base
    assert not any(r['role'] == 'preference' for r in c.criteria)


def test_shared_piece_every_configuration_immutable_parent(inputs):
    original = copy.deepcopy(inputs[2]); c = make(inputs)
    a = c.generate([.555, .41], c.catalogue[-1])
    assert a.generate('short')['bom'] == a.generate('long')['bom']
    assert a.field_info('pieces.inner.length')[0] == .555
    assert c.request == original
    detached = c.request; detached['metadata'] = {'mutation':True}
    assert c.request == original
    inputs[2]['variables'].clear()
    assert len(c.variables) == 2
    broken = a.raw; broken['pieces']['inner']['outer_diameter']['value'] += .1
    with pytest.raises(ValueError, match='verrous'):
        c.check_locks(broken, c.catalogue[-1])
    broken = a.raw; broken['configurations']['short']['q']['slide']['value'] += .001
    with pytest.raises(ValueError, match='verrous'):
        c.check_locks(broken, c.catalogue[-1])


def test_linked_positions_and_derived_not_extra_freedom(inputs):
    context, raw, request = inputs
    raw['configurations']['middle'] = {'q':{'slide':q(.4)}}
    middle = copy.deepcopy(request['projections'][1]); middle.update(id='middle', configuration='middle')
    request['projections'].append(middle)
    request['variables'][1]['fields'].append('configurations.middle.q.slide')
    request['derived'] = [dict(id='diameter_link', field='pieces.prefix.segments.bore.diameter_out',
        unit='m', bounds=[.02,.05], expression={'field':'pieces.prefix.segments.bore.diameter_in'})]
    c = make(inputs); a = c.generate([.555,.415], c.catalogue[-1])
    assert a.field_info('configurations.middle.q.slide')[0] == .415
    assert len(c.variables) == 2 and len(c.derived) == 1
    broken = a.raw; set_field(broken, 'configurations.middle.q.slide', .416)
    with pytest.raises(ValueError, match='liaison'):
        c.check_locks(broken, c.catalogue[-1])


@pytest.mark.parametrize('bad', [True, False, float('nan'), float('inf'), '0.3'])
def test_invalid_bound_types(inputs, bad):
    inputs[2]['variables'][0]['bounds'][0] = bad
    with pytest.raises(ValueError): make(inputs)


@pytest.mark.parametrize('mutate', [
    lambda r:r.update(schema_version='unknown'), lambda r:r.update(unknown_scope={}),
    lambda r:r['variables'][0].update(unit='Hz'),
    lambda r:r['projections'][0]['request'].update(scope={'typo':'ignored'}),
    lambda r:r['projections'][0]['request']['criteria'][0].update(observable='passive_frequency'),
    lambda r:r['projections'][0]['request']['criteria'][0].update(mode='unknown'),
    lambda r:r['projections'][0]['request']['criteria'][0].update(tolerance=q(.1,'mm')),
    lambda r:r.update(coverage={'kind':'continuous','path':'absent'}),
    lambda r:r['projections'].pop(),
    lambda r:r['catalogue'][0]['values'].update({'pieces.inner.length':q(.57)}),
])
def test_strict_semantic_rejection(inputs, mutate):
    mutate(inputs[2])
    with pytest.raises((ValueError, TypeError)): make(inputs)


def test_played_and_uncertainty_preserved_unsupported(inputs):
    request = inputs[2]['projections'][0]['request']
    played = copy.deepcopy(request['criteria'][0]); played.pop('mode')
    played.update(id='played', observable='played_frequency', level='played', role='observe')
    request['criteria'].append(played)
    request['scope'] = {'uncertain_parameters':[dict(id='radius', field='segments.0.d_in_cm',
                                                   piece_id='prefix', variation=q(.1,'mm'))]}
    c = make(inputs)
    assert c.request == inputs[2]
    assert all(x['unsupported_reason'] for x in c.criteria if x['projection'] == 'short')
    assert c.criteria[-1]['unsupported_reason'] is None


def test_continuum_never_rewrites_r41_scopes(inputs):
    inputs[2]['coverage'] = {'kind':'continuous','path':'travel'}
    c = make(inputs)
    assert c.request == inputs[2]
    assert c.coverage['kind'] == 'continuous'
    assert all(p['request']['criteria'][0]['scope'] == {} for p in c.projections)


def test_global_lexicographic_priorities_are_unique(inputs):
    for p in inputs[2]['projections']:
        p['request']['criteria'][0].update(role='preference', priority=1)
    with pytest.raises(ValueError, match='priorités globales'): make(inputs)
    inputs[2]['projections'][1]['request']['criteria'][0]['priority'] = 2
    assert make(inputs).preference_supported


def test_catalogue_retains_rejected_geometry(inputs):
    c = make(inputs)
    for alternative in c.catalogue[:2]:
        with pytest.raises(ValueError, match='géométrie incompatible'):
            c.generate(c.initial, alternative).generate('short')
    assert c.generate(c.initial, c.catalogue[-1]).generate('short')['geometry_certificate']['geometry_certified']


def test_geometry_hard_candidate_violation_is_not_parser_error():
    inputs = fixed_inputs(); inputs[2]['projections'][0]['request'] = geometry_request(.7)
    c = make(inputs)
    assert c.criteria[0]['role'] == 'hard'
    assert c.criteria[0]['lower_si'] == pytest.approx(.699)


def test_native_config_extraction_has_exact_historical_parity(tmp_path):
    example = ROOT/'project_specs/examples/constrained_design'
    old = load_fixed_context(example/'config.yaml', example/'design.json')
    new = load_analysis_context(example/'config.yaml')
    for key in new:
        if key == 'provenance':
            assert new[key]['software'] == old[key]['software']
            assert new[key]['files'] == {k:v for k,v in old[key]['files'].items() if k != 'design'}
        elif key == 'material_db':
            assert new[key].raw == old[key].raw
        else:
            assert new[key] == old[key]
    assert 'design' not in new and 'design' not in new['provenance']['files']


@pytest.mark.parametrize('body,suffix', [('a: 1\na: 2','yaml'), ('{"a":NaN}','json'),
    ('x: &x [*x]','yaml'), ('a: .inf','yaml'), ('{"a":1,"a":2}','json')])
def test_duplicate_alias_nonfinite_strict(tmp_path, body, suffix):
    path = tmp_path/('request.'+suffix); path.write_text(body)
    with pytest.raises(ValueError): read_request(path)


def test_linked_units_canonical_si_without_floating_tolerance(inputs):
    context,raw,request=inputs
    raw['pieces']['inner']['length']=q(.56)
    raw['pieces']['prefix']['segments'][0]['length']=q(560,'mm')
    request['variables'][0]['fields'].append('pieces.prefix.segments.bore.length')
    request['variables'][0]['bounds']=[.3,.59]
    c=make(inputs);candidate=c.generate([.32507059835717234,.4],c.catalogue[-1])
    assert candidate.field_info('pieces.inner.length')[0]==candidate.field_info('pieces.prefix.segments.bore.length')[0]
    assert candidate.field_info('pieces.inner.length')[0]==.32507059835717234
    assert c.base['pieces']['prefix']['segments'][0]['length']['unit']=='mm'


def test_native_hard_geometry_contradiction_not_masked():
    data=fixed_inputs();request=data[2]['projections'][0]['request']
    request['criteria']=[geometry_request(.7)['criteria'][0],dict(geometry_request(.8)['criteria'][0],id='incompatible')]
    with pytest.raises(ValueError,match='contradiction directe'):make(data)


@pytest.mark.parametrize('unit,scale', [('m',1),('cm',100),('mm',1000)])
def test_decimal_affine_relation_accepts_exact_sum_and_rejects_neighbour(unit, scale):
    from fractions import Fraction
    data=fixed_inputs();raw,request=data[1:]
    raw['pieces']['body']['segments'][0]['length']=q(.3*scale,unit)
    raw['annotations']={'nominal':'0.1 + 0.2 m'}
    request['metadata']={'notation':'somme décimale nominale'}
    request['projections'][0]['request']=geometry_request(.7)
    request['derived']=[dict(id='decimal',field='pieces.body.segments.first.length',
        unit=unit,bounds=[.2*scale,.4*scale],expression=dict(
            affine=[dict(coefficient=1,expression={'constant':q(.1*scale,unit)})],
            offset=q(.2*scale,unit)))]
    original=copy.deepcopy(data[1:]);c=make(data)
    assert physical_expression(request['derived'][0]['expression'],raw)[0] == Fraction(3,10)
    assert c.plan()['degrees_of_freedom'] == 0 and len(c.derived) == 1
    assert c.generate([],c.catalogue[0]).raw == original[0]
    assert c.base == original[0] and c.request == original[1] and data[1:] == original
    raw['pieces']['body']['segments'][0]['length']=q(.30000000000000004)
    with pytest.raises(ValueError,match='relation dérivée'):make(data)
    altered=c.generate([],c.catalogue[0]).raw
    altered['pieces']['body']['segments'][0]['length']=q(.30000000000000004)
    with pytest.raises(ValueError,match='relation physique'):c.check_locks(altered)


def test_decimal_chained_relations_linked_units_bounds_and_locked_fields():
    data=fixed_inputs();raw,request=data[1:];segments=raw['pieces']['body']['segments']
    segments[0]['length']=q(10,'cm');segments[1]['length']=q(300,'mm')
    third=copy.deepcopy(segments[0]);third.update(id='third',length=q(.35))
    fourth=copy.deepcopy(segments[0]);fourth.update(id='fourth',length=q(100,'mm'))
    segments.extend([third,fourth]);request['projections'][0]['request']=geometry_request(.85)
    prefix='pieces.body.segments.'
    request['variables']=[dict(id='shared',fields=[prefix+'first.length',prefix+'fourth.length'],
        unit='cm',bounds=[10,20])]
    # Deliberately reverse declaration order; evaluation follows dependencies.
    request['derived']=[dict(id='third',field=prefix+'third.length',unit='mm',bounds=[350,450],
        expression=dict(affine=[dict(coefficient=1,expression={'field':prefix+'bell.length'})],offset=q(5,'cm'))),
        dict(id='bell',field=prefix+'bell.length',unit='m',bounds=[.3,.4],
        expression=dict(affine=[dict(coefficient=1,expression={'field':prefix+'first.length'})],offset=q(.2)))]
    c=make(data);a=c.generate([.2],c.catalogue[0]);c.check_locks(a.raw)
    assert c.plan()['degrees_of_freedom']==1 and len(c.derived)==2
    assert [field_info(a.raw,prefix+name+'.length')[0] for name in ('first','bell','third','fourth')]==[.2,.4,.45,.2]
    assert c.base==raw and c.request==request
    altered=a.raw;altered['pieces']['body']['segments'][3]['length']=q(.20000000000000004)
    with pytest.raises(ValueError,match='liaison'):c.check_locks(altered)
    with pytest.raises(ValueError,match='hors bornes'):c.generate([.20000000000000004],c.catalogue[0])
    altered=a.raw;altered['pieces']['body']['segments'][0]['diameter_in']=q(.031)
    with pytest.raises(ValueError,match='verrous'):c.check_locks(altered)


@pytest.mark.parametrize('coefficient,offset,expected',[(.1,.2,.23),(-.1,.2,.17)])
def test_affine_decimal_coefficients_are_exact(coefficient,offset,expected):
    from fractions import Fraction
    expr=dict(affine=[dict(coefficient=coefficient,expression={'constant':q(.3)})],offset=q(offset))
    assert physical_expression(expr,{})[:2] == (Fraction(str(expected)),'length')
