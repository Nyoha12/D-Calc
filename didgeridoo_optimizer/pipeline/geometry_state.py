"""Bounded public construction of a new certified scalar model per geometry."""
from pathlib import Path
import hashlib
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

_LOADED_SOURCE_PATH=Path(__file__).resolve()
_LOADED_SOURCE_SHA256=hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
_BLAS1_AT_IMPORT=all(os.environ.get(k)=='1' for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'))
from ..nonlinear import scalar_passivity_certificate as strict
from ..reporting import geometry_state as report
from ..nonlinear.passive_resonator import digest
from .fixed_design import load_fixed_context
from .design_pitch import models
from ..optimization.design_contract import read_request

ROOT=Path(__file__).resolve().parents[2]
BACKEND='certified_scalar_v1'
STATE_KEYS={'sample_rate_hz','h_cm','loss_model','radiation_model','air_reference'}
KEYS=STATE_KEYS|{'backend','top_hz','iterations','fit_seconds','certificate_seconds','seconds','mesh_maxrel','max_order','max_nodes','max_depth'}


def validate_recipe(r):
    if type(r) is not dict or set(r)!=KEYS: raise ValueError('Distinct complete certified scalar recipe required')
    if r['backend']!=BACKEND or r['sample_rate_hz']!=12000 or type(r['sample_rate_hz']) is not int:
        raise ValueError('Scalar backend/fs')
    if r['top_hz'] not in (3500,5500) or type(r['top_hz']) not in (int,float): raise ValueError('Scalar A/B domain')
    if r['loss_model']!='zk' or r['air_reference'] not in ('ck_dry20','ck_dry25') or r['radiation_model'] not in ('legacy','silva_unflanged','silva_flanged'):
        raise ValueError('Native ZK/air/radiation recipe')
    for k,lo,hi in [('iterations',12,12),('max_order',1,128),('max_nodes',1,12000),('max_depth',0,48)]: strict.integer(r[k],k,lo,hi)
    for k,lo,hi in [('h_cm',.05,2),('seconds',1,175),('fit_seconds',.001,150),('certificate_seconds',.001,150),('mesh_maxrel',.000001,.01)]:
        if type(r[k]) not in (int,float) or not math.isfinite(r[k]) or not lo<=r[k]<=hi: raise ValueError('Scalar budget/domain: '+k)
    if r['fit_seconds']+r['certificate_seconds']>=r['seconds']: raise ValueError('Reserve acquisition/conversion/readback within total fit budget')
    return r


def state_recipe(r):return {k:r[k] for k in sorted(STATE_KEYS)}


def preflight(config,design,recipe,output_dir=None):
    """Only bounded parsing, native geometry and metadata; no science or output."""
    validate_recipe(recipe)
    for path in (config,design): strict.read_bytes(path)
    # Bound section count before native geometry or discretizer construction.
    raw,_=read_request(Path(design))
    if type(raw) is not dict or type(raw.get('segments')) is not list or not 1<=len(raw['segments'])<=128:
        raise ValueError('Geometry section budget')
    config_raw,_=read_request(Path(config))
    material_config=config_raw.get('materials',{})
    for key,default in [('database_file','materials_base_v1.yaml'),('variant_rules_file','wood_variant_rules_v1.yaml')]:
        name=material_config.get(key,default);p=Path(name)
        strict.read_bytes(p if p.is_absolute() else (Path(config).resolve().parent/p).resolve())
    ctx=load_fixed_context(config,design)
    for item in ctx['provenance']['files'].values():
        if item['sha256'] is None: raise ValueError('All four actual inputs required')
        strict.read_bytes(item['path'])
    _,_,_,effective=models(ctx,recipe)
    bound=sum(max(1 if s.is_uniform else 2,math.ceil(s.length_cm/(recipe['h_cm']/2))) for s in ctx['design'].segments)
    if bound>4096 or bound*6528>8_000_000: raise ValueError('Mesh/frequency product before allocation')
    if output_dir is not None:
        out=strict.safe_path(output_dir,exists=False)
        if out.exists(): raise FileExistsError('New output required, including dry-run')
    # Import all producing modules before fingerprint capture, without calculation.
    from ..nonlinear import scalar_fit, scalar_certificate_builder, passive_realization
    from ..acoustics.transfer_matrix import input_impedance
    from ..geometry.discretization import GeometryDiscretizer
    from . import passive_realization as native
    sources=report.sources()
    git=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True,timeout=5).stdout.strip()
    plan=dict(schema='dcalc.geometry_state.plan.v1',inputs={k:dict(path=str(Path(v['path']).resolve()),sha256=v['sha256']) for k,v in ctx['provenance']['files'].items()},
        recipe=dict(recipe),effective=effective,physical_radius_m=ctx['design'].segments[-1].d_out_cm/200,
        mesh_segments_bound=bound,producer_sources=sources,base_sha=git,
        source_status='working_tree_fingerprints_not_a_published_commit',
        acquisition=dict(nature='simulation_tmm',date=datetime.now(timezone.utc).isoformat(),sha=digest(sources),
                         provenance='Native input_impedance; actual imported source fingerprints in geometry plan; no measured calibration'),
        output=str(out) if output_dir is not None else None,
        budgets=dict(memory_mib=768,cpu_seconds=175,file_mib=4,output_mib=32,wall_seconds=recipe['seconds'],blas_threads=1))
    plan['sha256']=digest(plan)
    return plan


def verify_plan(p):
    if p['sha256']!=digest({k:v for k,v in p.items() if k!='sha256'}): raise ValueError('Geometry plan fingerprint')
    validate_recipe(p['recipe']);report.verify_sources(p['producer_sources'])
    for item in p['inputs'].values():
        if strict.sha(strict.read_bytes(item['path']))!=item['sha256']: raise ValueError('Geometry input changed')
    if set(p['inputs'])!={'config','design','materials','variant_rules'}: raise ValueError('Four input inventory')
    if p['acquisition']['nature']!='simulation_tmm' or p['acquisition']['sha']!=digest(p['producer_sources']): raise ValueError('Acquisition provenance')


def limits():
    from .design_pitch import child_limits
    import resource
    child_limits();resource.setrlimit(resource.RLIMIT_FSIZE,(4*1024**2,4*1024**2))


def ready():
    from .passive_realization import worker_ready
    from ..nonlinear.scalar_fit import _BLAS1_AT_IMPORT as fit_blas
    worker_ready()
    import resource
    for kind,cap in ((resource.RLIMIT_AS,768*1024**2),(resource.RLIMIT_CPU,175),(resource.RLIMIT_FSIZE,4*1024**2)):
        soft,hard=resource.getrlimit(kind)
        if not 0<soft<=hard<=cap: raise ValueError('Actual worker hard/soft ceiling')
    if not _BLAS1_AT_IMPORT or not fit_blas: raise ValueError('BLAS1 before NumPy required')


def worker(plan):
    ready();verify_plan(plan)
    from ..nonlinear.scalar_fit import grids,initial_poles,fit
    from ..nonlinear.scalar_certificate_builder import certify
    from ..nonlinear.passive_realization import convert
    from ..nonlinear.passive_fit import DEFAULT_GATES
    from ..geometry.discretization import GeometryDiscretizer
    from ..acoustics.transfer_matrix import input_impedance
    from ..reporting import passive_realization as native_report
    from ..reporting import state_register_context as evidence
    from . import passive_realization as native
    import numpy as np
    start=time.monotonic();r=plan['recipe'];out=Path(plan['output']);fit_started=False;tmm_calls=[]
    def remaining():
        value=r['seconds']-(time.monotonic()-start)
        if value<=0: raise TimeoutError('Complete geometry construction budget')
        return value
    try:
        ctx=load_fixed_context(plan['inputs']['config']['path'],plan['inputs']['design']['path'])
        air,loss,rad,effective=models(ctx,r)
        if effective!=plan['effective']: raise ValueError('Effective physics changed')
        def target(f,h):
            remaining();verify_plan(plan)
            mesh=GeometryDiscretizer().discretize(ctx['design'],max_segment_cm=h)
            if len(mesh.segments)>plan['mesh_segments_bound']: raise ValueError('Actual mesh exceeds bound')
            tmm_calls.append(dict(points=len(f),h_cm=h))
            z=input_impedance(f,mesh,ctx['material_db'],air,exit_radius_m=plan['physical_radius_m'],loss_model=loss,radiation_model=rad)
            remaining();return z
        f,audits=grids(r['top_hz']);z=target(f,r['h_cm'])
        # Same native ZK DC algebra as time_domain_reference and R59. The
        # positive-frequency TMM deliberately excludes omega=0.
        mesh=GeometryDiscretizer().discretize(ctx['design'],max_segment_cm=r['h_cm'])
        R0=sum(8*effective['air']['mu']*(seg.length_cm/100)/(np.pi*(seg.d_in_cm/200)**4) for seg in mesh.segments)
        report.npz_new(out/'training.npz',frequency_hz=f,real=z.real,imag=z.imag)
        poles,seeds=initial_poles(f,z,R0,r['top_hz'])
        if sum(1 if p.imag==0 else 2 for p in poles)>r['max_order']: raise ValueError('Resolved peaks exceed numerical order budget')
        report.json_new(out/'seeds.json',seeds)
        fit_started=True
        c,trace=fit(f,z,poles,R0,iterations=12,seconds=min(r['fit_seconds'],remaining()))
        c['provenance'].update(inputs={k:v['sha256'] for k,v in plan['inputs'].items()},recipe=r,effective=effective,
                              physical_radius_m=plan['physical_radius_m'],producer_sources=plan['producer_sources'])
        strict.validate_source(c);report.json_new(out/'source.json',c);report.json_new(out/'fit_trace.json',trace)
        raw=strict.read_bytes(out/'source.json');h=strict.sha(raw)
        report.json_new(out/'frozen-before-audit.json',dict(sha256=h,fit_completed=True,audit_targets_acquired=False))
        arrays={};coarse=[];admitted=[];bands=[]
        boundaries={'low':[.1,40.],'interior':[40.,3000.],'guard':[3000.,3500.],'upper':[3500.,5500.],'edge':[5500.,5990.]}
        for name,fv in audits.items():
            zv=target(fv,r['h_cm']);arrays.update({name+'_f':fv,name+'_target':zv})
            accepted=fv[-1]<=r['top_hz']
            bands.append(dict(id=name,role='audit' if accepted else 'extrapolation',domain_hz=boundaries[name],points=len(fv)))
            if accepted: admitted.append(fv);coarse.append(zv)
        report.npz_new(out/'audit.npz',**arrays)
        af=np.concatenate(admitted);coarse=np.concatenate(coarse);fine=target(af,r['h_cm']/2)
        report.npz_new(out/'mesh.npz',frequency_hz=af,coarse=coarse,fine=fine)
        mesh_error=float(np.max(abs(coarse-fine)/abs(fine)))
        if mesh_error>r['mesh_maxrel']: raise ValueError('Declared mesh convergence criterion refused')
        certificate=certify(c,h,seconds=min(r['certificate_seconds'],remaining()),max_nodes=r['max_nodes'],max_depth=r['max_depth'])
        report.json_new(out/'certificate.json',certificate)
        if certificate['status']!='certified_strict_positive_real_boundary': raise ValueError('Scalar certificate '+certificate['status'])
        strict.verify_certificate(c,certificate,h,seconds=min(30.,remaining()))
        cr=strict.read_bytes(out/'certificate.json')
        converted=convert(raw,cr,seconds=min(175.,remaining()))
        bundle=out/'realization'
        rp=native.preflight(out/'source.json',out/'certificate.json',bundle,seconds=min(175.,remaining()))
        bundle.mkdir();native_report.json_new(bundle/'plan.json',rp)
        native_report.write_new(bundle/'stdout.txt',b'');native_report.write_new(bundle/'stderr.txt',b'')
        native_report.write_result(bundle,converted,raw,cr)
        ev=dict(schema=evidence.SCHEMA,inputs={k:v['sha256'] for k,v in plan['inputs'].items()},recipe=state_recipe(r),effective=effective,
            physical_radius_m=plan['physical_radius_m'],source_sha256=h,model_sha256=digest(converted['model'].parameters()),
            domain_hz=c['training_domain_hz'],coefficient_domain=c['coefficient_domain'],acquisition=plan['acquisition'],gates=DEFAULT_GATES,bands=bands,
            training=dict(file='training.npz',sha256=strict.sha(strict.read_bytes(out/'training.npz')),points=len(f)),
            audit=dict(file='audit.npz',sha256=strict.sha(strict.read_bytes(out/'audit.npz'))))
        report.json_new(out/'evidence.json',ev)
        em,files=evidence.inspect_evidence(out/'evidence.json');fidelity=evidence.validate_evidence(em,converted['model'],files)
        verify_plan(plan);remaining()
        report.json_new(out/'result.json',dict(schema=report.SCHEMA,status='computed_pending_collection',fidelity=fidelity,mesh_maxrel=mesh_error,
            source_sha256=h,seconds=time.monotonic()-start,tmm_calls=tmm_calls,new_instrument_fits=1,new_temporal_steps=0,physical_validation='not_validated'))
        return dict(ok=True,status='computed_pending_collection')
    except Exception as exc:
        report.json_new(out/'failure.json',dict(ok=False,status='refused',reason=type(exc).__name__+': '+str(exc),seconds=time.monotonic()-start,fit_started=fit_started,tmm_calls=tmm_calls))
        return dict(ok=False,status='refused',reason=str(exc))


def _child(mode,path,out,seconds):
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    start=time.monotonic();child=None;error=None
    with (out/(mode+'-stdout.txt')).open('xb') as stdout,(out/(mode+'-stderr.txt')).open('xb') as stderr:
        try:
            child=subprocess.Popen([sys.executable,'-B','-m','tools.geometry_state','--'+mode,str(path)],cwd=ROOT,env=env,stdout=stdout,stderr=stderr,preexec_fn=limits)
            child.wait(timeout=seconds)
            if time.monotonic()-start>=seconds: raise TimeoutError('Late worker completion')
        except (Exception,KeyboardInterrupt) as exc:error=type(exc).__name__+': '+str(exc)
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:child.wait(timeout=1)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=1)
    return dict(pid=child.pid if child else None,exit_code=child.returncode if child else None,reaped=child is not None and child.poll() is not None,
                error=error,seconds=time.monotonic()-start)


def run(config,design,recipe,output_dir,*,dry_run=False):
    """Public controller; caller limits, signal handlers and timers stay untouched."""
    plan=preflight(config,design,recipe,output_dir)
    if dry_run:return dict(ok=True,status='planned',output_created=False,plan=plan)
    start=time.monotonic();out=Path(plan['output']);out.mkdir(parents=True,exist_ok=False);report.json_new(out/'plan.json',plan)
    receipt=_child('worker',out/'plan.json',out,recipe['seconds'])
    report.json_new(out/'child.json',receipt)
    if receipt['exit_code']!=0 or receipt['error'] or not receipt['reaped']:return dict(ok=False,status='partial',receipt=receipt,output=str(out))
    report.close(out,receipt)
    remaining=recipe['seconds']-(time.monotonic()-start)
    if remaining<=0:return dict(ok=False,status='partial',reason='No readback budget',output=str(out))
    read=_child('verify-pending',out,out,remaining)
    ok=read['exit_code']==0 and read['reaped'] and read['error'] is None
    if ok:report.publish_complete(out,read)
    else:report.json_new(out/'reader-failure.json',read)
    return dict(ok=ok,status='geometry_state_verified' if ok else 'partial',output=str(out),receipt=receipt,reader=read)
