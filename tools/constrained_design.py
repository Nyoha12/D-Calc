"""CLI française du contrat de conception statique."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

from didgeridoo_optimizer.pipeline.constrained_design import ROOT, run, worker

ENTRY_PATH=Path(__file__).resolve()
ENTRY_SHA256=hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest()


def main(argv=None):
    parser=argparse.ArgumentParser(description='Conception statique sous exigences explicites')
    parser.add_argument('--config'); parser.add_argument('--design'); parser.add_argument('--request')
    parser.add_argument('--output-dir'); parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    try:
        # This entry point may only claim the provenance of its executed file.
        # Client scripts calling the public API do not use this CLI guard.
        if ENTRY_PATH!=ROOT/'tools/constrained_design.py' or (
                hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest()!=ENTRY_SHA256):
            raise ValueError('provenance CLI refusée: entrée copiée ou modifiée depuis chargement')
        if args.worker:
            result=worker(json.load(sys.stdin))
        else:
            if not all((args.config,args.design,args.request,args.output_dir)):
                parser.error('--config, --design, --request et --output-dir sont obligatoires')
            result=run(args.config,args.design,args.request,args.output_dir,dry_run=args.dry_run)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0 if result['ok'] else 1
    except Exception as exc:
        print(json.dumps(dict(ok=False,status='invalid_request' if isinstance(exc,ValueError) else 'failed',
                              reason=str(exc)),ensure_ascii=False,allow_nan=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
