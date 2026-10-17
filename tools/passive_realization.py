"""Opt-in certified scalar conversion; standalone offline result reader."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
if __name__=='__main__' and '--worker' not in sys.argv[1:]:
    for _key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[_key]='1'

from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as certs
from didgeridoo_optimizer.reporting.passive_realization import read_result


class Parser(argparse.ArgumentParser):
    def error(self,message): raise ValueError(message)


def main(argv=None):
    try:
        certs.verify_sources(); argv=sys.argv[1:] if argv is None else argv
        if len(argv)==2 and argv[0]=='--worker':
            from didgeridoo_optimizer.pipeline.passive_realization import worker,worker_ready
            worker_ready(); code=worker(certs.read_json(argv[1])); certs.verify_sources(); return code
        parser=Parser(description=__doc__)
        parser.add_argument('--source'); parser.add_argument('--certificate'); parser.add_argument('--output-dir')
        parser.add_argument('--dry-run',action='store_true'); parser.add_argument('--seconds',type=float,default=175.)
        parser.add_argument('--read-result')
        a=parser.parse_args(argv)
        if a.read_result:
            if a.source or a.certificate or a.output_dir or a.dry_run: raise ValueError('Reader is standalone')
            result=read_result(a.read_result)
        else:
            if not a.source or not a.output_dir: raise ValueError('--source and --output-dir required')
            from didgeridoo_optimizer.pipeline.passive_realization import run
            result=run(a.source,a.certificate,a.output_dir,dry_run=a.dry_run,seconds=a.seconds)
        certs.verify_sources()
    except (Exception,KeyboardInterrupt) as exc:
        result=dict(ok=False,status='refused',error=type(exc).__name__+': '+str(exc))
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0 if result['ok'] else 1


certs.verify_sources()
if __name__=='__main__': raise SystemExit(main())
