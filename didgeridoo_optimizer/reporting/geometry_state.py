"""Geometry-state integrity and semantic readback; no new acoustic acquisition."""
from pathlib import Path
import hashlib
import io
import math
import os
import stat
import re
import time
import importlib
import sys
from types import FunctionType, CodeType

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


# Fixed minimum, independent of whichever unrelated modules a reader imported.
from . import register_target as _register
from ..nonlinear.passive_state import STATE_PORT_SOURCES
REQUIRED_SOURCES = set(STATE_PORT_SOURCES) | set(NEW_SOURCES) | _register.REQUIRED_SOURCES | set(strict.SOURCES) | {
    'didgeridoo_optimizer/acoustics/'+name+'.py' for name in
    ('transfer_matrix','air','losses','thermoviscous','radiation','radiation_models')}
REQUIRED_SOURCES |= {'didgeridoo_optimizer/'+name+'.py' for name in (
    'geometry/models','geometry/builders','geometry/discretization',
    'materials/database','materials/models','materials/variants',
    'pipeline/fixed_design','pipeline/design_pitch','pipeline/time_domain_reference',
    'optimization/design_contract','pipeline/design_input','pipeline/run_optimizer','pipeline/evaluate_linear')}
def _verify_loaded_code(module,path,raw):
    """Native modules without snapshots must match freshly compiled source.

    This checks functions and methods already imported before this boundary;
    it never executes the compiled module or alters the import machinery.
    """
    compiled={}
    def collect(code):
        compiled[code.co_qualname]=code
        for item in code.co_consts:
            if isinstance(item,CodeType): collect(item)
    collect(compile(raw,str(path),'exec',dont_inherit=True,optimize=sys.flags.optimize))
    def functions(values):
        for value in values:
            if isinstance(value,(classmethod,staticmethod)): value=value.__func__
            if isinstance(value,property):
                yield from functions([value.fget,value.fset,value.fdel])
            elif isinstance(value,FunctionType): yield value
            elif isinstance(value,type) and value.__module__==module.__name__:
                yield from functions(vars(value).values())
    import inspect,dataclasses
    for value in functions(tuple(vars(module).values())):
        value=inspect.unwrap(value)
        if (value.__module__!=module.__name__ or value.__code__.co_filename.startswith('<')
                or value.__code__.co_filename==dataclasses.__file__): continue
        code=value.__code__
        if Path(code.co_filename).resolve()!=path or code!=compiled.get(code.co_qualname):
            raise ValueError('Source changed before geometry import: '+str(path))


# Capture native modules lacking their own import snapshots before admission.
_IMPORT_SOURCES = {}
for _name in sorted(REQUIRED_SOURCES):
    if not _name.startswith('tools/'):
        _module = importlib.import_module(_name[:-3].replace('/','.'))
        _raw=strict.read_bytes(ROOT/_name)
        if not hasattr(_module,'_LOADED_SOURCE_SHA256'): _verify_loaded_code(_module,ROOT/_name,_raw)
        _IMPORT_SOURCES[_name] = getattr(_module, '_LOADED_SOURCE_SHA256', strict.sha(_raw))


def _source_path(name):
    if (type(name) is not str or not name.endswith('.py') or '\\' in name
            or Path(name).is_absolute() or Path(name).as_posix()!=name
            or any(x in ('','.','..') for x in name.split('/'))):
        raise ValueError('Ambiguous/non-Python producer source')
    p=strict.safe_path(ROOT/name)
    if not p.is_relative_to(ROOT) or p!=ROOT/name: raise ValueError('Producer source outside root')
    return p


def _loaded(name,h):
    module=sys.modules.get(name[:-3].replace('/','.'))
    main=sys.modules.get('__main__')
    modules=[module]
    if getattr(main,'__file__',None) and Path(main.__file__).resolve()==ROOT/name: modules.append(main)
    for module in modules:
        if module is None: continue
        if Path(module.__file__).resolve()!=ROOT/name: raise ValueError('Loaded source root mismatch')
        snapshot=getattr(module,'_LOADED_SOURCE_SHA256',getattr(module,'ENTRY_SHA',_IMPORT_SOURCES.get(name)))
        if snapshot is not None and snapshot!=h: raise ValueError('Source changed after import: '+name)
        for value in tuple(vars(module).values()):
            if name in NEW_SOURCES and isinstance(value,FunctionType) and value.__module__==module.__name__ and Path(value.__code__.co_filename).resolve()!=ROOT/name:
                raise ValueError('Function source mismatch: '+name)


def sources():
    result=_register.sources()
    paths=set(REQUIRED_SOURCES)|set(result)
    for name,module in tuple(sys.modules.items()):
        if name=='didgeridoo_optimizer' or name.startswith('didgeridoo_optimizer.'):
            if getattr(module,'__file__',None) and '.tests' not in name:
                paths.add(Path(module.__file__).resolve().relative_to(ROOT).as_posix())
    for name in paths:
        h=strict.sha(strict.read_bytes(_source_path(name)));_loaded(name,h);result[name]=h
    return result


def verify_sources(expected):
    if type(expected) is not dict or not REQUIRED_SOURCES<=expected.keys() or len(expected)>128:
        raise ValueError('Incomplete producer source inventory')
    for name,h in expected.items():
        strict.hash_string(h)
        if strict.sha(strict.read_bytes(_source_path(name)))!=h: raise ValueError('Producer source changed: '+name)
        # Extras bind their declared bytes, not unrelated ambient imports.
        if name in REQUIRED_SOURCES: _loaded(name,h)


def exact(value,keys,name):
    if type(value) is not dict or set(value)!=set(keys): raise ValueError(name+' schema')


def number(value,name,low,high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(name+' finite domain')
    return value


def validate_receipt(child,seconds,*,reader=False,out=None):
    public={'pid','exit_code','reaped','error','seconds','operation_seconds','collection_seconds',
            'collection_limit_seconds','collected','pipes_closed','stdout_bytes','stderr_bytes','limit_seconds'}
    played={'kind','pid','exit_code','launched','reaped','seconds','operation_seconds','collection_seconds',
            'collection_limit_seconds','timeout_or_error','log','input_bytes','steps_charged',
            'collected','pipes_closed','log_bytes','limit_seconds'}
    if type(child) is not dict: raise ValueError('Child receipt schema')
    is_public='error' in child
    exact(child,public if is_public else played,'Child receipt')
    if type(child['pid']) is not int or child['pid']<=0 or type(child['exit_code']) is not int or child['exit_code']!=0:
        raise ValueError('Child PID/exit receipt')
    if any(child[k] is not True for k in ('reaped','collected','pipes_closed')): raise ValueError('Incomplete child collection')
    if child['error' if is_public else 'timeout_or_error'] is not None: raise ValueError('Failed child receipt')
    limit=number(child['limit_seconds'],'Child deadline',0.000001,seconds)
    operation=number(child['operation_seconds'],'Child operation',0,limit)
    collection=number(child['collection_seconds'],'Child collection',0,4)
    total=number(child['seconds'],'Child elapsed',0,limit+4)
    if operation>=limit or child['collection_limit_seconds']!=4 or type(child['collection_limit_seconds']) is not int or total<operation+collection:
        raise ValueError('Late/inconsistent child receipt')
    for k in (('stdout_bytes','stderr_bytes') if is_public else ('log_bytes','input_bytes')):
        strict.integer(child[k],k,0,(8 if k=='log_bytes' else 4)*1024**2)
    if not is_public:
        if child['launched'] is not True or child['kind']!=('read' if reader else 'fit') or type(child['steps_charged']) is not int or child['steps_charged']!=0:
            raise ValueError('Played child receipt')
        if type(child['log']) is not str or re.fullmatch(r'child-[0-9]{4,}\.log',child['log']) is None: raise ValueError('Child log')
    if out is not None:
        for path,size in receipt_logs(out,child,reader=reader).items():
            if len(_log_bytes(path))!=size: raise ValueError('Child log size mismatch')
    return dict(created=True,exit_code=0,reaped=child['reaped'],collected=child['collected'],pipes_closed=child['pipes_closed'],pid=child['pid'])


def _log_bytes(path):
    # The shared played controller allows an 8 MiB log; geometry files stay 4.
    p=strict.safe_path(path);cap=8*1024**2
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>cap: raise ValueError('Child log budget')
        raw=stream.read(cap+1);after=os.fstat(stream.fileno())
    signature=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
    if len(raw)>cap or signature(before)!=signature(after) or signature(after)!=signature(strict.safe_path(p).stat()):
        raise ValueError('Child log changed during read')
    return raw


def _pin_sha(path):
    raw=_log_bytes(path) if re.fullmatch(r'child-[0-9]{4,}\.log',Path(path).name) else strict.read_bytes(path)
    return strict.sha(raw)


def receipt_logs(out,child,*,reader=False):
    out=Path(out)
    if 'error' in child:
        mode='verify-pending' if reader else 'worker'
        return {out/(mode+'-'+stream+'.txt'):child[stream+'_bytes'] for stream in ('stdout','stderr')}
    return {out.parent.parent/child['log']:child['log_bytes']}


def compute_receipt(out,plan,execution):
    child=execution['child']
    native=validate_receipt(child,plan['recipe']['seconds'],out=out)
    if strict.strict_json(strict.read_json(Path(out)/'child.json'))!=strict.strict_json(child) or strict.strict_json(strict.read_json(Path(out)/'realization/execution.json')['child'])!=strict.strict_json(native):
        raise ValueError('Compute/native receipt contradiction')
    return child


def native_dc(plan):
    from ..pipeline.fixed_design import load_fixed_context
    from ..pipeline.design_pitch import models
    from ..geometry.discretization import GeometryDiscretizer
    ctx=load_fixed_context(plan['inputs']['config']['path'],plan['inputs']['design']['path'])
    effective=models(ctx,plan['recipe'])[3]
    mesh=GeometryDiscretizer().discretize(ctx['design'],max_segment_cm=plan['recipe']['h_cm'])
    return sum(8*effective['air']['mu']*(s.length_cm/100)/(math.pi*(s.d_in_cm/200)**4) for s in mesh.segments)


def verify_dc(source,plan):
    declared=number(source['provenance']['native_dc_pa_s_m3'],'Native DC',0,1e100)
    expected=native_dc(plan)
    terms=[source['direct_normalized']];k=0
    for a,b in zip(source['p_real'],source['p_imag']):
        u=source['residues'][k];k+=1
        if b==0: terms.append(-u/a)
        else:
            v=source['residues'][k];k+=1
            terms.append(-2*(a*u+b*v)/(a*a+b*b))
    scalar=source['zscale']*math.fsum(terms)
    if abs(declared-expected)>2e-8*max(abs(expected),1e-30) or abs(scalar-declared)>2e-8*max(abs(declared),1e-30):
        raise ValueError('Frozen scalar/native DC mismatch')


def validate_result(result,plan,source_sha,checks,error):
    exact(result,{'schema','status','fidelity','mesh_maxrel','source_sha256','seconds','tmm_calls',
                  'new_instrument_fits','new_temporal_steps','physical_validation'},'Geometry result')
    if (result['schema']!=SCHEMA or result['status']!='computed_pending_collection'
            or result['source_sha256']!=source_sha or result['physical_validation']!='not_validated'
            or type(result['new_instrument_fits']) is not int or result['new_instrument_fits']!=1
            or type(result['new_temporal_steps']) is not int or result['new_temporal_steps']!=0):
        raise ValueError('Geometry result identity/status/counters')
    number(result['seconds'],'Result elapsed',1e-12,plan['recipe']['seconds'])
    number(result['mesh_maxrel'],'Mesh error',0,plan['recipe']['mesh_maxrel'])
    n=1859 if plan['recipe']['top_hz']==3500 else 2868
    expected=[dict(points=p,h_cm=plan['recipe']['h_cm']) for p in (6528,257,1201,401,1009,257)]
    expected.append(dict(points=n,h_cm=plan['recipe']['h_cm']/2))
    calls=result['tmm_calls']
    if type(calls) is not list or len(calls)!=7: raise ValueError('TMM acquisition inventory')
    for call in calls:
        exact(call,{'points','h_cm'},'TMM call');strict.integer(call['points'],'TMM points',1,6528)
        number(call['h_cm'],'TMM mesh',.025,2)
    if calls!=expected or result['mesh_maxrel']!=error or strict.strict_json(result['fidelity'])!=strict.strict_json(checks):
        raise ValueError('Geometry mesh/fidelity/acquisition evidence changed')


def json_new(path,value):
    realization.json_new(path,value)


def npz_new(path,**arrays):
    import numpy as np
    data=io.BytesIO();np.savez(data,**arrays)
    realization.write_new(path,data.getvalue())


def close(out,child):
    """Controller-only closure using its actual collected compute child receipt."""
    out=Path(out)
    from ..pipeline.geometry_state import verify_plan
    plan=strict.read_json(out/'plan.json');verify_plan(plan)
    native_child=validate_receipt(child,plan['recipe']['seconds'],out=out)
    if strict.strict_json(strict.read_json(out/'child.json'))!=strict.strict_json(child): raise ValueError('Compute receipt differs')
    bundle=out/'realization'
    realization.write_manifest(bundle)
    json_new(bundle/'execution.json',dict(ok=True,status='represented',error=None,child=native_child,
        cancelled=[],manifest_sha256=strict.sha(strict.read_bytes(bundle/'manifest.json'))))
    json_new(bundle/'execution.closed.json',dict(execution_sha256=strict.sha(strict.read_bytes(bundle/'execution.json')),cancelled=False))
    files={p.relative_to(out).as_posix():strict.sha(strict.read_bytes(p)) for p in out.rglob('*') if p.is_file()}
    json_new(out/'manifest.json',dict(schema=SCHEMA,files=files))
    json_new(out/'execution.json',dict(child=child,manifest_sha256=strict.sha(strict.read_bytes(out/'manifest.json'))))
    json_new(out/'execution.closed.json',dict(execution_sha256=strict.sha(strict.read_bytes(out/'execution.json'))))


def read_payload(out, *, config=None, design=None, recipe=None, _pins=None):
    from ..pipeline.geometry_state import verify_plan, state_recipe
    from ..pipeline.state_register_context import case_context, load_verified
    import numpy as np
    start=time.monotonic();out=strict.safe_path(out)
    def deadline():
        if time.monotonic()-start>=175: raise TimeoutError('Geometry reader wall budget')
    pins={} if _pins is None else _pins
    def pinned(path):
        deadline();path=Path(path);raw=strict.read_bytes(path);h=strict.sha(raw)
        if path in pins and pins[path]!=h: raise ValueError('Artifact changed during reading: '+str(path))
        pins[path]=h;return raw
    manifest=strict.parse_json(pinned(out/'manifest.json'));execution=strict.parse_json(pinned(out/'execution.json'))
    pinned(out/'execution.closed.json')
    exact(manifest,{'schema','files'},'Geometry manifest')
    exact(execution,{'child','manifest_sha256'},'Geometry execution')
    if manifest.get('schema')!=SCHEMA or execution['manifest_sha256']!=strict.sha(strict.read_bytes(out/'manifest.json')):
        raise ValueError('Geometry manifest mismatch')
    if strict.read_json(out/'execution.closed.json')!=dict(execution_sha256=strict.sha(strict.read_bytes(out/'execution.json'))):
        raise ValueError('Geometry closure mismatch')
    child=execution['child']
    required={'child.json','plan.json','source.json','fit_trace.json','seeds.json','frozen-before-audit.json','training.npz','audit.npz','mesh.npz','evidence.json','result.json'}
    required|={'realization/'+p for p in realization.REQUIRED_FILES|{'manifest.json','execution.json','execution.closed.json'}}
    if type(manifest['files']) is not dict or len(manifest['files'])>64: raise ValueError('Geometry file inventory')
    if not required<=manifest['files'].keys(): raise ValueError('Incomplete geometry bundle')
    if (out/'failure.json').exists() or (out/'reader-failure.json').exists(): raise ValueError('Failed geometry execution')
    total=0
    for name,h in manifest['files'].items():
        strict.hash_string(h)
        if Path(name).is_absolute() or Path(name).as_posix()!=name or any(x in ('','.','..') for x in name.split('/')) or '\\' in name: raise ValueError('Ambiguous artifact path')
        data=pinned(out/name);total+=len(data)
        if total>32*1024**2: raise ValueError('Geometry output budget')
        if strict.sha(data)!=h:
            raise ValueError('Geometry file mismatch: '+name)
    plan=strict.parse_json(pinned(out/'plan.json'));verify_plan(plan)
    compute_receipt(out,plan,execution)
    for item in plan['inputs'].values():
        pins[Path(item['path'])]=item['sha256']
    for name,h in plan['producer_sources'].items(): pins[ROOT/name]=h
    for path in receipt_logs(out,child): pins[path]=_pin_sha(path)
    if recipe is not None and recipe!=plan['recipe']: raise ValueError('Fit recipe association')
    for name,path in (('config',config),('design',design)):
        if path is not None and strict.sha(strict.read_bytes(path))!=plan['inputs'][name]['sha256']:
            raise ValueError('Geometry input association: '+name)
    source=strict.validate_source(strict.read_json(out/'source.json'))
    if (source['schema']!=strict.SOURCE_SCHEMA or not 1<=source['order']<=plan['recipe']['max_order']
            or source['wref']!=2*math.pi*1000 or source['coefficient_domain']!='discrete_prewarped'):
        raise ValueError('Scalar recipe order/convention')
    expected=dict(inputs={k:v['sha256'] for k,v in plan['inputs'].items()},recipe=plan['recipe'],
                  effective=plan['effective'],physical_radius_m=plan['physical_radius_m'],
                  producer_sources=plan['producer_sources'],native_dc_pa_s_m3=source['provenance']['native_dc_pa_s_m3'])
    if strict.strict_json(source['provenance'])!=strict.strict_json(expected) or source['coefficient_origin']!='Native TMM geometry scalar VF':
        raise ValueError('Scalar origin/provenance mismatch')
    verify_dc(source,plan)
    frozen=strict.read_json(out/'frozen-before-audit.json')
    if frozen!=dict(sha256=strict.sha(strict.read_bytes(out/'source.json')),fit_completed=True,audit_targets_acquired=False):
        raise ValueError('Freeze evidence mismatch')
    if strict.read_bytes(out/'source.json')!=strict.read_bytes(out/'realization/source.json'): raise ValueError('Source mutated at conversion')
    case=dict(kind='saved_state',id='geometry',config=plan['inputs']['config']['path'],design=plan['inputs']['design']['path'],
              realization=str(out/'realization'),evidence=str(out/'evidence.json'),recipe=state_recipe(plan['recipe']))
    deadline();meta,_,_=case_context(case);model,checks=load_verified(case,meta,None,None);deadline()
    if meta['evidence']['acquisition']!=plan['acquisition']: raise ValueError('Acquisition provenance changed')
    # Check the reserved grids independently, without calling the fitter.
    train=np.r_[np.geomspace(.1,40,384,endpoint=False),np.linspace(40,plan['recipe']['top_hz'],6144)]
    def shifted(a,b,n):return a+(np.arange(n)+.3141592653589793)*(b-a)/n
    grids=dict(low=np.exp(shifted(np.log(.1),np.log(40),257)),interior=shifted(40,3000,1201),
               guard=shifted(3000,3500,401),upper=shifted(3500,5500,1009),edge=shifted(5500,5990,257))
    headers=evidence._expected(meta['evidence'])
    def npz(name,expected):
        raw=pinned(out/name);evidence._npz_headers(raw,expected)
        return np.load(io.BytesIO(raw),allow_pickle=False)
    with npz('training.npz',headers['training']) as z:
        if not np.array_equal(z['frequency_hz'],train): raise ValueError('Training grid changed')
    audit_coarse=[]
    with npz('audit.npz',headers['audit']) as z:
        for name,f in grids.items():
            if f[-1]<=plan['recipe']['top_hz']: audit_coarse.append(z[name+'_target'])
            if not np.array_equal(z[name+'_f'],f): raise ValueError('Reserved audit grid changed')
    admitted=np.concatenate([f for f in grids.values() if f[-1]<=plan['recipe']['top_hz']])
    # Header budget before mesh arrays. Three arrays, exact point count and dtype.
    mesh_headers={k+'.npy':((len(admitted),),np.dtype(d)) for k,d in [('frequency_hz','float64'),('coarse','complex128'),('fine','complex128')]}
    with npz('mesh.npz',mesh_headers) as z:
        if not np.array_equal(z['coarse'],np.concatenate(audit_coarse)): raise ValueError('Mesh coarse differs from acquired audit')
        if not np.array_equal(z['frequency_hz'],admitted): raise ValueError('Mesh admitted frequencies changed')
        if not np.isfinite(z['coarse']).all() or not np.isfinite(z['fine']).all() or np.any(abs(z['fine'])==0): raise ValueError('Mesh finite nonzero reference')
        error=float(np.max(abs(z['coarse']-z['fine'])/abs(z['fine'])))
    result=strict.read_json(out/'result.json')
    validate_result(result,plan,frozen['sha256'],checks['finite_fidelity'],error)
    if result['seconds']>child['operation_seconds']: raise ValueError('Result exceeds compute receipt')
    trace=strict.read_json(out/'fit_trace.json')
    if type(trace) is not list or len(trace)!=12: raise ValueError('Incomplete full-rank VF trace')
    previous=0.
    for i,t in enumerate(trace):
        exact(t,{'iteration','order','rank','residue_rank','reflected_poles','weighted_residual','seconds'},'VF trace')
        for k,v in dict(iteration=i+1,order=source['order'],rank=2*source['order'],residue_rank=source['order']).items():
            strict.integer(t[k],k,v,v)
        strict.integer(t['reflected_poles'],'Reflections',0,source['order'])
        number(t['weighted_residual'],'Residual',0,1e100)
        previous=number(t['seconds'],'Iteration elapsed',previous,min(plan['recipe']['fit_seconds'],result['seconds']))
    verify_plan(plan)
    for path,h in pins.items():
        if _pin_sha(path)!=h: raise ValueError('Artifact changed during reading: '+str(path))
    deadline()
    return dict(ok=True,status='geometry_state_verified',model_sha256=meta['model_sha256'],metadata=meta,
                source_sha256=frozen['sha256'],mesh_maxrel=error)


def publish_complete(out,reader):
    out=Path(out)
    plan=strict.read_json(out/'plan.json')
    from ..pipeline.geometry_state import verify_plan
    verify_plan(plan)
    validate_receipt(reader,175,reader=True,out=out)
    compute_receipt(out,plan,strict.read_json(out/'execution.json'))
    if (out/'failure.json').exists() or (out/'reader-failure.json').exists(): raise ValueError('Failed geometry execution')
    # The fresh reader has returned; no changed acquired byte may be published.
    raw=strict.read_bytes(out/'manifest.json');manifest=strict.parse_json(raw)
    execution_raw=strict.read_bytes(out/'execution.json');execution=strict.parse_json(execution_raw)
    exact(manifest,{'schema','files'},'Geometry manifest')
    if (manifest['schema']!=SCHEMA or execution['manifest_sha256']!=strict.sha(raw)
            or strict.read_json(out/'execution.closed.json')!=dict(execution_sha256=strict.sha(execution_raw))):
        raise ValueError('Publication closure contradiction')
    required={'child.json','plan.json','source.json','fit_trace.json','seeds.json','frozen-before-audit.json','training.npz','audit.npz','mesh.npz','evidence.json','result.json'}
    required|={'realization/'+p for p in realization.REQUIRED_FILES|{'manifest.json','execution.json','execution.closed.json'}}
    if type(manifest['files']) is not dict or not required<=manifest['files'].keys() or len(manifest['files'])>64:
        raise ValueError('Publication inventory')
    for name,h in manifest['files'].items():
        if Path(name).is_absolute() or Path(name).as_posix()!=name or any(x in ('','.','..') for x in name.split('/')) or '\\' in name:
            raise ValueError('Publication artifact path')
        if strict.sha(strict.read_bytes(out/name))!=h: raise ValueError('Artifact changed before publication')
    json_new(out/'reader.json',reader)
    json_new(out/'complete.json',dict(schema=SCHEMA,reader_sha256=strict.sha(strict.read_bytes(out/'reader.json')),
        execution_sha256=strict.sha(strict.read_bytes(out/'execution.json')),
        manifest_sha256=strict.sha(strict.read_bytes(out/'manifest.json'))))


def read_closure(out):
    out=Path(out);reader=strict.read_json(out/'reader.json');complete=strict.read_json(out/'complete.json')
    if complete!=dict(schema=SCHEMA,reader_sha256=strict.sha(strict.read_bytes(out/'reader.json')),
                     execution_sha256=strict.sha(strict.read_bytes(out/'execution.json')),
                     manifest_sha256=strict.sha(strict.read_bytes(out/'manifest.json'))): raise ValueError('Geometry terminal read closure')
    validate_receipt(reader,175,reader=True,out=out)


def read_result(out,**kwargs):
    out=Path(out)
    pins={out/name:strict.sha(strict.read_bytes(out/name)) for name in ('reader.json','complete.json')}
    read_closure(out)
    reader=strict.read_json(out/'reader.json')
    pins.update({path:_pin_sha(path) for path in receipt_logs(out,reader,reader=True)})
    value=read_payload(out,_pins=pins,**kwargs)
    read_closure(out)
    for path,h in pins.items():
        if _pin_sha(path)!=h: raise ValueError('Artifact/input/source changed at final read closure')
    return value
