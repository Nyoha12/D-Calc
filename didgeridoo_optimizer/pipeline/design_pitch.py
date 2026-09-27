"""Bounded tuning of supplied physical designs; existing acoustics only."""
from __future__ import annotations

import copy
from dataclasses import asdict
import json
import os
import signal
import threading
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

from ..acoustics import forced_response as fr
from ..acoustics.air import AirProperties
from ..acoustics.losses import LegacyBetaLossModel
from ..acoustics.radiation_models import get_radiation_model
from ..acoustics.source_impedance import apply_thevenin_source
from ..acoustics.thermoviscous import CK_DRY_20C, CK_DRY_25C, ZwikkerKostenLossModel
from ..acoustics.transfer_matrix import input_impedance
from .design_input import (_load_yaml_design, _validate_annotations, _unique_json_mapping,
                           _reject_json_constant, finite_real,
                           validate_design)
from .fixed_design import load_fixed_context
from ..reporting import forced_response as export_fr
from ..reporting import forced_response_comparison as comparison
from ..reporting import design_pitch as report
from tools.equal_pitch_study import (SURVEY, TOLERANCE_HZ, StudyError, first_peak,
                                    scale_design, solve_factor, target_value)
from tools.forced_response_compare import termination_assumptions
from tools.thermo_reference_compare import extract_modes

try:
    import resource
except ImportError:
    resource = None

ROOT = Path(__file__).resolve().parents[2]
AIR_REFERENCES = {'ck_dry20': CK_DRY_20C, 'ck_dry25': CK_DRY_25C}
REFINEMENT = (1025, 2049)
MAX_INPUT_BYTES = 2 * 1024**2
MAX_PHYSICAL_SEGMENTS = 128
TOTAL_SECONDS = 420.


def strict_input(path):
    """Bound file size and reject ambiguous YAML/JSON before legacy adapters."""
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f'Input must be a regular file <=2 MiB: {path}')
    text = path.read_text(encoding='utf-8-sig')
    raw = (json.loads(text, object_pairs_hook=_unique_json_mapping, parse_constant=_reject_json_constant)
           if path.suffix.lower() == '.json' else _load_yaml_design(text))
    _validate_annotations(raw, str(path), set())
    return raw


def source_options(options):
    choices = [('pressure', options.get('pressure_peak_pa')),
               ('volume_flow', options.get('flow_peak_m3_s')),
               ('thevenin_pressure', options.get('thevenin_pressure_peak_pa'))]
    selected = [(kind, value) for kind, value in choices if value is not None]
    if len(selected) != 1:
        raise ValueError('Exactly one explicit peak source is required')
    kind, amplitude = selected[0]
    amplitude = fr.real_positive(amplitude, 'peak source')
    resistance = options.get('source_resistance_pa_s_m3')
    if kind == 'thevenin_pressure':
        resistance = finite_real(resistance, 'source resistance Rs')
        if resistance < 0:
            raise ValueError('source resistance Rs must be >=0')
    elif resistance is not None:
        raise ValueError('source resistance requires thevenin pressure (including Rs=0)')
    return dict(kind=kind, amplitude=amplitude, resistance_pa_s_m3=resistance)


def models(context, options):
    original = AirProperties.from_config(context['config'])
    state = AIR_REFERENCES.get(options.get('air_reference'))
    if options['loss_model'] == 'zk':
        if state is None:
            raise ValueError('zk requires explicit --air-reference ck_dry20 or ck_dry25')
        air, loss = state.as_air_properties(), ZwikkerKostenLossModel(state)
    elif options['loss_model'] == 'legacy':
        if options.get('air_reference') is not None:
            raise ValueError('legacy uses exact CONFIG air; --air-reference requires zk')
        air, loss = original, LegacyBetaLossModel()
    else:
        raise ValueError('loss_model must be legacy or zk')
    radiation = get_radiation_model(options['radiation_model'])
    effective = dict(air=asdict(state) if state else asdict(air), original_air=asdict(original),
        air_substitution='explicit CK diagnostic, in memory only' if state else 'none; exact CONFIG air',
        loss_model=loss.name, radiation=radiation.describe())
    return air, loss, radiation, effective


def scaled(context, factor):
    # The historical helper adds its own annotation. Keep user metadata exactly,
    # including an existing longitudinal_factor; record this run's factor outside.
    # validate_design retains the native derived metadata.total_length_cm update.
    design = scale_design(context['design'], factor)
    design.metadata = copy.deepcopy(context['design'].metadata)
    return validate_design(design.as_dict(), context['material_db'], context['config'])


def common_frequencies(target):
    return sorted({target*i for i in range(1, 8)} | {target+d for d in (-5, -1, 1, 5)})


def destination(path):
    original = Path(path).absolute()
    if os.path.lexists(original):
        raise FileExistsError(f'Refusing existing output directory, including partial runs: {original}')
    parent = original.parent
    while not parent.exists():
        if parent.is_symlink():
            raise ValueError(f'Dangling output parent: {parent}')
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError(f'Output parent is not a writable directory: {parent}')
    resolved = original.resolve()
    if os.path.lexists(resolved):
        raise FileExistsError(f'Refusing existing resolved output directory: {resolved}')
    return resolved


def preflight(config, designs, output_dir, **options):
    defaults = dict(target_hz=70., scale_min=.8, scale_max=1.4, h_cm=None,
                    max_iterations=16, loss_model='legacy', radiation_model='legacy', air_reference=None)
    defaults.update(options)
    options = defaults
    source = source_options(options)  # Must precede any acoustic work.
    options['target_hz'] = target_value(options['target_hz'])
    lo, hi = (fr.real_positive(options[key], key) for key in ('scale_min', 'scale_max'))
    if not .25 <= lo < hi <= 4.:
        raise ValueError('Require 0.25 <= scale_min < scale_max <= 4; no automatic bound relaxation')
    count = options['max_iterations']
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 64:
        raise ValueError('max_iterations must be an integer in [1,64]')
    paths = [Path(p).resolve(strict=True) for p in designs]
    if not 1 <= len(paths) <= 4:
        raise ValueError('Require 1..4 DESIGN files')
    for i, path in enumerate(paths):
        if any(path.samefile(previous) for previous in paths[:i]):
            raise ValueError(f'Repeated DESIGN path (including symlink/hardlink alias): {path}')
    config = Path(config).resolve(strict=True)
    raw_config = strict_input(config)
    if not isinstance(raw_config, dict):
        raise ValueError('CONFIG must be a mapping')
    output = destination(output_dir)
    contexts, mapping, identifiers = [], [], set()
    shared_files = None
    for index, path in enumerate(paths, 1):
        raw = strict_input(path)
        if not isinstance(raw, dict) or not isinstance(raw.get('segments'), list):
            raise ValueError(f'DESIGN must contain segments: {path}')
        if not 1 <= len(raw['segments']) <= MAX_PHYSICAL_SEGMENTS:
            raise ValueError('Budget: physical segment count must be in [1,128]')
        context = load_fixed_context(config, path)
        # Adapter resolution is historical. Reject any cwd/fallback substitution;
        # material paths here have the unambiguous CONFIG-relative meaning.
        for label, key, default in [('materials', 'database_file', 'materials_base_v1.yaml'),
                                    ('variant_rules', 'variant_rules_file', 'wood_variant_rules_v1.yaml')]:
            requested = Path(context['config'].get('materials', {}).get(key, default))
            expected = requested.resolve() if requested.is_absolute() else (config.parent/requested).resolve()
            actual = Path(context['provenance']['files'][label]['path'])
            if expected != actual:
                raise ValueError(f'{key}: ambiguous historical path resolution: expected {expected}, got {actual}; use an absolute path')
        files = {k: v for k, v in context['provenance']['files'].items() if k != 'design'}
        if shared_files is not None and files != shared_files:
            raise ValueError('CONFIG/material context changed between DESIGN loads')
        shared_files = files
        design = context['design']
        if design.id in identifiers:
            raise ValueError(f'Repeated DESIGN id {design.id!r}; assign distinct ids explicitly')
        identifiers.add(design.id)
        frequency = context['effective_parameters']['frequency_analysis']
        if frequency['n_points'] > fr.MAX_FREQUENCIES:
            raise ValueError('CONFIG frequency budget exceeds 20000 points')
        h = fr.real_positive(options['h_cm'] if options['h_cm'] is not None else
                             frequency['discretization_max_segment_cm'], 'h_cm')
        if h > 2.:
            raise ValueError('h_cm must be <=2 cm for this bounded diagnostic')
        options['h_cm'] = h
        _, _, _, effective = models(context, options)
        bounds = []
        for factor in (1., lo, hi):
            try:
                physical = scaled(context, factor)
                mesh = fr.prepare_mesh(physical, h/2, max(REFINEMENT))
                bounds.append(dict(factor=factor, fine_segments=len(mesh.segments)))
            except (ValueError, ArithmeticError) as exc:
                raise ValueError(f'DESIGN {index:03d}, scale={factor:g}: bounds/geometry/budget incompatible: {exc}') from exc
        contexts.append(context)
        mapping.append(dict(index=index, design_id=design.id, input_path=str(path),
                            input_sha256=context['provenance']['files']['design']['sha256'],
                            tuned_design=f'tuned_design_{index:03d}.json', bounds=bounds))
    plan = dict(schema='dcalc.design_pitch.plan.v1', config=str(config), output_dir=str(output),
                options=options, source=source, effective=effective, mapping=mapping,
                common_frequency_hz=common_frequencies(options['target_hz']),
                total_budget_seconds=TOTAL_SECONDS, child_seconds=180, child_memory_mib=768,
                tolerance_hz=TOLERANCE_HZ, survey_band_hz=[10, 700], survey_points=len(SURVEY),
                refinement_points=list(REFINEMENT), max_physical_segments=MAX_PHYSICAL_SEGMENTS,
                destinations=report.artifact_names(len(paths)), shared_input_files=shared_files)
    # Reject any remaining non-JSON values before creating output.
    json.dumps(plan, allow_nan=False)
    return plan, contexts


def evaluator(context, design, h, options):
    # The largest refinement is budgeted before any mesh/frequency propagation.
    mesh = fr.prepare_mesh(design, h, max(REFINEMENT))
    air, loss, radiation, _ = models(context, options)
    radius = design.segments[-1].d_out_cm/200

    def evaluate(frequencies):
        fr.check_budget(len(frequencies), len(mesh.segments))
        freq = fr.frequencies(frequencies)
        z = input_impedance(freq, mesh, context['material_db'], air,
                            exit_radius_m=radius, loss_model=loss, radiation_model=radiation)
        if not np.all(np.isfinite(z)):
            raise StudyError('Nonfinite input impedance')
        return z
    return evaluate, mesh


def inspect_modes(context, design, h, options):
    evaluate, mesh = evaluator(context, design, h, options)
    peak = first_peak(evaluate)
    zin = evaluate(SURVEY)
    modes_found = extract_modes(evaluate, SURVEY, zin, max_modes=3, refinement_points=REFINEMENT)
    reasons = []
    if len(modes_found) != 3 or any(m['status'] != 'resolved' for m in modes_found):
        reasons.append('Three unique frequency-ordered modes not resolved in 10..700 Hz')
    if modes_found and modes_found[0]['status'] == 'resolved':
        if abs(peak['frequency_hz']-modes_found[0]['frequency_max_abs_hz']) > TOLERANCE_HZ:
            reasons.append('First local peak and native mode extraction disagree')
    for mode in modes_found:
        levels = mode['frequency_refinement']
        if all(level['status'] == 'resolved' for level in levels):
            if abs(levels[-1]['frequency_max_abs_hz']-levels[-2]['frequency_max_abs_hz']) > TOLERANCE_HZ:
                reasons.append(f"Mode {mode['mode_ordinal']}: frequency refinement exceeds .002 Hz")
        else:
            reasons.append(f"Mode {mode['mode_ordinal']}: unresolved frequency refinement")
    return dict(h_cm=h, slices=len(mesh.segments), physical_sha256=report.fingerprint(design.as_dict()),
                first_peak=peak, modes=modes_found, reasons=reasons, verified=not reasons)


def forced_bundle(context, design, options, identity):
    source = source_options(options)
    frequency = common_frequencies(options['target_hz'])
    mesh = fr.prepare_mesh(design, options['h_cm']/2, len(frequency))
    air, loss, radiation, effective = models(context, options)
    started = time.monotonic()
    transfer = fr.loaded_transfer(np.array(frequency), mesh, context['material_db'], air,
        exit_radius_m=design.segments[-1].d_out_cm/200, loss_model=loss, radiation_model=radiation)
    elapsed = time.monotonic()-started
    transfer['radiation']['termination'] = termination_assumptions(design)
    response = (apply_thevenin_source(transfer, source['amplitude'], source['resistance_pa_s_m3'])
                if source['kind'] == 'thevenin_pressure' else
                fr.apply_source(transfer, source['kind'], source['amplitude']))
    model = export_fr.model_payload(transfer, response, elapsed)
    effective.update(h_cm=options['h_cm']/2, f_min_hz=frequency[0], f_max_hz=frequency[-1],
                     n_points=len(frequency), frequency_groups='common target multiples 1..7 and offsets -5,-1,+1,+5 Hz')
    v2 = source['kind'] == 'thevenin_pressure'
    payload = dict(schema=export_fr.SCHEMA_V2 if v2 else export_fr.SCHEMA,
        convention=comparison.CONVENTION, units=export_fr.UNITS | export_fr.SOURCE_UNITS if v2 else export_fr.UNITS,
        provenance=identity, effective_parameters=effective, materials_used=context['materials_used'],
        original_context=dict(config=context['config'], provenance=context['provenance']),
        warnings=context['warnings'], assumptions=report.LIMITS,
        not_executed=export_fr.NOT_EXECUTED,
        cases=[dict(nature='user supplied; no experimental status inferred', physical_design=design.as_dict(),
                    analysis_design=mesh.as_dict(), models=[model])])
    comparison.validate_export(payload)
    return payload


def profile(context, options, output, index, identity):
    """Checkpoint each completed stage; tuning trace survives a killed child."""
    suffix = f'{index:03d}'
    stage = 'original'
    trace = output/f'trace_{suffix}.jsonl'
    result = dict(ok=False, verified=False, index=index, stage=stage)

    def event(payload):
        with trace.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, allow_nan=False)+'\n')
            stream.flush()

    def save(name, value):
        report.write_json(output/f'{name}_{suffix}.json', value)

    try:
        original = context['design']
        save('original_design', original.as_dict())
        before = inspect_modes(context, original, options['h_cm']/2, options)
        save('original', before)
        bundle = forced_bundle(context, original, options, identity)
        export_fr.export_bundle(bundle, output/f'response_original_{suffix}')
        stage = 'tuning'
        measurements = []

        def measure(factor):
            event(dict(stage=stage, event='proposal', factor=factor))
            try:
                physical = scaled(context, factor)  # EVERY proposal validated.
                evaluate, mesh = evaluator(context, physical, options['h_cm'], options)
                peak = first_peak(evaluate)
                row = dict(factor=factor, slices=len(mesh.segments), **peak,
                           error_hz=peak['frequency_hz']-options['target_hz'])
                measurements.append(row)
                event(dict(stage=stage, event='measurement', **row))
                return peak['frequency_hz']
            except Exception as exc:
                event(dict(stage=stage, event='error', factor=factor, error=str(exc)))
                raise

        tuned = solve_factor(measure, options['target_hz'], (options['scale_min'], options['scale_max']),
                             max_iterations=options['max_iterations'])
        tuned['measurements'] = measurements
        save('tuning', tuned)
        physical = scaled(context, tuned['factor'])
        save('tuned_design', physical.as_dict())
        stage = 'validation'
        levels = []
        for h in (options['h_cm'], options['h_cm']/2):
            level = inspect_modes(context, physical, h, options)
            level['error_hz'] = level['first_peak']['frequency_hz']-options['target_hz']
            event(dict(stage=stage, event='level', **level))
            levels.append(level)
        reasons = [reason for level in levels for reason in level['reasons']]
        if any(abs(level['error_hz']) > TOLERANCE_HZ for level in levels):
            reasons.append('Target not verified within .002 Hz at h and h/2; no retuning at h/2')
        for level in levels:
            if level['modes'] and level['modes'][0]['status'] == 'resolved':
                if abs(level['modes'][0]['frequency_max_abs_hz']-options['target_hz']) > TOLERANCE_HZ:
                    reasons.append('Native first mode extraction exceeds .002 Hz target residual')
        if levels[0]['physical_sha256'] != levels[1]['physical_sha256']:
            reasons.append('Physical geometry changed during mesh verification')
        validation = dict(verified=not reasons, reasons=reasons, levels=levels,
            fixed_design_no_retune=True, error_hz=levels[-1]['error_hz'],
            mesh_frequency_deltas_hz=[b['frequency_max_abs_hz']-a['frequency_max_abs_hz']
                if a['status'] == b['status'] == 'resolved' else None
                for a, b in zip(levels[0]['modes'], levels[1]['modes'])])
        save('validation', validation)
        stage = 'forced_response'
        after_bundle = forced_bundle(context, physical, options, identity)
        export_fr.export_bundle(after_bundle, output/f'response_tuned_{suffix}')
        complete = all(b['cases'][0]['models'][0]['numerically_complete'] for b in (bundle, after_bundle))
        reasons += before['reasons']
        if not complete:
            reasons.append('Forced-response observables not all numerically complete; inspect statuses/reasons')
        result.update(ok=not reasons, verified=not reasons, stage='complete', reasons=reasons)
    except Exception as exc:
        result.update(stage=stage, error=f'{type(exc).__name__}: {exc}', history=getattr(exc, 'history', []))
        event(dict(stage=stage, event='failure', error=result['error'], history=result['history']))
    save('profile', result)
    return result


def execution_ready():
    if resource is None or os.name != 'posix':
        raise ValueError('Bounded execution requires POSIX resource limits; --dry-run is portable')


def child_limits():
    execution_ready()
    resource.setrlimit(resource.RLIMIT_AS, (768*1024**2, 768*1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (175, 175))


def worker():
    task = json.load(sys.stdin)
    try:
        context = load_fixed_context(task['config'], task['design'])
        if context['provenance']['files'] != task['input_files']:
            raise ValueError('Inputs changed since preflight; refusing acoustic calculation')
        result = profile(context, task['options'], Path(task['output']), task['index'], task['identity'])
    except Exception as exc:
        result = dict(ok=False, verified=False, error=f'{type(exc).__name__}: {exc}')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result['ok'] else 1


def bounded_task(task, timeout):
    execution_ready()
    started = time.monotonic()
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    try:
        child = subprocess.run([sys.executable, '-B', '-c',
            'from didgeridoo_optimizer.pipeline.design_pitch import worker; raise SystemExit(worker())'],
            input=json.dumps(task, allow_nan=False), text=True, capture_output=True, cwd=ROOT,
            env=env, preexec_fn=child_limits, timeout=min(180., timeout))
        try:
            result = json.loads(child.stdout, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
            if not isinstance(result, dict):
                raise ValueError('Expected child object')
        except (ValueError, TypeError):
            result = dict(ok=False, error='Child returned no strict result JSON')
        result.update(exit_code=child.returncode, stderr=child.stderr, stdout=child.stdout)
        result['ok'] = bool(result.get('ok') is True and result.get('verified') is True and child.returncode == 0)
    except subprocess.TimeoutExpired as exc:
        def text(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
        result = dict(ok=False, error='Child wall deadline reached; killed and reaped; no retry',
                      exit_code=None, timeout=True, stdout=text(exc.stdout), stderr=text(exc.stderr))
    except (OSError, subprocess.SubprocessError) as exc:
        result = dict(ok=False, error=f'{type(exc).__name__}: {exc}', exit_code=None)
    result['wall_seconds'] = time.monotonic()-started
    return result


class RunInterrupted(Exception):
    """A parent signal must unwind subprocess.run, killing/reaping its child."""


def interrupt_run(signum, frame):
    raise RunInterrupted(f'Run interrupted by signal {signum}; partial artifacts retained')


def run(config, designs, output_dir, *, dry_run=False, **options):
    plan, contexts = preflight(config, designs, output_dir, **options)
    if dry_run:
        return dict(ok=True, dry_run=True, output_created=False, plan=plan)
    execution_ready()  # Unsupported backend refuses BEFORE any directory write.
    identity = report.provenance(ROOT)
    output = Path(plan['output_dir'])
    output.mkdir(parents=True, exist_ok=False)
    report.write_json(output/'plan.json', plan)
    started, jobs = time.monotonic(), []
    interrupted = False
    old_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum, interrupt_run)
    try:
        for row, context in zip(plan['mapping'], contexts, strict=True):
            remaining = TOTAL_SECONDS-(time.monotonic()-started)
            index = row['index']
            print(f"Profil {index:03d}/{len(contexts):03d} : accord et vérification ; budget restant {remaining:.1f} s", file=sys.stderr, flush=True)
            task = dict(config=plan['config'], design=row['input_path'], options=plan['options'],
                        output=str(output), index=index, input_files=context['provenance']['files'], identity=identity)
            if interrupted or remaining <= 2:
                result = dict(ok=False, error='Run interrupted or total budget exhausted; profile not launched', exit_code=None)
            else:
                try:
                    result = bounded_task(task, min(180., remaining-2))
                except RunInterrupted as exc:
                    # subprocess.run kills and waits for its child on this exception.
                    interrupted = True
                    result = dict(ok=False, interrupted=True, error=str(exc), exit_code=None)
                except Exception as exc:
                    result = dict(ok=False, error=f'{type(exc).__name__}: {exc}', exit_code=None)
            result['index'] = index
            report.write_json(output/f'job_{index:03d}.json', result)
            jobs.append(result)
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    # Acquired artifacts remain readable even when a later child fails/timeouts.
    payload = report.summarize(output, plan, jobs, identity)
    payload['numerical_wall_seconds'] = time.monotonic()-started
    report.write_summary(output, payload)
    return dict(ok=payload['status'] == 'complete', status=payload['status'], output_dir=str(output),
                summary=str(output/'summary.json'), numerical_wall_seconds=payload['numerical_wall_seconds'])
