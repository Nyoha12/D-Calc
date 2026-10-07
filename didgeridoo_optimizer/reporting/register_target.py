"""Immutable chunk/checkpoint chains; hashes establish integrity, not authorship."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import zipfile

import numpy as np

from . import paired_onset as strict
from . import regime_reference as native
from .phase_reference import sources as phase_sources
from .constrained_design import versions, verify_sources
from ..nonlinear.passive_resonator import digest
from ..nonlinear import register_target as core
from ..pipeline.fixed_design import _software_source

ROOT=Path(__file__).resolve().parents[2]
SCHEMA='dcalc.register_target.result.v1'
NEW_SOURCES=('didgeridoo_optimizer/nonlinear/register_target.py',
             'didgeridoo_optimizer/pipeline/register_target.py',
             'didgeridoo_optimizer/reporting/register_target.py','tools/register_target.py')
REQUIRED_SOURCES=set(NEW_SOURCES)|{
    'didgeridoo_optimizer/nonlinear/simultaneous_coupling.py',
    'didgeridoo_optimizer/nonlinear/passive_resonator.py',
    'didgeridoo_optimizer/nonlinear/lips.py',
    'didgeridoo_optimizer/nonlinear/lip_ports.py',
    'didgeridoo_optimizer/nonlinear/phase_reference.py',
    'didgeridoo_optimizer/pipeline/regime_reference.py',
    'didgeridoo_optimizer/pipeline/paired_onset.py',
    'didgeridoo_optimizer/reporting/paired_onset.py',
    'didgeridoo_optimizer/optimization/design_contract.py'}
read_plan_source=strict.read_plan_source
read_json=native.read_json
safe_path=native.safe_path
file_sha256=native.file_sha256
write_json=native.write_json
atomic_bytes=native.atomic_bytes
MAX_BYTES=160*1024**2


def sources():
    result=phase_sources();result.update(strict.sources())
    for name in NEW_SOURCES:result[name]=file_sha256(ROOT/name)
    return result


def provenance():
    return dict(software=_software_source(),versions=versions(),loaded_sources_sha256=sources())


def reference(path):
    return dict(path=str(safe_path(path)),sha256=file_sha256(path))


def verify_reference(ref):
    core.exact(ref,{'path','sha256'},'file reference')
    path=safe_path(ref['path'])
    if file_sha256(path)!=ref['sha256']:raise ValueError('Referenced file fingerprint mismatch')
    return path


def save_checkpoint(out,sequence,experiment,context,previous,chunk=None):
    """Series first, checkpoint last. An orphan series is never committed history."""
    out=Path(out);limit=context['request']['budgets']['output_mib']*1024**2
    series=None
    if chunk is not None and chunk['stop_step']>chunk['start_step']:
        name=f'samples-{sequence:06d}.npz'
        native.write_npz(out/name,chunk['arrays'],budget_bytes=limit)
        series=dict(file=reference(out/name),start_step=chunk['start_step'],stop_step=chunk['stop_step'])
    value=dict(schema='dcalc.register_target.saved_checkpoint.v1',sequence=sequence,
               context_sha256=context['context_sha256'],context=context,previous=previous,
               series=series,experiment=experiment.checkpoint())
    name=f'checkpoint-{sequence:06d}.json'
    write_json(out/name,dict(payload=value,sha256=digest(value)),budget_bytes=limit)
    ref=reference(out/name)
    if read_json(out/name)['payload']!=value:raise ValueError('Checkpoint readback')
    return ref


def _zip_budget(path, n, states):
    expected={'states.npy':(n+1)*states*8,'times.npy':(n+1)*8,
              'values.npy':n*len(core.COLUMNS)*8,'midpoint_times.npy':n*8,'segments.npy':n*8}
    with zipfile.ZipFile(path) as z:
        items=z.infolist()
        if len(items)!=len(expected) or {i.filename for i in items}!=set(expected):raise ValueError('NPZ inventory')
        for i in items:
            if i.file_size>expected[i.filename]+512 or i.file_size<expected[i.filename]:raise ValueError('NPZ allocation quota')
            with z.open(i) as stream:
                version=np.lib.format.read_magic(stream)
                if version not in ((1,0),(2,0)):raise ValueError('NPY version')
                size_bytes=stream.read(2 if version==(1,0) else 4)
                header_size=int.from_bytes(size_bytes,'little')
                if not 1<=header_size<=256:raise ValueError('NPY header budget before read')
                header=stream.read(header_size)
                bounded=io.BytesIO(size_bytes+header)
                reader=np.lib.format.read_array_header_1_0 if version==(1,0) else np.lib.format.read_array_header_2_0
                shape,fortran,dtype=reader(bounded,max_header_size=256)
                shapes={'states.npy':(n+1,states),'times.npy':(n+1,),'values.npy':(n,len(core.COLUMNS)),'midpoint_times.npy':(n,),'segments.npy':(n,)}
                required=np.dtype('int64') if i.filename=='segments.npy' else np.dtype('float64')
                if shape!=shapes[i.filename] or fortran or dtype!=required or i.file_size!=stream.tell()+expected[i.filename]:raise ValueError('NPY header shape/dtype/bytes before allocation')
            if i.file_size>expected[i.filename]+512 or i.file_size<expected[i.filename]:raise ValueError('NPZ allocation quota')


def inspect_checkpoint(path, *, expected_request=None):
    """Bounded read-only chain inspection; no model, eigensolve or array loading."""
    ref=reference(path);seen=set();chain=[];bytes_seen={};request_hash=None
    while ref is not None:
        p=verify_reference(ref)
        if p in seen or len(chain)>=256:raise ValueError('Checkpoint cyclic/excessive ancestry')
        seen.add(p);bytes_seen[str(p)]=p.stat().st_size
        env=read_json(p);core.exact(env,{'payload','sha256'},'saved checkpoint')
        c=env['payload'];core.bounded_json(c)
        if env['sha256']!=digest(c) or c.get('schema')!='dcalc.register_target.saved_checkpoint.v1':raise ValueError('Saved checkpoint checksum/schema')
        core.exact(c,{'schema','sequence','context_sha256','context','previous','series','experiment'},'saved checkpoint payload')
        context=c['context'];r=context['request'];core.validate_request(r,states=context['metadata']['states'])
        if context['context_sha256']!=digest({k:v for k,v in context.items() if k!='context_sha256'}) or c['context_sha256']!=context['context_sha256']:
            raise ValueError('Checkpoint context identity')
        producer=context['producer']
        if not REQUIRED_SOURCES<=set(producer['loaded_sources_sha256']):raise ValueError('Incomplete source manifest')
        verify_sources(producer['loaded_sources_sha256'])
        if producer['versions']!=versions():raise ValueError('Checkpoint software versions')
        e=c['experiment'];core.exact(e,{'payload','sha256'},'experiment checkpoint')
        if e['sha256']!=digest(e['payload']):raise ValueError('Experiment checkpoint checksum')
        ep=e['payload'];h=digest(r)
        if ep['request_sha256']!=h or ep['request']!=r:raise ValueError('Experiment request differs')
        if request_hash is not None and request_hash!=h:raise ValueError('Resume changed controls/plan')
        request_hash=h
        if expected_request is not None and r!=expected_request:raise ValueError('Identical resume requires identical plan')
        if c['series'] is not None:
            s=c['series'];sp=verify_reference(s['file']);bytes_seen[str(sp)]=sp.stat().st_size
            n=s['stop_step']-s['start_step'];core.integer(n,'chunk rows',1,r['budgets']['chunk_steps'])
            _zip_budget(sp,n,context['metadata']['states'])
        if sum(bytes_seen.values())>MAX_BYTES:raise ValueError('Referenced chain storage quota')
        chain.append((p,c));ref=c['previous']
    chain.reverse();cursor=0;previous_dir=None;sequence=-1
    for path,c in chain:
        core.integer(c['sequence'],'checkpoint sequence',0,255)
        expected_sequence=sequence+1 if path.parent==previous_dir else 0
        if c['sequence']!=expected_sequence:raise ValueError('Checkpoint local sequence')
        previous_dir=path.parent;sequence=c['sequence']
        s=c['series']
        if s is not None:
            if s['start_step']!=cursor:raise ValueError('Gap/duplicate in series')
            cursor=s['stop_step']
        if c['experiment']['payload']['accepted_steps']!=cursor:raise ValueError('Counter/series mismatch')
    if not chain or chain[0][1]['experiment']['payload']['accepted_steps']!=0:raise ValueError('Initial checkpoint missing')
    return chain


def load_series(chain):
    """Allocate once from verified dimensions, stream chunks into final arrays."""
    last=chain[-1][1];context=last['context'];r=context['request']
    n=last['experiment']['payload']['accepted_steps'];d=context['metadata']['states']
    core.validate_request(r,states=d)
    if n>sum(s['steps'] for s in r['plateaus']):raise ValueError('Series quota')
    states=np.empty((n+1,d));times=np.empty(n+1);values=np.empty((n,len(core.COLUMNS)))
    mid=np.empty(n);segments=np.empty(n,dtype=np.int64)
    initial=chain[0][1]['experiment']['payload']['experiment_initial']['payload']['snapshot']
    states[0]=initial['state'];times[0]=initial['time_s'];cursor=0
    sums={k:0. for k in core.SUM_FIELDS}
    for _,c in chain:
        s=c['series']
        if s is None:continue
        p=verify_reference(s['file']);a=s['start_step'];b=s['stop_step'];m=b-a
        _zip_budget(p,m,d)
        with np.load(p,allow_pickle=False) as z:
            loaded={k:z[k] for k in ('states','times','values','midpoint_times','segments')}
        shapes={'states':(m+1,d),'times':(m+1,),'values':(m,len(core.COLUMNS)),'midpoint_times':(m,),'segments':(m,)}
        for k,v in loaded.items():core._array(v,shapes[k],k)
        fs=core.sample_rate(r);origin=initial['time_s']
        if loaded['segments'].dtype!=np.dtype('int64'):raise ValueError('Integer segment labels required')
        if not np.allclose(loaded['times'],origin+np.arange(a,b+1)/fs,rtol=0,atol=1e-10) or not np.allclose(loaded['midpoint_times'],origin+(np.arange(a,b)+.5)/fs,rtol=0,atol=1e-10):raise ValueError('Native time grid mismatch')
        if not np.array_equal(states[a],loaded['states'][0]) or times[a]!=loaded['times'][0]:raise ValueError('Chunk boundary discontinuity')
        states[a:b+1]=loaded['states'];times[a:b+1]=loaded['times'];values[a:b]=loaded['values']
        mid[a:b]=loaded['midpoint_times'];segments[a:b]=loaded['segments'];cursor=b
        ep=c['experiment']['payload'];snap=ep['native']['payload']['snapshot']
        if snap['state']!=states[b].tolist() or snap['time_s']!=times[b]:raise ValueError('Checkpoint/series state mismatch')
        for row in loaded['values']:
            for k in sums:sums[k]+=float(row[core.COLUMNS.index(k)])
        if sums!=ep['sums']:raise ValueError('Cumulative energy ledger mismatch')
        expected=[]
        for i,plateau in enumerate(r['plateaus']):expected.extend([i]*plateau['steps'])
        if not np.array_equal(segments[a:b],np.asarray(expected[a:b])):raise ValueError('Segment labels mismatch')
    if cursor!=n:raise ValueError('Incomplete series')
    for _,c in chain:
        ep=c['experiment']['payload'];index=ep['segment'];start=sum(s['steps'] for s in r['plateaus'][:index])
        if ep['segment_reset']!=states[start].tolist():raise ValueError('Segment reset/series mismatch')
        for event in ep['events']:
            k=event['step'];snap=event['before']['payload']['snapshot']
            if snap['state']!=states[k].tolist() or snap['time_s']!=times[k]:raise ValueError('Event/series boundary mismatch')
    return dict(states=states,times=times,values=values,midpoint_times=mid,segments=segments)


def table(path,rows,fields,limit):
    stream=io.StringIO(newline='');w=csv.writer(stream);w.writerow([*fields,'record_json'])
    for row in rows:w.writerow([*[row.get(k) for k in fields],json.dumps(row,allow_nan=False,sort_keys=True)])
    atomic_bytes(path,stream.getvalue().encode(),budget_bytes=limit)


def export(out,result,limit):
    out=Path(out)
    # Rich observations are split per window, and full PHASE is a separate bounded file.
    analysis=result.pop('analysis');windows=analysis.pop('windows');phase=analysis.pop('phase')
    refs={}
    for name,row in windows.items():
        file='window-'+name+'.json';write_json(out/file,row,budget_bytes=limit);refs[name]=file
    if phase is not None:write_json(out/'phase.json',phase,budget_bytes=limit)
    result['analysis']=dict(analysis,windows=refs,phase_file='phase.json' if phase is not None else None)
    table(out/'criteria.csv',analysis['criteria'],['id','role','observable','status','unit_si','reason'],limit)
    table(out/'controls.csv',result['plan']['request']['plateaus'],['id','pressure_pa','resonance_hz','damping_ratio','steps','duration_s'],limit)
    table(out/'events.csv',result['events'],['step','old_segment','new_segment','command_work_j','lip_energy_before_j','lip_energy_after_j'],limit)
    write_json(out/'result.json',result,budget_bytes=limit)
    lines=['REGISTER-TARGET-01 — expérience prescrite',
           'Trajectoire : '+result['status']+' ; conformité : '+analysis['conformity'],
           'Fréquence/périodicité observée selon bande et protocole explicites, pas primitive certifiée.',
           'Pic spectral, passages, activité, PHASE, stabilité orbitale et accessibilité restent distincts.',
           'Les paramètres V2 sont provisoires, à calibrer. Aucun joueur automatique ni validation physiologique.',
           'Reprise identique : checkpoint produit et chaîne vérifiés. Import externe R36 non supporté.',
           'Les checksums prouvent une intégrité, pas une identité d’auteur ni une recertification du fit.',
           'Le marqueur terminal confirme la clôture logicielle ; aucune transaction disque universelle promise.']
    atomic_bytes(out/'summary.txt',('\n'.join(lines)+'\n').encode(),budget_bytes=limit)


def prepare_completion(out,execution,limit):
    out=Path(out)
    manifest={p.name:file_sha256(p) for p in out.iterdir() if p.is_file() and not p.name.startswith('.pending')}
    write_json(out/'manifest.json',manifest,budget_bytes=limit)
    execution=dict(execution,manifest_sha256=file_sha256(out/'manifest.json'))
    write_json(out/'execution.json',execution,budget_bytes=limit)
    write_json(out/'execution.closed.json',dict(execution_sha256=file_sha256(out/'execution.json')),budget_bytes=limit)
    marker=dict(schema='dcalc.register_target.completion.v1',execution_sha256=file_sha256(out/'execution.json'),
                closure_sha256=file_sha256(out/'execution.closed.json'))
    write_json(out/'.pending-completion.json',marker,budget_bytes=limit)


def publish_completion(out):
    # Absolutely last fallible operation, after child reaping and restorations.
    out=Path(out);os.link(out/'.pending-completion.json',out/'execution.completed.json',follow_symlinks=False)


def read_result(out,expected_context=None):
    out=safe_path(out)
    try:
        marker=read_json(out/'execution.completed.json');execution=read_json(out/'execution.json')
        if marker!=dict(schema='dcalc.register_target.completion.v1',execution_sha256=file_sha256(out/'execution.json'),
                        closure_sha256=file_sha256(out/'execution.closed.json')):raise ValueError('Terminal marker')
        if read_json(out/'execution.closed.json')!=dict(execution_sha256=file_sha256(out/'execution.json')):raise ValueError('Closure')
        children=execution['children']
        if len(children)!=1 or children[0]['reaped'] is not True or (execution['ok'] and children[0]['exit_code']!=0):raise ValueError('Actual child receipt')
        manifest=read_json(out/'manifest.json')
        if file_sha256(out/'manifest.json')!=execution['manifest_sha256']:raise ValueError('Manifest')
        if not {'plan.json','result.json','criteria.csv','controls.csv','events.csv','summary.txt'}<=set(manifest):raise ValueError('Missing required artifact')
        for name,h in manifest.items():
            if Path(name).name!=name or file_sha256(out/name)!=h:raise ValueError('Artifact integrity')
        result=read_json(out/'result.json');context=read_json(out/'plan.json')
        if result['schema']!=SCHEMA or result['plan']!=context:raise ValueError('Result context')
        if expected_context is not None and context['context_sha256']!=expected_context:raise ValueError('Unexpected context')
        analysis=result['analysis'];window_ids={w['id'] for w in context['request']['windows']}
        if set(analysis['windows'])!=window_ids:raise ValueError('Missing observation window references')
        for wid,name in analysis['windows'].items():
            if name!='window-'+wid+'.json' or name not in manifest:raise ValueError('Uncommitted observation artifact')
            read_json(out/name)
        expected_phase='phase.json' if context['request']['observation']['phase'] is not None else None
        if analysis['phase_file']!=expected_phase or (expected_phase is not None and expected_phase not in manifest):raise ValueError('Uncommitted PHASE artifact')
        chain=inspect_checkpoint(verify_reference(result['checkpoint']),expected_request=context['request'])
        if chain[-1][1]['context_sha256']!=context['context_sha256']:raise ValueError('Tip context')
        arrays=load_series(chain)
        # The native importer validates full reset, diagnostics and physical state.
        from ..pipeline.register_target import experiment_for
        experiment=experiment_for(context);experiment.restore(chain[-1][1]['experiment'])
        if result['accepted_steps']!=experiment.accepted or result['sums']!=experiment.sums or result['events']!=experiment.events:
            raise ValueError('Result/checkpoint counters or ledger')
        total=sum(s['steps'] for s in context['request']['plateaus'])
        if execution['ok'] and (result['status']!='complete' or experiment.accepted!=total):raise ValueError('False trajectory completion')
        return dict(ok=execution['ok'],status=result['status'],execution=execution,result=result)
    except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile) as exc:
        return dict(ok=False,status='unconfirmed',reason=str(exc))
