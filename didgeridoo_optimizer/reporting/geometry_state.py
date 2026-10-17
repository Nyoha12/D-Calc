"""Geometry-state integrity and semantic readback; no new acoustic acquisition."""
from pathlib import Path
import hashlib
import io
import sys
from types import FunctionType

from ..nonlinear import scalar_passivity_certificate as strict
from ..nonlinear.passive_resonator import digest
from . import passive_realization as realization
from . import state_register_context as evidence

ROOT=Path(__file__).resolve().parents[2]
_LOADED_SOURCE_PATH=Path(__file__).resolve()
_LOADED_SOURCE_SHA256=hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
NEW_SOURCES=('didgeridoo_optimizer/nonlinear/scalar_fit.py',
 'didgeridoo_optimizer/nonlinear/scalar_certificate_builder.py',
 'didgeridoo_optimizer/pipeline/geometry_state.py',
 'didgeridoo_optimizer/reporting/geometry_state.py','tools/geometry_state.py')
SCHEMA='dcalc.geometry_state.v1'


def sources():
    from . import register_target
    result=register_target.sources()
    # Pin all actual loaded native Python sources, including TMM/material readers.
    paths=set(NEW_SOURCES)
    for name,module in tuple(sys.modules.items()):
        if name=='didgeridoo_optimizer' or name.startswith('didgeridoo_optimizer.'):
            p=Path(module.__file__).resolve()
            paths.add(p.relative_to(ROOT).as_posix())
    for name in paths:
        p=ROOT/name; h=strict.sha(strict.read_bytes(p));result[name]=h
        module=sys.modules.get(name[:-3].replace('/','.'))
        if module is not None:
            if Path(module.__file__).resolve()!=p: raise ValueError('Loaded source root mismatch')
            snapshot=getattr(module,'_LOADED_SOURCE_SHA256',None)
            if snapshot is not None and snapshot!=h: raise ValueError('Source changed after import: '+name)
            for value in tuple(vars(module).values()):
                if name in NEW_SOURCES and isinstance(value,FunctionType) and value.__module__==module.__name__ and Path(value.__code__.co_filename).resolve()!=p:
                    raise ValueError('Function source mismatch: '+name)
    return result


def verify_sources(expected):
    current=sources()
    for name,h in expected.items():
        if Path(name).is_absolute() or '..' in Path(name).parts or strict.sha(strict.read_bytes(ROOT/name))!=h: raise ValueError('Producer source changed: '+name)


def json_new(path,value):
    realization.json_new(path,value)


def npz_new(path,**arrays):
    import numpy as np
    data=io.BytesIO();np.savez(data,**arrays)
    realization.write_new(path,data.getvalue())


def close(out,child):
    """Controller-only closure using its actual collected compute child receipt."""
    out=Path(out)
    if child['exit_code']!=0 or child['reaped'] is not True: raise ValueError('Compute child not confirmed')
    bundle=out/'realization'
    realization.write_manifest(bundle)
    native_child=dict(created=True,exit_code=child['exit_code'],reaped=True,collected=True,pipes_closed=True,pid=child['pid'])
    json_new(bundle/'execution.json',dict(ok=True,status='represented',error=None,child=native_child,
        cancelled=[],manifest_sha256=strict.sha(strict.read_bytes(bundle/'manifest.json'))))
    json_new(bundle/'execution.closed.json',dict(execution_sha256=strict.sha(strict.read_bytes(bundle/'execution.json')),cancelled=False))
    files={p.relative_to(out).as_posix():strict.sha(strict.read_bytes(p)) for p in out.rglob('*') if p.is_file()}
    json_new(out/'manifest.json',dict(schema=SCHEMA,files=files))
    json_new(out/'execution.json',dict(child=child,manifest_sha256=strict.sha(strict.read_bytes(out/'manifest.json'))))
    json_new(out/'execution.closed.json',dict(execution_sha256=strict.sha(strict.read_bytes(out/'execution.json'))))


def read_payload(out, *, config=None, design=None, recipe=None):
    from ..pipeline.geometry_state import verify_plan, state_recipe
    from ..pipeline.state_register_context import case_context, load_verified
    import numpy as np
    out=strict.safe_path(out)
    manifest=strict.read_json(out/'manifest.json');execution=strict.read_json(out/'execution.json')
    if manifest.get('schema')!=SCHEMA or execution['manifest_sha256']!=strict.sha(strict.read_bytes(out/'manifest.json')):
        raise ValueError('Geometry manifest mismatch')
    if strict.read_json(out/'execution.closed.json')!=dict(execution_sha256=strict.sha(strict.read_bytes(out/'execution.json'))):
        raise ValueError('Geometry closure mismatch')
    child=execution['child']
    if child['exit_code']!=0 or child['reaped'] is not True or not child['pid']>0: raise ValueError('Geometry child receipt')
    required={'plan.json','source.json','fit_trace.json','seeds.json','frozen-before-audit.json','training.npz','audit.npz','mesh.npz','evidence.json','result.json'}
    required|={'realization/'+p for p in realization.REQUIRED_FILES|{'manifest.json','execution.json','execution.closed.json'}}
    if not required<=manifest['files'].keys(): raise ValueError('Incomplete geometry bundle')
    for name,h in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or strict.sha(strict.read_bytes(out/name))!=h:
            raise ValueError('Geometry file mismatch: '+name)
    plan=strict.read_json(out/'plan.json');verify_plan(plan)
    if recipe is not None and recipe!=plan['recipe']: raise ValueError('Fit recipe association')
    for name,path in (('config',config),('design',design)):
        if path is not None and strict.sha(strict.read_bytes(path))!=plan['inputs'][name]['sha256']:
            raise ValueError('Geometry input association: '+name)
    source=strict.validate_source(strict.read_json(out/'source.json'))
    expected=dict(inputs={k:v['sha256'] for k,v in plan['inputs'].items()},recipe=plan['recipe'],
                  effective=plan['effective'],physical_radius_m=plan['physical_radius_m'],
                  producer_sources=plan['producer_sources'],native_dc_pa_s_m3=source['provenance']['native_dc_pa_s_m3'])
    if source['provenance']!=expected or source['coefficient_origin']!='Native TMM geometry scalar VF':
        raise ValueError('Scalar origin/provenance mismatch')
    frozen=strict.read_json(out/'frozen-before-audit.json')
    if frozen!=dict(sha256=strict.sha(strict.read_bytes(out/'source.json')),fit_completed=True,audit_targets_acquired=False):
        raise ValueError('Freeze evidence mismatch')
    if strict.read_bytes(out/'source.json')!=strict.read_bytes(out/'realization/source.json'): raise ValueError('Source mutated at conversion')
    case=dict(kind='saved_state',id='geometry',config=plan['inputs']['config']['path'],design=plan['inputs']['design']['path'],
              realization=str(out/'realization'),evidence=str(out/'evidence.json'),recipe=state_recipe(plan['recipe']))
    meta,_,_=case_context(case);model,checks=load_verified(case,meta,None,None)
    if meta['evidence']['acquisition']!=plan['acquisition']: raise ValueError('Acquisition provenance changed')
    # The exact reserved grids are checked without importing or running the fitter.
    train=np.r_[np.geomspace(.1,40,384,endpoint=False),np.linspace(40,plan['recipe']['top_hz'],6144)]
    def shifted(a,b,n):return a+(np.arange(n)+.3141592653589793)*(b-a)/n
    grids=dict(low=np.exp(shifted(np.log(.1),np.log(40),257)),interior=shifted(40,3000,1201),
               guard=shifted(3000,3500,401),upper=shifted(3500,5500,1009),edge=shifted(5500,5990,257))
    with np.load(io.BytesIO(strict.read_bytes(out/'training.npz')),allow_pickle=False) as z:
        if not np.array_equal(z['frequency_hz'],train): raise ValueError('Training grid changed')
    audit_coarse=[]
    with np.load(io.BytesIO(strict.read_bytes(out/'audit.npz')),allow_pickle=False) as z:
        for name,f in grids.items():
            if f[-1]<=plan['recipe']['top_hz']: audit_coarse.append(z[name+'_target'])
            if not np.array_equal(z[name+'_f'],f): raise ValueError('Reserved audit grid changed')
    admitted=np.concatenate([f for f in grids.values() if f[-1]<=plan['recipe']['top_hz']])
    # Header budget before mesh arrays. Three arrays, exact point count and dtype.
    evidence._npz_headers(strict.read_bytes(out/'mesh.npz'),{k+'.npy':((len(admitted),),np.dtype(d)) for k,d in [('frequency_hz','float64'),('coarse','complex128'),('fine','complex128')]})
    with np.load(io.BytesIO(strict.read_bytes(out/'mesh.npz')),allow_pickle=False) as z:
        if not np.array_equal(z['coarse'],np.concatenate(audit_coarse)): raise ValueError('Mesh coarse differs from acquired audit')
        if not np.array_equal(z['frequency_hz'],admitted): raise ValueError('Mesh admitted frequencies changed')
        if not np.isfinite(z['coarse']).all() or not np.isfinite(z['fine']).all() or np.any(abs(z['fine'])==0): raise ValueError('Mesh finite nonzero reference')
        error=float(np.max(abs(z['coarse']-z['fine'])/abs(z['fine'])))
    result=strict.read_json(out/'result.json')
    if error>plan['recipe']['mesh_maxrel'] or result['mesh_maxrel']!=error or result['fidelity']!=checks['finite_fidelity']:
        raise ValueError('Geometry mesh/fidelity evidence changed')
    trace=strict.read_json(out/'fit_trace.json')
    if len(trace)!=12 or any(t['iteration']!=i+1 or t['order']!=source['order'] or t['rank']!=2*source['order'] or t['residue_rank']!=source['order'] for i,t in enumerate(trace)):
        raise ValueError('Incomplete full-rank VF trace')
    verify_plan(plan)
    return dict(ok=True,status='geometry_state_verified',model_sha256=meta['model_sha256'],metadata=meta,
                source_sha256=frozen['sha256'],mesh_maxrel=error)


def publish_complete(out,reader):
    out=Path(out)
    if reader['exit_code']!=0 or reader['reaped'] is not True or reader.get('error') or reader.get('timeout_or_error'):
        raise ValueError('Fresh geometry reader not collected successfully')
    json_new(out/'reader.json',reader)
    json_new(out/'complete.json',dict(schema=SCHEMA,reader_sha256=strict.sha(strict.read_bytes(out/'reader.json')),
        execution_sha256=strict.sha(strict.read_bytes(out/'execution.json')),
        manifest_sha256=strict.sha(strict.read_bytes(out/'manifest.json'))))


def read_closure(out):
    out=Path(out);reader=strict.read_json(out/'reader.json');complete=strict.read_json(out/'complete.json')
    if complete!=dict(schema=SCHEMA,reader_sha256=strict.sha(strict.read_bytes(out/'reader.json')),
                     execution_sha256=strict.sha(strict.read_bytes(out/'execution.json')),
                     manifest_sha256=strict.sha(strict.read_bytes(out/'manifest.json'))): raise ValueError('Geometry terminal read closure')
    if reader['exit_code']!=0 or reader['reaped'] is not True or reader.get('error') or reader.get('timeout_or_error'):
        raise ValueError('Geometry read not confirmed')


def read_result(out,**kwargs):
    read_closure(out)
    value=read_payload(out,**kwargs)
    read_closure(out)
    return value
