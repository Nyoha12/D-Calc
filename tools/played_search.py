"""Finite shared-geometry feasibility search with prescribed native experiments."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


OWNED=__name__=='__main__'
ENTRY=Path(__file__).resolve()
ENTRY_SHA=hashlib.sha256(ENTRY.read_bytes()).hexdigest()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job'); parser.add_argument('--output-dir')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--read-result')
    args=parser.parse_args(argv)
    try:
        if (args.worker and (args.job or args.output_dir or args.dry_run or args.read_result)) or (args.read_result and (args.job or args.output_dir or args.dry_run)):
            raise ValueError('Exclusive CLI modes')
        if OWNED and 'numpy' not in sys.modules:
            for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[key]='1'
        from didgeridoo_optimizer.pipeline.played_search import ROOT, run, worker
        from didgeridoo_optimizer.nonlinear.passive_resonator import _pairs, _constant
        if ENTRY != ROOT/'tools/played_search.py' or hashlib.sha256(ENTRY.read_bytes()).hexdigest()!=ENTRY_SHA:
            raise ValueError('Copied or changed CLI refused')
        if args.read_result:
            if not OWNED: raise ValueError('Fresh owned reader CLI required')
            from didgeridoo_optimizer.pipeline.played_search import _limits
            from didgeridoo_optimizer.reporting.played_search import read_result
            import signal
            _limits();signal.alarm(175);result=read_result(args.read_result)
        elif args.worker:
            raw=sys.stdin.read(4*1024**2+1)
            if len(raw)>4*1024**2: raise ValueError('Worker task quota')
            result=worker(json.loads(raw,object_pairs_hook=_pairs,parse_constant=_constant))
        else:
            if not args.job or not args.output_dir: parser.error('--job et --output-dir requis')
            result=run(args.job,args.output_dir,dry_run=args.dry_run)
        print(json.dumps(result,allow_nan=False,ensure_ascii=False))
        return 0 if result.get('ok') or (args.worker and result.get('read_confirmed') is True) else 1
    except (Exception,KeyboardInterrupt) as exc:
        print(json.dumps(dict(ok=False,status='refused',reason=type(exc).__name__+': '+str(exc)),allow_nan=False))
        return 2


if __name__=='__main__': raise SystemExit(main())
