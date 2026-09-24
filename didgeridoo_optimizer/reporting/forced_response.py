"""Strict neutral IO-01 diagnostic exports; no optimizer/fixed-design schema."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import numpy as np

from .export import _to_builtin

SCHEMA = 'dcalc.forced_response.v1'
NOT_EXECUTED = ['optimization', 'ranking', 'pareto', 'robustness', 'nonlinear',
                'scoring', 'peak_extraction', 'calibration', 'far_field', 'played_sound']
UNITS = dict(Zin='Pa.s/m^3', Yin='m^3/(Pa.s)', Hu='1', Yt='m^3/(Pa.s)',
             Zt='Pa.s/m^3', Hp='1', p1='Pa peak', U1='m^3/s peak',
             p2='Pa peak', U2='m^3/s peak', Pin='W', Pload='W', Pdiss='W', eta='1')


def model_payload(transfer, response, elapsed_seconds):
    logs = transfer['log_scale']
    result = dict(model=transfer['model'], model_version=transfer['model_version'],
                propagation_seconds=elapsed_seconds, warnings=transfer['warnings'],
                frequency_hz=transfer['frequency_hz'].tolist(), ka_out=transfer['ka_out'].tolist(),
                load_type=transfer['load_type'], load=_to_builtin(transfer['load']),
                exit_radius_m=transfer['exit_radius_m'], zref_pa_s_m3=transfer['zref'],
                log_scale=[float(x) if np.isfinite(x) else None for x in logs],
                log_scale_status=['ok' if np.isfinite(x) else 'unavailable: propagation overflow' for x in logs],
                transfers={name: curve.payload() for name, curve in transfer['transfers'].items()},
                source=response['source'], ports={name: curve.payload() for name, curve in response['ports'].items()},
                powers=response['powers'])
    if 'radiation' in transfer:
        result['radiation'] = _to_builtin(transfer['radiation'])
    counts = {}
    for group in ('transfers', 'ports', 'powers'):
        for data in result[group].values():
            for status in data['status']:
                counts[status] = counts.get(status, 0)+1
    result['observable_status_counts'] = counts
    result['numerically_complete'] = all(s in {'ok','analytic_zero'} for s in counts)
    return result


def _rows(payload):
    for case in payload['cases']:
        for model in case['models']:
            n = len(model['frequency_hz'])
            observables = {**model['transfers'], **model['ports'], **model['powers']}
            for name, data in observables.items():
                if any(len(values) != n for values in data.values()):
                    raise ValueError(f'Export alignment mismatch: {name}')
            for key in ('ka_out', 'load', 'log_scale', 'log_scale_status'):
                if len(model[key]) != n:
                    raise ValueError(f'Export alignment mismatch: {key}')
            if any(len(v) != n for v in model['source']['amplitude'].values()):
                raise ValueError('Export source alignment mismatch')
            radiation = model.get('radiation')
            if radiation and any(len(radiation[key]) != n for key in ('ka_out', 'model_status', 'model_reason')):
                raise ValueError('Export radiation alignment mismatch')
            for i, frequency in enumerate(model['frequency_hz']):
                amplitude = model['source']['amplitude']['value'][i]
                row = dict(case=case['physical_design']['id'], model=model['model'], frequency_hz=frequency,
                           source_kind=model['source']['kind'], source_units=model['source']['units'],
                           source_real=None if amplitude is None else amplitude['real'],
                           source_imag=None if amplitude is None else amplitude['imag'],
                           ka_out=model['ka_out'][i], load_real_pa_s_m3=model['load'][i]['real'],
                           load_imag_pa_s_m3=model['load'][i]['imag'], log_scale=model['log_scale'][i],
                           log_scale_status=model['log_scale_status'][i])
                # Low-level explicit-load fixtures may have no radiation model.
                coefficients = (radiation or {}).get('coefficients') or {}
                row.update(radiation_model=(radiation or {}).get('name'),
                           radiation_variant=(radiation or {}).get('variant'),
                           radiation_version=(radiation or {}).get('version'),
                           radiation_category=(radiation or {}).get('category'),
                           radiation_source=json.dumps(radiation['source'], ensure_ascii=False) if radiation else None,
                           radiation_n1=coefficients.get('n1'), radiation_d1=coefficients.get('d1'), radiation_d2=coefficients.get('d2'),
                           radiation_radius_m=(radiation or {}).get('radius_m'),
                           radiation_reference_abs_ka_max=(radiation or {}).get('reference_band', {}).get('abs_ka_max'),
                           radiation_assumptions=json.dumps(radiation['assumptions'], ensure_ascii=False) if radiation else None,
                           radiation_termination=json.dumps(radiation.get('termination'), ensure_ascii=False) if radiation else None,
                           radiation_model_status=radiation['model_status'][i] if radiation else 'explicit_load',
                           radiation_model_reason=radiation['model_reason'][i] if radiation else None)
                for name, data in observables.items():
                    value = data['value'][i]
                    if name in model['powers']:
                        row[name] = value
                    else:
                        row[name+'_real'] = None if value is None else value['real']
                        row[name+'_imag'] = None if value is None else value['imag']
                    row[name+'_units'] = UNITS[name]
                    for key in data:
                        if key != 'value':
                            row[name+'_'+key] = data[key][i]
                yield row


def render_bundle(payload):
    """Serialize all three artifacts before creating any output directory."""
    payload = _to_builtin(payload)
    if payload.get('schema') != SCHEMA:
        raise ValueError('Expected dcalc.forced_response.v1')
    js = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)+'\n'
    rows = list(_rows(payload))
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['frequency_hz'])
    writer.writeheader(); writer.writerows(rows)
    lines = ['Réponse entrée-sortie sous excitation acoustique définie',
             'Amplitudes complexes crête ; U positif vers la sortie ; puissances en W.',
             'Transferts chargés du profil entier ; p2 est une pression équivalente de section 1D.',
             'La charge et son domaine de référence sont identifiés par modèle ; consulter ka_out et les hypothèses de montage.',
             'Les statuts et raisons accompagnent chaque observable. Un null ne vaut pas zéro.',
             'eta est un rapport de puissances acoustiques, pas un rendement du joueur.',
             'Le balayage ne définit aucun spectre broadband : les watts ne sont pas sommés.',
             'Aucun son joué, FFT, SPL distant, pression respiratoire statique ou validation expérimentale.',
             'Phases non exécutées : '+', '.join(NOT_EXECUTED)+'.']
    for case in payload['cases']:
        for model in case['models']:
            radiation = model.get('radiation')
            if radiation:
                description = ('asymptote basse fréquence sans seuil de précision établi' if radiation['name'] == 'legacy'
                               else 'fit numérique publié Padé(1,2), pas une mesure ; bande privilégiée |ka|<=2')
                lines.append(f"Radiation {radiation['name']} : {description}. "
                             f"{radiation['model_status'].count('extrapolation')} points en extrapolation.")
                lines.append('Montage : '+'; '.join(radiation['assumptions'])+'.')
                if radiation.get('termination'):
                    lines.append('Sortie physique : '+radiation['termination']['interpretation']+
                                 '; épaisseur et environnement extérieur inconnus. Aucune validation géométrique implicite.')
            else:
                lines.append('Charge explicite du calcul bas niveau ; aucun modèle de radiation attribué.')
            unavailable = sum(status not in {'ok', 'analytic_zero', 'subnormal'}
                              for group in ('transfers', 'ports', 'powers')
                              for data in model[group].values() for status in data['status'])
            lines.append(f"Profil {case['physical_design']['id']}, modèle {model['model']}, "
                         f"{len(model['frequency_hz'])} fréquences, propagation {model['propagation_seconds']:.6g} s, "
                         f"{unavailable} observations indisponibles ou limitées par l'arrondi.")
    return {'forced_response.json': js, 'forced_response.csv': stream.getvalue(),
            'forced_response.txt': '\n'.join(lines)+'\n'}


def export_bundle(payload, output_dir):
    content = render_bundle(payload)
    directory = Path(output_dir)
    paths = [directory/name for name in content]
    if any(path.exists() for path in paths):
        raise FileExistsError('Refusing to overwrite an existing forced-response artifact')
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in content.items():
        with (directory/name).open('x', encoding='utf-8', newline='') as stream:
            stream.write(text)
    return {name: str(directory/name) for name in content}
