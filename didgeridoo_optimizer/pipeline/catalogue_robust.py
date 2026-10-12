"""Sequential isolated R54 composition with one scientific worker at a time."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from ..optimization.catalogue_robust import (
    MIB, ROOT, job_contract, load_inputs, native_documents, require,
)
from ..optimization.design_contract import read_request
from ..reporting import catalogue_robust as report

_LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
COLLECTION_SECONDS = 10.0
CLOSURE_SECONDS = 5.0
CONTROLLER_SECONDS = 205.0


def _interrupt(signum, frame):
    raise InterruptedError("signal catalogue: " + str(signum))


def _subreaper(value=None):
    """Linux process-local adoption; never enumerate or reap unrelated groups."""
    require(sys.platform.startswith("linux"), "supervision catalogue: Linux requis")
    libc = ctypes.CDLL(None, use_errno=True)
    current = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(current), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_GET_CHILD_SUBREAPER")
    previous = current.value
    if value is not None and libc.prctl(36, int(value), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    return previous


def _controller_limits():
    """A distinct envelope; R54 still applies AS768/CPU175 to its own worker."""
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (1024 * MIB, 1024 * MIB))
    resource.setrlimit(resource.RLIMIT_CPU, (200, 200))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * MIB, 4 * MIB))


def _group_exists(group):
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False


def _signal_group(group, signum):
    try:
        os.killpg(group, signum)
    except ProcessLookupError:
        pass


def _reap_adopted(group):
    reaped = []
    while True:
        try:
            pid, status = os.waitpid(-group, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            break
        reaped.append(dict(pid=pid, wait_status=status))
    return reaped


def supervise(job, output, deadline, logs, *, popen=None):
    """Collect only the newly created session/group, under one cleanup deadline."""
    popen = subprocess.Popen if popen is None else popen
    started = time.monotonic()
    child = None
    reason = None
    sent = []
    received = []
    adopted = []
    collected = False
    handlers = {}
    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
               MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1",
               PYTHONDONTWRITEBYTECODE="1")
    try:
        compute_end = min(started + CONTROLLER_SECONDS,
                          deadline - COLLECTION_SECONDS - CLOSURE_SECONDS)
        require(compute_end > started, "budget global avant lancement")
        child = popen(
            [sys.executable, "-B", "-m", "tools.adjustment_policy",
             "--job", str(job), "--output-dir", str(output)],
            stdin=subprocess.DEVNULL, stdout=logs[0], stderr=logs[1],
            cwd=ROOT, env=env, start_new_session=True,
            preexec_fn=_controller_limits)
        while child.poll() is None:
            remaining = compute_end - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("contrôleur R54", compute_end - started)
            try:
                child.wait(timeout=min(0.2, remaining))
            except subprocess.TimeoutExpired:
                pass
        if time.monotonic() >= compute_end:
            raise subprocess.TimeoutExpired("retour tardif R54", compute_end - started)
    except BaseException as exc:
        reason = type(exc).__name__ + ": " + str(exc)
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
            handlers[signum] = signal.signal(
                signum, lambda received_signal, frame: received.append(received_signal))
        try:
            end = min(time.monotonic() + COLLECTION_SECONDS,
                      deadline - CLOSURE_SECONDS)
            if child is None:
                collected = True
            else:
                group = child.pid
                if _group_exists(group):
                    sent.append(signal.SIGTERM)
                    _signal_group(group, signal.SIGTERM)
                escalation = min(time.monotonic() + 1.0, end)
                while time.monotonic() < end:
                    direct_reaped = child.poll() is not None
                    if direct_reaped:
                        # Popen owns its child; waitpid here owns adopted members only.
                        adopted.extend(_reap_adopted(group))
                    if direct_reaped and not _group_exists(group):
                        collected = True
                        break
                    if time.monotonic() >= escalation and signal.SIGKILL not in sent:
                        sent.append(signal.SIGKILL)
                        _signal_group(group, signal.SIGKILL)
                    time.sleep(min(0.02, max(0.0, end - time.monotonic())))
                if not collected and reason is None:
                    reason = "collecte du groupe non confirmée dans le budget"
        except BaseException as exc:
            collected = False
            reason = "collecte: " + type(exc).__name__ + ": " + str(exc)
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
    exit_code = child.returncode if child is not None else None
    clean = bool(child is not None and collected and exit_code == 0
                 and reason is None and not sent and not received)
    return dict(created=child is not None, pid=child.pid if child else None,
                exit_code=exit_code, collected=collected, clean_exit=clean,
                signals=sent + received, adopted_reaped=adopted, reason=reason,
                wall_seconds=time.monotonic() - started)


def _run(job, output_dir, deadline):
    prepared, plan = load_inputs(job)
    limit = plan["budgets"]["output_mib"] * MIB
    output = report.native.new_destination(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    report.put(output, "plan.json", plan, limit)
    attempts = []
    for candidate in plan["candidates"]:
        if candidate["preflight"] != "ready":
            continue
        if time.monotonic() >= deadline - COLLECTION_SECONDS - CLOSURE_SECONDS:
            break
        ident = candidate["id"]
        directory = output / ident
        for name, value in native_documents(prepared, plan, candidate, directory).items():
            report.put(output, ident + "/" + name, value, limit)
        streams = []
        state = None
        try:
            for name in ("controller.stdout.log", "controller.stderr.log"):
                streams.append((directory / name).open("xb"))
            state = supervise(directory / "policy.json", directory / "native",
                              deadline, streams)
        finally:
            errors = []
            for stream in streams:
                try:
                    stream.close()
                except BaseException as exc:
                    errors.append(str(exc))
            if state is not None and errors:
                state.update(clean_exit=False, reason="fermeture logs: " + "; ".join(errors))
        state["id"] = ident
        report.put(output, "attempt_" + ident + ".json", state, limit)
        attempts.append(state)
        if not state["collected"]:
            break
        if time.monotonic() >= deadline - CLOSURE_SECONDS:
            break
    # A changed source invalidates closure; previously written native evidence remains.
    require(load_inputs(job)[1] == plan, "entrées ou sources modifiées pendant le run")
    result = report.export(output, prepared, plan)
    receipt = dict(
        ok=result["execution_complete"], execution_complete=result["execution_complete"],
        closure_confirmed=all(a["collected"] for a in attempts),
        attempted_candidates=[a["id"] for a in attempts],
        manifest_sha256=report.native.file_sha256(output / "manifest.json"))
    report.put(output, "receipt.json", receipt, limit)
    return output, plan, result, receipt


def run(job, output_dir, *, dry_run=False):
    """Dry-run does not create a destination, process, alarm or subreaper state."""
    require(threading.current_thread() is threading.main_thread(),
            "API supervisée: thread principal requis")
    if dry_run:
        _, plan = load_inputs(job)
        return dict(ok=True, dry_run=True, output_created=False, plan=plan)
    require(sys.platform.startswith("linux"), "supervision catalogue: Linux requis")
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
            "API supervisée: alarme préexistante refusée")
    raw, _ = read_request(job)
    limits, _ = job_contract(raw)
    deadline = time.monotonic() + limits["seconds"]
    handlers = {}
    previous_subreaper = None
    pending = None
    failure = None
    try:
        previous_subreaper = _subreaper(True)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
            handlers[signum] = signal.signal(signum, _interrupt)
        signal.setitimer(signal.ITIMER_REAL, max(0.001, deadline - time.monotonic()))
        pending = _run(job, output_dir, deadline)
    except BaseException as exc:
        failure = type(exc).__name__ + ": " + str(exc)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        if previous_subreaper is not None:
            try:
                _subreaper(previous_subreaper)
            except OSError as exc:
                failure = "restauration subreaper: " + str(exc)
    if pending is None:
        return dict(ok=False, execution_complete=False, closure_confirmed=False,
                    reason=failure, output_dir=str(output_dir))
    output, plan, result, receipt = pending
    limit = plan["budgets"]["output_mib"] * MIB
    terminal = False
    if failure is None and receipt["closure_confirmed"] and time.monotonic() < deadline:
        try:
            report.put(output, "completed.json", {
                "receipt_sha256": report.native.file_sha256(output / "receipt.json")},
                limit)
            terminal = time.monotonic() < deadline
            if not terminal:
                failure = "publication terminale tardive"
        except BaseException as exc:
            failure = "publication terminale: " + str(exc)
    if not terminal:
        marker = output / "completed.json"
        if marker.exists():
            try:
                report.put(output, "cancelled.json", {
                    "receipt_sha256": report.native.file_sha256(output / "receipt.json"),
                    "reason": failure or "clôture non confirmée"}, limit)
            except BaseException:
                # Revoke only the terminal marker created by this invocation.
                marker.unlink(missing_ok=True)
        result.update(execution_complete=False, request_fully_covered=False,
                      selection_complete=False)
    result.update(ok=bool(terminal and result["execution_complete"]),
                  closure_confirmed=terminal, reason=failure)
    return result
