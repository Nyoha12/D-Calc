"""Immutable strict exports, SI scalar CSV, loaded-source fingerprints."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import sys

from .regime_reference import safe_path, atomic_bytes, write_json
from ..pipeline.fixed_design import _software_source


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def source_files():
    root=Path(__file__).resolve().parents[2]
    paths=set()
    for name,module in tuple(sys.modules.items()):
        if name.startswith(('didgeridoo_optimizer','tools.')):
            f=getattr(module,'__file__',None)
            if f: paths.add(Path(f).resolve())
    # CLI may be loaded as __main__ rather than tools.constrained_design.
    module=sys.modules.get('__main__'); f=getattr(module,'__file__',None)
    if f and Path(f).resolve()==root/'tools/constrained_design.py': paths.add(Path(f).resolve())
    result={}
    for p in sorted(paths):
        name=p.relative_to(root).as_posix()
        result[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def provenance():
    return dict(software=_software_source(),loaded_sources_sha256=source_files())


def context_identity(contract):
    return fingerprint(dict(request=contract.request,design=contract.base,config=contract.context['config'],
        inputs={k:v['sha256'] for k,v in contract.context['provenance']['files'].items()},
        models=contract.options,sources=source_files()))


def new_destination(path):
    p=safe_path(path,exists=False)
    if p.exists(): raise FileExistsError('Sortie existante refusée, même partielle')
    return p


def csv_write(path, rows, fields):
    stream=io.StringIO(newline='')
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
    writer.writeheader(); writer.writerows(rows)
    atomic_bytes(path,stream.getvalue().encode('utf-8'))


def export_result(output,result,contract):
    output=Path(output)
    for i,candidate in enumerate(result['solutions'],1):
        contract.check_locks(candidate['physical_design'])
        name=f'design_{i:03d}.json'
        write_json(output/name,candidate['physical_design'])
        reread=json.loads((output/name).read_text())
        contract.check_locks(reread)
        if reread!=candidate['physical_design']: raise ValueError('relecture export différente')
        candidate['design_file']=name
    if not result['solutions']:
        contract.check_locks(result['final']['physical_design'])
        write_json(output/'partial_design.json',result['final']['physical_design'])
    fields=['candidate','id','observable','role','status','value_si','unit_si','error_cents',
            'margin','margin_unit','convergence_estimate','certified_bound','reason']
    csv_write(output/'criteria.csv',[dict(row,candidate='final') for row in result['final']['criteria']],fields)
    rows=[]
    for h in result['search']['history']:
        for c in h['criteria']:
            rows.append(dict(c,evaluation=h['evaluation'],stage=h['stage'],search_feasible=h['search_feasible']))
    csv_write(output/'trials.csv',rows,['evaluation','stage','search_feasible']+fields[1:])
    write_json(output/'result.json',result)
    atomic_bytes(output/'report.txt',(
        'Conception sous exigences — modèle statique nominal\n'
        f"État : {result['status']} ; solutions conformes : {len(result['solutions'])}\n"
        f"Évaluations recherche : {result['search']['evaluations']}\n"
        'Conformité numérique au modèle/scénario/résolution déclaré ; aucune validation physique.\n'
        'Meilleur témoin trouvé, aucun optimum global ni impossibilité globale prouvés.\n'
        'Les limites et critères non couverts sont conservés dans result.json.\n').encode())
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    write_json(output/'manifest.json',manifest)
    return result


def read_checkpoint(output,contract):
    """Read-only recovery of last accepted search witness, never automatic resume."""
    out=safe_path(output)
    paths=sorted(out.glob('checkpoint_*.json'))
    if not paths: return None
    value=json.loads(safe_path(paths[-1]).read_text())
    if value['request_sha256']!=fingerprint(contract.request): raise ValueError('REQUEST de reprise différent')
    if value['context_sha256']!=context_identity(contract): raise ValueError('contexte/sources de reprise différents')
    if value['candidate_sha256']!=fingerprint(value['candidate']): raise ValueError('checkpoint altéré')
    contract.check_locks(value['candidate']['physical_design'])
    return value
