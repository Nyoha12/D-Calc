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


def _collect(child, terminate=False):
    if terminate and child.poll() is None:
        try: os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: child.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
    return child.wait()


def read_native(bundle, directory, index, budgets, deadline, *, closure_bytes=2*s.MIB):
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise TimeoutError('budget total de lecture épuisé; réserve de clôture conservée')
    wall = min(budgets['child_seconds'], remaining)
    # Reserve output space BEFORE opening files or starting a process.
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
    child = None; timed_out = False; cancelled = False; start = time.monotonic()
    try:
        with stdout_path.open('xb') as stdout, stderr_path.open('xb') as stderr:
            child = subprocess.Popen(command, cwd=s.ROOT, env=env, stdin=subprocess.DEVNULL,
                                     stdout=stdout, stderr=stderr, start_new_session=True)
            try: child.wait(timeout=wall)
            except subprocess.TimeoutExpired:
                timed_out = True
                _collect(child, terminate=True)
            except KeyboardInterrupt:
                cancelled = True
                _collect(child, terminate=True)
            finally:
                _collect(child, terminate=child.poll() is None)
    finally:
        if child is not None and child.poll() is None:
            _collect(child, terminate=True)
    receipt = dict(kind=bundle['kind'], bundle=bundle['path'], pid=child.pid,
        exit_code=child.returncode, reaped=True, pipes_closed=True, timed_out=timed_out, cancelled=cancelled,
        seconds=time.monotonic()-start, wall_limit_seconds=wall, memory_mib=budgets['memory_mib'],
        file_bytes_limit=file_bytes, blas_threads=1, fresh_interpreter=True,
        stdout=stdout_path.name, stdout_sha256=s.sha(stdout_path), stderr=stderr_path.name, stderr_sha256=s.sha(stderr_path))
    if cancelled:
        value = dict(ok=False, status='cancelled', reason='Lecture annulée et enfant collecté')
    elif timed_out:
        value = dict(ok=False, status='timeout', reason='Délai du lecteur natif dépassé')
    else:
        try: value = s.read(stdout_path)
        except (ValueError, OSError): value = dict(ok=False, status='reader_failed', reason='Réponse native absente/invalide')
        if child.returncode != 0:
            value = dict(ok=False, status='reader_failed', reason=value.get('reason', 'Code enfant non nul'))
    return value, receipt


def boundary(plan):
    s.verify_pins(plan['pins'])
    for bundle in plan['bundles']:
        root = Path(bundle['path'])
        current = sorted(p.relative_to(root).as_posix() for p in root.rglob('*')
                         if p.is_file() and not any(x.startswith('.') for x in p.relative_to(root).parts))
        s.require(current == bundle['files'], 'inventaire natif modifié: ' + str(root))


def reader_identity():
    sha = subprocess.run(['git','rev-parse','HEAD'], cwd=s.ROOT, check=True, capture_output=True, text=True, timeout=3).stdout.strip()
    return dict(sha=sha, sources={name:s.sha(s.ROOT/name) for name in s.OWN_SOURCES},
                source_status='working_tree_fingerprints_not_a_published_commit', python=sys.version.split()[0])


class Writer:
    def __init__(self, out, budget):
        self.out = out; self.budget = budget*s.MIB

    def put(self, name, raw):
        s.require(Path(name).name == name, 'nom sortie')
        s.require(len(raw) <= s.FILE_LIMIT, 'plafond sortie par fichier')
        used = sum(p.stat().st_size for p in self.out.iterdir() if p.is_file())
        s.require(used+len(raw) <= self.budget, 'budget cumulé sortie')
        with (self.out/name).open('xb') as stream:
            stream.write(raw)

    def json(self, name, value): self.put(name, s.encoded(value))


def seal(out, writer, ok, children):
    s.require(all(c['reaped'] and c['pipes_closed'] for c in children), 'enfant non collecté')
    manifest = dict(schema=s.RESULT_SCHEMA, files={p.name:s.sha(p) for p in sorted(out.iterdir()) if p.is_file()})
    writer.json('manifest.json', manifest)
    writer.json('execution.json', dict(ok=ok, status='complete' if ok else 'partial', children=children,
                                      manifest_sha256=s.sha(out/'manifest.json')))
    writer.json('execution.closed.json', dict(execution_sha256=s.sha(out/'execution.json'), children_collected=True))
    writer.json('execution.completed.json', dict(schema=s.RESULT_SCHEMA, manifest_sha256=s.sha(out/'manifest.json'),
        execution_sha256=s.sha(out/'execution.json'), closure_sha256=s.sha(out/'execution.closed.json')))


def run(job, output, dry_run=False):
    start = time.monotonic()
    plan = preflight(job, output)
    if dry_run:
        return dict(ok=True, status='preflight', plan=plan, output_created=False, science_performed=False)
    out = s.safe(output, exists=False); b = plan['job']['budgets']
    deadline = start+b['total_seconds']-b['closure_seconds']
    boundary(plan)
    identity = reader_identity()
    out.mkdir()  # exclusive; no parents, no overwrite
    writer = Writer(out, b['output_mib'])
    writer.json('plan.json', plan); writer.json('preflight.json', plan)
    verified = {}; native_files = {}; children = []; errors = []; interrupted = False
    try:
        for index, bundle in enumerate(plan['bundles']):
            boundary(plan)
            response, receipt = read_native(bundle, out, index, b, deadline, closure_bytes=plan['closure_bytes'])
            children.append(receipt)
            boundary(plan)
            key = report._key(bundle['kind'], bundle['path'])
            if report.usable(bundle['kind'], response):
                verified[key] = response; native_files[key] = receipt['stdout']
            if response.get('ok') is not True:
                errors.append(dict(bundle=bundle['path'], kind=bundle['kind'], status=response.get('status'), reason=response.get('reason')))
            if receipt['cancelled']:
                interrupted = True
                break
        boundary(plan)
    except (Exception, KeyboardInterrupt) as exc:
        errors.append(dict(status='cancelled' if isinstance(exc, KeyboardInterrupt) else 'partial', reason=type(exc).__name__+': '+str(exc)))
        interrupted = isinstance(exc, KeyboardInterrupt)
    try:
        payload = report.compose(plan, verified, errors)
        # Persistent mutation prohibits a successful terminal receipt, even if
        # native readers had individually confirmed the earlier bytes.
        boundary(plan)
    except (Exception, KeyboardInterrupt) as exc:
        errors.append(dict(status='invalid_evidence', reason=type(exc).__name__+': '+str(exc)))
        payload = dict(candidates=[], passive_audits=[], comparisons=[], execution_complete=False, errors=errors, scope=report.SCOPE)
    result = dict(schema_version=s.RESULT_SCHEMA, created_utc=datetime.now(timezone.utc).isoformat(),
                  reader=identity, payload=payload, native_results=native_files)
    writer.json('result.json', result)
    for c,a in zip(payload['candidates'], plan['associations']):
        writer.put(c['profile_file'], s.data(a['profile']))
    for name, raw in report.views(payload).items(): writer.put(name, raw)
    ok = payload['execution_complete'] and not interrupted
    # Last source boundary before publishing authority. Failed/partial outputs
    # still retain their native responses, errors and terminal receipt.
    if ok:
        try: boundary(plan)
        except Exception:
            ok = False
    seal(out, writer, ok, children)
    return dict(ok=ok, status='complete' if ok else 'partial', output=str(out),
                payload=payload, children=children, seconds=time.monotonic()-start)
