"""Finite tolerance audit, one fixed native instrument and explicit scenarios."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

# Disable interpreter cache writes, including in a strict dry-run.
sys.dont_write_bytecode = True
from didgeridoo_optimizer.pipeline.tolerance_audit import ROOT, run, worker

ENTRY_PATH = Path(__file__).resolve()
ENTRY_SHA256 = hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Audit de scénarios dimensionnels finis, sans recherche')
    parser.add_argument('--job')
    parser.add_argument('--output-dir')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if ENTRY_PATH != ROOT/'tools/tolerance_audit.py' or hashlib.sha256(ENTRY_PATH.read_bytes()).hexdigest()!=ENTRY_SHA256:
            raise ValueError('provenance CLI refusée: entrée copiée/modifiée')
        if args.worker:
            result = worker(json.load(sys.stdin))
        else:
            if not args.job or not args.output_dir: parser.error('--job et --output-dir obligatoires')
            result = run(args.job,args.output_dir,dry_run=args.dry_run)
        # A successful dry-run is silent: validation only, no output artifact/stdout.
        if not args.dry_run: print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0 if result['ok'] else 1
    except Exception as exc:
        print(json.dumps(dict(ok=False,status='invalid_request' if isinstance(exc,ValueError) else 'failed',
                              reason=str(exc)),ensure_ascii=False,allow_nan=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
