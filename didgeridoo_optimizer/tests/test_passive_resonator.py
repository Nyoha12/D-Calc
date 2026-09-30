"""Independent state-space/DTFT and energy oracles; dated R29 coefficients."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator, digest
from didgeridoo_optimizer.nonlinear.passive_fit import audit, DEFAULT_GATES

DC = dict(kind='imposed_reference',description='Analytical single mode has exact zero DC')
FIXTURES = Path(__file__).parent/'fixtures/td_pass_01'


def modal(fs=12000):
    w=2*np.pi*70; g=w/8
    return PassiveResonator([1e7*g],[g],[w],R0=0.,sample_rate_hz=fs,dc_origin=DC)


def test_modal_continuous_and_midpoint_independent_matrix():
    model=modal(); f=np.array([0.,30.,70.,110.,1000.])
    w=2*np.pi*70; g=w/8; a=1e7*g
    s=2j*np.pi*f
    np.testing.assert_allclose(model.continuous_response(f),a*s/(s*s+g*s+w*w))
    dt=1/model.sample_rate_hz
    A=np.array([[0.,1.],[-w*w,-g]]); b=np.array([0.,1.]); c=np.array([0.,a])
    step=np.linalg.solve(np.eye(2)-dt*A/2,np.eye(2)+dt*A/2)
    gain=np.linalg.solve(np.eye(2)-dt*A/2,dt*b)
    state=np.zeros(2)
    rng=np.random.default_rng(123)
    for u in rng.normal(0,1e-5,400):
        new=step@state+gain*u
        expected=c@((state+new)/2)
        assert model.step(u)==pytest.approx(expected,rel=2e-12,abs=2e-13)
        np.testing.assert_allclose(np.r_[model.state[0],model.state[1]],new,rtol=3e-12,atol=1e-18)
        state=new


def test_energy_forced_and_relaxation_independent_instances():
    a=modal(); b=modal(); rng=np.random.default_rng(67)
    work=loss=0.
    for i in range(1600):
        u=float(rng.normal(0,1e-5)) if i<800 else 0.
        old=a.energy(); p=a.step(u); new=a.energy()
        expected=(p*u-a.last_dissipation_w)/a.sample_rate_hz
        assert abs(new-old-expected)<=3e-13*max(abs(old),abs(new),abs(expected),1e-30)
        work+=p*u/a.sample_rate_hz; loss+=a.last_dissipation_w/a.sample_rate_hz
        if i>=800: assert new<=old
    assert work==pytest.approx(a.energy()+loss,rel=1e-12)
    assert b.energy()==0 and not np.any(b.state[0])
    a.reset(); assert a.energy()==0
    x=a.a; x[:]=0; assert a.a[0]>0
    assert not hasattr(a,'impulse_kernel')


def test_impulse_dtft_matches_discrete_response_without_changing_state():
    model=modal(4000); model.step(1e-7); before=model.state
    h=model.impulse_response(5000); f=np.array([0.,70.,100.,500.,1500.])
    dtft=np.exp(-2j*np.pi*f[:,None]*np.arange(len(h))/4000)@h
    np.testing.assert_allclose(dtft,model.discrete_response(f),rtol=2e-12,atol=1e-7)
    for x,y in zip(before,model.state): np.testing.assert_array_equal(x,y)
    flow=np.r_[1e-6,2e-6,np.zeros(30)]
    np.testing.assert_allclose(model.pressure_from_flow(flow),np.convolve(flow,h)[:len(flow)],rtol=1e-13,atol=1e-12)


@pytest.mark.parametrize('field,value',[('a',[-1]),('a',[True]),('gamma',[0]),('omega',[float('inf')]),('sample_rate_hz',True),('R0',float('nan')),('R0',-1),('domain','unknown'),('dc_origin',{})])
def test_invalid_coefficients_refused(field,value):
    args=dict(a=[1.],gamma=[2.],omega=[3.],R0=0.,sample_rate_hz=4000,dc_origin=DC)
    args[field]=value
    with pytest.raises(ValueError):PassiveResonator(**args)


def test_duplicates_nonfinite_flows_and_frequency_domain():
    with pytest.raises(ValueError,match='Duplicate'):
        PassiveResonator([1,2],[2,2],[3,3],R0=0,sample_rate_hz=4000,dc_origin=DC)
    m=modal()
    for x in [True,complex(1),float('inf')]:
        with pytest.raises(ValueError):m.step(x)
    for f in [[-1],[6000],[True]]:
        with pytest.raises(ValueError):m.discrete_response(f)


def test_strict_save_reload_identity_and_mutation(tmp_path):
    path=tmp_path/'model.json'; m=modal(); m.save(path)
    hashed=hashlib.sha256(path.read_bytes()).hexdigest()
    loaded=PassiveResonator.load(path,expected_sha256=hashed)
    assert loaded.parameters()==m.parameters()
    np.testing.assert_array_equal(loaded.pressure_from_flow([1e-6,0,0]),m.pressure_from_flow([1e-6,0,0]))
    with pytest.raises(FileExistsError):m.save(path)
    path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError,match='changed'):loaded.pressure_from_flow([0])
    with pytest.raises(ValueError,match='changed'):PassiveResonator.load(path,expected_sha256=hashed)


@pytest.mark.parametrize('mutation',['units','domain','negative','duplicate','nonfinite','unknown'])
def test_model_refusals_even_with_recomputed_content_digest(tmp_path,mutation):
    p=modal().parameters()
    if mutation=='units':p['units']['a']='kg'
    if mutation=='domain':p['domain']='physical_modes'
    if mutation=='negative':p['a']=[-1]
    if mutation=='unknown':p['code']='eval(1)'
    path=tmp_path/'bad.json'
    text=json.dumps(dict(parameters=p,sha256=digest(p)))
    if mutation=='duplicate':text=text.replace('"R0": 0.0','"R0": 0.0, "R0": 0.0')
    if mutation=='nonfinite':text=text.replace('"R0": 0.0','"R0": NaN')
    path.write_text(text)
    with pytest.raises(ValueError):PassiveResonator.load(path)


@pytest.mark.parametrize('case',['cylinder_zk','exponential_zk'])
def test_both_r29_fixtures_reconstruct_exact_fresh_audit(case,tmp_path):
    manifest=json.loads((FIXTURES/'manifest.json').read_text())
    entry=manifest['cases'][case]; path=FIXTURES/entry['file']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
    with np.load(path,allow_pickle=False) as data:
        model=PassiveResonator(data['a'],data['gamma'],data['omega'],R0=float(data['R0']),sample_rate_hz=int(data['fs']),
            dc_origin=dict(kind='zk_local_1d',description=entry['R0_origin']),domain='discrete_prewarped')
        assert len(model.a)==entry['reference_active_terms']
        got=audit(model,data['frequency_hz'],data['target'],fit_frequencies=data['fit_f'],gates=DEFAULT_GATES)
        assert got['status']=='accepted'
        for key in DEFAULT_GATES:
            assert got['metrics'][key]==pytest.approx(entry['expected'][key],rel=2e-11,abs=1e-13)
        np.testing.assert_allclose(model.discrete_response(data['frequency_hz']),data['model'],rtol=1e-14,atol=1e-7)
        model.save(tmp_path/'replica.json'); replica=PassiveResonator.load(tmp_path/'replica.json')
        np.testing.assert_array_equal(replica.discrete_response(data['frequency_hz']),model.discrete_response(data['frequency_hz']))


@pytest.mark.parametrize('case',['cylinder_zk','exponential_zk'])
def test_r29_targets_match_frozen_native_tmm_on_entire_reserved_grid(case):
    from didgeridoo_optimizer.pipeline.fixed_design import load_fixed_context
    from didgeridoo_optimizer.pipeline.design_pitch import models
    from didgeridoo_optimizer.geometry import GeometryDiscretizer
    from didgeridoo_optimizer.acoustics.transfer_matrix import input_impedance
    root=Path(__file__).resolve().parents[2]
    manifest=json.loads((FIXTURES/'manifest.json').read_text())
    entry=manifest['cases'][case]
    context=load_fixed_context(root/'project_specs/examples/design_pitch/config.yaml',root/entry['design_file'])
    for key in ('materials','variant_rules','design'):
        assert context['provenance']['files'][key]['sha256']==entry['inputs'][key]['sha256']
    air,loss,rad,effective=models(context,dict(loss_model='zk',radiation_model='legacy',air_reference='ck_dry20'))
    assert effective==entry['effective']
    mesh=GeometryDiscretizer().discretize(context['design'],max_segment_cm=entry['mesh_max_cm'])
    with np.load(FIXTURES/entry['file'],allow_pickle=False) as data:
        actual=input_impedance(data['frequency_hz'],mesh,context['material_db'],air,
            exit_radius_m=context['design'].segments[-1].d_out_cm/200,loss_model=loss,radiation_model=rad)
        np.testing.assert_allclose(actual,data['target'],rtol=3e-13,atol=1e-7)


def test_loaded_response_refuses_changed_file(tmp_path):
    path=tmp_path/'m.json';modal().save(path);m=PassiveResonator.load(path)
    path.write_text(path.read_text()+' ')
    for method in (m.continuous_response,m.discrete_response):
        with pytest.raises(ValueError,match='changed'):method([70])
