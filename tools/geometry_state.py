"""Build or verify a geometry-specific certified scalar native state bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
ENTRY=Path(__file__).resolve()
ENTRY_SHA=hashlib.sha256(ENTRY.read_bytes()).hexdigest()
OWNED=__name__=='__main__'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('config','design','recipe','output-dir','worker','read-result','verify-pending'):p.add_argument('--'+name)
    p.add_argument('--dry-run',action='store_true');a=p.parse_args(argv)
    try:
        special=int(a.worker is not None)+int(a.read_result is not None)+int(a.verify_pending is not None)
        if special>1 or (special and any((a.config,a.design,a.recipe,a.output_dir,a.dry_run))):raise ValueError('Exclusive CLI modes')
        if special and not OWNED:raise ValueError('Worker/reader requires owned fresh CLI')
        if not special and not all((a.config,a.design,a.recipe,a.output_dir)):raise ValueError('CONFIG DESIGN recipe output required')
        if OWNED:
            import signal
            def cancelled(signum,frame): raise InterruptedError('Geometry CLI cancelled by signal '+str(signum))
            for signum in (signal.SIGTERM,signal.SIGINT,signal.SIGALRM): signal.signal(signum,cancelled)
            if special:
                signal.alarm(175)
                if a.read_result or a.verify_pending:
                    import resource
                    for kind,cap in ((resource.RLIMIT_AS,768*1024**2),(resource.RLIMIT_CPU,175),(resource.RLIMIT_FSIZE,4*1024**2)):
                        soft,hard=resource.getrlimit(kind)
                        bound=cap if hard==resource.RLIM_INFINITY else min(cap,hard)
                        resource.setrlimit(kind,(bound,bound))
            if 'numpy' in sys.modules:raise ValueError('Fresh CLI required before NumPy')
            for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='1'
        from didgeridoo_optimizer.pipeline import geometry_state as flow
        from didgeridoo_optimizer.reporting import geometry_state as report
        from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as strict
        if ENTRY!=flow.ROOT/'tools/geometry_state.py' or strict.sha(ENTRY.read_bytes())!=ENTRY_SHA:raise ValueError('Copied/changed CLI')
        if special:
            flow.ready()
        if a.worker:value=flow.worker(strict.read_json(a.worker))
        elif a.read_result:value=report.read_result(a.read_result)
        elif a.verify_pending:value=report.read_payload(a.verify_pending)
        else:value=flow.run(a.config,a.design,strict.read_json(a.recipe),a.output_dir,dry_run=a.dry_run)
        print(json.dumps(value,allow_nan=False));return 0 if value['ok'] else 1
    except Exception as exc:
        print(json.dumps(dict(ok=False,status='refused',reason=type(exc).__name__+': '+str(exc))));return 2


if __name__=='__main__':raise SystemExit(main())
