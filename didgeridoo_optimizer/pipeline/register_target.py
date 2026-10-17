"""One saved configuration, immutable prescribed plan and bounded child execution."""
from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
import os
from pathlib import Path

_LOADED_SOURCE_PATH=Path(__file__).resolve()
_LOADED_SOURCE_SHA256=hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
import resource
import signal
import subprocess
import sys
import time

from .paired_onset import _case_context
from .design_pitch import destination, execution_ready, child_limits
from ..nonlinear import register_target as core
from ..nonlinear.passive_resonator import PassiveResonator, digest, _pairs, _constant
from ..reporting import register_target as report

ROOT=Path(__file__).resolve().parents[2]
SCHEMA=core.SCHEMA


@dataclass(frozen=True)
class Plan:
    document: str
    def as_dict(self):
        p=json.loads(self.document,object_pairs_hook=_pairs,parse_constant=_constant)
        core.validate_request(p['request'],states=p['metadata']['states'])
        if p['context_sha256']!=digest({k:v for k,v in p.items() if k!='context_sha256'}):raise ValueError('Immutable context')
        return p


def validate_request(value):
    return core.Request.validate(value)


def preflight(plan_path,output_dir,*,resume=None,steps_this_run=None):
    """Strict readers + native context adapter; no model construction or writes."""
    report.verify_sources(LOADED_SOURCES)
    raw,source=report.read_plan_source(plan_path);core.validate_request(raw)
    if raw['case']['kind'] not in ('saved','saved_state'):raise ValueError('CLI requires real saved CONFIG/DESIGN/model; synthetic source uses memory API')
    r=json.loads(json.dumps(raw,allow_nan=False));case=r['case']
    for key in (('config','design','realization','evidence') if case['kind']=='saved_state' else ('config','design','model_in')):
        path=Path(case[key]);case[key]=str(report.safe_path(path if path.is_absolute() else Path(source['path']).parent/path))
    out=report.safe_path(output_dir,exists=False);destination(out)
    if case['kind']=='saved_state':
        from .state_register_context import case_context
        meta,context,mp=case_context(case,r['observation'],r['rho_kg_m3'])
    else:
        meta,context,mp=_case_context({k:v for k,v in case.items() if k!='kind'},out)
    core.validate_request(r,states=meta['states'])
    # Conservative JSON/checkpoint overhead and sampled-series budget before arrays.
    total=sum(s['steps'] for s in r['plateaus']);b=r['budgets']
    checkpoints=2+(total+b['chunk_steps']-1)//b['chunk_steps']
    estimate=(total+1)*(meta['states']+len(core.COLUMNS)+4)*8+checkpoints*(32768+meta['states']*len(r['plateaus'])*240)+4*1024**2
    if checkpoints>256 or estimate>b['output_mib']*1024**2:raise ValueError('Checkpoint/series/JSON storage quota before allocation')
    parent=None;accepted=0
    if resume is not None:
        chain=report.inspect_checkpoint(resume,expected_request=r)
        tip=chain[-1][1];accepted=tip['experiment']['payload']['accepted_steps']
        if tip['context']['metadata']!=meta:raise ValueError('Resume CONFIG/DESIGN/model context differs')
        parent=report.reference(resume)
    if steps_this_run is None:steps_this_run=total-accepted
    core.integer(steps_this_run,'steps_this_run',0,total-accepted)
    p=dict(schema='dcalc.register_target.execution_plan.v1',request=r,request_source=source,
           metadata=meta,input_files=context['provenance']['files'],output=str(out),resume=parent,
           steps_this_run=steps_this_run,accepted_before=accepted,estimated_bytes=estimate,producer=report.provenance())
    p['context_sha256']=digest(p)
    if report.file_sha256(source['path'])!=source['sha256']:raise ValueError('Plan changed during preflight')
    return Plan(json.dumps(p,allow_nan=False,sort_keys=True))


def check_inputs(p):
    report.verify_sources(LOADED_SOURCES)
    Plan(json.dumps(p,allow_nan=False)).as_dict()
    if not report.REQUIRED_SOURCES<=set(p['producer']['loaded_sources_sha256']):raise ValueError('Incomplete source manifest')
    report.verify_sources(p['producer']['loaded_sources_sha256'])
    if p['producer']['versions']!=report.versions():raise ValueError('Versions changed')
    if report.file_sha256(p['request_source']['path'])!=p['request_source']['sha256']:raise ValueError('Plan bytes changed')
    if set(p['input_files'])!={'config','design','materials','variant_rules'}:raise ValueError('Complete input manifest required')
    for item in p['input_files'].values():
        path=Path(item['path']);actual=report.file_sha256(path) if path.is_file() else None
        if actual!=item['sha256']:raise ValueError('Loaded input changed')
    if p['request']['case']['kind']=='saved_state':
        from .state_register_context import verify_files
        verify_files(p['metadata']['bundle_files']);verify_files(p['metadata']['evidence_files'])
        return
    if report.file_sha256(p['request']['case']['model_in'])!=p['metadata']['model_sha256']:raise ValueError('Model changed')


def experiment_for(p):
    check_inputs(p)
    if p['request']['case']['kind']=='saved_state':
        from .state_register_context import load_verified
        model,checks,binding=load_verified(p['request']['case'],p['metadata'],p['request']['observation'],p['request']['rho_kg_m3'],binding=True)
        experiment=core.Experiment(model,p['request'],source=dict(kind='verified_state',
            description='Finite saved acoustic evidence and native R61 realization verified; documentary origin declared',
            model_sha256=digest(model.parameters()),context_sha256=p['metadata']['acoustic_identity']),_verified_context=binding)
        experiment.context_checks=checks
        native_guard=experiment._context_guard
        def guarded():
            native_guard()
            if report.file_sha256(p['request_source']['path'])!=p['request_source']['sha256']: raise ValueError('Plan changed before step')
        experiment._context_guard=guarded
        return experiment
    case={k:v for k,v in p['request']['case'].items() if k!='kind'}
    metadata,_,_=_case_context(case,Path(p['output'])/'unused-read-only-context')
    if metadata!=p['metadata']:raise ValueError('Saved context no longer matches actual inputs')
    model=PassiveResonator.load(p['request']['case']['model_in'],expected_sha256=p['metadata']['model_sha256'])
    return core.Experiment(model,p['request'],source=dict(kind='verified_saved',
        description='Saved model associated with exact CONFIG/DESIGN/DB/recipe; historical fit not recertified',
        model_sha256=digest(model.parameters())))


def calculate(p,*,stop=lambda:False):
    """Child entry also usable by a resource-bounded caller. No fake onset plan."""
    check_inputs(p);out=Path(p['output']);r=p['request'];b=r['budgets'];started=time.monotonic()
    experiment=experiment_for(p);previous=p['resume'];sequence=0
    if previous is not None:
        chain=report.inspect_checkpoint(report.verify_reference(previous),expected_request=r)
        experiment.restore(chain[-1][1]['experiment'])
    # Every local run begins with an exact snapshot (including old history pointer).
    tip=report.save_checkpoint(out,sequence,experiment,p,previous);sequence+=1
    target=experiment.accepted+p['steps_this_run'];reason=None
    def halted():return stop() or time.monotonic()-started>=min(170.,b['child_seconds']-2)
    while experiment.accepted<target and not halted():
        if r['case']['kind']=='saved_state': check_inputs(p)
        n=min(b['chunk_steps'],target-experiment.accepted)
        chunk=experiment.advance(n,seconds=min(170.,max(0.,b['child_seconds']-2-(time.monotonic()-started))),stop=halted)
        tip=report.save_checkpoint(out,sequence,experiment,p,tip,chunk);sequence+=1
        if not chunk['ok']:reason=chunk['reason'];break
    if halted():reason=reason or 'stop_or_child_budget'
    check_inputs(p)
    chain=report.inspect_checkpoint(report.verify_reference(tip),expected_request=r)
    arrays=report.load_series(chain)
    analysis=core.analyze(r,times=arrays['times']-experiment.origin_time_s,states=arrays['states'],
        midpoint_times=arrays['midpoint_times']-experiment.origin_time_s,pressure=arrays['values'][:,0],
        source=dict(kind='verified_bundle',description='Verified product chunk chain',checkpoint=tip))
    complete=experiment.accepted==sum(s['steps'] for s in r['plateaus']) and not halted()
    result=dict(schema=report.SCHEMA,plan=p,status='complete' if complete else 'partial',reason=reason,
        accepted_steps=experiment.accepted,new_steps_this_run=experiment.accepted-p['accepted_before'],
        new_duration_s=experiment.accepted/core.sample_rate(r),historical_source_steps=0,
        checkpoint=tip,sums=experiment.sums,events=experiment.events,analysis=analysis)
    if r['case']['kind']=='saved_state':
        result['context_checks']=experiment.context_checks
        result['new_duration_s']=result['new_steps_this_run']/core.sample_rate(r)
    report.export(out,result,b['output_mib']*1024**2)
    return dict(ok=complete,status=result['status'],accepted_steps=experiment.accepted,
                hard_conforming=result['analysis']['hard_conforming'],reason=reason)


_STATE_BLAS_KEYS=('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')


def _state_worker_ready(blas_before_import):
    """Verify actual worker limits; never change the caller's resource policy.

    The CLI captures BLAS before importing pipeline (whose package imports NumPy).
    Direct memory callers use calculate/experiment_for under their own budget.
    """
    execution_ready()
    expected={key:'1' for key in _STATE_BLAS_KEYS}
    if blas_before_import != expected or any(os.environ.get(key)!='1' for key in expected):
        raise ValueError('State worker requires BLAS1 captured before NumPy import and still present')
    for name,cap in (('RLIMIT_AS',768*1024**2),('RLIMIT_CPU',175),('RLIMIT_FSIZE',8*1024**2)):
        soft,hard=resource.getrlimit(getattr(resource,name))
        if not (0 < soft <= hard <= cap):
            raise ValueError('State worker requires bounded '+name)


def worker(task, *, _blas_before_import=None):
    core.exact(task,{'plan'},'worker task')
    if task['plan']['request']['case']['kind']=='saved_state':
        _state_worker_ready(_blas_before_import)
    flags=[];old={}
    try:
        for sig in (signal.SIGINT,signal.SIGTERM):old[sig]=signal.signal(sig,lambda s,f:flags.append(s))
        result=calculate(task['plan'],stop=lambda:bool(flags))
        if flags:result.update(ok=False,status='partial',reason='cancelled')
        return result
    finally:
        for sig,handler in old.items():signal.signal(sig,handler)


def _state_limits():
    child_limits();resource.setrlimit(resource.RLIMIT_CPU,(175,175));resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024**2,8*1024**2))


def _run_state_child(p,out,limit):
    """File IPC and native bounded collection: no blocking stdin or pipe drain."""
    from .tolerance_audit import _collect_child
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    child=None;flags=[];old={};error=None;receipt=None;late=False
    collection_deadline=time.monotonic()+min(175.,p['request']['budgets']['child_seconds'])
    deadline=collection_deadline-min(2.,p['request']['budgets']['child_seconds']/2)
    try:
        for sig in (signal.SIGINT,signal.SIGTERM): old[sig]=signal.signal(sig,lambda s,f:flags.append(s))
        with (out/'worker.log').open('xb') as log:
            try:
                child=subprocess.Popen([sys.executable,'-B','-m','tools.register_target','--worker-plan',str(out/'plan.json')],
                    cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,preexec_fn=_state_limits)
                while child.poll() is None:
                    if flags or time.monotonic()>=deadline:
                        error='cancelled' if flags else 'child_wall_budget';break
                    try: child.wait(timeout=min(.05,max(1e-6,deadline-time.monotonic())))
                    except subprocess.TimeoutExpired: pass
                if time.monotonic()>=deadline: error=error or 'late_child_collection'
            finally:
                receipt,errors,late=_collect_child(child,False,min(collection_deadline,time.monotonic()+2.))
                if errors or late or not all(receipt[k] for k in ('reaped','collected','pipes_closed')):
                    error=error or 'child_collection_unconfirmed'
    finally:
        for sig,handler in old.items():signal.signal(sig,handler)
    if flags:error='cancelled'
    check_inputs(p)
    if receipt is None or receipt['exit_code']!=0:error=error or 'child_failed'
    raw=(out/'worker.log').read_bytes()
    if len(raw)>65536:raise ValueError('Worker receipt quota')
    try: response=json.loads(raw.decode().splitlines()[-1],object_pairs_hook=_pairs,parse_constant=_constant)
    except (ValueError,IndexError,UnicodeError):raise ValueError(error or 'Worker receipt unavailable; checkpoints preserved')
    if not (out/'result.json').is_file(): raise ValueError(error or 'No result; checkpoints preserved')
    response.update(ok=error is None and response.get('ok') is True,output=str(out),reason=error or response.get('reason'))
    report.prepare_completion(out,dict(ok=response['ok'],status=response['status'],children=[receipt],reason=response['reason']),limit)
    return response


def _limits():
    child_limits();resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024**2,8*1024**2))


def _run(plan_path,output_dir,*,dry_run=False,resume=None,steps_this_run=None):
    plan=preflight(plan_path,output_dir,resume=resume,steps_this_run=steps_this_run);p=plan.as_dict()
    if dry_run:return dict(ok=True,status='planned',plan=p)
    execution_ready();check_inputs(p);out=Path(p['output']);out.mkdir(parents=False,exist_ok=False)
    limit=p['request']['budgets']['output_mib']*1024**2
    report.write_json(out/'plan.json',p,budget_bytes=limit)
    if p['request']['case']['kind']=='saved_state': return _run_state_child(p,out,limit)
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    active=None;children=[];flags=[];old={};error=None;response=None
    try:
        for sig in (signal.SIGINT,signal.SIGTERM):old[sig]=signal.signal(sig,lambda s,f:flags.append(s))
        with (out/'worker.log').open('xb') as stream:
            active=subprocess.Popen([sys.executable,'-B','-m','tools.register_target','--worker'],
                cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,preexec_fn=_limits)
            active.stdin.write(json.dumps(dict(plan=p),allow_nan=False).encode());active.stdin.close()
            deadline=time.monotonic()+p['request']['budgets']['child_seconds'];raw=bytearray()
            os.set_blocking(active.stdout.fileno(),False)
            def drain():
                try:part=os.read(active.stdout.fileno(),65536)
                except BlockingIOError:return
                if len(raw)+len(part)>65536:raise ValueError('Worker output quota')
                raw.extend(part);stream.write(part)
            while active.poll() is None:
                drain()
                if flags or time.monotonic()>deadline:
                    error='cancelled' if flags else 'child_wall_budget';active.terminate()
                    try:active.wait(timeout=2)
                    except subprocess.TimeoutExpired:active.kill();active.wait()
                    break
                time.sleep(.02)
            code=active.wait();drain();active.stdout.close()
            children.append(dict(pid=active.pid,exit_code=code,reaped=True));active=None
            if code!=0:error=error or 'child_failed'
            try:response=json.loads(raw.decode().splitlines()[-1],object_pairs_hook=_pairs,parse_constant=_constant)
            except (ValueError,IndexError,UnicodeError):error=error or 'child_receipt_unavailable'
        check_inputs(p)
        if flags:error='cancelled'
    finally:
        if active is not None:
            if active.poll() is None:active.terminate()
            try:active.wait(timeout=2)
            except subprocess.TimeoutExpired:active.kill();active.wait()
            if active.stdout:active.stdout.close()
            children.append(dict(pid=active.pid,exit_code=active.returncode,reaped=True))
        for sig,handler in old.items():signal.signal(sig,handler)
    if flags:error='cancelled'
    if response is None or not (out/'result.json').is_file():
        raise ValueError(error or 'No result; committed checkpoints remain available')
    ok=error is None and response.get('ok') is True
    response.update(ok=ok,output=str(out),reason=error or response.get('reason'))
    report.prepare_completion(out,dict(ok=ok,status=response['status'],children=children,reason=response['reason']),limit)
    return response


def run(plan_path,output_dir,*,dry_run=False,resume=None,steps_this_run=None):
    if dry_run:return _run(plan_path,output_dir,dry_run=True,resume=resume,steps_this_run=steps_this_run)
    request,_=report.read_plan_source(plan_path);core.validate_request(request);execution_ready()
    started=time.monotonic();old=signal.getsignal(signal.SIGALRM);timer=signal.getitimer(signal.ITIMER_REAL)
    def deadline(s,f):raise TimeoutError('orchestrator_wall_budget')
    result=None
    try:
        signal.signal(signal.SIGALRM,deadline)
        allowance=request['budgets']['orchestrator_seconds']
        signal.setitimer(signal.ITIMER_REAL,min(allowance,timer[0]) if timer[0]>0 else allowance)
        result=_run(plan_path,output_dir,resume=resume,steps_this_run=steps_this_run)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old)
        signal.setitimer(signal.ITIMER_REAL,max(.001,timer[0]-(time.monotonic()-started)) if timer[0]>0 else 0,timer[1])
    report.publish_completion(output_dir)
    return result


LOADED_SOURCES=report.sources()
