"""EQUAL-PITCH-01: bounded equal first-|Zin|-peak diagnostic, no scoring.

Linux execution uses sequential children (768 MiB address space, <=180 s wall).
Run from the checkout with python -B -m tools.equal_pitch_study.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import io
import json
import math
import os
from pathlib import Path
try:
    import resource
except ImportError:  # Keep helpers, dry-run and test collection usable on Windows.
    resource = None
import subprocess
import sys
import time

import numpy as np

from didgeridoo_optimizer.acoustics import forced_response as fr
from didgeridoo_optimizer.acoustics.source_impedance import apply_thevenin_source
from didgeridoo_optimizer.acoustics.thermoviscous import CK_DRY_20C, ZwikkerKostenLossModel
from didgeridoo_optimizer.acoustics.transfer_matrix import input_impedance
from didgeridoo_optimizer.acoustics.radiation_models import get_radiation_model
from didgeridoo_optimizer.geometry.builders import DesignBuilder
from didgeridoo_optimizer.geometry.discretization import GeometryDiscretizer
from didgeridoo_optimizer.reporting.forced_response import model_payload, UNITS, SOURCE_UNITS
from tools.forced_response_compare import PROFILES, builtin_design, synthetic_material, termination_assumptions
from tools.thermo_reference_compare import extract_modes, mode_metrics, _local_maxima

ROOT = Path(__file__).resolve().parents[1]
NAMES = tuple(PROFILES)
SURVEY = np.linspace(10., 700., 691)
AIR = CK_DRY_20C.as_air_properties()
ZREF_COMMON = AIR.rho * AIR.c / (math.pi * .015**2)
TOLERANCE_HZ = .002
TOTAL_SECONDS = 420.
SOURCE_NAMES = ('pressure', 'volume_flow', 'thevenin_R0', 'thevenin_Rref', 'thevenin_R10ref')
LIMITS = [
    'Protocol and derivation: inferred. Empirical code and player parameters remain to_calibrate.',
    'Conditional geometry plus tuning comparison; not an experiment isolating one bulge.',
    'First three cases share 3 cm inlet/outlet; body_bell is a distinct geometry group.',
    'ZK represents rigid smooth sealed walls; material effects are omitted, not measured zero.',
    'Silva unflanged cylindrical termination is transposed to body_bell; mounting not validated.',
    'Finite 10..700 Hz surveys and refinements bound mode identification, not a proof at arbitrary resolution.',
    'Numerical study criteria are not physical validation, physiological efficiency or causal claims.',
    'Common harmonic points describe a hypothetical excitation; played spectrum is unknown.',
    'No power sum, score, ranking, calibration, coefficient adjustment or material promotion.',
]


class StudyError(ValueError):
    def __init__(self, message, history=None):
        super().__init__(message)
        self.history = history or []


def positive(value, name):
    return fr.real_positive(value, name)


def target_value(value):
    value = positive(value, 'target_hz')
    if not 50 <= value <= 90:
        raise ValueError('target_hz must be in [50, 90] Hz')
    return value


def scale_design(design, factor):
    factor = positive(factor, 'longitudinal factor')
    data = design.as_dict()
    for segment in data['segments']:
        segment['length_cm'] *= factor
        segment.pop('position_start_cm', None)
        segment.pop('position_end_cm', None)
    data['metadata'] = dict(data.get('metadata', {}), longitudinal_factor=factor)
    return DesignBuilder().build(data)


def solve_factor(evaluate, target, bracket, *, tolerance=TOLERANCE_HZ, max_iterations=16):
    """Safeguarded secant/bisection on measured frequency, never on 1/L.

    max_iterations counts interior evaluations; endpoints are also recorded.
    The callable owns modal identification and must explicitly reject ambiguity.
    """
    target = positive(target, 'target')
    tolerance = positive(tolerance, 'tolerance')
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or not 1 <= max_iterations <= 64:
        raise ValueError('max_iterations must be an integer in [1, 64]')
    if len(bracket) != 2:
        raise ValueError('Two bracket endpoints required')
    lo, hi = (positive(x, 'bracket factor') for x in bracket)
    if not lo < hi:
        raise ValueError('Require increasing positive bracket')
    history = []

    def sample(factor):
        try:
            frequency = positive(evaluate(factor), 'measured frequency')
        except (ValueError, ArithmeticError) as exc:
            history.append(dict(factor=factor, frequency_hz=None, error_hz=None, reason=str(exc)))
            raise StudyError(str(exc), history) from exc
        error = frequency-target
        history.append(dict(factor=factor, frequency_hz=frequency, error_hz=error))
        return error

    a = sample(lo)
    if abs(a) <= tolerance:
        return dict(factor=lo, frequency_hz=a+target, error_hz=a, history=history)
    b = sample(hi)
    if abs(b) <= tolerance:
        return dict(factor=hi, frequency_hz=b+target, error_hz=b, history=history)
    if a*b >= 0:
        raise StudyError('No sign-changing frequency bracket', history)
    for _ in range(max_iterations):
        x = hi-b*(hi-lo)/(b-a)
        # Safeguard against endpoint stagnation while retaining rapid secants.
        if not lo+.02*(hi-lo) < x < hi-.02*(hi-lo):
            x = (lo+hi)/2
        error = sample(x)
        if abs(error) <= tolerance:
            return dict(factor=x, frequency_hz=error+target, error_hz=error, history=history)
        if a*error > 0:
            lo, a = x, error
        else:
            hi, b = x, error
    raise StudyError('Maximum scalar iterations reached without target residual', history)


def evaluator(design, h_cm):
    mesh = GeometryDiscretizer().discretize(design, h_cm)
    material = synthetic_material()
    model = ZwikkerKostenLossModel(CK_DRY_20C)
    radiation = get_radiation_model('silva_unflanged')
    radius = design.segments[-1].d_out_cm/200

    def evaluate(frequencies):
        z = input_impedance(frequencies, mesh, {material.id: material}, AIR,
                            exit_radius_m=radius, loss_model=model, radiation_model=radiation)
        if not np.all(np.isfinite(z)):
            raise StudyError('Nonfinite impedance')
        return z
    return evaluate, mesh


def first_peak(evaluate):
    """Survey first candidate and require exactly one maximum in its local cell."""
    z = evaluate(SURVEY)
    peaks = _local_maxima(abs(z))
    if not len(peaks):
        raise StudyError('No first magnitude maximum in 10..700 Hz')
    index = int(peaks[0])
    left, right = SURVEY[index-1], SURVEY[index+1]
    levels = []
    for count in (81, 161):
        grid = np.linspace(left, right, count)
        local_z = evaluate(grid)
        candidates = _local_maxima(abs(local_z))
        if len(candidates) != 1:
            raise StudyError('Missing or ambiguous first local maximum')
        metrics = mode_metrics(grid, local_z)
        if metrics is None or not left < metrics['frequency_max_abs_hz'] < right:
            raise StudyError('First local peak left its tracking interval')
        levels.append(dict(frequency_hz=metrics['frequency_max_abs_hz'], step_hz=float(grid[1]-grid[0])))
    delta = levels[-1]['frequency_hz']-levels[0]['frequency_hz']
    if abs(delta) > .0005:
        raise StudyError('First peak frequency refinement exceeds 0.0005 Hz')
    return dict(frequency_hz=levels[-1]['frequency_hz'], survey_peak_count=len(peaks),
                first_candidate_hz=float(SURVEY[index]), tracking_interval_hz=[float(left), float(right)],
                frequency_refinement=levels, delta_frequency_hz=delta)


def tune(name, target):
    design = builtin_design(name)
    measurements = []

    def measure(factor):
        evaluate, mesh = evaluator(scale_design(design, factor), .5)
        peak = first_peak(evaluate)
        measurements.append(dict(factor=factor, error_hz=peak['frequency_hz']-target,
                                 slices=len(mesh.segments), **peak))
        return peak['frequency_hz']

    baseline = measure(1.)
    guess = baseline/target  # Initialization only; all accepted frequencies measured.
    try:
        result = solve_factor(measure, target, (guess*.88, guess*1.12))
    except StudyError as exc:
        exc.history = measurements + exc.history[-1:]
        raise
    return dict(name=name, h_cm=.5, target_hz=target, initial_inverse_length_guess=guess,
                **result, measurements=measurements,
                physical_design=scale_design(design, result['factor']).as_dict())


def validate(name, factor, target):
    design = scale_design(builtin_design(name), factor)
    levels = []
    for h in (.5, .25):
        evaluate, mesh = evaluator(design, h)
        zin = evaluate(SURVEY)
        modes = extract_modes(evaluate, SURVEY, zin, max_modes=3, refinement_points=(1025, 2049))
        if len(modes) != 3 or any(m['status'] != 'resolved' for m in modes):
            raise StudyError('Three unique frequency-ordered modes not resolved')
        # Separate efficient peak measurement cross-checks the native basin extraction.
        peak = first_peak(evaluate)
        if abs(peak['frequency_hz']-modes[0]['frequency_max_abs_hz']) > .002:
            raise StudyError('First mode extraction disagrees with local peak check')
        levels.append(dict(h_cm=h, slices=len(mesh.segments), modes=modes,
                           survey_frequency_hz=SURVEY.tolist(), survey_zin_real=zin.real.tolist(),
                           survey_zin_imag=zin.imag.tolist(), first_peak_check=peak, residual_hz=modes[0]['frequency_max_abs_hz']-target))
    if abs(levels[0]['residual_hz']) > TOLERANCE_HZ:
        raise StudyError('Final h=.5 extraction exceeds tuning residual')
    deltas = []
    for coarse, fine in zip(levels[0]['modes'], levels[1]['modes'], strict=True):
        q0, q1 = coarse['q_half_power'], fine['q_half_power']
        deltas.append(dict(mode_ordinal=fine['mode_ordinal'],
                           frequency_hz=fine['frequency_max_abs_hz']-coarse['frequency_max_abs_hz'],
                           q_relative=None if q0 is None or q1 is None else q1/q0-1))
    return dict(name=name, factor=factor, fixed_design_no_retune=True, levels=levels,
                mesh_deltas=deltas,
                frequency_deltas=[dict(mode_ordinal=m['mode_ordinal'],
                    delta_hz=m['frequency_refinement'][1]['frequency_max_abs_hz']-m['frequency_refinement'][0]['frequency_max_abs_hz'],
                    q_relative=(None if any(l['q_half_power'] is None for l in m['frequency_refinement']) else
                                m['frequency_refinement'][1]['q_half_power']/m['frequency_refinement'][0]['q_half_power']-1))
                    for m in levels[-1]['modes']])


def frequency_points(target, own_peak):
    # Propagation requires strictly increasing frequencies. If own peak equals a
    # common point, reuse that sample; source_rows emits both semantic groups.
    points = {target*i: 'common_harmonic' for i in range(1, 8)}
    points.update({target+d: 'common_detuning' for d in (-5, -1, 1, 5)})
    points.setdefault(own_peak, 'own_peak')
    frequencies = sorted(points)
    return frequencies, [points[f] for f in frequencies]


def apply_sources(transfer):
    return dict(pressure=fr.apply_source(transfer, 'pressure', 1.),
                volume_flow=fr.apply_source(transfer, 'volume_flow', 1e-6),
                thevenin_R0=apply_thevenin_source(transfer, 1., 0.),
                thevenin_Rref=apply_thevenin_source(transfer, 1., ZREF_COMMON),
                thevenin_R10ref=apply_thevenin_source(transfer, 1., 10*ZREF_COMMON))


def sources(name, factor, target, own_peak):
    design = scale_design(builtin_design(name), factor)
    freq, groups = frequency_points(target, own_peak)
    mesh = fr.prepare_mesh(design, .25, len(freq))
    material = synthetic_material()
    started = time.monotonic()
    transfer = fr.loaded_transfer(np.array(freq), mesh, {material.id: material}, AIR,
        exit_radius_m=design.segments[-1].d_out_cm/200,
        loss_model=ZwikkerKostenLossModel(CK_DRY_20C),
        radiation_model=get_radiation_model('silva_unflanged'), zref=ZREF_COMMON)
    elapsed = time.monotonic()-started
    transfer['radiation']['termination'] = termination_assumptions(design)
    responses = {key: model_payload(transfer, response, elapsed)
                 for key, response in apply_sources(transfer).items()}
    # Direct agreement of independent source closures, at each identical frequency.
    errors = {}
    for key in ('Pin', 'Pload', 'Pdiss', 'eta'):
        a = responses['pressure']['powers'][key]['value']
        b = responses['thevenin_R0']['powers'][key]['value']
        errors[key] = max(abs(x-y)/max(abs(x), abs(y), 1e-300)
                          for x, y in zip(a, b, strict=True) if x is not None and y is not None)
    if max(errors.values()) > 1e-8:
        raise StudyError('Rs=0 / ideal pressure mismatch')
    return dict(name=name, factor=factor, h_cm=.25, frequency_groups=groups,
                transfers_computed=1, own_peak_frequency_hz=own_peak, zref_common_pa_s_m3=ZREF_COMMON,
                rs0_pressure_relative_errors=errors, responses=responses)


def source_rows(source):
    for label, model in source['responses'].items():
        for i, frequency in enumerate(model['frequency_hz']):
            for group in ('transfers', 'ports', 'powers', 'source_powers'):
                for observable, curve in model.get(group, {}).items():
                    row = dict(case=source['name'], geometry_group=('distinct_body_bell' if source['name']=='body_bell' else 'common_3cm_ports'),
                        source=label, frequency_group=source['frequency_groups'][i], frequency_hz=frequency,
                        observable=observable, unit=(UNITS | SOURCE_UNITS)[observable])
                    point = {key: values[i] for key, values in curve.items()}
                    value = point.pop('value')
                    row.update(value=None if isinstance(value, dict) else value,
                               real=value.get('real') if isinstance(value, dict) else None,
                               imag=value.get('imag') if isinstance(value, dict) else None, **point)
                    yield row
                    if frequency == source['own_peak_frequency_hz'] and row['frequency_group'] != 'own_peak':
                        yield dict(row, frequency_group='own_peak')


def ratios_against_cylinder(rows):
    """Compare only matched common frequencies, never different own peaks."""
    keys = ('source', 'frequency_group', 'frequency_hz', 'observable')
    baseline = {tuple(row[k] for k in keys): row for row in rows
                if row['case']=='cylinder' and row['frequency_group']!='own_peak'}
    ratios = []
    for row in rows:
        if row['frequency_group']=='own_peak':
            continue
        ref = baseline.get(tuple(row[k] for k in keys))
        result = {k: row[k] for k in ('case', 'geometry_group', *keys)}
        value, status, reason = None, 'unavailable', 'Missing or unresolved cylinder/reference observable'
        if ref and row['status'] in {'ok', 'analytic_zero'} and ref['status'] in {'ok', 'analytic_zero'}:
            a, b = row['value'], ref['value']
            kind = 'signed_scalar_ratio'
            if row['real'] is not None and ref['real'] is not None:
                a, b = math.hypot(row['real'], row['imag']), math.hypot(ref['real'], ref['imag'])
                kind = 'magnitude_ratio'
            if a is not None and b is not None and b != 0:
                candidate = a/b
                if math.isfinite(candidate):
                    value, status, reason = candidate, 'ok', None
            else:
                reason = 'Zero or unavailable denominator/value; no ratio'
        else:
            kind = 'unavailable'
        ratios.append(dict(**result, ratio=value, ratio_kind=kind, status=status, reason=reason))
    return ratios


def provenance():
    # Fingerprint the actual imported source tree, including uncommitted tool bytes.
    files = sorted((ROOT/'didgeridoo_optimizer').rglob('*.py'))
    files += [ROOT/'tools'/name for name in ('equal_pitch_study.py', 'forced_response_compare.py', 'thermo_reference_compare.py')]
    fingerprints = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    def git(*args):
        return subprocess.run(['git', '-C', str(ROOT), *args], check=True, capture_output=True,
                              text=True, timeout=5).stdout.strip()
    return dict(head_sha=git('rev-parse', 'HEAD'), origin_main_sha=git('rev-parse', 'origin/main'),
                worktree=str(ROOT), status=git('status', '--short'), source_sha256=fingerprints,
                python=sys.version, numpy=np.__version__, executable=sys.executable)


def artifact_names():
    return (['run.json', 'run.csv', 'run.md', 'ratios.csv'] +
            [f'{stage}_{name}.json' for stage in ('tune', 'validation', 'sources', 'design') for name in NAMES])


def preflight(output, target):
    target = target_value(target)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Refusing existing output directory (including partial runs)')
    return dict(output_dir=str(output), target_hz=target, destinations=[str(output/n) for n in artifact_names()],
                cases=list(NAMES), total_budget_seconds=TOTAL_SECONDS, child_max_wall_seconds=180,
                child_max_address_space_mib=768, zref_common_pa_s_m3=ZREF_COMMON,
                source_resistances_pa_s_m3=[0., ZREF_COMMON, 10*ZREF_COMMON])


def write_json(path, payload):
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)+'\n'
    with path.open('x', encoding='utf-8') as stream:
        stream.write(text)


def execution_ready():
    if resource is None or os.name != 'posix':
        raise StudyError('Bounded numerical execution requires POSIX resource limits; helpers and --dry-run remain available')


def _child_limits():
    execution_ready()
    resource.setrlimit(resource.RLIMIT_AS, (768*1024**2, 768*1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (175, 175))


def _worker():
    task = json.load(sys.stdin)
    started = time.monotonic()
    try:
        stage = task.pop('stage')
        result = {'tune': tune, 'validation': validate, 'sources': sources}[stage](**task)
        output = dict(ok=True, result=result)
    except Exception as exc:
        output = dict(ok=False, error=f'{type(exc).__name__}: {exc}', history=getattr(exc, 'history', []))
    output['elapsed_seconds'] = time.monotonic()-started
    print(json.dumps(output, allow_nan=False))
    return 0 if output['ok'] else 1


def bounded_task(task, timeout):
    execution_ready()
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    started = time.monotonic()
    try:
        child = subprocess.run([sys.executable, '-B', '-c',
            'from tools.equal_pitch_study import _worker; raise SystemExit(_worker())'],
            input=json.dumps(task, allow_nan=False), text=True, capture_output=True, cwd=ROOT,
            env=env, preexec_fn=_child_limits, timeout=min(180., timeout))
        try:
            result = json.loads(child.stdout)
        except (ValueError, TypeError):
            result = dict(ok=False, error='Child returned no strict result JSON')
        result.update(exit_code=child.returncode, stderr=child.stderr)
        result['ok'] = bool(result['ok'] and child.returncode == 0)
    except subprocess.TimeoutExpired:
        result = dict(ok=False, error='Child wall deadline reached; killed, no retry', exit_code=None)
    result['wall_seconds'] = time.monotonic()-started
    return result


def csv_text(rows):
    stream = io.StringIO(newline='')
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ['status']
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def render_markdown(payload):
    lines = ['# EQUAL-PITCH-01', '', f"Statut : {payload['status']}. Protocole : inferred.", '',
             'Diamètres conservés ; toutes les longueurs multipliées par un facteur unique.',
             'Accord sur maximum de |Zin|, distinct du zéro de phase. Vérification sans réaccord.', '',
             '| Profil | Facteur | f1 h=.5 (Hz) | f1 h=.25 (Hz) | Q1 h=.25 |',
             '|---|---:|---:|---:|---:|']
    for name, validation in payload['validation'].items():
        levels = validation['levels']
        lines.append(f"| {name} | {validation['factor']:.9f} | {levels[0]['modes'][0]['frequency_max_abs_hz']:.6f} | {levels[1]['modes'][0]['frequency_max_abs_hz']:.6f} | {levels[1]['modes'][0]['q_half_power']} |")
    lines += ['', 'Pload expansion / cylindre, à fréquence commune et source identique :', '',
              '| Source | Fréquence (Hz) | Rapport Pload |', '|---|---:|---:|']
    for row in payload['ratios']:
        if row['case']=='expansion' and row['observable']=='Pload' and row['frequency_group']=='common_harmonic':
            lines.append(f"| {row['source']} | {row['frequency_hz']:g} | {row['ratio']} |")
    lines += ['', 'Les fichiers sources conservent unités, valeurs complexes, logs, statuts et raisons.',
              'Les pics propres sont exportés séparément et exclus des rapports à fréquence commune.', '', *LIMITS]
    return '\n'.join(lines)+'\n'


def run(output, target=70., *, dry_run=False):
    plan = preflight(output, target)
    if dry_run:
        return dict(ok=True, dry_run=True, output_created=False, plan=plan)
    execution_ready()
    output = Path(plan['output_dir'])
    identity = provenance()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    payload = dict(schema='dcalc.equal_pitch.v1', status='partial', plan=plan, provenance=identity,
        convention='exp(+j omega t); peak sources; U towards outlet; time-averaged powers',
        units=UNITS | SOURCE_UNITS, air=asdict(CK_DRY_20C),
        air_units=dict(rho='kg/m^3', c='m/s', mu='Pa.s', kappa='W/(m.K)', cp='J/(kg.K)', gamma='1', temperature_c='degC', humidity_percent='%'),
        models=dict(loss='ZwikkerKostenLossModel', radiation=get_radiation_model('silva_unflanged').describe()),
        materials_used=synthetic_material().as_dict(), methods=dict(tuning_h_cm=.5, verification_h_cm=.25,
        residual_hz=TOLERANCE_HZ, survey_band_hz=[10,700], survey_step_hz=1,
        tuning_local_points=[81,161], final_refinement_points=[1025,2049], max_scalar_iterations=16,
        propagation='existing midpoint cylinder slices, no merging or conical matrix',
        sources_h_cm=.25, frequency_groups='common harmonic multiples of target; +/-1,+/-5 Hz; own peak separate'),
        limitations=LIMITS, tune={}, validation={}, sources={}, jobs=[])
    # All tuning first, then fixed-design validation, then source closures.
    # Estimates are conservative launch reservations, not claims of measured cost.
    for stage, estimate, cap in (('tune', 20., 60.), ('validation', 35., 90.), ('sources', 3., 20.)):
        for name in NAMES:
            task = dict(stage=stage, name=name, target=plan['target_hz'])
            if stage != 'tune':
                if name not in payload['tune']:
                    continue
                task['factor'] = payload['tune'][name]['factor']
            if stage == 'sources':
                if name not in payload['validation']:
                    continue
                task['own_peak'] = payload['validation'][name]['levels'][-1]['modes'][0]['frequency_max_abs_hz']
            remaining = TOTAL_SECONDS-(time.monotonic()-started)
            if remaining < estimate+2:
                result = dict(ok=False, error='Insufficient remaining budget for planned stage; no retry', exit_code=None, wall_seconds=0.)
            else:
                result = bounded_task(task, min(cap, remaining-2))
            result.update(stage=stage, name=name, planned_seconds=estimate, budget_remaining_before_seconds=remaining)
            write_json(output/f'{stage}_{name}.json', result)
            payload['jobs'].append({k:v for k,v in result.items() if k!='result'})
            if result['ok']:
                payload[stage][name] = result['result']
                if stage == 'tune':
                    write_json(output/f'design_{name}.json', result['result']['physical_design'])
            print(json.dumps({k:v for k,v in result.items() if k not in ('result','history','stderr')}, allow_nan=False), flush=True)
    rows = [row for source in payload['sources'].values() for row in source_rows(source)]
    payload['ratios'] = ratios_against_cylinder(rows)
    payload['numerical_wall_seconds'] = time.monotonic()-started
    payload['status'] = 'complete' if all(len(payload[k])==4 for k in ('tune','validation','sources')) else 'partial'
    write_json(output/'run.json', payload)
    for name, content in [('run.csv', csv_text(rows)), ('ratios.csv', csv_text(payload['ratios'])), ('run.md', render_markdown(payload))]:
        with (output/name).open('x', encoding='utf-8') as stream:
            stream.write(content)
    return dict(ok=payload['status']=='complete', status=payload['status'], output_dir=str(output),
                numerical_wall_seconds=payload['numerical_wall_seconds'])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--target-hz', type=float, default=70.)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args(argv)
    try:
        result = run(args.output_dir, args.target_hz, dry_run=args.dry_run)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result = dict(ok=False, error=str(exc))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
