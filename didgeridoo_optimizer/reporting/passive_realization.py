"""Portable strict realization bundles. Reading performs bounded algebra only.

No producer import, conversion, fitting, TMM, frequency or temporal simulation.
Hashes establish relative integrity, not external authorship authentication.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path

_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()

import numpy as np
from ..nonlinear import scalar_passivity_certificate as certs
from ..nonlinear.passive_state import validate_parameter_data
from ..nonlinear.passive_resonator import digest

SCHEMA='dcalc.passive_realization.v1'
POLICY=dict(response_maxrel=2e-8,dc_relative=2e-8,direct_relative=2e-12,
            matrix_normalized=2e-10,transform_condition_eps=1e-8)
DATA_FILES={'source.json','certificate.json','model.json','diagnostics.json','result.json','witness.json'}
REQUIRED_FILES=DATA_FILES|{'plan.json','stdout.txt','stderr.txt'}


def write_new(path, raw):
    certs.safe_path(path,exists=False)
    if type(raw) is not bytes or len(raw)>certs.MAX_BYTES: raise ValueError('Bounded bytes required')
    with Path(path).open('xb') as stream: stream.write(raw)


def json_new(path, value): write_new(path,certs.strict_json(value).encode())


def validate_plan(plan):
    """Portable plan schema; historical input paths need not still exist."""
    certs.bounded(plan)
    if (type(plan) is not dict or set(plan)!={'schema','source','certificate','output','seconds','order','producer_sources','budgets'}
            or plan['schema']!='dcalc.passive_realization.plan.v1'):
        raise ValueError('Realization plan schema')
    _metric(plan['seconds'],'seconds',positive=True)
    if plan['seconds']>175: raise ValueError('Plan compute budget')
    certs.integer(plan['order'],'order',0,128)
    for key in ('source','certificate'):
        item=plan[key]
        if type(item) is not dict or set(item)!={'path','sha256'}: raise ValueError('Plan input schema')
        if key=='certificate' and item=={'path':None,'sha256':None}: continue
        certs.hash_string(item['sha256'])
        if (type(item['path']) is not str or not Path(item['path']).is_absolute()
                or '..' in Path(item['path']).parts or '\x00' in item['path']):
            raise ValueError('Plan input path format')
    if (type(plan['output']) is not str or not Path(plan['output']).is_absolute()
            or '..' in Path(plan['output']).parts or '\x00' in plan['output']):
        raise ValueError('Plan output path format')
    expected=dict(memory_mib=768,child_cpu_seconds=175,blas_threads=1,file_mib=4,
        compute_wall_seconds=plan['seconds'],preflight_seconds=2.,collection_seconds=2.,closure_seconds=5.,
        public_wall_seconds=plan['seconds']+9.)
    if certs.strict_json(plan['budgets'])!=certs.strict_json(expected): raise ValueError('Plan resource policy mismatch')
    if type(plan['producer_sources']) is not dict or set(plan['producer_sources'])!=set(certs.verify_sources()[0]):
        raise ValueError('Plan producer source keys')
    for value in plan['producer_sources'].values(): certs.hash_string(value)
    return plan


def write_result(out, conversion, source_bytes, certificate_bytes):
    certs.verify_sources(); out=certs.safe_path(out)
    model=conversion['model']; parameters=model.parameters()
    write_new(out/'source.json',source_bytes)
    write_new(out/'certificate.json',certificate_bytes if certificate_bytes is not None else b'null\n')
    json_new(out/'model.json',dict(parameters=parameters,sha256=digest(parameters)))
    json_new(out/'diagnostics.json',conversion['diagnostics'])
    json_new(out/'witness.json',conversion['witness'])
    json_new(out/'result.json',dict(schema=SCHEMA,status='represented',
        source_sha256=conversion['source_sha256'],certificate_sha256=conversion['certificate_sha256'],
        model_sha256=digest(parameters),physical_validation='not_validated',
        producer_sources=certs.verify_sources()[0],source_roles=certs.verify_sources()[1]))
    certs.verify_sources()


def write_manifest(out):
    out=certs.safe_path(out)
    json_new(out/'manifest.json',dict(schema=SCHEMA,
        files={name:certs.sha(certs.read_bytes(out/name)) for name in sorted(REQUIRED_FILES)}))


def _close(actual, expected, limit=2e-10):
    a,b=np.asarray(actual),np.asarray(expected)
    scale=max(float(np.linalg.norm(a)),float(np.linalg.norm(b)))
    error=float(np.linalg.norm(a-b))
    if not np.isfinite(error) or (error/scale if scale else error)>limit:
        raise ValueError('Saved algebraic representation identity mismatch')


def _metric(value, name, *, positive=False):
    if (type(value) not in (int,float) or not math.isfinite(value)
            or value<0 or (positive and value==0)):
        raise ValueError('Invalid diagnostic metric: '+name)
    return value


def _matrices(records, names):
    if type(records) is not dict or set(records)!=set(names):
        raise ValueError('Complete matrix diagnostics required')
    for name,e in records.items():
        if type(e) is not dict or set(e)!={'absolute','scale','normalized'}:
            raise ValueError('Matrix residual schema')
        for key,value in e.items(): _metric(value,name+'/'+key)
        expected=e['absolute']/e['scale'] if e['scale'] else 0.
        if ((e['scale']==0 and e['absolute']!=0) or e['normalized']!=expected
                or e['normalized']>POLICY['matrix_normalized']):
            raise ValueError('Matrix diagnostic ceiling/inconsistency')


def _diagnostics(d, source, proof):
    common={'policy','storage_attempts','scalar_proof','grid_is_continuous_bound','method',
            'selected_storage','matrices','response','dc','direct','source_dc_pa_s_m3',
            'source_direct_pa_s_m3','source_R0_attribution','grid'}
    n=source['order']
    if type(d) is not dict or set(d)!=(common|({'residue_coordinate_scales'} if n else set())):
        raise ValueError('Complete diagnostics schema required')
    if type(d['policy']) is not dict or set(d['policy'])!=set(POLICY):
        raise ValueError('Representation policy schema')
    for key,value in d['policy'].items():
        _metric(value,key,positive=True)
        if value!=POLICY[key]: raise ValueError('Representation policy changed')
    if certs.strict_json(d['scalar_proof'])!=certs.strict_json(proof) or d['grid_is_continuous_bound'] is not False:
        raise ValueError('Scalar proof or grid claim mismatch')
    if d['source_R0_attribution']!=source.get('R0'): raise ValueError('R59 R0 attribution mismatch')
    grid=d['grid']
    if (type(grid) is not dict or set(grid)!={'origin','frequency_hz','training_domain_hz','extrapolation_count'}
            or grid['origin'] not in ('deterministic_declared_band_with_DC','caller_control_grid')
            or grid['training_domain_hz']!=source['training_domain_hz']):
        raise ValueError('Control grid schema/attribution mismatch')
    for x in grid['training_domain_hz']: certs.number(x)
    f=grid['frequency_hz']
    if type(f) is not list or not 1<=len(f)<=4096: raise ValueError('Control grid size')
    for x in f:
        if not 0<=certs.number(x)<source['sample_rate_hz']/2: raise ValueError('Control grid domain')
    lo,hi=source['training_domain_hz']
    count=sum(x<lo or x>hi for x in f)
    certs.integer(grid['extrapolation_count'],'extrapolation_count',count,count)
    if type(d['response']) is not dict or set(d['response'])!={'continuous','discrete'}:
        raise ValueError('Response diagnostics schema')
    for e in d['response'].values():
        if type(e) is not dict or set(e)!={'maxrel','max_absolute','zero_reference_count'}:
            raise ValueError('Response error schema')
        _metric(e['maxrel'],'maxrel'); _metric(e['max_absolute'],'max_absolute')
        certs.integer(e['zero_reference_count'],'zero_reference_count',0,len(f))
        if e['maxrel']>POLICY['response_maxrel']: raise ValueError('Response error ceiling')
    if not n:
        if d['method']!='analytic_constant' or d['selected_storage'] is not None or d['storage_attempts']!=[]:
            raise ValueError('Constant method diagnostics')
        _matrices(d['matrices'],{'constant_factor'})
    else:
        if d['method']!='balanced_auxiliary_riccati': raise ValueError('General method diagnostics')
        _matrices(d['matrices'],{'native_A','native_B','native_C','native_D'})
        s=d['selected_storage']; keys={'delta','hamiltonian_guard','stable_dimension','imaginary_axis_gap',
            'subspace_condition','storage_condition','transform_condition','transform_condition_eps',
            'original_lmi_min_eigenvalue','matrices'}
        if type(s) is not dict or set(s)!=keys: raise ValueError('Storage diagnostics schema')
        certs.integer(s['stable_dimension'],'stable_dimension',n,n)
        for key,value in s.items():
            if key not in ('stable_dimension','matrices'): _metric(value,key,positive=True)
        if s['transform_condition_eps']>POLICY['transform_condition_eps']:
            raise ValueError('Transform diagnostic ceiling')
        _matrices(s['matrices'],{'storage_imaginary','storage_raw_symmetry','auxiliary_riccati',
            'storage_factor','normalized_factor','similarity_A','similarity_B','similarity_C','original_lmi_congruence'})
        fractions=(.1,.03,.01,.003,.001,.0003,.0001); attempts=d['storage_attempts']
        if type(attempts) is not list or not 1<=len(attempts)<=len(fractions): raise ValueError('Storage attempts budget')
        for i,a in enumerate(attempts):
            last=i==len(attempts)-1
            if (type(a) is not dict or set(a)!=({'fraction','status','diagnostics'} if last else
                    {'fraction','delta','status','reason','diagnostics'}) or a['fraction']!=fractions[i]
                    or a['status']!=('selected' if last else 'refused')):
                raise ValueError('Storage attempt sequence mismatch')
            _metric(a['fraction'],'fraction',positive=True)
            if not last and (type(a['reason']) is not str or not a['reason']): raise ValueError('Missing refusal reason')
        if attempts[-1]['diagnostics']!=s or s['delta']!=min(-a for a in source['p_real'])*fractions[len(attempts)-1]:
            raise ValueError('Selected original-pole shift mismatch')
    return d


def _parameters(source, envelope, witness, diagnostic, result, certificate, raw_source, raw_certificate):
    if (type(envelope) is not dict or set(envelope)!={'parameters','sha256'}
            or envelope['sha256']!=digest(envelope['parameters'])):
        raise ValueError('Native model checksum mismatch')
    p=envelope['parameters']; validate_parameter_data(p)
    if result['model_sha256']!=envelope['sha256']: raise ValueError('Result/model identity mismatch')
    if (p['omega_ref']!=source['wref'] or p['Zscale']!=source['zscale']
            or p['sample_rate_hz']!=source['sample_rate_hz'] or p['domain']!=source['coefficient_domain']
            or len(p['g'])!=source['order']): raise ValueError('Native normalization mismatch')
    proof=(certs.verify_certificate(source,certificate,certs.sha(raw_source)) if certificate is not None else
        dict(kind='analytic_constant',verified=True,constant_normalized=source['direct_normalized'],
             coefficient_sha256=certs.sha(raw_source),physical_validation='not_validated'))
    if certificate is None and (source['order']!=0 or source['direct_normalized']<0):
        raise ValueError('Missing strict general certificate')
    expected_provenance=dict(source_sha256=certs.sha(raw_source),
        certificate_sha256=certs.sha(raw_certificate) if certificate is not None else None,
        source_schema=source['schema'],source_passivity=source['passivity'],
        source_physical_validation=source['physical_validation'],scalar_proof=proof,
        representation_policy=POLICY,conversion='PASSIVE-REALIZATION-01',
        training_domain_hz=source['training_domain_hz'],original_provenance=source.get('provenance',{}))
    if certs.strict_json(p['provenance'])!=certs.strict_json(expected_provenance): raise ValueError('Native provenance mismatch')
    if p['coefficient_origin']!=source.get('coefficient_origin','Frozen R59 scalar coefficients; separate R60 scalar certificate'):
        raise ValueError('Native coefficient origin mismatch')
    _diagnostics(diagnostic,source,proof)
    if diagnostic.get('policy')!=POLICY or diagnostic.get('scalar_proof')!=proof:
        raise ValueError('Diagnostics policy/proof mismatch')
    for name in ('dc','direct'):
        e=diagnostic.get(name,{})
        if (set(e)!={'absolute','scale','relative','zero_reference'} or type(e['zero_reference']) is not bool
                or type(e['relative']) not in (int,float) or not 0<=e['relative']<=POLICY[name+'_relative']):
            raise ValueError('Invalid admitted representation error')
    for name in ('continuous','discrete'):
        e=diagnostic.get('response',{}).get(name,{})
        if type(e.get('maxrel')) not in (int,float) or not 0<=e['maxrel']<=POLICY['response_maxrel']:
            raise ValueError('Invalid admitted response diagnostic')
    if diagnostic.get('grid_is_continuous_bound') is not False:
        raise ValueError('Grid must not claim a continuous bound')
    for e in diagnostic.get('matrices',{}).values():
        if type(e) is not dict or not 0<=e.get('normalized',float('inf'))<=POLICY['matrix_normalized']:
            raise ValueError('Invalid matrix diagnostic')
    n=source['order']; J=np.array(p['J']).reshape(n,n); g=np.array(p['g']); L=np.array(p['L'])
    W=L@L.T; A=J-W[:n,:n]; B=g-W[:n,n]; C=g+W[:n,n]; D=float(W[n,n])
    if type(witness) is not dict or set(witness)!={'T','balance_scales'}:
        raise ValueError('Witness schema mismatch')
    if n:
        T=witness['T']; scales=witness['balance_scales']
        if type(T) is not list or len(T)!=n or any(type(row) is not list or len(row)!=n for row in T):
            raise ValueError('Witness transform shape')
        if type(scales) is not list or len(scales)!=len(source['p_real']): raise ValueError('Witness scales shape')
        for row in T:
            for x in row: certs.number(x)
        T=np.array(T); As=np.zeros((n,n)); Bs=np.zeros(n); Cs=np.zeros(n); i=k=0
        for a,b,scale in zip(source['p_real'],source['p_imag'],scales):
            if certs.number(scale)<=0: raise ValueError('Positive balance scale required')
            u=source['residues'][k]; k+=1
            if b==0:
                As[i,i]=a; Bs[i]=scale; Cs[i]=u/scale; i+=1
            else:
                v=source['residues'][k]; k+=1
                As[i:i+2,i:i+2]=[[a,-b],[b,a]]
                Bs[i]=scale; Cs[i:i+2]=[2*u/scale,-2*v/scale]; i+=2
        # Direct bounded algebra, not a newly computed realization.
        _close(A@T,T@As); _close(B,T@Bs); _close(C@T,Cs)
        if np.linalg.cond(T)*np.finfo(float).eps>POLICY['transform_condition_eps']:
            raise ValueError('Saved transform conditioning ceiling')
        dc=float(source['zscale']*(D-C@np.linalg.solve(A,B)))
    else:
        if witness!={'T':[],'balance_scales':[]}: raise ValueError('Constant witness mismatch')
        dc=source['zscale']*D
    _close(D,source['direct_normalized'],POLICY['direct_relative'])
    rows,_,_=certs.terms(source)
    exact_dc=certs.number(source['direct_normalized'])
    for a,b,u,v in rows:
        exact_dc+=u/a if not b else 2*(u*a-v*b)/(a*a+b*b)
    expected_dc=float(exact_dc*certs.number(source['zscale']))
    expected_direct=float(certs.number(source['direct_normalized'])*certs.number(source['zscale']))
    for name,actual,expected in (('dc',dc,expected_dc),('direct',source['zscale']*D,expected_direct)):
        value=diagnostic['source_'+name+'_pa_s_m3']
        _metric(value,'source_'+name)
        if value!=expected: raise ValueError('Independent scalar DC/direct oracle mismatch')
        absolute=abs(actual-expected)
        e=dict(absolute=absolute,scale=abs(expected),relative=absolute/abs(expected) if expected else 0.,
               zero_reference=bool(expected==0))
        for key in ('absolute','scale','relative'): _metric(diagnostic[name][key],name+'/'+key)
        if diagnostic[name]!=e or (expected==0 and absolute!=0):
            raise ValueError('DC/direct error diagnostic mismatch')
        _close(actual,expected,POLICY[name+'_relative'])
    return p


def read_payload(output):
    """Verify all required files, exact scalar proof and saved matrix identities."""
    certs.verify_sources(); out=certs.safe_path(output)
    manifest=certs.read_json(out/'manifest.json')
    if (type(manifest) is not dict or set(manifest)!={'schema','files'} or manifest['schema']!=SCHEMA
            or type(manifest['files']) is not dict or set(manifest['files'])!=REQUIRED_FILES):
        raise ValueError('Complete realization manifest required')
    raw={}
    for name,expected in manifest['files'].items():
        certs.hash_string(expected); raw[name]=certs.read_bytes(out/name)
        if certs.sha(raw[name])!=expected: raise ValueError('Bundle fingerprint mismatch: '+name)
    c=certs.validate_source(certs.parse_json(raw['source.json']))
    certificate=certs.parse_json(raw['certificate.json'])
    result=certs.parse_json(raw['result.json'])
    if (type(result) is not dict or set(result)!={'schema','status','source_sha256','certificate_sha256',
            'model_sha256','physical_validation','producer_sources','source_roles'}
            or result['schema']!=SCHEMA or result['status']!='represented'
            or result['physical_validation']!='not_validated'
            or result['source_sha256']!=certs.sha(raw['source.json'])
            or result['certificate_sha256']!=(certs.sha(raw['certificate.json']) if certificate is not None else None)):
        raise ValueError('Result schema/identity mismatch')
    expected_sources=set(certs.verify_sources()[0])
    if (type(result['producer_sources']) is not dict or set(result['producer_sources'])!=expected_sources
            or type(result['source_roles']) is not dict or set(result['source_roles'])!=expected_sources):
        raise ValueError('Producer provenance schema mismatch')
    for name,value in result['producer_sources'].items():
        certs.hash_string(value)
        if result['source_roles'][name] not in ('referenced','loaded_snapshot_verified'):
            raise ValueError('Producer source role mismatch')
    diagnostic=certs.parse_json(raw['diagnostics.json'])
    p=_parameters(c,certs.parse_json(raw['model.json']),certs.parse_json(raw['witness.json']),
                  diagnostic,result,certificate,raw['source.json'],raw['certificate.json'])
    plan=validate_plan(certs.parse_json(raw['plan.json']))
    if (plan['source']['sha256']!=result['source_sha256']
            or plan['certificate']['sha256']!=result['certificate_sha256']
            or plan['order']!=c['order'] or plan['producer_sources']!=result['producer_sources']):
        raise ValueError('Plan/input identity mismatch')
    certs.verify_sources()
    return dict(result=result,model_parameters=p,diagnostics=diagnostic)


def read_result(output):
    certs.verify_sources(); out=certs.safe_path(output)
    payload=read_payload(out)
    raw=certs.read_bytes(out/'execution.json'); receipt=certs.parse_json(raw)
    closed_raw=certs.read_bytes(out/'execution.closed.json'); closed=certs.parse_json(closed_raw)
    if closed_raw!=certs.strict_json(closed).encode():
        raise ValueError('Noncanonical execution closure bytes')
    if (type(receipt) is not dict or set(receipt)!={'ok','status','error','child','manifest_sha256','cancelled'}
            or receipt['ok'] is not True or receipt['status']!='represented' or receipt['error'] is not None
            or receipt['cancelled']!=[]
            or receipt['manifest_sha256']!=certs.sha(certs.read_bytes(out/'manifest.json'))):
        raise ValueError('Successful collected execution required')
    child=receipt['child']
    if (type(child) is not dict or set(child)!={'created','exit_code','reaped','collected','pipes_closed','pid'}
            or any(child[k] is not True for k in ('created','reaped','collected','pipes_closed'))
            or type(child['exit_code']) is not int or child['exit_code']!=0
            or type(child['pid']) is not int or child['pid']<=0):
        raise ValueError('Invalid worker collection receipt')
    if (type(closed) is not dict or set(closed)!={'execution_sha256','cancelled'}
            or closed['execution_sha256']!=certs.sha(raw) or closed['cancelled'] is not False
            or os.path.lexists(out/'execution.cancelled.json')):
        raise ValueError('Execution closure absent or cancelled')
    certs.verify_sources()
    return dict(ok=True,**payload,execution=receipt)


certs.verify_sources()
