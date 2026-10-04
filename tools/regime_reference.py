"""Opt-in CONFIG/DESIGN/DB native regime progression, without fitting."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from didgeridoo_optimizer.pipeline import regime_reference as workflow
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters
from didgeridoo_optimizer.nonlinear.regime_observables import ObservationPlan
from didgeridoo_optimizer.reporting.regime_reference import read_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--config');parser.add_argument('--design');parser.add_argument('--output')
    parser.add_argument('--model-in');parser.add_argument('--lip-parameters',help='Strict JSON of all dimensioned SI lip parameters')
    parser.add_argument('--rho',type=float,help='Explicit Bernoulli density kg/m3, independently recorded from fit air')
    parser.add_argument('--observation-plan',help='Strict JSON: fixed windows, section and complete state scales')
    parser.add_argument('--target-steps',type=int,help='Cumulative target from original chain, <=72000 and <=6 seconds')
    parser.add_argument('--sample-rate-hz',type=int,default=12000)
    parser.add_argument('--v2-port-model',choices=['jet-only','conjugate'],default='jet-only')
    parser.add_argument('--resume');parser.add_argument('--seconds',type=float,default=170.)
    parser.add_argument('--checkpoint-steps',type=int,default=1200)
    parser.add_argument('--max-extensions',type=int,default=32);parser.add_argument('--max-iterations',type=int,default=80)
    parser.add_argument('--basis-completion',choices=['observed-only','r29'],default='observed-only')
    parser.add_argument('--loss-model',choices=['legacy','zk'],default='zk')
    parser.add_argument('--radiation-model',default='legacy');parser.add_argument('--air-reference',default='ck_dry20')
    parser.add_argument('--h-cm',type=float,default=.5);parser.add_argument('--R0',type=float);parser.add_argument('--dc-origin')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    if args.worker:return workflow.worker()
    for key in ('config','design','output','model_in','lip_parameters','rho','observation_plan','target_steps'):
        if getattr(args,key) is None:parser.error('--'+key.replace('_','-')+' required')
    try:
        if Path(__file__).resolve()!=workflow.ROOT/'tools/regime_reference.py':
            raise ValueError('Mixed CLI producer roots')
        fields=read_json(args.lip_parameters)
        if type(fields) is not dict or set(fields)!=set(DimensionedLipParameters().as_dict()):
            raise ValueError('All dimensioned lip parameters must be declared')
        params=DimensionedLipParameters(**fields)
        obs=ObservationPlan.from_dict(read_json(args.observation_plan))
        options=vars(args).copy()
        for k in ('worker','config','design','output','lip_parameters','observation_plan'):options.pop(k)
        value=workflow.run(args.config,args.design,args.output,params=params,observation=obs,**options)
    except (Exception,KeyboardInterrupt) as exc:
        value=dict(ok=False,status='refused',reason=type(exc).__name__+': '+str(exc))
    print(json.dumps(value,allow_nan=False,sort_keys=True))
    return 0 if value['ok'] else 2


if __name__=='__main__':raise SystemExit(main())
