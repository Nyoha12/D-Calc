"""Strict portable prescribed-flow bundles; offline reading never runs physics."""
from __future__ import annotations

import csv
import hashlib
import io
import math
import os
from pathlib import Path
import sys

_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()

from .nonlinear_onset import source_fingerprints, strict_json
from .regime_reference import read_json_source, read_json, safe_path, atomic_bytes
from ..nonlinear.passive_resonator import digest, integer, real
from ..nonlinear.passive_state import verify_loaded_sources, bounded_json, SCHEMA as MODEL_SCHEMA, STATE_SCHEMA, validate_parameter_data

SCHEMA = 'dcalc.state_port_reference.v1'
ROOT = Path(__file__).resolve().parents[2]
LIMITS = [
    'Réponse numérique à débit imposé, début au repos ; aucune validation instrumentale.',
    'Pression au milieu du pas, états et énergie aux frontières du pas.',
    'Passivité structurelle et stabilité numérique sont distinctes de la fidélité acoustique.',
    'Aucun fit, TMM, calibration ou promotion ; aucun raccord played_search/register_target.',
]
FIELDS = ('index','start_time_s','midpoint_time_s','end_time_s','flow_m3_s',
          'pressure_pa','energy_old_j','energy_j','work_j','dissipation_j','balance_error_j')


def sources():
    verified, _ = verify_loaded_sources()
    result = source_fingerprints()
    result.update(verified)
    paths = {ROOT/'tools/state_port_reference.py'}
    for name, module in tuple(sys.modules.items()):
        if name.startswith('tools.') or (name=='__main__' and Path(getattr(module,'__file__','')).name=='state_port_reference.py'):
            paths.add(Path(module.__file__).resolve())
    for path in paths:
        if not path.is_relative_to(ROOT) or path.suffix!='.py' or not path.is_file():
            raise ValueError('Mixed producer source roots')
        result[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def summary(payload):
    return '\n'.join(['STATE-PORT-01 — expérience numérique à débit imposé',
        f"État : {payload['status']} ; {len(payload['samples'])}/{payload['requested_steps']} pas ; fs={payload['sample_rate_hz']} Hz.",
        f"Feedthrough : {payload['model_metadata']['feedthrough']['value']:.12g} Pa.s/m³ ; DC : {payload['model_metadata']['dc']['value']:.12g} Pa.s/m³.",
        f"Énergie finale : {payload['final_energy_j']:.12g} J ; travail : {payload['work_j']:.12g} J ; dissipation : {payload['dissipation_j']:.12g} J.",
        f"Défaut cumulé : {payload['balance_error_j']:.6g} J.", *LIMITS, ''])


def csv_text(payload):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(payload['samples'])
    return stream.getvalue()


def write_result(output, payload, *, partial=False):
    verify_loaded_sources()
    out = Path(output)
    envelope = dict(payload=payload, sha256=digest(payload))
    if len(strict_json(envelope).encode())>4*1024**2:
        raise ValueError('JSON result resource budget exceeded')
    if partial:
        atomic_bytes(out/f"partial-{len(payload['samples']):04d}.json", strict_json(envelope).encode())
        verify_loaded_sources()
        return
    files = {'result.json':strict_json(envelope), 'samples.csv':csv_text(payload),
             'summary.md':summary(payload)}
    for name, text in files.items():
        atomic_bytes(out/name, text.encode())
    manifest = {name:hashlib.sha256(text.encode()).hexdigest() for name,text in files.items()}
    atomic_bytes(out/'manifest.json', strict_json(dict(schema=SCHEMA, files=manifest)).encode())
    verify_loaded_sources()


def read_payload(output):
    """Hash/shape/index checks only; original inputs and producer need not exist."""
    verify_loaded_sources()
    out = safe_path(output)
    manifest = read_json(out/'manifest.json')
    if (type(manifest) is not dict or set(manifest)!={'schema','files'} or manifest['schema']!=SCHEMA
            or type(manifest['files']) is not dict or set(manifest['files'])!={'result.json','samples.csv','summary.md'}):
        raise ValueError('Invalid bundle manifest')
    for name, expected in manifest['files'].items():
        path = safe_path(out/name)
        if not path.is_file() or path.stat().st_size>4*1024**2:
            raise ValueError('Result file outside budget')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Result file fingerprint mismatch')
    data = read_json(out/'result.json')
    if type(data) is not dict or set(data)!={'payload','sha256'} or data['sha256']!=digest(data['payload']):
        raise ValueError('Result checksum mismatch')
    p = data['payload']
    if type(p) is not dict: raise ValueError('Result mapping required')
    if p.get('schema')!=SCHEMA or p.get('initial_condition')!='rest' or p.get('pressure_port')!='midpoint':
        raise ValueError('Invalid result semantics')
    validate_payload(p)
    if (out/'samples.csv').read_text()!=csv_text(p) or (out/'summary.md').read_text()!=summary(p):
        raise ValueError('Derived result exports mismatch')
    verify_loaded_sources()
    return p


def validate_payload(p):
    required = {'schema','status','reason','pressure_port','sample_rate_hz','initial_condition',
        'requested_steps','model_parameters','model_metadata','initial_state','physical_validation',
        'units','limits','samples','final_state','final_time_s','final_energy_j','work_j',
        'dissipation_j','balance_error_j','provenance'}
    if set(p)!=required or p['status'] not in {'resolved','partial'} or p['physical_validation']!='not_validated':
        raise ValueError('Invalid result schema/status')
    if (p['reason'] is not None and type(p['reason']) is not str) or (p['status']=='resolved' and p['reason'] is not None):
        raise ValueError('Invalid result reason')
    fs=integer(p['sample_rate_hz'],'sample_rate_hz',1,12000)
    count=integer(p['requested_steps'],'requested_steps',1,min(2400,int(fs*.2)))
    rows=p['samples']
    if type(rows) is not list or len(rows)>count or (p['status']=='resolved' and len(rows)!=count):
        raise ValueError('Incomplete resolved result')
    if p['units']!=dict(flow='m^3/s',pressure='Pa',energy='J',time='s') or p['limits']!=LIMITS:
        raise ValueError('Invalid result units/limits')
    model=p['model_parameters'];validate_parameter_data(model)
    if type(model) is not dict or model.get('schema')!=MODEL_SCHEMA or model.get('sample_rate_hz')!=fs:
        raise ValueError('Invalid model identity')
    n=len(model['g']);integer(n,'state_count',0,128)
    meta=p['model_metadata'];bounded_json(meta)
    meta_keys={'schema','state_layout','state_count','pressure_port','coefficient_domain','passivity',
               'asymptotic_stability','stability_roundoff_guard','feedthrough','dc','coefficient_origin','physical_validation'}
    if (type(meta) is not dict or set(meta)!=meta_keys or meta['schema']!=MODEL_SCHEMA
            or type(meta['state_count']) is not int or meta['state_count']!=n or meta['state_layout']!='flat_x'
            or meta['pressure_port']!='midpoint' or meta['coefficient_domain']!=model['domain']
            or meta['coefficient_origin']!=model['coefficient_origin'] or meta['physical_validation']!='not_validated'
            or meta['passivity']!='structural_L_LT' or meta['asymptotic_stability']!='strict_numerical_check'):
        raise ValueError('Invalid state metadata')
    real(meta['stability_roundoff_guard'],'stability_roundoff_guard',minimum=0.)
    for key in ('dc','feedthrough'):
        item=meta[key]
        if type(item) is not dict or set(item)!={'value','origin'} or type(item['origin']) is not str or not item['origin']:
            raise ValueError('Invalid DC/feedthrough provenance')
        real(item['value'],key,minimum=0.)
    for key in ('initial_state','final_state'):
        state=p[key];bounded_json(state)
        if type(state) is not dict or set(state)!={'payload','sha256'} or state['sha256']!=digest(state['payload']):
            raise ValueError('Invalid state checksum')
        q=state['payload']
        if (type(q) is not dict or set(q)!={'schema','model_sha256','x','last_dissipation_w'}
                or q['schema']!=STATE_SCHEMA or q['model_sha256']!=digest(model)
                or type(q['x']) is not list or len(q['x'])!=n):
            raise ValueError('Invalid state identity/shape')
        for v in q['x']: real(v,'state')
        real(q['last_dissipation_w'],'loss',minimum=0.)
    initial=p['initial_state']['payload']
    if any(v!=0 for v in initial['x']) or initial['last_dissipation_w']!=0:
        raise ValueError('Initial state must be at rest')
    previous=0.
    for i,row in enumerate(rows):
        if type(row) is not dict or set(row)!=set(FIELDS):raise ValueError('Invalid sample schema')
        integer(row['index'],'index',i,i)
        for key in FIELDS[1:]:real(row[key],key)
        if (row['start_time_s']!=i/fs or row['midpoint_time_s']!=(i+.5)/fs or row['end_time_s']!=(i+1)/fs
                or row['energy_old_j']!=previous or row['energy_j']<0 or row['dissipation_j']<0
                or row['work_j']!=row['pressure_pa']*row['flow_m3_s']/fs
                or row['balance_error_j']!=row['energy_j']-row['energy_old_j']-row['work_j']+row['dissipation_j']):
            raise ValueError('Inconsistent sample times/work/energy')
        previous=row['energy_j']
    for key in ('final_time_s','final_energy_j','work_j','dissipation_j','balance_error_j'):real(p[key],key)
    if (p['final_time_s']!=len(rows)/fs or p['final_energy_j']!=previous
            or p['work_j']!=math.fsum(r['work_j'] for r in rows)
            or p['dissipation_j']!=math.fsum(r['dissipation_j'] for r in rows)
            or p['balance_error_j']!=p['final_energy_j']-p['work_j']+p['dissipation_j']):
        raise ValueError('Inconsistent result summary')
    scale=model['Zscale']/model['omega_ref']
    state_energy=math.fsum((scale*x)*x for x in p['final_state']['payload']['x'])/2
    if not math.isfinite(state_energy) or not math.isclose(state_energy,p['final_energy_j'],rel_tol=2e-13,abs_tol=0.):
        raise ValueError('Final state energy mismatch')
    if rows and p['final_state']['payload']['last_dissipation_w']/fs!=rows[-1]['dissipation_j']:
        raise ValueError('Final loss mismatch')
    if not rows and p['final_state']!=p['initial_state']:raise ValueError('Empty result state mismatch')
    inputs=p['provenance']['inputs']
    signal=inputs['signal']['requested'];bounded_json(signal)
    if (type(signal) is not dict or set(signal)!={'schema','units','sample_rate_hz','initial_condition','flow_m3_s','provenance'}
            or signal['schema']!='dcalc.prescribed_flow.v1' or signal['units']!={'flow':'m^3/s'}
            or type(signal['sample_rate_hz']) is not int or signal['sample_rate_hz']!=fs
            or signal['initial_condition']!='rest' or type(signal['provenance']) is not dict
            or type(signal['flow_m3_s']) is not list or len(signal['flow_m3_s'])!=count):
        raise ValueError('Invalid recorded input signal')
    for value in signal['flow_m3_s']:real(value,'flow_m3_s')
    if signal['flow_m3_s'][:len(rows)]!=[r['flow_m3_s'] for r in rows]:
        raise ValueError('Result differs from prescribed input')


def read_result(output):
    verify_loaded_sources()
    out=safe_path(output)
    p=read_payload(out)
    receipt, source=read_json_source(out/'execution.json')
    closed=read_json(out/'execution.closed.json')
    if (type(closed) is not dict or set(closed)!={'execution_sha256','cancelled'}
            or closed['execution_sha256']!=source['sha256'] or type(closed['cancelled']) is not bool):
        raise ValueError('Missing/inconsistent execution closure')
    if (type(receipt) is not dict or type(receipt.get('ok')) is not bool
            or receipt.get('result_sha256')!=hashlib.sha256((out/'result.json').read_bytes()).hexdigest()
            or receipt.get('child',{}).get('reaped') is not True
            or receipt.get('child',{}).get('collected') is not True
            or receipt.get('child',{}).get('pipes_closed') is not True
            or (receipt.get('ok') and (type(receipt['child'].get('exit_code')) is not int
                or receipt['child']['exit_code']!=0 or p['status']!='resolved'))):
        raise ValueError('Invalid execution receipt')
    verify_loaded_sources()
    if os.path.lexists(out/'execution.cancelled.json'):
        cancelled=read_json(out/'execution.cancelled.json')
        if cancelled!={'execution_sha256':source['sha256'],'ok':False}:raise ValueError('Invalid cancellation')
        return dict(ok=False,result=p,execution=receipt,cancelled=True)
    if closed['cancelled']:raise ValueError('Missing cancellation marker')
    return dict(ok=receipt['ok'], result=p, execution=receipt)
