"""STATE-PORT-01: explicit state coefficients and prescribed SI flow, no fit."""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import sys

_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()

# Set before NumPy is imported in a fresh CLI process. API callers own their environment.
if __name__ == '__main__' and '--worker' not in sys.argv[1:]:
    for _variable in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[_variable]='1'

from didgeridoo_optimizer.nonlinear.passive_state import verify_loaded_sources
ROOT = Path(__file__).resolve().parents[1]
from didgeridoo_optimizer.reporting.state_port_reference import read_json, read_result


class Parser(argparse.ArgumentParser):
    def error(self, message): raise ValueError(message)


def main(argv=None):
    try:
        verify_loaded_sources()
        if Path(__file__).resolve()!=ROOT/'tools/state_port_reference.py':
            raise ValueError('Mixed producer source roots')
        argv = sys.argv[1:] if argv is None else argv
        if len(argv)==2 and argv[0]=='--worker':
            from didgeridoo_optimizer.pipeline.state_port_reference import worker, worker_ready
            worker_ready()
            code = worker(read_json(argv[1]))
            verify_loaded_sources()
            return code
        p = Parser(description=__doc__)
        p.add_argument('--model-in')
        p.add_argument('--signal-in')
        p.add_argument('--output-dir')
        p.add_argument('--dry-run', action='store_true')
        p.add_argument('--seconds', type=float, default=175.)
        p.add_argument('--read-result')
        a = p.parse_args(argv)
        if a.read_result:
            if a.model_in or a.signal_in or a.output_dir or a.dry_run:
                raise ValueError('Read-result is a standalone offline operation')
            result = read_result(a.read_result)
        else:
            if not all((a.model_in,a.signal_in,a.output_dir)):
                raise ValueError('--model-in, --signal-in and --output-dir are required')
            from didgeridoo_optimizer.pipeline.state_port_reference import run
            result = run(a.model_in,a.signal_in,a.output_dir,dry_run=a.dry_run,seconds=a.seconds)
        verify_loaded_sources()
    except (Exception, KeyboardInterrupt) as exc:
        result = dict(ok=False,status='refused',error=type(exc).__name__+': '+str(exc))
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0 if result['ok'] else 1


if __name__=='__main__': raise SystemExit(main())
