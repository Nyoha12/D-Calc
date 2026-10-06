"""Physical geometry checks independent of the acoustic search adapter."""
import copy
from fractions import Fraction
import json
import math
from pathlib import Path

import pytest

from didgeridoo_optimizer.geometry.assemblies import (
    Assembly, InvalidAssembly, field_info, read_assembly, set_field)
from didgeridoo_optimizer.materials.database import MaterialDatabase
from didgeridoo_optimizer.pipeline.design_input import validate_design


@pytest.fixture
def db():
    return MaterialDatabase.from_yaml(Path(__file__).parents[2]/'project_specs/materials_base_v1.yaml')


def q(value,unit='m'):
    return {'value':value,'unit':unit}


def specimen():
    return {'schema_version':'dcalc.assembly.v1','id':'reference',
        'pieces':{
            'prefix':{'kind':'fixed','material_id':'pvc_pressure','segments':[
                {'id':'bore','kind':'cylinder','length':q(.03),'diameter_in':q(30,'mm'),'diameter_out':q(3,'cm')}]},
            'inner':{'kind':'tube','material_id':'pvc_pressure','length':q(.550895109027624),
                'inner_diameter':q(.03),'outer_diameter':q(.032)},
            'outer':{'kind':'tube','material_id':'pvc_pressure','length':q(.5),
                'inner_diameter':q(.04),'wall':q(.0015)}},
        'blocks':[{'id':'mouth','kind':'fixed','piece':'prefix'},
            {'id':'slide','kind':'telescope','inner':'inner','outer':'outer','orientation':'outer_first',
             'seal':'inner_tip_isolated','min_overlap':q(.08)}],
        'configurations':{'short':{'q':{'slide':q(.01)}},'long':{'q':{'slide':q(.4190399207919836)}}},
        'paths':[{'id':'travel','kind':'affine','from':'short','to':'long'}]}


def fixed():
    return {'schema_version':'dcalc.assembly.v1','id':'fixed',
        'pieces':{'body':{'kind':'fixed','material_id':'pvc_pressure','segments':[
            {'id':'first','kind':'cylinder','length':q(.2),'diameter_in':q(.03),'diameter_out':q(.03)},
            {'id':'bell','kind':'flare_powerlaw','length':q(.4),'diameter_in':q(.03),'diameter_out':q(.08),
             'profile_params':{'power':2.25}}]}},
        'blocks':[{'id':'body','kind':'fixed','piece':'body'}],
        'configurations':{'nominal':{}}}


def test_fixed_without_any_slide_field_preserves_native_profiles(db):
    raw = fixed()
    result = Assembly(raw,db,{}).generate('nominal')
    assert result['positions'] == []
    assert result['design'].segments[1].kind == 'flare_powerlaw'
    assert result['design'].segments[1].profile_params == {'power':2.25}
    assert result['terminal_radius_m'] == .04
    assert len(result['bom']) == 1
    assert result['bom'][0]['mass_kg'] is None
    assert result['lumen_volume_method'].endswith('approximation')
    assert result['spans'][1]['segment_id'] == 'bell'
    assert result['design'].total_length_cm == 60


def test_r42_geometry_independent_lengths_volume_and_stock(db):
    raw = specimen()
    a = Assembly(raw,db,{})
    short,long = a.generate('short'),a.generate('long')
    assert short['deployed_length_m'] == pytest.approx(.590895109027624,abs=1e-15)
    assert long['deployed_length_m'] == pytest.approx(.9999350298196076,abs=1e-15)
    assert long['positions'][0]['overlap_m'] == pytest.approx(.0809600792080164,abs=1e-16)
    assert short['positions'][0]['overlap_m'] == .49
    expected = math.pi/4*(.03**2*(.03+.550895109027624)+.04**2*.4190399207919836)
    assert long['lumen_volume_m3'] == pytest.approx(expected,rel=2e-15)
    tubes = [r for r in long['bom'] if r['kind']=='tube']
    assert sum(r['material_volume_m3'] for r in tubes) == pytest.approx(.00015143340019299408,rel=2e-15)
    assert short['bom'] == long['bom']
    assert next(r for r in short['bom'] if r['piece_id']=='outer')['stock_length_m'] == .5
    assert all(r['mass_kg'] is None for r in short['bom'])
    assert len({r['piece_id'] for r in short['bom']}) == 3
    assert short['profile_sha256'] != long['profile_sha256']
    assert short['design'].metadata['assembly_sha256'] == a.sha256


@pytest.mark.parametrize('orientation,order,terminal',[('outer_first',['prefix','outer','inner'],.015),
                                                      ('inner_first',['prefix','inner','outer'],.02)])
def test_two_directions_exact_physical_terminal(db,orientation,order,terminal):
    raw=specimen();raw['blocks'][1]['orientation']=orientation
    result=Assembly(raw,db,{}).generate('short')
    assert [s['piece_id'] for s in result['spans']]==order
    assert result['terminal_radius_m']==terminal
    assert result['positions'][0]['inner_end_m'] <= result['deployed_length_m']
    assert result['positions'][0]['outer_end_m'] <= result['deployed_length_m']


@pytest.mark.parametrize('orientation',['outer_first','inner_first'])
def test_q_zero_omits_only_exposed_segment_keeps_piece_and_stable_ids(db,orientation):
    raw=specimen();raw['blocks'][1]['orientation']=orientation
    raw['configurations']['short']['q']['slide']=q(0)
    result=Assembly(raw,db,{}).generate('short')
    assert [s['piece_id'] for s in result['spans']]==['prefix','inner']
    assert len(result['bom'])==3
    assert result['terminal_radius_m']==.015
    assert [s['segment_id'] for s in result['spans']]==['bore','lumen']
    assert all(s.length_cm > 0 for s in result['design'].segments)
    assert result['positions'][0]['q_m']==0


def test_rational_equality_accepted_but_one_decimal_step_outside_rejected(db):
    raw=specimen();raw['configurations']['long']['q']['slide']=q(.42)
    a=Assembly(raw,db,{})
    assert a.generate('long')['geometry_certificate']['margins_m_exact']['slide']['minimum_overlap']=='0'
    raw['configurations']['long']['q']['slide']=q(.42000000000000004)
    with pytest.raises(InvalidAssembly,match='géométrie incompatible'):
        Assembly(raw,db,{}).generate('long')


def test_invalid_catalogue_geometry_retained_until_generation(db):
    raw=specimen();raw['pieces']['inner']['length']=q(.5542056038975713)
    raw['pieces']['outer']['length']=q(.5679669850319623)
    raw['pieces']['outer']['inner_diameter']=q(.036)
    raw['configurations']['long']['q']['slide']=q(.4879669850319623)
    a=Assembly(raw,db,{})
    with pytest.raises(InvalidAssembly,match='inner_stock'):
        a.generate('short')


@pytest.mark.parametrize('mutation',[
    lambda r:r['pieces']['inner'].update(outer_diameter=q(.04)),
    lambda r:r['pieces']['inner'].update(outer_diameter=q(.03)),
    lambda r:r['pieces']['outer'].update(length=q(.7)),
])
def test_overlap_wall_collision_rejected_without_repair(db,mutation):
    raw=specimen();mutation(raw);a=Assembly(raw,db,{})
    with pytest.raises(InvalidAssembly):a.generate('short')
    assert a.raw==raw


@pytest.mark.parametrize('seal',['outer_end','communicating','unknown'])
def test_annulus_other_seal_not_serialized_as_series(db,seal):
    raw=specimen();raw['blocks'][1]['seal']=seal
    with pytest.raises(InvalidAssembly,match='annulus'):
        Assembly(raw,db,{})


def test_double_insertion_is_explicitly_unsupported(db):
    raw=specimen();second=copy.deepcopy(raw['blocks'][1]);second['id']='second';raw['blocks'].append(second)
    with pytest.raises(InvalidAssembly,match='double emploi/insertion'):
        Assembly(raw,db,{})


@pytest.mark.parametrize('bad',[True,float('nan'),float('inf'),-1,0])
def test_length_bool_nonfinite_nonpositive_rejected(db,bad):
    raw=specimen();raw['pieces']['inner']['length']=q(bad)
    with pytest.raises(ValueError):Assembly(raw,db,{})


@pytest.mark.parametrize('bad',[{'value':1,'unit':'inch'},{'value':1,'unit':'Hz'}, {'value':1,'unit':'m','hidden':0}])
def test_units_strict(db,bad):
    raw=specimen();raw['pieces']['inner']['length']=bad
    with pytest.raises(ValueError):Assembly(raw,db,{})


def test_duplicate_yaml_keys_rejected_before_mapping_collapse(tmp_path):
    path=tmp_path/'assembly.yaml';path.write_text('schema_version: dcalc.assembly.v1\nid: first\nid: second\n')
    with pytest.raises(ValueError,match='duplicate key'):read_assembly(path)


def test_unknown_scope_and_boolean_q_rejected(db):
    raw=specimen();raw['configurations']['short']['q']['slide']=q(True)
    with pytest.raises(ValueError):Assembly(raw,db,{})
    raw=specimen();raw['configurations']['short']['q']['unknown']=q(0)
    with pytest.raises(ValueError):Assembly(raw,db,{})


def test_immutability_aliases_and_stable_physical_variable_references(db):
    raw=specimen();a=Assembly(raw,db,{})
    raw['pieces']['inner']['length']['value']=99
    assert a.field_info('pieces.inner.length')[0] == .550895109027624
    altered=a.raw
    set_field(altered,'pieces.inner.length',.56)
    set_field(altered,'configurations.long.q.slide',.4)
    assert a.field_info('pieces.inner.length')[0] == .550895109027624
    b=Assembly(altered,db,{})
    assert b.generate('short')['bom']==b.generate('long')['bom']
    assert b.field_info('pieces.prefix.segments.bore.diameter_in')==(.03,'length',.001)
    with pytest.raises(ValueError):field_info(altered,'segments.0.length')
    # Acyclic YAML aliases are detached, so a variable on one piece cannot
    # silently alter a different physical identity.
    raw=fixed();segment=raw['pieces']['body']['segments'][0]
    raw['pieces']['other']={'kind':'fixed','material_id':'pvc_pressure','segments':[segment]}
    raw['blocks'].append({'id':'other','kind':'fixed','piece':'other'})
    a=Assembly(raw,db,{});altered=a.raw
    set_field(altered,'pieces.other.segments.first.length',.3)
    assert field_info(altered,'pieces.body.segments.first.length')[0]==.2


def test_exact_affine_certificate_has_no_acoustic_claim(db):
    a=Assembly(specimen(),db,{})
    cert=a.certify_paths()[0]
    assert cert['geometry_certified'] is True
    assert cert['acoustics_certified'] is False
    for endpoint in cert['endpoint_checks']:
        assert all(Fraction(v)>=0 for v in endpoint['margins_m_exact']['slide'].values())
    # Independent affine-polynomial oracle: a linear margin equals its endpoint
    # convex combination at every rational t, without a sampled proof claim.
    margins=[e['margins_m_exact']['slide'] for e in cert['endpoint_checks']]
    for key in margins[0]:
        a0,a1=Fraction(margins[0][key]),Fraction(margins[1][key])
        assert min(a0,a1)>=0
        for t in [Fraction(1,7),Fraction(4,9)]:
            assert min(a0,a1)<=(1-t)*a0+t*a1<=max(a0,a1)


def test_design_reload_uses_native_validator(db):
    result=Assembly(specimen(),db,{}).generate('long')
    reloaded=validate_design(json.loads(json.dumps(result['design'].as_dict())),db,{})
    assert reloaded.as_dict()==result['design'].as_dict()


def test_two_independent_telescope_blocks_have_shared_position_coordinates(db):
    raw=specimen()
    raw['pieces']['inner2']=copy.deepcopy(raw['pieces']['inner'])
    raw['pieces']['outer2']=copy.deepcopy(raw['pieces']['outer'])
    block=copy.deepcopy(raw['blocks'][1]);block.update(id='slide2',inner='inner2',outer='outer2')
    raw['blocks'].append(block)
    for cfg in raw['configurations'].values():cfg['q']['slide2']=q(.02)
    result=Assembly(raw,db,{}).generate('short')
    first,second=result['positions']
    assert second['outer_start_m']==pytest.approx(.03+first['deployed_m'])
    assert len(result['bom'])==5
    assert second['inner_end_m']==result['deployed_length_m']


def test_fixed_stock_and_conical_volume(db):
    raw=fixed();piece=raw['pieces']['body']
    piece['segments']=piece['segments'][1:]
    piece['segments'][0]['kind']='cone';piece['segments'][0]['profile_params']={}
    piece['stock']={'length':q(.4),'outer_diameter':q(.09)}
    result=Assembly(raw,db,{}).generate('nominal')
    expected=math.pi*.4/12*(.03**2+.03*.08+.08**2)
    assert result['lumen_volume_m3']==pytest.approx(expected,rel=1e-15)
    assert result['bom'][0]['material_volume_m3']==pytest.approx(math.pi*.09**2*.4/4-expected)
    piece['stock']['length']=q(.39)
    with pytest.raises(InvalidAssembly,match='stock insuffisant'):
        Assembly(raw,db,{}).generate('nominal')
