"""Accorder 1..4 DESIGN JSON/YAML réels puis comparer sous une source commune."""
from __future__ import annotations

import argparse
import json
import sys

from didgeridoo_optimizer.acoustics.radiation_models import NAMES as RADIATION_NAMES
from didgeridoo_optimizer.pipeline.design_pitch import run


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    p = Parser(description=__doc__, allow_abbrev=False)
    p.add_argument('--config', required=True)
    p.add_argument('--design', nargs='+', required=True)
    p.add_argument('--target-hz', type=float, default=70.)
    p.add_argument('--scale-min', type=float, default=.8)
    p.add_argument('--scale-max', type=float, default=1.4)
    p.add_argument('--h-cm', type=float, help='Maillage accord ; vérification et réponses à h/2 (sinon CONFIG)')
    p.add_argument('--max-iterations', type=int, default=16)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--pressure-peak-pa', type=float)
    source.add_argument('--flow-peak-m3-s', type=float)
    source.add_argument('--thevenin-pressure-peak-pa', type=float)
    p.add_argument('--source-resistance-pa-s-m3', type=float)
    p.add_argument('--loss-model', choices=['legacy', 'zk'], default='legacy')
    p.add_argument('--radiation-model', choices=RADIATION_NAMES, default='legacy')
    p.add_argument('--air-reference', choices=['ck_dry20', 'ck_dry25'])
    p.add_argument('--output-dir', required=True)
    p.add_argument('--dry-run', action='store_true')
    return p


def main(argv=None):
    try:
        args = vars(parser().parse_args(argv))
        args['designs'] = args.pop('design')
        result = run(**args)
    except Exception as exc:
        result = dict(ok=False, status='error', error=dict(type=type(exc).__name__, message=str(exc)))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
