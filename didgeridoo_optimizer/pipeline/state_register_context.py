"""Fixed saved-state acoustic context, with pure preflight and bounded-worker checks.

No fitting, conversion, TMM or temporal evolution occurs here. Documentary
acquisition provenance is declared, never authenticated by an integrity hash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..nonlinear import scalar_passivity_certificate as certs
from ..nonlinear.passive_resonator import digest, real, integer
from ..nonlinear.passive_state import PassiveState, SCHEMA, UNITS
from ..reporting import passive_realization as realization
from ..reporting import state_register_context as evidence
from . import time_domain_reference as td
from .design_pitch import models

_LOADED_SOURCE_PATH=Path(__file__).resolve()
_LOADED_SOURCE_SHA256=hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
RECIPE={'sample_rate_hz','h_cm','loss_model','radiation_model','air_reference'}


def exact(value, keys, where):
    if type(value) is not dict or set(value)!=set(keys): raise ValueError(where+' schema')
    return value


def validate_recipe(recipe):
    exact(recipe,RECIPE,'State recipe')
    integer(recipe['sample_rate_hz'],'fs',1000,12000)
    if not 0<real(recipe['h_cm'],'h_cm')<=2: raise ValueError('State mesh support')
    if recipe['loss_model'] not in ('zk','legacy'): raise ValueError('State loss recipe')
    if recipe['radiation_model'] not in ('legacy','silva_unflanged','silva_flanged'): raise ValueError('State radiation recipe')
    if ((recipe['loss_model']=='zk' and recipe['air_reference'] not in ('ck_dry20','ck_dry25'))
            or (recipe['loss_model']=='legacy' and recipe['air_reference'] is not None)):
        raise ValueError('State air recipe')


def reference(path):
    path=certs.safe_path(path)
    return dict(path=str(path),sha256=certs.sha(certs.read_bytes(path)))


def verify_files(files):
    for item in files.values():
        if reference(item['path'])!=item: raise ValueError('State context file changed')


def parameter_shape(p):
    """List/scalar checks before arrays, eigensolves or scientific proof checks."""
    certs.bounded(p)
    exact(p,{'schema','units','pressure_port','sample_rate_hz','domain','J','g','L','omega_ref','Zscale','coefficient_origin','provenance'},'State parameters')
    if p['schema']!=SCHEMA or p['units']!=UNITS or p['pressure_port']!='midpoint': raise ValueError('State units/port')
    if type(p['g']) is not list: raise ValueError('State vector')
    n=len(p['g']);integer(n,'state count',0,128)
    for x in p['g']: real(x,'g')
    for name,rows,cols in [('J',n,n),('L',n+1,None)]:
        a=p[name]
        if type(a) is not list or len(a)!=rows: raise ValueError('State matrix rows')
        if cols is None:
            if not a or type(a[0]) is not list: raise ValueError('State L rows')
            cols=len(a[0]);integer(cols,'L columns',0,129)
        for row in a:
            if type(row) is not list or len(row)!=cols: raise ValueError('State matrix shape')
            for x in row: real(x,'matrix')
    if any(p['J'][i][j]!=-p['J'][j][i] for i in range(n) for j in range(n)): raise ValueError('State antisymmetry')
    for name in ('omega_ref','Zscale'):
        if real(p[name],name)<=0: raise ValueError('Positive state scale')
    if type(p['coefficient_origin']) is not str or not p['coefficient_origin'].strip() or type(p['provenance']) is not dict:
        raise ValueError('State provenance')
    return n


def case_context(case, observation=None, rho=None):
    """Read-only metadata preflight; certificates are verified only in worker."""
    validate_recipe(case['recipe'])
    for key in ('config','design','realization','evidence'): certs.safe_path(case[key])
    context=td._context(case['config'],case['design'])
    _,_,_,effective=models(context,case['recipe'])
    files=context['provenance']['files']
    if any(v['sha256'] is None for v in files.values()): raise ValueError('All four actual inputs required')
    for item in files.values(): certs.safe_path(item['path'])
    radius=context['design'].segments[-1].d_out_cm/200
    manifest,proof_files=evidence.inspect_evidence(case['evidence'])
    bundle=Path(case['realization']);bm=certs.read_json(bundle/'manifest.json')
    exact(bm,{'schema','files'},'Realization manifest')
    if bm['schema']!=realization.SCHEMA or set(bm['files'])!=realization.REQUIRED_FILES: raise ValueError('Realization inventory')
    bundle_files={name:reference(bundle/name) for name in sorted(realization.REQUIRED_FILES|{'manifest.json','execution.json','execution.closed.json'})}
    for name,h in bm['files'].items():
        if bundle_files[name]['sha256']!=h: raise ValueError('Realization fingerprint')
    src=certs.validate_source(certs.read_json(bundle/'source.json'))
    if src['schema']==certs.R59_SCHEMA and manifest['acquisition']['nature']!='simulation_tmm':
        raise ValueError('R59 TMM acquisition cannot be relabelled as measurement or synthetic fixture')
    envelope=certs.read_json(bundle/'model.json');exact(envelope,{'parameters','sha256'},'State envelope')
    mp=envelope['parameters'];n=parameter_shape(mp)
    if envelope['sha256']!=digest(mp): raise ValueError('State fingerprint')
    fs=case['recipe']['sample_rate_hz']
    if mp['sample_rate_hz']!=fs or src['sample_rate_hz']!=fs or n!=src['order'] or mp['domain']!=src['coefficient_domain']:
        raise ValueError('State scalar domain/fs/order mismatch')
    if mp['omega_ref']!=src['wref'] or mp['Zscale']!=src['zscale']: raise ValueError('State scalar units/scales')
    certificate=certs.read_json(bundle/'certificate.json')
    if certificate is not None: certs.validate_certificate(src,certificate,bundle_files['source.json']['sha256'])
    elif n or src['direct_normalized']<0: raise ValueError('Missing scalar certificate')
    expected=dict(inputs={k:v['sha256'] for k,v in files.items()},recipe=case['recipe'],effective=effective,
                  physical_radius_m=radius,source_sha256=bundle_files['source.json']['sha256'],model_sha256=digest(mp),
                  domain_hz=src['training_domain_hz'],coefficient_domain=src['coefficient_domain'])
    if any(manifest[k]!=v for k,v in expected.items()): raise ValueError('Acoustic evidence input/recipe/source/model mismatch')
    if rho is not None and rho!=effective['air']['rho']: raise ValueError('Lip/acoustic air density mismatch')
    if observation is not None:
        lo,hi=observation['band_hz'];a,b=manifest['domain_hz']
        if lo<a or hi>b: raise ValueError('Observation outside finite acoustic coverage')
        phase=observation['phase']
        if phase is not None and phase['state_units']!=['m','m/s']+['m^3/s']*n: raise ValueError('Complete native state units required')
    td._unchanged(context);verify_files(bundle_files);verify_files(proof_files)
    meta=dict(kind='saved_state',states=2+n,state_units=['m','m/s']+['m^3/s']*n,
              model_sha256=bundle_files['model.json']['sha256'],model_parameters_sha256=digest(mp),
              inputs=expected['inputs'],recipe=case['recipe'],effective=effective,
              evidence=manifest,evidence_files=proof_files,bundle_files=bundle_files)
    meta['acoustic_identity']=digest(meta)
    return meta,context,mp


def load_verified(case, metadata, observation, rho, *, binding=False):
    actual,_,_=case_context(case,observation,rho)
    if actual!=metadata: raise ValueError('State context changed since preflight')
    validated=realization.read_result(case['realization'])
    model=PassiveState.load(Path(case['realization'])/'model.json',expected_sha256=metadata['model_sha256'])
    if digest(validated['model_parameters'])!=digest(model.parameters()): raise ValueError('Realization/native identity')
    checks=evidence.validate_evidence(metadata['evidence'],model,metadata['evidence_files'])
    verify_files(metadata['bundle_files']);verify_files(metadata['evidence_files'])
    checks=dict(realization_verified=True,finite_fidelity=checks,physical_validation='not_validated',
                documentary_origin='declared, not authenticated',nonlinear_spectrum_bounded=False)
    return (model,checks,_VerifiedBinding(_SEAL,model,metadata,checks)) if binding else (model,checks)


def context_guard(metadata, input_files):
    """Pin files and imported product sources; hash only after a stat change.

    Files are hashed on admission; inode/ctime/mtime/size are checked before each
    step. A changed signature triggers a byte check, never a refreshed identity.
    """
    from ..reporting import register_target as report
    files=list(metadata['bundle_files'].values())+list(metadata['evidence_files'].values())+list(input_files.values())
    files += [dict(path=str(report.ROOT/name),sha256=h) for name,h in report.sources().items()]
    def signature(path):
        p=certs.safe_path(path);s=p.stat()
        return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    pins=[(x['path'],x['sha256'],signature(x['path'])) for x in files]
    def check():
        for path,expected,saved in pins:
            if signature(path)!=saved and report.file_sha256(path)!=expected:
                raise ValueError('State input/source changed before native step: '+path)
    check()
    return check


_SEAL=object()


class _VerifiedBinding:
    """Internal single-process receipt, created only after native verification.

    Not an acquisition signature. Public callers omit this optimization and the
    Experiment constructor performs exactly the same verification itself.
    """
    def __init__(self, seal, model, metadata, checks):
        if seal is not _SEAL: raise ValueError('Native verified binding required')
        self._seal=seal;self._model=model
        self._metadata=digest(metadata);self._checks=json.dumps(checks,sort_keys=True,allow_nan=False)

    def checked(self, model, metadata):
        if self._seal is not _SEAL or self._model is not model or self._metadata!=digest(metadata):
            raise ValueError('Verified state binding/model/context changed')
        model.verify_file_unchanged();verify_files(metadata['bundle_files']);verify_files(metadata['evidence_files'])
        return json.loads(self._checks)
