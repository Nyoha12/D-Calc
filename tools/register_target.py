"""Prescribed register experiment CLI; copied entrypoints cannot claim this checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ENTRY_PATH=Path(__file__).resolve()
ENTRY_SHA256=hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest()
_OWNED_CLI=__name__=='__main__'
_BLAS_KEYS=('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')


def _validate_modes(args):
    special=sum((args.read_result is not None,args.worker_plan is not None,args.worker))
    normal=(args.plan is not None or args.output_dir is not None or args.dry_run
            or args.resume is not None or args.steps_this_run is not None)
    if special>1 or (special and normal):
        raise ValueError('Ambiguous CLI modes: reader/worker modes are exclusive of each other and plan options')
    if not special and (not args.plan or not args.output_dir):
        raise ValueError('--plan et --output-dir requis')


def _reader_owned():
    import signal
    if not _OWNED_CLI or 'numpy' in sys.modules:
        raise ValueError('read-result requires a fresh owned CLI process; use reporting.read_result with an external budget')
    if signal.getitimer(signal.ITIMER_REAL)!=(0.,0.) or signal.getsignal(signal.SIGALRM)!=signal.SIG_DFL:
        raise ValueError('read-result refuses an existing caller timer or SIGALRM handler')


def main(argv=None):
    parser=argparse.ArgumentParser(description='Commandes prescrites, périodicité observée et exigences indépendantes')
    parser.add_argument('--plan');parser.add_argument('--output-dir');parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--resume',help='Checkpoint produit vérifié, reprise strictement identique')
    parser.add_argument('--steps-this-run',type=int,help='Arrêt partiel explicite sans modifier le PLAN ni ses quotas cumulés')
    parser.add_argument('--worker-plan',help=argparse.SUPPRESS)
    parser.add_argument('--read-result',help='Vérifier un résultat; inputs et preuves sources restent requis')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    try:
        _validate_modes(args)
        if args.read_result is not None:
            _reader_owned()
        # Establish and capture BLAS before pipeline/__init__.py imports NumPy.
        if _OWNED_CLI and 'numpy' not in sys.modules:
            for key in _BLAS_KEYS:os.environ[key]='1'
        blas_before_import={key:os.environ.get(key) for key in _BLAS_KEYS} if 'numpy' not in sys.modules else None
        from didgeridoo_optimizer.pipeline.register_target import ROOT, run, worker, _state_worker_ready
        from didgeridoo_optimizer.nonlinear.passive_resonator import _pairs, _constant
        if ENTRY_PATH!=ROOT/'tools/register_target.py' or hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest()!=ENTRY_SHA256:
            raise ValueError('CLI source mismatch: copied or modified entry refused; imported API remains available')
        if args.read_result is not None:
            import signal
            from didgeridoo_optimizer.pipeline.register_target import _state_limits
            from didgeridoo_optimizer.reporting.register_target import read_result
            _state_limits();_state_worker_ready(blas_before_import);signal.alarm(175)
            result=read_result(args.read_result)
        elif args.worker_plan is not None:
            _state_worker_ready(blas_before_import)
            from didgeridoo_optimizer.reporting.register_target import read_json
            result=worker(dict(plan=read_json(args.worker_plan)),_blas_before_import=blas_before_import)
        elif args.worker:
            raw=sys.stdin.read(4*1024**2+1)
            if len(raw)>4*1024**2:raise ValueError('Worker task budget')
            result=worker(json.loads(raw,object_pairs_hook=_pairs,parse_constant=_constant),_blas_before_import=blas_before_import)
        else:
            result=run(args.plan,args.output_dir,dry_run=args.dry_run,resume=args.resume,steps_this_run=args.steps_this_run)
        print(json.dumps(result,allow_nan=False,ensure_ascii=False))
        # A planned partial stop is a successful child computation, not complete trajectory.
        return 0 if args.worker or args.worker_plan or result['ok'] else 1
    except Exception as exc:
        print(json.dumps(dict(ok=False,status='invalid_request' if isinstance(exc,ValueError) else 'failed',
                              reason=f'{type(exc).__name__}: {exc}'),allow_nan=False,ensure_ascii=False))
        return 2


if __name__=='__main__':raise SystemExit(main())
