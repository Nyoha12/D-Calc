"""CONFIG/DESIGN/DB progression using only the native simultaneous step."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import zipfile

import numpy as np

from . import time_domain_reference as td
from .design_pitch import execution_ready, child_limits
from ..nonlinear.lips import DimensionedLipParameters
from ..nonlinear.onset_stability import validate_parameters
from ..nonlinear.passive_resonator import PassiveResonator, digest, integer, real
from ..nonlinear.simultaneous_coupling import SimultaneousCoupling
from ..nonlinear.regime_observables import ObservationPlan, analyze
from ..reporting import regime_reference as report

ROOT=Path(__file__).resolve().parents[2]
COLUMNS=('pressure_pa','flow_m3_s','jet_m3_s','upstream_flow_m3_s','downstream_flow_m3_s',
         'source_work_j','jet_loss_j','lip_loss_j','contact_loss_j','resonator_loss_j',
         'pressure_work_j','lip_energy_j','resonator_energy_j','midpoint_time_s')


@dataclass(frozen=True)
class RunPlan:
    document: str

    def as_dict(self):
        if type(self.document) is not str or len(self.document)>report.MAX_JSON_BYTES:
            raise ValueError('Bounded serialized run plan required')
        p=json.loads(self.document)
        required={'schema','config','design','output','model_in','model_sha256','recipe','compatibility',
            'plan_identity','producer_sources','software','fit_certificate_scope','fit_certificate_sha256',
            'fit_producer_sources','fit_air','bernoulli_air','target_steps','accepted_steps','origin_time_s',
            'seconds','checkpoint_steps','resume','parent','budgets'}
        if type(p) is not dict or set(p)!=required or p['schema']!='dcalc.regime_plan.v1':
            raise ValueError('Run plan schema mismatch')
        c=p['compatibility']
        if type(c) is not dict or p['plan_identity']!=digest(c):
            raise ValueError('Run plan identity mismatch')
        fs=integer(c['fs'],'fs',1000,12000)
        target=integer(p['target_steps'],'target',1,min(72000,6*fs))
        integer(p['accepted_steps'],'accepted',0,target-1)
        if real(p['origin_time_s'],'origin',minimum=0.)!=0.:
            raise ValueError('Chain origin must be zero')
        if not 0<=real(p['seconds'],'seconds')<=175:
            raise ValueError('Wall budget')
        cp=integer(p['checkpoint_steps'],'checkpoint steps',1,12000)
        if math.ceil(target/cp)>1000:raise ValueError('Checkpoint count budget')
        ObservationPlan.from_dict(c['observation'])
        params=DimensionedLipParameters(**c['parameters']);validate_parameters(params,c['rho_kg_m3'])
        for name,hashed in p['producer_sources'].items():
            path=Path(name)
            if (path.is_absolute() or '..' in path.parts or path.suffix!='.py'
                    or path.parts[0] not in ('didgeridoo_optimizer','tools')
                    or type(hashed) is not str or len(hashed)!=64):
                raise ValueError('Invalid producer source identity')
        return p


def preflight(config,design,output_dir,*,model_in,params,rho,observation,
              target_steps,sample_rate_hz=12000,v2_port_model='jet-only',
              max_extensions=32,max_iterations=80,seconds=170.,checkpoint_steps=1200,
              resume=None,**recipe):
    """Read-only: reuse native preflight/helpers; no acoustics, fit or step."""
    integer(target_steps,'target_steps',1,72000)
    fs=integer(sample_rate_hz,'sample_rate_hz',1000,12000)
    if target_steps>6*fs:
        raise ValueError('Chain maximum is six seconds from origin')
    integer(checkpoint_steps,'checkpoint_steps',1,12000)
    # Keep file and checkpoint counts bounded even for a full chain.
    if math.ceil(target_steps/checkpoint_steps)>1000:
        raise ValueError('Checkpoint file count budget exceeded')
    seconds=real(seconds,'seconds',minimum=0.)
    if seconds>175:
        raise ValueError('Child wall budget <=175 seconds, with shutdown reserve')
    integer(max_extensions,'max_extensions',0,64);integer(max_iterations,'max_iterations',1,100)
    if not isinstance(params,DimensionedLipParameters):
        raise ValueError('Explicit dimensioned lip parameters required')
    validate_parameters(params,rho)
    if not isinstance(observation,ObservationPlan):
        raise ValueError('Explicit immutable observation plan required')
    # Refuse links BEFORE the frozen native helpers resolve paths.
    for p in (config,design,model_in):report.safe_path(p)
    cfg=td.strict_input(config)
    for key,default in (('database_file','materials_base_v1.yaml'),('variant_rules_file','wood_variant_rules_v1.yaml')):
        p=Path(cfg.get('materials',{}).get(key,default))
        p=p if p.is_absolute() else Path(config).absolute().parent/p
        report.safe_path(p,exists=key=='database_file')
    output=report.safe_path(output_dir,exists=False)
    allowed={'h_cm','loss_model','radiation_model','air_reference','R0','dc_origin','basis_completion','fit_min_hz','fit_max_hz','fit_points','guard_max_hz','guard_points','audit_points'}
    if set(recipe)-allowed:
        raise ValueError('Unknown acoustic recipe options')
    base,context=td.preflight(config,design,output,model_in=model_in,sample_rate_hz=fs,
                              v2_schedule='simultaneous',v2_port_model=v2_port_model,**recipe)
    model=PassiveResonator.load(model_in,expected_sha256=base['model_sha256'])
    mp=model.parameters()
    if len(model.a)>192 or len(observation.scales)!=2+2*len(model.a):
        raise ValueError('At most 192 native terms and complete scales required')
    quality=mp['quality'];inventory=quality.get('candidate_inventory',{})
    if type(inventory) is not dict or set(inventory)!={'a','gamma','omega'}:
        raise ValueError('Historical fitted candidate inventory required; synthetic CLI recipe refused')
    if any(type(inventory[k]) is not list or not 1<=len(inventory[k])<=256 for k in inventory):
        raise ValueError('Historical inventory outside budget')
    inv=PassiveResonator(**inventory,R0=model.R0,sample_rate_hz=fs,dc_origin=mp['dc_origin'],domain=mp['domain'])
    active=inv.a>0
    if any(not np.array_equal(getattr(inv,k)[active],getattr(model,k)) for k in inventory):
        raise ValueError('Saved coefficients differ from historical active inventory')
    frequencies=quality.get('fit_frequency_hz')
    if (type(frequencies) is not list or len(frequencies)!=base['options']['fit_points']
            or frequencies[0]!=base['options']['fit_min_hz'] or frequencies[-1]!=base['options']['fit_max_hz']):
        raise ValueError('Historical fit grid differs from requested recipe')
    if quality.get('certificate',{}).get('candidate_count')!=len(inv.a):
        raise ValueError('Historical certificate inventory mismatch')
    for key in ('fit_spectrum_sha256','guard_spectrum_sha256'):
        if type(quality.get(key)) is not str or len(quality[key])!=64 or quality[key]!=mp['provenance'].get(key):
            raise ValueError('Historical spectral identity mismatch')
    saved=mp['provenance'];inputs=td._inputs(context)
    if set(inputs)!={'config','design','materials','variant_rules'} or digest(saved.get('inputs'))!=digest(inputs):
        raise ValueError('Saved model must identify the four actual input fingerprints')
    if mp['domain']!='discrete_prewarped' or model.sample_rate_hz!=fs:
        raise ValueError('Saved native prewarped model and exact fs required')
    opt=base['options']
    dc=(dict(kind='explicit',description=opt['dc_origin']) if opt['R0'] is not None else
        dict(kind='zk_local_1d',description='Sum 8*mu*L/(pi*a^4) on local circular rigid slices; linear acoustic DC limit, not finite mean-flow law'))
    if digest(dc)!=digest(mp['dc_origin']) or (opt['R0'] is not None and opt['R0']!=model.R0):
        raise ValueError('Acoustic DC recipe differs from saved model')
    identity=digest(dict(inputs=inputs,effective=base['effective'],h_cm=opt['h_cm'],fs=fs,
                         R0=model.R0,dc=dc,basis_completion=base['basis_completion']))
    if saved.get('context_identity')!=identity:
        raise ValueError('Acoustic recipe does not match saved model context identity')
    producer=report.sources()
    numerical=dict(model_sha256=base['model_sha256'],model_parameters_sha256=digest(mp),
        inputs=inputs,recipe_identity=identity,parameters=params.as_dict(),rho_kg_m3=float(rho),
        fs=fs,v2_port_model=v2_port_model,max_extensions=max_extensions,max_iterations=max_iterations,
        observation=observation.as_dict(),compatibility_sources=report.compatibility_sources(producer))
    parent=None;accepted=0;origin=0.;inherited_bytes=0
    if resume is not None:
        parent=report.verify_chain(resume)
        inherited_bytes=report.chain_storage_bytes(resume)
        if inherited_bytes>report.MAX_RESULTS_BYTES-4*1024**2:
            raise ValueError('Insufficient remaining result budget for safe resume')
        old=parent['payload']
        if digest(old['compatibility'])!=digest(numerical):
            raise ValueError('Resume compatibility differs; producer bytes are retained separately')
        accepted=old['accepted_steps'];origin=old['origin_time_s']
        if target_steps<=accepted:
            raise ValueError('Target is cumulative and must exceed accepted chain steps')
        # Transactional public API validates the full state before any output.
        probe=SimultaneousCoupling(model,params=params,rho=rho,v2_port_model=v2_port_model,
                                   max_extensions=max_extensions,max_iterations=max_iterations)
        probe.import_checkpoint(old['checkpoint'])
    plan=dict(schema='dcalc.regime_plan.v1',config=base['config'],design=base['design'],output=str(output),
        model_in=str(Path(model_in).absolute()),model_sha256=base['model_sha256'],recipe=recipe,
        compatibility=numerical,plan_identity=digest(numerical),producer_sources=producer,
        software=context['provenance']['software'],fit_certificate_scope='historical_saved_fit_only; no refit or physical recertification',
        fit_certificate_sha256=digest(mp['quality']),fit_producer_sources=saved.get('sources_sha256'),
        fit_air=base['effective']['air'],bernoulli_air=dict(rho_kg_m3=float(rho),choice='explicit'),
        target_steps=target_steps,accepted_steps=accepted,origin_time_s=origin,
        seconds=seconds,checkpoint_steps=checkpoint_steps,resume=str(Path(resume).absolute()) if resume else None,
        parent=parent,budgets=dict(chain_seconds=6,chain_steps=72000,terms=192,memory_mib=768,
                                  child_seconds=180,total_scientific_seconds=600,blas_threads=1,result_bytes=report.MAX_RESULTS_BYTES,inherited_result_bytes=inherited_bytes))
    td._unchanged(context);model.verify_file_unchanged()
    return RunPlan(json.dumps(plan,allow_nan=False,sort_keys=True)),context


def advance(coupling,count,*,seconds,stop=lambda:False,after_accept=None):
    """One bounded chunk. Stop flags are examined only around accepted steps."""
    integer(count,'steps',0,72000);seconds=real(seconds,'seconds',minimum=0.)
    if seconds>175:raise ValueError('Wall budget')
    started=time.monotonic();reason=None;done=0
    for _ in range(count):
        if stop():reason='signal_before_step';break
        if time.monotonic()-started>=seconds:reason='timeout_before_step';break
        row=coupling.step()
        if not row['ok']:reason=row['reason'];break
        done+=1
        if after_accept is not None:after_accept(row)
        if stop():reason='signal_after_accepted_step';break
        if time.monotonic()-started>=seconds:reason='timeout_after_accepted_step';break
    return dict(ok=reason is None,accepted_steps=done,reason=reason)



def _history(path):
    """Verified ancestry, oldest first; paths remain source references only."""
    tip=report.verify_chain(path)
    first=report.read_json(Path(path).parent/'checkpoint-000000.json')['payload']
    records=[]
    if first['parent'] is not None:
        records.extend(_history(first['parent']['path']))
    for i in range(1,tip['payload']['sequence']+1):
        r=report.read_json(Path(path).parent/f'checkpoint-{i:06d}.json')['payload']
        records.append((Path(path).parent,r))
    return records


def _load_history(path,states,times,data,columns):
    done=0;references=[]
    for directory,record in _history(path):
        entry=record['series']
        if entry is None:raise ValueError('Missing native series in resume chain')
        if entry['columns']!=list(columns) or entry['start_steps']!=done:
            raise ValueError('Series continuity/columns mismatch')
        end=entry['end_steps'];size=end-done
        if not 1<=size<=12000 or end>=len(times):raise ValueError('Series size budget')
        file=report.safe_path(directory/entry['name'])
        with zipfile.ZipFile(file) as archive:
            info=archive.infolist()
            if set(i.filename for i in info)!={'time_s.npy','states.npy','data.npy'} or len(info)!=3:
                raise ValueError('Unexpected NPZ members')
            expected=8*((size+1)*(states.shape[1]+1)+size*len(columns))+3*1024
            if sum(i.file_size for i in info)>expected:
                raise ValueError('NPZ expansion budget exceeded')
            shapes={'time_s.npy':(size+1,), 'states.npy':(size+1,states.shape[1]), 'data.npy':(size,len(columns))}
            for member in info:
                with archive.open(member) as stream:
                    version=np.lib.format.read_magic(stream)
                    if version==(1,0):
                        shape,fortran,dtype=np.lib.format.read_array_header_1_0(stream,max_header_size=1024)
                    elif version==(2,0):
                        shape,fortran,dtype=np.lib.format.read_array_header_2_0(stream,max_header_size=1024)
                    else:raise ValueError('Unsupported NPY version')
                    if shape!=shapes[member.filename] or fortran or dtype!=np.dtype('float64') or member.file_size!=stream.tell()+8*math.prod(shape):
                        raise ValueError('NPY header shape/dtype/size rejected before allocation')
        with np.load(file,allow_pickle=False) as arrays:
            t,z,d=arrays['time_s'],arrays['states'],arrays['data']
            if t.shape!=(size+1,) or z.shape!=(size+1,states.shape[1]) or d.shape!=(size,len(columns)) or any(a.dtype!=np.float64 for a in (t,z,d)):
                raise ValueError('Native series shape/dtype mismatch')
            if not np.all(np.isfinite(t)) or not np.all(np.isfinite(z)) or not np.all(np.isfinite(d[:,0])):
                raise ValueError('Nonfinite native state/pressure')
            if done and (t[0]!=times[done] or not np.array_equal(z[0],states[done])):
                raise ValueError('Native series boundary differs')
            snap=record['checkpoint']['payload']['snapshot']
            if t[-1]!=snap['time_s'] or z[-1].tolist()!=snap['state']:
                raise ValueError('Native series endpoint differs from checkpoint')
            times[done:end+1]=t;states[done:end+1]=z;data[done:end]=d
        references.append(dict(path=str(file),sha256=entry['sha256'],producer_sources=record['producer_sources']))
        if report.file_sha256(file)!=entry['sha256']:raise ValueError('Series changed during load')
        done=end
    return done,references


def calculate(plan,*,stop=lambda:False):
    p=plan.as_dict();out=Path(p['output']);cinfo=p['compatibility'];started=time.monotonic()
    report.safe_path(out)
    initial_files=[]
    for entry in out.iterdir():
        if len(initial_files)>=2:raise ValueError('Fresh prepared output bundle required')
        initial_files.append(entry)
    if initial_files:
        if not (out/'plan.json').is_file() or report.read_json(out/'plan.json')!=p:
            raise ValueError('Prepared output plan differs')
        identity=(out/'plan.json').stat().st_ino
        for entry in initial_files:
            report.safe_path(entry)
            if entry.name!='plan.json' and (not entry.name.startswith('.pending-') or entry.stat().st_ino!=identity):
                raise ValueError('Unexpected existing output file')
    budget=report.MAX_RESULTS_BYTES-p['budgets']['inherited_result_bytes']-1024**2
    # Revalidate serialized requests too: --worker is not a preflight bypass.
    verified,_=preflight(p['config'],p['design'],out/'.validation-only',
        model_in=p['model_in'],params=DimensionedLipParameters(**cinfo['parameters']),
        rho=cinfo['rho_kg_m3'],observation=ObservationPlan.from_dict(cinfo['observation']),
        target_steps=p['target_steps'],sample_rate_hz=cinfo['fs'],v2_port_model=cinfo['v2_port_model'],
        max_extensions=cinfo['max_extensions'],max_iterations=cinfo['max_iterations'],
        seconds=p['seconds'],checkpoint_steps=p['checkpoint_steps'],resume=p['resume'],**p['recipe'])
    checked=verified.as_dict()
    for key in ('compatibility','accepted_steps','origin_time_s','parent','model_sha256',
                'fit_air','bernoulli_air','fit_certificate_sha256','fit_certificate_scope','budgets'):
        if digest(p[key])!=digest(checked[key]):raise ValueError('Serialized plan differs from native preflight')
    context=td._context(p['config'],p['design'])
    if td._inputs(context)!=cinfo['inputs']:raise ValueError('Inputs changed after preflight')
    for name,hashed in p['producer_sources'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=hashed:
            raise ValueError('Producer bytes changed after preflight')
    executed_sources=report.sources()
    model=PassiveResonator.load(p['model_in'],expected_sha256=p['model_sha256'])
    params=DimensionedLipParameters(**cinfo['parameters']);obs=ObservationPlan.from_dict(cinfo['observation'])
    c=SimultaneousCoupling(model,params=params,rho=cinfo['rho_kg_m3'],v2_port_model=cinfo['v2_port_model'],
                           max_extensions=cinfo['max_extensions'],max_iterations=cinfo['max_iterations'])
    parent=p['parent'];accepted=p['accepted_steps']
    if parent is not None:
        actual=report.verify_chain(p['resume'])
        if actual!=parent:raise ValueError('Resume source changed after preflight')
        c.import_checkpoint(parent['payload']['checkpoint'])
    initial=c.export_checkpoint();origin=p['origin_time_s'];chain_id=parent['payload']['chain_id'] if parent else digest(initial)
    sequence=0;previous=None;last_saved=accepted;chunks=[];reason=None
    def save(series=None):
        nonlocal sequence,previous,last_saved
        current=report.sources()
        if current!=executed_sources:raise ValueError('Producer source changed during execution')
        record=dict(schema='dcalc.regime_checkpoint.v1',sequence=sequence,previous_sha256=previous,
            parent=None if parent is None else dict(path=p['resume'],sha256=parent['sha256'],producer_sources=parent['payload']['producer_sources']),
            chain_id=chain_id,compatibility=cinfo,producer_sources=current,
            origin_time_s=origin,accepted_steps=accepted,checkpoint=c.export_checkpoint(),
            plan_identity=p['plan_identity'],series=series)
        previous=report.checkpoint(out,record,budget_bytes=budget);sequence+=1;last_saved=accepted
    save()
    nstate=len(c.state);count=p['target_steps'];segment_start=accepted
    if nstate>386:raise ValueError('Native state count budget')
    # One bounded trajectory matrix, no list of full per-step diagnostic dicts.
    states=np.empty((count+1,nstate));times=np.empty(count+1);data=np.empty((count,len(COLUMNS)))
    prior_series=[];done=0
    if p['resume'] is not None:
        states[0]=c.state;times[0]=c.time_s
        done,prior_series=_load_history(p['resume'],states,times,data,COLUMNS)
        if done!=accepted or times[done]!=c.time_s or states[done].tolist()!=c.state.tolist():
            raise ValueError('Resume history does not reach imported state')
    else:
        states[0]=c.state;times[0]=c.time_s
    chunk_start=done
    def collect(row):
        nonlocal done,accepted
        data[done]=[np.nan if row[k] is None else row[k] for k in COLUMNS]
        done+=1;accepted+=1;states[done]=c.state;times[done]=c.time_s
    try:
        while accepted<p['target_steps']:
            amount=min(p['checkpoint_steps'],p['target_steps']-accepted)
            remaining=max(0.,p['seconds']-(time.monotonic()-started))
            result=advance(c,amount,seconds=remaining,stop=stop,after_accept=collect)
            if accepted>last_saved:
                name=f'samples-{sequence:06d}.npz'
                arrays=dict(time_s=times[chunk_start:done+1],states=states[chunk_start:done+1],data=data[chunk_start:done])
                report.write_npz(out/name,arrays,budget_bytes=budget)
                series=dict(name=name,sha256=hashlib.sha256((out/name).read_bytes()).hexdigest(),
                            columns=list(COLUMNS),start_steps=accepted-(done-chunk_start),end_steps=accepted)
                save(series);chunks.append(series);chunk_start=done
            if not result['ok']:reason=result['reason'];break
            if stop():reason='signal_after_checkpoint';break
            if time.monotonic()-started>=p['seconds']:reason='timeout_after_checkpoint';break
    except (Exception,KeyboardInterrupt) as exc:
        reason=type(exc).__name__+': '+str(exc)
        # A failed save never invalidates the preceding complete checkpoint.
    observations=analyze(times[:done+1]-origin,states[:done+1],data[:done,0],sample_rate_hz=c.sample_rate_hz,plan=obs)
    for window in observations['windows']:
        for candidate in window['candidates']:
            if candidate.get('crossings_s'):
                candidate['energy']=report.energy_intervals(times[:done+1]-origin,states[:done+1],data[:done],list(COLUMNS),candidate['crossings_s'],
                    params=params,model=model,port_model=c.v2_port_model,start=window['start_s'],end=window['end_s'])
    td._unchanged(context);model.verify_file_unchanged()
    if time.monotonic()-started>=p['seconds'] and reason is None:reason='timeout_after_observation'
    if report.sources()!=executed_sources:reason='producer_changed'
    if stop() and reason is None:reason='signal_before_export'
    result=dict(schema='dcalc.regime_reference.v1',ok=reason is None,status='completed' if reason is None else 'partial',reason=reason,
        target_steps=p['target_steps'],accepted_chain_steps=accepted,accepted_segment_steps=done-segment_start,
        last_complete_checkpoint_steps=last_saved,last_checkpoint=f'checkpoint-{sequence-1:06d}.json',
        chain_id=chain_id,plan_identity=p['plan_identity'],compatibility=cinfo,producer_sources=executed_sources,
        fit_certificate_scope=p['fit_certificate_scope'],fit_certificate_sha256=p['fit_certificate_sha256'],
        fit_air=p['fit_air'],bernoulli_air=p['bernoulli_air'],initial=initial,final=c.export_checkpoint(),
        observations=observations,series=chunks,prior_series=prior_series,units=dict(time='s',pressure='Pa',flow='m^3/s',energy='J'),
        time_convention='state at n/n+1; native pressure, flow and powers at midpoint; work over [n,n+1]',
        completion_authority='execution.json required for CLI success; data completion alone is not process success',
        physical_validation='not_validated',parameters_calibration='to_calibrate',
        observation_scope='complete verified native chain; fixed disjoint windows; no duplicated evidence',elapsed_seconds=time.monotonic()-started)
    report.export(out,result,budget_bytes=budget)
    return result


def worker():
    raw=sys.stdin.read(report.MAX_JSON_BYTES+1)
    if len(raw)>report.MAX_JSON_BYTES:raise ValueError('Worker input budget')
    p=json.loads(raw);flag=[False]
    def handler(signum,frame):flag[0]=True
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,handler)
    try:
        value=calculate(RunPlan(json.dumps(p,allow_nan=False)),stop=lambda:flag[0])
        if flag[0]:value.update(ok=False,status='partial',reason='signal')
        print(json.dumps(dict(ok=value['ok'],status=value['status'],reason=value['reason']),allow_nan=False))
        return 0 if value['ok'] else 2
    except (Exception,KeyboardInterrupt) as exc:
        print(json.dumps(dict(ok=False,status='failed',reason=type(exc).__name__+': '+str(exc)),allow_nan=False));return 2


def run(config,design,output_dir,*,dry_run=False,**options):
    plan,_=preflight(config,design,output_dir,**options);p=plan.as_dict()
    if dry_run:return dict(ok=True,status='preflight_valid',dry_run=True,output_created=False,plan=p,not_executed=['acoustics','fit','simulation','writes'])
    execution_ready();out=Path(p['output']);out.mkdir(parents=True,exist_ok=False);report.write_json(out/'plan.json',p,budget_bytes=report.MAX_RESULTS_BYTES-p['budgets']['inherited_result_bytes'])
    flag=[False];handlers={};child=None;started=time.monotonic();reason=None
    def handler(signum,frame):flag[0]=True
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):handlers[sig]=signal.signal(sig,handler)
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    try:
        child=subprocess.Popen([sys.executable,'-B','-m','tools.regime_reference','--worker'],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,preexec_fn=child_limits)
        child.stdin.write(plan.document);child.stdin.close();child.stdin=None
        sent=False
        while child.poll() is None:
            elapsed=time.monotonic()-started
            if (flag[0] or elapsed>=p['seconds']+2) and not sent:
                child.send_signal(signal.SIGTERM);sent=True;reason='signal' if flag[0] else 'parent_timeout'
            if elapsed>=180:
                child.kill();reason='hard_child_deadline';break
            time.sleep(.02)
        stdout,stderr=child.communicate(timeout=5)
        value=json.loads(stdout) if stdout.strip() else dict(ok=False,status='failed',reason='child_without_result')
        if child.returncode!=0 or reason is not None:value.update(ok=False,status='partial',reason=reason or value.get('reason'))
        value['child']=dict(pid=child.pid,exit_code=child.returncode,reaped=True,wall_seconds=time.monotonic()-started,memory_mib=768,blas_threads=1)
        value['output_dir']=str(out)
        value['completion_authority']='execution.json: actual child exit and supervisor outcome'
        report.write_json(out/'execution.json',value,budget_bytes=report.MAX_RESULTS_BYTES-p['budgets']['inherited_result_bytes'])
        return value
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        for sig,h in handlers.items():signal.signal(sig,h)
