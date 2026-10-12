"""CLI for finite nominal catalogues; successful dry-runs are silent."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

ENTRY = Path(__file__).resolve()
ENTRY_SHA256 = hashlib.sha256(ENTRY.read_bytes()).hexdigest()
_LOADED_SOURCE_SHA256 = ENTRY_SHA256


def main(argv=None):
    parser = argparse.ArgumentParser(description="Catalogue nominal robuste fini")
    parser.add_argument("--job", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--read", action="store_true")
    args = parser.parse_args(argv)
    try:
        from didgeridoo_optimizer.optimization.catalogue_robust import ROOT
        if (ENTRY != ROOT / "tools/catalogue_robust.py"
                or hashlib.sha256(ENTRY.read_bytes()).hexdigest() != ENTRY_SHA256):
            raise ValueError("provenance CLI refusée: entrée copiée/modifiée")
        if args.read and args.dry_run:
            raise ValueError("--read et --dry-run sont exclusifs")
        if args.read:
            from didgeridoo_optimizer.reporting.catalogue_robust import read_result
            result = read_result(args.output_dir, args.job)
        else:
            from didgeridoo_optimizer.pipeline.catalogue_robust import run
            result = run(args.job, args.output_dir, dry_run=args.dry_run)
        if not args.dry_run:
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result["ok"] else 1
    except Exception as exc:
        print(json.dumps(dict(
            ok=False, status="invalid_request" if isinstance(exc, ValueError) else "failed",
            reason=str(exc)[:2000]), ensure_ascii=False, allow_nan=False),
            file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
