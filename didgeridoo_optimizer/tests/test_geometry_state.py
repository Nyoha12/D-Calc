"""Public numerical oracles; no private historical geometry or coefficients."""
import copy
import json
from pathlib import Path
import numpy as np
import pytest
from didgeridoo_optimizer.nonlinear import scalar_fit as vf
from didgeridoo_optimizer.nonlinear import scalar_certificate_builder as builder
from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as verifier
from didgeridoo_optimizer.nonlinear.passive_realization import convert
from didgeridoo_optimizer.pipeline import geometry_state as flow

EXAMPLE=Path(__file__).resolve().parents[2]/'project_specs/examples/geometry_state'


def source(p=(-1.,),b=(0.,),r=(-.4,),d=1.):
    return dict(schema='dcalc.scalar_rational.v1',p_real=list(p),p_imag=list(b),residues=list(r),direct_normalized=d,
        zscale=1.,wref=float(vf.WREF),sample_rate_hz=12000,coefficient_domain='discrete_prewarped',training_domain_hz=[.1,3500.],
        order=len(r),passivity='not_assessed',physical_validation='not_validated',coefficient_origin='Synthetic rational oracle',provenance={})


def cert(c,**kw):
    raw=verifier.strict_json(c).encode();return raw,builder.certify(c,verifier.sha(raw),**kw)


def test_exact_certificate_and_native_realization_dc_direct_discrete():
    c=source();raw,proof=cert(c)
    assert proof['status']=='certified_strict_positive_real_boundary'
    assert verifier.verify_certificate(c,proof,verifier.sha(raw))['verified']
    converted=convert(raw,verifier.strict_json(proof).encode());m=converted['model']
    f=np.array([0.,.1,40.,1000.,3500.,5990.])
    np.testing.assert_allclose(m.discrete_response(f),vf.independent_response(f,c),rtol=2e-10)
    assert m.dc_pa_s_m3==pytest.approx(.6,rel=1e-12)
    assert m.feedthrough_pa_s_m3==pytest.approx(1.,rel=1e-12)
    assert c['passivity']=='not_assessed'


def test_vf_known_rational_and_frozen_dc():
    c=source();f,_=vf.grids(3500);z=vf.response(f,c)
    result,trace=vf.fit(f,z,[-2.+0j],.6,iterations=12,seconds=10)
    np.testing.assert_allclose(vf.response(f,result),z,rtol=2e-10)
    assert abs(vf.independent_response([0.],result)[0]-.6)<1e-12
    assert len(trace)==12 and all(t['rank']==2 and t['residue_rank']==1 for t in trace)


def test_nonpassive_narrow_pocket_budget_and_tampered_proof():
    raw,negative=cert(source(d=-1.));assert negative['status']=='nonpassive'
    raw,narrow=cert(source(p=(-.0001,),b=(10.,),r=(-.001,0.),d=1.))
    assert narrow['status']=='nonpassive'
    raw,limited=cert(source(p=(-.01,),b=(10.,),r=(-.005,0.)),max_nodes=1);assert limited['status']=='unresolved'
    raw,limited=cert(source(),seconds=0);assert limited['status']=='unresolved'
    c=source();raw,proof=cert(c);proof['leaves'][0]['lower_units']+=1
    with pytest.raises(ValueError):verifier.verify_certificate(c,proof,verifier.sha(raw))


def test_r59_reserved_grids_and_numerical_seeds():
    for top in (3500,5500):
        f,a=vf.grids(top);assert len(f)==6528
        seen=f
        for points in a.values():
            assert not np.intersect1d(seen,points).size;seen=np.r_[seen,points]
        p,meta=vf.initial_poles(f,np.ones(len(f),complex)*2,1.,top)
        assert len(meta['numerical_tails'])==2 and meta['real_relaxation_hz']==[.05,.5,5.,50.]
        assert sum(1 if x.imag==0 else 2 for x in p)==8


@pytest.mark.parametrize('key,value',[('max_order',129),('iterations',11),('top_hz',5990),('seconds',176),('mesh_maxrel',.02),('sample_rate_hz',True),('extra',1)])
def test_strict_recipe_before_science(key,value):
    r=json.loads((EXAMPLE/'recipe.json').read_text());r[key]=value
    with pytest.raises(ValueError):flow.validate_recipe(r)


def test_preflight_zero_science_and_before_destination(tmp_path,monkeypatch):
    from didgeridoo_optimizer.acoustics import transfer_matrix
    def forbidden(*a,**kw):raise AssertionError('Science during preflight')
    monkeypatch.setattr(transfer_matrix,'input_impedance',forbidden)
    monkeypatch.setattr(vf,'fit',forbidden);monkeypatch.setattr(np.linalg,'eigvals',forbidden)
    r=json.loads((EXAMPLE/'recipe.json').read_text());out=tmp_path/'absent'
    plan=flow.preflight(EXAMPLE/'config.yaml',EXAMPLE/'design.json',r,out)
    assert not out.exists() and set(plan['inputs'])=={'config','design','materials','variant_rules'}
    enormous=copy.deepcopy(json.loads((EXAMPLE/'design.json').read_text()));enormous['segments']*=129
    path=tmp_path/'large.json';path.write_text(json.dumps(enormous))
    with pytest.raises(ValueError,match='section budget'):flow.preflight(EXAMPLE/'config.yaml',path,r,out)
    assert not out.exists()


def test_worker_requires_actual_limits_before_calculation(monkeypatch):
    import resource
    monkeypatch.setattr(resource,'getrlimit',lambda kind:(-1,-1))
    with pytest.raises(ValueError):flow.worker({})


def test_input_mutation_after_preflight_is_not_refreshed(tmp_path):
    design=tmp_path/'design.json';design.write_bytes((EXAMPLE/'design.json').read_bytes())
    r=json.loads((EXAMPLE/'recipe.json').read_text());plan=flow.preflight(EXAMPLE/'config.yaml',design,r)
    design.write_text(design.read_text()+'\n')
    with pytest.raises(ValueError,match='input changed'):flow.verify_plan(plan)


def test_loaded_source_mutation_is_detected_without_refresh(monkeypatch):
    from didgeridoo_optimizer.reporting import geometry_state as report
    original=Path.read_bytes
    def changed(path):
        data=original(path)
        return data+b'\n# changed' if path==vf._LOADED_SOURCE_PATH else data
    monkeypatch.setattr(Path,'read_bytes',changed)
    # read_bytes uses an fd for current source fingerprints; the import snapshot
    # is separately pinned, so model a changed on-disk read at that boundary.
    original_read=verifier.read_bytes
    def disk(path):
        raw=original_read(path)
        return raw+b'\n# changed' if Path(path)==vf._LOADED_SOURCE_PATH else raw
    monkeypatch.setattr(verifier,'read_bytes',disk)
    with pytest.raises(ValueError,match='Source changed after import'):report.sources()


def test_conjugate_basis_and_reflection_count_are_explicit():
    p=np.array([-.2+1j,-2+3j,-.03]);r=np.array([2.,-3.,4.,.5,-.7]);s=1j*np.geomspace(.001,100,301)
    pp,rr=vf.expanded(p,r);expected=np.array([sum(b/(v-a) for a,b in zip(pp,rr)) for v in s])
    np.testing.assert_allclose(vf.basis(s,p)@r,expected,rtol=3e-13,atol=1e-13)
    relocated,count=vf.regroup(np.array([1+2j,1-2j,-3+0j]))
    assert count==2 and np.all(relocated.real<0)
    with pytest.raises(ValueError):vf.fit([1,2],[1+1j,2+2j],[-1]*129,1.)
    with pytest.raises(ValueError):vf.frequencies([1.] * 10001)
