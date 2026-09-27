"""Offline diagnostics for forced-response v1/v2 exports; standard library only.

No acoustics, scoring, peak extraction or normalization is performed here.
The CLI loads this file directly to avoid reporting.__init__'s eager imports.
"""
from __future__ import annotations

from collections import Counter
import csv
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path
import stat
import subprocess
import sys

INPUT_SCHEMA = 'dcalc.forced_response.v1'
INPUT_SCHEMA_V2 = 'dcalc.forced_response.v2'
SCHEMA = 'dcalc.forced_response_comparison.v1'
VERSION = '1'
MAX_BYTES = 32 * 1024 * 1024
CONVENTION = 'exp(+j omega t), forward exp(-j k x), U1/U2 toward outlet; peak amplitudes'
UNITS = dict(Zin='Pa.s/m^3', Yin='m^3/(Pa.s)', Hu='1', Yt='m^3/(Pa.s)',
             Zt='Pa.s/m^3', Hp='1', p1='Pa peak', U1='m^3/s peak',
             p2='Pa peak', U2='m^3/s peak', Pin='W', Pload='W', Pdiss='W', eta='1')
COMPLEX_NAMES = ('Zin', 'Hu', 'Yt')
REAL_NAMES = ('Pin', 'Pload', 'Pdiss', 'eta')
SOURCE_UNITS = dict(Psupply='W', Pinternal='W', eta_source='1')
CARTESIAN = {'ok', 'subnormal', 'analytic_zero', 'passivity_violation'}
LOGARITHMIC = {'ok', 'subnormal', 'underflow', 'overflow', 'passivity_violation'}
STATUSES = CARTESIAN | LOGARITHMIC | {'unavailable', 'roundoff_limited'}
ARTIFACTS = ('comparison.json', 'comparison.csv', 'comparison.md')
LIMITS = [
    'Diagnostic linéaire hors ligne : aucune nouvelle simulation ou validation physique.',
    'Même source complexe crête, grille exacte, air effectif, pas demandé, unités et convention.',
    'Aucune interpolation, normalisation, somme de watts, score ou classement meilleur instrument.',
    'Aucune déduction de f0, Q ou déplacement de pics ; aucune FFT jouée ou rendement du joueur.',
    'Les statuts numériques et l’extrapolation physique restent distincts ; null ne vaut pas zéro.',
    'Les coefficients et les montages gardent leurs limites et statuts d’origine.',
    'Les écarts portent sur les valeurs exportées, sans nouvelle estimation de leur incertitude.',
]


def fail(where, message):
    raise ValueError(f'{where}: {message}')


def mapping(value, where):
    if not isinstance(value, dict):
        fail(where, 'objet JSON requis')
    return value


def number(value, where, *, nullable=False, positive=False):
    if value is None and nullable:
        return value
    if type(value) not in (int, float):
        fail(where, 'nombre réel fini requis (bool interdit)')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (positive and value <= 0):
        fail(where, 'nombre réel fini strictement positif requis' if positive else 'nombre hors plage finie')
    return value


def string(value, where, *, nullable=False):
    if value is None and nullable:
        return value
    if not isinstance(value, str) or not value:
        fail(where, 'texte non vide requis')
    return value


def array(value, where, n=None):
    if not isinstance(value, list) or (n is not None and len(value) != n):
        fail(where, f'array alignée requise, longueur {n}')
    return value


def _finite_tree(value):
    if isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)
    elif type(value) in (int, float):
        number(value, 'JSON')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            fail('JSON', f'clé dupliquée {key!r}')
        result[key] = value
    return result


def _constant(value):
    fail('JSON', f'constante non finie interdite : {value}')


def load_export(path):
    """Read at most 32 MiB, fingerprint the actual bytes, validate every case."""
    path = Path(path).resolve()
    if not stat.S_ISREG(path.stat().st_mode):
        fail(str(path), 'fichier régulier requis')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        fail(str(path), 'taille maximale 32 MiB dépassée')
    try:
        payload = json.loads(raw.decode('utf-8'), parse_constant=_constant, object_pairs_hook=_pairs)
        _finite_tree(payload)
        validate_export(payload)
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError(f'{path}: structure v1/v2 incomplète ou invalide ({exc})') from exc
    return dict(payload=payload, file=dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))


def _complex(value, where):
    mapping(value, where)
    if set(value) != {'real', 'imag'}:
        fail(where, 'composantes real/imag requises')
    number(value['real'], where+'.real')
    number(value['imag'], where+'.imag')


def _close(a, b):
    # Serialization/libm consistency only, never an acoustic acceptance tolerance.
    return abs(a-b) <= 64 * sys.float_info.epsilon * max(1., abs(a), abs(b))


def validate_curve(data, n, where, complex_value):
    mapping(data, where)
    required = {'value', 'status', 'reason'} | ({'log_abs', 'phase_rad'} if complex_value else set())
    if not required <= data.keys():
        fail(where, 'champs value/status/reason et représentation manquants')
    for key, values in data.items():
        array(values, where+'.'+key, n)
    for i in range(n):
        point = {key: values[i] for key, values in data.items()}
        status, value = point['status'], point['value']
        if status not in STATUSES:
            fail(where, f'statut inconnu {status!r}')
        string(point['reason'], where+'.reason', nullable=True)
        if status not in {'ok', 'analytic_zero', 'subnormal'} and not point['reason']:
            fail(where, 'statut non résolu sans raison')
        for key in ('log_abs', 'phase_rad', 'sign', 'roundoff_tolerance_w', 'roundoff_tolerance_log_w'):
            if key in point:
                number(point[key], where+'.'+key, nullable=True)
        if 'positive_log_resolved' in point and type(point['positive_log_resolved']) is not bool:
            fail(where, 'positive_log_resolved doit être booléen')
        if value is not None:
            if complex_value:
                _complex(value, where+'.value')
            else:
                number(value, where+'.value')
        if status in CARTESIAN and value is None:
            fail(where, 'valeur manquante pour un statut représentable')
        if status in {'overflow', 'underflow'} and value is not None:
            fail(where, 'projection présente malgré overflow/underflow')
        zero = value == {'real': 0, 'imag': 0} if complex_value else value == 0
        if status == 'analytic_zero':
            if not zero or point.get('log_abs') is not None or point.get('phase_rad') is not None:
                fail(where, 'zéro analytique incohérent')
        if status in {'ok', 'subnormal'} and zero:
            fail(where, 'zéro sans statut analytique ou limite d’arrondi')
        if 'sign' in point:
            sign = point['sign']
            if sign not in (None, -1, 0, 1) or (value is not None and sign != ((value > 0)-(value < 0))):
                fail(where, 'signe incohérent')
        log = point.get('log_abs')
        if status in LOGARITHMIC and ('log_abs' in data) and log is None:
            fail(where, 'logarithme fini requis pour ce statut')
        if complex_value and status in LOGARITHMIC and point['phase_rad'] is None:
            fail(where, 'phase manquante')
        if value is not None and status in {'ok', 'subnormal', 'passivity_violation'}:
            scale = max(abs(value['real']), abs(value['imag'])) if complex_value else abs(value)
            # Subnormal projections are quantized; their retained logs are authoritative.
            if scale >= sys.float_info.min and log is not None:
                expected = (math.log(scale) + math.log(math.hypot(value['real']/scale, value['imag']/scale))
                            if complex_value else math.log(scale))
                if not _close(log, expected):
                    fail(where, 'valeur et log_abs contradictoires')
                if complex_value:
                    phase = math.remainder(point['phase_rad'], math.tau)
                    error = math.remainder(phase-math.atan2(value['imag'], value['real']), math.tau)
                    if not _close(error, 0):
                        fail(where, 'valeur et phase contradictoires')


def validate_design(design, materials, where):
    mapping(design, where)
    string(design['id'], where+'.id')
    segments = array(design['segments'], where+'.segments')
    if not segments:
        fail(where, 'segments absents')
    for segment in segments:
        mapping(segment, where+'.segment')
        string(segment['kind'], where+'.kind')
        for key in ('length_cm', 'd_in_cm', 'd_out_cm'):
            number(segment[key], where+'.'+key, positive=True)
        for key in ('position_start_cm', 'position_end_cm'):
            number(segment[key], where+'.'+key)
        params = mapping(segment['profile_params'], where+'.profile_params')
        for key, value in params.items():
            if key in {'source_kind'}:
                string(value, where+'.profile_params.'+key)
            else:
                number(value, where+'.profile_params.'+key)
        material_id = string(segment['material_id'], where+'.material_id')
        if material_id not in materials:
            fail(where, f'matériau non résolu {material_id}')


def effective_air(payload):
    # Ignore only documented labels/usage; new physical fields remain conservative.
    return {k: v for k, v in payload['effective_parameters']['air'].items()
            if k not in {'identifier', 'provenance', 'thermoviscous_parameters_used'}}


def validate_source_model(model, n, schema):
    """Shared source/schema contract, also used by the producer serializer."""
    mapping(model, 'model')
    source = mapping(model['source'], 'source')
    thevenin = schema == INPUT_SCHEMA_V2
    allowed = {'thevenin_pressure'} if thevenin else {'pressure', 'volume_flow'}
    if source['kind'] not in allowed:
        fail('schema/source.kind', 'type de source contradictoire avec '+schema)
    expected_units = 'm^3/s peak' if source['kind'] == 'volume_flow' else 'Pa peak'
    if source['units'] != expected_units:
        fail('source.units', 'unités incompatibles avec le type')
    validate_curve(source['amplitude'], n, 'source.amplitude', True)
    if any(s not in {'ok', 'subnormal', 'analytic_zero'} for s in source['amplitude']['status']):
        fail('source', 'amplitudes complexes représentables requises')
    if not thevenin:
        if 'impedance' in source or 'impedance_units' in source or 'source_powers' in model:
            fail('schema/source', 'métadonnées Thévenin interdites dans v1')
        return
    if source['impedance_units'] != 'Pa.s/m^3':
        fail('source.impedance_units', 'Pa.s/m^3 requis')
    validate_curve(source['impedance'], n, 'source.impedance', True)
    if any(s not in {'ok', 'subnormal', 'analytic_zero'} for s in source['impedance']['status']):
        fail('source.impedance', 'impédance complexe finie représentable requise')
    if any(v['real'] < 0 for v in source['impedance']['value']):
        fail('source.impedance', 'Re(Zs)>=0 requis')
    powers = mapping(model['source_powers'], 'source_powers')
    if set(powers) != set(SOURCE_UNITS):
        fail('source_powers', 'Psupply/Pinternal/eta_source requis')
    for name, curve in powers.items():
        validate_curve(curve, n, 'source_powers.'+name, False)
        required = {'log_abs'} if name == 'eta_source' else {'log_abs', 'sign', 'roundoff_tolerance_w', 'roundoff_tolerance_log_w'}
        if name == 'Psupply':
            required.add('positive_log_resolved')
        if not required <= curve.keys():
            fail('source_powers.'+name, 'champs numériques manquants')


def validate_export(payload):
    try:
        _validate_export(payload)
    except (KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise ValueError(f'structure v1/v2 incomplète ou invalide ({exc})') from exc


def _validate_export(payload):
    mapping(payload, 'export')
    if payload['schema'] not in (INPUT_SCHEMA, INPUT_SCHEMA_V2):
        fail('schema', f'attendu {INPUT_SCHEMA} ou {INPUT_SCHEMA_V2}')
    units = UNITS | SOURCE_UNITS if payload['schema'] == INPUT_SCHEMA_V2 else UNITS
    if payload['units'] != units or payload['convention'] != CONVENTION:
        fail('units/convention', 'unités ou convention v1 non reconnues')
    effective = mapping(payload['effective_parameters'], 'effective_parameters')
    if 'radiation' in effective:
        mapping(effective['radiation'], 'effective_parameters.radiation')
    number(effective['h_cm'], 'h_cm', positive=True)
    air = mapping(effective['air'], 'air')
    for key in ('rho', 'c'):
        number(air[key], 'air.'+key, positive=True)
    for key, value in effective_air(payload).items():
        number(value, 'air.'+key)
    for key in ('mu', 'kappa', 'cp', 'gamma'):
        if key in air:
            number(air[key], 'air.'+key, positive=True)
    materials = mapping(payload['materials_used'], 'materials_used')
    for key, material in materials.items():
        if material['id'] != key:
            fail('materials_used', 'id incompatible avec sa clé')
        acoustic = mapping(material['acoustic_model'], 'acoustic_model')
        for field in ('beta', 'porosity_leak', 'wall_loss'):
            for suffix in ('nominal', 'min', 'max'):
                number(acoustic[field+'_'+suffix], 'acoustic_model.'+field+'_'+suffix)
    cases = array(payload['cases'], 'cases')
    if not cases:
        fail('cases', 'aucun cas disponible')
    ids = set()
    for case in cases:
        for key in ('physical_design', 'analysis_design'):
            validate_design(case[key], materials, key)
        case_id = case['physical_design']['id']
        if case_id in ids:
            fail('cases', f'id dupliqué {case_id}')
        ids.add(case_id)
        models = array(case['models'], 'models')
        if not models:
            fail(case_id, 'aucun modèle disponible')
        names = set()
        for model in models:
            name = string(model['model'], 'model')
            string(model['model_version'], 'model_version')
            if name in names:
                fail(case_id, f'modèle dupliqué {name}')
            names.add(name)
            f = array(model['frequency_hz'], 'frequency_hz')
            if not 1 <= len(f) <= 20000:
                fail('frequency_hz', '1 à 20000 points requis')
            for i, frequency in enumerate(f):
                number(frequency, 'frequency_hz', positive=True)
                if i and frequency <= f[i-1]:
                    fail('frequency_hz', 'grille strictement croissante requise')
            if (type(effective['n_points']) is not int or effective['n_points'] != len(f)
                    or number(effective['f_min_hz'], 'f_min_hz') != f[0]
                    or number(effective['f_max_hz'], 'f_max_hz') != f[-1]):
                fail('frequency_hz', 'grille incohérente avec effective_parameters')
            n = len(f)
            for key in ('ka_out', 'load', 'log_scale', 'log_scale_status'):
                array(model[key], key, n)
            for value in model['load']:
                _complex(value, 'load')
            for key in ('ka_out', 'log_scale'):
                for value in model[key]:
                    number(value, key, nullable=(key == 'log_scale'))
            for value, status in zip(model['log_scale'], model['log_scale_status']):
                string(status, 'log_scale_status')
                if (status == 'ok') != (value is not None):
                    fail('log_scale', 'valeur et statut contradictoires')
            for key in ('exit_radius_m', 'zref_pa_s_m3'):
                number(model[key], key, positive=True)
            if name == 'zwikker_kosten_circular':
                for key in ('mu','kappa','cp','gamma'):
                    number(air[key], 'air.'+key, positive=True)
            validate_source_model(model, n, payload['schema'])
            for group, expected_names in [('transfers', set(UNITS)-set(REAL_NAMES)-{'p1','p2','U1','U2'}),
                                          ('ports', {'p1','p2','U1','U2'}), ('powers', set(REAL_NAMES))]:
                if set(mapping(model[group], group)) != expected_names:
                    fail(group, 'observables v1 manquantes ou inconnues')
                for key, data in model[group].items():
                    mapping(data, group+'.'+key)
                    if group == 'powers':
                        required = {'Pin': {'log_abs','sign','roundoff_tolerance_w','positive_log_resolved'},
                                    'Pload': {'log_abs','sign','roundoff_tolerance_w'},
                                    'Pdiss': {'roundoff_tolerance_w'}, 'eta': {'log_abs'}}[key]
                        if not required <= data.keys():
                            fail(group+'.'+key, 'champs numériques v1 manquants')
                    validate_curve(data, n, group+'.'+key, group != 'powers')
            radiation = model.get('radiation')
            if radiation is not None:
                mapping(radiation, 'radiation')
                for key in ('name', 'version', 'variant'):
                    string(radiation[key], 'radiation.'+key)
                for key in ('ka_out', 'model_status', 'model_reason'):
                    array(radiation[key], 'radiation.'+key, n)
                for value in radiation['ka_out']:
                    number(value, 'radiation.ka_out')
                coefficients = radiation.get('coefficients')
                if coefficients is not None:
                    mapping(coefficients, 'radiation.coefficients')
                    for key, value in coefficients.items():
                        number(value, 'radiation.coefficients.'+key)
                band = mapping(radiation.get('reference_band', {}), 'radiation.reference_band')
                if 'abs_ka_max' in band:
                    number(band['abs_ka_max'], 'radiation.reference_band', nullable=True, positive=True)
                if radiation['ka_out'] != model['ka_out']:
                    fail('radiation', 'ka_out incohérent')
                for status, reason in zip(radiation['model_status'], radiation['model_reason']):
                    string(status, 'radiation.model_status')
                    string(reason, 'radiation.model_reason', nullable=True)
                    if status == 'extrapolation' and not reason:
                        fail('radiation', 'extrapolation sans raison')
                for key in ('radius_m', 'normalization_radius_m'):
                    number(radiation[key], 'radiation.'+key, positive=True)
                if radiation['radius_m'] != model['exit_radius_m']:
                    fail('radiation', 'rayon physique incohérent')
                if 'radiation' in effective and any(radiation.get(k) != v for k,v in effective['radiation'].items()):
                    fail('radiation', 'descriptions effective/modèle contradictoires')
            elif 'radiation' in effective:
                fail('radiation', 'modèle absent malgré une sélection effective')


def select(payload, case_id=None, model_name=None, label='input'):
    choices = [(c, m) for c in payload['cases'] for m in c['models']]
    matches = [(c, m) for c, m in choices if (case_id is None or c['physical_design']['id'] == case_id)
               and (model_name is None or m['model'] == model_name)]
    if len(matches) != 1:
        available = ', '.join(f"{c['physical_design']['id']} / {m['model']}" for c, m in choices)
        fail(label, f'sélection {"ambiguë" if matches else "introuvable"}; choisir exactement un couple cas/modèle. '
             f'Choix disponibles : {available}')
    return matches[0]


def unavailable(reason):
    return dict(value=None, status='unavailable', reason=reason)


def metric(value):
    if isinstance(value, Fraction):
        try:
            projected = float(value)
        except OverflowError:
            return unavailable('derived_overflow: résultat hors plage binary64')
        if projected == 0 and value != 0:
            return unavailable('derived_underflow: résultat non nul non représentable')
        value = projected
    if not math.isfinite(value):
        return unavailable('derived_nonfinite: opération hors plage finie')
    return dict(value=value, status='subnormal' if 0 < abs(value) < sys.float_info.min else 'ok', reason=None)


def _subnormal(point):
    value = point['value']
    components = value.values() if isinstance(value, dict) else [value]
    return point['status'] == 'subnormal' or any(
        v is not None and 0 < abs(v) < sys.float_info.min for v in components)


def _snapshot(curve, i):
    return {k: v[i] for k, v in curve.items()}


def _blocked(a, b, allowed):
    problems = [f"{label}: {p['status']} ({p['reason']})" for label,p in [('baseline',a),('candidate',b)]
                if p['status'] not in allowed]
    return '; '.join(problems)


def compare_complex(a, b):
    result = {}
    blocked = _blocked(a, b, CARTESIAN)
    if blocked:
        for key in ('delta_real', 'delta_imag', 'relative_real', 'relative_imag'):
            result[key] = unavailable(blocked)
    else:
        # Exact rational intermediates avoid both spurious overflow and lost
        # small components in complex division. Only final projections round.
        ar, ai = (Fraction(a['value'][k]) for k in ('real', 'imag'))
        br, bi = (Fraction(b['value'][k]) for k in ('real', 'imag'))
        dr, di = br-ar, bi-ai
        result.update(delta_real=metric(dr), delta_imag=metric(di))
        denominator = ar*ar + ai*ai
        if not denominator:
            result.update({k: unavailable('baseline_zero: quotient par zéro analytique')
                           for k in ('relative_real', 'relative_imag')})
        else:
            result.update(relative_real=metric((dr*ar+di*ai)/denominator),
                          relative_imag=metric((di*ar-dr*ai)/denominator))
    blocked = _blocked(a, b, LOGARITHMIC)
    if blocked:
        reason = 'analytic_zero: rapport de modules ou phase non définis' if any(
            p['status'] == 'analytic_zero' for p in (a,b)) else blocked
        result.update(magnitude_ratio_db=unavailable(reason), phase_delta_rad=unavailable(reason))
    else:
        result['magnitude_ratio_db'] = metric((b['log_abs']-a['log_abs'])*(20/math.log(10)))
        result['phase_delta_rad'] = metric(math.remainder(
            math.remainder(b['phase_rad'], math.tau)-math.remainder(a['phase_rad'], math.tau), math.tau))
    return result


def compare_real(a, b, name):
    blocked = _blocked(a, b, CARTESIAN)
    if blocked:
        result = dict(delta=unavailable(blocked), relative=unavailable(blocked))
    else:
        av, bv = Fraction(a['value']), Fraction(b['value'])
        result = dict(delta=metric(bv-av), relative=metric((bv-av)/av) if av else
                      unavailable('baseline_zero: quotient par zéro analytique'))
    if name in {'eta', 'eta_source'}:
        return result
    logs = []
    for label, point in [('baseline', a), ('candidate', b)]:
        if point['status'] not in LOGARITHMIC or (name in {'Pin', 'Psupply'} and not point.get('positive_log_resolved', False)):
            result['power_ratio_db'] = unavailable(f"{label}: puissance positive résolue requise ({point['status']})")
            return result
        value = point['value']
        sign = point.get('sign') if value is None else ((value > 0)-(value < 0))
        if sign != 1:
            result['power_ratio_db'] = unavailable(f'{label}: puissance non positive ou signe inconnu')
            return result
        log = point.get('log_abs')
        if log is None and value is not None:
            log = math.log(value)
        if log is None:
            result['power_ratio_db'] = unavailable(f'{label}: logarithme indisponible')
            return result
        logs.append(log)
    result['power_ratio_db'] = metric((logs[1]-logs[0])*(10/math.log(10)))
    return result


def _geometry(design):
    return [{k:v for k,v in s.items() if k not in {'material_id', 'metadata', 'id'}} for s in design['segments']]


def _materials(payload, case):
    def assignment(design):
        spans = []
        for segment in design['segments']:
            mat = payload['materials_used'][segment['material_id']]
            entry = {k:v for k,v in mat.items() if k in {'id','acoustic_model','generated_variant'}}
            if spans and spans[-1]['material'] == entry:
                spans[-1]['end_cm'] = segment['position_end_cm']
            else:
                spans.append(dict(material=entry, start_cm=segment['position_start_cm'],
                                  end_cm=segment['position_end_cm']))
        # A homogeneous material stays the same factor when geometry changes.
        return [spans[0]['material']] if len(spans) == 1 else spans
    return {key:assignment(case[key]) for key in ('physical_design','analysis_design')}


def _radiation(model):
    radiation = model.get('radiation')
    return None if radiation is None else {k:v for k,v in radiation.items() if k not in {
        'ka_out','model_status','model_reason','radius_m','normalization_radius_m','termination'}}


def context(loaded, case, model):
    payload = loaded['payload']
    ids = {s['material_id'] for key in ('physical_design','analysis_design') for s in case[key]['segments']}
    return dict(file=loaded['file'], producer_provenance=payload.get('provenance'),
                original_context=payload.get('original_context'), case_id=case['physical_design']['id'],
                nature=case.get('nature'), loss_model=model['model'], loss_model_version=model['model_version'],
                radiation=model.get('radiation'), load_type=model['load_type'],
                physical_design=case['physical_design'], analysis_design=case['analysis_design'],
                materials_used={k:payload['materials_used'][k] for k in sorted(ids)},
                effective_parameters=payload['effective_parameters'], source=model['source'],
                warnings=dict(export=payload.get('warnings'), model=model.get('warnings')),
                assumptions=payload.get('assumptions'), zref_pa_s_m3=model['zref_pa_s_m3'])


def compare_exports(baseline, candidate, *, baseline_case=None, candidate_case=None,
                    baseline_model=None, candidate_model=None, comparator=None):
    a, b = baseline['payload'], candidate['payload']
    validate_export(a); validate_export(b)
    ac, am = select(a, baseline_case, baseline_model, 'baseline')
    bc, bm = select(b, candidate_case, candidate_model, 'candidate')
    if am['source']['kind'] != bm['source']['kind']:
        fail('source_kind', 'familles de sources différentes ; Thévenin Zs=0 reste distinct de la pression idéale')
    thevenin = am['source']['kind'] == 'thevenin_pressure'
    if thevenin and am['source']['impedance']['value'] != bm['source']['impedance']['value']:
        fail('source_impedance', 'Zs diffère : sources Thévenin différentes, aucune normalisation autorisée')
    compatibility = dict(frequency_hz=(am['frequency_hz'], bm['frequency_hz']),
                         air=(effective_air(a), effective_air(b)),
                         h_cm=(a['effective_parameters']['h_cm'], b['effective_parameters']['h_cm']),
                         units=(a['units'], b['units']), convention=(a['convention'], b['convention']),
                         source_kind=(am['source']['kind'], bm['source']['kind']),
                         source_amplitude=(am['source']['amplitude']['value'], bm['source']['amplitude']['value']))
    for key, (left, right) in compatibility.items():
        if left != right:
            fail(key, 'contextes incompatibles ; aucune interpolation ou normalisation autorisée')
    factors = []
    tests = dict(geometry=(_geometry(ac['physical_design']), _geometry(bc['physical_design'])),
                 materials=(_materials(a,ac), _materials(b,bc)),
                 loss_model=((am['model'],am['model_version']), (bm['model'],bm['model_version'])),
                 radiation=(_radiation(am), _radiation(bm)))
    for name, (left, right) in tests.items():
        if left != right:
            factors.append(name)
    mesh_changed = _geometry(ac['analysis_design']) != _geometry(bc['analysis_design'])
    if mesh_changed and 'geometry' not in factors:
        factors.append('analysis_mesh')
    load_changed = am['load'] != bm['load']
    if load_changed and not {'geometry','radiation'} & set(factors):
        factors.append('load')
    if am['zref_pa_s_m3'] != bm['zref_pa_s_m3'] and 'geometry' not in factors:
        factors.append('numerical_reference')
    producer_changed = a.get('provenance') != b.get('provenance')
    if producer_changed:
        factors.append('producer_provenance')
    contexts = dict(baseline=context(baseline,ac,am), candidate=context(candidate,bc,bm))
    points = []
    coverage = {}
    for i, frequency in enumerate(am['frequency_hz']):
        point = dict(index=i, frequency_hz=frequency, radiation={}, observables={})
        for label, model in [('baseline', am), ('candidate', bm)]:
            rad = model.get('radiation')
            point['radiation'][label] = dict(status=rad['model_status'][i] if rad else 'unidentified_explicit_load',
                                             reason=rad['model_reason'][i] if rad else 'Aucun modèle de radiation attribué',
                                             ka_out=model['ka_out'][i], load=model['load'][i])
        for name in (*COMPLEX_NAMES, *REAL_NAMES, *(SOURCE_UNITS if thevenin else ())):
            group = 'transfers' if name in COMPLEX_NAMES else 'source_powers' if name in SOURCE_UNITS else 'powers'
            observable = group+'.'+name if group == 'source_powers' else name
            av, bv = _snapshot(am[group][name],i), _snapshot(bm[group][name],i)
            metrics = compare_complex(av,bv) if name in COMPLEX_NAMES else compare_real(av,bv,name)
            flags = [label+'_subnormal' for label,p in [('baseline',av),('candidate',bv)]
                     if _subnormal(p)]
            point['observables'][observable] = dict(units=(UNITS | SOURCE_UNITS)[name], baseline=av, candidate=bv,
                                             flags=flags, metrics=metrics)
            for key, entry in metrics.items():
                counts = coverage.setdefault(observable+'.'+key, dict(available=0, unavailable=0, subnormal=0, refused_indices=[]))
                if entry['value'] is None:
                    counts['unavailable'] += 1
                    counts['refused_indices'].append(i)
                else:
                    counts['available'] += 1
                    counts['subnormal'] += entry['status'] == 'subnormal'
        points.append(point)
    result = dict(schema=SCHEMA, comparator_provenance=comparator, contexts=contexts,
                convention=CONVENTION, units=(UNITS | {'source_powers.'+k:v for k,v in SOURCE_UNITS.items()} if thevenin else UNITS), changed_factors=factors,
                interpretation='multifactorielle ; aucune causalité isolée' if len(factors)>1 else
                               'écart descriptif conditionnel ; aucune supériorité instrumentale',
                context_changes=dict(analysis_mesh=mesh_changed, load=load_changed,
                                     material_records=contexts['baseline']['materials_used'] != contexts['candidate']['materials_used'],
                                     design_labels_or_metadata=(ac['physical_design']['id'] != bc['physical_design']['id'] or
                                         ac['physical_design'].get('metadata') != bc['physical_design'].get('metadata'))),
                definitions=dict(direction='candidate - baseline', complex_relative='(candidate - baseline) / baseline, complexe',
                                 magnitude_ratio_db='20/ln(10) * (ln|candidate| - ln|baseline|)',
                                 phase_delta_rad='remainder(arg(candidate) - arg(baseline), 2*pi), [-pi, pi]',
                                 power_ratio_db='10/ln(10) * (ln(candidate) - ln(baseline)), puissances positives résolues',
                                 real_relative='(candidate - baseline) / baseline, signé',
                                 eta='différence absolue et relative du rapport acoustique Pload/Pin, sans dB'),
                coverage=coverage, points=points, limits=LIMITS)
    if thevenin:
        result['definitions']['eta_source'] = 'différence absolue et relative de Pload/Psupply, sans dB ni rendement physiologique'
    return result


def source_identity(cli_path):
    """Comparator provenance only; producer claims are never rewritten."""
    module, cli = Path(__file__).resolve(), Path(cli_path).resolve()
    root = module.parents[2]
    sources = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (module,cli)}
    result = dict(version=VERSION, python=sys.version.split()[0], sources_sha256=sources,
                  sha=None, working_tree_dirty=None, origin='unknown')
    try:
        def git(*args):
            return subprocess.run(['git','-C',str(root),*args], check=True, capture_output=True,
                                  text=True, timeout=5).stdout.strip()
        if Path(git('rev-parse','--show-toplevel')).resolve() != root or cli.parent.parent != root:
            raise ValueError('racines des sources différentes')
        paths = [str(p.relative_to(root)) for p in (module,cli)]
        tracked = git('ls-files','--error-unmatch','--',*paths).splitlines()
        if set(tracked) != set(paths):
            raise ValueError('sources non suivies')
        result.update(sha=git('rev-parse','HEAD'), working_tree_dirty=bool(git('status','--porcelain')),
                      origin='Git HEAD du worktree vérifié ; empreintes des sources chargées séparées')
    except (ValueError, OSError, subprocess.SubprocessError):
        result['origin'] = 'unknown: Git ou appartenance des sources indisponible'
    return result


def _flat(prefix, value, output):
    if isinstance(value, dict):
        for key, item in value.items():
            _flat(prefix+'_'+key if prefix else key, item, output)
    elif isinstance(value, list):
        output[prefix] = json.dumps(value, ensure_ascii=False, allow_nan=False)
    else:
        output[prefix] = value


def render_bundle(payload):
    """Serialize JSON/CSV/FR Markdown in full before any output mutation."""
    if payload.get('schema') != SCHEMA:
        fail('schema', 'schéma comparison attendu')
    js = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)+'\n'
    rows, fields = [], {}
    for point in payload['points']:
        for name, data in point['observables'].items():
            row = dict(index=point['index'], frequency_hz=point['frequency_hz'], observable=name)
            _flat('', data, row)
            _flat('radiation', point['radiation'], row)
            fields.update(dict.fromkeys(row))
            rows.append(row)
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(fields))
    writer.writeheader()
    writer.writerows(rows)
    lines = ['# Comparaison hors ligne de réponses acoustiques', '',
             'Sens des écarts : candidat − référence. '+payload['interpretation']+'.', '']
    for label, context_data in payload['contexts'].items():
        rad = context_data['radiation']
        lines += [f"- {label} : cas `{context_data['case_id']}`, pertes `{context_data['loss_model']}`, "
                  f"radiation `{rad['name'] if rad else 'non identifiée'}`.",
                  f"  Fichier : `{context_data['file']['path']}` ; SHA256 `{context_data['file']['sha256']}`.",
                  f"  Source : {context_data['source']['kind']}, {context_data['source']['units']} ; "
                  f"{json.dumps(context_data['source']['amplitude']['value'][0], ensure_ascii=False)} au premier point "
                  '(vecteur complet conservé dans comparison.json).',
                  f"  Air effectif : {json.dumps(context_data['effective_parameters']['air'], ensure_ascii=False)}.",
                  f"  Pas demandé : {context_data['effective_parameters']['h_cm']} cm."]
        if context_data['source']['kind'] == 'thevenin_pressure':
            lines += ['  Zs (Pa.s/m^3) : '+json.dumps(context_data['source']['impedance']['value'], ensure_ascii=False)+'.']
        if rad:
            lines += ['  Hypothèses : '+json.dumps(rad.get('assumptions'), ensure_ascii=False)+'.',
                      '  Montage : '+json.dumps(rad.get('termination'), ensure_ascii=False)+'.']
    lines += ['', 'Facteurs changés : '+(', '.join(payload['changed_factors']) or 'aucun')+'.',
              'Contextes détaillés, géométries, matériaux et provenances séparées dans comparison.json.', '',
              f"Grille : {len(payload['points'])} points, {payload['points'][0]['frequency_hz']:g} à "
              f"{payload['points'][-1]['frequency_hz']:g} Hz, correspondance exacte.", '',
              '| Métrique | Disponibles | Indisponibles | Subnormaux dérivés |', '|---|---:|---:|---:|']
    for name, counts in payload['coverage'].items():
        lines.append(f"| {name} | {counts['available']} | {counts['unavailable']} | {counts['subnormal']} |")
    lines += ['', 'Les listes refused_indices (indices base 0) identifient chaque point refusé ; '
              'CSV/JSON gardent ses valeurs, statuts et raisons. Aucun point exclu du dénominateur.', '']
    for label in ('baseline','candidate'):
        counts = Counter(p['radiation'][label]['status'] for p in payload['points'])
        subnormal = sum(bool(o['flags']) and label+'_subnormal' in o['flags']
                        for p in payload['points'] for o in p['observables'].values())
        lines += [f"{label} : statuts physiques de radiation {dict(counts)} ; {subnormal} observations subnormales."]
    lines += ['', '## Points explicites (sans recherche de pics)', '',
              '| Hz | Observable | Δ réel / scalaire | Δ imag | dB | Δ phase rad |', '|---:|---|---:|---:|---:|---:|']
    frequencies = {70, 1000, 1500, payload['points'][0]['frequency_hz'], payload['points'][-1]['frequency_hz']}
    for p in payload['points']:
        if p['frequency_hz'] not in frequencies:
            continue
        for name, data in p['observables'].items():
            m = data['metrics']
            def cell(key):
                if key not in m:
                    return 'sans objet'
                item = m[key]
                return format(item['value'], '.12g') if item['value'] is not None else 'null ('+item['reason']+')'
            lines.append(f"| {p['frequency_hz']:g} | {name} | {cell('delta_real' if name in COMPLEX_NAMES else 'delta')} | "
                         f"{cell('delta_imag')} | {cell('magnitude_ratio_db' if name in COMPLEX_NAMES else 'power_ratio_db')} | "
                         f"{cell('phase_delta_rad')} |")
    if any('source_powers.Psupply' in p['observables'] for p in payload['points']):
        lines += ['', '| Hz | Puissance source | Unité | Référence : valeur / statut / raison | Candidat : valeur / statut / raison |', '|---:|---|---|---|---|']
        for p in payload['points']:
            for name, data in p['observables'].items():
                if name.startswith('source_powers.'):
                    cells = [json.dumps({k:data[side][k] for k in ('value','status','reason')}, ensure_ascii=False).replace('|', '&#124;') for side in ('baseline','candidate')]
                    lines.append(f"| {p['frequency_hz']:g} | {name} | {data['units']} | {cells[0]} | {cells[1]} |")
    lines += ['', '## Définitions et limites', '']
    lines += ['- '+key+' : '+value for key,value in payload['definitions'].items()]
    lines += ['']+['- '+line for line in payload['limits']]
    return dict(zip(ARTIFACTS, (js, stream.getvalue(), '\n'.join(lines)+'\n')))


def preflight_output(directory):
    directory = Path(directory).absolute()
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        fail('output-dir', 'répertoire réel requis')
    for name in ARTIFACTS:
        path = directory/name
        if path.exists() or path.is_symlink():
            raise FileExistsError(f'Aucun écrasement autorisé : {path}')
    return directory


def write_bundle(content, directory):
    if set(content) != set(ARTIFACTS) or any(not isinstance(v, str) for v in content.values()):
        fail('bundle', 'trois artefacts entièrement sérialisés requis')
    directory = preflight_output(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        # Exclusive creation also protects a path appearing after preflight.
        with (directory/name).open('x', encoding='utf-8', newline='') as stream:
            stream.write(content[name])
    return {name:str(directory/name) for name in ARTIFACTS}
