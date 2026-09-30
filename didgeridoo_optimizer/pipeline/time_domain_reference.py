"""Bounded explicit CONFIG/DESIGN passive reference, independent of ranking."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import numpy as np

from .fixed_design import load_fixed_context
from .design_pitch import strict_input, models, destination, execution_ready, child_limits
from .evaluate_linear import LinearEvaluationPipeline
from ..geometry import GeometryDiscretizer
from ..acoustics.transfer_matrix import input_impedance
from ..acoustics.air import AirProperties
from ..nonlinear.passive_resonator import PassiveResonator, real, integer, digest
from ..nonlinear.passive_fit import (fit_passive, audit, metrics, quality_gates, DEFAULT_GATES,
                                    spectrum_identity, kkt_certificate, candidate_dictionary,
                                    completion_spec, dictionary_identity)
from ..nonlinear.resonator_td import TimeDomainResonator
from ..nonlinear.thresholds import OscillationThresholdEstimator
from ..nonlinear.lips import DimensionedLipParameters
from ..nonlinear.onset_stability import validate_parameters, equilibria
from ..reporting import time_domain_reference as report

ROOT = Path(__file__).resolve().parents[2]


def _inputs(context):
    return {key: dict(sha256=value['sha256'], read=value['read'])
            for key, value in context['provenance']['files'].items()}


def _unchanged(context):
    for key, value in context['provenance']['files'].items():
        path = Path(value['path'])
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if current != value['sha256']:
            raise ValueError('Input changed during processing: '+key)


def _context(config, design):
    config, design = Path(config).resolve(strict=True), Path(design).resolve(strict=True)
    validated = {}
    def strict_snapshot(path):
        path = Path(path).resolve()
        if not path.is_file() or path.stat().st_size > 2*1024**2:
            raise ValueError('Strict input must be a regular file <=2 MiB')
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        value = strict_input(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != before:
            raise ValueError('Input changed during strict validation')
        validated[path] = before
        return value
    cfg = strict_snapshot(config)
    raw = strict_snapshot(design)
    if not isinstance(cfg, dict) or not isinstance(raw, dict) or not isinstance(raw.get('segments'), list) or not 1 <= len(raw['segments']) <= 128:
        raise ValueError('CONFIG mapping and 1..128 DESIGN segments required')
    # Resolve and validate database bytes before invoking the historical loader.
    for key, default in [('database_file', 'materials_base_v1.yaml'), ('variant_rules_file','wood_variant_rules_v1.yaml')]:
        path = Path(cfg.get('materials', {}).get(key, default))
        path = path if path.is_absolute() else config.parent/path
        if path.exists() or key == 'database_file':
            strict_snapshot(path)
        else:
            validated[path.resolve()] = None
    context = load_fixed_context(config, design)
    for label, key, default in [('materials','database_file','materials_base_v1.yaml'), ('variant_rules','variant_rules_file','wood_variant_rules_v1.yaml')]:
        path = Path(cfg.get('materials', {}).get(key, default))
        expected = path.resolve() if path.is_absolute() else (config.parent/path).resolve()
        if expected != Path(context['provenance']['files'][label]['path']):
            raise ValueError('Ambiguous CONFIG-relative database path')
    for source in context['provenance']['files'].values():
        path = Path(source['path'])
        if path not in validated or source['sha256'] != validated[path]:
            raise ValueError('Input changed between strict validation and context loading')
    _unchanged(context)
    return context


def preflight(config, design, output_dir, **options):
    source_before = report.sources()
    defaults = dict(sample_rate_hz=12000, fit_min_hz=40., fit_max_hz=3000., fit_points=4096,
        guard_max_hz=3500., guard_points=768, audit_points=1536, h_cm=.5,
        loss_model='zk', radiation_model='legacy', air_reference='ck_dry20',
        R0=None, dc_origin=None, gates=DEFAULT_GATES.copy(), mesh_gate=.005,
        seconds=175., fit_seconds=110., max_passes=10, model_in=None,
        flow_peak_m3_s=1e-6, signal_samples=2048, v2_pressure_pa=None, v2_duration_s=.05,
        basis_completion='observed-only')
    if set(options)-set(defaults):
        raise ValueError('Unknown options: '+','.join(sorted(set(options)-set(defaults))))
    opt = {**defaults, **options}
    fs = integer(opt['sample_rate_hz'], 'sample_rate_hz', 1000, 12000)
    for key, lo, hi in [('fit_points',32,8192), ('guard_points',32,2048), ('audit_points',32,4096),
                        ('signal_samples',32,12000), ('max_passes',1,10)]:
        opt[key] = integer(opt[key], key, lo, hi)
    for key in ('fit_min_hz','fit_max_hz','guard_max_hz','h_cm','seconds','fit_seconds','mesh_gate','v2_duration_s'):
        opt[key] = real(opt[key], key, minimum=0.)
    if not 0 < opt['fit_min_hz'] < opt['fit_max_hz'] < opt['guard_max_hz'] < fs/2:
        raise ValueError('Require 0 < fit_min < fit_max < guard_max < Nyquist')
    completion = completion_spec(opt['basis_completion'], fit_max_hz=opt['fit_max_hz'],
                                 fs=fs, domain='discrete_prewarped')
    if not 0 < opt['h_cm'] <= 2 or not 0 < opt['seconds'] <= 180 or not 0 <= opt['fit_seconds'] <= opt['seconds'] or not .001 <= opt['v2_duration_s'] <= .2:
        raise ValueError('Mesh/time budget outside bounded domain')
    opt['flow_peak_m3_s'] = real(opt['flow_peak_m3_s'], 'flow_peak_m3_s', minimum=0.)
    if opt['v2_pressure_pa'] is not None:
        opt['v2_pressure_pa'] = real(opt['v2_pressure_pa'], 'v2_pressure_pa', minimum=0.)
    opt['gates'] = quality_gates(opt['gates'])
    if opt['R0'] is not None:
        opt['R0'] = real(opt['R0'], 'R0', minimum=0.)
        if not isinstance(opt['dc_origin'], str) or not opt['dc_origin'].strip():
            raise ValueError('Explicit R0 requires dc_origin text')
    elif opt['loss_model'] != 'zk' or opt['dc_origin'] is not None:
        raise ValueError('Legacy requires explicit R0 and its origin; no universal zero')
    context = _context(config, design)
    _, _, _, effective = models(context, opt)
    # Geometry preflight only, no acoustic evaluation.
    fine_h = opt['h_cm']/2
    segments = context['design'].segments
    if fine_h == 0 or fine_h < sum(s.length_cm for s in segments)/4096:
        raise ValueError('Mesh/frequency resource budget exceeded before allocation')
    count = sum(max(1 if s.is_uniform else 2,math.ceil(s.length_cm/fine_h)) for s in segments)
    if count > 4096 or count*max(opt['fit_points'],opt['audit_points']) > 8_000_000:
        raise ValueError('Mesh/frequency resource budget exceeded before allocation')
    GeometryDiscretizer().discretize(context['design'], max_segment_cm=fine_h)
    out = destination(output_dir)
    if opt['model_in'] is not None:
        path = Path(opt['model_in']).resolve(strict=True)
        model = PassiveResonator.load(path)
        if model.sample_rate_hz != fs:
            raise ValueError('Loaded model fs differs from requested effective fs')
        _check_completion(model, completion)
        opt['model_in'] = str(path)
        model_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        model_hash = None
    plan = dict(config=str(Path(config).resolve()), design=str(Path(design).resolve()), output=str(out),
        options=opt, basis_completion=completion, model_sha256=model_hash, input_files=_inputs(context), effective=effective,
        design_id=context['design'].id, gates_declared_before_fit=opt['gates'],
        budgets=dict(child_seconds=opt['seconds'], memory_mib=768, blas_threads=1,
                     total_scientific_seconds=600, maximum_children=1),
        limits=report.LIMITS)
    json.dumps(plan, allow_nan=False)
    source_after = report.sources()
    if any(source_after.get(k) != v for k,v in source_before.items()):
        raise ValueError("Loaded source changed during preflight")
    return plan, context


def _dtft(kernel, f, fs):
    out = np.empty(len(f), complex)
    n = np.arange(len(kernel))
    for i in range(0,len(f),16):
        out[i:i+16] = np.exp(-2j*np.pi*f[i:i+16,None]*n/fs)@kernel
    return out


def simulate_v2(model, *, pressure_pa, duration_s=.05, params=None, air=None):
    """Run the historical simulator without copying RK4 or replacing its factory."""
    if not isinstance(model,(PassiveResonator,TimeDomainResonator)):
        raise ValueError('Explicit native FIR or passive backend required')
    fs = integer(model.sample_rate_hz, 'effective fs', 1000, 12000)
    pressure = real(pressure_pa, 'pressure_pa', minimum=0.)
    duration = real(duration_s, 'duration_s', minimum=.001)
    if duration > .2:
        raise ValueError('V2 demonstration budget <=.2 seconds')
    params = params if params is not None else DimensionedLipParameters()
    if not isinstance(params, DimensionedLipParameters):
        raise ValueError('Explicit dimensioned_v2 parameters required')
    params = replace(params,mouth_pressure_kpa=pressure/1000)
    air = air or AirProperties.from_config({})
    validate_parameters(params,air.rho)
    dc = model.R0 if isinstance(model,PassiveResonator) else float(sum(model.impulse_kernel))
    branches = equilibria(params,air.rho,pressure,closure='fir_dc',dc=dc)
    config = dict(nonlinear_simulation=dict(lip_model_type='dimensioned_v2',
        sample_rate_hz=fs, simulation_duration_s=duration, warmup_duration_s=0.))
    verify = model.verify_file_unchanged if isinstance(model,PassiveResonator) else lambda: None
    verify()
    sim = OscillationThresholdEstimator().simulate_at_pressure(model, params,
        pressure/1000, config, reference_freq_hz=70., air=air)
    if sim['sample_rate_hz'] != fs or sim['surrogate_excitation_used']:
        raise ValueError('V2 effective fs/surrogate contract violated')
    for key in ('flow_signal','pressure_signal'):
        if not np.all(np.isfinite(sim[key])):
            raise ValueError('Nonfinite V2 experiment')
    verify()
    return dict(status='experimental_not_coupled_energy_validated', sample_rate_hz=fs,
        pressure_pa=pressure, requested_duration_s=duration, duration_s=len(sim['flow_signal'])/fs, parameters=params.as_dict(),
        equilibrium=branches, dc_closure='native DC algebra; passive R0 or native FIR sum; no stability inference',
        flow_m3_s=sim['flow_signal'].tolist(),
        pressure_port='midpoint' if isinstance(model,PassiveResonator) else 'native FIR sample',
        **({'midpoint_pressure_pa':sim['pressure_signal'].tolist()} if isinstance(model,PassiveResonator) else {'fir_pressure_pa':sim['pressure_signal'].tolist()}),
        surrogate_excitation_used=False, contact_fraction=sim.get('contact_fraction'),
        schedule='native RK4 under previous pressure, native V2 flow, '+('midpoint resonator step' if isinstance(model,PassiveResonator) else 'native FIR step'))


def _check_completion(model, expected):
    p = model.parameters()
    q = p['quality']
    certificate = q.get('certificate')
    if not isinstance(certificate, dict) or p['domain'] != expected['domain']:
        raise ValueError('Loaded completion domain/certificate mismatch')
    for saved in (q.get('basis_completion'), p['provenance'].get('basis_completion'),
                  certificate.get('basis_completion')):
        # Canonical JSON comparison also distinguishes booleans from numbers.
        if digest(saved) != digest(expected):
            raise ValueError('Loaded basis completion differs from requested deterministic specification')


def _reload_certificate(model, f, z, fg, zg, *, basis_completion=None):
    p = model.parameters(); q = p['quality']
    saved = q.get('basis_completion')
    if not isinstance(saved, dict):
        raise ValueError('Reload requires explicit basis completion metadata')
    mode = saved.get('mode') if basis_completion is None else basis_completion
    completion = completion_spec(mode, fit_max_hz=float(f[-1]), fs=model.sample_rate_hz, domain=p['domain'])
    _check_completion(model, completion)
    inventory = q.get('candidate_inventory', {})
    a, gamma, omega = (np.asarray(inventory.get(k, []), float) for k in ('a','gamma','omega'))
    if not 1 <= len(a) <= 256 or not (a.shape == gamma.shape == omega.shape):
        raise ValueError('Reload needs complete candidate inventory for recertification')
    # Validate all candidate coefficients, including inactive poles, before recertifying.
    candidate = PassiveResonator(inventory.get('a'),inventory.get('gamma'),inventory.get('omega'),
        R0=model.R0,sample_rate_hz=model.sample_rate_hz,dc_origin=p['dc_origin'],domain=p['domain'])
    expected_w,expected_g,rows,guard_identity = candidate_dictionary(f,z,R0=model.R0,fs=model.sample_rate_hz,
        domain=p['domain'],guard_frequency_hz=fg,guard_impedance=zg,basis_completion=mode)
    if not np.array_equal(candidate.omega,expected_w) or not np.array_equal(candidate.gamma,expected_g):
        raise ValueError('Loaded candidate dictionary differs from deterministic source seeds')
    active = a > 0
    if not (np.array_equal(a[active], model.a) and np.array_equal(gamma[active], model.gamma) and np.array_equal(omega[active], model.omega)):
        raise ValueError('Loaded model diverges from fit inventory')
    fit_identity = spectrum_identity(f,z)
    if q.get('fit_spectrum_sha256') != fit_identity or q.get('fit_frequency_hz') != f.tolist():
        raise ValueError('Loaded fit spectrum identity differs')
    for metadata in (q, p['provenance']):
        if metadata.get('fit_spectrum_sha256') != fit_identity or metadata.get('guard_spectrum_sha256') != guard_identity:
            raise ValueError('Loaded fit/guard source identity differs')
    dictionary_hash = dictionary_identity(expected_w, expected_g, rows, completion, fit_identity, guard_identity)
    if digest(q.get('seeds')) != digest(rows) or any(metadata.get('candidate_dictionary_sha256') != dictionary_hash for metadata in (q, q['certificate'])):
        raise ValueError('Loaded dictionary identity/observed seeds differ')
    s = 2j*model.sample_rate_hz*np.tan(np.pi*f/model.sample_rate_hz) if p['domain'] == 'discrete_prewarped' else 2j*np.pi*f
    weight = 1/np.maximum(abs(z), .01*np.max(abs(z)))
    B = s[:,None]/(s[:,None]**2+gamma*s[:,None]+omega**2)*weight[:,None]
    b = (z-model.R0)*weight
    certificate, _ = kkt_certificate(np.vstack((B.real,B.imag)),np.r_[b.real,b.imag],a)
    certificate.update(basis_completion=completion, candidate_dictionary_sha256=dictionary_hash,
                       candidate_count=len(a), recertified_from_actual_spectra=True)
    return certificate


def calculate(plan, context):
    opt = plan['options']; output = Path(plan['output']); started = time.monotonic()
    payload = dict(schema='dcalc.time_domain_reference.v1', status='partial', ok=False,
        statuses=report.statuses(), limits=report.LIMITS, options=opt,
        basis_completion=plan['basis_completion'],
        provenance=dict(inputs=_inputs(context), software=context['provenance']['software']),
        materials_used=context['materials_used'], effective=plan['effective'], budgets=plan['budgets'])
    before = report.sources()
    def checkpoint(stage):
        payload['elapsed_seconds'] = time.monotonic()-started
        report.checkpoint(output,payload,stage)
    checkpoint('started')
    try:
        if _inputs(context) != plan['input_files']:
            raise ValueError('Inputs changed since preflight')
        completion = completion_spec(opt['basis_completion'], fit_max_hz=opt['fit_max_hz'],
                                     fs=opt['sample_rate_hz'], domain='discrete_prewarped')
        if digest(completion) != digest(plan['basis_completion']):
            raise ValueError('Basis completion changed since preflight')
        air, loss, radiation, effective = models(context,opt)
        design = context['design']; geometry = GeometryDiscretizer()
        mesh = geometry.discretize(design,max_segment_cm=opt['h_cm'])
        fine = geometry.discretize(design,max_segment_cm=opt['h_cm']/2)
        radius = design.segments[-1].d_out_cm/200
        def target(f, mesh_used=mesh):
            z = input_impedance(f,mesh_used,context['material_db'],air,exit_radius_m=radius,
                                loss_model=loss,radiation_model=radiation)
            if not np.all(np.isfinite(z)):
                raise ValueError('TMM returned nonfinite spectrum')
            return z
        f = np.linspace(opt['fit_min_hz'],opt['fit_max_hz'],opt['fit_points'])
        fg = np.linspace(opt['fit_max_hz'],opt['guard_max_hz'],opt['guard_points'])
        fv = opt['fit_min_hz']+(np.arange(opt['audit_points'])+.618033988749895)*(opt['fit_max_hz']-opt['fit_min_hz'])/opt['audit_points']
        z, zg = target(f), target(fg)
        if opt['R0'] is None:
            mu = effective['air']['mu']
            R0 = sum(8*mu*(s.length_cm/100)/(np.pi*(s.d_in_cm/200)**4) for s in mesh.segments)
            dc = dict(kind='zk_local_1d', description='Sum 8*mu*L/(pi*a^4) on local circular rigid slices; linear acoustic DC limit, not finite mean-flow law')
        else:
            R0 = opt['R0']; dc = dict(kind='explicit',description=opt['dc_origin'])
        identity = digest(dict(inputs=_inputs(context), effective=effective, h_cm=opt['h_cm'],
                               fs=opt['sample_rate_hz'],R0=R0,dc=dc,basis_completion=completion))
        payload['dc'] = dict(R0=R0,origin=dc)
        payload['spectra'] = dict(fit_sha256=spectrum_identity(f,z),guard_sha256=spectrum_identity(fg,zg))
        checkpoint('spectra_ready')
        if opt['model_in']:
            model = PassiveResonator.load(opt['model_in'],expected_sha256=plan['model_sha256'])
            if model.parameters()['provenance'].get('context_identity') != identity or model.parameters()['domain'] != 'discrete_prewarped' or model.R0 != R0 or model.sample_rate_hz != opt['sample_rate_hz']:
                raise ValueError('Model/context mismatch; mixed sources refused')
            fit = model.parameters()['quality']
            fit['certificate'] = _reload_certificate(model,f,z,fg,zg,basis_completion=opt['basis_completion'])
            fit['status'] = 'converged' if fit['certificate']['converged'] else 'not_converged'
            if fit.get('guard_spectrum_sha256') != spectrum_identity(fg,zg):
                raise ValueError('Loaded guard identity differs')
        else:
            model, fit = fit_passive(f,z,R0=R0,dc_origin=dc,sample_rate_hz=opt['sample_rate_hz'],
                gates=opt['gates'],guard_frequency_hz=fg,guard_impedance=zg,
                basis_completion=opt['basis_completion'],
                seconds=opt['fit_seconds'],max_passes=opt['max_passes'],
                progress=lambda value: report.write_json(output/'fit_progress.json',value,replace=True),
                provenance=dict(context_identity=identity,inputs=_inputs(context),sources_sha256=before))
        payload['fit'] = fit
        payload['statuses'].update(passivity='structural',fitter=fit['status'])
        parameters = model.parameters()
        parameters['quality'] = fit
        model = PassiveResonator.from_parameters(parameters)
        model.save(output/'model.json')
        model = PassiveResonator.load(output/'model.json')
        checkpoint('model_saved_reloaded')
        zv = target(fv)
        payload['audit'] = audit(model,fv,zv,fit_frequencies=f,gates=opt['gates'])
        payload['statuses']['fidelity'] = payload['audit']['status']
        fine_z = target(fv,fine)
        mesh_metrics = metrics(fine_z,zv)
        payload['mesh'] = dict(coarse_cm=opt['h_cm'],fine_cm=opt['h_cm']/2,
                              nrmse_gate=opt['mesh_gate'],metrics=mesh_metrics)
        payload['statuses']['mesh'] = 'accepted' if mesh_metrics['complex_nrmse'] <= opt['mesh_gate'] else 'not_accepted'
        np.savez_compressed(output/'spectra.npz',fit_f=f,fit_z=z,guard_f=fg,guard_z=zg,
                            audit_f=fv,audit_z=zv,fine_z=fine_z,model_z=model.discrete_response(fv))
        checkpoint('audited')
        # Real native legacy evaluation and native metadata, including actual peaks.
        cfg = copy.deepcopy(context['config'])
        cfg.setdefault('frequency_analysis',{}).update(f_min_hz=opt['fit_min_hz'],f_max_hz=opt['fit_max_hz'],n_points=opt['fit_points'],discretization_max_segment_cm=opt['h_cm'])
        cfg['nonlinear_simulation'] = dict(sample_rate_hz=opt['sample_rate_hz'],resonator_model_type='fir_long_logfit',resonator_kernel_duration_s=1.)
        native = LinearEvaluationPipeline().evaluate(copy.deepcopy(design),cfg,context['material_db'])
        if not native['valid'] or native['errors']:
            raise ValueError('Native legacy reference evaluation invalid')
        fir = TimeDomainResonator.from_linear_result(native,cfg)
        legacy_z = input_impedance(fv,mesh,context['material_db'],
            exit_radius_m=radius,air=AirProperties.from_config(cfg))
        # This explicit legacy reference always uses CONFIG air and default legacy loss/radiation.
        fir_z = _dtft(fir.impulse_kernel,fv,fir.sample_rate_hz)
        payload['fir'] = dict(source='native LinearEvaluationPipeline legacy / native TimeDomainResonator.from_linear_result',
            metadata=fir.metadata,peaks=native['peaks'],
            native_features={k:native['features'].get(k) for k in ('f0_hz','fundamental_peak_magnitude','fundamental_q','peak_count')},
            dc_pa_s_m3=float(sum(fir.impulse_kernel)),metrics_against_legacy=metrics(legacy_z,fir_z),
            passive_against_same_legacy=metrics(legacy_z,model.discrete_response(fv)),
            comparison_scope='passive target may be ZK; model differences explicitly retained',
            native_spectrum_sha256=spectrum_identity(np.asarray(native['freq_hz']),np.asarray(native['zin'])))
        n = opt['signal_samples']; u = opt['flow_peak_m3_s']*np.sin(2*np.pi*70*np.arange(n)/model.sample_rate_hz)
        p = model.pressure_from_flow(u)
        np.savez_compressed(output/'forced.npz',flow_m3_s=u,midpoint_pressure_pa=p,
            fir_pressure_pa=fir.pressure_from_flow(u),impulse=model.impulse_response(n),fs=model.sample_rate_hz)
        payload['forced'] = dict(status='experimental_prescribed_flow',
            sinusoid_within_fit_band=bool(opt['fit_min_hz'] <= 70 <= opt['fit_max_hz']),
            extrapolation_warning=None if opt['fit_min_hz'] <= 70 <= opt['fit_max_hz'] else '70 Hz is outside the audited fit band',
            impulse_scope='finite unit-sample experiment; broadband fidelity not certified',samples=n,sample_rate_hz=model.sample_rate_hz,
            transient_scope='Start from rest, finite sinusoid and impulse are not bandlimited; no validated fidelity outside the fit band',
            units=dict(flow='m^3/s',pressure='Pa'),pressure_port='midpoint',frequency_hz=70.,flow_peak_m3_s=opt['flow_peak_m3_s'])
        if opt['v2_pressure_pa'] is not None:
            payload['v2'] = dict(passive=simulate_v2(model,pressure_pa=opt['v2_pressure_pa'],duration_s=opt['v2_duration_s'],air=air),
                native_fir=simulate_v2(fir,pressure_pa=opt['v2_pressure_pa'],duration_s=opt['v2_duration_s'],air=air),
                scope='Same effective fs, air, supplied pressure and V2 parameters; different resonator models and port conventions')
        model.verify_file_unchanged()
        if opt['model_in']:
            PassiveResonator.load(opt['model_in'],expected_sha256=plan['model_sha256'])
        _unchanged(context)
        after = report.sources()
        if any(after.get(k) != v for k,v in before.items()):
            raise ValueError('Loaded source changed during processing')
        payload['provenance'].update(sources_sha256=after,inputs_and_sources_unchanged=True,
            executed_model_sha256=hashlib.sha256((output/'model.json').read_bytes()).hexdigest())
        accepted = all(payload['statuses'][k] == expected for k,expected in
                       [('passivity','structural'),('fitter','converged'),('fidelity','accepted'),('mesh','accepted')])
        payload.update(ok=accepted,status='numerically_accepted' if accepted else 'not_accepted')
        checkpoint('complete')
    except (Exception, KeyboardInterrupt) as exc:
        payload.update(ok=False,status='interrupted' if isinstance(exc,(KeyboardInterrupt,TimeoutError)) else 'failed',
                       error=type(exc).__name__+': '+str(exc))
        payload['statuses']['fidelity'] = 'not_accepted'
        checkpoint('failed')
    return payload


def worker():
    plan = json.load(sys.stdin)
    try:
        context = _context(plan['config'],plan['design'])
        value = calculate(plan,context)
    except Exception as exc:
        value = dict(ok=False,status='failed',statuses=report.statuses(),error=type(exc).__name__+': '+str(exc))
    print(json.dumps(value,allow_nan=False))
    return 0 if value['ok'] else 1


class RunInterrupted(Exception):
    pass


def run(config, design, output_dir, *, dry_run=False, **options):
    plan, context = preflight(config,design,output_dir,**options)
    if dry_run:
        return dict(ok=True,status='preflight_valid',dry_run=True,output_created=False,plan=plan,
                    statuses=report.statuses(),not_executed=['acoustics','fit','simulation','writes'])
    execution_ready()
    output = Path(plan['output']); output.mkdir(parents=True,exist_ok=False)
    report.write_json(output/'plan.json',plan)
    initial_sources = report.sources()
    started = time.monotonic(); handlers = {}
    def interrupt(signum, frame):
        raise RunInterrupted('Signal '+str(signum)+'; child reaped, partial evidence retained')
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):
            handlers[sig] = signal.signal(sig,interrupt)
    child = None; error = None
    try:
        env = dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',
                   NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
        child = subprocess.run([sys.executable,'-B','-m','tools.time_domain_reference','--worker'],
            input=json.dumps(plan,allow_nan=False),text=True,capture_output=True,cwd=ROOT,
            env=env,preexec_fn=child_limits,timeout=plan['options']['seconds'])
        value = json.loads(child.stdout)
        if child.returncode != 0:
            value['ok'] = False
            if value.get('status') == 'numerically_accepted':
                value.update(status='failed',error='Nonzero child exit contradicts reported acceptance')
                value['statuses']['fidelity'] = 'not_accepted'
    except (Exception,KeyboardInterrupt) as exc:
        error = type(exc).__name__+': '+str(exc)
        value = json.loads((output/'partial.json').read_text()) if (output/'partial.json').is_file() else dict(statuses=report.statuses())
        value.update(ok=False,status='interrupted',error=error)
        value['statuses']['fidelity'] = 'not_accepted'
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig,handler)
    value['child'] = dict(exit_code=child.returncode if child else None,wall_seconds=time.monotonic()-started,
                          reaped=True,limit_seconds=plan['options']['seconds'],memory_mib=768,blas_threads=1)
    value.setdefault('basis_completion', plan['basis_completion'])
    after = report.sources()
    if any(after.get(k) != v for k,v in initial_sources.items()):
        value.update(ok=False,status='failed',error='Parent source changed during processing')
        value['statuses']['fidelity'] = 'not_accepted'
    report.export(output,value)
    return dict(ok=value['ok'],status=value['status'],statuses=value['statuses'],
                basis_completion=value['basis_completion'],child=value['child'],output_dir=str(output))
