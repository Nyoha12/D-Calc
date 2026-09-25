"""Bounded forced-response diagnostic, with explicit peak source; no scores."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import yaml

from didgeridoo_optimizer.acoustics import forced_response as fr
from didgeridoo_optimizer.acoustics.source_impedance import apply_thevenin_source
from didgeridoo_optimizer.acoustics.air import AirProperties
from didgeridoo_optimizer.acoustics.losses import LegacyBetaLossModel
from didgeridoo_optimizer.acoustics.radiation_models import NAMES as RADIATION_NAMES, get_radiation_model
from didgeridoo_optimizer.acoustics.thermoviscous import CK_DRY_20C, CK_DRY_25C, ZwikkerKostenLossModel
from didgeridoo_optimizer.geometry.builders import DesignBuilder
from didgeridoo_optimizer.geometry.constraints import GeometryValidator
from didgeridoo_optimizer.materials.models import Material, AcousticParameter
from didgeridoo_optimizer.pipeline.fixed_design import load_fixed_context, _software_source
from didgeridoo_optimizer.reporting.forced_response import SCHEMA, SCHEMA_V2, SOURCE_UNITS, NOT_EXECUTED, UNITS, model_payload, export_bundle

PROFILES = {
    'cylinder': [(1.21,.03,.03)],
    'expansion': [(.455,.03,.03),(.10,.03,.05),(.10,.05,.05),(.10,.05,.03),(.455,.03,.03)],
    'constriction': [(.455,.03,.03),(.10,.03,.02),(.10,.02,.02),(.10,.02,.03),(.455,.03,.03)],
    'body_bell': [(1.2,.038,.038),(.2,.038,.12)],
}
AIR_REFERENCES = {'ck_dry20': CK_DRY_20C, 'ck_dry25': CK_DRY_25C}


def synthetic_material():
    def p(value):
        return AcousticParameter(value, value, value, 'inferred', 'low')
    return Material('io_test', 'io_test', 'synthetic', 'test_only', None, p(2.4), p(0.), p(0.),
                    'test_only', 'test_only', 'test_only', True, True, True,
                    notes='Synthetic comparison only; no calibration or material adoption')


def builtin_design(name):
    segments = [dict(kind='cylinder' if di == do else 'cone', length_cm=100*length,
                     d_in_cm=100*di, d_out_cm=100*do, material_id='io_test')
                for length, di, do in PROFILES[name]]
    return DesignBuilder().build(dict(id=name, segments=segments, metadata={'nature': 'synthetic'}))


def termination_assumptions(design):
    last = design.segments[-1]
    transposed = last.kind not in {'cylinder', 'mouthpiece'} or not last.is_uniform
    return dict(last_physical_segment_kind=last.kind,
                interpretation=('charge cylindrique transposée au pavillon ou profil variable' if transposed
                                else 'sortie cylindrique; montage extérieur non établi'),
                wall_thickness='unknown', exterior_environment='unknown',
                physical_radius_m=last.d_out_cm/200,
                model_geometry_validated=False)


def source_identity():
    identity = _software_source()
    script = Path(__file__).resolve()
    root = script.parents[1]
    try:
        if root != Path(fr.__file__).resolve().parents[2]:
            raise ValueError('Tool and package do not share a source root')
        check = subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch','--',script.relative_to(root).as_posix()],
                               check=True, capture_output=True, text=True, timeout=5)
        if check.stdout.strip() != script.relative_to(root).as_posix():
            raise ValueError('Tool source is not tracked')
    except (OSError, subprocess.SubprocessError, ValueError):
        identity = dict(sha=None, origin='unknown: forced-response tool source membership not established', working_tree_dirty=None)
    identity['tool_sha256'] = hashlib.sha256(script.read_bytes()).hexdigest()
    return identity


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    p = Parser(description=__doc__)
    p.add_argument('--config'); p.add_argument('--design')
    p.add_argument('--case', choices=PROFILES, nargs='+')
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--flow-peak-m3-s', type=float)
    source.add_argument('--pressure-peak-pa', type=float)
    source.add_argument('--thevenin-pressure-peak-pa', type=float)
    p.add_argument('--source-resistance-pa-s-m3', type=float)
    p.add_argument('--loss-model', choices=['legacy','zk','both'], default='legacy')
    p.add_argument('--radiation-model', choices=RADIATION_NAMES, default='legacy')
    p.add_argument('--air-reference', choices=AIR_REFERENCES)
    p.add_argument('--f-min', type=float); p.add_argument('--f-max', type=float)
    p.add_argument('--points', type=int); p.add_argument('--h-cm', type=float)
    p.add_argument('--output-dir', required=True); p.add_argument('--dry-run', action='store_true')
    return p


def run(args):
    radiation_model = get_radiation_model(args.radiation_model)
    if bool(args.config) != bool(args.design) or (args.config and args.case):
        raise ValueError('Use CONFIG+DESIGN together, or built-in cases')
    if args.loss_model in {'zk','both'} and not args.air_reference:
        raise ValueError('--air-reference is required for zk/both')
    thevenin = args.thevenin_pressure_peak_pa is not None
    resistance = args.source_resistance_pa_s_m3
    if thevenin:
        if resistance is None:
            raise ValueError('--source-resistance-pa-s-m3 is required for thevenin')
        resistance = fr.complex_vector(resistance, 1, 'source resistance')[0]
        if resistance.imag != 0 or resistance.real < 0:
            raise ValueError('source resistance: finite real Rs>=0 required')
        kind = 'thevenin_pressure'
        amplitude = fr.real_positive(args.thevenin_pressure_peak_pa, 'peak source')
    else:
        if resistance is not None:
            raise ValueError('--source-resistance-pa-s-m3 requires --thevenin-pressure-peak-pa (including Rs=0)')
        kind = 'volume_flow' if args.flow_peak_m3_s is not None else 'pressure'
        amplitude = fr.real_positive(args.flow_peak_m3_s if kind == 'volume_flow' else args.pressure_peak_pa, 'peak source')
    schema = SCHEMA_V2 if thevenin else SCHEMA
    context = load_fixed_context(args.config, args.design) if args.config else None
    if context:
        original = dict(config=context['config'], effective_parameters=context['effective_parameters'],
                        provenance=context['provenance'], materials_used=context['materials_used'],
                        config_schema_version=context['config_schema_version'], config_schema_status=context['config_schema_status'])
        frequency_config = context['effective_parameters']['frequency_analysis']
        designs, materials = [context['design']], context['material_db']
        materials_used, warnings = context['materials_used'], context['warnings']
        original_air = AirProperties.from_config(context['config'])
    else:
        original = None
        frequency_config = dict(f_min_hz=40., f_max_hz=1000., n_points=128, discretization_max_segment_cm=1.)
        designs = [builtin_design(name) for name in (args.case or ['cylinder'])]
        if len(designs) != len(set(d.id for d in designs)):
            raise ValueError('Duplicate built-in case')
        material = synthetic_material(); materials = {material.id: material}
        materials_used, warnings = {material.id: material.as_dict()}, []
        original_air = fr.DEFAULT_AIR
    state = AIR_REFERENCES.get(args.air_reference)
    air = state.as_air_properties() if state else original_air
    effective_air = asdict(state) if state else asdict(air)
    effective_air['thermoviscous_parameters_used'] = args.loss_model in {'zk','both'}
    fmin = fr.real_positive(args.f_min if args.f_min is not None else frequency_config['f_min_hz'], 'f_min')
    fmax = fr.real_positive(args.f_max if args.f_max is not None else frequency_config['f_max_hz'], 'f_max')
    count = args.points if args.points is not None else frequency_config['n_points']
    h = fr.real_positive(args.h_cm if args.h_cm is not None else frequency_config['discretization_max_segment_cm'], 'h_cm')
    if fmax <= fmin or isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= fr.MAX_FREQUENCIES:
        raise ValueError('Require f_max>f_min and 2<=points<=20000')
    # All case/source/grid/mesh budgets precede frequency allocation or propagation.
    prepared = []
    for design in designs:
        errors = GeometryValidator().validate(design, context['config'] if context else {})
        if errors:
            raise ValueError('Geometry: '+'; '.join(errors))
        mesh = fr.prepare_mesh(design, h, count)
        prepared.append((design, mesh))
    output = Path(args.output_dir).resolve()
    if output.exists() and not output.is_dir():
        raise NotADirectoryError('output-dir is not a directory')
    for name in ('forced_response.json','forced_response.csv','forced_response.txt'):
        if (output/name).exists():
            raise FileExistsError('Refusing to overwrite forced-response artifacts')
    identity = source_identity()
    effective = dict(air=effective_air, original_air=asdict(original_air),
                     air_substitution='explicit diagnostic memory-only' if state else 'none; config/DEFAULT_AIR',
                     f_min_hz=fmin, f_max_hz=fmax, n_points=count, h_cm=h,
                     radiation=radiation_model.describe(),
                     radiation_selection='explicit diagnostic option; original CONFIG unchanged')
    if args.dry_run:
        return dict(ok=True, dry_run=True, output_created=False, effective=effective, provenance=identity,
                    terminations=[termination_assumptions(design) for design, _ in prepared],
                    segment_counts=[len(mesh.segments) for _, mesh in prepared], not_executed=NOT_EXECUTED)
    frequency = fr.frequencies(np.linspace(fmin, fmax, count))
    models = [LegacyBetaLossModel()] if args.loss_model == 'legacy' else [ZwikkerKostenLossModel(state)]
    if args.loss_model == 'both':
        models.insert(0, LegacyBetaLossModel())
    cases = []
    for design, mesh in prepared:
        results = []
        radius = design.segments[-1].d_out_cm/200
        zref = fr.characteristic_impedance(air.rho, air.c, fr.area_from_diameter(design.segments[0].d_in_cm/100))
        for model in models:
            started = time.perf_counter()
            transfer = fr.loaded_transfer(frequency, mesh, materials, air, exit_radius_m=radius, loss_model=model, zref=zref,
                                          radiation_model=radiation_model)
            transfer['radiation']['termination'] = termination_assumptions(design)
            elapsed = time.perf_counter()-started
            response = (apply_thevenin_source(transfer, amplitude, resistance) if thevenin
                        else fr.apply_source(transfer, kind, amplitude))
            results.append(model_payload(transfer, response, elapsed))
        cases.append(dict(nature='user supplied; no experimental status inferred' if context else 'synthetic',
                          physical_design=design.as_dict(), analysis_design=mesh.as_dict(), models=results))
    payload = dict(schema=schema, convention='exp(+j omega t), forward exp(-j k x), U1/U2 toward outlet; peak amplitudes',
                   provenance=identity, original_context=original, effective_parameters=effective,
                   materials_used=materials_used, units=UNITS | SOURCE_UNITS if thevenin else UNITS, warnings=warnings, cases=cases,
                   assumptions=['linear resting air; circular 1D sections', 'existing material statuses unchanged',
                                'radiation boundary: '+radiation_model.describe()['name']+'; see reference band and mounting assumptions; no far field',
                                'no broadband power sum'],
                   not_executed=NOT_EXECUTED)
    exports = export_bundle(payload, output)
    return dict(ok=True, dry_run=False, schema=schema, cases=len(cases), models_per_case=len(models), exports=exports)


def main(argv=None):
    try:
        result = run(parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, ArithmeticError, yaml.YAMLError) as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False, allow_nan=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
