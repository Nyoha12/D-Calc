"""Strict scalar data and independent exact-rational certificate verification.

R60 interval arithmetic is rechecked, never trusted as a status flag. No
certificate producer, fitting code, or private runtime path is imported.
"""
from __future__ import annotations

from fractions import Fraction as Q
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
from types import FunctionType

_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
ROOT = _LOADED_SOURCE_PATH.parents[2]
SOURCES = ('didgeridoo_optimizer/nonlinear/scalar_passivity_certificate.py',
           'didgeridoo_optimizer/nonlinear/passive_realization.py',
           'didgeridoo_optimizer/reporting/passive_realization.py',
           'didgeridoo_optimizer/pipeline/passive_realization.py',
           'tools/passive_realization.py')
SOURCE_SCHEMA = 'dcalc.scalar_rational.v1'
R59_SCHEMA = 'dcalc.private.scalar_frequency.r59.v1'
CERT_SCHEMA = 'dcalc.scalar_interval.v1'
R60_SCHEMA = 'dcalc.private.scalar_interval.r60.v1'
MAX_BYTES = 4*1024**2
DEN = 2**80


def verify_sources():
    """Each loaded module keeps its own import snapshot, including early imports.

    Relative integrity only: these hashes do not authenticate an author.
    Absent producers are inspected as bytes, never imported by the reader.
    """
    hashes, roles = {}, {}
    for name in SOURCES:
        path = ROOT/name
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[name] = current
        roles[name] = 'referenced'
        modules = [sys.modules.get(name[:-3].replace('/', '.'))]
        main = sys.modules.get('__main__')
        if getattr(main, '__file__', None) and Path(main.__file__).resolve() == path:
            modules.append(main)
        for module in modules:
            if module is None:
                continue
            if (Path(module.__file__).resolve() != path
                    or getattr(module, '_LOADED_SOURCE_PATH', None) != path
                    or getattr(module, '_LOADED_SOURCE_SHA256', None) != current):
                raise ValueError('Loaded source changed since import: '+name)
            for value in tuple(vars(module).values()):
                if isinstance(value, FunctionType) and value.__module__ == module.__name__:
                    if Path(value.__code__.co_filename).resolve() != path:
                        raise ValueError('Loaded function source path mismatch: '+name)
            roles[name] = 'loaded_snapshot_verified'
    # Reuse the native model boundary without importing a producer.
    from .passive_state import verify_loaded_sources
    native, native_roles = verify_loaded_sources()
    hashes.update(native); roles.update(native_roles)
    return hashes, roles


def bounded(value, depth=0, nodes=None):
    nodes = [0] if nodes is None else nodes
    nodes[0] += 1
    if depth > 12 or nodes[0] > 150000:
        raise ValueError('JSON structure budget exceeded')
    if type(value) is dict:
        if len(value)>128 or any(type(k) is not str or len(k)>128 for k in value):
            raise ValueError('JSON mapping budget exceeded')
        for v in value.values(): bounded(v, depth+1, nodes)
    elif type(value) is list:
        if len(value)>4096: raise ValueError('JSON list budget exceeded')
        for v in value: bounded(v, depth+1, nodes)
    elif type(value) is str:
        if len(value)>4096: raise ValueError('JSON string budget exceeded')
    elif type(value) is int:
        if value.bit_length()>512: raise ValueError('JSON integer budget exceeded')
    elif type(value) is float:
        if not math.isfinite(value): raise ValueError('Nonfinite JSON number')
    elif value is not None and type(value) is not bool:
        raise ValueError('Plain JSON data required')


def strict_json(value):
    bounded(value)
    text = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)+'\n'
    if len(text.encode())>MAX_BYTES: raise ValueError('JSON byte budget exceeded')
    return text


def parse_json(raw):
    if type(raw) is not bytes or len(raw)>MAX_BYTES:
        raise ValueError('Bounded immutable JSON bytes required')
    def pairs(items):
        result = {}
        for k,v in items:
            if k in result: raise ValueError('Duplicate JSON key')
            result[k]=v
        return result
    def invalid(v): raise ValueError('Nonfinite JSON constant')
    try:
        value=json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
        bounded(value)
    except (RecursionError, UnicodeError, OverflowError) as exc:
        raise ValueError('Invalid/bounded JSON required') from exc
    return value


def safe_path(path, *, exists=True):
    if not isinstance(path, (str, Path)): raise ValueError('Path required')
    p=Path(path).absolute()
    if '..' in p.parts: raise ValueError('Parent traversal refused')
    for q in (p,*p.parents):
        if q.is_symlink(): raise ValueError('Symbolic link path refused')
    if exists and not p.exists(): raise ValueError('Required path absent')
    return p


def read_bytes(path):
    p=safe_path(path)
    fd=os.open(p, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_BYTES:
            raise ValueError('Bounded regular file required')
        raw=stream.read(MAX_BYTES+1)
        after=os.fstat(stream.fileno())
    signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if (len(raw)>MAX_BYTES or signature(before)!=signature(after)
            or signature(after)!=signature(safe_path(p).stat())):
        raise ValueError('File changed during read or exceeds budget')
    return raw


def read_json(path): return parse_json(read_bytes(path))
def sha(raw): return hashlib.sha256(raw).hexdigest()


def number(v):
    if type(v) not in (float,int) or not math.isfinite(v) or abs(v)>1e12:
        raise ValueError('Bounded finite coefficient scalar required')
    return Q(v)


def integer(v, name, low, high):
    if type(v) is not int or not low<=v<=high: raise ValueError('Invalid integer '+name)
    return v


def hash_string(v):
    if type(v) is not str or re.fullmatch('[0-9a-f]{64}',v) is None:
        raise ValueError('SHA256 required')
    return v


def validate_source(c):
    bounded(c)
    common={'schema','p_real','p_imag','residues','direct_normalized','zscale','wref',
            'sample_rate_hz','coefficient_domain','training_domain_hz','order','passivity','physical_validation'}
    if type(c) is not dict or c.get('schema') not in (SOURCE_SCHEMA,R59_SCHEMA):
        raise ValueError('Scalar rational schema required')
    legacy=c['schema']==R59_SCHEMA
    if set(c)!=(common|({'R0'} if legacy else {'coefficient_origin','provenance'})):
        raise ValueError('Scalar source keys mismatch')
    if legacy: number(c['R0'])
    elif (type(c['coefficient_origin']) is not str or not c['coefficient_origin'].strip()
          or type(c['provenance']) is not dict):
        raise ValueError('Explicit coefficient origin and provenance required')
    if any(type(c[k]) is not list for k in ('p_real','p_imag','residues')):
        raise ValueError('Plain coefficient lists required')
    if not 0<=len(c['p_real'])==len(c['p_imag'])<=128 or len(c['residues'])>128:
        raise ValueError('Pole/residue shape budget')
    n=0
    for a,b in zip(c['p_real'],c['p_imag']):
        if number(a)>=0 or number(b)<0:
            raise ValueError('Outside domain: strictly stable representative poles required')
        n+=1 if b==0 else 2
    integer(c['order'],'order',0,128)
    if c['order']!=n or len(c['residues'])!=n: raise ValueError('Scalar order mismatch')
    for v in c['residues']: number(v)
    for k in ('zscale','wref'):
        if number(c[k])<=0: raise ValueError('Strictly positive dimensional scale required')
    number(c['direct_normalized'])
    fs=integer(c['sample_rate_hz'],'sample_rate_hz',1,12000)
    if c['coefficient_domain'] not in ('continuous','discrete_prewarped'):
        raise ValueError('Unknown coefficient domain')
    if legacy and (fs!=12000 or c['coefficient_domain']!='discrete_prewarped'):
        raise ValueError('R59 frequency convention changed')
    if c['passivity']!='not_assessed' or c['physical_validation']!='not_validated':
        raise ValueError('Source attribution changed')
    band=c['training_domain_hz']
    if type(band) is not list or len(band)!=2 or not 0<number(band[0])<number(band[1])<Q(fs,2):
        raise ValueError('Declared band required below Nyquist')
    return c


def terms(c):
    validate_source(c)
    rows=[]; k=0; norm=Q(0); radius=Q(0)
    for aa,bb in zip(c['p_real'],c['p_imag']):
        a=-number(aa); b=number(bb); u=number(c['residues'][k]); k+=1; v=Q(0)
        if b: v=number(c['residues'][k]); k+=1
        rows.append((a,b,u,v))
        norm+=abs(u) if not b else 2*(abs(u)+abs(v))
        radius=max(radius,a+b)
    return rows,norm,radius


def rational(v):
    if (type(v) is not list or len(v)!=2 or any(type(x) is not str or len(x)>100
            or re.fullmatch(r'-?(0|[1-9][0-9]*)',x) is None for x in v)):
        raise ValueError('Canonical bounded rational pair required')
    n,d=map(int,v)
    if d<=0: raise ValueError('Positive rational denominator required')
    q=Q(n,d)
    if [str(q.numerator),str(q.denominator)]!=v: raise ValueError('Reduced rational required')
    return q


def rounded(v, up=False):
    return -((-v.numerator*DEN)//v.denominator) if up else (v.numerator*DEN)//v.denominator


def interval_bounds(c, rows, left, right):
    """Exact expanded quadratic extrema; outward fixed-point sums (80 bits)."""
    xl,xh=left**2,right**2
    low=rounded(number(c['direct_normalized'])); high=rounded(number(c['direct_normalized']),True)
    for a,b,u,v in rows:
        if not b:
            ns=[u*a,u*a]; ds=[a*a+xl,a*a+xh]
        else:
            cc=a*a+b*b
            num=lambda x:2*u*a*(cc+x)+2*v*b*(x-cc)
            poly=lambda x:x*x+2*(a*a-b*b)*x+cc*cc
            values=[poly(xl),poly(xh)]; critical=b*b-a*a
            if xl<critical<xh: values.append(poly(critical))
            ds=[min(values),max(values)]; ns=[num(xl),num(xh)]
        if min(ds)<=0: raise ValueError('Denominator positivity failed')
        ratios=[n/d for n in ns for d in ds]
        low+=rounded(min(ratios)); high+=rounded(max(ratios),True)
    return low,high


def validate_certificate(c, cert, source_sha256):
    bounded(cert); validate_source(c); hash_string(source_sha256)
    keys={'schema','coefficient_sha256','status','arithmetic','fractional_bits','order',
          'training_domain_hz','coefficient_domain','physical_validation','no_state_or_time_computed',
          'source_sha256','tail','reason','global_lower_units','leaves','nodes','max_depth_reached',
          'seconds','remaining_intervals','dc_units'}
    if type(cert) is not dict or set(cert)!=keys:
        raise ValueError('Complete certificate schema required')
    expected=R60_SCHEMA if c['schema']==R59_SCHEMA else CERT_SCHEMA
    if cert['schema']!=expected or cert['status']!='certified_strict_positive_real_boundary':
        raise ValueError('Strict scalar certificate required; outside supported domain')
    if cert['arithmetic']!='exact_binary64_rationals_outward_fixed_point' or cert['reason'] is not None:
        raise ValueError('Certificate arithmetic/status mismatch')
    hash_string(cert['source_sha256'])
    if cert['coefficient_sha256']!=source_sha256: raise ValueError('Certificate/source identity mismatch')
    integer(cert['fractional_bits'],'fractional_bits',80,80)
    integer(cert['order'],'order',c['order'],c['order'])
    band=cert['training_domain_hz']
    if type(band) is not list or len(band)!=2:
        raise ValueError('Certificate band types')
    for value in band: number(value)
    if (cert['training_domain_hz']!=c['training_domain_hz'] or cert['coefficient_domain']!=c['coefficient_domain']
            or cert['physical_validation']!='not_validated' or cert['no_state_or_time_computed'] is not True):
        raise ValueError('Certificate attribution mismatch')
    integer(cert['remaining_intervals'],'remaining_intervals',0,0)
    integer(cert['nodes'],'nodes',1,12000)
    integer(cert['max_depth_reached'],'max_depth_reached',0,48)
    if number(cert['seconds'])<0: raise ValueError('Invalid certificate duration')
    integer(cert['global_lower_units'],'global_lower_units',1,2**512-1)
    if (type(cert['dc_units']) is not list or len(cert['dc_units'])!=2
            or any(type(x) is not int for x in cert['dc_units'])): raise ValueError('DC enclosure types')
    tail=cert['tail']
    if type(tail) is not dict or set(tail)!={'start','residue_l1_bound','pole_l1_bound','lower','direct'}:
        raise ValueError('Tail schema')
    for value in tail.values(): rational(value)
    if not 0<rational(tail['start'])<=2**40: raise ValueError('Tail endpoint budget')
    leaves=cert['leaves']
    if type(leaves) is not list or not 1<=len(leaves)<=2048: raise ValueError('Finite cover size')
    for row in leaves:
        if type(row) is not dict or set(row)!={'lo','hi','lower_units','upper_units'}:
            raise ValueError('Leaf schema')
        rational(row['lo']); rational(row['hi'])
        if any(type(row[k]) is not int for k in ('lower_units','upper_units')): raise ValueError('Leaf integer types')
    return cert


def verify_certificate(c, cert, source_sha256, *, seconds=30.):
    verify_sources(); validate_certificate(c, cert, source_sha256)
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=175:
        raise ValueError('Certificate wall budget must lie in (0,175]')
    deadline=time.monotonic()+seconds
    rows,norm,radius=terms(c); d=number(c['direct_normalized']); tail=cert['tail']; W=rational(tail['start'])
    if d<=0 or W<2*radius or W<4*norm/d: raise ValueError('Invalid infinite tail coverage')
    if (rational(tail['direct'])!=d or rational(tail['residue_l1_bound'])!=norm
            or rational(tail['pole_l1_bound'])!=radius): raise ValueError('Tail coefficients mismatch')
    lower=d-2*norm/W
    if rational(tail['lower'])!=lower or lower<=0: raise ValueError('Tail lower bound mismatch')
    previous=Q(0); minimum=rounded(lower)
    for row in cert['leaves']:
        if time.monotonic()>=deadline: raise TimeoutError('Certificate verification budget exceeded')
        lo,hi=rational(row['lo']),rational(row['hi'])
        if lo!=previous or hi<=lo or hi>W: raise ValueError('Gap/overlap in finite cover')
        ll,hh=interval_bounds(c,rows,lo,hi)
        if row['lower_units']!=ll or row['upper_units']!=hh or ll<=0:
            raise ValueError('Invalid exact leaf enclosure')
        minimum=min(minimum,ll); previous=hi
    if previous!=W or cert['global_lower_units']!=minimum: raise ValueError('Incomplete global certificate')
    if cert['dc_units']!=list(interval_bounds(c,rows,Q(0),Q(0))): raise ValueError('DC enclosure mismatch')
    verify_sources()
    if time.monotonic()>=deadline: raise TimeoutError('Certificate verification budget exceeded')
    return dict(kind='exact_scalar_certificate',verified=True,leaf_count=len(cert['leaves']),
                global_lower_units=minimum,fractional_bits=80,
                global_lower_pa_s_m3_approx=float(Q(minimum,DEN)*number(c['zscale'])),
                coefficient_sha256=source_sha256,physical_validation='not_validated')


# Called after all definitions: each module owns its original snapshot.
# The complete source set must be installed before importing this opt-in feature.
verify_sources()
