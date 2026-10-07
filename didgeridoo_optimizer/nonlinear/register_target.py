"""Prescribed controls and target-independent observations, never a player model.

The recurrence is exclusively SimultaneousCoupling.step via native advance.
Period estimates describe the sampled signal under the declared protocol;
mathematical primitive periods and orbital stability are not certified.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import time

import numpy as np

from .lips import DimensionedLipParameters
from .onset_stability import validate_parameters
from .passive_resonator import PassiveResonator, digest, json_copy, real, integer
from .simultaneous_coupling import SimultaneousCoupling
from .phase_reference import PhasePlan, analyze as phase_analyze
from ..optimization.design_contract import obj, identifier, quantity, target_quantity

SCHEMA = 'dcalc.register_target.plan.v1'
MAX_STEPS = 72000
MAX_STATES = 386
SUM_FIELDS = ('source_work_j', 'jet_loss_j', 'lip_loss_j', 'contact_loss_j',
              'resonator_loss_j', 'pressure_work_j', 'resonator_work_j',
              'lip_energy_change_j', 'resonator_energy_change_j', 'total_energy_change_j')
COLUMNS = ('pressure_pa', 'flow_m3_s', 'jet_m3_s', 'upstream_flow_m3_s',
           'downstream_flow_m3_s', 'lip_energy_j', 'resonator_energy_j',
           'total_energy_j', *SUM_FIELDS)
DIMENSIONS = {'played_frequency': ('frequency', 'Hz'), 'spectral_peak': ('frequency', 'Hz'),
              'passage_frequency': ('frequency', 'Hz'), 'frequency_ratio': ('scalar', '1'),
              'pressure_max': ('pressure', 'Pa'), 'activity_rms': ('pressure', 'Pa'),
              'duration': ('time', 's'), 'state_recurrence': ('scalar', '1')}
UNSUPPORTED = {'primitive_period', 'orbital_stability', 'physiological_accessibility'}
OBS_KEYS = {'band_hz', 'frame_steps', 'hop_steps', 'min_rms_pa', 'min_periods',
            'max_error', 'competitor_margin', 'max_drift_cents', 'max_dispersion_cents',
            'multiple_tolerance', 'period_error_floor', 'section_level_pa', 'section_direction', 'phase', 'pressure_scale_pa'}


def metric_quantity(q, dimension=None):
    if type(q) is dict and q.get('unit')=='s':
        exact(q,{'value','unit'},'duration quantity')
        if dimension not in (None,'time'):raise ValueError('Duration dimension')
        return real(q['value'],'seconds'),'time'
    return quantity(q,dimension)


def metric_target(q,dimension):
    return metric_quantity(q,dimension)[0] if dimension=='time' else target_quantity(q,dimension)


def exact(v, keys, where):
    return obj(v, keys, where, keys)


def bounded_json(value):
    """Reject cycles, non-JSON types and oversized trees before copying."""
    count = [0]
    def visit(v, depth=0, ancestors=frozenset()):
        count[0] += 1
        if depth > 24 or count[0] > 80000: raise ValueError('JSON structure budget')
        if type(v) in (dict, list):
            if id(v) in ancestors or len(v) > 4096: raise ValueError('JSON cycle/vector budget')
            if type(v) is dict and any(type(k) is not str for k in v): raise ValueError('JSON keys')
            for x in (v.values() if type(v) is dict else v): visit(x, depth+1, ancestors | {id(v)})
        elif v is not None and type(v) not in (str, bool, int, float): raise ValueError('JSON types')
        elif type(v) is str and len(v) > 4096: raise ValueError('JSON string budget')
        elif type(v) in (int, float): real(v, 'JSON number')
    visit(value)
    return value


def unique(rows, where, maximum, minimum=0):
    if type(rows) is not list or not minimum <= len(rows) <= maximum: raise ValueError(where+': count budget')
    ids = [identifier(x.get('id') if type(x) is dict else None, where) for x in rows]
    if len(set(ids)) != len(ids): raise ValueError(where+': duplicate id')
    return set(ids)


def active_parameters(request, index):
    s = request['plateaus'][index]
    return replace(DimensionedLipParameters(**request['reference_lips']),
                   mouth_pressure_kpa=s['pressure_pa']/1000,
                   resonance_hz=s['resonance_hz'], damping_ratio=s['damping_ratio'])


def validate_request(value, *, states=None):
    """Pure JSON/domain/quota validation, before numeric arrays or contexts."""
    bounded_json(value)
    exact(value, {'schema', 'case', 'reference_lips', 'rho_kg_m3', 'source', 'port_model',
                  'initial', 'plateaus', 'windows', 'observation', 'criteria', 'budgets'}, 'plan')
    if value['schema'] != SCHEMA: raise ValueError('Unsupported schema')
    b = exact(value['budgets'], {'chunk_steps', 'new_steps', 'new_seconds', 'child_seconds',
              'orchestrator_seconds', 'memory_mib', 'blas_threads', 'output_mib', 'observation_ops'}, 'budgets')
    for k, lo, hi in [('chunk_steps', 32, 1200), ('new_steps', 1, MAX_STEPS), ('memory_mib',768,768),
                       ('blas_threads',1,1), ('output_mib',2,160), ('observation_ops',1,100000000)]:
        integer(b[k], k, lo, hi)
    for k, hi in [('new_seconds',6), ('child_seconds',180), ('orchestrator_seconds',600)]:
        if not 0 < real(b[k], k) <= hi: raise ValueError(k+': budget')
    case = value['case']
    if type(case) is not dict: raise ValueError('case object')
    if case.get('kind') == 'synthetic':
        exact(case, {'kind','id','sample_rate_hz','description'}, 'synthetic case')
        if not isinstance(case['description'],str) or not case['description'].strip(): raise ValueError('Synthetic description')
        fs = case['sample_rate_hz']
    elif case.get('kind') == 'saved':
        exact(case, {'kind','id','config','design','model_in','recipe'}, 'saved case')
        from ..pipeline.paired_onset import RECIPE
        exact(case['recipe'], RECIPE, 'recipe')
        fs = case['recipe']['sample_rate_hz']
        for k in ('config','design','model_in'):
            if type(case[k]) is not str or not case[k].strip(): raise ValueError('Explicit input paths')
        integer(case['recipe']['fit_points'],'fit_points',2,10000)
        for k in ('guard_points','audit_points'): integer(case['recipe'][k],k,2,10000)
    else: raise ValueError('Explicit saved or synthetic case required')
    identifier(case['id'], 'case'); integer(fs, 'fs',1000,12000)
    exact(value['reference_lips'], set(DimensionedLipParameters().as_dict()), '12 V2 parameters')
    p = DimensionedLipParameters(**value['reference_lips'])
    validate_parameters(p, value['rho_kg_m3'])
    if p.pressure_force_sign != -1 or value['source'] != 'ideal' or value['port_model'] != 'conjugate':
        raise ValueError('Only inward -1 / ideal / conjugate supported')
    # Derived stiffness/damping must be representable before constructing a solver.
    for x in (p.mass_kg*(2*math.pi*p.resonance_hz)**2, 2*p.damping_ratio*p.mass_kg*2*math.pi*p.resonance_hz):
        real(x,'derived mechanics')
    exact(value['initial'], {'kind'}, 'initial')
    if value['initial']['kind'] != 'native':
        raise ValueError('External/R36 import unsupported; use a verified product checkpoint for identical resume')
    ids = unique(value['plateaus'], 'plateaus',8,1)
    total = 0
    for i,s in enumerate(value['plateaus']):
        exact(s, {'id','pressure_pa','resonance_hz','damping_ratio','steps','duration_s'}, 'plateau')
        integer(s['steps'],'steps',1,MAX_STEPS); real(s['pressure_pa'],'pressure',minimum=0)
        total += s['steps']
        if not math.isclose(real(s['duration_s'],'duration',minimum=0),s['steps']/fs,rel_tol=0,abs_tol=1e-12):
            raise ValueError('Plateau duration/steps mismatch')
        a = active_parameters(value,i); validate_parameters(a,value['rho_kg_m3'])
        real(a.mass_kg*(2*math.pi*a.resonance_hz)**2,'derived stiffness')
        real(2*a.damping_ratio*a.mass_kg*2*math.pi*a.resonance_hz,'derived damping')
    if total > b['new_steps'] or total/fs > b['new_seconds'] or total > MAX_STEPS or total/fs > 6:
        raise ValueError('Cumulative new trajectory quota')
    window_ids = unique(value['windows'],'windows',16)
    for w in value['windows']:
        exact(w, {'id','plateau','start_step','stop_step'}, 'window')
        if w['plateau'] not in ids: raise ValueError('Unknown window plateau')
        s = next(s for s in value['plateaus'] if s['id']==w['plateau'])
        integer(w['start_step'],'window start',0,s['steps']-1)
        integer(w['stop_step'],'window stop',w['start_step']+1,s['steps'])
    o = exact(value['observation'], OBS_KEYS,'observation')
    if type(o['band_hz']) is not list or len(o['band_hz'])!=2: raise ValueError('Observation band')
    lo,hi = (real(x,'band',minimum=1e-9) for x in o['band_hz'])
    if not lo < hi < fs/2 or fs/hi < 4: raise ValueError('Band/Nyquist: at least four samples per period')
    integer(o['frame_steps'],'frame_steps',16,MAX_STEPS)
    integer(o['hop_steps'],'hop_steps',1,o['frame_steps'])
    integer(o['min_periods'],'min_periods',2,100)
    for k in ('min_rms_pa','max_error','competitor_margin','max_drift_cents','max_dispersion_cents','multiple_tolerance','period_error_floor','pressure_scale_pa'):
        if real(o[k],k,minimum=0) <= 0: raise ValueError(k+': positive protocol value required')
    if o['max_error']>=1 or o['competitor_margin']>=o['max_error'] or o['period_error_floor']>o['competitor_margin'] or o['multiple_tolerance']>=.1:
        raise ValueError('Observation error domain')
    real(o['section_level_pa'],'section level')
    if o['section_direction'] not in ('rising','falling'): raise ValueError('Section direction')
    # Each lag uses the SAME W; two temporal halves fit inside it.
    lag = math.ceil(fs/lo)
    if o['frame_steps']//2-lag-2 < o['min_periods']*lag:
        raise ValueError('Insufficient fixed support for band and periods')
    ops = 0
    for w in value['windows']:
        n=w['stop_step']-w['start_step']
        frames=max(0,1+(n-o['frame_steps'])//o['hop_steps'])
        if frames>128: raise ValueError('Observation frame quota')
        ops += frames*lag*o['frame_steps']*6
    if ops > b['observation_ops']: raise ValueError('Observation operation quota before arrays')
    if o['phase'] is not None:
        pp=PhasePlan.from_dict(o['phase'])
        if not pp.state_units or (states is not None and len(pp.scales)!=states): raise ValueError('Complete phase scales/units')
        if pp.validations[-1][1]>total/fs: raise ValueError('PHASE exceeds experiment duration')
    unique(value['criteria'],'criteria',128)
    for c in value['criteria']:
        obj(c, {'id','role','observable','windows','target','tolerance','minimum','maximum'}, 'criterion',
            {'id','role','observable','windows'})
        if c['role'] not in ('hard','observe'): raise ValueError('Criterion role')
        ws=c['windows']
        if type(ws) is not list or not 1<=len(ws)<=16 or len(set(ws))!=len(ws) or any(x not in window_ids for x in ws):
            raise ValueError('Explicit distinct criterion windows')
        name=c['observable']
        if name not in DIMENSIONS and name not in UNSUPPORTED: raise ValueError('Unknown observable')
        if name=='frequency_ratio':
            if len(ws)!=2: raise ValueError('Ratio requires two distinct windows')
            wa,wb=[next(w for w in value['windows'] if w['id']==x) for x in ws]
            if wa['plateau']==wb['plateau'] and max(wa['start_step'],wb['start_step'])<min(wa['stop_step'],wb['stop_step']):
                raise ValueError('Independent ratio windows may not overlap')
        dim=DIMENSIONS.get(name,('scalar','1'))[0]
        if name not in UNSUPPORTED:
            if not any(k in c for k in ('target','minimum','maximum')): raise ValueError('Criterion needs explicit bound/target')
            if ('target' in c)!=('tolerance' in c): raise ValueError('Target and tolerance required together')
            if 'target' in c:
                target=metric_target(c['target'],dim); real(target,'target')
                tol,td=metric_quantity(c['tolerance'])
                if tol<0 or (td!=dim and not (dim=='frequency' and td=='cent' and target>0)):
                    raise ValueError('Tolerance dimension')
            bounds=[]
            for k in ('minimum','maximum'):
                if k in c: bounds.append(metric_quantity(c[k],dim)[0])
            if len(bounds)==2 and bounds[0]>bounds[1]: raise ValueError('Criterion reversed bounds')
    if states is not None:
        integer(states,'states',2,MAX_STATES)
        estimated=(total+1)*(states+len(COLUMNS)+4)*8 + 8*1024**2
        if estimated>b['output_mib']*1024**2: raise ValueError('Series/output quota before arrays')
    return value


@dataclass(frozen=True)
class Request:
    document: str
    @classmethod
    def validate(cls, value, **kw):
        validate_request(value,**kw)
        return cls(json.dumps(value,sort_keys=True,allow_nan=False))
    def as_dict(self):
        value=json.loads(self.document);validate_request(value);return value


def sample_rate(request):
    c=request['case'];return c['sample_rate_hz'] if c['kind']=='synthetic' else c['recipe']['sample_rate_hz']


class Experiment:
    """Memory API. Explicit source qualification is mandatory, no invented fit.

    Checkpoint integrity is not authorship. File provenance/chain verification is
    the reporting layer's job. This API deliberately accepts small synthetic models.
    """
    def __init__(self, model, request, *, source):
        r=request.as_dict() if isinstance(request,Request) else request
        if not isinstance(model,PassiveResonator): raise ValueError('PassiveResonator required')
        self.request=Request.validate(r,states=2+2*len(model.a))
        if model.sample_rate_hz!=sample_rate(r): raise ValueError('Native fs mismatch')
        exact(source,{'kind','description','model_sha256'},'memory source')
        if source['kind'] not in ('synthetic','verified_saved') or not source['description']:
            raise ValueError('Explicit source qualification required')
        if source['model_sha256']!=digest(model.parameters()): raise ValueError('Source model identity mismatch')
        self.source=json_copy(source);self.model=model
        self.coupling=SimultaneousCoupling(model,params=active_parameters(r,0),rho=r['rho_kg_m3'],v2_port_model='conjugate')
        self.initial=self.coupling.export_checkpoint();self.segment_reset=self.coupling.state.tolist()
        self.accepted=0;self.segment=0;self.events=[];self.sums={k:0. for k in SUM_FIELDS}
        self.origin_time_s=self.coupling.time_s
        self.history_source_steps=0
        self._request_hash=digest(r)

    def _transition(self):
        r=self.request.as_dict()
        ends=np.cumsum([s['steps'] for s in r['plateaus']]).tolist()
        target=min(next((i for i,e in enumerate(ends) if self.accepted<e),len(ends)-1),len(ends)-1)
        if target==self.segment:return
        old=self.coupling;params=active_parameters(r,target)
        state=old.state
        if params!=old.params:
            new=SimultaneousCoupling(self.model,params=params,rho=r['rho_kg_m3'],
                                     initial_state=state,v2_port_model='conjugate')
            new.initialize(state,time_s=old.time_s,last_dissipation_w=old.last_dissipation_w)
            delta=.5*(new.K-old.K)*float(state[0])**2
            before=old.lip_energy(state);after=new.lip_energy(state)
            if not math.isclose(after-before,delta,rel_tol=2e-12,abs_tol=1e-18): raise ValueError('Command energy mismatch')
            event=dict(step=self.accepted,old_segment=self.segment,new_segment=target,
                before=old.export_checkpoint(),after=new.export_checkpoint(),
                lip_energy_before_j=before,lip_energy_after_j=after,command_work_j=delta,
                pressure_impulse_work_j=0.,damping_stored_energy_change_j=0.)
            self.events.append(event);self.coupling=new
        self.segment=target;self.segment_reset=state.tolist()

    def checkpoint(self):
        p=dict(schema='dcalc.register_target.checkpoint.v1',request=self.request.as_dict(),
            request_sha256=self._request_hash,source=self.source,native=self.coupling.export_checkpoint(),
            experiment_initial=self.initial,segment_reset=self.segment_reset,accepted_steps=self.accepted,
            segment=self.segment,events=self.events,sums=self.sums,origin_time_s=self.origin_time_s,
            historical_source_steps=self.history_source_steps)
        p=json_copy(p);return dict(payload=p,sha256=digest(p))

    def restore(self, checkpoint):
        """Validate a candidate completely before changing this experiment."""
        bounded_json(checkpoint)
        exact(checkpoint,{'payload','sha256'},'checkpoint')
        p=checkpoint['payload']
        if checkpoint['sha256']!=digest(p): raise ValueError('Checkpoint checksum')
        expected=set(self.checkpoint()['payload'])
        exact(p,expected,'checkpoint payload')
        r=self.request.as_dict();total=sum(x['steps'] for x in r['plateaus'])
        if p['schema']!='dcalc.register_target.checkpoint.v1' or p['request']!=r or p['request_sha256']!=self._request_hash or p['source']!=self.source:
            raise ValueError('Checkpoint experiment identity')
        n=integer(p['accepted_steps'],'accepted',0,total);index=integer(p['segment'],'segment',0,len(r['plateaus'])-1)
        start=sum(x['steps'] for x in r['plateaus'][:index]);end=start+r['plateaus'][index]['steps']
        if not start<=n<=end:raise ValueError('Checkpoint cursor')
        if p['experiment_initial']!=self.initial or p['origin_time_s']!=self.origin_time_s or p['historical_source_steps']!=0:
            raise ValueError('Experiment reset/origin mismatch; external import unsupported')
        if type(p['events']) is not list or len(p['events'])>7:raise ValueError('Event budget')
        trial=SimultaneousCoupling(self.model,params=active_parameters(r,index),rho=r['rho_kg_m3'],v2_port_model='conjugate')
        trial.import_checkpoint(p['native'])
        if not math.isclose(trial.time_s,self.origin_time_s+n/sample_rate(r),rel_tol=0,abs_tol=1e-10):raise ValueError('Checkpoint clock')
        expected_events=[i for i in range(1,index+1) if active_parameters(r,i)!=active_parameters(r,i-1)]
        if [e.get('new_segment') for e in p['events']]!=expected_events:raise ValueError('Missing/duplicate command event')
        for e in p['events']:
            exact(e,{'step','old_segment','new_segment','before','after','lip_energy_before_j','lip_energy_after_j','command_work_j','pressure_impulse_work_j','damping_stored_energy_change_j'},'event')
            if e['pressure_impulse_work_j']!=0 or e['damping_stored_energy_change_j']!=0:raise ValueError('Event nonstored work')
            i=e['new_segment'];boundary=sum(x['steps'] for x in r['plateaus'][:i])
            if e['step']!=boundary or e['old_segment']!=i-1:raise ValueError('Event boundary')
            a=SimultaneousCoupling(self.model,params=active_parameters(r,i-1),rho=r['rho_kg_m3'],v2_port_model='conjugate')
            b=SimultaneousCoupling(self.model,params=active_parameters(r,i),rho=r['rho_kg_m3'],v2_port_model='conjugate')
            a.import_checkpoint(e['before']);b.import_checkpoint(e['after'])
            if not math.isclose(a.time_s,self.origin_time_s+boundary/sample_rate(r),rel_tol=0,abs_tol=1e-10):raise ValueError('Event clock')
            if e['after']['payload']['initial_state']!=a.state.tolist() or b.diagnostics is not None:raise ValueError('Event segment reset/diagnostics')
            if not np.array_equal(a.state,b.state) or a.time_s!=b.time_s or a.last_dissipation_w!=b.last_dissipation_w:
                raise ValueError('Event state discontinuity')
            if e['command_work_j']!=.5*(b.K-a.K)*float(a.state[0])**2 or e['lip_energy_before_j']!=a.lip_energy(a.state) or e['lip_energy_after_j']!=b.lip_energy(b.state):
                raise ValueError('Event energy')
        exact(p['sums'],set(SUM_FIELDS),'energy sums')
        for k,v in p['sums'].items():
            real(v,k)
            if k.endswith('loss_j') and v<0:raise ValueError('Negative accumulated loss')
        totals=p['sums']
        energy_from_ports=totals['source_work_j']-sum(totals[k] for k in ('jet_loss_j','lip_loss_j','contact_loss_j','resonator_loss_j'))
        for a,b in ((energy_from_ports,totals['total_energy_change_j']),
                    (totals['lip_energy_change_j']+totals['resonator_energy_change_j'],totals['total_energy_change_j'])):
            if not math.isclose(a,b,rel_tol=1e-8,abs_tol=1e-14):raise ValueError('Inconsistent cumulative energy ledger')
        initial_solver=SimultaneousCoupling(self.model,params=active_parameters(r,0),rho=r['rho_kg_m3'],v2_port_model='conjugate')
        initial_energy=initial_solver.lip_energy(self.initial['payload']['snapshot']['state'])
        # Initial resonator is natively at rest. Subsequent energy is a native
        # diagnostic, or the last explicit event plus its previous diagnostic.
        if trial.diagnostics is not None:final_energy=trial.diagnostics['total_energy_j']
        elif p['events']:
            event=p['events'][-1];previous=event['before']['payload']['snapshot']['diagnostics']
            final_energy=previous['total_energy_j']+event['command_work_j']
        else:final_energy=initial_energy
        predicted=initial_energy+totals['total_energy_change_j']+sum(e['command_work_j'] for e in p['events'])
        if not math.isclose(final_energy,predicted,rel_tol=1e-8,abs_tol=1e-14):raise ValueError('Cumulative stored energy mismatch')
        reset=p['segment_reset']
        if type(reset) is not list or len(reset)!=len(trial.state):raise ValueError('Segment reset')
        for v in reset:real(v,'segment reset')
        # Native initial must be the last changed-control segment reset.
        native_reset=p['events'][-1]['after']['payload']['initial_state'] if p['events'] else self.initial['payload']['initial_state']
        if p['native']['payload']['initial_state']!=native_reset:raise ValueError('Native reset mismatch')
        if p['events'] and p['events'][-1]['new_segment']==index and reset!=p['events'][-1]['after']['payload']['initial_state']:raise ValueError('Segment reset mismatch')
        self.coupling=trial;self.accepted=n;self.segment=index
        self.events=json_copy(p['events']);self.sums=json_copy(p['sums']);self.segment_reset=list(reset)

    def advance(self, count, *, seconds=170., stop=lambda:False):
        """One bounded memory chunk; a stop never loses an accepted sample."""
        from ..pipeline.regime_reference import advance
        r=self.request.as_dict();integer(count,'chunk count',0,r['budgets']['chunk_steps'])
        remaining=sum(s['steps'] for s in r['plateaus'])-self.accepted
        if count>remaining:raise ValueError('Cumulative quota; no repeated budget on resume')
        seconds=real(seconds,'seconds',minimum=0)
        if seconds>175:raise ValueError('Chunk wall quota')
        t0=time.monotonic();start=self.accepted
        states=np.empty((count+1,len(self.coupling.state)));times=np.empty(count+1)
        values=np.empty((count,len(COLUMNS)));mid=np.empty(count);segments=np.empty(count,dtype=np.int64)
        states[0]=self.coupling.state;times[0]=self.coupling.time_s
        reason=None
        def collect(row):
            j=self.accepted-start
            # No user callbacks or fallible file operations between native commit and recording.
            states[j+1]=self.coupling.state;times[j+1]=row['time_s'];mid[j]=row['midpoint_time_s']
            values[j]=[row[k] for k in COLUMNS];segments[j]=self.segment
            for k in SUM_FIELDS:self.sums[k]+=row[k]
            self.accepted+=1
        while self.accepted-start<count:
            if stop():reason='stop_requested';break
            left=seconds-(time.monotonic()-t0)
            if left<=0:reason='chunk_time_budget';break
            self._transition()
            boundary=sum(s['steps'] for s in r['plateaus'][:self.segment+1])
            n=min(count-(self.accepted-start),boundary-self.accepted)
            result=advance(self.coupling,n,seconds=left,stop=stop,after_accept=collect)
            if not result['ok']:reason=result['reason'];break
        n=self.accepted-start
        return dict(ok=reason is None,reason=reason,start_step=start,stop_step=self.accepted,
                    arrays=dict(states=states[:n+1],times=times[:n+1],values=values[:n],midpoint_times=mid[:n],segments=segments[:n]))


def _period_frame(y,fs,o):
    lo,hi=o['band_hz'];lmin=max(2,math.floor(fs/hi));lmax=math.ceil(fs/lo)
    W=len(y)-lmax-2
    energy=float(np.mean((y[:W]-np.mean(y[:W]))**2))
    if energy<o['min_rms_pa']**2:return dict(status='unresolved',reason='low_ac',frequency_hz=None,candidates=[])
    # Fixed-support quadratic difference. This is an original bounded adaptation,
    # not a claim to implement all of YIN. No target enters lag or frame selection.
    d=np.empty(lmax+1);d[0]=0.
    for lag in range(1,lmax+1):
        v=y[:W]-y[lag:lag+W];d[lag]=float(np.dot(v,v)/(2*W*energy))
    candidates=[]
    for lag in range(max(2,lmin),lmax):
        if not (d[lag]<=d[lag-1] and d[lag]<d[lag+1]):continue
        curvature=d[lag-1]-2*d[lag]+d[lag+1]
        delta=float(np.clip(.5*(d[lag-1]-d[lag+1])/curvature,-.5,.5)) if curvature>0 else 0.
        tau=lag+delta;f=fs/tau
        if not lo<=f<=hi:continue
        def score(offset):
            q=lag+offset;k=int(math.floor(q));u=q-k
            # Four-point Lagrange interpolation on the same fixed support.
            a,b,c,e=(y[k+j:k+j+W] for j in (-1,0,1,2))
            shifted=(-u*(u-1)*(u-2)/6*a+(u+1)*(u-1)*(u-2)/2*b
                     -(u+1)*u*(u-2)/2*c+(u+1)*u*(u-1)/6*e)
            v=y[:W]-shifted
            return float(np.dot(v,v)/(2*W*energy))
        # Bounded refinement, all candidates, never target-driven.
        for h in (.1,.01,.001):
            center=float(np.clip(delta,-.49,.49));a=score(center-h);b=score(center);c=score(center+h)
            denom=a-2*b+c
            if denom>0:delta=float(np.clip(center+.5*h*(a-c)/denom,-.5,.5))
        tau=lag+delta;f=fs/tau;error=score(delta)
        if not lo<=f<=hi:continue
        candidates.append(dict(lag_samples=tau,frequency_hz=f,error=error,
                               sample_resolution_hz=fs/max(1.,tau-.5)-fs/(tau+.5)))
    if not candidates:return dict(status='unresolved',reason='no_interior_minimum',frequency_hz=None,candidates=[])
    best=min(c['error'] for c in candidates)
    eligible=[c for c in candidates if c['error']<=min(o['max_error'],best+o['competitor_margin'])]
    if not eligible:return dict(status='unresolved',reason='nonperiodic_error',frequency_hz=None,candidates=candidates)
    resolved=[c for c in eligible if c['error']<=best+o['period_error_floor']]
    shortest=min(resolved,key=lambda c:c['lag_samples'])
    for c in candidates:
        ratio=c['lag_samples']/shortest['lag_samples']
        c['multiple_of_selected']=int(round(ratio)) if abs(ratio-round(ratio))<=o['multiple_tolerance'] else None
        c['competitive']=c in eligible
    incompatible=[c for c in eligible if c['multiple_of_selected'] is None or c['lag_samples']<shortest['lag_samples']*(1-o['multiple_tolerance'])]
    return dict(status='unresolved' if incompatible else 'observed',reason='competing_periods' if incompatible else None,
                frequency_hz=None if incompatible else shortest['frequency_hz'],candidates=candidates,
                fixed_support_samples=W,normalization_ac_pa2=energy,
                selected_lag_samples=shortest['lag_samples'],resolution_hz=shortest['sample_resolution_hz'])


def observe_pressure(pressure, fs, observation):
    """Target-free signal API; all declared sliding windows and two halves retained."""
    if type(pressure) is not np.ndarray or pressure.ndim!=1 or pressure.dtype.kind not in 'fiu' or len(pressure)>MAX_STEPS:
        raise ValueError('Bounded real ndarray pressure required')
    if not np.all(np.isfinite(pressure)):raise ValueError('Nonfinite pressure')
    o=observation;n=len(pressure)
    if not 1000<=real(fs,'fs')<=12000:raise ValueError('Sample rate domain')
    if n*math.ceil(fs/o['band_hz'][0])*6>100000000:raise ValueError('Observation operation quota')
    pressure=pressure.astype(np.float64,copy=False)
    if n and np.max(np.abs(pressure))>1e100:raise ValueError('Unrepresentable observation amplitude')
    ac=float(np.std(pressure)) if n else None
    out=dict(status='unresolved',reason=None,frequency_hz=None,activity_rms_pa=ac,
             spectral_peak_hz=None,passage_frequency_hz=None,frames=[],
             scope='Observed sampled-signal periodicity within declared band; no certified primitive or physiological register',
             band_hz=list(o['band_hz']))
    if n<o['frame_steps']:out['reason']='insufficient_window';return out
    if ac<o['min_rms_pa']:out['reason']='low_ac';return out
    freq=np.fft.rfftfreq(n,1/fs);spectrum=abs(np.fft.rfft((pressure-np.mean(pressure))*np.hanning(n)))
    band=(freq>=o['band_hz'][0])&(freq<=o['band_hz'][1])
    if np.any(band):out['spectral_peak_hz']=float(freq[band][np.argmax(spectrum[band])])
    s=pressure-o['section_level_pa'];sign=1 if o['section_direction']=='rising' else -1;s=s*sign
    ix=np.flatnonzero((s[:-1]<0)&(s[1:]>=0))
    if len(ix)>=o['min_periods']+1:
        crossings=ix-s[ix]/(s[ix+1]-s[ix]);periods=np.diff(crossings)/fs
        out['passage_frequency_hz']=float(1/np.mean(periods))
        out['passage_dispersion_s']=float(np.std(periods))
    estimates=[]
    for start in range(0,n-o['frame_steps']+1,o['hop_steps']):
        y=pressure[start:start+o['frame_steps']]
        central=_period_frame(y,fs,o)
        # Both halves keep the same lag band and fixed support, no best-half selection.
        halves=[_period_frame(x,fs,o) for x in np.array_split(y,2)]
        row=dict(start_sample=start,stop_sample=start+len(y),central=central,sensitivities=halves)
        out['frames'].append(row)
        estimates.extend([central,*halves])
    if any(e['frequency_hz'] is None for e in estimates):out['reason']='unresolved_frame_or_sensitivity';return out
    f=np.array([e['frequency_hz'] for e in estimates]);median=float(np.median(f))
    cents=1200*np.log2(f/median)
    out['drift_cents']=float(np.ptp(cents));out['dispersion_cents']=float(np.std(cents))
    if out['drift_cents']>o['max_drift_cents'] or out['dispersion_cents']>o['max_dispersion_cents']:
        out['reason']='temporal_drift_or_dispersion';return out
    out.update(status='observed',frequency_hz=median,reason=None)
    return out


def _array(a,shape,name):
    if type(a) is not np.ndarray or a.shape!=shape or a.dtype.kind not in 'fiu' or a.dtype.itemsize>8:
        raise ValueError(name+': explicit bounded real ndarray')
    if not np.all(np.isfinite(a)):raise ValueError(name+': finite values')


def analyze(request, *, times, states, midpoint_times, pressure, source):
    """Analyze explicitly sourced memory arrays without running a trajectory.

    Missing data stays null. Mutating targets cannot alter observations or PHASE.
    Times are experiment-relative; pressure has native midpoint times.
    """
    r=request.as_dict() if isinstance(request,Request) else request
    validate_request(r,states=states.shape[1] if type(states) is np.ndarray and states.ndim==2 else None)
    if type(source) is not dict or not source.get('description') or source.get('kind') not in ('synthetic','verified_bundle','supplied_arrays'):
        raise ValueError('Explicit array source qualification required')
    bounded_json(source)
    fs=sample_rate(r);total=sum(s['steps'] for s in r['plateaus'])
    if type(times) is not np.ndarray or not 1<=len(times)<=total+1:raise ValueError('Array point quota')
    n=len(times)-1
    _array(times,(n+1,),'times');_array(states,(n+1,states.shape[1]),'states')
    _array(midpoint_times,(n,),'midpoints');_array(pressure,(n,),'pressure')
    if not np.allclose(times,np.arange(n+1)/fs,rtol=0,atol=1e-9) or not np.allclose(midpoint_times,(np.arange(n)+.5)/fs,rtol=0,atol=1e-9):
        raise ValueError('Native complete time support required')
    windows={};offset=0;starts={}
    for s in r['plateaus']:starts[s['id']]=offset;offset+=s['steps']
    for w in r['windows']:
        a=starts[w['plateau']]+w['start_step'];b=starts[w['plateau']]+w['stop_step']
        if b>n:
            windows[w['id']]=dict(status='unavailable',reason='window_not_acquired',frequency_hz=None,activity_rms_pa=None,
                                  spectral_peak_hz=None,passage_frequency_hz=None,duration_s=None,pressure_max_pa=None)
        else:
            row=observe_pressure(pressure[a:b],fs,r['observation'])
            plateau=next(s for s in r['plateaus'] if s['id']==w['plateau'])
            row.update(duration_s=(b-a)/fs,pressure_max_pa=plateau['pressure_pa'])
            windows[w['id']]=row
    phase=None
    if r['observation']['phase'] is not None:
        phase=phase_analyze(times,states,plan=PhasePlan.from_dict(r['observation']['phase']),
            signals=dict(pressure=dict(times=midpoint_times,values=pressure,unit='Pa',scale=r['observation']['pressure_scale_pa'])))
    result=dict(schema='dcalc.register_target.analysis.v1',source=json_copy(source),windows=windows,phase=phase)
    result.update(evaluate_criteria(r,windows,phase))
    return result


def evaluate_criteria(r, windows, phase=None):
    rows=[]
    mapping={'played_frequency':'frequency_hz','spectral_peak':'spectral_peak_hz',
             'passage_frequency':'passage_frequency_hz','pressure_max':'pressure_max_pa',
             'activity_rms':'activity_rms_pa','duration':'duration_s'}
    for c in r['criteria']:
        name=c['observable'];dim,unit=DIMENSIONS.get(name,('scalar','1'))
        selected=[windows.get(x,{}) for x in c['windows']]
        values=[x.get(mapping.get(name,'')) for x in selected]
        reason=None
        if name in UNSUPPORTED:reason='unsupported_obligation'
        elif name=='frequency_ratio':
            a,b=[x.get('frequency_hz') for x in selected];values=[b/a if a is not None and b is not None and a>0 else None]
        elif name=='state_recurrence':
            values=[];offsets={};cursor=0
            for s in r['plateaus']:offsets[s['id']]=cursor;cursor+=s['steps']
            for wid in c['windows']:
                w=next(w for w in r['windows'] if w['id']==wid)
                interval=[(offsets[w['plateau']]+w[k])/sample_rate(r) for k in ('start_step','stop_step')]
                errors=[];unavailable=phase is None
                for g in (phase or {}).get('groups',[]):
                    for outcome in [g.get('central'),*[x.get('result') for x in g['sensitivities']]]:
                        matches=[x for x in (outcome or {}).get('windows',[]) if x.get('requested')==interval]
                        if len(matches)!=1 or matches[0].get('maximum_scaled') is None:unavailable=True
                        else:errors.append(matches[0]['maximum_scaled'])
                values.append(None if unavailable or not errors else max(errors))
        if reason is not None:status='unsupported';values=[None]
        elif any(v is None for v in values):status='unresolved';reason='requested_observable_unavailable'
        else:
            passes=[]
            for v in values:
                ok=True
                if 'minimum' in c:ok &= v>=metric_quantity(c['minimum'],dim)[0]
                if 'maximum' in c:ok &= v<=metric_quantity(c['maximum'],dim)[0]
                if 'target' in c:
                    t=metric_target(c['target'],dim);tol,td=metric_quantity(c['tolerance'])
                    error=abs(1200*math.log2(v/t)) if td=='cent' and v>0 else (math.inf if td=='cent' else abs(v-t))
                    ok &= error<=tol
                passes.append(bool(ok))
            status='pass' if all(passes) else 'fail'
        rows.append(dict(id=c['id'],role=c['role'],observable=name,windows=c['windows'],status=status,
                         values_si=values,unit_si='s' if name=='duration' else unit,reason=reason))
    hard=[x for x in rows if x['role']=='hard']
    verdict=False if any(x['status']=='fail' for x in hard) else (None if any(x['status']!='pass' for x in hard) else True)
    return dict(criteria=rows,hard_conforming=verdict if hard else None,
                conformity='descriptive' if not hard else ('conforming' if verdict is True else 'violated' if verdict is False else 'unresolved'))
