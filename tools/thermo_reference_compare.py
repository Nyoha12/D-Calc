"""Bounded experimental ZK/legacy diagnostic, distinct from fixed-design exports.

Run as ``python -B -m tools.thermo_reference_compare --help`` from the checkout.
Network acquisition is explicit, public, bounded and never part of unit tests.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
import zipfile

import numpy as np

from didgeridoo_optimizer.acoustics.losses import LegacyBetaLossModel
from didgeridoo_optimizer.acoustics.thermoviscous import (
    CK_DRY_20C, CK_DRY_25C, MATERIAL_WARNING, ThermoviscousAir, ZwikkerKostenLossModel, positive_vector,
)
from didgeridoo_optimizer.acoustics.transfer_matrix import input_impedance, segment_matrix
from didgeridoo_optimizer.geometry.builders import DesignBuilder
from didgeridoo_optimizer.geometry.discretization import GeometryDiscretizer
from didgeridoo_optimizer.materials.models import AcousticParameter, Material
from didgeridoo_optimizer.pipeline.fixed_design import load_fixed_context, _software_source

SCHEMA = 'dcalc.thermo.comparison.v1'
RECORD = 'https://zenodo.org/records/20024938'
ARCHIVE_URL = RECORD + '/files/Raw_data.zip?download=1'
ARCHIVE_MD5 = '392ff6e6e5c29985513c5dfd5d26998b'
MAX_ARCHIVE_BYTES = 200_000_000
MAX_EXPANDED_BYTES = 800_000_000
MAX_MEMBER_BYTES = 50_000_000
AIR_REFERENCES = {'ck_dry20': CK_DRY_20C, 'ck_dry25': CK_DRY_25C}
LIMITS = [MATERIAL_WARNING,
          'Synthetic comparisons are not measurements, played FFTs, or material calibration.',
          'The open-end radiation law is the unchanged low-frequency approximation; no full-band validation.',
          'Peak extraction is bounded to bracketed modes; an unresolved Q is null with a reason.',
          'Closed termination is diagnostic-only and does not extend the production radiation API.']


def synthetic_material():
    def parameter(value):
        return AcousticParameter(value, value, value, 'inferred', 'low')
    return Material('thermo_test', 'thermo_test', 'test_only', 'test_only', None,
                    parameter(2.4), parameter(0.), parameter(0.),
                    'test_only', 'test_only', 'test_only', True, True, True,
                    notes='Synthetic beta=2.4; no material database mutation or promotion')


def builtin_design(case):
    def segment(kind, length, inlet, outlet):
        return dict(kind=kind, length_cm=length, d_in_cm=inlet, d_out_cm=outlet, material_id='thermo_test')
    if case == 'cylinder':
        segments = [segment('cylinder', 121., 3., 3.)]
    elif case == 'body_bell':
        segments = [segment('cylinder', 120., 3.8, 3.8), segment('flare_conical', 20., 3.8, 12.)]
    elif case == 'closed_cylinder':
        segments = [segment('cylinder', 18., 1.4, 1.4)]
    else:
        raise ValueError(f'Unknown built-in case: {case}')
    return DesignBuilder().build(dict(id=case, segments=segments))


def make_evaluator(design, materials, state, model, h_cm, *, closed=False):
    h = float(positive_vector(h_cm, 'h_cm'))
    if sum(np.ceil(s.length_cm/h) for s in design.segments) > 10000:
        raise ValueError('Diagnostic budget exceeded: maximum 10000 slices')
    mesh = GeometryDiscretizer().discretize(design, h)
    radius = design.segments[-1].d_out_cm / 200
    air = state.as_air_properties()
    if closed and (len(design.segments) != 1 or not design.segments[0].is_uniform):
        raise ValueError('Closed diagnostic requires one uniform physical cylinder')

    def evaluate(frequency):
        freq = positive_vector(frequency, 'frequency_hz')
        if freq.ndim != 1 or freq.size > 100000:
            raise ValueError('Diagnostic frequency grid must be 1D with at most 100000 points')
        if closed:
            segment = design.segments[0]
            diameter = segment.d_in_cm/100
            mat = materials.get(segment.material_id)
            z0 = state.rho*state.c/(np.pi*(diameter/2)**2)
            loss = model.evaluate(2*np.pi*freq, diameter, mat, z0, air)
            # U_out=0, hence Zin=A/C; no radiation evaluation or extra end length.
            a, _, c, _ = segment_matrix(loss.zc_complex, loss.k_complex, segment.length_cm/100)
            zin = a/c
        else:
            zin = input_impedance(freq, mesh, materials, air, exit_radius_m=radius, loss_model=model)
        if not np.all(np.isfinite(zin)):
            raise ArithmeticError('Nonfinite diagnostic Zin; no sanitized success')
        return zin
    return evaluate, mesh, radius


def _crossing(freq, values, index):
    x0, x1 = freq[index:index+2]
    y0, y1 = values[index:index+2]
    return float(x0-y0*(x1-x0)/(y1-y0))


def mode_metrics(freq, zin):
    """Metrics for a single bracketed mode, without extrapolating absent data."""
    magnitude = np.abs(zin)
    index = int(np.argmax(magnitude))
    if index in (0, len(freq)-1):
        return None
    # Quadratic log-magnitude vertex, evaluated only inside adjacent samples.
    offset = np.polyfit(freq[index-1:index+2]-freq[index], np.log(magnitude[index-1:index+2]), 2)
    dx = -offset[1]/(2*offset[0]) if offset[0] < 0 else 0.
    peak_f = float(freq[index]+np.clip(dx, freq[index-1]-freq[index], freq[index+1]-freq[index]))
    peak_mag = float(np.exp(np.polyval(offset, peak_f-freq[index])))
    phase = float(np.interp(peak_f, freq, np.unwrap(np.angle(zin))))
    half = magnitude-peak_mag/np.sqrt(2)
    left = np.flatnonzero((half[:-1] <= 0) & (half[1:] > 0))
    right = np.flatnonzero((half[:-1] >= 0) & (half[1:] < 0))
    left = left[left < index]
    right = right[right >= index]
    q, width, reason = None, None, 'Half-power crossings not bracketed'
    if left.size and right.size:
        il, ir = int(left[-1]), int(right[0])
        width = _crossing(freq, half, ir)-_crossing(freq, half, il)
        if ir-il >= 11 and width > 0:
            q, reason = peak_f/width, None
        else:
            reason = 'Fewer than 11 grid intervals across half-power width'
    # Resonant zero: downward phase crossing, not an antiresonance crossing.
    crossings = np.flatnonzero((zin.imag[:-1] > 0) & (zin.imag[1:] <= 0) & (zin.real[:-1] > 0))
    zero = min((_crossing(freq, zin.imag, i) for i in crossings), key=lambda f: abs(f-peak_f), default=None)
    return dict(frequency_max_abs_hz=peak_f, magnitude_pa_s_m3=peak_mag, phase_rad=phase,
                q_half_power=q, width_hz=width, q_unavailable_reason=reason,
                resonant_phase_zero_hz=zero,
                phase_zero_method='linear ImZ crossing; distinct from published +/-5-cent phase-fit protocol')


def extract_modes(evaluate, frequency, zin, *, max_modes=3, refinement_points=(1025, 2049)):
    mag = np.abs(zin)
    peaks = np.flatnonzero((mag[1:-1] > mag[:-2]) & (mag[1:-1] >= mag[2:]))+1
    results = []
    for ordinal, index in enumerate(peaks[:max_modes]):
        lo = 0 if ordinal == 0 else (int(peaks[ordinal-1])+int(index))//2
        hi = len(frequency)-1 if ordinal+1 == len(peaks) else (int(index)+int(peaks[ordinal+1]))//2
        levels = []
        for points in refinement_points:
            local_f = np.linspace(frequency[lo], frequency[hi], points)
            metrics = mode_metrics(local_f, evaluate(local_f))
            if metrics is not None:
                levels.append(dict(points=points, step_hz=float(local_f[1]-local_f[0]), **metrics))
        if levels:
            results.append(dict(mode_ordinal=ordinal+1, bracket_hz=[float(frequency[lo]), float(frequency[hi])],
                                frequency_refinement=levels, **levels[-1]))
    return results


def source_identity():
    source = _software_source()
    root = Path(__file__).resolve().parents[1]
    relative = Path(__file__).resolve().relative_to(root).as_posix()
    try:
        subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch','--',relative], check=True, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        source = dict(sha=None, origin='unknown: diagnostic script not established as tracked source', working_tree_dirty=None)
    source['diagnostic_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return source


def compare_case(design, materials, state, frequency, steps, *, closed=False, max_modes=3, refinement_points=(1025,2049)):
    result = dict(design=design.as_dict(), termination='closed U=0' if closed else 'legacy low-frequency radiation',
                  propagation_method='analytic uniform cylinder A/C; h does not refine this reference' if closed else 'production midpoint cylinders with fixed physical outlet',
                  materials_used={key:materials.get(key).as_dict() for key in dict.fromkeys(design.material_ids)}, models=[])
    for model in (LegacyBetaLossModel(), ZwikkerKostenLossModel(state)):
        levels = []
        for h in steps:
            evaluate, mesh, radius = make_evaluator(design, materials, state, model, h, closed=closed)
            started = time.perf_counter()
            zin = evaluate(frequency)
            curve_seconds = time.perf_counter()-started
            started = time.perf_counter()
            modes = extract_modes(evaluate, frequency, zin, max_modes=max_modes, refinement_points=refinement_points)
            levels.append(dict(h_cm=h, segment_count=len(mesh.segments), exit_radius_m=radius,
                               frequency_hz=frequency.tolist(), zin_real=zin.real.tolist(), zin_imag=zin.imag.tolist(),
                               curve_seconds=curve_seconds, extraction_seconds=time.perf_counter()-started, modes=modes))
        result['models'].append(dict(model=model.name, levels=levels))
    result['mode_matching'] = 'Frequency-ordered bracketed modes, limited to first max_modes; compare the same ordinal, no fitted coefficients.'
    return result


def write_bundle(payload, output_dir, *, stem='thermo_comparison'):
    output = Path(output_dir)
    targets = [output/(stem+suffix) for suffix in ('.json','.csv','.txt')]
    if any(path.exists() for path in targets):
        raise FileExistsError('Diagnostic output already exists; choose a new directory or stem')
    # Validate strict serialization before creating directories.
    encoded = json.dumps(payload, indent=2, allow_nan=False, ensure_ascii=False)
    table = io.StringIO(newline='')
    writer = csv.writer(table)
    writer.writerow(['case','model','h_cm','frequency_hz','real_z_pa_s_m3','imag_z_pa_s_m3'])
    lines = [SCHEMA, 'No optimization, calibration, material promotion or experimental validation.',
             'Air substitution (diagnostic memory only): '+json.dumps(payload.get('air', {}), allow_nan=False)]
    for case in payload.get('cases', []):
        for model in case['models']:
            for level in model['levels']:
                writer.writerows((case['design']['id'],model['model'],level['h_cm'],f,re,im) for f,re,im in zip(level['frequency_hz'],level['zin_real'],level['zin_imag'],strict=True))
                lines.append(f"{case['design']['id']} {model['model']} h={level['h_cm']} cm: curve={level['curve_seconds']:.6g}s; modes="+json.dumps(level['modes'],allow_nan=False))
    lines += payload.get('limits', [])
    output.mkdir(parents=True, exist_ok=True)
    for path, text in zip(targets, (encoded,table.getvalue(),'\n'.join(lines)+'\n')):
        with path.open('x', encoding='utf-8', newline='') as handle:
            handle.write(text)
    return targets


class _ZenodoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.netloc != 'zenodo.org':
            raise ValueError('Acquisition redirect outside cited Zenodo host refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_public(url, target, *, max_bytes, timeout_seconds=180, expected_md5=None):
    """One attempt; callers may explicitly make at most a second attempt.

    No credentials, scripts, cookies or external downloader are used. Partial
    evidence is retained on failure, never silently overwritten.
    """
    if not url.startswith('https://zenodo.org/'):
        raise ValueError('Acquisition is restricted to the cited public Zenodo host')
    target = Path(target)
    if target.exists():
        raise FileExistsError(target.name)
    started = time.monotonic()
    sha, md5 = hashlib.sha256(), hashlib.md5()
    total = 0
    opener = urllib.request.build_opener(_ZenodoRedirect())
    with opener.open(url, timeout=min(30, timeout_seconds)) as response:
        if not response.url.startswith('https://zenodo.org/'):
            raise ValueError('Unexpected acquisition redirect host')
        if int(response.headers.get('Content-Length', 0)) > max_bytes:
            raise ValueError('Download exceeds declared byte budget')
        with target.open('xb') as handle:
            while True:
                if time.monotonic()-started > timeout_seconds:
                    raise TimeoutError('Download wall-clock budget exceeded')
                data = response.read1(min(1024*1024, max_bytes-total+1))
                if not data:
                    break
                total += len(data)
                if total > max_bytes:
                    raise ValueError('Download exceeds byte budget')
                handle.write(data)
                sha.update(data)
                md5.update(data)
    if expected_md5 and md5.hexdigest() != expected_md5:
        raise ValueError('Downloaded MD5 differs from announced checksum')
    return dict(url=url, file=target.name, bytes=total, sha256=sha.hexdigest(), md5=md5.hexdigest(), seconds=time.monotonic()-started)


def inspect_archive(path):
    entries, names, total = [], set(), 0
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist()) > 10000:
            raise ValueError('Too many archive entries')
        for info in archive.infolist():
            # Python normalizes Windows separators when reading ZipInfo; check
            # the original central-directory spelling before accepting paths.
            name = info.orig_filename
            pure = PurePosixPath(name)
            raw_parts = name.rstrip('/').split('/')
            canonical = '/'.join(raw_parts).casefold()
            reserved = {'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}
            if (pure.is_absolute() or '..' in pure.parts or '\\' in name or ':' in name
                    or not pure.parts or canonical in names or info.flag_bits & 1
                    or any(not p or p in ('.','..') or p.endswith(('.', ' ')) or p.split('.')[0].casefold() in reserved or any(ord(c)<32 or c in '<>"|?*' for c in p) for p in raw_parts)
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError(f'Unsafe or ambiguous archive member: {name!r}')
            names.add(canonical)
            total += info.file_size
            if info.file_size > MAX_MEMBER_BYTES or total > MAX_EXPANDED_BYTES:
                raise ValueError('Archive expansion budget exceeded')
            entries.append(dict(path=name, bytes=info.file_size, compressed_bytes=info.compress_size, directory=info.is_dir()))
        files = {item['path'].casefold() for item in entries if not item['directory']}
        for item in entries:
            if any(str(parent).casefold() in files for parent in PurePosixPath(item['path']).parents):
                raise ValueError('Archive file/directory collision')
    return entries


def extract_selected(archive_path, output, members):
    entries = {item['path']:item for item in inspect_archive(archive_path)}
    output = Path(output).resolve()
    selected = []
    for member in members:
        if member not in entries or entries[member]['directory']:
            raise ValueError('Selected member is absent or a directory')
        dest = output.joinpath(*PurePosixPath(member).parts).resolve()
        if not dest.is_relative_to(output) or dest.exists():
            raise ValueError('Extraction collision or path outside destination')
        selected.append((member,dest))
    with zipfile.ZipFile(archive_path) as archive:
        result = []
        for member, dest in selected:
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(member)  # bounded size already checked; ZIP CRC verified
            with dest.open('xb') as handle:
                handle.write(data)
            result.append(dict(path=member, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    return result


def acquire_external(output_dir):
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError('Acquisition requires a new directory')
    output.mkdir(parents=True)
    report = dict(source=RECORD, doi='10.5281/zenodo.20024938', downloads=[], archive_attempts=0)
    try:
        for url, filename, budget in (( 'https://zenodo.org/api/records/20024938','metadata.json',2_000_000),
                                      (RECORD+'/files/README.md?download=1','README.md',100_000)):
            report['downloads'].append(download_public(url,output/filename,max_bytes=budget,timeout_seconds=30))
        metadata = json.loads((output/'metadata.json').read_text())
        report['licence'] = metadata.get('metadata',{}).get('license', 'unknown')
        report['archive_attempts'] = 1
        report['downloads'].append(download_public(ARCHIVE_URL,output/'Raw_data.zip',max_bytes=MAX_ARCHIVE_BYTES,expected_md5=ARCHIVE_MD5))
        report['entries'] = inspect_archive(output/'Raw_data.zip')
        report['status'] = 'downloaded and inspected; no extraction or script execution'
    except Exception as exc:
        report['status'] = 'incomplete'
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        with (output/'acquisition.json').open('x',encoding='utf-8') as handle:
            json.dump(report,handle,indent=2,allow_nan=False)
    return report


def read_external_curve(path, fiche):
    """No temperature/sign/normalization guessing; inputs must be established."""
    required = {'source','doi','version','relative_path','nature','sha256','units','normalization',
                'convention','geometry','environment','termination','licence','method','role'}
    missing = required-set(fiche)
    if missing:
        raise ValueError(f'External fiche missing fields: {sorted(missing)}')
    if fiche['role'] != 'held-out; no adjustment' or not isinstance(fiche['geometry'],dict) or not isinstance(fiche['environment'],dict):
        raise ValueError('External geometry/environment mappings and held-out; no adjustment role required')
    expected_units = 'Hz, dimensionless ReZ/ImZ' if fiche['normalization'] == 'rho*c/S' else 'Hz, Pa.s/m3 ReZ/ImZ'
    if fiche['units'] != expected_units:
        raise ValueError('External units contradict normalization')
    rel = PurePosixPath(fiche['relative_path'])
    if rel.is_absolute() or '..' in rel.parts or '\\' in str(rel) or ':' in str(rel):
        raise ValueError('External source path must be relative and unambiguous')
    if Path(path).stat().st_size > MAX_MEMBER_BYTES:
        raise ValueError('External curve exceeds file byte budget')
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != fiche['sha256']:
        raise ValueError('External curve fingerprint mismatch')
    if fiche['nature'] not in ('measurement','simulation') or fiche['termination'] != 'closed':
        raise ValueError('External comparison currently requires identified closed-tube data')
    if fiche['convention'] != 'exp(+j*omega*t)':
        raise ValueError('External convention is unknown or incompatible; no sign conversion guessed')
    values = np.loadtxt(io.BytesIO(data), comments='#')
    if values.ndim != 2 or values.shape[1] != 3 or not 3 <= values.shape[0] <= 100000 or not np.all(np.isfinite(values)):
        raise ValueError('External data must be finite frequency/ReZ/ImZ columns')
    frequency = values[:,0]
    if np.any(frequency <= 0) or np.any(np.diff(frequency) <= 0):
        raise ValueError('External frequency must be strictly positive/increasing')
    zin = values[:,1]+1j*values[:,2]
    if fiche['normalization'] == 'rho*c/S':
        env, geo = fiche['environment'], fiche['geometry']
        scale = float(positive_vector(env['rho'],'external rho'))*float(positive_vector(env['c'],'external c'))/(np.pi*(float(positive_vector(geo['normalization_diameter_m'],'normalization diameter'))/2)**2)
        zin *= scale
    elif fiche['normalization'] != 'dimensional Pa.s/m3':
        raise ValueError('Unknown external normalization')
    if not np.all(np.isfinite(zin)) or not np.any(zin):
        raise ValueError('External dimensional curve is nonfinite or identically zero')
    return frequency, zin


def benchmark_modes(frequency, zin, *, max_modes=3):
    """Published phase-fit definition, with an explicit resolution gate.

    Linear phase and quadratic log-magnitude fit within +/-5 cents of each
    downward resonant phase zero. Never invent the required >=11 observations.
    Q is a separate half-power observable, only if resolved in the data.
    """
    crossing = np.flatnonzero((zin.imag[:-1] > 0) & (zin.imag[1:] <= 0) & (zin.real[:-1] > 0))
    modes = []
    for ordinal, index in enumerate(crossing[:max_modes]):
        guess = _crossing(frequency,zin.imag,index)
        mask = (frequency >= guess*2**(-5/1200)) & (frequency <= guess*2**(5/1200))
        count = int(mask.sum())
        item = dict(mode_ordinal=ordinal+1, observed_window_samples=count, frequency_phase_zero_hz=None,
                    magnitude_at_phase_zero_pa_s_m3=None, q_half_power=None,
                    q_unavailable_reason='Magnitude mode not bracketed',
                    method='linear phase / quadratic log-magnitude fit, +/-5 cents, >=11 samples')
        if count < 11:
            item['unavailable_reason'] = 'Fewer than 11 observations in +/-5-cent phase window'
        else:
            x = frequency[mask]-guess
            slope, intercept = np.polyfit(x,np.unwrap(np.angle(zin[mask])),1)
            fzero = guess-intercept/slope
            if slope >= 0 or not frequency[mask][0] <= fzero <= frequency[mask][-1]:
                item['unavailable_reason'] = 'Fitted resonant phase zero outside sampled window'
            else:
                logmag = np.polyfit(x,np.log(abs(zin[mask])),2)
                item.update(frequency_phase_zero_hz=float(fzero),
                            magnitude_at_phase_zero_pa_s_m3=float(np.exp(np.polyval(logmag,fzero-guess))),unavailable_reason=None)
        # Closed-tube compliance diverges toward DC: the first mode must be
        # bracketed by valleys rather than selecting the largest DC sample.
        previous = 0 if ordinal == 0 else int(crossing[ordinal-1])+1
        following = len(frequency)-1 if ordinal+1 == len(crossing) else int(crossing[ordinal+1])
        lo = previous+int(np.argmin(abs(zin[previous:index+1])))
        hi = int(index)+1+int(np.argmin(abs(zin[index+1:following+1])))
        ordinary = mode_metrics(frequency[lo:hi+1],zin[lo:hi+1])
        if ordinary:
            item['q_half_power'] = ordinary['q_half_power']
            item['q_unavailable_reason'] = ordinary['q_unavailable_reason']
        modes.append(item)
    return modes


def compare_external(path, fiche, state):
    frequency, observed = read_external_curve(path, fiche)
    env, geometry = fiche['environment'], fiche['geometry']
    fields = ('rho','c','mu','kappa','cp','gamma','temperature_c','humidity_percent')
    if set(fields)-env.keys() or not {'diameter_m','length_m'} <= geometry.keys():
        raise ValueError('External geometry/air fields are not fully established')
    external_state = ThermoviscousAir(**{key:env[key] for key in fields},identifier='external_declared',provenance=str(fiche['source']))
    for key in fields:
        if not np.isclose(getattr(external_state,key),getattr(state,key),rtol=1e-7,atol=1e-10):
            raise ValueError(f'External {key} not established as matching selected air; no implicit correction')
    if fiche['nature'] == 'measurement' and not geometry.get('uncertainty'):
        raise ValueError('Measurement comparison requires declared geometry uncertainty')
    d = float(positive_vector(geometry['diameter_m'],'external diameter'))
    length = float(positive_vector(geometry['length_m'],'external length'))
    design = builtin_design('closed_cylinder')
    segment = design.segments[0]
    segment.length_cm, segment.d_in_cm, segment.d_out_cm = length*100,d*100,d*100
    design = DesignBuilder().build(design)
    material = synthetic_material()
    models = []
    for model in (LegacyBetaLossModel(), ZwikkerKostenLossModel(state)):
        evaluate, _, _ = make_evaluator(design,{material.id:material},state,model,1,closed=True)
        start = time.perf_counter()
        predicted = evaluate(frequency)
        scale = np.max(abs(observed))
        models.append(dict(model=model.name,seconds=time.perf_counter()-start,
                           relative_complex_l2=float(np.linalg.norm(predicted/scale-observed/scale)/np.linalg.norm(observed/scale)),
                           benchmark_modes=benchmark_modes(frequency,predicted),
                           predicted_real=predicted.real.tolist(),predicted_imag=predicted.imag.tolist()))
    return dict(fiche=fiche,frequency_hz=frequency.tolist(),observed_real=observed.real.tolist(),
                observed_imag=observed.imag.tolist(),observed_benchmark_modes=benchmark_modes(frequency,observed),models=models,
                limits='Held-out, no fitting; real geometry and environmental uncertainty are not eliminated by numerical agreement.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--air-reference',choices=tuple(AIR_REFERENCES))
    parser.add_argument('--case',choices=('all','cylinder','body_bell','closed_cylinder'),default='all')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--design',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--f-min',type=float,default=40.)
    parser.add_argument('--f-max',type=float,default=1200.)
    parser.add_argument('--points',type=int,default=1161)
    parser.add_argument('--spatial-steps',type=float,nargs='+',default=[1.,.5,.25])
    parser.add_argument('--max-modes',type=int,default=3)
    parser.add_argument('--acquire-external',action='store_true')
    parser.add_argument('--external-data',type=Path)
    parser.add_argument('--external-fiche',type=Path)
    args = parser.parse_args(argv)
    try:
        if args.acquire_external:
            report = acquire_external(args.output_dir)
            print(json.dumps({key:report[key] for key in ('status','archive_attempts','licence')}))
            return 0
        if not args.air_reference:
            raise ValueError('--air-reference is required: no silent air default')
        if any((args.output_dir/('thermo_comparison'+suffix)).exists() for suffix in ('.json','.csv','.txt')):
            raise FileExistsError('Diagnostic output already exists; choose a new directory')
        if bool(args.config) != bool(args.design):
            raise ValueError('--config and --design must be provided together')
        state = AIR_REFERENCES[args.air_reference]
        positive_vector([args.f_min,args.f_max,*args.spatial_steps],'analysis')
        if args.f_max <= args.f_min or not 3 <= args.points <= 100000 or not 0 <= args.max_modes <= 10 or not 1 <= len(args.spatial_steps) <= 4:
            raise ValueError('Invalid analysis bounds or diagnostic budget')
        context, cases = None, []
        if args.config:
            context = load_fixed_context(args.config,args.design)
            cfg = context['effective_parameters']['frequency_analysis']
            if cfg['n_points'] > 100000:
                raise ValueError('Diagnostic budget exceeded: maximum 100000 frequency points')
            frequency = np.linspace(cfg['f_min_hz'],cfg['f_max_hz'],cfg['n_points'])
            cases = [compare_case(context['design'],context['material_db'],state,frequency,args.spatial_steps,max_modes=args.max_modes)]
        else:
            frequency = np.linspace(args.f_min,args.f_max,args.points)
            mat = synthetic_material()
            for case in ('cylinder','body_bell','closed_cylinder') if args.case == 'all' else (args.case,):
                cases.append(compare_case(builtin_design(case),{mat.id:mat},state,frequency,args.spatial_steps,closed=case=='closed_cylinder',max_modes=args.max_modes))
        payload = dict(schema=SCHEMA,nature='synthetic' if context is None else 'user-supplied diagnostic; local only',
                       source=source_identity(),air=asdict(state),air_substitution='explicit diagnostic memory-only; original inputs unchanged',
                       original_context=None if context is None else dict(config=context['config'],effective_parameters=context['effective_parameters'],provenance=context['provenance'],materials_used=context['materials_used'],warnings=context['warnings']),
                       cases=cases,limits=LIMITS)
        if bool(args.external_data) != bool(args.external_fiche):
            raise ValueError('--external-data and --external-fiche must be supplied together')
        if args.external_data:
            payload['external'] = compare_external(args.external_data,json.loads(args.external_fiche.read_text(encoding='utf-8-sig')),state)
            payload['nature'] = 'mixed diagnostic: synthetic/internal cases and separately identified external reference'
        paths = write_bundle(payload,args.output_dir)
        print(json.dumps(dict(ok=True,air=asdict(state),files=[p.name for p in paths])))
        return 0
    except (ValueError, TypeError, KeyError, ArithmeticError, OSError, zipfile.BadZipFile) as exc:
        print(f'thermo diagnostic failed: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
