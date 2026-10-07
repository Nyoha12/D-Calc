"""CLI for explicit paired local stability; no fit or trajectory."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from didgeridoo_optimizer.pipeline.paired_onset import ROOT, run, worker
from didgeridoo_optimizer.nonlinear.passive_resonator import _pairs, _constant

ENTRY_PATH=Path(__file__).resolve()
ENTRY_SHA256=hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest()


def main(argv=None):
    parser=argparse.ArgumentParser(description='Comparaison appariée de stabilité locale sous scénarios explicites')
    parser.add_argument('--plan');parser.add_argument('--output-dir')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    try:
        if ENTRY_PATH!=ROOT/'tools/paired_onset_reference.py' or hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest()!=ENTRY_SHA256:
            raise ValueError('CLI source mismatch: copied or modified entry refused')
        if args.worker:
            raw=sys.stdin.read(4*1024**2+1)
            if len(raw)>4*1024**2: raise ValueError('Worker task size budget')
            result=worker(json.loads(raw,object_pairs_hook=_pairs,parse_constant=_constant))
        else:
            if not args.plan or not args.output_dir: parser.error('--plan et --output-dir requis')
            result=run(args.plan,args.output_dir,dry_run=args.dry_run)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0 if result['ok'] else 1
    except Exception as exc:
        print(json.dumps(dict(ok=False,status='invalid_request' if isinstance(exc,ValueError) else 'failed',
                              reason=f'{type(exc).__name__}: {exc}'),ensure_ascii=False,allow_nan=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
