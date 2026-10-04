"""Declared finite-window observations; neither a toot score nor a stability test."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import numpy as np

from .passive_resonator import digest, integer, real

MAX_STEPS = 72000
MAX_STATES = 386
MAX_ARRAY_BYTES = 224 * 1024**2


def numeric_array(value, name, shape):
    """Check dimensions and scalar types BEFORE conversion or allocation."""
    if type(value) is np.ndarray:
        if value.shape != shape or value.dtype.kind not in 'fiu' or value.nbytes > MAX_ARRAY_BYTES:
            raise ValueError(name+': bounded real shape required')
        if not np.all(np.isfinite(value)):
            raise ValueError(name+': finite values required')
    elif type(value) in (list, tuple):
        if len(value) != shape[0]:
            raise ValueError(name+': shape mismatch')
        if len(shape) == 2:
            for row in value:
                if type(row) not in (list, tuple) or len(row) != shape[1]:
                    raise ValueError(name+': rectangular shape required')
                for x in row:
                    real(x, name)
        else:
            for x in value:
                real(x, name)
    else:
        raise ValueError(name+': explicit numeric array required')
    if math.prod(shape)*8 > MAX_ARRAY_BYTES:
        raise ValueError(name+': allocation budget exceeded')
    out = np.asarray(value, dtype=float)
    if not np.all(np.isfinite(out)):
        raise ValueError(name+': finite values required')
    return out


@dataclass(frozen=True)
class ObservationPlan:
    """Immutable experimental protocol, fixed before progression.

    Scales and section are supplied explicitly; defaults are declared numerical
    criteria, not calibrated or universal scientific policy.
    """
    windows: tuple
    scales: tuple
    section_index: int
    section_level: float
    groups: tuple = (1, 2)
    min_returns: int = 12
    ac_floor_pa: float = .01
    equilibrium_span: float = 1e-6
    period_span: float = .002
    amplitude_span: float = .005
    envelope_drift: float = .005
    return_scaled: float = .003
    return_relative: float = .01
    shape_span: float = .005
    mean_span: float = .001
    interpolation_scaled: float = .005
    max_phase_points: int = 4097
    max_returns: int = 1024
    max_shape_cells: int = 1600000
    max_interpolation_cells: int = 32000000
    protocol: str = 'dcalc.regime_observation.v1'

    def __post_init__(self):
        if type(self.windows) is not tuple or not 1 <= len(self.windows) <= 16:
            raise ValueError('1..16 immutable windows required')
        end = -1.
        for w in self.windows:
            if type(w) is not tuple or len(w) != 2:
                raise ValueError('Window must be immutable (start,end)')
            a,b = (real(v,'window',minimum=0.) for v in w)
            if a < end or not a < b <= 6:
                raise ValueError('Disjoint ordered windows within chain [0,6] required')
            end = b
        if type(self.scales) is not tuple or not 2 <= len(self.scales) <= MAX_STATES:
            raise ValueError('Immutable complete state scales required')
        for s in self.scales:
            if real(s,'state scale',minimum=0.) <= 0:
                raise ValueError('Positive scales required')
        integer(self.section_index,'section_index',0,len(self.scales)-1)
        real(self.section_level,'section_level')
        if type(self.groups) is not tuple or not 1 <= len(self.groups) <= 8:
            raise ValueError('Immutable group list required')
        for k in self.groups:
            integer(k,'group',1,8)
        if len(set(self.groups)) != len(self.groups):
            raise ValueError('Duplicate group')
        integer(self.min_returns,'min_returns',2,1024)
        integer(self.max_returns,'max_returns',self.min_returns,1024)
        integer(self.max_phase_points,'max_phase_points',65,4097)
        integer(self.max_shape_cells,'max_shape_cells',130,1600000)
        integer(self.max_interpolation_cells,'max_interpolation_cells',130,32000000)
        for key in ('ac_floor_pa','equilibrium_span','period_span','amplitude_span',
                    'envelope_drift','return_scaled','return_relative','shape_span',
                    'mean_span','interpolation_scaled'):
            if real(getattr(self,key),key,minimum=0.) <= 0:
                raise ValueError('Strictly positive criteria required')
        if self.protocol != 'dcalc.regime_observation.v1':
            raise ValueError('Unknown observation protocol')

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise ValueError('Observation mapping required')
        if len(value)>32:
            raise ValueError('Observation key budget')
        value = dict(value)
        if type(value.get('windows')) not in (list,tuple) or len(value['windows'])>16 or any(type(w) not in (list,tuple) or len(w)!=2 for w in value['windows']):
            raise ValueError('Bounded window pairs required')
        for key in ('scales','groups','windows'):
            if type(value.get(key)) not in (tuple,list):
                raise ValueError('Explicit observation vectors required')
            if len(value[key]) > (MAX_STATES if key=='scales' else 16):
                raise ValueError('Observation vector budget')
            value[key] = tuple(tuple(w) if type(w) in (list,tuple) else w for w in value[key]) if key=='windows' else tuple(value[key])
        return cls(**value)


def interpolate(t, z, targets, *, cubic=False):
    """Local Lagrange sensitivity, never described as an error bound."""
    idx = np.clip(np.searchsorted(t,targets,side='right')-1,0,len(t)-2)
    frac = (targets-t[idx])/(t[idx+1]-t[idx])
    out = (1-frac[:,None])*z[idx]+frac[:,None]*z[idx+1]
    if cubic:
        good = (idx>=1)&(idx+2<len(t))
        ii = idx[good,None]+np.arange(-1,3)
        nodes = t[ii]
        weights = np.ones(nodes.shape)
        for j in range(4):
            for k in range(4):
                if j != k:
                    weights[:,j] *= (targets[good]-nodes[:,k])/(nodes[:,j]-nodes[:,k])
        out[good] = sum(weights[:,j,None]*z[ii[:,j]] for j in range(4))
    return out


def span(values):
    return float(np.ptp(values)/max(abs(np.mean(values)),1e-300))


def fft_peak(pressure, fs, floor):
    ac = pressure-np.mean(pressure)  # remove DC BEFORE Hann
    if len(ac) < 4 or float(np.std(ac)) < floor:
        return None
    spectrum = abs(np.fft.rfft(ac*np.hanning(len(ac))))
    if not np.any(spectrum[1:] > 0):
        return None
    return float(np.fft.rfftfreq(len(ac),1/fs)[1+np.argmax(spectrum[1:])])


def candidate(t,z,p,sc,crossings,group,plan,fs):
    ct = crossings[::group]
    count = len(ct)-1
    out = dict(group=group, returns=max(0,count), status='insufficient_observation',
               reason='too_few_disjoint_returns', checks={}, return_frequency_hz=None)
    if count < plan.min_returns:
        return out
    if count > plan.max_returns:
        return dict(out,reason='return_budget_exceeded')
    periods = np.diff(ct)
    points = max(65,2*math.ceil(float(max(periods))*fs)+1)
    if (points > plan.max_phase_points or points*len(sc)>plan.max_shape_cells
            or count*points*len(sc)>plan.max_interpolation_cells):
        return dict(out,reason='shape_allocation_or_work_budget_exceeded')
    phase = np.linspace(0.,1.,points)
    low = high = None
    means=[]; amps=[]; interp_error=0.
    # Only one return's reconstruction is resident. All state components enter
    # the extrema; no mean/RMS across states can hide an unresolved component.
    for a,b in zip(ct[:-1],ct[1:]):
        target = a+(b-a)*phase
        shape = interpolate(t,z,target)/sc
        sensitivity = interpolate(t,z,target,cubic=True)/sc
        interp_error = max(interp_error,float(np.max(abs(sensitivity-shape))))
        low = shape.copy() if low is None else np.minimum(low,shape)
        high = shape.copy() if high is None else np.maximum(high,shape)
        means.append(np.trapezoid(shape,phase,axis=0))
        ps = np.interp(target,t,p)
        pm = np.trapezoid(ps,phase)
        amps.append(float(np.sqrt(np.trapezoid((ps-pm)**2,phase))))
    at = interpolate(t,z,ct)
    indices=np.clip(np.searchsorted(t,ct,side='right')-1,0,len(t)-2)
    lo=t[indices].copy();hi=t[indices+1].copy()
    section=z[:,plan.section_index:plan.section_index+1]
    for _ in range(28):
        mid=(lo+hi)/2
        above=interpolate(t,section,mid,cubic=True)[:,0]>plan.section_level
        hi=np.where(above,mid,hi);lo=np.where(above,lo,mid)
    cubic_ct=(lo+hi)/2
    cat = interpolate(t,z,cubic_ct,cubic=True)
    delta = abs(np.diff(at,axis=0))
    ret = float(np.max(delta/sc))
    relative = float(np.max(delta/np.maximum(np.std(z,axis=0),sc*1e-5)))
    slope = float(np.polyfit((ct[:-1]+ct[1:])/2,np.log(np.maximum(amps,1e-300)),1)[0])
    metrics = dict(period_relative_span=span(periods), amplitude_relative_span=span(amps),
        envelope_log_drift=abs(slope)*float(ct[-1]-ct[0]), log_amplitude_slope_s=slope,
        return_scaled_max=ret,return_relative_ac_max=relative,
        shape_scaled_span=float(np.max(high-low)),
        state_mean_scaled_span=float(np.max(np.ptp(means,axis=0))),
        interpolation_sensitivity_scaled_max=interp_error,
        cubic_return_scaled_max=float(np.max(abs(np.diff(cat,axis=0))/sc)),
        crossing_time_sensitivity_max_s=float(np.max(abs(cubic_ct-ct))),
        section_state_sensitivity_scaled_max=float(np.max(abs(cat-at)/sc)))
    limits = dict(period_relative_span=plan.period_span,amplitude_relative_span=plan.amplitude_span,
        envelope_log_drift=plan.envelope_drift,return_scaled_max=plan.return_scaled,
        return_relative_ac_max=plan.return_relative,shape_scaled_span=plan.shape_span,
        state_mean_scaled_span=plan.mean_span,interpolation_sensitivity_scaled_max=plan.interpolation_scaled,
        cubic_return_scaled_max=plan.return_scaled,section_state_sensitivity_scaled_max=plan.interpolation_scaled)
    checks = {key:metrics[key]<=value for key,value in limits.items()}
    ok = all(checks.values())
    return dict(out,status='recurrence_observed' if ok else 'unresolved',
        reason='declared_criteria_pass' if ok else 'observation_insufficiently_resolved',
        checks=checks,failed_checks=[k for k,v in checks.items() if not v],**metrics,
        return_frequency_hz=float(1/np.mean(periods)),periods_s=periods.tolist(),
        crossings_s=ct.tolist(),phase_points=points,
        pressure_ac_per_return_pa=float(np.mean(amps)),state_mean_scaled=np.mean(means,axis=0).tolist(),
        covered_s=float(ct[-1]-ct[0]), interpolation_scope='linear/cubic reconstruction and section root in the same sampled bracket; sensitivity, not rigorous bound',section_time_sensitivity_status='observed_sensitivity')


def analyze(times, states, midpoint_pressure, *, sample_rate_hz, plan):
    """Analyze disjoint declared windows without choosing or changing a group."""
    if not isinstance(plan,ObservationPlan):
        raise ValueError('Frozen ObservationPlan required')
    fs = integer(sample_rate_hz,'sample_rate_hz',1000,12000)
    if type(times) not in (list,tuple,np.ndarray):
        raise ValueError('Explicit time vector required')
    n = len(times)
    if not 1 <= n <= MAX_STEPS+1:
        raise ValueError('Time sample budget exceeded')
    t = numeric_array(times,'times',(n,))
    z = numeric_array(states,'states',(n,len(plan.scales)))
    pressure = numeric_array(midpoint_pressure,'pressure',(n-1,))
    if np.any(np.diff(t)<=0) or (n>1 and not np.allclose(np.diff(t),1/fs,rtol=1e-9,atol=1e-14)):
        raise ValueError('Monotone native time grid required')
    sc = np.array(plan.scales)
    with np.errstate(over='raise',invalid='raise',divide='raise'):
        try:
            if not np.all(np.isfinite(z/sc)) or np.max(abs(z/sc))>1e100 or (len(pressure) and np.max(abs(pressure))>1e100):
                raise ValueError('Unrepresentable observation scale')
        except FloatingPointError as exc:
            raise ValueError('Unrepresentable observation normalization') from exc
    result = dict(schema='dcalc.regime_observations.v1',plan_sha256=digest(plan.as_dict()),
        status='insufficient_observation',windows=[],groups=[],minimal_period_s=None,
        fundamental_hz=None,minimal_period_status='not_identified',
        orbital_stability=None,orbital_stability_status='not_evaluated',
        frequency_interpretation='FFT auxiliary, section passages and grouped returns are distinct; no played note',
        evidence_accounting='disjoint windows and nonoverlapping returns within each candidate; candidates share evidence')
    if n<3:
        return result
    pt = (t[:-1]+t[1:])/2
    ps = np.interp(t,pt,pressure)
    for start,end in plan.windows:
        row=dict(start_s=start,end_s=end,status='insufficient_observation',
            reason='declared_window_not_covered',fft_auxiliary_hz=None,passage_frequency_hz=None,candidates=[])
        if start < t[0]-1e-12 or end > t[-1]+1e-10:
            result['windows'].append(row);continue
        mask=(t>=start)&(t<end);pmask=(pt>=start)&(pt<end)
        tw,zw,pw=t[mask],z[mask],ps[mask]
        if len(tw)<32 or not np.any(pmask):
            result['windows'].append(dict(row,reason='too_few_native_samples'));continue
        native=pressure[pmask];ac=float(np.std(native));ss=float(np.max(np.ptp(zw,axis=0)/sc))
        row.update(native_midpoint_pressure_mean_pa=float(np.mean(native)),pressure_ac_rms_pa=ac,
            state_scaled_span=ss,fft_auxiliary_hz=fft_peak(native,fs,plan.ac_floor_pa),
            section_index=plan.section_index,section_level=plan.section_level,
            effective_start_s=float(tw[0]),effective_end_s=float(tw[-1]),samples=len(tw))
        if ac<plan.ac_floor_pa:
            row.update(status='equilibrium_observed' if ss<=plan.equilibrium_span else 'unresolved',
                       reason='full_state_and_AC_stationary' if ss<=plan.equilibrium_span else 'state_drift_without_pressure_AC')
        else:
            y=zw[:,plan.section_index]-plan.section_level
            ix=np.flatnonzero((y[:-1]<=0)&(y[1:]>0))
            ct=tw[ix]-y[ix]*(tw[ix+1]-tw[ix])/(y[ix+1]-y[ix])
            row['passages']=len(ct)
            row['passage_frequency_hz']=float((len(ct)-1)/(ct[-1]-ct[0])) if len(ct)>1 else None
            row['candidates']=[candidate(tw,zw,pw,sc,ct,k,plan,fs) for k in plan.groups]
            row.update(status='recurrence_observed' if any(c['status']=='recurrence_observed' for c in row['candidates']) else 'unresolved',
                reason='all_fixed_candidates_retained')
        result['windows'].append(row)
    for k in plan.groups:
        rows=[next((c for c in w['candidates'] if c['group']==k),None) for w in result['windows']]
        common=all(c is not None and c['status']=='recurrence_observed' for c in rows)
        checks={}
        if common:
            checks=dict(frequency=span([c['return_frequency_hz'] for c in rows])<=plan.period_span,
                amplitude=span([c['pressure_ac_per_return_pa'] for c in rows])<=plan.amplitude_span,
                state_means=float(np.max(np.ptp([c['state_mean_scaled'] for c in rows],axis=0)))<=plan.mean_span)
            common=all(checks.values())
        result['groups'].append(dict(group=k,status='recurrence_observed' if common else 'not_established',
            reason='same_group_all_disjoint_windows' if common else 'one_or_more_windows_or_persistence_checks_fail',checks=checks))
    if all(w['status']=='equilibrium_observed' for w in result['windows']):
        # Cross-window offsets matter even if each individual window is constant.
        means=[np.mean(z[(t>=a)&(t<b)]/sc,axis=0) for a,b in plan.windows]
        result['status']='equilibrium_observed' if np.max(np.ptp(means,axis=0))<=plan.equilibrium_span else 'unresolved'
    elif any(g['status']=='recurrence_observed' for g in result['groups']):
        result['status']='recurrence_observed'
    elif all(w['status']=='insufficient_observation' for w in result['windows']):
        result['status']='insufficient_observation'
    else:
        result['status']='unresolved'
    return result
