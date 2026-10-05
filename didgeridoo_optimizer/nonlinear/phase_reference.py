"""Frozen TRAIN phase maps and native held-out residuals; no regime classifier."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time
import numpy as np

from .passive_resonator import digest, integer, real
from .regime_observables import interpolate


@dataclass(frozen=True)
class PhasePlan:
    train: tuple
    validations: tuple
    scales: tuple
    section_index: int
    section_level: float
    section_method: str
    groups: tuple = (1, 2)
    state_units: tuple = ()
    section_direction: str = 'rising'
    sensitivity_partition: str = 'temporal'
    phase_tolerance: float = 1e-10
    max_phase_gap: float = .25
    min_crossings: int = 12
    cubic_iterations: int = 36
    max_points: int = 72001
    max_states: int = 386
    max_chain_bytes: int = 250*1024**2
    batch_points: int = 1024
    component_block: int = 16
    seconds: float = 170.
    schema: str = 'dcalc.phase_plan.v1'

    def __post_init__(self):
        if self.schema != 'dcalc.phase_plan.v1' or self.section_direction != 'rising':
            raise ValueError('Unknown phase schema/direction')
        if self.sensitivity_partition not in ('temporal','r37_grouped_crossings'):
            raise ValueError('Unknown TRAIN sensitivity partition')
        if self.section_method not in ('linear', 'cubic'):
            raise ValueError('Explicit linear/cubic section required')
        if type(self.train) is not tuple or len(self.train) != 2:
            raise ValueError('Immutable TRAIN pair required')
        if type(self.validations) is not tuple or not 1 <= len(self.validations) <= 16:
            raise ValueError('1..16 immutable validation windows required')
        end = -1.
        for window in (self.train, *self.validations):
            if type(window) is not tuple or len(window) != 2:
                raise ValueError('Immutable window pair required')
            a,b = (real(v, 'window', minimum=0) for v in window)
            if not end <= a < b:
                raise ValueError('Ordered disjoint TRAIN then validation windows required')
            end = b
        if type(self.scales) is not tuple or not 1 <= len(self.scales) <= self.max_states:
            raise ValueError('Complete immutable state scales required')
        for s in self.scales:
            if real(s, 'scale', minimum=0) <= 0: raise ValueError('Positive scales required')
        if type(self.state_units) is not tuple or (self.state_units and
                (len(self.state_units) != len(self.scales) or any(type(u) is not str or not 1 <= len(u) <= 64 for u in self.state_units))):
            raise ValueError('Complete immutable unit labels required')
        integer(self.section_index, 'section_index', 0, len(self.scales)-1)
        real(self.section_level, 'section_level')
        if type(self.groups) is not tuple or not 1 <= len(self.groups) <= 8:
            raise ValueError('Explicit immutable groups required')
        for g in self.groups: integer(g, 'group', 1, 8)
        if len(set(self.groups)) != len(self.groups): raise ValueError('Distinct groups required')
        # Conservative serialized-result budget: every requested group, both
        # sensitivities and all component records must remain publishable.
        if 3*len(self.groups)*len(self.validations)*(len(self.scales)*512+4096)>3*1024**2:
            raise ValueError('Result record budget exceeded before calculation')
        for key,lo,hi in [('min_crossings',3,72000),('cubic_iterations',1,64),
                          ('max_points',3,72001),('max_states',1,386),
                          ('max_chain_bytes',1,250*1024**2),('batch_points',1,1024),('component_block',1,32)]:
            integer(getattr(self,key),key,lo,hi)
        if not 0 < real(self.phase_tolerance,'phase_tolerance') < .01: raise ValueError('Phase tolerance domain')
        if not self.phase_tolerance < real(self.max_phase_gap,'max_phase_gap') <= 1: raise ValueError('Phase gap domain')
        if not 0 < real(self.seconds,'seconds') <= 180: raise ValueError('Time budget domain')
        for key in ('section_index','min_crossings','cubic_iterations','max_points','max_states','max_chain_bytes','batch_points','component_block'):
            object.__setattr__(self,key,int(getattr(self,key)))
        for key in ('section_level','phase_tolerance','max_phase_gap','seconds'):
            object.__setattr__(self,key,float(getattr(self,key)))
        object.__setattr__(self,'train',tuple(map(float,self.train)))
        object.__setattr__(self,'validations',tuple(tuple(map(float,w)) for w in self.validations))
        object.__setattr__(self,'scales',tuple(map(float,self.scales)))
        object.__setattr__(self,'groups',tuple(map(int,self.groups)))

    def as_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict: raise ValueError('Plan object required')
        v = dict(value)
        for key in ('train','validations','scales','groups','state_units'):
            if key in v:
                if type(v[key]) not in (list,tuple) or len(v[key]) > 386: raise ValueError('Bounded plan vectors required')
                v[key] = tuple(tuple(x) if type(x) in (tuple,list) else x for x in v[key])
        try: return cls(**v)
        except TypeError as exc: raise ValueError('Unknown/missing plan field') from exc


class AnalysisStopped(Exception):
    pass


class _Control:
    def __init__(self, plan, stop, callback):
        self.deadline = time.monotonic()+plan.seconds
        self.stop, self.callback = stop, callback
    def check(self):
        if self.stop(): raise AnalysisStopped('stop_requested')
        if time.monotonic() >= self.deadline: raise AnalysisStopped('analysis_time_budget')
        if self.callback is not None: self.callback()
        if self.stop(): raise AnalysisStopped('stop_requested')


def _arrays(times, states, plan, control):
    # ndarray-only API avoids arbitrary coercion and allocation before budgets.
    if type(times) is not np.ndarray or type(states) is not np.ndarray:
        raise ValueError('Explicit ndarray times/states required')
    if times.ndim != 1 or states.ndim != 2 or states.shape != (len(times),len(plan.scales)):
        raise ValueError('Complete state/time shape mismatch')
    if not 2 <= len(times) <= plan.max_points or states.shape[1] > plan.max_states:
        raise ValueError('Point/state budget exceeded before allocation')
    if any(a.dtype.kind not in 'fiu' or a.dtype.itemsize > 8 for a in (times,states)):
        raise ValueError('Real bounded numeric arrays required')
    if states.size*8+times.size*8 > plan.max_chain_bytes:
        raise ValueError('Array budget exceeded before allocation')
    for start in range(0,len(times),plan.batch_points):
        control.check()
        block=states[start:start+plan.batch_points]
        if not np.all(np.isfinite(block)) or not np.all(np.isfinite(times[start:start+plan.batch_points])):
            raise ValueError('Nonfinite states/times')
        with np.errstate(over='raise',invalid='raise',divide='raise'):
            try:
                if np.max(abs(block / np.array(plan.scales))) > 1e100:
                    raise ValueError('Unrepresentable state scales')
            except FloatingPointError as exc:
                raise ValueError('Unrepresentable state scales') from exc
    if np.any(times[1:] <= times[:-1]): raise ValueError('Strictly increasing times required')
    return times,states


def section(times, values, plan, control=None):
    """Only TRAIN arrays enter here. Boundary segments explicitly use linear roots.

    Cubic uniqueness is certified by a strictly positive quadratic derivative on
    the entire bracket. An ambiguous polynomial is unavailable, never guessed.
    """
    x = values[:,plan.section_index:plan.section_index+1]
    ix = np.flatnonzero((x[:-1,0] < plan.section_level) & (x[1:,0] >= plan.section_level))
    roots=[]; residuals=[]; boundary=0
    for begin in range(0,len(ix),plan.batch_points):
        if control: control.check()
        for i in ix[begin:begin+plan.batch_points]:
            dt=float(times[i+1]-times[i]); lo=float(times[i]); hi=float(times[i+1])
            cubic=plan.section_method=='cubic' and i>=1 and i+2<len(times)
            if cubic:
                # Lagrange polynomial in normalized segment coordinate avoids
                # ill-conditioned powers of an absolute archived clock.
                nodes=(times[i-1:i+3]-lo)/dt
                y=x[i-1:i+3,0]-plan.section_level
                coef=np.zeros(4)
                for j in range(4):
                    poly=np.array([1.]); den=1.
                    for k in range(4):
                        if j!=k:
                            poly=np.polynomial.polynomial.polymul(poly,[-nodes[k],1.]); den*=nodes[j]-nodes[k]
                    coef+=poly*(y[j]/den)
                derivative=np.array([coef[1],2*coef[2],3*coef[3]])
                checks=[derivative[0],sum(derivative)]
                if derivative[2]!=0:
                    vertex=-derivative[1]/(2*derivative[2])
                    if 0<vertex<1: checks.append(float(np.polynomial.polynomial.polyval(vertex,derivative)))
                if not np.all(np.isfinite(coef)) or min(checks)<=0:
                    return None,dict(reason='ambiguous_or_nonmonotone_cubic_segment',segment_index=int(i))
                for _ in range(plan.cubic_iterations):
                    mid=lo+(hi-lo)/2
                    value=float(interpolate(times,x,np.array([mid]),cubic=True)[0,0])
                    if value>=plan.section_level: hi=mid
                    else: lo=mid
                root=lo+(hi-lo)/2
            else:
                boundary+=int(plan.section_method=='cubic')
                root=float(times[i]+(plan.section_level-x[i,0])/(x[i+1,0]-x[i,0])*dt)
            residual=float(interpolate(times,x,np.array([root]),cubic=cubic)[0,0]-plan.section_level)
            scale=max(abs(plan.section_level),float(np.max(abs(x[max(0,i-1):min(len(x),i+3)]))),np.finfo(float).tiny)
            allowed=scale*(64*np.finfo(float).eps+32*2.**(-plan.cubic_iterations)) if cubic else scale*128*np.finfo(float).eps
            # Include representability of the absolute timestamp in the residual.
            allowed+=scale*64*np.spacing(max(abs(root),1.))/dt
            if not times[i]<=root<=times[i+1] or not np.isfinite(residual) or abs(residual)>allowed:
                return None,dict(reason='section_root_not_representable',segment_index=int(i),residual_si=residual if np.isfinite(residual) else None)
            roots.append(root);residuals.append(abs(residual))
    return np.array(roots),dict(method=plan.section_method,direction='rising',passages=len(roots),
        boundary_linear_roots=boundary,root_residual_max_si=max(residuals,default=None),
        boundary_policy='linear when four TRAIN nodes are unavailable',reason=None)


def _fit(crossings, group, plan):
    c=crossings[::group]
    if len(c)<plan.min_crossings: return None
    j=np.arange(len(c),dtype=float); centered=j-j.mean()
    period=float(centered@(c-c.mean())/(centered@centered))
    origin=float(c.mean()-period*j.mean())
    if not np.isfinite(period) or period<=0 or not np.isfinite(origin): raise ValueError('Unrepresentable period')
    residual=c-(origin+period*j)
    return dict(period_s=period,origin_s=origin,grouped_period_s=period,
        passage_frequency_hz=float(group/period),return_frequency_hz=float(1/period),
        grouped_crossings=len(c),returns=len(c)-1,timing_max_s=float(np.max(abs(residual))),
        timing_rms_s=float(np.sqrt(np.mean(residual**2))))


def _phase_index(t, period, origin, plan):
    phase=np.mod((t-origin)/period,1.)
    order=np.argsort(phase,kind='stable'); p=phase[order]
    if len(p)<2: raise ValueError('degenerate_phase_coverage')
    gaps=np.diff(np.r_[p,p[0]+1]); seam=float(p[(int(np.argmax(gaps))+1)%len(p)])
    # Put seam in the largest empty arc. Clusters straddling original 0/1
    # are thereby averaged together, with their unwrapped phase coordinates.
    phase=np.mod(phase-seam,1.);order=np.argsort(phase,kind='stable');p=phase[order]
    begin=np.r_[0,np.flatnonzero(np.diff(p)>plan.phase_tolerance)+1]
    counts=np.diff(np.r_[begin,len(p)])
    knots=np.add.reduceat(p,begin)/counts
    gap=float(np.max(np.diff(np.r_[knots,knots[0]+1])))
    info=dict(nodes=len(knots),maximum_phase_gap=gap,maximum_phase_gap_s=gap*period,
        phase_seam=seam,duplicate_samples=int(len(p)-len(knots)),
        circular_policy='largest-gap seam; adjacent close phases averaged including original 0/1',
        duplicate_phase_tolerance=plan.phase_tolerance)
    if len(knots)<3: return None,dict(info,reason='degenerate_phase_coverage')
    if gap>plan.max_phase_gap: return None,dict(info,reason='phase_coverage_gap')
    return (order,begin,counts,knots,seam),info


def _metrics(tt, zz, controls, scales, fit, plan, control):
    period,origin=fit['period_s'],fit['origin_s']
    index,coverage=_phase_index(tt,period,origin,plan)
    if index is None: return [],coverage
    order,begin,counts,knots,seam=index
    extended=np.r_[knots[-1]-1,knots,knots[0]+1]
    rows=[]
    for t,z in controls:
        n=len(t)
        rows.append(dict(samples=n,components=[],maximum_scaled=None,rms_scaled=None,
                         worst_component=None,worst_time_s=None,maximum_nearest_phase=0.))
    duplicate_max=np.zeros(zz.shape[1]);duplicate_rms=np.zeros(zz.shape[1])
    sums=[0.]*len(rows);global_max=[-1.]*len(rows)
    for first in range(0,zz.shape[1],plan.component_block):
        control.check();last=min(first+plan.component_block,zz.shape[1])
        block=np.asarray(zz[:,first:last],dtype=float)[order]
        vals=np.add.reduceat(block,begin,axis=0)/counts[:,None]
        delta=block-vals[np.repeat(np.arange(len(begin)),counts)]
        duplicate_max[first:last]=np.max(abs(delta),axis=0)
        duplicate_rms[first:last]=np.sqrt(np.mean(delta**2,axis=0))
        vals=np.vstack((vals[-1],vals,vals[0]))
        del block,delta
        for number,(t,z) in enumerate(controls):
            if not len(t): continue
            maximum=np.zeros(last-first);ss=np.zeros(last-first);worst=np.zeros(last-first)
            for a in range(0,len(t),plan.batch_points):
                control.check();b=min(a+plan.batch_points,len(t))
                q=np.mod((t[a:b]-origin)/period-seam,1.)
                ix=np.searchsorted(extended,q,side='right')-1
                fraction=(q-extended[ix])/(extended[ix+1]-extended[ix])
                predicted=(1-fraction[:,None])*vals[ix]+fraction[:,None]*vals[ix+1]
                error=np.asarray(z[a:b,first:last],dtype=float)-predicted
                absolute=abs(error); where=np.argmax(absolute,axis=0);mx=absolute[where,np.arange(last-first)]
                update=mx>=maximum;worst[update]=t[a:b][where[update]];maximum=np.maximum(maximum,mx)
                ss+=np.sum(error**2,axis=0)
                nearest=float(np.max(np.minimum(q-extended[ix],extended[ix+1]-q)))
                rows[number]['maximum_nearest_phase']=max(rows[number]['maximum_nearest_phase'],nearest)
            rms=np.sqrt(ss/len(t));scaled=maximum/scales[first:last]
            for k in range(last-first):
                rows[number]['components'].append(dict(component=first+k,maximum_si=float(maximum[k]),rms_si=float(rms[k]),
                    maximum_scaled=float(scaled[k]),rms_scaled=float(rms[k]/scales[first+k]),worst_time_s=float(worst[k])))
            k=int(np.argmax(scaled))
            if scaled[k]>global_max[number]:
                global_max[number]=float(scaled[k]);rows[number].update(maximum_scaled=float(scaled[k]),worst_component=first+k,worst_time_s=float(worst[k]))
            sums[number]+=float(np.sum(ss/scales[first:last]**2))
    coverage.update(duplicate_max_si=duplicate_max.tolist(),duplicate_rms_si=duplicate_rms.tolist(),
        duplicate_max_scaled=float(np.max(duplicate_max/scales)),duplicate_rms_scaled=float(np.sqrt(np.mean((duplicate_rms/scales)**2))))
    for i,row in enumerate(rows):
        if row['samples']:
            row.update(rms_scaled=float(np.sqrt(sums[i]/(row['samples']*zz.shape[1]))),
                maximum_nearest_phase_s=row['maximum_nearest_phase']*period,status='evaluated',reason=None)
        else: row.update(status='unavailable',reason='no_native_samples')
    return rows,coverage


def _window(t, window):
    a,b=np.searchsorted(t,window,side='left')
    # A state window must end within the recorded temporal support.
    tolerance=64*np.finfo(float).eps*len(t)*max(1.,abs(float(t[-1])))
    supported=window[0]>=t[0]-tolerance and window[1]<=t[-1]+tolerance
    return int(a),int(b),bool(supported)


def analyze(times, states, *, plan, signals=None, stop=lambda:False, callback=None):
    """Array API, inline provenance; optional signals carry THEIR native times.

    signals = {name: {times: ndarray, values: ndarray, unit: str, scale: float|None}}
    Missing/nonfinite auxiliaries do not invalidate finite complete states.
    Callback/stop run at bounded batch boundaries; no worker/simulator is started.
    """
    if not isinstance(plan,PhasePlan): raise ValueError('PhasePlan required')
    if signals is not None and (type(signals) is not dict or len(signals)>3 or any(type(k) is not str or not 1<=len(k)<=64 for k in signals)):
        raise ValueError('At most three explicitly named auxiliary signals required')
    control=_Control(plan,stop,callback)
    from ..reporting.phase_reference import sources
    result=dict(schema='dcalc.phase_reference.v1',ok=False,status='partial',reason=None,
        plan=plan.as_dict(),plan_sha256=digest(plan.as_dict()),provenance=dict(mode='inline',analysis_sources=sources()),groups=[],
        minimal_period=None,fundamental=None,orbital_stability=None,
        interpretation='Conditional descriptive repeatability at native samples; no intersample bound or experimental validation')
    for g in plan.groups:
        result['groups'].append(dict(group=g,status='not_evaluated',reason='not_started',central=None,
            sensitivities=[dict(half=i,status='not_evaluated',reason='not_started',result=None) for i in (1,2)]))
    try:
        t,z=_arrays(times,states,plan,control);sc=np.array(plan.scales)
        a,b,covered=_window(t,plan.train)
        result.update(samples=len(t),states=z.shape[1],train_samples=b-a,
                      time_support_tolerance_s=64*np.finfo(float).eps*len(t)*max(1.,abs(float(t[-1]))),
                      effective_train_samples_s=[float(t[a]),float(t[b-1])] if b>a else None)
        if not covered or b-a<2:
            for row in result['groups']:
                row.update(status='unavailable',reason='train_not_covered')
                for sensitivity in row['sensitivities']: sensitivity['reason']='train_not_covered'
            result.update(ok=True,status='processed',reason='train_not_covered');return result
        tt,zz=t[a:b],z[a:b]
        windows=[];controls=[]
        for w in plan.validations:
            c,d,available=_window(t,w)
            windows.append(dict(requested=list(w),status='pending' if available else 'unavailable',reason=None if available else 'validation_not_covered',
                effective_samples_s=[float(t[c]),float(t[d-1])] if d>c else None,available_samples=d-c,samples=d-c))
            controls.append((t[c:d],z[c:d]) if available else (t[:0],z[:0]))
        for group in result['groups']:
            control.check();g=group['group'];central_roots=None
            for half,interval in [(0,plan.train),(1,(plan.train[0],sum(plan.train)/2)),(2,(sum(plan.train)/2,plan.train[1]))]:
                ca,cb,_=_window(t,interval)
                roots,section_info=section(t[ca:cb],z[ca:cb],plan,control)
                if half==0: central_roots=roots
                fit=None if roots is None else _fit(roots,g,plan)
                if half and plan.sensitivity_partition=='r37_grouped_crossings':
                    grouped=np.array([]) if central_roots is None else central_roots[::g]
                    split=len(grouped)//2
                    chunk=grouped[:split+1] if half==1 else grouped[split:]
                    fit=_fit(chunk,1,plan)
                    if fit is not None:
                        period=fit['period_s'];ordinal=np.arange(len(grouped),dtype=float)
                        origin=float(grouped.mean()-period*ordinal.mean())
                        residual=grouped-(origin+period*ordinal)
                        fit.update(origin_s=origin,passage_frequency_hz=g/period,
                            timing_max_s=float(np.max(abs(residual))),timing_rms_s=float(np.sqrt(np.mean(residual**2))))
                    section_info=dict(method=plan.section_method,reason=None,passages=None,
                        partition='grouped crossing ordinal halves sharing middle crossing',
                        grouped_crossing_range_s=[float(chunk[0]),float(chunk[-1])] if len(chunk) else None,
                        origin_policy='origin refit on all grouped TRAIN crossings with half-estimated period')
                outcome=dict(status='unavailable',reason=section_info.get('reason') or 'insufficient_training_crossings',
                    period_s=None,section=section_info,fit_window=list(interval) if not half or plan.sensitivity_partition=='temporal' else None,
                    sensitivity_partition=plan.sensitivity_partition,map_window=list(plan.train),
                    map_policy='full TRAIN refolded for central and both half-period sensitivities',windows=[dict(w) for w in windows],signals={})
                if fit is not None:
                    with np.errstate(over='raise',invalid='raise',divide='raise'):
                        rows,coverage=_metrics(tt,zz,controls,sc,fit,plan,control)
                    outcome.update(fit,coverage=coverage,status='evaluated' if rows else 'unavailable',reason=coverage.get('reason'))
                    outcome['windows']=[dict(w,**{k:v for k,v in r.items() if k not in ('status','reason')},
                        status=r['status'] if w['status']=='pending' else w['status'],reason=r['reason'] if w['status']=='pending' else w['reason']) for w,r in zip(windows,rows)] if rows else [dict(w) for w in windows]
                    for name,signal in (signals or {}).items():
                        outcome['signals'][name]=_signal(signal,plan,fit,control)
                for window in outcome['windows']:
                    if window['status']=='pending':
                        window.update(status='not_evaluated',reason=outcome['reason'] or 'phase_map_unavailable')
                if half==0: group.update(central=outcome,status=outcome['status'],reason=outcome['reason'])
                else: group['sensitivities'][half-1].update(status=outcome['status'],reason=outcome['reason'],result=outcome)
        result.update(ok=True,status='processed',reason=None)
    except AnalysisStopped as exc:
        result.update(reason=str(exc))
    except FloatingPointError:
        result.update(reason='unrepresentable_numeric_analysis')
    return result


@np.errstate(over='raise',invalid='raise',divide='raise',under='raise')
def _signal(signal,plan,fit,control):
    try:
        if type(signal) is not dict or set(signal)!={'times','values','unit','scale'}: raise ValueError('explicit signal fields required')
        t,v=signal['times'],signal['values'];unit=signal['unit'];scale=signal['scale']
        if type(t) is not np.ndarray or type(v) is not np.ndarray or t.ndim!=1 or v.shape!=t.shape or not 2<=len(t)<=plan.max_points:
            raise ValueError('bounded signal arrays required')
        if any(x.dtype.kind not in 'fiu' or x.dtype.itemsize>8 for x in (t,v)) or t.nbytes+v.nbytes>plan.max_chain_bytes:
            raise ValueError('bounded real auxiliary arrays required')
        if any(not np.all(np.isfinite(x)) for x in (t,v)) or np.any(np.diff(t)<=0): raise ValueError('nonfinite/invalid auxiliary')
        if type(unit) is not str or not 1<=len(unit)<=64: raise ValueError('signal SI unit required')
        if scale is not None and real(scale,'signal scale',minimum=0)<=0: raise ValueError('positive signal scale required')
        def signal_window(window):
            a,b=np.searchsorted(t,window,side='left')
            eps=64*np.finfo(float).eps*len(t)*max(1.,abs(float(t[-1])))
            covered=window[0]>=t[0]-(t[1]-t[0])/2-eps and window[1]<=t[-1]+(t[-1]-t[-2])/2+eps
            return int(a),int(b),bool(covered)
        a,b,covered=signal_window(plan.train)
        if not covered or b-a<2: raise ValueError('auxiliary_train_not_covered')
        # Midpoint support is defined by its own native sample mask. No n+1
        # value is relabelled as n+1/2; missing end coverage is explicit.
        rows=[];controls=[]
        for w in plan.validations:
            c,d,covered=signal_window(w)
            controls.append((t[c:d],v[c:d,None]) if covered else (t[:0],v[:0,None]))
        values,coverage=_metrics(t[a:b],v[a:b,None],controls,np.array([1. if scale is None else scale]),fit,plan,control)
        for w,row in zip(plan.validations,values):
            row['requested']=list(w)
            row['maximum_si']=row['components'][0]['maximum_si'] if row['components'] else None
            row['rms_si']=row['components'][0]['rms_si'] if row['components'] else None
            if scale is None:
                row['maximum_scaled']=row['rms_scaled']=None
                for c in row['components']: c['maximum_scaled']=c['rms_scaled']=None
        if scale is None: coverage['duplicate_max_scaled']=coverage['duplicate_rms_scaled']=None
        return dict(status='evaluated' if values else 'unavailable',reason=coverage.get('reason'),unit=unit,scale=scale,
                    time_convention='native midpoint times; support bounded by adjacent half intervals',train_samples=b-a,coverage=coverage,windows=values)
    except (ValueError,IndexError,FloatingPointError) as exc:
        # Auxiliary arithmetic has its own failure boundary. Keep the complete
        # state analysis and never export Inf, silence overflow, or invent zero.
        reason='unrepresentable_auxiliary_numeric_analysis' if isinstance(exc,FloatingPointError) else str(exc)
        return dict(status='unavailable',reason=reason,windows=[],maximum_si=None,rms_si=None,
                    maximum_scaled=None,rms_scaled=None)
