"""Native entry point for comparative dossiers over previously acquired evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ENTRY = Path(__file__).resolve()
ENTRY_SHA = hashlib.sha256(ENTRY.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Dossier comparatif français de preuves acquises')
    parser.add_argument('--job'); parser.add_argument('--output-dir')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--read-result')
    args = parser.parse_args(argv)
    try:
        if args.read_result is not None:
            if args.job is not None or args.output_dir is not None or args.dry_run:
                raise ValueError('modes --read-result et création/dry-run exclusifs')
        elif not args.job or not args.output_dir:
            raise ValueError('--job et --output-dir requis')
        # Isolated worker processes always establish their own environment.
        # The genuine CLI establishes BLAS before package import as well.
        if __name__ == '__main__' and 'numpy' not in sys.modules:
            for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
                os.environ[key] = '1'
        if __name__ == '__main__':
            import signal
            def cancel(signum, frame):
                raise KeyboardInterrupt('annulation CLI')
            signal.signal(signal.SIGTERM, cancel)
        from didgeridoo_optimizer.reporting import design_dossier_schema as s
        s.loaded_sources()
        if ENTRY != s.ROOT/'tools/design_dossier.py' or s.sha(ENTRY) != ENTRY_SHA:
            raise ValueError('entrée CLI copiée/modifiée refusée')
        if args.read_result:
            from didgeridoo_optimizer.reporting.design_dossier import read_result
            value = read_result(args.read_result)
        else:
            from didgeridoo_optimizer.pipeline.design_dossier import run
            value = run(args.job, args.output_dir, dry_run=args.dry_run)
        print(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True))
        return 0 if value['ok'] else 1
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps(dict(ok=False, status='refused', reason=type(exc).__name__+': '+str(exc)), ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
