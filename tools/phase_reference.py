"""Offline TRAIN phase maps from a saved native bundle; no simulation or refit."""
import argparse
import json
from pathlib import Path
from didgeridoo_optimizer.pipeline import phase_reference as workflow


def main(argv=None):
    if Path(__file__).resolve()!=workflow.ROOT/'tools/phase_reference.py':
        print(json.dumps(dict(ok=False,status='refused',reason='Mixed CLI producer roots')))
        return 2
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-bundle',required=True)
    parser.add_argument('--plan',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    result=workflow.run(args.input_bundle,args.plan,args.output,dry_run=args.dry_run)
    # Keep stdout compact; exact records are retained in the immutable export.
    view=result if args.dry_run else {k:result.get(k) for k in ('schema','ok','status','reason','verified_saved_steps')}
    print(json.dumps(view,sort_keys=True,allow_nan=False))
    return 0 if result['ok'] else 2


if __name__=='__main__':raise SystemExit(main())
