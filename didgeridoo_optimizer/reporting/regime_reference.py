"""Strict immutable bundles and checksummed checkpoint chains."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import uuid

import numpy as np
from ..nonlinear.passive_resonator import digest, _pairs, _constant
from .time_domain_reference import sources as loaded_sources

MAX_RESULTS_BYTES = 250*1024**2
MAX_JSON_BYTES = 4*1024**2


def safe_path(path, *, exists=True):
    p=Path(path).absolute()
    for q in (p,*p.parents):
        if q.is_symlink():
            raise ValueError('Symbolic links, including dangling links, are refused')
    if exists and not p.exists():
        raise ValueError('Required file is absent')
    return p


def sources():
    result=loaded_sources()
    root=Path(__file__).resolve().parents[2]
    for name in ('tools/regime_reference.py','didgeridoo_optimizer/nonlinear/regime_observables.py',
                 'didgeridoo_optimizer/pipeline/regime_reference.py','didgeridoo_optimizer/reporting/regime_reference.py'):
        result[name]=hashlib.sha256((root/name).read_bytes()).hexdigest()
    return result


def compatibility_sources(manifest):
    names=('nonlinear/simultaneous_coupling.py','nonlinear/passive_resonator.py',
           'nonlinear/lip_ports.py','nonlinear/lips.py','nonlinear/onset_stability.py')
    return {n:manifest['didgeridoo_optimizer/'+n] for n in names}


def read_json_source(path):
    """Parse and fingerprint the SAME bounded bytes, retaining the request."""
    path=safe_path(path)
    if not path.is_file() or path.stat().st_size>MAX_JSON_BYTES:
        raise ValueError('Bounded regular JSON file required')
    # O_NOFOLLOW closes the final-component race after checking ancestors.
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        before=os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Regular JSON required')
        raw=f.read(MAX_JSON_BYTES+1)
        after=os.fstat(f.fileno())
    current=safe_path(path).stat()
    if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
            !=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
            or (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
            !=(current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns,current.st_ctime_ns)):
        raise ValueError('JSON source changed during read')
    if len(raw)>MAX_JSON_BYTES:
        raise ValueError('JSON size budget')
    value=json.loads(raw,object_pairs_hook=_pairs,parse_constant=_constant)
    json.dumps(value,allow_nan=False)  # also rejects overflow such as 1e400
    return value,dict(mode='file',format='json',path=str(path),
                      sha256=hashlib.sha256(raw).hexdigest(),requested=value)


def read_json(path):
    return read_json_source(path)[0]


def read_execution(output):
    """Normative command outcome AFTER run has closed; cancellation wins.

    A live writer's candidate is not a completion notification. Neither data
    completion nor a missing/malformed receipt is a command success.
    """
    out=Path(output)
    try:
        value=read_json(out/'execution.json')
        if type(value) is not dict or type(value.get('ok')) is not bool:
            raise ValueError('Invalid execution receipt')
        cancel=out/'execution.cancelled.json'
        if os.path.lexists(cancel):
            cancelled=read_json(cancel)
            if (type(cancelled) is not dict or cancelled.get('ok') is not False
                    or cancelled.get('execution_sha256')!=file_sha256(out/'execution.json')):
                raise ValueError('Invalid terminal cancellation')
            return cancelled
        closed=read_json(out/'execution.closed.json')
        if closed!={'execution_sha256':file_sha256(out/'execution.json'),'cancelled':False}:
            raise ValueError('Missing or inconsistent command closure')
        if type(value.get('child')) is not dict:
            raise ValueError('Child receipt required')
        if value['ok'] and (value.get('child',{}).get('exit_code')!=0
                            or value.get('child',{}).get('reaped') is not True):
            raise ValueError('Successful reaped child required')
        return value
    except (OSError,ValueError,TypeError) as exc:
        return dict(ok=False,status='unconfirmed',reason='terminal_receipt_unavailable: '+str(exc))


def directory_size(path):
    total=0;inodes=set()
    for p in safe_path(path).iterdir():
        safe_path(p)
        if not p.is_file():
            raise ValueError('Unexpected directory in bundle')
        st=p.stat()
        if (st.st_dev,st.st_ino) not in inodes:total+=st.st_size
        inodes.add((st.st_dev,st.st_ino))
    return total


def atomic_bytes(path, raw, *, budget_bytes=MAX_RESULTS_BYTES):
    """Never overwrite. A partial temp has no valid published identity.

    Signal handlers only set flags, so serialization/fsync/publication runs to
    its next coherent boundary. Hard interruptions leave the older complete
    checkpoint and an explicitly uncommitted .pending file.
    """
    path=safe_path(path,exists=False)
    if os.path.lexists(path):
        raise FileExistsError('Immutable output exists')
    if directory_size(path.parent)+len(raw)>budget_bytes:
        raise ValueError('Result storage budget exceeded')
    pending=path.with_name('.pending-'+uuid.uuid4().hex)
    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    # link is atomic and fails if the destination already exists. Retain the
    # temp inode under its explicit uncommitted name; no destructive cleanup.
    os.link(pending,path,follow_symlinks=False)
    dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(dfd)
    finally:os.close(dfd)


def write_json(path,value, *, budget_bytes=MAX_RESULTS_BYTES):
    raw=(json.dumps(value,allow_nan=False,sort_keys=True,indent=2)+'\n').encode()
    if len(raw)>MAX_JSON_BYTES:
        raise ValueError('JSON output budget')
    atomic_bytes(path,raw,budget_bytes=budget_bytes)


def write_npz(path,arrays, *, budget_bytes=MAX_RESULTS_BYTES):
    if any(type(v) is not np.ndarray or v.dtype.kind not in 'fiu' for v in arrays.values()):
        raise ValueError('NPZ numeric arrays only, no pickle')
    if sum(v.nbytes for v in arrays.values())>224*1024**2:
        raise ValueError('Series budget exceeded')
    buf=io.BytesIO();np.savez_compressed(buf,**arrays)
    atomic_bytes(path,buf.getvalue(),budget_bytes=budget_bytes)


def checkpoint(output,record, *, budget_bytes=MAX_RESULTS_BYTES):
    number=record['sequence']
    payload=dict(record)
    envelope=dict(payload=payload,sha256=digest(payload))
    name=f'checkpoint-{number:06d}.json'
    write_json(Path(output)/name,envelope,budget_bytes=budget_bytes)
    # Verify the bytes actually published before passing the chain identity on.
    if read_json(Path(output)/name)!=envelope:
        raise ValueError('Checkpoint readback differs')
    return envelope['sha256']


def file_sha256(path, maximum=MAX_RESULTS_BYTES):
    path=safe_path(path)
    if not path.is_file() or path.stat().st_size>maximum:
        raise ValueError('File size budget exceeded')
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def verify_chain(path, _seen=None, _budget=None):
    path=safe_path(path)
    _seen=set() if _seen is None else _seen
    _budget={'bytes':0,'files':set(),'checkpoints':0} if _budget is None else _budget
    if path in _seen or len(_seen)>=64:
        raise ValueError('Cyclic or excessive resume ancestry')
    _seen.add(path)
    match=re.fullmatch(r'checkpoint-(\d{6})\.json',path.name)
    if match is None:
        raise ValueError('Numbered checkpoint required')
    index=int(match[1])
    _budget['checkpoints']+=index+1
    if index>1000 or _budget['checkpoints']>2048:
        raise ValueError('Checkpoint chain budget')
    allowed={'plan.json','result.json','summary.csv','windows.csv','returns.csv','summary.md','execution.json','execution.cancelled.json','execution.closed.json'}
    for number,p in enumerate(path.parent.iterdir()):
        if number>=5000:
            raise ValueError('Bundle file budget')
        safe_path(p)
        if not p.is_file() or not (p.name in allowed or re.fullmatch(r'(checkpoint-\d{6}\.json|samples-\d{6}\.npz)',p.name) or re.fullmatch(r'\.pending-[0-9a-f]{32}',p.name)):
            raise ValueError('Unexpected bundle file')
        st=p.stat();inode=(st.st_dev,st.st_ino)
        if inode not in _budget['files']:_budget['bytes']+=st.st_size
        _budget['files'].add(inode)
        if _budget['bytes']>MAX_RESULTS_BYTES:
            raise ValueError('Complete chain storage budget')
    previous=None;first=None;last=None
    for number in range(index+1):
        envelope=read_json(path.parent/f'checkpoint-{number:06d}.json')
        if type(envelope) is not dict or set(envelope)!={'payload','sha256'} or envelope['sha256']!=digest(envelope['payload']):
            raise ValueError('Checkpoint content fingerprint mismatch')
        r=envelope['payload']
        required={'schema','sequence','previous_sha256','parent','chain_id','compatibility','producer_sources',
                  'origin_time_s','accepted_steps','checkpoint','plan_identity','series'}
        if type(r) is not dict or set(r)!=required or r['schema']!='dcalc.regime_checkpoint.v1' or type(r['sequence']) is not int or r['sequence']!=number or r['previous_sha256']!=previous:
            raise ValueError('Broken checkpoint sequence')
        if r['plan_identity']!=digest(r['compatibility']):
            raise ValueError('False plan identity')
        if first is None:
            first=r
            parent=r['parent']
            if parent is not None:
                if type(parent) is not dict or set(parent)!={'path','sha256','producer_sources'}:
                    raise ValueError('Invalid parent reference')
                old=verify_chain(parent['path'],_seen,_budget)
                if old['sha256']!=parent['sha256'] or old['payload']['producer_sources']!=parent['producer_sources']:
                    raise ValueError('Parent fingerprint mismatch')
                for key in ('chain_id','compatibility','origin_time_s','plan_identity','accepted_steps','checkpoint'):
                    if digest(r[key])!=digest(old['payload'][key]):
                        raise ValueError('Resumed origin differs from parent')
            elif r['accepted_steps']!=0 or r['origin_time_s']!=0.:
                raise ValueError('Original chain must start at zero')
        for key in ('chain_id','compatibility','origin_time_s','plan_identity','parent'):
            if digest(r[key])!=digest(first[key]):
                raise ValueError('Broken chain identity')
        steps=r['accepted_steps'];fs=r['checkpoint']['payload']['identity']['sample_rate_hz']
        if type(steps) is not int or not 0<=steps<=min(72000,6*fs):
            raise ValueError('Chain duration/step budget')
        cp=r['checkpoint']
        if type(cp) is not dict or set(cp)!={'payload','sha256'} or cp['sha256']!=digest(cp['payload']):
            raise ValueError('State checksum mismatch')
        snap=cp['payload']['snapshot']
        if abs(snap['time_s']-r['origin_time_s']-steps/fs)>1e-8 or (steps and snap['diagnostics'] is None):
            raise ValueError('Chain clock/history mismatch')
        if last is not None and steps<=last['accepted_steps']:
            raise ValueError('Nonprogressing checkpoint sequence')
        s=r['series']
        if s is not None:
            if type(s) is not dict or set(s)!={'name','sha256','columns','start_steps','end_steps'} or type(s['name']) is not str:
                raise ValueError('Invalid series record')
            if last is None or s['start_steps']!=last['accepted_steps'] or s['end_steps']!=steps:
                raise ValueError('Series step bounds differ from checkpoints')
            sp=safe_path(path.parent/s['name'])
            if sp.parent!=path.parent or s['name']!=f'samples-{number:06d}.npz' or file_sha256(sp)!=s['sha256']:
                raise ValueError('Series fingerprint mismatch')
        previous=envelope['sha256'];last=r
    return dict(payload=last,sha256=previous)


def chain_storage_bytes(path):
    budget={'bytes':0,'files':set(),'checkpoints':0}
    verify_chain(path,_budget=budget)
    return budget['bytes']


def energy_intervals(times,states,data,columns,crossings,*,params,model,port_model,start,end):
    """Prorated native work, including beginning/end fractions, never a proof."""
    if len(times)<2 or not len(crossings):return None
    keys=('source_work_j','jet_loss_j','lip_loss_j','contact_loss_j','resonator_loss_j','pressure_work_j')
    work=np.column_stack([data[:,columns.index(k)] for k in keys])
    if not np.all(np.isfinite(work)):return None
    cumulative=np.vstack((np.zeros(len(keys)),np.cumsum(work,axis=0)))
    def energy(state):
        n=len(model.a);x,v=state[:2]
        spring=params.mass_kg*(2*np.pi*params.resonance_hz)**2
        return float(.5*(params.mass_kg*v*v+spring*x*x)+.5*params.contact_stiffness_n_per_m*max(params.min_opening_m-params.rest_opening_m-x,0)**2+.5*np.sum(model.a*(state[2+n:]**2+model.omega**2*state[2:2+n]**2)))
    native=np.r_[energy(states[0]),data[:,columns.index('lip_energy_j')]+data[:,columns.index('resonator_energy_j')]]
    points=np.array([start,*crossings,end]);cw=np.column_stack([np.interp(points,times,cumulative[:,j]) for j in range(len(keys))]);ee=np.interp(points,times,native)
    from ..nonlinear.regime_observables import interpolate
    se=np.array([energy(s) for s in interpolate(times,states,points)])
    rows=[]
    for i,(a,b) in enumerate(zip(points[:-1],points[1:])):
        w=cw[i+1]-cw[i];change=ee[i+1]-ee[i]
        net=w[0]-sum(w[1:5])+(w[5] if port_model=='jet-only' else 0.)
        rows.append(dict(kind='initial_fraction' if i==0 else 'terminal_fraction' if i==len(points)-2 else 'return',
            start_s=float(a),end_s=float(b),duration_s=float(b-a),works_j=dict(zip(keys,map(float,w))),
            energy_change_j=float(change),balance_defect_j=float(change-net),
            endpoint_sample_fractions=[float((v-times[0])*model.sample_rate_hz%1) for v in (a,b)]))
    return dict(intervals=rows,energy_interpolation_discrepancy_j_max=float(np.max(abs(ee-se))),
        nonconjugate_mechanical_term_included=port_model=='jet-only',
        interpretation='native work/energy prorating is algebraic, not independent quadrature or evidence of recurrence')


def export(output,result, *, budget_bytes=MAX_RESULTS_BYTES):
    out=Path(output)
    write_json(out/'result.json',result,budget_bytes=budget_bytes)
    observations=result.get('observations',{})
    windows=observations.get('windows',[])
    criteria=result.get('compatibility',{}).get('observation',{})
    errors=('period_relative_span','amplitude_relative_span','envelope_log_drift',
            'return_scaled_max','return_relative_ac_max','shape_scaled_span',
            'state_mean_scaled_span','interpolation_sensitivity_scaled_max',
            'cubic_return_scaled_max','crossing_time_sensitivity_max_s',
            'section_state_sensitivity_scaled_max')
    # Scalar columns are numeric/text/bool; empty means unavailable, never zero.
    # Complex secondary cells and the exact original row retain strict JSON.
    def table(name,rows,fields,extra=lambda i,r:{}):
        buf=io.StringIO(newline='');w=csv.writer(buf)
        w.writerow(['index',*fields,'record_json'])
        for i,row in enumerate(rows):
            values=dict(row,**extra(i,row))
            w.writerow([i,*[values.get(k) for k in fields],
                        json.dumps(row,allow_nan=False,sort_keys=True)])
        atomic_bytes(out/name,buf.getvalue().encode(),budget_bytes=budget_bytes)
    compact=lambda v:json.dumps(v,allow_nan=False,sort_keys=True)
    fs=result.get('compatibility',{}).get('fs')
    steps=result.get('accepted_chain_steps')
    duration=steps/fs if type(steps) is int and type(fs) in (int,float) and fs>0 else None
    table('summary.csv',[result],['status','reason','ok','chain_id','plan_identity',
        'elapsed_seconds','duration_s','target_steps','accepted_chain_steps','accepted_segment_steps',
        'last_complete_checkpoint_steps','observation_status','completion_authority'],
        lambda i,r:dict(observation_status=observations.get('status'),duration_s=duration,
            completion_authority='read_execution(output) after command termination'))
    table('windows.csv',windows,['start_s','end_s','effective_start_s','effective_end_s',
        'section_index','section_level','status','reason','samples','native_midpoint_pressure_mean_pa',
        'pressure_ac_rms_pa','fft_auxiliary_hz','passages','passage_frequency_hz'],
        lambda i,r:dict(section_index=r.get('section_index',criteria.get('section_index')),
                       section_level=r.get('section_level',criteria.get('section_level'))))
    returns=[dict(window=i,**c) for i,w in enumerate(windows) for c in w.get('candidates',[])]
    table('returns.csv',returns,['window','group','status','reason','returns','return_frequency_hz',
        'covered_s','phase_points',*errors,'checks_json','failed_checks_json','criteria_json'],
        lambda i,r:dict(checks_json=compact(r.get('checks')),failed_checks_json=compact(r.get('failed_checks')),
                       criteria_json=compact(criteria)))
    def cell(v):
        if v is None:return '—'
        return str(v).replace('|',' / ').replace('\n',' ')
    lines=['# NL-REGIMES-01 — observation numérique expérimentale','',
        '**DONNÉES** : '+cell(result.get('status'))+' ; raison : '+cell(result.get('reason'))+'.',
        '**COMMANDE** : consulter `read_execution(output)` après terminaison. '
        '[execution.json](execution.json) est le reçu candidat ; '
        '`execution.cancelled.json`, si présent, prime. `execution.closed.json` confirme la clôture. '
        'Absence de clôture valide : succès non confirmé.',
        '', '| Résultat clé | Valeur |','|---|---|']
    for label,value in [('Pas acceptés / cible',str(result.get('accepted_chain_steps','—'))+' / '+str(result.get('target_steps','—'))),
                        ('Durée du calcul observée (s)',result.get('elapsed_seconds')),
                        ('Durée native couverte (s)',duration),
                        ('Observation',observations.get('status')),('Chaîne',result.get('chain_id')),
                        ('Identité du plan',result.get('plan_identity'))]:
        lines.append('| '+label+' | '+cell(value)+' |')
    lines+=['','| Fenêtre (s) | Statut / raison | AC RMS (Pa) | FFT auxiliaire (Hz) | Passages (Hz) |',
            '|---|---|---|---|---|']
    for w in windows:
        lines.append('| '+' | '.join(map(cell,[str(w.get('start_s','—'))+'–'+str(w.get('end_s','—')),
            str(w.get('status','—'))+' / '+str(w.get('reason','—')),w.get('pressure_ac_rms_pa'),
            w.get('fft_auxiliary_hz'),w.get('passage_frequency_hz')]))+' |')
    lines+=['','| Groupement | Persistance / raison |','|---|---|']
    for g in observations.get('groups',[]):
        lines.append('| '+cell(g.get('group'))+' | '+cell(g.get('status'))+' / '+cell(g.get('reason'))+' |')
    # Bound the synthesis independently of the number of candidate metrics.
    for group in sorted({r.get('group') for r in returns if type(r.get('group')) is int}):
        rows=[r for r in returns if r.get('group')==group]
        frequencies=[r['return_frequency_hz'] for r in rows if r.get('return_frequency_hz') is not None]
        reasons=sorted({r['reason'] for r in rows if r.get('reason')})
        failed=sorted({k for r in rows for k in r.get('failed_checks',[])})
        lines.append('Groupe '+str(group)+' : retours (Hz) '+
            (cell(min(frequencies))+' à '+cell(max(frequencies)) if frequencies else 'non observés')+
            ' ; raisons : '+', '.join(reasons)+' ; critères échoués : '+(', '.join(failed) or '—')+'.')
    lines+=['','[Détails JSON](result.json) · [Résumé CSV](summary.csv) · [Fenêtres](windows.csv) · [Retours et critères](returns.csv)',
        '', 'Fréquence FFT auxiliaire, passages et retours sont distincts. Période minimale non identifiée ; '
        'stabilité orbitale non évaluée. Aucun son joué ni toot validé. Le certificat de fit est historique. '
        'Le prorata des bilans est algébrique. Les candidats partagent les mêmes observations ; '
        'un groupe 2 ne désigne pas une note à la fréquence de retour.']
    atomic_bytes(out/'summary.md',('\n'.join(lines)+'\n').encode(),budget_bytes=budget_bytes)
