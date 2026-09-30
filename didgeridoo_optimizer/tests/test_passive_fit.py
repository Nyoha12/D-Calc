import itertools
import numpy as np
import pytest

from didgeridoo_optimizer.nonlinear.passive_fit import (nnls,column_nnls,FitBudget,fit_passive,
    metrics,audit,DEFAULT_GATES,seeds)
from didgeridoo_optimizer.tests.test_passive_resonator import modal, DC


def test_nnls_matches_exhaustive_active_subsets_not_clipped_ls():
    rng=np.random.default_rng(67)
    for _ in range(8):
        A=rng.normal(size=(9,5)); b=rng.normal(size=9)
        values,cert=nnls(A,b)
        best=float(np.linalg.norm(b)**2)
        for flags in itertools.product([False,True],repeat=5):
            active=np.array(flags)
            if not np.any(active):continue
            x=np.linalg.lstsq(A[:,active],b,rcond=None)[0]
            if np.all(x>=0):best=min(best,float(np.linalg.norm(A[:,active]@x-b)**2))
        assert cert['converged']
        assert np.linalg.norm(A@values-b)**2==pytest.approx(best,rel=1e-11,abs=1e-13)


def test_column_generation_checks_omitted_candidates_and_keeps_zeros():
    A=np.eye(12); b=np.zeros(12); b[[0,9,11]]=[1,2,3]
    x,cert=column_nnls(A,b,working_limit=4)
    assert cert['converged'] and len(cert['trace'])==2 and len(x)==12
    np.testing.assert_allclose(x,b,atol=1e-14)
    _,partial=column_nnls(A,b,working_limit=4,max_passes=1)
    assert not partial['converged'] and partial['global_kkt']>5e-10
    _,timed=column_nnls(A,b,budget=FitBudget(0))
    assert not timed['converged'] and 'budget' in timed['reason']


def test_exact_analytic_fit_explicit_single_mode_and_independent_audit():
    model=modal(); f=np.linspace(30,200,512); z=model.continuous_response(f)
    fitted,info=fit_passive(f,z,R0=0,dc_origin=DC,sample_rate_hz=12000,gates=DEFAULT_GATES,
        domain='continuous',single_mode=True)
    assert info['certificate']['converged']
    fv=30+(np.arange(257)+.618)*(170/257)
    result=audit(fitted,fv,model.continuous_response(fv),fit_frequencies=f,gates=DEFAULT_GATES)
    assert result['status']=='accepted' and result['metrics']['complex_nrmse']<1e-10
    assert 'structural assumption' in info['seeds'][0]['method']
    assert len(info['candidate_inventory']['a'])==9
    with pytest.raises(ValueError,match='disjoint'):
        audit(fitted,f,z,fit_frequencies=f,gates=DEFAULT_GATES)
    bad=audit(fitted,fv,model.continuous_response(fv)*2,fit_frequencies=f,gates=DEFAULT_GATES)
    assert bad['status']=='not_accepted'


def test_fit_timeout_has_explicit_noncertified_partial_and_inventory():
    m=modal(); f=np.linspace(30,200,128)
    model,info=fit_passive(f,m.continuous_response(f),R0=0,dc_origin=DC,sample_rate_hz=12000,
        gates=DEFAULT_GATES,seconds=0)
    assert info['status']=='not_converged' and info['fidelity']=='not_audited'
    assert len(info['candidate_inventory']['a'])==9 and model.energy()==0


@pytest.mark.parametrize('change',[{'sample_rate_hz':True},{'gates':{}},{'guard_frequency_hz':[200,210]},{'max_passes':11},{'seconds':181}])
def test_invalid_fit_contracts(change):
    m=modal(); f=np.linspace(30,200,128)
    kwargs=dict(R0=0,dc_origin=DC,sample_rate_hz=12000,gates=DEFAULT_GATES)
    kwargs.update(change)
    with pytest.raises(ValueError):fit_passive(f,m.continuous_response(f),**kwargs)


def test_unresolved_guard_never_invented_and_phase_is_circular():
    rows=seeds(np.arange(1.,10.),np.array([1,2,3,4,5,4,3,2,1],complex))
    assert rows[0]['status']=='resolved'
    result=metrics(np.exp(1j*np.deg2rad([179.])),np.exp(1j*np.deg2rad([-179.])))
    assert result['phase_rms_deg']==pytest.approx(2.)
    with pytest.raises(ValueError):nnls(np.zeros((3,2)),np.ones(3))


def test_progress_preserves_full_iterate_and_zero_phase_is_undefined():
    seen=[]
    x,cert=column_nnls(np.eye(4),np.array([1.,0.,2.,0.]),working_limit=2,
        progress=lambda x,c:seen.append((x.copy(),c)))
    assert cert['converged'] and len(seen)==2
    np.testing.assert_array_equal(seen[-1][0],x)
    assert len(seen[0][0])==4
    got=metrics(np.array([1.+1j]),np.array([0.j]))
    assert got['phase_rms_deg'] is None and got['phase_status']=='undefined_zero_response'


@pytest.mark.parametrize('domain',['continuous','discrete_prewarped'])
def test_r29_completion_is_exact_separate_numerical_basis(domain):
    from didgeridoo_optimizer.nonlinear.passive_fit import candidate_dictionary,completion_spec
    f=np.linspace(30,200,256); z=modal().continuous_response(f)
    plain=candidate_dictionary(f,z,R0=0,fs=12000,domain=domain)
    completed=candidate_dictionary(f,z,R0=0,fs=12000,domain=domain,basis_completion='r29')
    np.testing.assert_array_equal(completed[0][:-2],plain[0])
    np.testing.assert_array_equal(completed[1][:-2],plain[1])
    assert completed[2:]==plain[2:]
    centers=200*np.array([1.15,1.65])
    expected=2*12000*np.tan(np.pi*centers/12000) if domain=='discrete_prewarped' else 2*np.pi*centers
    np.testing.assert_array_equal(completed[0][-2:],expected)
    np.testing.assert_array_equal(completed[1][-2:],.08*expected)
    spec=completion_spec('r29',fit_max_hz=200.,fs=12000,domain=domain)
    assert [t['frequency_hz'] for t in spec['terms']]==centers.tolist()
    assert spec['units']==dict(frequency_hz='Hz',omega='rad/s',gamma='1/s',gamma_over_omega='dimensionless')
    assert all(row['source']=='fit' for row in completed[2])


@pytest.mark.parametrize('change',[
    {'mode':True},{'mode':False},{'mode':None},{'mode':'R29'},{'mode':{}},
    {'fs':True},{'fs':8000},{'fit_max_hz':True},{'fit_max_hz':0},
    {'fit_max_hz':float('nan')},{'fit_max_hz':float('inf')},
    {'fit_max_hz':4000.},{'domain':True},{'domain':{}},{'domain':'unknown'}])
def test_completion_contract_refuses_invalid_types_domains_and_nyquist(change):
    from didgeridoo_optimizer.nonlinear.passive_fit import completion_spec
    kwargs=dict(mode='r29',fit_max_hz=3000.,fs=12000,domain='discrete_prewarped')
    kwargs.update(change)
    with pytest.raises(ValueError):completion_spec(**kwargs)


def test_r29_completion_checks_nyquist_boundary_before_seeding(monkeypatch):
    from didgeridoo_optimizer.nonlinear import passive_fit
    monkeypatch.setattr(passive_fit,'seeds',lambda *a,**k:pytest.fail('seeding before completion validation'))
    f=np.array([1.,2000.]); z=np.ones(2,dtype=complex)
    with pytest.raises(ValueError,match='Nyquist'):
        passive_fit.candidate_dictionary(f,z,R0=0,fs=6600,domain='discrete_prewarped',basis_completion='r29')


def test_r29_completion_respects_full_candidate_ceiling(monkeypatch):
    from didgeridoo_optimizer.nonlinear import passive_fit
    rows=[dict(frequency_hz=70.+i,width_hz=1.,status='resolved') for i in range(29)]
    monkeypatch.setattr(passive_fit,'seeds',lambda *a,**k:rows)
    with pytest.raises(ValueError,match='256'):
        passive_fit.candidate_dictionary(np.array([30.,200.]),np.ones(2,complex),R0=0,fs=12000,
            domain='discrete_prewarped',basis_completion='r29')


def test_completion_partial_retains_declared_basis_and_separate_source_ids():
    f=np.linspace(30,200,128); fg=np.linspace(200,250,32); m=modal()
    _,info=fit_passive(f,m.discrete_response(f),R0=0,dc_origin=DC,sample_rate_hz=12000,
        gates=DEFAULT_GATES,basis_completion='r29',guard_frequency_hz=fg,
        guard_impedance=m.discrete_response(fg),seconds=0)
    assert info['status']=='not_converged'
    assert info['basis_completion']['mode']=='r29'
    assert info['certificate']['basis_completion']==info['basis_completion']
    assert info['fit_spectrum_sha256']!=info['guard_spectrum_sha256']
    assert len(info['candidate_inventory']['a'])==11


@pytest.mark.parametrize('case',['cylinder_zk','exponential_zk'])
@pytest.mark.parametrize('mode',['observed-only','r29'])
def test_new_fits_from_r29_spectra(case,mode,tmp_path):
    """Refit residues from numerical spectra, never load the dated coefficients."""
    import hashlib
    import json
    from pathlib import Path
    from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator
    from didgeridoo_optimizer.pipeline.time_domain_reference import _reload_certificate
    fixtures=Path(__file__).parent/'fixtures/td_pass_01'
    manifest=json.loads((fixtures/'manifest.json').read_text()); entry=manifest['cases'][case]
    path=fixtures/entry['file']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
    with np.load(path,allow_pickle=False) as data:
        # Only fit/guard arrays enter fitting. Reserved audit arrays are read afterwards.
        f,z,fg,zg=(data[k] for k in ('fit_f','fit_z','guard_f','guard_z'))
        assert (len(f),f[0],f[-1],len(fg),fg[0],fg[-1])==(4096,40.,3000.,768,3000.,3500.)
        model,info=fit_passive(f,z,R0=float(data['R0']),dc_origin=dict(kind='zk_local_1d',description=entry['R0_origin']),
            sample_rate_hz=int(data['fs']),gates=DEFAULT_GATES,guard_frequency_hz=fg,guard_impedance=zg,basis_completion=mode)
        cert=info['certificate']
        assert cert['converged'] and cert['global_kkt']<=5e-10 and cert['tolerance']==5e-10
        assert cert['candidate_count']==(227 if mode=='r29' else 225)
        assert len(cert['trace'])<=10 and all(row['working_columns']<=192 for row in cert['trace'])
        assert np.all(model.a>=0) and all(s['source'] in ('fit','guard') for s in info['seeds'])
        result=audit(model,data['frequency_hz'],data['target'],fit_frequencies=f,gates=DEFAULT_GATES)
        assert result['status']==('accepted' if mode=='r29' else 'not_accepted')
        if mode=='r29':
            assert all(result['metrics'][key]<=limit for key,limit in DEFAULT_GATES.items())
            assert len(model.a)==entry['reference_active_terms']
        else:
            assert all(result['metrics'][key]>limit for key,limit in DEFAULT_GATES.items())
        model.save(tmp_path/'model.json'); reloaded=PassiveResonator.load(tmp_path/'model.json')
        np.testing.assert_array_equal(reloaded.discrete_response(data['frequency_hz']),model.discrete_response(data['frequency_hz']))
        np.testing.assert_array_equal(reloaded.impulse_response(64),model.impulse_response(64))
        assert _reload_certificate(reloaded,f,z,fg,zg,basis_completion=mode)['converged']
        print('R30_FIT_RESULT '+json.dumps(dict(case=case,mode=mode,status=result['status'],metrics=result['metrics'],
            active_terms=len(model.a),global_kkt=cert['global_kkt'],passes=len(cert['trace']),fixture_sha256=entry['sha256'])))


def test_completion_progress_keeps_certified_dictionary_identity():
    seen=[]; m=modal();f=np.linspace(30,200,128)
    _,info=fit_passive(f,m.discrete_response(f),R0=0,dc_origin=DC,sample_rate_hz=12000,
        gates=DEFAULT_GATES,basis_completion='r29',progress=seen.append)
    assert seen and seen[-1]['accepted'] is False
    assert seen[-1]['basis_completion']==info['basis_completion']
    assert seen[-1]['certificate']['candidate_dictionary_sha256']==info['candidate_dictionary_sha256']
    assert seen[-1]['fit_spectrum_sha256']==info['fit_spectrum_sha256']
    assert seen[-1]['guard_spectrum_sha256'] is None
    assert seen[-1]['a']==info['candidate_inventory']['a']
