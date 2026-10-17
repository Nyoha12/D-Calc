"""Finite saved acoustic evidence, with bounded structural and scientific phases.

The v1 schema fixes frequencies to Hz and complex impedance targets to Pa.s/m^3.
Hashes bind documentary assertions to inputs; they do not authenticate acquisition.
No fitter, TMM, conversion, temporal stepping or certificate producer is called.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import io
import math
from pathlib import Path

_LOADED_SOURCE_PATH=Path(__file__).resolve()
_LOADED_SOURCE_SHA256=hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
import re
import zipfile

import numpy as np

from ..nonlinear import scalar_passivity_certificate as strict
from ..nonlinear.passive_fit import DEFAULT_GATES, metrics, spectrum
from ..nonlinear.passive_resonator import digest

SCHEMA = 'dcalc.state_register.evidence.v1'
MAX_BYTES = 4*1024**2
MAX_POINTS = 16384
MAX_TRAINING = 8192
MAX_BANDS = 16
MAX_BAND_POINTS = 4096
UNITS = dict(frequency_hz='Hz', impedance='Pa.s/m^3')
_FIELDS = {'schema','inputs','recipe','effective','physical_radius_m','source_sha256',
           'model_sha256','domain_hz','coefficient_domain','acquisition','gates',
           'bands','training','audit'}


def _exact(value, keys, name):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('Evidence '+name+' schema')
    return value


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Evidence '+name+' budget')
    return value


def _number(value, name, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('Evidence '+name+' finite number')
    return value


def _hash(value, name):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('Evidence '+name+' SHA256')
    return value


def _text(value, name):
    if type(value) is not str or not value.strip() or len(value) > 4096:
        raise ValueError('Evidence '+name+' text')
    return value


def _domain(value, name, fs):
    if type(value) is not list or len(value) != 2:
        raise ValueError('Evidence '+name+' interval')
    lo, hi = (_number(v, name, positive=True) for v in value)
    if not lo < hi < fs/2:
        raise ValueError('Evidence '+name+' outside positive Nyquist domain')
    return lo, hi


def _filename(value):
    if type(value) is not str or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\\.npz', value) is None:
        raise ValueError('Evidence NPZ must use a simple relative .npz filename')
    return value


def validate_manifest(manifest):
    """Pure bounded JSON semantics; no model or spectral array is constructed."""
    strict.bounded(manifest)
    m = _exact(manifest, _FIELDS, 'manifest')
    if m['schema'] != SCHEMA:
        raise ValueError('Evidence schema version')
    inputs = _exact(m['inputs'], {'config','design','materials','variant_rules'}, 'four inputs')
    for k, v in inputs.items(): _hash(v, k)
    for k in ('source_sha256','model_sha256'): _hash(m[k], k)
    r = _exact(m['recipe'], {'sample_rate_hz','h_cm','loss_model','radiation_model','air_reference'}, 'recipe')
    fs = _integer(r['sample_rate_hz'], 'sample rate', 1000, 12000)
    if not 0 < _number(r['h_cm'], 'h_cm', positive=True) <= 2:
        raise ValueError('Evidence mesh size')
    if r['loss_model'] not in ('zk','legacy'):
        raise ValueError('Evidence loss model')
    if ((r['loss_model'] == 'zk' and r['air_reference'] not in ('ck_dry20','ck_dry25'))
            or (r['loss_model'] == 'legacy' and r['air_reference'] is not None)):
        raise ValueError('Evidence air recipe')
    _text(r['radiation_model'], 'radiation model')
    effective = _exact(m['effective'], {'air','original_air','air_substitution','loss_model','radiation'}, 'effective conditions')
    if any(type(effective[k]) is not dict or not effective[k] for k in ('air','original_air','radiation')):
        raise ValueError('Evidence effective physical records')
    for k in ('air_substitution','loss_model'): _text(effective[k], k)
    _number(m['physical_radius_m'], 'physical radius', positive=True)
    if m['coefficient_domain'] not in ('continuous','discrete_prewarped'):
        raise ValueError('Evidence coefficient domain')
    lo, hi = _domain(m['domain_hz'], 'training domain', fs)
    acquisition = _exact(m['acquisition'], {'nature','provenance','date','sha'}, 'acquisition')
    if acquisition['nature'] not in ('simulation_tmm','measurement','synthetic'):
        raise ValueError('Evidence acquisition nature')
    _text(acquisition['provenance'], 'acquisition provenance')
    _text(acquisition['date'], 'acquisition date')
    try:
        if len(acquisition['date']) == 10: date.fromisoformat(acquisition['date'])
        else: datetime.fromisoformat(acquisition['date'].replace('Z','+00:00'))
    except ValueError as exc:
        raise ValueError('Evidence acquisition ISO date') from exc
    if type(acquisition['sha']) is not str or re.fullmatch('(?:[0-9a-f]{40}|[0-9a-f]{64})', acquisition['sha']) is None:
        raise ValueError('Evidence acquisition revision or data SHA')
    _exact(m['gates'], DEFAULT_GATES, 'native gates')
    for k, v in m['gates'].items():
        if _number(v, k, positive=True) != DEFAULT_GATES[k]:
            raise ValueError('Evidence native fidelity gate changed')
    train = _exact(m['training'], {'file','sha256','points'}, 'training')
    audit = _exact(m['audit'], {'file','sha256'}, 'audit')
    for record in (train, audit):
        _filename(record['file']); _hash(record['sha256'], 'NPZ')
    if train['file'] == audit['file']:
        raise ValueError('Evidence training and audit file must differ')
    points = _integer(train['points'], 'training points', 2, MAX_TRAINING)
    bands = m['bands']
    if type(bands) is not list or not 1 <= len(bands) <= MAX_BANDS:
        raise ValueError('Evidence bands budget')
    names = set(); in_domain = []; all_intervals = []
    for b in bands:
        _exact(b, {'id','role','domain_hz','points'}, 'band')
        if type(b['id']) is not str or re.fullmatch('[a-z][a-z0-9_]{0,31}', b['id']) is None or b['id'] in names:
            raise ValueError('Evidence unique band identifier')
        names.add(b['id'])
        points += _integer(b['points'], 'band points', 2, MAX_BAND_POINTS)
        a, z = _domain(b['domain_hz'], 'band', fs)
        all_intervals.append((a,z))
        if b['role'] == 'audit':
            if a < lo or z > hi: raise ValueError('Audit outside training domain')
            in_domain.append((a,z))
        elif b['role'] == 'extrapolation':
            if not (z <= lo or a >= hi):
                raise ValueError('Extrapolation overlaps accepted domain')
        else:
            raise ValueError('Evidence band role')
    if points > MAX_POINTS:
        raise ValueError('Evidence total point budget')
    ordered = sorted(all_intervals)
    if any(b > c for (_,b),(c,_) in zip(ordered, ordered[1:])):
        raise ValueError('Evidence overlapping band interiors')
    covered = sorted(in_domain)
    if not covered or covered[0][0] != lo or covered[-1][1] != hi or any(b != c for (_,b),(c,_) in zip(covered,covered[1:])):
        raise ValueError('Evidence audit bands must partition the entire declared domain without gaps')
    return m


def _npz_headers(raw, expected):
    """Check every NPY shape/dtype/byte count before np.load or data allocation."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            items = z.infolist()
            if len(items) != len(expected) or {i.filename for i in items} != set(expected):
                raise ValueError('Evidence NPZ inventory or duplicate names')
            if sum(i.file_size for i in items) > MAX_BYTES:
                raise ValueError('Evidence NPZ uncompressed byte cap')
            for item in items:
                shape, dtype = expected[item.filename]
                required = math.prod(shape)*dtype.itemsize
                if item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValueError('Evidence encrypted/unsupported NPZ member')
                if not required <= item.file_size <= required+512:
                    raise ValueError('Evidence NPZ allocation quota before loading')
                with z.open(item) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version not in ((1,0),(2,0)):
                        raise ValueError('Evidence NPY version')
                    size_bytes = stream.read(2 if version == (1,0) else 4)
                    header_size = int.from_bytes(size_bytes, 'little')
                    if not 1 <= header_size <= 256:
                        raise ValueError('Evidence NPY header cap before read')
                    header = stream.read(header_size)
                    if len(header) != header_size:
                        raise ValueError('Evidence truncated NPY header')
                    reader = np.lib.format.read_array_header_1_0 if version == (1,0) else np.lib.format.read_array_header_2_0
                    actual_shape, fortran, actual_dtype = reader(io.BytesIO(size_bytes+header), max_header_size=256)
                    if (actual_shape != shape or fortran or actual_dtype != dtype or actual_dtype.hasobject
                            or item.file_size != stream.tell()+required):
                        raise ValueError('Evidence NPY shape/dtype/bytes before allocation')
    except (zipfile.BadZipFile, EOFError) as exc:
        raise ValueError('Evidence corrupt NPZ') from exc


def _expected(manifest):
    n = manifest['training']['points']
    train = {k+'.npy':((n,),np.dtype('float64')) for k in ('frequency_hz','real','imag')}
    audit = {}
    for band in manifest['bands']:
        for suffix, dtype in (('_f',np.dtype('float64')),('_target',np.dtype('complex128'))):
            audit[band['id']+suffix+'.npy'] = ((band['points'],),dtype)
    return dict(training=train, audit=audit)


def inspect_evidence(path):
    """Return (strict manifest, immutable file references), without scientific work.

    ``path`` is the JSON manifest; its two NPZ references are simple relative
    filenames in the same directory. No destination is created or consulted.
    """
    path = strict.safe_path(path)
    raw = strict.read_bytes(path)
    manifest = validate_manifest(strict.parse_json(raw))
    files = dict(manifest=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest()))
    for key, headers in _expected(manifest).items():
        file = strict.safe_path(path.parent/manifest[key]['file'])
        data = strict.read_bytes(file)
        sha = hashlib.sha256(data).hexdigest()
        if sha != manifest[key]['sha256']:
            raise ValueError('Evidence file fingerprint mismatch: '+key)
        _npz_headers(data,headers)
        files[key] = dict(path=str(file),sha256=sha)
    if hashlib.sha256(strict.read_bytes(path)).hexdigest() != files['manifest']['sha256']:
        raise ValueError('Evidence manifest changed during inspection')
    return manifest, files


def verify_files(files):
    _exact(files, {'manifest','training','audit'}, 'references')
    for key, ref in files.items():
        _exact(ref, {'path','sha256'}, 'reference')
        _hash(ref['sha256'], 'reference')
        if hashlib.sha256(strict.read_bytes(ref['path'])).hexdigest() != ref['sha256']:
            raise ValueError('Evidence changed: '+key)


def validate_evidence(manifest, model, files):
    """Recompute finite fidelity and disjunction in the bounded worker/reader.

    The caller separately binds CONFIG/DESIGN/DB, effective recipe, radius,
    realization bundle and observation coverage. This function never claims
    physical validation, whole-spectrum coverage, or authenticated provenance.
    """
    verify_files(files)
    current, current_files = inspect_evidence(files['manifest']['path'])
    if current_files != files or current != manifest:
        raise ValueError('Evidence supplied manifest differs from saved evidence')
    m = current
    params = model.parameters()
    if (digest(params) != m['model_sha256'] or model.sample_rate_hz != m['recipe']['sample_rate_hz']
            or params['domain'] != m['coefficient_domain']
            or params['provenance'].get('source_sha256') != m['source_sha256']
            or params['provenance'].get('training_domain_hz') != m['domain_hz']):
        raise ValueError('Evidence realized model/source/fs/domain mismatch')
    state_before = model.export_state()
    arrays = {}
    for key in ('training','audit'):
        data = strict.read_bytes(files[key]['path'])
        if hashlib.sha256(data).hexdigest() != files[key]['sha256']:
            raise ValueError('Evidence changed before loading')
        with np.load(io.BytesIO(data),allow_pickle=False) as z:
            arrays[key] = {k:z[k] for k in z.files}
    train = arrays['training']
    f, target = spectrum(train['frequency_hz'],train['real']+1j*train['imag'],fs=model.sample_rate_hz)
    if [float(f[0]),float(f[-1])] != m['domain_hz']:
        raise ValueError('Evidence training endpoints do not match declared domain')
    def response(frequency):
        # Coefficient provenance does not change the map executed by the coupling.
        return np.concatenate([model.discrete_response(frequency[i:i+4096]) for i in range(0,len(frequency),4096)])
    def evaluated(frequency, reference):
        values = metrics(reference,response(frequency))
        passed = all(values[k] is not None and values[k] <= limit for k,limit in DEFAULT_GATES.items())
        return dict(points=len(frequency),metrics=values,passes_reference_gates=passed)
    training_result = evaluated(f,target)
    if not training_result['passes_reference_gates']:
        raise ValueError('Evidence training fidelity refused by native gates')
    seen = f; results = []
    for band in m['bands']:
        values = arrays['audit']; name = band['id']
        bf, bz = spectrum(values[name+'_f'], values[name+'_target'],fs=model.sample_rate_hz)
        lo, hi = band['domain_hz']
        if bf[0] < lo or bf[-1] > hi or np.intersect1d(seen,bf).size:
            raise ValueError('Evidence frequency outside band or duplicate/training-audit overlap')
        if band['role'] == 'extrapolation' and np.any((bf >= m['domain_hz'][0]) & (bf <= m['domain_hz'][1])):
            raise ValueError('Extrapolation sample inside admitted domain')
        seen = np.concatenate((seen,bf))
        result = evaluated(bf,bz)
        if band['role'] == 'audit' and not result['passes_reference_gates']:
            raise ValueError('Evidence audit fidelity refused by native gates: '+name)
        results.append(dict(id=name,role=band['role'],domain_hz=band['domain_hz'],
                            admitted=band['role']=='audit',**result))
    if model.parameters() != params or model.export_state() != state_before:
        raise ValueError('Evidence response evaluation changed native model/state')
    verify_files(files)
    return dict(schema='dcalc.state_register.fidelity.v1',status='finite_fidelity_verified',
        units=UNITS,gates=dict(DEFAULT_GATES),training=training_result,bands=results,
        domain_hz=m['domain_hz'],coefficient_domain=m['coefficient_domain'],audited_response_domain='discrete',
        source_sha256=m['source_sha256'],model_sha256=m['model_sha256'],
        evidence_sha256=files['manifest']['sha256'],acquisition=m['acquisition'],
        documentary_origin='declared_not_authenticated',sample_disjunction_verified=True,
        whole_nonlinear_spectrum_bounded=False,physical_validation='not_validated',
        passivity_scope='separate_native_realization_certificate',
        conditioning_scope='separate_native_realization_diagnostics')


def verify_saved_analysis(out, result, arrays, experiment, chain):
    """Verify acquired rows and observations without taking a temporal step.

    Residuals are evaluated on saved endpoints and native port/energy functions;
    no trajectory is reconstructed, advanced or refitted.
    """
    from . import register_target as report
    from ..nonlinear import register_target as core
    from ..nonlinear.simultaneous_coupling import SimultaneousCoupling, contact_forces
    from ..nonlinear.lip_ports import flows
    r=result['plan']['request'];model=experiment.model
    if result.get('context_checks')!=experiment.context_checks: raise ValueError('Saved scientific checks differ')
    starts={}
    for path,item in chain:
        context=item['context'];parent=str(path.parent)
        if parent not in starts:
            starts[parent]=item
            if item['experiment']['payload']['accepted_steps']!=context['accepted_before'] or item['previous']!=context['resume'] or item['series'] is not None:
                raise ValueError('Local state run origin/resume counter mismatch')
            core.integer(context['steps_this_run'],'local step quota',0,sum(x['steps'] for x in r['plateaus'])-context['accepted_before'])
        if not context['accepted_before']<=item['experiment']['payload']['accepted_steps']<=context['accepted_before']+context['steps_this_run']:
            raise ValueError('Local state run cumulative quota')
        if item['context']['metadata']!=result['plan']['metadata']: raise ValueError('State chain acoustic context changed')
        experiment.restore(item['experiment'])
    if result['new_steps_this_run']!=experiment.accepted-result['plan']['accepted_before'] or result['historical_source_steps']!=0:
        raise ValueError('New/historical state counters')
    if result['new_duration_s']!=result['new_steps_this_run']/core.sample_rate(r): raise ValueError('New state duration')
    expected=core.analyze(r,times=arrays['times']-experiment.origin_time_s,states=arrays['states'],
        midpoint_times=arrays['midpoint_times']-experiment.origin_time_s,pressure=arrays['values'][:,0],
        source=dict(kind='verified_bundle',description='Verified product chunk chain',checkpoint=result['checkpoint']))
    actual=dict(result['analysis']);actual['windows']={k:report.read_json(Path(out)/name) for k,name in actual['windows'].items()}
    phase_file=actual.pop('phase_file');actual['phase']=report.read_json(Path(out)/phase_file) if phase_file else None
    if actual!=expected: raise ValueError('Saved observations/criteria differ from acquired series')
    # The three public tables must also describe the verified data, even if a
    # caller recomputed the enclosing manifest hashes after altering CSV text.
    import csv
    for name,rows,fields in (
        ('criteria.csv',expected['criteria'],['id','role','observable','status','unit_si','reason']),
        ('controls.csv',r['plateaus'],['id','pressure_pa','resonance_hz','damping_ratio','steps','duration_s']),
        ('events.csv',experiment.events,['step','old_segment','new_segment','command_work_j','lip_energy_before_j','lip_energy_after_j'])):
        text=io.StringIO(newline='');writer=csv.writer(text);writer.writerow([*fields,'record_json'])
        import json
        for row in rows: writer.writerow([*[row.get(k) for k in fields],json.dumps(row,allow_nan=False,sort_keys=True)])
        if (Path(out)/name).read_bytes()!=text.getvalue().encode(): raise ValueError('Saved semantic table mismatch')
    solvers=[SimultaneousCoupling(model,params=core.active_parameters(r,i),rho=r['rho_kg_m3'],v2_port_model='conjugate') for i in range(len(r['plateaus']))]
    A,B,C,D=model.A,model.B,model.C,model.D
    def close(a,b,name,atol=1e-14):
        if not np.allclose(a,b,rtol=2e-9,atol=atol): raise ValueError('Saved native '+name+' residual')
    for i,row in enumerate(arrays['values']):
        s0,s1=arrays['states'][i:i+2];c=solvers[int(arrays['segments'][i])]
        saved=dict(zip(core.COLUMNS,row));vm=(s0[1]+s1[1])/2;xm=(s0[2:]+s1[2:])/2
        p=saved['pressure_pa'];ud=saved['downstream_flow_m3_s'];u=saved['jet_m3_s']
        close(s1[0],s0[0]+c.dt*vm,'lip displacement',1e-16)
        close(s1[2:]-s0[2:],c.dt*model.omega_ref*(A@xm+B*ud),'state dynamics',1e-13)
        close(p,model.Zscale*(C@xm+D*ud),'pressure',1e-7)
        for key,value in flows(u,vm,c.Au,c.Ad,'conjugate').items():
            if key in saved: close(saved[key],value,key,1e-16)
        close(saved['flow_m3_s'],ud,'native flow',1e-16)
        c._backend.initialize(s0[2:]);ph,_=c._backend.affine_port()
        port_p,port_u,_,_=c._port(vm,s0[0],ph)
        close(p,port_p,'port pressure',1e-7);close(u,port_u,'jet',1e-15)
        fc,_=contact_forces(s0[0],s1[0],c.dt,threshold=c.xt,stiffness=c.params.contact_stiffness_n_per_m,damping=c.params.contact_damping_n_s_per_m)
        fd=c.params.contact_damping_n_s_per_m*max(-vm-max(s0[0]-c.xt,0.)/c.dt,0.)
        rhs=c.m*s0[1]-c.tau*c.K*s0[0]+c.tau*c.Au*c.Pu
        close(c.M*vm-c.tau*(fc+fd),rhs+c.tau*c.Ad*p,'mechanical',1e-15)
        el0,el1=c.lip_energy(s0),c.lip_energy(s1);er0,er1=model.energy_of(s0[2:]),model.energy_of(s1[2:])
        dissipated=model.Zscale*float(np.linalg.norm(model.L.T@np.r_[xm,ud])**2)*c.dt
        predicted=dict(lip_energy_j=el1,resonator_energy_j=er1,total_energy_j=el1+er1,
            lip_energy_change_j=el1-el0,resonator_energy_change_j=er1-er0,total_energy_change_j=el1-el0+er1-er0,
            pressure_work_j=c.dt*(c.Au*c.Pu+c.Ad*p)*vm,resonator_work_j=c.dt*p*ud,
            source_work_j=c.dt*c.Pu*saved['upstream_flow_m3_s'],jet_loss_j=c.dt*(c.Pu-p)*u,
            lip_loss_j=c.dt*c.r*vm*vm,contact_loss_j=-c.dt*fd*vm,resonator_loss_j=dissipated)
        for name,value in predicted.items(): close(saved[name],value,name,1e-16)
