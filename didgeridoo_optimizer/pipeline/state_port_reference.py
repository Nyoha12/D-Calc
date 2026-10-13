"""Explicit numerical model + SI flow samples; no CONFIG or geometry loader."""
from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import threading
from pathlib import Path
import subprocess
import sys
import time

_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()

from .design_pitch import destination, execution_ready, child_limits as native_child_limits
from .tolerance_audit import _collect_child
from ..nonlinear.passive_state import _BLAS1_AT_IMPORT as MODEL_BLAS1_AT_IMPORT
from ..nonlinear.passive_state import verify_loaded_sources, PassiveState, bounded_vector, bounded_json
from ..nonlinear.passive_resonator import integer, real, digest
from ..reporting import state_port_reference as report

ROOT = Path(__file__).resolve().parents[2]
FLOW_SCHEMA = 'dcalc.prescribed_flow.v1'
PREFLIGHT_SECONDS = 2.
COLLECTION_SECONDS = 2.
CLOSURE_SECONDS = 1.
_BLAS1_AT_IMPORT = all(os.environ.get(k) == '1' for k in
    ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'))


def signal_parameters(data, fs):
    bounded_json(data)
    if (type(data) is not dict or set(data)!={'schema','units','sample_rate_hz','initial_condition','flow_m3_s','provenance'}
            or data['schema']!=FLOW_SCHEMA or data['units']!={'flow':'m^3/s'}
            or data['initial_condition']!='rest' or type(data['provenance']) is not dict):
        raise ValueError('Explicit SI flow signal with rest initialization required')
    rate = integer(data['sample_rate_hz'], 'sample_rate_hz', 1, 12000)
    if rate!=fs: raise ValueError('Signal/model fs mismatch; no resampling')
    flow = bounded_vector(data['flow_m3_s'], 'flow_m3_s', 2400)
    if not 1<=len(flow)<=rate*.2:
        raise ValueError('Signal duration must be <=0.2 seconds and <=2400 steps')
    return flow


def _verify_sources(expected):
    if type(expected) is not dict or any(type(k) is not str or type(v) is not str
            or len(v)!=64 or any(c not in '0123456789abcdef' for c in v)
            or not (ROOT/k).resolve().is_relative_to(ROOT) or Path(k).suffix!='.py'
            for k,v in expected.items()):
        raise ValueError('Invalid source manifest')
    current = report.sources()
    if any((ROOT/k).is_file() is False or hashlib.sha256((ROOT/k).read_bytes()).hexdigest()!=v
           or (k in current and current[k]!=v) for k,v in expected.items()):
        raise ValueError('Loaded producer source changed')
    return current


def preflight(model_in, signal_in, output_dir, *, seconds=175.):
    seconds = real(seconds, 'seconds', minimum=0.)
    if not 0<seconds<=175: raise ValueError('Wall budget must lie in (0,175] seconds')
    _verify_sources(LOADED_SOURCES)
    model_data, model_source = report.read_json_source(model_in)
    model = PassiveState.load(model_in, expected_sha256=model_source['sha256'])
    signal_data, signal_source = report.read_json_source(signal_in)
    flow = signal_parameters(signal_data, model.sample_rate_hz)
    out = destination(output_dir)
    model_source = {k:v for k,v in model_source.items() if k!='requested'}
    plan = dict(schema='dcalc.state_port_plan.v1', model=model_source, signal=signal_source,
        output=str(out), seconds=seconds, sample_rate_hz=model.sample_rate_hz,
        sample_count=len(flow), initial_condition='rest', model_metadata=model.metadata,
        producer_sources=report.sources(), source_roles=verify_loaded_sources()[1], budgets=dict(memory_mib=768, blas_threads=1,
            child_cpu_seconds=175, child_wall_seconds=seconds, file_mib=4,
            public_wall_seconds=seconds+PREFLIGHT_SECONDS+COLLECTION_SECONDS+CLOSURE_SECONDS,
            preflight_seconds=PREFLIGHT_SECONDS, collection_seconds=COLLECTION_SECONDS, closure_seconds=CLOSURE_SECONDS, max_steps=2400, max_duration_s=.2),
        limits=report.LIMITS)
    if len(report.strict_json(plan).encode())+len(report.strict_json(model.parameters()).encode())+len(flow)*1200>4*1024**2:
        raise ValueError('Serialized plan/result resource budget exceeded before execution')
    _verify_sources(plan['producer_sources'])
    return plan


def prescribed_flow(model, flow_m3_s, *, initial_condition, seconds=175., progress=None):
    """API experiment on an independent rest state; supplied model stays intact."""
    started = time.monotonic()
    verify_loaded_sources()
    if not isinstance(model, PassiveState) or initial_condition!='rest':
        raise ValueError('PassiveState and explicit rest initialization required')
    seconds = real(seconds, 'seconds', minimum=0.)
    if not 0<seconds<=175: raise ValueError('Wall budget must lie in (0,175]')
    fs = integer(model.sample_rate_hz, 'sample_rate_hz', 1, 12000)
    flow = bounded_vector(flow_m3_s, 'flow_m3_s', 2400)
    if not 1<=len(flow)<=.2*fs: raise ValueError('Prescribed-flow duration/step budget exceeded')
    backend = model.copy()
    initial = backend.export_state()
    accepted = (backend, [], initial, 0.)
    p = dict(schema=report.SCHEMA, status='partial', reason=None, pressure_port='midpoint',
        sample_rate_hz=fs, initial_condition='rest', requested_steps=len(flow),
        model_parameters=model.parameters(), model_metadata=model.metadata,
        initial_state=initial, physical_validation='not_validated',
        units=dict(flow='m^3/s', pressure='Pa', energy='J', time='s'), limits=report.LIMITS)
    def finish():
        _, rows, state, energy = accepted
        p.update(samples=rows, final_state=state, final_time_s=len(rows)/fs,
            final_energy_j=energy, work_j=math.fsum(r['work_j'] for r in rows),
            dissipation_j=math.fsum(r['dissipation_j'] for r in rows))
        p['balance_error_j']=p['final_energy_j']-p['work_j']+p['dissipation_j']
        return p
    finish()
    if progress: progress(p)
    try:
        for i,u in enumerate(flow):
            if time.monotonic()-started>=seconds: raise TimeoutError('prescribed_flow_wall_budget')
            backend, rows, _, e0 = accepted
            trial = backend.copy(with_state=True)
            pressure = trial.step(u)
            e1 = trial.energy()
            work, loss = pressure*float(u)/fs, trial.last_dissipation_w/fs
            row = dict(index=i, start_time_s=i/fs, midpoint_time_s=(i+.5)/fs,
                end_time_s=(i+1)/fs, flow_m3_s=float(u), pressure_pa=pressure,
                energy_old_j=e0, energy_j=e1, work_j=work, dissipation_j=loss,
                balance_error_j=e1-e0-work+loss)
            # A single assignment publishes an accepted state and its sample together.
            candidate = (trial, rows+[row], trial.export_state(), e1)
            accepted = candidate
            if progress and (i+1)%64==0: progress(finish())
        if time.monotonic()-started>=seconds: raise TimeoutError('prescribed_flow_wall_budget')
        model.verify_file_unchanged()
        verify_loaded_sources()
        p['status']='resolved'
    except (ValueError, ArithmeticError, OSError, MemoryError, KeyboardInterrupt) as exc:
        p['reason']=type(exc).__name__+': '+str(exc)
    result = finish()
    try:
        verify_loaded_sources()
        if time.monotonic()-started >= seconds:
            raise TimeoutError('prescribed_flow_wall_budget')
    except (ValueError, OSError) as exc:
        result.update(status='partial', reason=type(exc).__name__+': '+str(exc))
    return result


def worker(plan):
    out = Path(plan['output'])
    try:
        _verify_sources(plan['producer_sources'])
        if report.read_json_source(plan['model']['path'])[1]['sha256'] != plan['model']['sha256']:
            raise ValueError('Model changed since preflight')
        data, source = report.read_json_source(plan['signal']['path'])
        if source['sha256']!=plan['signal']['sha256']: raise ValueError('Signal changed since preflight')
        worker_ready()
        model = PassiveState.load(plan['model']['path'], expected_sha256=plan['model']['sha256'])
        flow = signal_parameters(data, model.sample_rate_hz)
        result = prescribed_flow(model, flow, initial_condition='rest', seconds=plan['seconds'],
            progress=lambda p:report.write_result(out,p,partial=True))
        result['provenance']=dict(inputs=dict(model=plan['model'],signal=plan['signal']),
                                  producer_sources=report.sources(), source_roles=verify_loaded_sources()[1])
        _verify_sources(plan['producer_sources'])
        model.verify_file_unchanged()
        if report.read_json_source(plan['signal']['path'])[1]['sha256']!=source['sha256']:
            raise ValueError('Signal changed during processing')
        report.write_result(out,result)
        _verify_sources(plan['producer_sources'])
        return 0 if result['status']=='resolved' else 1
    except (Exception, KeyboardInterrupt) as exc:
        report.atomic_bytes(out/'failure.json', report.strict_json(dict(error=type(exc).__name__+': '+str(exc))).encode())
        return 1


def child_limits():
    """Only the spawned worker receives these process-local limits."""
    import resource
    native_child_limits()
    resource.setrlimit(resource.RLIMIT_FSIZE, (4*1024**2, 4*1024**2))


def worker_ready():
    import resource
    execution_ready()
    if not MODEL_BLAS1_AT_IMPORT or not _BLAS1_AT_IMPORT or any(os.environ.get(k) != '1' for k in
            ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')):
        raise ValueError('worker requires BLAS1 before import')
    for kind, ceiling in ((resource.RLIMIT_AS,768*1024**2),
                          (resource.RLIMIT_CPU,175),(resource.RLIMIT_FSIZE,4*1024**2)):
        if not 0 < resource.getrlimit(kind)[0] <= ceiling:
            raise ValueError('worker requires imposed memory/CPU/file limits')


def _check_deadline(deadline, phase):
    if time.monotonic() >= deadline:
        raise TimeoutError('Public wall budget exhausted: '+phase)


def _run(model_in, signal_in, output_dir, *, dry_run=False, seconds=175., cancelled, deadline=None):
    if deadline is None:
        deadline = time.monotonic()+seconds+PREFLIGHT_SECONDS+COLLECTION_SECONDS+CLOSURE_SECONDS
    preflight_deadline = min(time.monotonic()+PREFLIGHT_SECONDS, deadline-COLLECTION_SECONDS-CLOSURE_SECONDS)
    plan = preflight(model_in, signal_in, output_dir, seconds=seconds)
    _check_deadline(preflight_deadline, 'preflight')
    if dry_run: return dict(ok=not cancelled, status='preflight_only', plan=plan)
    execution_ready()
    out = Path(plan['output'])
    out.mkdir(parents=True, exist_ok=False)
    report.atomic_bytes(out/'plan.json', report.strict_json(plan).encode())
    env = os.environ.copy()
    env.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1', PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    child = None
    error = None
    collected = False
    late_completion = False
    child_state = dict(created=False,exit_code=None,reaped=False,collected=False,pipes_closed=False)
    # Preflight and finalization share the public envelope. Always reserve two
    # seconds for collection and at least one for terminal publication.
    compute_deadline = min(time.monotonic()+seconds, deadline-COLLECTION_SECONDS-CLOSURE_SECONDS)
    with (out/'stdout.txt').open('wb') as stdout, (out/'stderr.txt').open('wb') as stderr:
        try:
            _check_deadline(compute_deadline, 'before child')
            child = subprocess.Popen([sys.executable,'-m','tools.state_port_reference','--worker',str(out/'plan.json')],
                cwd=ROOT, env=env, stdout=stdout, stderr=stderr, preexec_fn=child_limits)
            while child.poll() is None:
                if cancelled: raise InterruptedError('Run cancelled by signal '+str(cancelled[-1]))
                remaining = compute_deadline-time.monotonic()
                if remaining<=0: raise TimeoutError('Child wall budget exhausted')
                try: child.wait(timeout=min(.05,remaining))
                except subprocess.TimeoutExpired: pass
            late_completion = time.monotonic() >= compute_deadline
            _check_deadline(compute_deadline, 'child completion')
        except (Exception, KeyboardInterrupt) as exc:
            error = type(exc).__name__+': '+str(exc)
        finally:
            collection_deadline = min(time.monotonic()+COLLECTION_SECONDS, deadline-CLOSURE_SECONDS)
            if child is not None:
                try:
                    if child.poll() is None:
                        child.terminate()
                        remaining = collection_deadline-time.monotonic()
                        if remaining > 0:
                            child.wait(timeout=min(.1, remaining/4))
                except (Exception, KeyboardInterrupt) as exc:
                    if not isinstance(exc, subprocess.TimeoutExpired):
                        error = error or type(exc).__name__+': '+str(exc)
            child_state, errors, late = _collect_child(child, collected, collection_deadline)
            # Native helper reports exit and channel collection independently;
            # late acknowledgement cannot certify a timely reap here.
            if late or late_completion:
                child_state['reaped'] = False
            if errors or late or not all(child_state[k] for k in ('reaped','collected','pipes_closed')):
                error = error or '; '.join(errors) or 'Child collection not confirmed'
    # File descriptors used as log channels also have to close on time.
    if not stdout.closed or not stderr.closed or time.monotonic() >= deadline-CLOSURE_SECONDS:
        child_state['pipes_closed'] = False
        error = error or 'Log closure not confirmed within budget'
    ok = not cancelled and error is None and child is not None and child.returncode==0
    try:
        _check_deadline(deadline, 'verification')
        _verify_sources(plan['producer_sources'])
        for key in ('model','signal'):
            if report.read_json_source(plan[key]['path'])[1]['sha256']!=plan[key]['sha256']:
                raise ValueError('Input changed before completion')
        if ok: report.read_payload(out)
        _check_deadline(deadline, 'verified result')
    except Exception as exc:
        ok, error = False, str(exc)
    if cancelled: ok, error = False, 'Run cancelled by signal '+str(cancelled[-1])
    receipt = dict(ok=ok,status='resolved' if ok else 'partial',error=error,
        child=dict(child_state,pid=child.pid if child else None),
        result_sha256=hashlib.sha256((out/'result.json').read_bytes()).hexdigest() if (out/'result.json').is_file() else None)
    _check_deadline(deadline, 'execution receipt')
    report.atomic_bytes(out/'execution.json', report.strict_json(receipt).encode())
    _check_deadline(deadline, 'execution receipt published')
    return dict(receipt, output=str(out))


def run(model_in, signal_in, output_dir, *, dry_run=False, seconds=175.):
    started = time.monotonic()
    seconds = real(seconds, 'seconds', minimum=0.)
    if not 0 < seconds <= 175: raise ValueError('Wall budget must lie in (0,175]')
    deadline = started+seconds+PREFLIGHT_SECONDS+COLLECTION_SECONDS+CLOSURE_SECONDS
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('Bounded child controller requires the main thread')
    cancelled = []
    handlers = {}
    result = None
    timer_installed = False
    # One timer at the public API boundary covers preflight, collection and
    # publication; no timer or worker rlimit is applied to numerical steps.
    execution_ready()
    if signal.getitimer(signal.ITIMER_REAL) != (0., 0.):
        raise ValueError('Public controller requires an unused wall timer')
    def expired(sig, frame):
        raise TimeoutError('Public wall budget exhausted')
    try:
        for sig in (signal.SIGINT,signal.SIGTERM):
            handlers[sig] = signal.signal(sig, lambda sig,frame:cancelled.append(sig))
        handlers[signal.SIGALRM] = signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, max(deadline-time.monotonic(), 1e-6))
        timer_installed = True
        result = _run(model_in,signal_in,output_dir,dry_run=dry_run,
                      seconds=seconds,cancelled=cancelled,deadline=deadline)
        if dry_run:
            _check_deadline(deadline, 'preflight completion')
            verify_loaded_sources()
            result['ok'] = not cancelled
        else:
            out = Path(result['output'])
            execution_sha = hashlib.sha256((out/'execution.json').read_bytes()).hexdigest()
            signals = {signal.SIGINT,signal.SIGTERM}
            old_mask = signal.pthread_sigmask(signal.SIG_BLOCK,signals)
            try:
                verify_loaded_sources()
                _check_deadline(deadline, 'closure')
                interrupted = bool(cancelled) or bool(signal.sigpending() & signals)
                if interrupted:
                    result.update(ok=False,status='partial',error='Run cancelled by signal')
                    report.atomic_bytes(out/'execution.cancelled.json',report.strict_json(dict(execution_sha256=execution_sha,ok=False)).encode())
                report.atomic_bytes(out/'execution.closed.json',report.strict_json(dict(execution_sha256=execution_sha,cancelled=interrupted)).encode())
                verify_loaded_sources()
                _check_deadline(deadline, 'closure publication')
                # Finite boundary AFTER publication and its source/time checks.
                # Pending signals up to this snapshot cancel; later ones cannot
                # reopen the already closed command.
                interrupted = interrupted or bool(cancelled) or bool(signal.sigpending() & signals)
                if interrupted and not os.path.lexists(out/'execution.cancelled.json'):
                    result.update(ok=False,status='partial',error='Run cancelled by signal')
                    report.atomic_bytes(out/'execution.cancelled.json',report.strict_json(dict(execution_sha256=execution_sha,ok=False)).encode())
                    _check_deadline(deadline, 'cancellation publication')
                    verify_loaded_sources()
            except (Exception,KeyboardInterrupt):
                # Revoke only our terminal marker; partial data and logs remain.
                # Do not attempt more publications after an exhausted timer.
                if time.monotonic() < deadline:
                    try:
                        if not os.path.lexists(out/'execution.cancelled.json'):
                            report.atomic_bytes(out/'execution.cancelled.json',report.strict_json(dict(execution_sha256=execution_sha,ok=False)).encode())
                    except (Exception,KeyboardInterrupt):
                        (out/'execution.closed.json').unlink(missing_ok=True)
                        raise
                else:
                    (out/'execution.closed.json').unlink(missing_ok=True)
                raise
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK,old_mask)
    finally:
        if timer_installed: signal.setitimer(signal.ITIMER_REAL, 0.)
        for sig,handler in handlers.items(): signal.signal(sig,handler)
    return result


LOADED_SOURCES = report.sources()
