"""Explicit experimental passive reference CLI; no default pipeline changes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from didgeridoo_optimizer.pipeline.time_domain_reference import run, worker


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    p = Parser(description=__doc__)
    p.add_argument('--config', required=True)
    p.add_argument('--design', required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--dry-run', action='store_true')
    for name, default in [('sample-rate-hz',12000),('fit-points',4096),('guard-points',768),('audit-points',1536),('signal-samples',2048),('max-passes',10)]:
        p.add_argument('--'+name,type=int,default=default)
    for name, default in [('fit-min-hz',40.),('fit-max-hz',3000.),('guard-max-hz',3500.),('h-cm',.5),('seconds',175.),('fit-seconds',110.),('mesh-gate',.005),('flow-peak-m3-s',1e-6),('v2-duration-s',.05)]:
        p.add_argument('--'+name,type=float,default=default)
    p.add_argument('--loss-model',choices=['legacy','zk'],default='zk')
    p.add_argument('--radiation-model',default='legacy')
    p.add_argument('--air-reference',choices=['ck_dry20','ck_dry25'])
    p.add_argument('--r0',dest='R0',type=float)
    p.add_argument('--dc-origin')
    p.add_argument('--model-in')
    p.add_argument('--basis-completion', choices=['observed-only','r29'], default='observed-only',
        help='Declared numerical basis (default: observed-only); r29 adds two numerical out-of-band terms, not observed modes')
    p.add_argument('--v2-pressure-pa',type=float)
    p.add_argument('--v2-schedule', choices=['historical','simultaneous'], default='historical',
        help='Explicit V2 schedule for passive backend; FIR comparator remains historical')
    p.add_argument('--nrmse-gate',type=float,default=.005)
    p.add_argument('--max-relative-gate',type=float,default=.08)
    p.add_argument('--phase-rms-gate-deg',type=float,default=.3)
    return p


def main(argv=None):
    from didgeridoo_optimizer.pipeline.time_domain_reference import ROOT
    if Path(__file__).resolve() != ROOT/'tools/time_domain_reference.py':
        print(json.dumps(dict(ok=False,status='refused',error='Mixed producer source roots')))
        return 1
    argv = sys.argv[1:] if argv is None else argv
    if argv == ['--worker']:
        return worker()
    try:
        args = vars(parser().parse_args(argv))
        args['gates'] = dict(complex_nrmse=args.pop('nrmse_gate'),relative_max=args.pop('max_relative_gate'),phase_rms_deg=args.pop('phase_rms_gate_deg'))
        result = run(**args)
    except (Exception,KeyboardInterrupt) as exc:
        result = dict(ok=False,status='refused',error=type(exc).__name__+': '+str(exc))
    print(json.dumps(result,allow_nan=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
