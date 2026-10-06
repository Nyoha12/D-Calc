"""Immutable strict exports, SI scalar CSV, loaded-source fingerprints."""
from __future__ import annotations

import csv
import copy
import hashlib
import io
import json
from pathlib import Path
import sys

import numpy as np
import yaml

from .regime_reference import safe_path, atomic_bytes, write_json
from ..pipeline.fixed_design import _software_source

ROOT=Path(__file__).resolve().parents[2]
CHECKPOINT_SCHEMA='dcalc.constrained_design.checkpoint.v2'


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
    return dict(software=_software_source(),versions=versions(),loaded_sources_sha256=source_files())


def versions():
    return dict(python=sys.version,numpy=np.__version__,pyyaml=yaml.__version__)


def calculation_context(contract):
    """Calculation inputs only: the reader's ambient imports are not identity."""
    from ..pipeline.design_pitch import models
    return copy.deepcopy(dict(request=contract.request,design=contract.base,config=contract.context['config'],
        inputs={k:v['sha256'] for k,v in contract.context['provenance']['files'].items()},
        request_bytes_sha256=getattr(contract,'input_files',{}).get('request',{}).get('sha256'),
        models=contract.options,effective_models=models(contract.context,contract.options)[3],
        lock_mask=contract.lock_mask,versions=versions()))


def context_identity(contract):
    return fingerprint(calculation_context(contract))


def verify_sources(sources):
    """Read the producer's actual source paths; never import them into a reader."""
    for name,expected in sources.items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or path.suffix!='.py':
            raise ValueError('manifeste sources invalide')
        resolved=(ROOT/path).resolve(strict=True)
        if not resolved.is_relative_to(ROOT): raise ValueError('source hors dépôt')
        if hashlib.sha256(resolved.read_bytes()).hexdigest()!=expected:
            raise ValueError('sources producteur modifiées: '+name)


def checkpoint_record(contract,candidate,producer,calculation=None):
    value=dict(schema_version=CHECKPOINT_SCHEMA,
        calculation_context=calculation if calculation is not None else calculation_context(contract),
        producer_provenance=producer,request_sha256=fingerprint(contract.request),
        candidate_sha256=fingerprint(candidate),candidate=candidate,
        status='search_witness_not_final_conformity')
    # Copy the snapshot: later candidate/context mutations must not change it.
    value=json.loads(json.dumps(value,allow_nan=False))
    value['context_sha256']=fingerprint(value['calculation_context'])
    value['checkpoint_sha256']=fingerprint(value)
    return value


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
    if value.get('schema_version')!=CHECKPOINT_SCHEMA:
        raise ValueError('checkpoint incompatible: manifeste producteur explicite requis (v2)')
    if value['request_sha256']!=fingerprint(contract.request): raise ValueError('REQUEST de reprise différent')
    if value['context_sha256']!=context_identity(contract): raise ValueError('contexte/sources de reprise différents')
    if value['candidate_sha256']!=fingerprint(value['candidate']): raise ValueError('checkpoint altéré')
    if value['context_sha256']!=fingerprint(value['calculation_context']): raise ValueError('contexte altéré')
    if value['checkpoint_sha256']!=fingerprint({k:v for k,v in value.items() if k!='checkpoint_sha256'}):
        raise ValueError('checkpoint altéré')
    producer=value['producer_provenance']
    if producer['versions']!=versions(): raise ValueError('versions producteur différentes')
    sources=producer['loaded_sources_sha256']
    if not sources: raise ValueError('manifeste sources producteur vide')
    verify_sources(sources)
    contract.check_locks(value['candidate']['physical_design'])
    return value
