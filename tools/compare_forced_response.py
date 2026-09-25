"""Comparer deux exports existants, sans calcul acoustique ni classement."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

# reporting.__init__ eagerly imports plotting/ranking. Load the autonomous file
# directly: the offline CLI also works under python -S, without site-packages.
_MODULE = Path(__file__).resolve().parents[1]/'didgeridoo_optimizer/reporting/forced_response_comparison.py'
_SPEC = importlib.util.spec_from_file_location('_dcalc_offline_comparison', _MODULE)
comparison = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(comparison)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    p = Parser(description=__doc__, allow_abbrev=False)
    p.add_argument('--baseline', required=True)
    p.add_argument('--candidate', required=True)
    p.add_argument('--output-dir', required=True)
    for side in ('baseline', 'candidate'):
        p.add_argument('--'+side+'-case')
        p.add_argument('--'+side+'-model', help='Nom exact du loss model dans l’export (pas la radiation)')
    p.add_argument('--dry-run', action='store_true')
    return p


def run(args):
    baseline = comparison.load_export(args.baseline)
    candidate = comparison.load_export(args.candidate)
    result = comparison.compare_exports(
        baseline, candidate, baseline_case=args.baseline_case, candidate_case=args.candidate_case,
        baseline_model=args.baseline_model, candidate_model=args.candidate_model,
        comparator=comparison.source_identity(__file__))
    content = comparison.render_bundle(result)
    output = comparison.preflight_output(args.output_dir)
    exports = {} if args.dry_run else comparison.write_bundle(content, output)
    return dict(ok=True, dry_run=args.dry_run, schema=comparison.SCHEMA, exports=exports,
                points=len(result['points']), changed_factors=result['changed_factors'],
                coverage={k:{field:value for field,value in counts.items() if field != 'refused_indices'}
                          for k,counts in result['coverage'].items()},
                comparator_provenance=result['comparator_provenance'])


def main(argv=None):
    try:
        result = run(parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, ArithmeticError, RecursionError) as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False, allow_nan=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
