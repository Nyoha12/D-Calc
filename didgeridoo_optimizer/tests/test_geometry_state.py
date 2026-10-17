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


@pytest.mark.parametrize('missing',[None,'didgeridoo_optimizer/acoustics/transfer_matrix.py','didgeridoo_optimizer/nonlinear/scalar_fit.py'])
def test_c1_required_producer_inventory(missing):
    r=json.loads((EXAMPLE/'recipe.json').read_text())
    p=flow.preflight(EXAMPLE/'config.yaml',EXAMPLE/'design.json',r)
    if missing is None:p['producer_sources']={}
    else:p['producer_sources'].pop(missing)
    p['acquisition']['sha']=flow.digest(p['producer_sources'])
    p['sha256']=flow.digest({k:v for k,v in p.items() if k!='sha256'})
    with pytest.raises(ValueError):flow.verify_plan(p)


@pytest.mark.parametrize('key,value',[
    ('schema','other'),('mesh_segments_bound',True),('mesh_segments_bound',4097),
    ('base_sha','invented'),('source_status','published'),('physical_radius_m',1.),
    ('extra',0),('output','relative')])
def test_c1_strict_rehashed_plan(key,value):
    r=json.loads((EXAMPLE/'recipe.json').read_text());p=flow.preflight(EXAMPLE/'config.yaml',EXAMPLE/'design.json',r)
    p[key]=value;p['sha256']=flow.digest({k:v for k,v in p.items() if k!='sha256'})
    with pytest.raises(ValueError):flow.verify_plan(p)


@pytest.mark.parametrize('name',['../outside.py','/tmp/outside.py','tools//geometry_state.py','tools/./geometry_state.py','AGENTS.md'])
def test_c1_source_paths_cannot_be_ambiguous(name):
    from didgeridoo_optimizer.reporting import geometry_state as report
    r=json.loads((EXAMPLE/'recipe.json').read_text());p=flow.preflight(EXAMPLE/'config.yaml',EXAMPLE/'design.json',r)
    p['producer_sources'][name]='0'*64
    with pytest.raises(ValueError):report.verify_sources(p['producer_sources'])


def test_c1_native_tmm_import_snapshot(monkeypatch):
    from didgeridoo_optimizer.reporting import geometry_state as report
    original=verifier.read_bytes
    def changed(path):
        raw=original(path)
        return raw+b'\n' if str(path).endswith('/acoustics/transfer_matrix.py') else raw
    monkeypatch.setattr(verifier,'read_bytes',changed)
    with pytest.raises(ValueError,match='Source changed after import'):report.sources()


def _receipt(*,reader=False,played=False):
    common=dict(pid=123,exit_code=0,reaped=True,collected=True,pipes_closed=True,
        seconds=2.2,operation_seconds=2.,collection_seconds=.1,collection_limit_seconds=4,limit_seconds=175)
    if played:return dict(common,kind='read' if reader else 'fit',launched=True,timeout_or_error=None,
        log='child-0001.log',input_bytes=1,steps_charged=0,log_bytes=0)
    return dict(common,error=None,stdout_bytes=0,stderr_bytes=0)


@pytest.fixture
def synthetic_bundle(tmp_path):
    """Explicit synthetic receipt/evidence fixture; no instrument fit or TMM.

    It exercises the production reader with a one-pole rational oracle, not an
    acquisition claim or a replacement for the pilot's future demonstrations.
    """
    from didgeridoo_optimizer.reporting import geometry_state as report
    from didgeridoo_optimizer.reporting import passive_realization as nr
    from didgeridoo_optimizer.reporting import state_register_context as ev
    from didgeridoo_optimizer.pipeline import passive_realization as native
    from didgeridoo_optimizer.nonlinear.passive_fit import DEFAULT_GATES
    out=tmp_path/'synthetic';r=json.loads((EXAMPLE/'recipe.json').read_text())
    design=tmp_path/'design.json';design.write_bytes((EXAMPLE/'design.json').read_bytes())
    plan=flow.preflight(EXAMPLE/'config.yaml',design,r,out);out.mkdir();report.json_new(out/'plan.json',plan)
    dc=report.native_dc(plan);c=source();c['zscale']=dc/.6;c['coefficient_origin']='Native TMM geometry scalar VF'
    c['provenance']=dict(native_dc_pa_s_m3=dc,inputs={k:v['sha256'] for k,v in plan['inputs'].items()},
        recipe=r,effective=plan['effective'],physical_radius_m=plan['physical_radius_m'],producer_sources=plan['producer_sources'])
    raw,proof=cert(c);cr=verifier.strict_json(proof).encode();converted=convert(raw,cr);model=converted['model']
    report.json_new(out/'source.json',c);report.json_new(out/'certificate.json',proof)
    h=verifier.sha(raw);report.json_new(out/'frozen-before-audit.json',dict(sha256=h,fit_completed=True,audit_targets_acquired=False))
    report.json_new(out/'fit_trace.json',[dict(iteration=i+1,order=1,rank=2,residue_rank=1,reflected_poles=0,weighted_residual=0.,seconds=.01*(i+1)) for i in range(12)])
    report.json_new(out/'seeds.json',{})
    f,audits=vf.grids(r['top_hz']);z=np.concatenate([model.discrete_response(f[i:i+4096]) for i in range(0,len(f),4096)])
    report.npz_new(out/'training.npz',frequency_hz=f,real=z.real,imag=z.imag)
    arrays={};bands=[];bounds=dict(low=[.1,40.],interior=[40.,3000.],guard=[3000.,3500.],upper=[3500.,5500.],edge=[5500.,5990.])
    for name,fv in audits.items():
        arrays[name+'_f']=fv;arrays[name+'_target']=model.discrete_response(fv)
        bands.append(dict(id=name,role='audit' if fv[-1]<=r['top_hz'] else 'extrapolation',domain_hz=bounds[name],points=len(fv)))
    report.npz_new(out/'audit.npz',**arrays)
    af=np.concatenate([f for f in audits.values() if f[-1]<=r['top_hz']]);az=model.discrete_response(af)
    report.npz_new(out/'mesh.npz',frequency_hz=af,coarse=az,fine=az)
    bundle=out/'realization';rp=native.preflight(out/'source.json',out/'certificate.json',bundle);bundle.mkdir()
    nr.json_new(bundle/'plan.json',rp);nr.write_new(bundle/'stdout.txt',b'');nr.write_new(bundle/'stderr.txt',b'');nr.write_result(bundle,converted,raw,cr)
    evidence=dict(schema=ev.SCHEMA,inputs={k:v['sha256'] for k,v in plan['inputs'].items()},recipe=flow.state_recipe(r),effective=plan['effective'],
        physical_radius_m=plan['physical_radius_m'],source_sha256=h,model_sha256=flow.digest(model.parameters()),domain_hz=c['training_domain_hz'],
        coefficient_domain=c['coefficient_domain'],acquisition=plan['acquisition'],gates=DEFAULT_GATES,bands=bands,
        training=dict(file='training.npz',sha256=verifier.sha(verifier.read_bytes(out/'training.npz')),points=len(f)),
        audit=dict(file='audit.npz',sha256=verifier.sha(verifier.read_bytes(out/'audit.npz'))))
    report.json_new(out/'evidence.json',evidence);em,files=ev.inspect_evidence(out/'evidence.json');fidelity=ev.validate_evidence(em,model,files)
    calls=[dict(points=n,h_cm=r['h_cm']) for n in (6528,257,1201,401,1009,257)]+[dict(points=len(af),h_cm=r['h_cm']/2)]
    report.json_new(out/'result.json',dict(schema=report.SCHEMA,status='computed_pending_collection',fidelity=fidelity,mesh_maxrel=0.,source_sha256=h,
        seconds=1.,tmm_calls=calls,new_instrument_fits=1,new_temporal_steps=0,physical_validation='not_validated'))
    receipt=_receipt();report.json_new(out/'child.json',receipt)
    for mode in ('worker','verify-pending'):
        for stream in ('stdout','stderr'):(out/(mode+'-'+stream+'.txt')).write_bytes(b'')
    report.close(out,receipt);report.publish_complete(out,_receipt(reader=True))
    return out


def _rewrite(path,value):path.write_text(verifier.strict_json(value))


def _rehash_bundle(out):
    """Rehash only a disposable synthetic bundle, never an author archive."""
    m=verifier.read_json(out/'manifest.json')
    m['files']={name:verifier.sha(verifier.read_bytes(out/name)) for name in m['files']}
    _rewrite(out/'manifest.json',m)
    e=verifier.read_json(out/'execution.json');e['manifest_sha256']=verifier.sha(verifier.read_bytes(out/'manifest.json'));_rewrite(out/'execution.json',e)
    _rewrite(out/'execution.closed.json',dict(execution_sha256=verifier.sha(verifier.read_bytes(out/'execution.json'))))
    c=verifier.read_json(out/'complete.json');c.update(execution_sha256=verifier.sha(verifier.read_bytes(out/'execution.json')),
        manifest_sha256=verifier.sha(verifier.read_bytes(out/'manifest.json')),reader_sha256=verifier.sha(verifier.read_bytes(out/'reader.json')))
    _rewrite(out/'complete.json',c)


def test_synthetic_reader_no_new_science_and_caller_policy(synthetic_bundle,monkeypatch):
    import resource,signal
    from didgeridoo_optimizer.reporting import geometry_state as report
    from didgeridoo_optimizer.nonlinear import passive_realization
    from didgeridoo_optimizer.acoustics import transfer_matrix
    from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
    from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
    def no(*a,**kw):raise AssertionError('New science during read')
    for obj,name in [(vf,'fit'),(builder,'certify'),(passive_realization,'convert'),(transfer_matrix,'input_impedance'),(SimultaneousCoupling,'step'),(PassiveState,'step')]:monkeypatch.setattr(obj,name,no)
    before=[resource.getrlimit(k) for k in (resource.RLIMIT_CPU,resource.RLIMIT_AS,resource.RLIMIT_FSIZE)]
    handler=signal.getsignal(signal.SIGALRM);timer=signal.getitimer(signal.ITIMER_REAL)
    assert report.read_result(synthetic_bundle)['ok']
    assert before==[resource.getrlimit(k) for k in (resource.RLIMIT_CPU,resource.RLIMIT_AS,resource.RLIMIT_FSIZE)]
    assert handler==signal.getsignal(signal.SIGALRM) and timer==signal.getitimer(signal.ITIMER_REAL)


@pytest.mark.parametrize('key,value',[('schema','bad'),('status','partial'),('source_sha256','0'*64),('new_temporal_steps',42),
    ('new_temporal_steps',False),('new_instrument_fits',True),('new_instrument_fits',2),('physical_validation','validated'),('seconds',176),('seconds',True),('extra',1)])
def test_c1_rehashed_normative_result_refused(synthetic_bundle,key,value):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle;r=verifier.read_json(out/'result.json');r[key]=value;_rewrite(out/'result.json',r);_rehash_bundle(out)
    with pytest.raises(ValueError):report.read_result(out)


@pytest.mark.parametrize('played',[False,True])
@pytest.mark.parametrize('reader',[False,True])
@pytest.mark.parametrize('key,value',[('error','reported worker timeout'),('exit_code',False),('pid',True),('reaped',1),('collected',False),
    ('pipes_closed',False),('operation_seconds',175),('collection_seconds',5),('seconds',float('inf'))])
def test_c1_receipt_formats_strict(played,reader,key,value):
    from didgeridoo_optimizer.reporting import geometry_state as report
    receipt=_receipt(reader=reader,played=played);report.validate_receipt(receipt,175,reader=reader)
    receipt['timeout_or_error' if played and key=='error' else key]=value
    with pytest.raises(ValueError):report.validate_receipt(receipt,175,reader=reader)


@pytest.mark.parametrize('reader',[False,True])
def test_c1_rehashed_timeout_refused(synthetic_bundle,reader):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle
    if reader:
        r=verifier.read_json(out/'reader.json');r['error']='reported worker timeout';_rewrite(out/'reader.json',r)
    else:
        r=verifier.read_json(out/'child.json');r['error']='reported worker timeout';_rewrite(out/'child.json',r)
        e=verifier.read_json(out/'execution.json');e['child']=r;_rewrite(out/'execution.json',e)
    _rehash_bundle(out)
    with pytest.raises(ValueError,match='Failed child'):report.read_result(out)


@pytest.mark.parametrize('changed',['input','source','training.npz','audit.npz','mesh.npz','realization/model.json','seeds.json','manifest.json','reader.json','complete.json'])
def test_c2_persistent_mutation_after_admission(synthetic_bundle,monkeypatch,changed):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle;original=report.validate_result
    plan=verifier.read_json(out/'plan.json')
    path=Path(plan['inputs']['design']['path']) if changed=='input' else out/changed
    def mutate(*a,**kw):
        value=original(*a,**kw)
        if changed=='source':
            old=verifier.read_bytes
            def disk(p):
                data=old(p)
                return data+b'\n' if str(p).endswith('/acoustics/transfer_matrix.py') else data
            monkeypatch.setattr(verifier,'read_bytes',disk)
        else:path.write_bytes(path.read_bytes()+b'\n')
        return value
    monkeypatch.setattr(report,'validate_result',mutate)
    with pytest.raises(ValueError):report.read_result(out)


def test_c1_dc_independent_of_declared_provenance(synthetic_bundle):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle;p=verifier.read_json(out/'plan.json');c=verifier.read_json(out/'source.json');report.verify_dc(c,p)
    changed=copy.deepcopy(c);changed['provenance']['native_dc_pa_s_m3']*=2
    with pytest.raises(ValueError,match='DC'):report.verify_dc(changed,p)
    changed=copy.deepcopy(c);changed['residues'][0]*=.9
    with pytest.raises(ValueError,match='DC'):report.verify_dc(changed,p)


@pytest.mark.parametrize('stage',['last_plan','last_closure'])
@pytest.mark.parametrize('file',['mesh.npz','realization/model.json','seeds.json'])
def test_c2_mutation_at_last_validation_block(synthetic_bundle,monkeypatch,stage,file):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle
    if stage=='last_closure':
        original=report.read_closure;calls=[]
        def wrapped(*a,**kw):
            v=original(*a,**kw);calls.append(1)
            if len(calls)==2:(out/file).write_bytes((out/file).read_bytes()+b'\n')
            return v
        monkeypatch.setattr(report,'read_closure',wrapped)
    else:
        original=flow.verify_plan;calls=[]
        def wrapped(*a,**kw):
            v=original(*a,**kw);calls.append(1)
            if len(calls)==2:(out/file).write_bytes((out/file).read_bytes()+b'\n')
            return v
        monkeypatch.setattr(flow,'verify_plan',wrapped)
    with pytest.raises(ValueError,match='changed'):report.read_result(out)


@pytest.mark.parametrize('file',['training.npz','audit.npz','mesh.npz'])
def test_c2_changed_npz_never_allocated_after_native_admission(synthetic_bundle,monkeypatch,file):
    import io
    from didgeridoo_optimizer.reporting import geometry_state as report
    from didgeridoo_optimizer.pipeline import state_register_context as context
    out=synthetic_bundle;original=context.load_verified;load=np.load
    data=io.BytesIO();np.savez(data,wrong=np.zeros(1));bad=data.getvalue()
    def admitted(*a,**kw):
        value=original(*a,**kw);(out/file).write_bytes(bad);_rehash_bundle(out);return value
    def guarded(stream,*a,**kw):
        if hasattr(stream,'getvalue') and stream.getvalue()==bad:raise AssertionError('Changed bytes allocated')
        return load(stream,*a,**kw)
    monkeypatch.setattr(context,'load_verified',admitted);monkeypatch.setattr(np,'load',guarded)
    with pytest.raises(ValueError,match='changed'):report.read_result(out)


def test_c1_nested_fidelity_boolean_type(synthetic_bundle):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle;r=verifier.read_json(out/'result.json');r['fidelity']['sample_disjunction_verified']=1
    _rewrite(out/'result.json',r);_rehash_bundle(out)
    with pytest.raises(ValueError):report.read_result(out)


def test_c1_preexisting_native_code_checked_without_execution():
    from didgeridoo_optimizer.reporting import geometry_state as report
    from didgeridoo_optimizer.acoustics import transfer_matrix
    p=Path(transfer_matrix.__file__);raw=p.read_bytes()
    report._verify_loaded_code(transfer_matrix,p,raw)
    # A changed native function already loaded before the geometry boundary.
    changed=raw.replace(b'def input_impedance(',b'def changed_input_impedance(',1)
    assert changed!=raw
    with pytest.raises(ValueError,match='before geometry import'):report._verify_loaded_code(transfer_matrix,p,changed)


def test_c1_close_and_publish_reject_failed_receipts(synthetic_bundle,tmp_path):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle;bad=_receipt();bad['error']='timeout'
    with pytest.raises(ValueError,match='Failed child'):report.close(out,bad)
    with pytest.raises(ValueError,match='Failed child'):report.publish_complete(out,bad)


def test_c1_compute_native_receipt_contradiction(synthetic_bundle):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle;e=verifier.read_json(out/'execution.json');e['child']['pid']+=1;_rewrite(out/'execution.json',e);_rehash_bundle(out)
    with pytest.raises(ValueError,match='receipt contradiction'):report.read_result(out)
    with pytest.raises(ValueError,match='receipt contradiction'):report.publish_complete(out,_receipt(reader=True))


def test_c2_publication_refuses_changed_payload(synthetic_bundle):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle;(out/'seeds.json').write_text('{}\n ')
    with pytest.raises(ValueError,match='before publication'):report.publish_complete(out,_receipt(reader=True))


@pytest.mark.parametrize('reader',[False,True])
def test_c1_rehashed_played_timeout_full_reader(synthetic_bundle,reader):
    from didgeridoo_optimizer.reporting import geometry_state as report
    out=synthetic_bundle;r=_receipt(reader=reader,played=True);r['timeout_or_error']='reported worker timeout'
    if reader:_rewrite(out/'reader.json',r)
    else:
        _rewrite(out/'child.json',r);e=verifier.read_json(out/'execution.json');e['child']=r;_rewrite(out/'execution.json',e)
    _rehash_bundle(out)
    with pytest.raises(ValueError,match='Failed child'):report.read_result(out)
