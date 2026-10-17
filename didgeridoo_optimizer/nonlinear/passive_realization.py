"""Opt-in scalar rational -> genuine native PassiveState, without refitting.

The scalar proof is exact rational arithmetic. Storage and representation are
floating-point checks, with fixed policy independent of acoustic validation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import time

_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()

import numpy as np
from . import scalar_passivity_certificate as certs
from .passive_state import PassiveState, bounded_vector

POLICY = dict(response_maxrel=2e-8, dc_relative=2e-8, direct_relative=2e-12,
              matrix_normalized=2e-10, transform_condition_eps=1e-8)
# Fixed BEFORE transfer auditing; the first admissible storage is selected.
DELTA_FRACTIONS = (.1, .03, .01, .003, .001, .0003, .0001)
EPS = np.finfo(float).eps


class RealizationRefused(ValueError):
    def __init__(self, reason, diagnostics=None):
        super().__init__(reason)
        self.diagnostics = {} if diagnostics is None else diagnostics


def _deadline(deadline):
    if time.monotonic()>=deadline: raise TimeoutError('Conversion wall budget exceeded')


def residual(actual, expected):
    a,b=np.asarray(actual),np.asarray(expected)
    absolute=float(np.linalg.norm(a-b))
    scale=max(float(np.linalg.norm(a)),float(np.linalg.norm(b)))
    normalized=absolute/scale if scale else (0. if absolute==0 else float('inf'))
    return dict(absolute=absolute,scale=scale,normalized=normalized)


def _check_residual(name, actual, expected, records):
    records[name]=residual(actual,expected)
    if not np.isfinite(records[name]['normalized']) or records[name]['normalized']>POLICY['matrix_normalized']:
        raise RealizationRefused('Matrix representation ceiling: '+name,records)


def source_response(c, frequency_hz, *, discrete=True):
    """Direct residue expression, independent of state-space evaluation."""
    certs.validate_source(c)
    f=bounded_vector(frequency_hz,'frequency_hz',4096)
    if np.any(f<0) or (discrete and np.any(f>=c['sample_rate_hz']/2)):
        raise ValueError('Frequency outside supported domain')
    s=(2j*c['sample_rate_hz']*np.tan(np.pi*f/c['sample_rate_hz']) if discrete else 2j*np.pi*f)/c['wref']
    h=np.full(len(f),complex(c['direct_normalized'])); k=0
    with np.errstate(over='raise',invalid='raise',divide='raise',under='raise'):
        for a,b in zip(c['p_real'],c['p_imag']):
            u=c['residues'][k]; k+=1
            if b==0: h+=u/(s-a)
            else:
                v=c['residues'][k]; k+=1
                h+=(u+1j*v)/(s-a-1j*b)+(u-1j*v)/(s-a+1j*b)
        h*=c['zscale']
    if not np.all(np.isfinite(h)): raise RealizationRefused('Nonfinite source response')
    return h


def real_blocks(c):
    """Every pole/residue retained; diagonal residue-amplitude coordinates."""
    certs.validate_source(c)
    n=c['order']; A=np.zeros((n,n)); B=np.zeros(n); C=np.zeros(n); i=k=0
    balances=[]
    for a,b in zip(c['p_real'],c['p_imag']):
        u=c['residues'][k]; k+=1
        if b==0:
            # Zero residues are not removed: they will fail storage admission.
            scale=np.sqrt(abs(u)) if u else 1.
            A[i,i]=a; B[i]=scale; C[i]=u/scale; i+=1
        else:
            v=c['residues'][k]; k+=1
            scale=np.sqrt(2*np.hypot(u,v)) if u or v else 1.
            A[i:i+2,i:i+2]=[[a,-b],[b,a]]
            B[i]=scale; C[i:i+2]=[2*u/scale,-2*v/scale]; i+=2
        balances.append(float(scale))
    return A,B,C,float(c['direct_normalized']),balances


def _storage(A,B,C,D,delta):
    n=len(B); Ad=A+delta*np.eye(n)
    F=Ad-np.outer(B,C)/(2*D)
    H=np.block([[F,np.outer(B,B)/(2*D)],[-np.outer(C,C)/(2*D),-F.T]])
    eig,V=np.linalg.eig(H)
    guard=float(64*EPS*np.linalg.norm(H,ord=np.inf))
    select=eig.real < -guard
    record=dict(delta=delta,hamiltonian_guard=guard,stable_dimension=int(np.sum(select)),
                imaginary_axis_gap=float(np.min(abs(eig.real))))
    if np.sum(select)!=n or np.any(abs(eig.real)<=guard):
        raise RealizationRefused('Auxiliary stable subspace not separated',record)
    U=V[:,select]
    # Orthonormalizing the same invariant subspace improves basis conditioning.
    U=np.linalg.qr(U)[0]; U1,U2=U[:n],U[n:]
    cond=float(np.linalg.cond(U1)); record['subspace_condition']=cond
    if not np.isfinite(cond) or cond*EPS>POLICY['transform_condition_eps']:
        raise RealizationRefused('Auxiliary subspace conditioning ceiling',record)
    raw=np.linalg.solve(U1.T,U2.T).T
    records={}
    _check_residual('storage_imaginary',raw,raw.real,records)
    _check_residual('storage_raw_symmetry',raw.real,raw.real.T,records)
    # Only after the measured raw defect passes. The next checks concern THIS P.
    P=(raw.real+raw.real.T)/2
    T=np.linalg.cholesky(P).T
    record.update(storage_condition=float(np.linalg.cond(P)),transform_condition=float(np.linalg.cond(T)))
    record['transform_condition_eps']=float(record['transform_condition']*EPS)
    if record['transform_condition_eps']>POLICY['transform_condition_eps']:
        raise RealizationRefused('Transform conditioning ceiling',record)
    q=C-P@B
    ric=-Ad.T@P-P@Ad-np.outer(q,q)/(2*D)
    scale=max(np.linalg.norm(Ad.T@P),np.linalg.norm(P@Ad),np.linalg.norm(np.outer(q,q)/(2*D)))
    records['auxiliary_riccati']=dict(absolute=float(np.linalg.norm(ric)),scale=float(scale),
                                     normalized=float(np.linalg.norm(ric)/scale) if scale else 0.)
    if records['auxiliary_riccati']['normalized']>POLICY['matrix_normalized']:
        raise RealizationRefused('Auxiliary Riccati residual ceiling',dict(record,matrices=records))
    _check_residual('storage_factor',T.T@T,P,records)
    # Recompute ORIGINAL-system LMI: delta is never exported as a pole shift.
    WP=np.block([[-A.T@P-P@A,q[:,None]],[q[None,:],np.array([[2*D]])]])
    symmetry=residual(WP,WP.T)
    if symmetry['normalized']>POLICY['matrix_normalized']: raise RealizationRefused('Original LMI asymmetry',record)
    np.linalg.cholesky((WP+WP.T)/2)
    record['original_lmi_min_eigenvalue']=float(np.min(np.linalg.eigvalsh((WP+WP.T)/2)))
    At=np.linalg.solve(T.T,(T@A).T).T
    Bt=T@B; Ct=np.linalg.solve(T.T,C)
    J=(At-At.T)/2; R=-(At+At.T)/2
    k=(Ct-Bt)/2; g=(Ct+Bt)/2
    Wn=np.block([[R,k[:,None]],[k[None,:],np.array([[D]])]])
    L=np.linalg.cholesky(Wn)  # No clipping, jitter, or added loss.
    _check_residual('normalized_factor',L@L.T,Wn,records)
    _check_residual('similarity_A',At@T,T@A,records)
    _check_residual('similarity_B',Bt,T@B,records)
    _check_residual('similarity_C',Ct@T,C,records)
    invT=np.linalg.solve(T,np.eye(n))
    S=np.zeros((n+1,n+1)); S[:n,:n]=invT; S[n,n]=1.
    _check_residual('original_lmi_congruence',S.T@WP@S,2*Wn,records)
    record['matrices']=records
    return J,g,L,At,Bt,Ct,record,T


def _native_model(diagnostics, *args, **kwargs):
    try:
        return PassiveState(*args, **kwargs)
    except (ValueError, ArithmeticError, np.linalg.LinAlgError) as exc:
        raise RealizationRefused('Native representation refused: '+str(exc), diagnostics) from exc


def _relative(actual, expected):
    absolute=abs(actual-expected)
    return dict(absolute=float(absolute),scale=float(abs(expected)),
                relative=float(absolute/abs(expected)) if expected else (0. if absolute==0 else None),
                zero_reference=bool(expected==0))


def convert(source_bytes, certificate_bytes=None, *, frequency_hz=None, seconds=175.):
    """In-memory conversion of immutable JSON bytes; returns model + evidence.

    No files are read as numerical inputs, no output or simulation is performed.
    General systems require a separately verifiable strict scalar certificate.
    """
    certs.verify_sources()
    if type(seconds) not in (int,float) or not np.isfinite(seconds) or not 0<seconds<=175:
        raise ValueError('Conversion wall budget must lie in (0,175]')
    deadline=time.monotonic()+seconds
    c=certs.validate_source(certs.parse_json(source_bytes)); source_hash=certs.sha(source_bytes)
    certificate=certs.parse_json(certificate_bytes) if certificate_bytes is not None else None
    D=c['direct_normalized']; n=c['order']
    if n==0:
        if D<0: raise RealizationRefused('Negative constant resistance is active')
        if certificate is not None:
            proof=certs.verify_certificate(c,certificate,source_hash,seconds=min(30.,seconds))
        else: proof=dict(kind='analytic_constant',verified=True,constant_normalized=D,
                         coefficient_sha256=source_hash,physical_validation='not_validated')
    else:
        if D<=0: raise RealizationRefused('Outside general domain: strictly positive direct term required; marginal passivity not classified')
        if certificate is None: raise RealizationRefused('Strict independent scalar certificate required')
        proof=certs.verify_certificate(c,certificate,source_hash,seconds=min(30.,seconds))
    _deadline(deadline)
    provenance=dict(source_sha256=source_hash,certificate_sha256=certs.sha(certificate_bytes) if certificate_bytes else None,
        source_schema=c['schema'],source_passivity=c['passivity'],source_physical_validation=c['physical_validation'],
        scalar_proof=proof,representation_policy=POLICY,conversion='PASSIVE-REALIZATION-01',
        training_domain_hz=c['training_domain_hz'],original_provenance=c.get('provenance',{}))
    origin=c.get('coefficient_origin','Frozen R59 scalar coefficients; separate R60 scalar certificate')
    diagnostic=dict(policy=POLICY.copy(),storage_attempts=[],scalar_proof=proof,grid_is_continuous_bound=False)
    with np.errstate(over='raise',invalid='raise',divide='raise',under='raise'):
        if not n:
            model=_native_model(diagnostic,[],[],[[np.sqrt(D)]],omega_ref=c['wref'],Zscale=c['zscale'],
                sample_rate_hz=c['sample_rate_hz'],coefficient_origin=origin,
                domain=c['coefficient_domain'],provenance=provenance)
            diagnostic.update(method='analytic_constant',selected_storage=None,matrices={})
            _check_residual('constant_factor',model.D,D,diagnostic['matrices'])
        else:
            A,B,C,D,balances=real_blocks(c)
            diagnostic['residue_coordinate_scales']=balances
            minimum=min(-float(x) for x in c['p_real'])
            selected=None
            for fraction in DELTA_FRACTIONS:
                _deadline(deadline)
                delta=minimum*fraction
                try:
                    selected=_storage(A,B,C,D,delta)
                    diagnostic['storage_attempts'].append(dict(fraction=fraction,status='selected',diagnostics=selected[-2]))
                    break
                except (ValueError,np.linalg.LinAlgError,FloatingPointError) as exc:
                    diagnostic['storage_attempts'].append(dict(fraction=fraction,delta=delta,status='refused',
                        reason=str(exc),diagnostics=getattr(exc,'diagnostics',{})))
            if selected is None: raise RealizationRefused('No representable interior storage in declared bounded sequence',diagnostic)
            J,g,L,At,Bt,Ct,record,T=selected
            diagnostic.update(method='balanced_auxiliary_riccati',selected_storage=record,matrices={})
            # Selection is final before constructing/auditing native transfer.
            model=_native_model(diagnostic,J,g,L,omega_ref=c['wref'],Zscale=c['zscale'],sample_rate_hz=c['sample_rate_hz'],
                coefficient_origin=origin,domain=c['coefficient_domain'],provenance=provenance)
            for name,actual,expected in (('native_A',model.A,At),('native_B',model.B,Bt),
                                         ('native_C',model.C,Ct),('native_D',model.D,D)):
                _check_residual(name,actual,expected,diagnostic['matrices'])
        _deadline(deadline)
        if frequency_hz is None:
            lo,hi=c['training_domain_hz']
            frequency_hz=np.unique(np.r_[0.,np.geomspace(lo,hi,384),np.linspace(lo,hi,384)])
            grid_origin='deterministic_declared_band_with_DC'
        else: grid_origin='caller_control_grid'
        f=bounded_vector(frequency_hz,'frequency_hz',4096)
        if not len(f): raise ValueError('Nonempty representation control grid required')
        audits={}
        for discrete in (False,True):
            _deadline(deadline)
            expected=source_response(c,f,discrete=discrete)
            actual=model.discrete_response(f) if discrete else model.continuous_response(f)
            absolute=abs(actual-expected); zero=abs(expected)==0
            if np.any(absolute[zero]!=0): raise RealizationRefused('Nonzero response at exact source zero',diagnostic)
            maximum=float(np.max(absolute[~zero]/abs(expected[~zero]))) if np.any(~zero) else 0.
            name='discrete' if discrete else 'continuous'
            audits[name]=dict(maxrel=maximum,max_absolute=float(np.max(absolute)),zero_reference_count=int(np.sum(zero)))
            if maximum>POLICY['response_maxrel']: raise RealizationRefused('Response representation ceiling: '+name,dict(diagnostic,response=audits))
        # Exact binary64 rational DC avoids cancellation in the scalar oracle.
        rows,_,_=certs.terms(c)
        exact_dc=certs.number(D)
        for a,b,u,v in rows:
            exact_dc+=u/a if not b else 2*(u*a-v*b)/(a*a+b*b)
        dc=float(exact_dc*certs.number(c['zscale']))
        direct=float(certs.number(D)*certs.number(c['zscale']))
        diagnostic.update(response=audits,dc=_relative(model.dc_pa_s_m3,dc),
            direct=_relative(model.feedthrough_pa_s_m3,direct),
            source_dc_pa_s_m3=dc,source_direct_pa_s_m3=direct,
            source_R0_attribution=c.get('R0'),grid=dict(origin=grid_origin,frequency_hz=f.tolist(),
                training_domain_hz=c['training_domain_hz'],
                extrapolation_count=int(np.sum((f<c['training_domain_hz'][0])|(f>c['training_domain_hz'][1])))))
        for name in ('dc','direct'):
            error=diagnostic[name]['relative']
            if error is None or error>POLICY[name+'_relative']:
                raise RealizationRefused('Representation ceiling: '+name,diagnostic)
    certs.verify_sources(); _deadline(deadline)
    return dict(model=model,diagnostics=diagnostic,source=c,certificate=certificate,
                source_sha256=source_hash,certificate_sha256=certs.sha(certificate_bytes) if certificate_bytes else None,
                witness=dict(T=T.tolist() if n else [],balance_scales=balances if n else []))


certs.verify_sources()
