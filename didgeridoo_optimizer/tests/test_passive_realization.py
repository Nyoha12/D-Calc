"""Independent scalar/native oracles and strict certificate failure cases."""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as certs
from didgeridoo_optimizer.nonlinear.passive_realization import convert, real_blocks, source_response, POLICY, RealizationRefused
from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters

ROOT=Path(__file__).resolve().parents[2]
EXAMPLES=ROOT/'project_specs/examples/passive_realization'
FIXTURES=Path(__file__).parent/'fixtures/passive_realization'


def inputs(name):
    source=(EXAMPLES/(name+'.json')).read_bytes()
    path=EXAMPLES/(name+'-certificate.json')
    return source,path.read_bytes() if path.exists() else None


def encoded(data): return json.dumps(data,allow_nan=False).encode()


@pytest.mark.parametrize('name,value',[('zero',0.),('resistance',12.)])
def test_analytic_constants_exact_native(name,value):
    r=convert(*inputs(name)); m=r['model']
    assert type(m) is PassiveState and m.state_count==0
    assert m.feedthrough_pa_s_m3==m.dc_pa_s_m3==value
    assert r['diagnostics']['method']=='analytic_constant'
    assert r['diagnostics']['dc']['relative']==0
    for u in [0.,1e-5,-1e-5]:
        assert m.step(u)==value*u
        assert m.energy()==0
    assert m.parameters()['provenance']['source_passivity']=='not_assessed'


def test_negative_residue_relaxation_signs_and_discrete_mapping():
    r=convert(*inputs('relaxation'));m=r['model'];f=np.array([0.,1.,23.,500.,3000.,5999.])
    np.testing.assert_allclose(m.continuous_response(f),1000-150000/(200+2j*np.pi*f),rtol=2e-13)
    s=2j*12000*np.tan(np.pi*f/12000)
    np.testing.assert_allclose(m.discrete_response(f),1000-150000/(200+s),rtol=2e-13)
    assert m.dc_pa_s_m3==pytest.approx(250.) and m.feedthrough_pa_s_m3==pytest.approx(1000.)
    assert r['diagnostics']['selected_storage']['delta']>0
    assert m.A[0,0]==pytest.approx(-1.,abs=1e-14)  # original pole, not -0.9
    assert m.B[0]*m.C[0]<0


def test_modal_positive_direct_oracle_native_pressure_energy_balance():
    r=convert(*inputs('modal'));m=r['model']
    oracle=PassiveResonator([2e5],[70.],[420.],R0=10.,sample_rate_hz=12000,
        dc_origin={'kind':'explicit','description':'Synthetic positive-direct modal oracle'})
    f=np.array([0.,1.,67.,100.,3000.,5999.])
    np.testing.assert_allclose(m.discrete_response(f),oracle.discrete_response(f),rtol=4e-13)
    for u in np.sin(np.arange(120)*.3)*1e-5:
        old=m.energy();p=m.step(u)
        assert p==pytest.approx(oracle.step(u),rel=4e-11,abs=1e-14)
        lost=m.last_dissipation_w/12000;work=p*u/12000
        scale=max(old,m.energy(),abs(work),lost)
        defect=abs(m.energy()-old-work+lost)
        assert (defect/scale if scale else defect)<2e-10
    # Interior storage need not equal modal stored energy: pressure is equivalent.


def test_mixed_blocks_exact_residue_convention_and_no_mutation():
    raw,proof=inputs('mixed');c=certs.parse_json(raw);before=copy.deepcopy(c)
    A,B,C,D,scales=real_blocks(c)
    f=np.array([0.,10.,67.,4000.]);s=2j*np.pi*f/c['wref']
    actual=np.array([c['zscale']*(D+C@np.linalg.solve(z*np.eye(len(B))-A,B)) for z in s])
    np.testing.assert_allclose(actual,source_response(c,f,discrete=False),rtol=3e-14)
    result=convert(raw,proof,frequency_hz=f)
    assert c==before and result['source']==before
    assert result['model'].state_count==3 and len(scales)==2


@pytest.mark.parametrize('mutation',['truncated','gap','overlap','lower','upper','tail','minimum','dc','hash','bool','extra','rational','attribution'])
def test_certificate_recomputed_not_status_or_hash(mutation):
    raw,proof=inputs('relaxation');c=certs.parse_json(raw);p=certs.parse_json(proof)
    if mutation=='truncated':p['leaves']=[]
    if mutation=='gap':p['leaves'][0]['lo']=['1','1']
    if mutation=='overlap':p['leaves'].append(p['leaves'][0].copy())
    if mutation=='lower':p['leaves'][0]['lower_units']+=1
    if mutation=='upper':p['leaves'][0]['upper_units']-=1
    if mutation=='tail':p['tail']['lower']=['1','1']
    if mutation=='minimum':p['global_lower_units']+=1
    if mutation=='dc':p['dc_units'][0]+=1
    if mutation=='hash':p['coefficient_sha256']='0'*64
    if mutation=='bool':p['order']=True
    if mutation=='extra':p['accepted']=True
    if mutation=='rational':p['tail']['start']=['08','2']
    if mutation=='attribution':p['physical_validation']='validated'
    with pytest.raises(ValueError):certs.verify_certificate(c,p,certs.sha(raw))


def test_stable_active_source_cannot_borrow_certificate():
    raw,proof=inputs('relaxation');c=certs.parse_json(raw);p=certs.parse_json(proof)
    c['residues']=[-2.];raw=encoded(c);p['coefficient_sha256']=certs.sha(raw)
    assert source_response(c,[0])[0].real<0
    with pytest.raises(ValueError):convert(raw,encoded(p))


def test_near_singular_refused_without_loss_or_residue_repair():
    raw,proof=inputs('near-singular');before=(raw,proof)
    with pytest.raises(RealizationRefused,match='Discrete asymptotic stability') as caught:convert(raw,proof)
    assert caught.value.diagnostics['storage_attempts'][-1]['status']=='selected'
    assert inputs('near-singular')==before


def test_marginal_general_direct_is_outside_domain_not_called_active():
    raw,_=inputs('modal');c=certs.parse_json(raw);c['direct_normalized']=0.
    with pytest.raises(RealizationRefused,match='Outside general domain'):convert(encoded(c))


@pytest.mark.parametrize('change',[{'order':True},{'residues':[True]},{'p_imag':[-1.]},
    {'zscale':0},{'sample_rate_hz':True},{'extra':1},{'provenance':[]},
    {'p_real':[-1.]*129},{'residues':[1.]*129},{'passivity':'accepted'}])
def test_strict_source_types_shapes_and_attribution(change):
    c=certs.parse_json(inputs('relaxation')[0]);c.update(change)
    with pytest.raises(ValueError):convert(encoded(c))


@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":1e999}',b'['*1000+b']'*1000,b' '* (4*1024**2+1)])
def test_bounded_json_duplicate_nan_depth_size(raw):
    with pytest.raises(ValueError):certs.parse_json(raw)


def test_arbitrary_input_not_coerced():
    class Trap:
        def __bytes__(self):raise AssertionError('coerced')
    with pytest.raises(ValueError):convert(Trap())


@pytest.mark.parametrize('case',['A','B'])
def test_frozen_originals_and_acquired_r59_control_grids(case):
    manifest=certs.read_json(FIXTURES/'manifest.json')
    for name in (case+'-coefficients.json',case+'-certificate.json',case+'-control-grid.npz'):
        assert hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest()==manifest['files'][name]['sha256']
    raw=(FIXTURES/(case+'-coefficients.json')).read_bytes();proof=(FIXTURES/(case+'-certificate.json')).read_bytes()
    with np.load(FIXTURES/(case+'-control-grid.npz'),allow_pickle=False) as audit:
        f=np.concatenate([audit[k] for k in audit.files if k.endswith('_f')])
        result=convert(raw,proof,frequency_hz=f);m=result['model']
        for name in ('low','interior','guard','upper','edge'):
            expected=audit[name+'_model'];actual=m.discrete_response(audit[name+'_f'])
            assert np.max(abs(actual-expected)/abs(expected))<POLICY['response_maxrel']
    assert m.state_count=={'A':70,'B':106}[case]
    assert result['source']['passivity']=='not_assessed'
    assert result['diagnostics']['grid']['extrapolation_count']>0


def test_true_simultaneous_coupling_and_checkpoint_resume():
    r=convert(*inputs('mixed'));m=r['model'];params=DimensionedLipParameters()
    a=SimultaneousCoupling(m,params=params,rho=1.204,v2_port_model='conjugate')
    for _ in range(100):
        d=a.step();assert d['ok'],d
        assert d['total_energy_residual']['normalized']<2e-10
    cp=json.loads(json.dumps(a.export_checkpoint()))
    b=SimultaneousCoupling(m,params=params,rho=1.204,v2_port_model='conjugate');b.import_checkpoint(cp)
    for _ in range(16):assert a.step()==b.step()
    assert m.energy()==0
