"""Serial supervised reading of acquired evidence; never invokes a producer."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import hashlib
_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()

from ..reporting import design_dossier_schema as s
from ..reporting import design_dossier as report

preflight = s.preflight
BLAS = ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS',
        'VECLIB_MAXIMUM_THREADS','BLIS_NUM_THREADS')
# This program is fixed by the product, never supplied by a JOB. Limits are set
# only inside the owned fresh interpreter, before importing NumPy or any reader.
CHILD = r'''
import os, sys, resource, json, importlib
memory, seconds, file_bytes = map(int, sys.argv[4:7])
resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
# Linux parent-death collection is a backstop; normal cancellation is supervised.
if sys.platform == 'linux':
    import ctypes, signal
    parent = os.getppid()
    ctypes.CDLL(None).prctl(1, signal.SIGKILL)
    if os.getppid() != parent: os._exit(125)
sys.path.insert(0, sys.argv[1])
assert 'numpy' not in sys.modules
assert all(os.environ.get(k) == '1' for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'))
assert sys.argv[2] in ('tolerance_audit','geometry_state','register_target')
try:
    reader = importlib.import_module('didgeridoo_optimizer.reporting.' + sys.argv[2])
    value = reader.read_result(sys.argv[3])
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True))
except Exception as exc:
    print(json.dumps(dict(ok=False, status='reader_error', reason=type(exc).__name__+': '+str(exc)), ensure_ascii=False))
    sys.exit(1)
'''


COLLECTION_SECONDS = 4.0


def remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError('échéance opération/publique dépassée')
    return left


def _collect(child, terminate=False, *, deadline=None):
    """One cumulative deadline, including repeated cancellation and escalation.

    A returned wait is confirmation only when the return code is an integer.
    No host timers/handlers are changed. Only our new session is signalled.
    """
    deadline = time.monotonic()+COLLECTION_SECONDS if deadline is None else deadline
    result = dict(reaped=False, cancelled=False, errors=[])
    if terminate:
        signals = (signal.SIGTERM, signal.SIGKILL)
    else:
        signals = (None, signal.SIGKILL)
    for sig in signals:
        try:
            if sig is not None and child.poll() is None:
                os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass
        except (Exception, KeyboardInterrupt) as exc:
            result['cancelled'] |= isinstance(exc, KeyboardInterrupt)
            result['errors'].append(type(exc).__name__+': '+str(exc))
        # Finite attempts also bound an adversarial double with a frozen clock.
        for _ in range(4):
            try:
                remaining(deadline)
                wait_deadline = min(deadline, time.monotonic()+.5) if sig == signal.SIGTERM else deadline
                code = child.wait(timeout=remaining(wait_deadline))
                result['reaped'] = type(code) is int and type(child.returncode) is int and code == child.returncode
                remaining(wait_deadline)  # includes a late wait without TimeoutExpired
                remaining(deadline)
                if result['reaped']:
                    return result
                result['errors'].append('wait sans code confirmé')
                break
            except KeyboardInterrupt as exc:
                result['cancelled'] = True
                result['errors'].append('KeyboardInterrupt: '+str(exc))
            except (Exception,) as exc:
                result['errors'].append(type(exc).__name__+': '+str(exc))
                break
    return result


def read_native(bundle, directory, index, budgets, deadline, *, closure_bytes=2*s.MIB):
    start = time.monotonic()
    wall = min(budgets['child_seconds'], remaining(deadline))
    operation_deadline = start+wall
    used = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
    available = budgets['output_mib']*s.MIB-used-closure_bytes
    file_bytes = min(budgets['file_mib']*s.MIB, available//2)
    s.require(file_bytes >= 64*1024, 'réserve sortie/clôture insuffisante avant lecteur')
    stdout_path = directory/f'native_{index:03d}.json'
    stderr_path = directory/f'native_{index:03d}.stderr.txt'
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for key in BLAS: env[key] = '1'
    command = [sys.executable, '-I', '-B', '-c', CHILD, str(s.ROOT), bundle['kind'], bundle['path'],
               str(budgets['memory_mib']*s.MIB), str(min(175, max(1, int(wall+1)))), str(file_bytes)]
    child = None; streams = []; timed_out = False; cancelled = False; errors = []
    collection = dict(reaped=False, cancelled=False, errors=[])
    try:
        remaining(operation_deadline)
        streams.append(stdout_path.open('xb')); streams.append(stderr_path.open('xb'))
        remaining(operation_deadline)
        child = subprocess.Popen(command, cwd=s.ROOT, env=env, stdin=subprocess.DEVNULL,
                                 stdout=streams[0], stderr=streams[1], start_new_session=True)
        code = child.wait(timeout=remaining(operation_deadline))
        s.require(type(code) is int and type(child.returncode) is int and code == child.returncode, 'code wait non confirmé')
        remaining(operation_deadline)
    except (subprocess.TimeoutExpired, TimeoutError) as exc:
        timed_out = True; errors.append(type(exc).__name__+': '+str(exc))
    except (Exception, KeyboardInterrupt) as exc:
        cancelled = isinstance(exc, KeyboardInterrupt)
        errors.append(type(exc).__name__+': '+str(exc))
    finally:
        operation_end = time.monotonic()
        timed_out |= operation_end > operation_deadline
        # Same deadline for TERM, KILL, every wait and both descriptor closes.
        collect_deadline = min(operation_end+COLLECTION_SECONDS,
                               deadline+budgets['closure_seconds'])
        if child is not None:
            collection = _collect(child, terminate=timed_out or cancelled or bool(errors), deadline=collect_deadline)
        cancelled |= collection['cancelled']; errors.extend(collection['errors'])
        for stream in streams:
            for _ in range(4):
                try:
                    # Closing regular-file descriptors is still attempted after a
                    # failed collection; lateness remains explicitly unconfirmed.
                    stream.close()
                    remaining(collect_deadline)
                    break
                except KeyboardInterrupt as exc:
                    cancelled = True; errors.append('KeyboardInterrupt: '+str(exc))
                except Exception as exc:
                    errors.append(type(exc).__name__+': '+str(exc)); break
    end = time.monotonic()
    closed = len(streams) == 2 and all(stream.closed for stream in streams)
    timely_collection = end <= collect_deadline
    receipt = dict(schema=s.CHILD_SCHEMA, kind=bundle['kind'], bundle=bundle['path'],
        pid=child.pid if child is not None else None, exit_code=child.returncode if child is not None else None,
        launched=child is not None, reaped=collection['reaped'], pipes_closed=closed,
        collection_confirmed=closed and collection['reaped'] and timely_collection,
        timed_out=timed_out, cancelled=cancelled, errors=errors,
        operation_seconds=operation_end-start, collection_seconds=end-operation_end,
        collection_limit_seconds=max(0., collect_deadline-operation_end),
        seconds=end-start, wall_limit_seconds=wall, memory_mib=budgets['memory_mib'],
        file_bytes_limit=file_bytes, blas_threads=1, fresh_interpreter=True,
        stdout=stdout_path.name, stderr=stderr_path.name)
    for name,path in (('stdout',stdout_path),('stderr',stderr_path)):
        try: receipt[name+'_sha256'] = s.sha(path)
        except (Exception, KeyboardInterrupt) as exc:
            receipt[name+'_sha256'] = None
            receipt['errors'].append(type(exc).__name__+': '+str(exc))
            receipt['cancelled'] |= isinstance(exc, KeyboardInterrupt)
    if receipt['cancelled']:
        value = dict(ok=False, status='cancelled', reason='Lecture annulée; voir collecte confirmée dans le reçu')
    elif timed_out:
        value = dict(ok=False, status='timeout', reason='Délai opération du lecteur dépassé')
    elif not receipt['launched']:
        value = dict(ok=False, status='launch_error', reason='; '.join(errors))
    elif not receipt['collection_confirmed']:
        value = dict(ok=False, status='collection_unconfirmed', reason='; '.join(errors))
    elif receipt['errors']:
        value = dict(ok=False, status='reader_failed', reason='; '.join(receipt['errors']))
    elif child.returncode != 0:
        value = dict(ok=False, status='reader_failed', reason='Code enfant non nul')
    else:
        try:
            remaining(deadline)
            value = s.read(stdout_path)
            s.require(type(value) is dict and type(value.get('ok')) is bool, 'réponse native booléenne')
            remaining(deadline)
        except (Exception, KeyboardInterrupt) as exc:
            receipt['cancelled'] |= isinstance(exc, KeyboardInterrupt)
            receipt['timed_out'] |= isinstance(exc, TimeoutError)
            receipt['errors'].append(type(exc).__name__+': '+str(exc))
            value = dict(ok=False, status='reader_failed', reason=str(exc))
    return value, receipt


def confirmed(child):
    return (child.get('launched') is True and child.get('reaped') is True and
            child.get('pipes_closed') is True and child.get('collection_confirmed') is True and
            type(child.get('exit_code')) is int and child['exit_code'] == 0 and
            child.get('timed_out') is False and child.get('cancelled') is False and not child.get('errors') and
            child['operation_seconds'] <= child['wall_limit_seconds'])


def boundary(plan):
    s.loaded_sources(); s.composition_sources()
    s.verify_pins(plan['pins'])
    for bundle in plan['bundles']:
        root = Path(bundle['path'])
        current = sorted(p.relative_to(root).as_posix() for p in root.rglob('*')
                         if p.is_file() and not any(x.startswith('.') for x in p.relative_to(root).parts))
        s.require(current == bundle['files'], 'inventaire natif modifié: ' + str(root))


def reader_identity():
    sha = subprocess.run(['git','rev-parse','HEAD'], cwd=s.ROOT, check=True, capture_output=True, text=True, timeout=3).stdout.strip()
    return dict(sha=sha, sources=s.loaded_sources(),
                source_status='working_tree_fingerprints_not_a_published_commit', python=sys.version.split()[0])


class Writer:
    def __init__(self, out, budget, deadline=None):
        self.out = out; self.budget = budget*s.MIB; self.deadline = deadline

    def put(self, name, raw):
        if self.deadline is not None: remaining(self.deadline)
        s.require(Path(name).name == name, 'nom sortie')
        s.require(len(raw) <= s.FILE_LIMIT, 'plafond sortie par fichier')
        used = sum(p.stat().st_size for p in self.out.iterdir() if p.is_file())
        s.require(used+len(raw) <= self.budget, 'budget cumulé sortie')
        with (self.out/name).open('xb') as stream:
            stream.write(raw)
        if self.deadline is not None: remaining(self.deadline)

    def json(self, name, value): self.put(name, s.encoded(value))


def seal(out, writer, ok, children, start, operation_deadline, public_deadline, operation_seconds):
    remaining(public_deadline)
    s.require(all(c['reaped'] is True and c['pipes_closed'] is True and c['collection_confirmed'] is True
                  for c in children), 'collecte/fermeture non confirmée')
    manifest = dict(schema=s.RESULT_SCHEMA, files={p.name:s.sha(p) for p in sorted(out.iterdir()) if p.is_file()})
    writer.json('manifest.json', manifest)
    writer.json('execution.json', dict(schema=s.EXECUTION_SCHEMA, candidate_complete=ok,
        status='ready' if ok else 'partial', children=children, operation_seconds=operation_seconds,
        operation_limit_seconds=operation_deadline-start, public_limit_seconds=public_deadline-start,
        manifest_sha256=s.sha(out/'manifest.json')))
    writer.json('execution.closed.json', dict(execution_sha256=s.sha(out/'execution.json'), children_collected=True))
    remaining(public_deadline)
    writer.json('execution.completed.json', dict(schema=s.RESULT_SCHEMA, ok=ok,
        seconds=time.monotonic()-start, manifest_sha256=s.sha(out/'manifest.json'),
        execution_sha256=s.sha(out/'execution.json'), closure_sha256=s.sha(out/'execution.closed.json')))
    remaining(public_deadline)


def run(job, output, dry_run=False):
    start = time.monotonic()
    plan = preflight(job, output)
    b = plan['job']['budgets']; public_deadline = start+b['total_seconds']
    deadline = public_deadline-b['closure_seconds']
    remaining(deadline)
    if dry_run:
        return dict(ok=True, status='preflight', plan=plan, output_created=False, science_performed=False)
    out = s.safe(output, exists=False)
    boundary(plan); identity = reader_identity(); remaining(deadline)
    out.mkdir()  # exclusive; no parents, no overwrite
    writer = Writer(out, b['output_mib'], deadline)
    verified = {}; native_files = {}; children = []; errors = []
    payload = dict(candidates=[], passive_audits=[], comparisons=[], execution_complete=False, errors=errors, scope=report.SCOPE)
    try:
        writer.json('plan.json', plan); writer.json('preflight.json', plan)
        for index, bundle in enumerate(plan['bundles']):
            remaining(deadline); boundary(plan); remaining(deadline)
            response, receipt = read_native(bundle, out, index, b, deadline, closure_bytes=plan['closure_bytes'])
            children.append(receipt)
            remaining(deadline); boundary(plan); remaining(deadline)
            key = report._key(bundle['kind'], bundle['path'])
            if confirmed(receipt) and report.usable(bundle['kind'], response):
                verified[key] = response; native_files[key] = receipt['stdout']
            if response.get('ok') is not True or not confirmed(receipt):
                errors.append(dict(bundle=bundle['path'], kind=bundle['kind'], status=response.get('status'), reason=response.get('reason')))
            if receipt['cancelled'] or receipt['timed_out'] or not receipt['collection_confirmed']:
                raise RuntimeError('lecture interrompue ou collecte non confirmée')
        remaining(deadline); boundary(plan); remaining(deadline)
        payload = report.compose(plan, verified, errors)
        remaining(deadline); boundary(plan); remaining(deadline)
        result = dict(schema_version=s.RESULT_SCHEMA, created_utc=datetime.now(timezone.utc).isoformat(),
                      reader=identity, payload=payload, native_results=native_files)
        writer.json('result.json', result)
        for c,a in zip(payload['candidates'], plan['associations']):
            remaining(deadline); writer.put(c['profile_file'], s.data(a['profile']))
        remaining(deadline)
        rendered = report.views(payload)
        remaining(deadline)
        for name, raw in rendered.items(): writer.put(name, raw)
        remaining(deadline); boundary(plan); remaining(deadline)
        ok = payload['execution_complete'] is True
        operation_seconds = time.monotonic()-start
        remaining(deadline)
        writer.deadline = public_deadline
        seal(out, writer, ok, children, start, deadline, public_deadline, operation_seconds)
        remaining(public_deadline); boundary(plan); remaining(public_deadline)
        return dict(ok=ok, status='complete' if ok else 'partial', output=str(out),
                    payload=payload, children=children, seconds=time.monotonic()-start)
    except (Exception, KeyboardInterrupt) as exc:
        errors.append(dict(status='cancelled' if isinstance(exc, KeyboardInterrupt) else 'partial',
                           reason=type(exc).__name__+': '+str(exc)))
        # Only this invocation's new marker is revoked. No archive is rewritten.
        # Finite retries tolerate repeated cancellation without changing handlers.
        for _ in range(8):
            try:
                (out/'execution.completed.json').unlink(missing_ok=True)
                break
            except KeyboardInterrupt:
                continue
            except Exception as revoke_error:
                errors.append(dict(status='revocation_failed', reason=str(revoke_error)))
                break
        failure = dict(ok=False, status='unconfirmed', errors=errors, children=children)
        for _ in range(8):
            try:
                if not (out/'execution.failed.json').exists():
                    Writer(out, b['output_mib']).json('execution.failed.json', failure)
                break
            except KeyboardInterrupt:
                continue
            except Exception:
                break
        payload['execution_complete'] = False
        return dict(ok=False, status='partial', output=str(out), payload=payload,
                    children=children, errors=errors, seconds=time.monotonic()-start)
