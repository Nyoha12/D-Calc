"""Independent exact interval certificate producer, adapted from R60.

The native verifier is used only for input schema validation, never for interval
arithmetic. A stable scalar fit can be nonpassive or unresolved under this budget.
"""
from fractions import Fraction as F
from pathlib import Path
import hashlib
import math
import time
from .scalar_passivity_certificate import validate_source, number as real, hash_string
_LOADED_SOURCE_PATH=Path(__file__).resolve()
_LOADED_SOURCE_SHA256=hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
BITS=80
SCALE=1<<BITS
SCHEMA='dcalc.scalar_interval.v1'

def prepare(c):
    validate_source(c); ps=[(real(a),real(b)) for a,b in zip(c['p_real'],c['p_imag'])];rs=list(map(real,c['residues']));terms=[];k=0;norm=F(0);radius=F(0)
    for pa,b in ps:
        a=-pa;u=rs[k];k+=1;radius=max(radius,a+b)
        if b==0:
            terms.append(('real',a,u));norm+=abs(u)
        else:
            v=rs[k];k+=1;cc=a*a+b*b
            terms.append(('pair',2*(u*a-v*b)*cc,2*(u*a+v*b),cc,4*a*a,b*b-a*a))
            norm+=2*(abs(u)+abs(v))
    return real(c['direct_normalized']),terms,norm,radius

def floor_units(v):return (v.numerator*SCALE)//v.denominator

def ceil_units(v):return -floor_units(-v)

def rational_pair(v):return [str(v.numerator),str(v.denominator)]

def quotient_bounds(n0,n1,d0,d1):
    if not 0<d0<=d1:raise ValueError('Positive denominator interval required')
    if n0>=0:return n0/d1,n1/d0
    if n1<=0:return n0/d0,n1/d1
    return n0/d0,n1/d0

def interval(d,terms,lo,hi):
    if not F(0)<=lo<=hi:raise ValueError('Ordered nonnegative frequency interval required')
    x0,x1=lo*lo,hi*hi;low=floor_units(d);high=ceil_units(d)
    for t in terms:
        if t[0]=='real':
            _,a,r=t;n0=n1=r*a;d0=a*a+x0;d1=a*a+x1
        else:
            _,alpha,beta,c,g,vertex=t
            n0,n1=sorted((alpha+beta*x0,alpha+beta*x1))
            den=lambda x:(c-x)*(c-x)+g*x
            xv=min(max(vertex,x0),x1)
            d0=den(xv);d1=max(den(x0),den(x1))
        a,b=quotient_bounds(n0,n1,d0,d1);low+=floor_units(a);high+=ceil_units(b)
    return low,high

def certify(c,sha,*,seconds=140.,max_nodes=12000,max_depth=48):
    if type(max_nodes) is not int or not 1<=max_nodes<=12000 or type(max_depth) is not int or not 0<=max_depth<=48:
        raise ValueError('Declared subdivision budget required')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<=seconds<=150:
        raise ValueError('Declared wall budget required')
    hash_string(sha)
    start=time.monotonic();d,terms,norm,radius=prepare(c)
    result=dict(schema=SCHEMA,coefficient_sha256=sha,status='unresolved',arithmetic='exact_binary64_rationals_outward_fixed_point',
        fractional_bits=BITS,order=c['order'],training_domain_hz=c['training_domain_hz'],
        coefficient_domain=c['coefficient_domain'],physical_validation='not_validated',
        no_state_or_time_computed=True,source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if d<=0:
        result.update(reason='negative_infinite_limit' if d<0 else 'non_strict_infinite_limit',
                      status='nonpassive' if d<0 else 'unresolved',direct=rational_pair(d),seconds=time.monotonic()-start)
        return result
    W=F(1)
    while W<2*radius or W<4*norm/d:
        W*=2
        if W>2**40:raise ValueError('Tail endpoint exceeds study budget')
    tail=d-2*norm/W
    result['tail']=dict(start=rational_pair(W),residue_l1_bound=rational_pair(norm),pole_l1_bound=rational_pair(radius),
                        lower=rational_pair(tail),direct=rational_pair(d))
    stack=[(F(0),W,0)];leaves=[];nodes=0;deepest=0
    while stack:
        if time.monotonic()-start>=seconds or nodes>=max_nodes or len(leaves)>=2048:
            result['reason']='subdivision_budget';break
        lo,hi,depth=stack.pop();nodes+=1;deepest=max(deepest,depth)
        low,high=interval(d,terms,lo,hi)
        if low>0:
            leaves.append(dict(lo=rational_pair(lo),hi=rational_pair(hi),lower_units=low,upper_units=high));continue
        mid=(lo+hi)/2
        witness=interval(d,terms,mid,mid)
        if witness[1]<0:
            result.update(status='nonpassive',reason='negative_real_part',witness=dict(w=rational_pair(mid),lower_units=witness[0],upper_units=witness[1]));break
        if depth>=max_depth:
            result['reason']='subdivision_depth';break
        stack.extend(((mid,hi,depth+1),(lo,mid,depth+1)))
    else:
        leaves.sort(key=lambda v:F(*map(int,v['lo'])))
        result.update(status='certified_strict_positive_real_boundary',reason=None,
                      global_lower_units=min([v['lower_units'] for v in leaves]+[floor_units(tail)]))
    result.update(leaves=leaves,nodes=nodes,max_depth_reached=deepest,seconds=time.monotonic()-start,
                  remaining_intervals=len(stack),dc_units=list(interval(d,terms,F(0),F(0))))
    return result
