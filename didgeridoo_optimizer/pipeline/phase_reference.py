"""Offline adapter to the frozen native checkpoint/NPZ readers; never simulate.

Private _history/_load_history remain versioned, tested dependencies. No CONFIG,
DESIGN, model file or current fit source is needed to interpret saved arrays.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import signal
import time
import numpy as np

from . import regime_reference as history
from ..nonlinear.phase_reference import PhasePlan, analyze, AnalysisStopped
from ..nonlinear.passive_resonator import digest
from ..nonlinear.regime_observables import ObservationPlan
from ..reporting import regime_reference as native
from ..reporting import phase_reference as report

ROOT=Path(__file__).resolve().parents[2]


def _identity(record):
    cp=record['checkpoint'];p=cp['payload'];identity=p['identity'];compat=record['compatibility']
    if set(p)!={'schema','identity','initial_state','snapshot'} or p['schema']!='dcalc.simultaneous.checkpoint.v1':
        raise ValueError('Native checkpoint schema required')
    expected=dict(schema='dcalc.simultaneous.identity.v1',model_sha256=compat['model_parameters_sha256'],
        parameters=compat['parameters'],rho_kg_m3=compat['rho_kg_m3'],sample_rate_hz=compat['fs'],
        v2_port_model=compat['v2_port_model'],schedule='simultaneous',max_extensions=compat['max_extensions'],max_iterations=compat['max_iterations'])
    if identity!=expected: raise ValueError('Checkpoint/compatibility identity mismatch')
    fs=identity['sample_rate_hz']
    if type(fs) is not int or not 1000<=fs<=12000: raise ValueError('Native fs budget')
    s=p['snapshot'];n=len(s['state'])
    if set(s)!={'state','time_s','last_dissipation_w','diagnostics'} or not 2<=n<=386 or n%2 or len(p['initial_state'])!=n:
        raise ValueError('Complete native snapshot required')
    if not all(type(x) in (int,float) and np.isfinite(x) for x in [*s['state'],*p['initial_state'],s['time_s'],s['last_dissipation_w']]):
        raise ValueError('Finite complete checkpoint required')
    observation=ObservationPlan.from_dict(compat['observation'])
    if len(observation.scales)!=n: raise ValueError('Historical observation state count mismatch')
    return fs,n


def inspect_bundle(input_bundle,plan):
    """Verify identities and budgets before trajectory allocation; no phase work."""
    bundle=native.safe_path(input_bundle).resolve()
    if not bundle.is_dir(): raise ValueError('Input bundle directory required')
    paths=sorted(p for p in bundle.iterdir() if re.fullmatch(r'checkpoint-\d{6}\.json',p.name))
    if not paths: raise ValueError('No safe checkpoint identity available')
    tip_path=paths[-1];tip=native.verify_chain(tip_path);record=tip['payload']
    fs,n=_identity(record);steps=record['accepted_steps']
    storage=native.chain_storage_bytes(tip_path)
    allocation=8*((steps+1)*(n+1)+steps*len(history.COLUMNS))
    if steps+1>plan.max_points or n>plan.max_states or storage>plan.max_chain_bytes or allocation>plan.max_chain_bytes:
        raise ValueError('Bundle budget exceeded before allocation')
    if len(plan.scales)!=n: raise ValueError('Plan must scale ALL checkpoint states')
    records=history._history(tip_path)
    directories={bundle};first=record
    cursor=tip_path
    while True:
        first=native.read_json(cursor.parent/'checkpoint-000000.json')['payload']
        _identity(first);directories.add(cursor.parent)
        if first['parent'] is None: break
        cursor=native.safe_path(first['parent']['path']).resolve()
    for _,r in records:
        if _identity(r)!=(fs,n): raise ValueError('Ancestral state/fs mismatch')
    result_path=bundle/'result.json';result=None;result_source=None
    if result_path.exists():
        result,result_source=native.read_json_source(result_path)
        if result.get('schema')!='dcalc.regime_reference.v1': raise ValueError('Historical result schema')
        for key in ('chain_id','plan_identity','compatibility'):
            if result.get(key)!=record[key]: raise ValueError('Result/tip '+key+' mismatch')
        if result.get('last_checkpoint')!=tip_path.name or result.get('last_complete_checkpoint_steps')!=steps:
            raise ValueError('Result does not identify actual latest checkpoint')
        accepted=result.get('accepted_chain_steps')
        if type(accepted) is not int or not steps<=accepted<=min(72000,6*fs): raise ValueError('Result accepted count mismatch')
        final=result.get('final')
        if type(final) is not dict or set(final)!={'payload','sha256'} or digest(final['payload'])!=final['sha256']:
            raise ValueError('Final checkpoint fingerprint mismatch')
        final_record=dict(record,checkpoint=final);_identity(final_record)
        if abs(final['payload']['snapshot']['time_s']-record['origin_time_s']-accepted/fs)>1e-8:
            raise ValueError('Final time/accepted count mismatch')
        if accepted==steps and final!=record['checkpoint']: raise ValueError('Result final differs from tip')
        if accepted>steps and result.get('ok') is True: raise ValueError('Successful result has uncheckpointed states')
        obs=result.get('observations',{})
        if obs.get('schema')!='dcalc.regime_observations.v1' or obs.get('plan_sha256')!=digest(record['compatibility']['observation']):
            raise ValueError('Historical observation identity mismatch')
    manifest={}
    # All source directory entries, not just paths asserted by a lone report.
    # Native verify_chain already enforces allowed names, links and chain budget.
    for directory in sorted(directories):
        manifest[str(directory)]={p.name:native.file_sha256(p) for p in sorted(directory.iterdir())}
    if result_source is not None and manifest[str(bundle)]['result.json']!=result_source['sha256']:
        raise ValueError('Historical result changed during inspection')
    if native.verify_chain(tip_path)!=tip:
        raise ValueError('Checkpoint chain changed during inspection')
    return dict(bundle=str(bundle),tip_path=str(tip_path),tip=tip,fs=fs,states=n,steps=steps,
        first_checkpoint=first['checkpoint'],storage_bytes=storage,allocation_bytes=allocation,
        manifest=manifest,historical_result=result,historical_execution=native.read_execution(bundle))


def _output(output, info):
    out=native.safe_path(output,exists=False).resolve()
    if os.path.lexists(out): raise FileExistsError('Fresh output directory required')
    for directory in info['manifest']:
        source=Path(directory)
        if out==source or out.is_relative_to(source) or source.is_relative_to(out):
            raise ValueError('Output overlaps a source bundle or its ancestors')
    if not out.parent.is_dir(): raise ValueError('Existing output parent required')
    return out


def _unchanged(info,request,producer):
    for directory,files in info['manifest'].items():
        d=native.safe_path(directory)
        if set(p.name for p in d.iterdir())!=set(files): raise ValueError('Source bundle inventory changed')
        for name,sha in files.items():
            if native.file_sha256(d/name)!=sha: raise ValueError('Source bundle changed')
    if request['mode']=='file' and native.file_sha256(request['path'])!=request['sha256']:
        raise ValueError('Plan source changed')
    if report.sources()!=producer: raise ValueError('Loaded analysis sources changed')


def load_bundle(info, *, stop=lambda:False):
    if stop(): raise AnalysisStopped('stop_before_load')
    size,n=info['steps']+1,info['states']
    t=np.empty(size);z=np.empty((size,n));data=np.empty((size-1,len(history.COLUMNS)))
    initial=info['first_checkpoint']['payload']['snapshot'];t[0]=initial['time_s'];z[0]=initial['state']
    # Native reader validates NPZ headers before allocating a bounded chunk,
    # columns, continuity, endpoint snapshots, and byte hashes after loading.
    done,refs=history._load_history(info['tip_path'],z,t,data,history.COLUMNS)
    if done!=size-1: raise ValueError('Incomplete saved prefix')
    if t[0]!=initial['time_s'] or z[0].tolist()!=initial['state']:
        raise ValueError('Loaded initial state differs from origin checkpoint')
    if stop(): raise AnalysisStopped('stop_after_load')
    if len(t)>1 and (np.any(np.diff(t)<=0) or not np.allclose(np.diff(t),1/info['fs'],rtol=1e-9,atol=1e-14)):
        raise ValueError('Native time cadence differs from fs')
    midpoint=data[:,history.COLUMNS.index('midpoint_time_s')]
    if not np.all(np.isfinite(midpoint)) or not np.allclose(midpoint,(t[:-1]+t[1:])/2,rtol=0,atol=1e-12):
        raise ValueError('Native midpoint clock mismatch')
    if t[-1]!=info['tip']['payload']['checkpoint']['payload']['snapshot']['time_s'] or z[-1].tolist()!=info['tip']['payload']['checkpoint']['payload']['snapshot']['state']:
        raise ValueError('Loaded final state differs from tip')
    signals={name:dict(times=midpoint,values=data[:,history.COLUMNS.index(column)],unit=unit,scale=None)
        for name,column,unit in [('pressure','pressure_pa','Pa'),('jet','jet_m3_s','m^3/s'),('downstream_flow','downstream_flow_m3_s','m^3/s')]}
    return t,z,signals,refs


def run(input_bundle,plan_source,output,*,dry_run=False,stop=lambda:False,callback=None):
    """Analysis command, with signal flags and bounded cooperative closure.

    Success seals a rechecked result. A later caught signal/error publishes an
    overriding cancellation. There is no simulation supervisor or child process.
    """
    started=time.monotonic();flags=[];old={};out=None;result=None;sealed=False;created=False
    def halted(): return bool(flags) or stop() or time.monotonic()-started>=plan.seconds
    try:
        if isinstance(plan_source,PhasePlan):
            plan=plan_source;request=dict(mode='inline',requested=plan.as_dict(),sha256=digest(plan.as_dict()))
        else:
            raw,request=native.read_json_source(plan_source);plan=PhasePlan.from_dict(raw)
        producer=report.sources();info=inspect_bundle(input_bundle,plan);out=_output(output,info)
        if dry_run:
            _unchanged(info,request,producer)
            return dict(schema='dcalc.phase_preflight.v1',ok=True,status='ready',phase_calculated=False,
                writes=0,native_steps=0,plan=plan.as_dict(),request=request,tip_sha256=info['tip']['sha256'],
                verified_steps=info['steps'],storage_bytes=info['storage_bytes'],allocation_bytes=info['allocation_bytes'])
        for sig in (signal.SIGTERM,signal.SIGINT):
            old[sig]=signal.signal(sig,lambda signum,frame:flags.append(signum))
        _unchanged(info,request,producer)
        if halted(): raise AnalysisStopped('stop_before_output')
        out.mkdir(exist_ok=False);created=True
        native.write_json(out/'plan.json',dict(request=request,effective=plan.as_dict()))
        t,z,signals,refs=load_bundle(info,stop=halted)
        if len(t)<2:
            result=dict(schema='dcalc.phase_reference.v1',ok=True,status='processed',reason='insufficient_saved_samples',
                plan=plan.as_dict(),plan_sha256=digest(plan.as_dict()),samples=len(t),states=z.shape[1],train_samples=0,
                groups=[dict(group=g,status='unavailable',reason='insufficient_saved_samples',central=None,
                    sensitivities=[dict(half=i,status='not_evaluated',reason='insufficient_saved_samples',result=None) for i in (1,2)]) for g in plan.groups],
                minimal_period=None,fundamental=None,orbital_stability=None)
        else: result=analyze(t,z,plan=plan,signals=signals,stop=halted,callback=callback)
        result.update(provenance=dict(mode='native_bundle',analysis_sources=producer,plan_request=request,
            tip_path=info['tip_path'],tip_sha256=info['tip']['sha256'],inputs=info['manifest'],series=refs,
            simulation_producer=info['tip']['payload']['producer_sources'],
            historical_fit={k:(info['historical_result'] or {}).get(k) for k in ('fit_certificate_scope','fit_certificate_sha256','fit_air','bernoulli_air')},
            historical_execution=info['historical_execution']),
            historical_observations=(info['historical_result'] or {}).get('observations'),
            historical_result_status={k:(info['historical_result'] or {}).get(k) for k in ('ok','status','reason','accepted_chain_steps')},
            verified_saved_steps=info['steps'],sample_rate_hz=info['fs'],
            time_convention='state at n; pressure/jet/downstream flow at saved native midpoint times',
            elapsed_seconds=time.monotonic()-started)
        _unchanged(info,request,producer)
        if halted(): result.update(ok=False,status='partial',reason='stop_or_timeout_before_export')
        report.export(out,result)
        _unchanged(info,request,producer)
        if halted(): raise AnalysisStopped('stop_or_timeout_before_closure')
        native.write_json(out/'analysis.closed.json',dict(schema='dcalc.phase_closure.v1',result_sha256=native.file_sha256(out/'result.json'),ok=result['ok']))
        sealed=True
        # Finite linearization boundary: recheck after the seal, while handlers
        # still record cancellations. Past this check no asynchronous guarantee.
        _unchanged(info,request,producer)
        if halted(): raise AnalysisStopped('stop_or_timeout_at_closure')
        return result
    except (Exception,KeyboardInterrupt) as exc:
        failure=dict(schema='dcalc.phase_reference.v1',ok=False,status='partial' if result else 'refused',reason=type(exc).__name__+': '+str(exc))
        if created:
            # Only a directory created by THIS call can have this prepared plan.
            try:
                native.write_json(out/'analysis.cancelled.json',dict(failure,sealed_candidate=sealed))
            except (OSError,ValueError): pass
        return failure
    finally:
        for sig,handler in old.items(): signal.signal(sig,handler)
