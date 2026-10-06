"""Contract counterexamples; prescribed observations are not acoustic validation."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from didgeridoo_optimizer.optimization.design_contract import (
    Contract, InvalidRequest, expression, read_request, target_quantity)
from didgeridoo_optimizer.pipeline.fixed_design import load_fixed_context

ROOT=Path(__file__).resolve().parents[2]
EXAMPLE=ROOT/'project_specs/examples/constrained_design'


@pytest.fixture(scope='module')
def context():
    return load_fixed_context(EXAMPLE/'config.yaml',EXAMPLE/'design.json')


@pytest.fixture
def request_data():
    return yaml.safe_load((EXAMPLE/'request.yaml').read_text())


def geometry_criterion(ident='length', role='hard', target=1.516538):
    return dict(id=ident,observable='geometry',expression={'total_length':True},
        target={'value':target,'unit':'m'},unit='m',tolerance={'value':1.e-3,'unit':'m'},
        level='geometry',scope={},role=role)


def test_fixed_needs_no_slide_zero_preferences(context,request_data):
    request_data['variables']=[]
    c=Contract(request_data,context)
    assert c.initial==[] and c.plan()['degrees_of_freedom']==0
    assert c.generate([]).as_dict()==context['design'].as_dict()
    assert not any(r['role']=='preference' for r in c.criteria)


@pytest.mark.parametrize('x',[True,False,float('nan'),float('inf'),'3'])
def test_hidden_bool_and_nonfinite(context,request_data,x):
    request_data['variables'][0]['bounds'][0]=x
    with pytest.raises(ValueError): Contract(request_data,context)


@pytest.mark.parametrize('body,suffix',[
    ('{"a":{"b":1,"b":2}}','json'),('a:\n  b: 1\n  b: 2','yaml'),
    ('{"a":NaN}','json'),('a: .inf','yaml'),('a: &a [*a]','yaml'),
    ('a: 1e400','json'),('x: {yes: 1}','yaml')])
def test_strict_nested_parser(tmp_path,body,suffix):
    p=tmp_path/('request.'+suffix);p.write_text(body)
    with pytest.raises((ValueError,yaml.YAMLError)): read_request(p)


def test_same_bytes_fingerprint(tmp_path):
    import hashlib
    p=tmp_path/'r.json'; raw=b'{"metadata":{"bool_annotation":true}}\n';p.write_bytes(raw)
    r,source=read_request(p)
    assert r['metadata']['bool_annotation'] is True
    assert source['sha256']==hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('mutate',[
    lambda r:r.update(schema_version='unknown'),lambda r:r.update(typo=1),
    lambda r:r['variables'][0].update(unit='Hz'),
    lambda r:r['variables'][0].update(bounds=[4.1,5.]),
    lambda r:r['variables'][0]['fields'].append('segments.2.d_in_cm'),
    lambda r:r['criteria'][0].update(observable='fft_toot'),
    lambda r:r['criteria'][0].update(mode='missing'),
    lambda r:r['criteria'][0].update(tolerance={'value':.1,'unit':'mm'}),
    lambda r:r['modes'][1].update(order=1),
    lambda r:r['budgets'].update(evaluations=True),
    lambda r:r['spectrum'].update(final_step_hz=.00001),
])
def test_malformed_is_invalid_not_unsupported(context,request_data,mutate):
    mutate(request_data)
    with pytest.raises(ValueError): Contract(request_data,context)


def test_locks_every_generation_validation_annotations_and_materials(context,request_data):
    c=Contract(request_data,context)
    for factor in (.8,1.,1.2):
        raw=c.generate([x*factor for x in c.initial]).as_dict();c.check_locks(raw)
        for i in (0,5): assert raw['segments'][i]==c.base['segments'][i]
        assert raw['metadata']==c.base['metadata']
        assert [s['material_id'] for s in raw['segments']]==[s['material_id'] for s in c.base['segments']]
        broken=copy.deepcopy(raw);broken['segments'][0]['length_cm']+=.01
        with pytest.raises(ValueError,match='verrous'):c.check_locks(broken)
        broken=copy.deepcopy(raw);broken['metadata']['annotations'].append('mutation')
        with pytest.raises(ValueError,match='verrous'):c.check_locks(broken)


def test_typed_affine_ratio_and_total(context):
    raw=context['design'].as_dict()
    f={'field':'segments.0.length_cm'}
    e={'affine':[{'coefficient':2.,'expression':f}], 'offset':{'value':1.,'unit':'cm'}}
    v,dim,_=expression(e,raw)
    assert v==pytest.approx(.01+2*raw['segments'][0]['length_cm']/100) and dim=='length'
    assert expression({'ratio':[e,f]},raw)[1]=='scalar'
    with pytest.raises(ValueError):expression({'ratio':[f,{'constant':{'value':2,'unit':'Hz'}}]},raw)
    with pytest.raises(ValueError):expression({'python':'1+1'},raw)


def test_derived_is_not_new_freedom_and_cycle_refused(context,request_data):
    request_data['variables'][0]['fields']=['segments.1.d_in_cm']
    request_data['derived']=[dict(id='linked_out',field='segments.1.d_out_cm',unit='cm',bounds=[3.,5.],
                                 expression={'field':'segments.1.d_in_cm'})]
    c=Contract(request_data,context)
    assert c.plan()['degrees_of_freedom']==4 and c.plan()['derived_count']==1
    raw=c.generate([.041]+c.initial[1:]).as_dict()
    assert raw['segments'][1]['d_out_cm']==raw['segments'][1]['d_in_cm']==4.1
    request_data['variables']=request_data['variables'][1:]
    request_data['derived'].append(dict(id='cycle',field='segments.1.d_in_cm',unit='cm',bounds=[3.,5.],expression={'field':'segments.1.d_out_cm'}))
    with pytest.raises(ValueError,match='cycle'):Contract(request_data,context)


def test_direct_lock_contradiction_vs_unfinished_acoustics(context,request_data):
    request_data['criteria']=[geometry_criterion(target=9.)]
    with pytest.raises(ValueError,match='verrouillé'):Contract(request_data,context)
    request_data['criteria']=[dict(geometry_criterion(),expression={'field':'segments.1.d_in_cm'},target={'value':.04,'unit':'m'})]
    r=copy.deepcopy(request_data['criteria'][0]);r.update(id='contradiction',target={'value':.08,'unit':'m'})
    request_data['criteria'].append(r)
    with pytest.raises(ValueError,match='contradiction'):Contract(request_data,context)


def test_known_played_is_preserved_never_passive(context,request_data):
    c=request_data['criteria'][0];c.pop('mode');c.update(observable='played_frequency',level='played')
    original=copy.deepcopy(request_data)
    contract=Contract(request_data,context)
    assert contract.request==original
    assert contract.criteria[0]['unsupported_reason']
    assert contract.plan()['criteria'][0]['status']=='unsupported'


def test_notes_octave_reference_and_ratio_other_than_three(context,request_data):
    assert target_quantity({'note':'A3','reference_hz':440.,'cents':0},'frequency')==220.
    c=request_data['criteria'][1];c.pop('mode');c.update(observable='resonance_ratio',numerator='m2',denominator='m1',unit='1',target={'value':2.34,'unit':'1'})
    assert Contract(request_data,context).criteria[1]['target_si']==2.34
    with pytest.raises(ValueError):target_quantity({'note':'A','reference_hz':440.,'cents':0},'frequency')


def test_hierarchy_not_weights(context,request_data):
    c=request_data['criteria'][0];c.update(role='preference',priority=1)
    Contract(request_data,context)
    c['weight']=10
    with pytest.raises(ValueError):Contract(request_data,context)
    c.pop('priority');request_data['preference_method']='weighted'
    contract=Contract(request_data,context)
    assert not contract.preference_supported and contract.plan()['criteria'][0]['status']=='unsupported'


def test_t03_radius_uncertainty_is_not_nominal_compliance(context,request_data):
    request_data['scope']['uncertain_parameters']=[dict(id='radius_error',field='segments.1.d_in_cm',piece_id='common_piece',variation={'value':.2,'unit':'mm'})]
    # Prescribed corner observations, not new simulations; .1 mm radius is .2 mm diameter.
    prescribed=np.array([5.6545,8.0135,4.4727])
    assert np.any(prescribed>5)
    assert all(c['unsupported_reason'] for c in Contract(request_data,context).criteria)


def test_samples_do_not_cover_continuum(context,request_data):
    e=lambda s:6*np.sin(12*np.pi*s)**2
    assert np.max(e(np.linspace(0,1,13)))<1.e-20
    assert np.max(e((np.arange(12)+.5)/12))>5.99
    request_data['scope']['q']={'kind':'continuous','unit':'1','bounds':[0.,1.]}
    assert all(c['unsupported_reason'] for c in Contract(request_data,context).criteria)


def test_overlapping_targets_cannot_reuse_one_peak(context,request_data):
    second=copy.deepcopy(request_data['criteria'][0]);second['id']='almost_same'
    second['target']['value']+=.00001
    request_data['criteria'].append(second)
    with pytest.raises(ValueError,match='injective'):Contract(request_data,context)


@pytest.mark.parametrize('where,value',[('unit',{}),('role',[]),('level',{}),('scope',None)])
def test_bad_types_are_invalid_requests(context,request_data,where,value):
    request_data['criteria'][0][where]=value
    with pytest.raises(InvalidRequest):Contract(request_data,context)


@pytest.mark.parametrize('unresolved_first',[False,True])
def test_search_best_feasibility_before_evaluable_preferences(unresolved_first):
    """Prescribed helper observations only; no acoustic or physiological claim."""
    from types import SimpleNamespace
    from didgeridoo_optimizer.optimization.constrained_search import search, preference_key
    c=SimpleNamespace(budgets=dict(evaluations=4,iterations=1,seconds=5),initial=[.5],
        variables=[dict(low=0.,high=1.)],preference_supported=True,
        criteria=[dict(id='h',role='hard',unsupported_reason=None),dict(id='p',role='preference',priority=0)])
    observations=iter([(2.,.0),(.5,None if unresolved_first else .1),(.1,.9)])
    def prescribed(x):
        hard,pref=next(observations)
        return dict(search_feasible=abs(hard)<=1,criteria=[
            dict(id='h',status='satisfied' if abs(hard)<=1 else 'violated',residual_normalized=hard),
            dict(id='p',status='unresolved' if pref is None else 'satisfied',residual_normalized=pref)])
    result=search(c,prescribed)
    assert result['best']['search_feasible']
    assert result['best'] is result['history'][2 if unresolved_first else 1]
    assert preference_key(c,result['history'][0])==(0.,)  # Better preference cannot buy hard feasibility.
    if unresolved_first:assert preference_key(c,result['history'][1]) is None
