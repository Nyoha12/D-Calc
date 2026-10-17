"""Prepare a fixed R49/R59/R61 context from user-supplied locations, without science.

Run from the checkout with PYTHONPATH=. and BLAS thread counts set to one.
Existing acquisitions are never fitted, converted, measured or stepped here.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import numpy as np

from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as strict
from didgeridoo_optimizer.nonlinear.passive_resonator import digest
from didgeridoo_optimizer.nonlinear.passive_fit import DEFAULT_GATES
from didgeridoo_optimizer.pipeline import time_domain_reference as td
from didgeridoo_optimizer.pipeline.design_pitch import models
from didgeridoo_optimizer.pipeline.state_register_context import case_context, reference
from didgeridoo_optimizer.reporting import state_register_context as evidence
from didgeridoo_optimizer.reporting.regime_reference import read_json


def prepare(config, design, acquisition, realization, template, output, *, date, materials=None, variant_rules=None):
    paths={k:strict.safe_path(v) for k,v in dict(config=config,design=design,acquisition=acquisition,
                                              realization=realization,template=template).items()}
    out=strict.safe_path(output,exists=False)
    if out.exists(): raise ValueError('New preparation directory required')
    r59=paths['acquisition'];bundle=paths['realization']
    old=read_json(r59/'manifest.json');intent=read_json(r59/'intent.json');result=read_json(r59/'result.json')
    sources=read_json(r59/'sources.json')
    # Relocated acquisitions keep original manifest filenames; compare by basename,
    # but never resolve or execute historical paths embedded in that manifest.
    for name in ('intent.json','result.json','sources.json','coefficients.json','training.npz','audit.npz'):
        matches=[h for p,h in old.items() if Path(p).name==name]
        if matches!=[reference(r59/name)['sha256']]: raise ValueError('R59 acquisition fingerprint: '+name)
    original_config_hash=reference(paths['config'])['sha256']
    if original_config_hash not in intent['inputs'].values() or reference(paths['design'])['sha256'] not in intent['inputs'].values():
        raise ValueError('Original acquisition CONFIG/DESIGN mismatch')
    relocated=False
    if materials is not None or variant_rules is not None:
        if materials is None or variant_rules is None: raise ValueError('Both relocation DB paths required')
        config_data=read_json(paths['config'])
        config_data['materials']=dict(config_data.get('materials',{}),database_file=str(strict.safe_path(materials)),
                                      variant_rules_file=str(strict.safe_path(variant_rules)))
        out.mkdir();relocated=True
        relocated_config=out/'config-relocated.json'
        relocated_config.write_text(json.dumps(config_data,indent=2)+'\n')
        paths['config']=relocated_config
    context=td._context(paths['config'],paths['design'])
    hashes={k:v['sha256'] for k,v in context['provenance']['files'].items()}
    original_inputs=list(intent['inputs'].values())
    if original_config_hash not in original_inputs or hashes['design'] not in original_inputs:
        raise ValueError('R59 CONFIG/DESIGN mismatch (preserve original bytes when relocating)')
    for role,name in [('materials','project_specs/materials_base_v1.yaml'),('variant_rules','project_specs/wood_variant_rules_v1.yaml')]:
        if sources[name]['sha256']!=hashes[role]: raise ValueError('R59 material/rules mismatch')
    recipe=dict(sample_rate_hz=12000,h_cm=.5,loss_model='zk',radiation_model='legacy',air_reference='ck_dry20')
    effective=models(context,recipe)[3]
    if effective!=result['physics'] or any(x['h_cm']!=.5 for x in result['tmm_calls'][:6]):
        raise ValueError('R59 effective recipe mismatch')
    source=read_json(r59/'coefficients.json');mp=read_json(bundle/'model.json')['parameters']
    source_hash=reference(r59/'coefficients.json')['sha256']
    if reference(bundle/'source.json')['sha256']!=source_hash or result['coefficient_sha256']!=source_hash:
        raise ValueError('R59/R61 original scalar mismatch')
    if result['gates']!=DEFAULT_GATES or result['training_domain_hz']!=source['training_domain_hz']:
        raise ValueError('R59 policy/domain mismatch')
    # R59 saved supports are fixed; A/B change their audit/extrapolation roles.
    boundaries=[('low',.1,40.),('interior',40.,3000.),('guard',3000.,3500.),('upper',3500.,5500.),('edge',5500.,5990.)]
    bands=[dict(id=k,role=result['bands'][k]['role'],domain_hz=[a,b],points=result['bands'][k]['points']) for k,a,b in boundaries]
    n=result['training_points']; strict.integer(n,'training points',2,8192)
    train_headers={k+'.npy':((n,),np.dtype('float64')) for k in ('frequency_hz','real','imag')}
    audit_headers={}
    for b in bands:
        for suffix,dtype in [('_f','float64'),('_target','complex128'),('_model','complex128')]:
            audit_headers[b['id']+suffix+'.npy']=((b['points'],),np.dtype(dtype))
    for name,dtype in [('anchor_f','float64'),('anchor_coarse','complex128'),('anchor_fine','complex128')]:
        audit_headers[name+'.npy']=((16,),np.dtype(dtype))
    train_raw=strict.read_bytes(r59/'training.npz');audit_raw=strict.read_bytes(r59/'audit.npz')
    evidence._npz_headers(train_raw,train_headers);evidence._npz_headers(audit_raw,audit_headers)
    with np.load(io.BytesIO(audit_raw),allow_pickle=False) as z:
        arrays={b['id']+suffix:z[b['id']+suffix] for b in bands for suffix in ('_f','_target')}
    buf=io.BytesIO();np.savez(buf,**arrays);audit_raw=buf.getvalue()
    manifest=dict(schema=evidence.SCHEMA,inputs=hashes,recipe=recipe,effective=effective,
        physical_radius_m=context['design'].segments[-1].d_out_cm/200,
        source_sha256=source_hash,model_sha256=digest(mp),domain_hz=source['training_domain_hz'],
        coefficient_domain=source['coefficient_domain'],
        acquisition=dict(nature='simulation_tmm',date=date,sha=result['sha'],
            provenance='R59 finite TMM acquisition; not a measurement. Original acquisition files: '+
            json.dumps({k:reference(r59/k)['sha256'] for k in ('manifest.json','intent.json','sources.json','training.npz','audit.npz','result.json')},sort_keys=True)+
            ('; CONFIG relocated: only the two material paths changed; original CONFIG SHA256 '+original_config_hash if relocated else '')),
        gates=dict(DEFAULT_GATES),bands=bands,
        training=dict(file='training.npz',sha256=strict.sha(train_raw),points=n),
        audit=dict(file='audit.npz',sha256=strict.sha(audit_raw)))
    evidence.validate_manifest(manifest)
    plan=read_json(paths['template'])
    plan['case']=dict(kind='saved_state',id=plan['case']['id'],config=str(paths['config']),design=str(paths['design']),
                      realization=str(bundle),evidence=str(out/'evidence.json'),recipe=recipe)
    if not relocated:out.mkdir()
    (out/'training.npz').write_bytes(train_raw);(out/'audit.npz').write_bytes(audit_raw)
    (out/'evidence.json').write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n')
    case_context(plan['case'],plan['observation'],plan['rho_kg_m3'])
    (out/'plan-r49.json').write_text(json.dumps(plan,indent=2,allow_nan=False)+'\n')
    return dict(evidence=str(out/'evidence.json'),plan=str(out/'plan-r49.json'),executed_steps=0)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('config','design','acquisition','realization','template','output','date'):p.add_argument('--'+name,required=True)
    p.add_argument('--materials');p.add_argument('--variant-rules')
    print(json.dumps(prepare(**vars(p.parse_args()))))


if __name__=='__main__':main()
