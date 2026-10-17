"""Real/conjugate scalar VF, R59 numerical recipe, without material tuning.

No private paths, historical coefficients, targets or temporal model enter fitting.
"""
from pathlib import Path
import hashlib
import os
import time
_LOADED_SOURCE_PATH=Path(__file__).resolve()
_LOADED_SOURCE_SHA256=hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
_BLAS1_AT_IMPORT=all(os.environ.get(k)=='1' for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'))
import numpy as np
from .passive_fit import seeds
FS=12000
WREF=2*np.pi*1000

def frequencies(f):
    if not isinstance(f,(list,tuple,np.ndarray)) or len(f)>10000: raise ValueError('Frequency count before allocation')
    f=np.asarray(f)
    if f.ndim!=1 or f.dtype.kind not in 'fiu' or len(f)>10000 or not np.isfinite(f).all() or np.any(f<0) or np.any(f>=FS/2):
        raise ValueError('Bounded frequencies within [0,Nyquist) required')
    return f.astype(float)


def warped(f): return 2j*FS*np.tan(np.pi*frequencies(f)/FS)/WREF


def basis(s,p):
    cols=[]
    for a in p:
        if a.imag==0: cols.append(1/(s-a))
        else: cols.extend([1/(s-a)+1/(s-a.conjugate()),1j/(s-a)-1j/(s-a.conjugate())])
    return np.column_stack(cols)


def expanded(p,r):
    pp=[]; rr=[]; k=0
    for a in p:
        if a.imag==0: pp.append(a);rr.append(r[k]);k+=1
        else:
            c=complex(r[k],r[k+1]);pp.extend([a,a.conjugate()]);rr.extend([c,c.conjugate()]);k+=2
    if k!=len(r):raise ValueError('Residue count mismatch')
    return np.asarray(pp),np.asarray(rr)


def regroup(poles):
    pending=list(poles); result=[]; reflected=0
    while pending:
        a=pending.pop(0)
        if abs(a.imag)<1e-8*max(1.,abs(a)):
            reflected+=int(a.real>=0);result.append(complex(-abs(a.real),0));continue
        if not pending:raise ValueError('Missing conjugate')
        j=int(np.argmin([abs(b-a.conjugate()) for b in pending]));b=pending.pop(j)
        if abs(b-a.conjugate())>1e-6*max(1.,abs(a)):raise ValueError('Conjugacy lost')
        real=(a.real+b.real)/2;imag=abs(a.imag-b.imag)/2;reflected+=2*int(real>=0)
        result.append(complex(-abs(real),imag))
    return np.array(sorted(result,key=lambda x:(abs(x.imag),x.real))),reflected


def ls(A,b):
    M=np.vstack([A.real,A.imag]);y=np.r_[b.real,b.imag]
    scale=np.linalg.norm(M,axis=0)
    if np.any(scale<=0) or not np.isfinite(M).all():raise ValueError('Degenerate LS')
    x,_,rank,_=np.linalg.lstsq(M/scale,y,rcond=1e-12)
    return x/scale,int(rank)


def fit(f,z,p,R0,*,iterations=12,seconds=145.):
    if not isinstance(z,(list,tuple,np.ndarray)) or len(z)>10000 or not isinstance(p,(list,tuple,np.ndarray)) or len(p)>128:
        raise ValueError('Fit input budget before allocation')
    if type(iterations) is not int or not 1<=iterations<=12 or type(seconds) not in (int,float) or not np.isfinite(seconds) or not 0<=seconds<=150:
        raise ValueError('Fit iteration/time budget')
    f=frequencies(f);z=np.asarray(z,complex);p=np.asarray(p,complex)
    if len(f)<2 or np.any(np.diff(f)<=0) or z.shape!=f.shape or not np.isfinite(z).all() or np.any(abs(z)==0):
        raise ValueError('Ordered nonzero finite training data required')
    n=sum(1 if a.imag==0 else 2 for a in p)
    if not 1<=n<=128 or not np.isfinite(p).all() or np.any(p.real>=0) or np.any(p.imag<0):raise ValueError('Pole budget/domain')
    if not np.isfinite(R0) or R0<0:raise ValueError('Native nonnegative DC required')
    start=time.monotonic();scale=float(max(abs(z)));s=warped(f);Y=(z-R0)/scale
    weights=1/np.maximum(abs(z)/scale,1e-5);trace=[]
    for it in range(iterations):
        if time.monotonic()-start>seconds:raise TimeoutError('Scalar fit budget')
        B=basis(s,p);Q=B-basis(np.array([0j]),p)
        values,rank=ls(np.c_[Q,-Y[:,None]*B]*weights[:,None],Y*weights)
        pp,c=expanded(p,values[n:]);new=np.linalg.eigvals(np.diag(pp)-np.ones((n,1))*c[None,:])
        p,reflected=regroup(new)
        if np.any(p.real == 0): raise ValueError('Zero pole after relocation')
        B=basis(s,p);Q=B-basis(np.array([0j]),p)
        r,rrank=ls(Q*weights[:,None],Y*weights)
        trace.append(dict(iteration=it+1,order=n,rank=rank,residue_rank=rrank,reflected_poles=reflected,
                          weighted_residual=float(np.linalg.norm((Q@r-Y)*weights)),seconds=time.monotonic()-start))
        if rank < 2*n or rrank < n: raise ValueError('Rank-deficient scalar fit')
    if time.monotonic()-start>seconds:raise TimeoutError('Scalar fit budget at completion')
    d=float((R0/scale-basis(np.array([0j]),p)@r).real[0])
    coeff=dict(schema='dcalc.scalar_rational.v1',p_real=p.real.tolist(),p_imag=p.imag.tolist(),residues=r.tolist(),
        direct_normalized=d,zscale=scale,wref=float(WREF),sample_rate_hz=FS,coefficient_domain='discrete_prewarped',
        training_domain_hz=[float(f[0]),float(f[-1])],order=n,passivity='not_assessed',physical_validation='not_validated', coefficient_origin='Native TMM geometry scalar VF',
        provenance=dict(native_dc_pa_s_m3=float(R0)))
    return coeff,trace


def response(f,c):
    p=np.asarray(c['p_real'])+1j*np.asarray(c['p_imag']);s=warped(f)
    return c['zscale']*(c['direct_normalized']+basis(s,p)@np.asarray(c['residues']))


def independent_response(f,c):
    s=warped(f);p=np.asarray(c['p_real'])+1j*np.asarray(c['p_imag']);p,r=expanded(p,c['residues'])
    out=np.full(s.shape,c['direct_normalized'],complex)
    for a,v in zip(p,r):out+=v/(s-a)
    return c['zscale']*out


def grids(top):
    train=np.r_[np.geomspace(.1,40,384,endpoint=False),np.linspace(40,top,6144)]
    def shifted(a,b,n):return a+(np.arange(n)+.3141592653589793)*(b-a)/n
    audit=dict(low=np.exp(shifted(np.log(.1),np.log(40),257)),interior=shifted(40,3000,1201),
               guard=shifted(3000,3500,401),upper=shifted(3500,5500,1009),edge=shifted(5500,5990,257))
    for f in audit.values():
        if np.intersect1d(train,f).size:raise ValueError('Training/audit overlap')
    return train,audit


def initial_poles(f,z,R0,top):
    records=seeds(f,z-R0);rows=[r for r in records if r['status']=='resolved'];p=[]
    for row in rows:
        f0=row['frequency_hz'];omega=float(warped([f0])[0].imag)
        gamma=2*np.pi*row['width_hz']/np.cos(np.pi*f0/FS)**2/WREF
        if omega*omega<=gamma*gamma/4:raise ValueError('Unresolved oscillatory seed')
        p.append(-gamma/2+1j*np.sqrt(omega*omega-gamma*gamma/4))
    for f0 in (.05,.5,5.,50.):p.append(complex(-2*np.pi*f0/WREF,0))
    tails=[]
    for frac in (.2,.6):
        f0=top+frac*(6000-top);w=float(warped([f0])[0].imag);g=.08*w
        p.append(-g/2+1j*np.sqrt(w*w-g*g/4));tails.append(dict(frequency_hz=f0,omega_normalized=w,gamma_normalized=g))
    return np.asarray(p),dict(observed_seeds=rows,unresolved_seeds=[r for r in records if r['status']!='resolved'],
                            real_relaxation_hz=[.05,.5,5.,50.],numerical_tails=tails)

