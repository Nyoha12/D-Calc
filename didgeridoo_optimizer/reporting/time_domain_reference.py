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
    'R29 completion terms are numerical out-of-band bases, not observed peaks or acquired guard-to-Nyquist data.',
    'The chosen 70 Hz drive may be outside the fit band; impulse and start/stop transients are not bandlimited. No out-of-band fidelity is validated.',
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
    fields = ['status', 'stage', *STATUS_KEYS, 'basis_completion_mode', 'basis_completion_json',
              'complex_nrmse', 'relative_max', 'phase_rms_deg', 'error']
    writer = csv.DictWriter(buf, fields)
    writer.writeheader()
    values = payload.get('audit', {}).get('metrics', {})
    completion = payload.get('basis_completion')
    writer.writerow(dict(status=payload['status'], stage=payload.get('stage'), **payload['statuses'],
        basis_completion_mode=completion.get('mode') if completion else None,
        basis_completion_json=json.dumps(completion,allow_nan=False,sort_keys=True),
        **{key: values.get(key) for key in ('complex_nrmse','relative_max','phase_rms_deg')}, error=payload.get('error')))
    with (output/'reference.csv').open('x', encoding='utf8', newline='') as stream:
        stream.write(buf.getvalue())
    text = ['TD-PASS-01 — référence numérique expérimentale', 'Statut : '+payload['status'],
            'Statuts indépendants (identiques aux JSON/CSV) :']
    text += [key+': '+payload['statuses'][key] for key in STATUS_KEYS]
    if completion:
        text += ['', 'Base demandée/effective : '+completion['mode'],
                 'Domaine des coefficients : '+completion['domain'],
                 'Transformation : '+completion['transformation'],
                 'fmax du fit : '+str(completion['fit_max_hz'])+' Hz ; fs : '+str(completion['sample_rate_hz'])+' Hz.']
        for term in completion['terms']:
            text.append('Complétion numérique : f='+format(term['frequency_hz'],'.12g')+' Hz ; omega='+str(term['omega'])+
                        ' rad/s ; gamma='+str(term['gamma'])+' 1/s (gamma/omega=0.08).')
        text.append('Ces bases représentent une contribution hors bande ; ce ne sont ni des pics observés, ni des mesures, pertes physiques ou coefficients matériaux.')
    if values:
        text += ['', 'Audit réservé : NRMSE complexe='+str(values.get('complex_nrmse'))+
                 ' ; maximum relatif='+str(values.get('relative_max'))+
                 ' ; phase RMS='+str(values.get('phase_rms_deg'))+' degrés.']
    forced = payload.get('forced')
    if forced:
        text += ['', 'Débit prescrit : fréquence choisie '+str(forced['frequency_hz'])+' Hz ; dans la bande de fit : '+
                 ('oui' if forced['sinusoid_within_fit_band'] else 'non')+'.',
                 'Pression passive au port milieu ; pression FIR au port natif FIR.']
    for backend, value in payload.get('v2', {}).items():
        if not isinstance(value, dict):
            continue
        text += ['', 'V2 '+backend+' : pression demandée/effective '+str(value['pressure_pa'])+' Pa ; fs effectif '+str(value['sample_rate_hz'])+' Hz.',
                 'Durée demandée '+str(value['requested_duration_s'])+' s ; effective '+str(value['duration_s'])+' s ; port '+value['pressure_port']+'.',
                 'Paramètres V2 effectifs : '+json.dumps(value['parameters'],ensure_ascii=False,sort_keys=True),
                 'Non-régularités : '+json.dumps([b.get('non_regular_reasons',[]) for b in value['equilibrium']['branches']],ensure_ascii=False)]
    text += ['', 'Limites : impulsion et transitoires de démarrage/arrêt non bandelimités ; aucune fidélité validée hors bande.',
             'La passivité du sous-système et la convergence NNLS ne prouvent ni stabilité globale du couplage V2, ni son joué, ni validation A–E.',
             'Le calendrier V2 historique est conservé. Aucun score, classement ou matériau promu.',
             'R0 reste une résistance acoustique linéarisée, pas une loi de débit moyen fini.']
    if payload.get('error'):
        text.append('Interruption/échec : '+payload['error'])
    with (output/'summary.txt').open('x', encoding='utf8') as stream:
        stream.write('\n'.join(text)+'\n')
