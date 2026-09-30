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
