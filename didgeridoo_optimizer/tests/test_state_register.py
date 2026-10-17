"""Native state experiments and finite evidence; no private runtime artifacts."""
import copy
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import warnings
import zipfile

import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear import register_target as core
from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
from didgeridoo_optimizer.nonlinear.passive_resonator import digest
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
from didgeridoo_optimizer.reporting import state_register_context as evidence
from didgeridoo_optimizer.tests.test_passive_state import relaxation, modal_pair, ORIGIN
from didgeridoo_optimizer.tests.test_register_target import request, two_plateaus, model as modal_model

BASE = 'cb1efcec1eb70adb25706ea1f218fd75c4425fb6'
ROOT = Path(__file__).resolve().parents[2]


def state_model(kind, fs=4000):
    if kind == 'relaxation': return relaxation(fs)
    if kind == 'modal': return modal_pair(fs)[1]
    return PassiveState([],[],[[0. if kind == 'zero' else math.sqrt(1000.)]],
        omega_ref=1.,Zscale=1.,sample_rate_hz=fs,coefficient_origin=ORIGIN)


def state_experiment(kind='relaxation', r=None):
    r = two_plateaus() if r is None else r
    m = state_model(kind,core.sample_rate(r))
    return core.Experiment(m,r,source=dict(kind='synthetic',description=ORIGIN,
                                         model_sha256=digest(m.parameters())))


@pytest.mark.parametrize('kind',['zero','resistance','relaxation','modal'])
def test_state_full_dimension_native_step_and_energy(kind):
    r = request(steps=32); e = state_experiment(kind,r)
    native = SimultaneousCoupling(state_model(kind),params=core.active_parameters(r,0),
                                  rho=r['rho_kg_m3'],v2_port_model='conjugate')
    result = e.advance(32)
    assert result['ok'],result['reason']
    assert result['arrays']['states'].shape == (33,2+e.model.state_count)
    assert not hasattr(e.model,'a') and not hasattr(e.model,'gamma') and not hasattr(e.model,'omega')
    assert e.initial['payload']['schema'] == 'dcalc.simultaneous.passive_state.checkpoint.v1'
    sums = {k:0. for k in core.SUM_FIELDS}
    for i in range(32):
        d = native.step(); assert d['ok'],d
        assert np.array_equal(result['arrays']['states'][i+1],native.state)
        assert np.array_equal(result['arrays']['values'][i],[d[k] for k in core.COLUMNS])
        assert result['arrays']['times'][i+1] == native.time_s
        assert d['total_energy_residual']['normalized'] <= 2e-10
        for k in sums: sums[k] += d[k]
    assert e.sums == sums and e.coupling.export_checkpoint() == native.export_checkpoint()
    assert e.model.energy() == 0  # SimultaneousCoupling owns its separate native state.


@pytest.mark.parametrize('kind',['zero','resistance','relaxation','modal'])
@pytest.mark.parametrize('split',[0,39,40,41,79,80])
def test_state_resume_exact_across_control_boundary(kind,split):
    full = state_experiment(kind); whole = full.advance(80)
    assert whole['ok'],whole['reason']
    part = state_experiment(kind); first = part.advance(split); cp = part.checkpoint()
    resumed = state_experiment(kind); resumed.restore(cp)
    assert resumed.checkpoint() == cp
    last = resumed.advance(80-split)
    assert last['ok'],last['reason']
    assert resumed.checkpoint() == full.checkpoint()
    assert np.array_equal(np.vstack((first['arrays']['states'],last['arrays']['states'][1:])),whole['arrays']['states'])
    assert len(resumed.events) == 1
    event = resumed.events[0]
    before, after = event['before']['payload']['snapshot'],event['after']['payload']['snapshot']
    for k in ('state','time_s','last_dissipation_w'): assert before[k] == after[k]
    assert event['lip_energy_after_j']-event['lip_energy_before_j'] == pytest.approx(event['command_work_j'],abs=1e-18)
    assert event['pressure_impulse_work_j'] == 0
    assert event['damping_stored_energy_change_j'] == 0
    assert cp['payload']['historical_source_steps'] == 0


@pytest.mark.parametrize('mutation',['shape','native_schema','model_identity','loss','clock','source','ledger','event'])
def test_state_checkpoint_semantic_refusal_atomic(mutation):
    e = state_experiment(); assert e.advance(42)['ok']
    before = e.checkpoint(); bad = copy.deepcopy(before); p = bad['payload']
    n = p['native']['payload']
    if mutation == 'shape': n['snapshot']['state'].append(0.)
    if mutation == 'native_schema': n['schema']='dcalc.simultaneous.checkpoint.v1'
    if mutation == 'model_identity':
        # The outer identity cannot make a different native model admissible.
        p['source']['model_sha256']='0'*64
    if mutation == 'loss': n['snapshot']['last_dissipation_w']=-1.
    if mutation == 'clock': n['snapshot']['time_s'] += .001
    if mutation == 'source': p['source']['description']='A different acquisition'
    if mutation == 'ledger': p['sums']['source_work_j'] += .01
    if mutation == 'event': p['events'][0]['command_work_j'] += .01
    p['native']['sha256']=digest(n);bad['sha256']=digest(p)
    with pytest.raises(ValueError): e.restore(bad)
    assert e.checkpoint() == before


def test_state_stop_quota_and_failed_native_step_keep_last_acceptance(monkeypatch):
    e = state_experiment(); result = e.advance(80,stop=lambda:e.accepted==41)
    assert result['ok'] is False and e.accepted == 41
    assert result['arrays']['states'].shape == (42,3)
    cp = e.checkpoint()
    def refused(self,flow):
        self._x = np.ones_like(self._x)
        raise ValueError('test native refusal')
    monkeypatch.setattr(PassiveState,'step',refused)
    result = e.advance(1)
    assert result['ok'] is False and e.checkpoint() == cp
    with pytest.raises(ValueError,match='Cumulative quota'): e.advance(40)
    assert e.checkpoint() == cp


def test_state_full_phase_dimensions_and_model_fs_refused():
    r = request(steps=64)
    r['observation']['phase']=dict(train=[0.,.004],validations=[[.004,.016]],
        scales=[1.]*4,state_units=['m','m/s','m^3/s','m^3/s'],
        section_index=0,section_level=0.,section_method='linear',groups=[1,2],min_crossings=8)
    with pytest.raises(ValueError,match='Complete phase'): state_experiment('relaxation',r)
    m = state_model('relaxation',12000)
    with pytest.raises(ValueError,match='fs'):
        core.Experiment(m,request(steps=32),source=dict(kind='synthetic',description=ORIGIN,model_sha256=digest(m.parameters())))


@pytest.fixture(scope='module')
def genuine_base(tmp_path_factory):
    """Load the actual immutable Git blob, never a rewritten baseline oracle."""
    path = tmp_path_factory.mktemp('state-register-base')/'register_target.py'
    result = subprocess.run(['git','show',BASE+':didgeridoo_optimizer/nonlinear/register_target.py'],
                            cwd=ROOT,capture_output=True,check=True,timeout=10)
    assert result.stdout and b'class Experiment' in result.stdout
    path.write_bytes(result.stdout)
    name = 'didgeridoo_optimizer.nonlinear._state_register_genuine_base'
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec);sys.modules[name]=module
    try:
        spec.loader.exec_module(module)
        yield module
    finally: sys.modules.pop(name,None)


@pytest.mark.parametrize('kind',['synthetic','saved'])
@pytest.mark.parametrize('split',[0,40,41])
def test_modal_bitwise_against_actual_canonical_source(genuine_base,kind,split):
    r = two_plateaus()
    if kind == 'saved':
        r['case']=dict(kind='saved',id='instrument',config='config.json',design='design.json',model_in='model.json',
            recipe=dict(sample_rate_hz=4000,h_cm=.5,loss_model='zk',radiation_model='legacy',air_reference='ck_dry20',
                R0=None,dc_origin=None,basis_completion='r29',fit_min_hz=40.,fit_max_hz=1000.,fit_points=4096,
                guard_max_hz=1500.,guard_points=768,audit_points=1536))
    source = dict(kind='synthetic' if kind=='synthetic' else 'verified_saved',description='Same canonical modal coefficients',
                  model_sha256=digest(modal_model().parameters()))
    current = core.Experiment(modal_model(),r,source=source)
    baseline = genuine_base.Experiment(modal_model(),r,source=source)
    for count in (split,80-split):
        a,b = current.advance(count),baseline.advance(count)
        assert a['ok'] and b['ok']
        for key in a['arrays']: assert np.array_equal(a['arrays'][key],b['arrays'][key]),key
        assert current.checkpoint() == baseline.checkpoint()
    restarted = core.Experiment(modal_model(),r,source=source)
    restarted.restore(baseline.checkpoint())
    assert restarted.checkpoint() == baseline.checkpoint()


def test_criteria_targets_do_not_enter_frequency_observation():
    r = request();r['criteria']=[dict(id='pitch',role='hard',observable='played_frequency',windows=['hold'],
        target=dict(value=70.,unit='Hz'),tolerance=dict(value=5.,unit='cent'))]
    fs=4000;t=np.arange(fs+1)/fs;tm=(np.arange(fs)+.5)/fs
    kwargs=dict(times=t,states=np.zeros((fs+1,3)),midpoint_times=tm,pressure=np.sin(2*np.pi*70*tm),
                source=dict(kind='synthetic',description='Analytic signal independent of targets'))
    first=core.analyze(r,**kwargs);r['criteria'][0]['target']['value']=210.
    second=core.analyze(r,**kwargs)
    assert first['windows'] == second['windows']
    assert first['criteria'][0]['status']=='pass' and second['criteria'][0]['status']=='fail'
    short=dict(times=t[:11],states=kwargs['states'][:11],midpoint_times=tm[:10],pressure=kwargs['pressure'][:10],source=kwargs['source'])
    unresolved=core.analyze(r,**short)
    assert unresolved['windows']['hold']['frequency_hz'] is None
    assert unresolved['criteria'][0]['status']=='unresolved'


def _sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_fixture(tmp_path, *, extrapolation=False):
    """Small explicitly synthetic targets; no claimed instrument acquisition."""
    source='1'*64;domain=[10.,700.]
    m=PassiveState([],[],[[math.sqrt(1000.)]],omega_ref=1.,Zscale=1.,sample_rate_hz=4000,
        coefficient_origin=ORIGIN,domain='discrete_prewarped',provenance=dict(source_sha256=source,training_domain_hz=domain))
    np.savez(tmp_path/'training.npz',frequency_hz=np.array([10.,100.,400.,700.]),real=np.full(4,1000.),imag=np.zeros(4))
    data=dict(inside_f=np.array([20.,200.,500.,600.]),inside_target=np.full(4,1000.+0j))
    bands=[dict(id='inside',role='audit',domain_hz=domain,points=4)]
    if extrapolation:
        data.update(edge_f=np.array([750.,850.]),edge_target=np.full(2,10000.+0j))
        bands.append(dict(id='edge',role='extrapolation',domain_hz=[700.,900.],points=2))
    np.savez(tmp_path/'audit.npz',**data)
    manifest=dict(schema=evidence.SCHEMA,inputs={k:'2'*64 for k in ('config','design','materials','variant_rules')},
        recipe=dict(sample_rate_hz=4000,h_cm=.5,loss_model='zk',radiation_model='legacy',air_reference='ck_dry20'),
        effective=dict(air=dict(rho=1.2),original_air=dict(rho=1.2),air_substitution='synthetic',loss_model='synthetic',radiation=dict(name='synthetic')),
        physical_radius_m=.03,source_sha256=source,model_sha256=digest(m.parameters()),domain_hz=domain,
        coefficient_domain='discrete_prewarped',acquisition=dict(nature='synthetic',provenance=ORIGIN,date='2026-10-17',sha='3'*40),
        gates=dict(evidence.DEFAULT_GATES),bands=bands,
        training=dict(file='training.npz',sha256=_sha(tmp_path/'training.npz'),points=4),
        audit=dict(file='audit.npz',sha256=_sha(tmp_path/'audit.npz')))
    path=tmp_path/'evidence.json';path.write_text(json.dumps(manifest));return path,manifest,m


def test_evidence_inspection_has_no_science_or_array_load(tmp_path,monkeypatch):
    path,manifest,_=evidence_fixture(tmp_path)
    def no(*a,**kw): pytest.fail('Scientific work/allocation in evidence inspection')
    monkeypatch.setattr(np,'load',no);monkeypatch.setattr(np,'empty',no)
    monkeypatch.setattr(np.linalg,'eigvals',no);monkeypatch.setattr(PassiveState,'__init__',no)
    actual,files=evidence.inspect_evidence(path)
    assert actual==manifest and set(files)=={'manifest','training','audit'}


@pytest.mark.parametrize('mutation',['extra','units','gate','nature','sha','zero_radius','band_duplicate','band_gap','domain','training_cap','audit_cap','points_cap','path_escape'])
def test_evidence_bad_manifest_refused_before_npz_allocation(tmp_path,monkeypatch,mutation):
    path,m,_=evidence_fixture(tmp_path)
    if mutation=='extra':m['accepted']=True
    if mutation=='units':m['units']={'frequency':'kHz'}
    if mutation=='gate':m['gates']['relative_max']=.8
    if mutation=='nature':m['acquisition']['nature']='accepted'
    if mutation=='sha':m['source_sha256']='wrong'
    if mutation=='zero_radius':m['physical_radius_m']=0.
    if mutation=='band_duplicate':m['bands']*=2
    if mutation=='band_gap':m['bands'][0]['domain_hz']=[11.,700.]
    if mutation=='domain':m['coefficient_domain']='arbitrary'
    if mutation=='training_cap':m['training']['points']=8193
    if mutation=='audit_cap':m['bands'][0]['points']=4097
    if mutation=='points_cap':
        m['training']['points']=8192;m['bands']=[dict(id='band'+str(i),role='audit',domain_hz=[10.+i*230.,10.+(i+1)*230.],points=4096) for i in range(3)]
    if mutation=='path_escape':m['audit']['file']='../audit.npz'
    path.write_text(json.dumps(m))
    monkeypatch.setattr(np,'load',lambda *a,**kw:pytest.fail('Allocation before manifest refusal'))
    with pytest.raises(ValueError): evidence.inspect_evidence(path)


@pytest.mark.parametrize('corruption',['huge_header','object','duplicate','missing'])
def test_evidence_npz_header_caps_before_load(tmp_path,monkeypatch,corruption):
    path,m,_=evidence_fixture(tmp_path);target=tmp_path/'training.npz'
    if corruption=='huge_header':
        buf=io.BytesIO();np.lib.format.write_array_header_1_0(buf,dict(descr='<f8',fortran_order=False,shape=(10**9,)))
        bad=buf.getvalue()
        with zipfile.ZipFile(target) as z:items={k:z.read(k) for k in z.namelist()}
        items['frequency_hz.npy']=bad
        with zipfile.ZipFile(target,'w') as z:
            for name,data in items.items():z.writestr(name,data)
    elif corruption=='object':
        np.savez(target,frequency_hz=np.array([10.,100.,400.,700.],dtype=object),real=np.full(4,1000.),imag=np.zeros(4))
    elif corruption=='duplicate':
        with zipfile.ZipFile(target) as z:raw=z.read('frequency_hz.npy')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            with zipfile.ZipFile(target,'a') as z:z.writestr('frequency_hz.npy',raw)
    else: np.savez(target,frequency_hz=np.array([10.,100.,400.,700.]),real=np.full(4,1000.))
    m['training']['sha256']=_sha(target);path.write_text(json.dumps(m))
    monkeypatch.setattr(np,'load',lambda *a,**kw:pytest.fail('NPZ data allocated before structural refusal'))
    with pytest.raises(ValueError):evidence.inspect_evidence(path)


def test_evidence_recomputes_fidelity_and_keeps_extrapolation_separate(tmp_path):
    path,m,model=evidence_fixture(tmp_path,extrapolation=True)
    manifest,files=evidence.inspect_evidence(path);before=model.export_state()
    result=evidence.validate_evidence(manifest,model,files)
    assert result['status']=='finite_fidelity_verified' and result['sample_disjunction_verified']
    assert result['bands'][0]['admitted'] is True
    assert result['bands'][1]['admitted'] is False and result['bands'][1]['passes_reference_gates'] is False
    assert result['physical_validation']=='not_validated' and result['whole_nonlinear_spectrum_bounded'] is False
    assert model.export_state()==before


@pytest.mark.parametrize('mutation',['overlap','targets','duplicate','outside_band'])
def test_rehashed_evidence_semantic_corruption_refused(tmp_path,mutation):
    path,m,model=evidence_fixture(tmp_path)
    with np.load(tmp_path/'audit.npz',allow_pickle=False) as z:data={k:z[k] for k in z.files}
    if mutation=='overlap':data['inside_f'][0]=10.
    if mutation=='targets':data['inside_target']*=10
    if mutation=='duplicate':data['inside_f'][1]=data['inside_f'][0]
    if mutation=='outside_band':data['inside_f'][-1]=701.
    np.savez(tmp_path/'audit.npz',**data);m['audit']['sha256']=_sha(tmp_path/'audit.npz');path.write_text(json.dumps(m))
    manifest,files=evidence.inspect_evidence(path)
    before=model.export_state()
    with pytest.raises(ValueError):evidence.validate_evidence(manifest,model,files)
    assert model.export_state()==before


def test_state_cannot_use_saved_modal_label_or_forge_verified_source():
    m=state_model('relaxation');r=two_plateaus()
    with pytest.raises(ValueError,match='Synthetic state'):
        core.Experiment(m,r,source=dict(kind='verified_saved',description='invented',model_sha256=digest(m.parameters())))
    r['case']=dict(kind='saved',id='instrument',config='config.json',design='design.json',model_in='model.json',
        recipe=dict(sample_rate_hz=4000,h_cm=.5,loss_model='zk',radiation_model='legacy',air_reference='ck_dry20',
            R0=None,dc_origin=None,basis_completion='r29',fit_min_hz=40.,fit_max_hz=1000.,fit_points=4096,
            guard_max_hz=1500.,guard_points=768,audit_points=1536))
    with pytest.raises(ValueError,match='PassiveState requires saved_state'):
        core.Experiment(m,r,source=dict(kind='verified_saved',description='invented',model_sha256=digest(m.parameters())))


def test_native_state_phase_units_not_only_dimension():
    r=request(steps=64)
    r['observation']['phase']=dict(train=[0.,.004],validations=[[.004,.016]],
        scales=[1.]*3,state_units=['m','m/s','wrong'],section_index=0,section_level=0.,
        section_method='linear',groups=[1,2],min_crossings=8)
    with pytest.raises(ValueError,match='native state units'):state_experiment('relaxation',r)


@pytest.mark.parametrize('targets',['continuous','discrete','continuous_audit'])
def test_continuous_provenance_audits_executed_discrete_response(tmp_path, targets):
    """Analytic rational oracle, independent of PassiveState response methods."""
    path, manifest, _ = evidence_fixture(tmp_path)
    domain = [10.,1500.]; fs = 4000
    model = PassiveState([[0.,6500.],[-6500.,0.]],[0.,math.sqrt(1e7)],
        np.diag([0.,math.sqrt(50.),math.sqrt(1000.)]),omega_ref=1.,Zscale=1.,
        sample_rate_hz=fs,domain='continuous',coefficient_origin=ORIGIN,
        provenance=dict(source_sha256='1'*64,training_domain_hz=domain))
    before = model.parameters(), model.export_state()
    def oracle(f, discrete):
        s = 2j*fs*np.tan(np.pi*f/fs) if discrete else 2j*np.pi*f
        return 1000. + 1e7*s/(s*s + 50.*s + 6500.**2)
    f = np.linspace(*domain,80)
    af = 10. + (np.arange(79)+.37)*1490./79
    target = oracle(f,targets!='continuous')
    audit = oracle(af,targets=='discrete')
    np.testing.assert_allclose(model.continuous_response(af),oracle(af,False),rtol=1e-12)
    np.testing.assert_allclose(model.discrete_response(af),oracle(af,True),rtol=1e-12)
    mismatch = evidence.metrics(oracle(af,False),model.discrete_response(af))
    assert mismatch['complex_nrmse'] > 1. and mismatch['relative_max'] > 27.
    assert mismatch['phase_rms_deg'] > 52.
    np.savez(tmp_path/'training.npz',frequency_hz=f,real=target.real,imag=target.imag)
    np.savez(tmp_path/'audit.npz',inside_f=af,inside_target=audit)
    manifest.update(model_sha256=digest(model.parameters()),domain_hz=domain,
        coefficient_domain='continuous',bands=[dict(id='inside',role='audit',domain_hz=domain,points=len(af))],
        training=dict(file='training.npz',sha256=_sha(tmp_path/'training.npz'),points=len(f)),
        audit=dict(file='audit.npz',sha256=_sha(tmp_path/'audit.npz')))
    path.write_text(json.dumps(manifest))
    manifest, files = evidence.inspect_evidence(path)
    if targets == 'discrete':
        result = evidence.validate_evidence(manifest,model,files)
        assert result['coefficient_domain'] == 'continuous'
        assert result['audited_response_domain'] == 'discrete'
        assert result['training']['passes_reference_gates']
        assert result['bands'][0]['passes_reference_gates']
    else:
        stage = 'training' if targets == 'continuous' else 'audit'
        with pytest.raises(ValueError,match=stage+' fidelity refused'):
            evidence.validate_evidence(manifest,model,files)
    assert (model.parameters(),model.export_state()) == before
