"""Bounded explicit conversion workflow; no implicit historical entry point."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
_BLAS1_AT_IMPORT = all(os.environ.get(k)=='1' for k in
    ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'))

from ..nonlinear import scalar_passivity_certificate as certs
from ..nonlinear.passive_realization import convert
from ..nonlinear.passive_state import _BLAS1_AT_IMPORT as MODEL_BLAS1_AT_IMPORT
from ..reporting import passive_realization as report
from .design_pitch import execution_ready, child_limits as native_child_limits, destination
from .tolerance_audit import _collect_child

ROOT=Path(__file__).resolve().parents[2]
PREFLIGHT_SECONDS=2.
COLLECTION_SECONDS=2.
CLOSURE_SECONDS=5.


def _seconds(value):
    if type(value) not in (int,float) or not 0<value<=175:
        raise ValueError('Compute wall budget must lie in (0,175] seconds')
    return float(value)


def _check(deadline, phase):
    if time.monotonic()>=deadline: raise TimeoutError('Public wall budget exhausted: '+phase)


def _inputs(source, certificate):
    raw=certs.read_bytes(source); c=certs.validate_source(certs.parse_json(raw))
    cr=certs.read_bytes(certificate) if certificate is not None else None
    if cr is not None: certs.validate_certificate(c,certs.parse_json(cr),certs.sha(raw))
    elif c['order']: raise ValueError('General source requires --certificate')
    return raw,cr,c


def preflight(source, certificate, output_dir, *, seconds=175.):
    """Structural checks only: no certificate arithmetic, conversion or output."""
    certs.verify_sources(); seconds=_seconds(seconds)
    raw,cr,c=_inputs(source,certificate)
    out=destination(certs.safe_path(output_dir,exists=False))
    plan=dict(schema='dcalc.passive_realization.plan.v1',
        source=dict(path=str(certs.safe_path(source)),sha256=certs.sha(raw)),
        certificate=dict(path=str(certs.safe_path(certificate)) if certificate is not None else None,
                         sha256=certs.sha(cr) if cr is not None else None),
        output=str(out),seconds=seconds,order=c['order'],producer_sources=certs.verify_sources()[0],
        budgets=dict(memory_mib=768,child_cpu_seconds=175,blas_threads=1,file_mib=4,
                     compute_wall_seconds=seconds,preflight_seconds=PREFLIGHT_SECONDS,
                     collection_seconds=COLLECTION_SECONDS,closure_seconds=CLOSURE_SECONDS,
                     public_wall_seconds=seconds+PREFLIGHT_SECONDS+COLLECTION_SECONDS+CLOSURE_SECONDS))
    certs.verify_sources()
    return plan


def _verify(plan):
    if certs.verify_sources()[0]!=plan['producer_sources']: raise ValueError('Producer source changed')
    raw=certs.read_bytes(plan['source']['path'])
    cr=certs.read_bytes(plan['certificate']['path']) if plan['certificate']['path'] is not None else None
    if certs.sha(raw)!=plan['source']['sha256'] or (certs.sha(cr) if cr is not None else None)!=plan['certificate']['sha256']:
        raise ValueError('Source/certificate changed since preflight')
    return raw,cr


def child_limits():
    import resource
    native_child_limits()
    resource.setrlimit(resource.RLIMIT_FSIZE,(4*1024**2,4*1024**2))


def worker_ready():
    import resource
    execution_ready()
    if not _BLAS1_AT_IMPORT or not MODEL_BLAS1_AT_IMPORT or any(os.environ.get(k)!='1' for k in
            ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')):
        raise ValueError('BLAS1 required before import')
    for kind,ceiling in ((resource.RLIMIT_AS,768*1024**2),(resource.RLIMIT_CPU,175),(resource.RLIMIT_FSIZE,4*1024**2)):
        if not 0<resource.getrlimit(kind)[0]<=ceiling: raise ValueError('Bounded worker limits required')


def worker(plan):
    worker_ready(); report.validate_plan(plan)
    out=certs.safe_path(plan['output'])
    try:
        raw,cr=_verify(plan)
        result=convert(raw,cr,seconds=_seconds(plan['seconds']))
        _verify(plan)
        report.write_result(out,result,raw,cr)
        _verify(plan)
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        def finite_evidence(value):
            if type(value) is dict: return {k:finite_evidence(v) for k,v in value.items()}
            if type(value) in (list,tuple): return [finite_evidence(v) for v in value]
            if type(value) is float and not math.isfinite(value): return {'nonfinite_metric':repr(value)}
            return value
        report.json_new(out/'failure.json',dict(status='refused',error=type(exc).__name__+': '+str(exc),
            diagnostics=finite_evidence(getattr(exc,'diagnostics',{}))))
        return 1


def _run(source, certificate, output_dir, *, dry_run, seconds, cancelled, deadline):
    preflight_deadline=min(time.monotonic()+PREFLIGHT_SECONDS,deadline-COLLECTION_SECONDS-CLOSURE_SECONDS)
    plan=preflight(source,certificate,output_dir,seconds=seconds)
    _check(preflight_deadline,'preflight')
    if dry_run: return dict(ok=not cancelled,status='preflight_only',plan=plan)
    out=Path(plan['output']); out.mkdir(parents=True,exist_ok=False)
    report.json_new(out/'plan.json',plan)
    env=os.environ.copy()
    env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',
               PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
    child=None; error=None; late_completion=False
    compute_deadline=min(time.monotonic()+seconds,deadline-COLLECTION_SECONDS-CLOSURE_SECONDS)
    with (out/'stdout.txt').open('xb') as stdout, (out/'stderr.txt').open('xb') as stderr:
        try:
            _check(compute_deadline,'before child')
            if cancelled: raise InterruptedError('Cancelled before child')
            child=subprocess.Popen([sys.executable,'-m','tools.passive_realization','--worker',str(out/'plan.json')],
                cwd=ROOT,env=env,stdout=stdout,stderr=stderr,preexec_fn=child_limits)
            while child.poll() is None:
                if cancelled: raise InterruptedError('Cancelled by signal '+str(cancelled[-1]))
                _check(compute_deadline,'worker')
                try: child.wait(timeout=min(.05,max(1e-6,compute_deadline-time.monotonic())))
                except subprocess.TimeoutExpired: pass
            late_completion=time.monotonic()>=compute_deadline
            _check(compute_deadline,'worker completion')
        except (Exception,KeyboardInterrupt) as exc:
            error=type(exc).__name__+': '+str(exc)
        finally:
            collection_deadline=min(time.monotonic()+COLLECTION_SECONDS,deadline-CLOSURE_SECONDS)
            state,errors,late=_collect_child(child,False,collection_deadline)
            if late or late_completion: state['reaped']=False
            if errors or late or not all(state[k] for k in ('reaped','collected','pipes_closed')):
                error=error or '; '.join(errors) or 'Child collection unconfirmed'
    if not stdout.closed or not stderr.closed or time.monotonic()>=deadline-CLOSURE_SECONDS:
        state['pipes_closed']=False; error=error or 'Log closure late/unconfirmed'
    ok=not cancelled and error is None and child is not None and child.returncode==0
    if child is not None and child.returncode!=0 and error is None:
        error='Worker exited with code '+str(child.returncode)
        if (out/'failure.json').is_file():
            failure=certs.read_json(out/'failure.json')
            if type(failure) is dict and type(failure.get('error')) is str:
                error=failure['error']
    try:
        _check(deadline,'verification'); _verify(plan)
        if ok:
            report.write_manifest(out)
            report.read_payload(out)
        _check(deadline,'verification complete')
    except (Exception,KeyboardInterrupt) as exc:
        ok=False; error=type(exc).__name__+': '+str(exc)
    if cancelled: ok=False; error='Cancelled by signal '+str(cancelled[-1])
    receipt=dict(ok=ok,status='represented' if ok else 'refused',error=error,
        child=dict(state,pid=child.pid if child else None),cancelled=list(cancelled),
        manifest_sha256=certs.sha(certs.read_bytes(out/'manifest.json')) if (out/'manifest.json').exists() else None)
    _check(deadline,'receipt'); report.json_new(out/'execution.json',receipt)
    _check(deadline,'receipt published')
    return dict(receipt,output=str(out))


def run(source, certificate, output_dir, *, dry_run=False, seconds=175.):
    """Public deadline includes preflight, child, collection and closure."""
    seconds=_seconds(seconds); deadline=time.monotonic()+seconds+PREFLIGHT_SECONDS+COLLECTION_SECONDS+CLOSURE_SECONDS
    execution_ready()
    if threading.current_thread() is not threading.main_thread(): raise ValueError('Main thread controller required')
    if signal.getitimer(signal.ITIMER_REAL)!=(0.,0.): raise ValueError('Unused wall timer required')
    cancelled=[]; handlers={}; result=None
    def expired(sig,frame): raise TimeoutError('Public wall budget exhausted')
    try:
        for sig in (signal.SIGINT,signal.SIGTERM): handlers[sig]=signal.signal(sig,lambda sig,frame:cancelled.append(sig))
        handlers[signal.SIGALRM]=signal.signal(signal.SIGALRM,expired)
        signal.setitimer(signal.ITIMER_REAL,max(1e-6,deadline-time.monotonic()))
        result=_run(source,certificate,output_dir,dry_run=dry_run,seconds=seconds,cancelled=cancelled,deadline=deadline)
        if dry_run:
            _check(deadline,'dry-run closure'); certs.verify_sources(); result['ok']=not cancelled
        else:
            out=Path(result['output']); execution_sha=certs.sha(certs.read_bytes(out/'execution.json'))
            signals={signal.SIGINT,signal.SIGTERM}; oldmask=signal.pthread_sigmask(signal.SIG_BLOCK,signals)
            try:
                certs.verify_sources(); _check(deadline,'closure')
                interrupted=bool(cancelled) or bool(signal.sigpending() & signals)
                report.json_new(out/'execution.closed.json',dict(execution_sha256=execution_sha,cancelled=interrupted))
                certs.verify_sources(); _check(deadline,'closure publication')
                interrupted=interrupted or bool(cancelled) or bool(signal.sigpending() & signals)
                if interrupted:
                    result.update(ok=False,status='refused',error='Cancelled by signal')
                    report.json_new(out/'execution.cancelled.json',dict(execution_sha256=execution_sha,ok=False))
                    _check(deadline,'cancellation publication'); certs.verify_sources()
            except (Exception,KeyboardInterrupt):
                # Revoke only our terminal marker; retain every partial artifact.
                (out/'execution.closed.json').unlink(missing_ok=True)
                raise
            finally: signal.pthread_sigmask(signal.SIG_SETMASK,oldmask)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0.)
        for sig,handler in handlers.items(): signal.signal(sig,handler)
    return result


certs.verify_sources()
