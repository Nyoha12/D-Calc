"""CLI française du contrat de conception statique."""
from __future__ import annotations
import argparse
import json
import sys

from didgeridoo_optimizer.pipeline.constrained_design import run, worker


def main(argv=None):
    parser=argparse.ArgumentParser(description='Conception statique sous exigences explicites')
    parser.add_argument('--config'); parser.add_argument('--design'); parser.add_argument('--request')
    parser.add_argument('--output-dir'); parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    try:
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
