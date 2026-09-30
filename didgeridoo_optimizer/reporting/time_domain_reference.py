"""Explicit numerical statuses, also retained for partial TD-PASS runs."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import sys

from .nonlinear_onset import source_fingerprints

STATUS_KEYS = ('passivity', 'fitter', 'fidelity', 'mesh', 'physical')
LIMITS = [
    'Experimental numerical signals, not measured or validated played sound.',
    'Pressure is the midpoint port; coefficients approximate impedance, not materials or lips.',
    'Historical V2 RK4/previous-pressure schedule is not an energy-coupled scheme.',
    'Passivity, NNLS convergence, complex fidelity, mesh convergence and physical validation are separate.',
    'R0 is a linearized acoustic resistance, not a finite mean-flow pressure law.',
    'No scores, ranking, material promotion or A-E validation.',
    'Modal reference: continuous 3996.671891607 Pa; historical schedule 4062.676894137 Pa at 12 kHz and 4198.524055211 Pa at 4 kHz; no correction applied.',
]


def sources():
    """Reuse the strict loaded-package guard, then include actual loaded tools."""
    root = Path(__file__).resolve().parents[2]
    result = source_fingerprints()
    paths = {root/'tools/time_domain_reference.py'}
    for name, module in tuple(sys.modules.items()):
        if name.startswith('tools.'):
            raw = getattr(module, '__file__', None)
            if raw is None:
                raise ValueError('Loaded tool source unavailable')
            paths.add(Path(raw).resolve())
    for path in paths:
        if not path.is_relative_to(root) or path.suffix != '.py' or not path.is_file():
            raise ValueError('Mixed tool source roots')
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def statuses():
    return dict(passivity='not_constructed', fitter='not_started', fidelity='not_accepted',
                mesh='not_checked', physical='not_validated')


def write_json(path, value, *, replace=False):
    path = Path(path)
    content = json.dumps(value, allow_nan=False, indent=2)+'\n'
    if replace:
        temporary = path.with_name('.'+path.name+'.tmp')
        with temporary.open('x', encoding='utf8') as stream:
            stream.write(content)
        temporary.replace(path)
    else:
        with path.open('x', encoding='utf8') as stream:
            stream.write(content)


def checkpoint(output, payload, stage):
    payload['stage'] = stage
    write_json(Path(output)/'partial.json', payload, replace=True)


def export(output, payload):
    output = Path(output)
    write_json(output/'reference.json', payload)
    buf = io.StringIO(newline='')
    fields = ['status', 'stage', *STATUS_KEYS, 'complex_nrmse', 'relative_max', 'phase_rms_deg', 'error']
    writer = csv.DictWriter(buf, fields)
    writer.writeheader()
    values = payload.get('audit', {}).get('metrics', {})
    writer.writerow(dict(status=payload['status'], stage=payload.get('stage'), **payload['statuses'],
        **{key: values.get(key) for key in ('complex_nrmse','relative_max','phase_rms_deg')}, error=payload.get('error')))
    with (output/'reference.csv').open('x', encoding='utf8', newline='') as stream:
        stream.write(buf.getvalue())
    text = ['TD-PASS-01 experimental reference', 'Status: '+payload['status']]
    text += [key+': '+payload['statuses'][key] for key in STATUS_KEYS]
    text += ['']+LIMITS
    if payload.get('error'):
        text.append('Interruption/failure: '+payload['error'])
    with (output/'summary.txt').open('x', encoding='utf8') as stream:
        stream.write('\n'.join(text)+'\n')
