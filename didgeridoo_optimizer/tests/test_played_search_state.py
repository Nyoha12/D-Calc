import copy
import json
from pathlib import Path
import pytest
from didgeridoo_optimizer.optimization import played_search as core
from didgeridoo_optimizer.pipeline import played_search as flow

EXAMPLE=Path(__file__).resolve().parents[2]/'project_specs/examples/geometry_state'


def test_opt_in_recipe_slots_and_no_science(tmp_path,monkeypatch):
    from didgeridoo_optimizer.nonlinear import scalar_fit
    def no(*a,**kw):raise AssertionError('No fit')
    monkeypatch.setattr(scalar_fit,'fit',no)
    p=flow.preflight(EXAMPLE/'job.yaml',tmp_path/'absent')
    assert not (tmp_path/'absent').exists()
    assert len(core.recipe(p['job']['fit']))==5
    template=p['scenarios'][0]['template'];assert template['case']['kind']=='saved_state'
    real=flow.instantiate(template,EXAMPLE/'config.yaml',EXAMPLE/'design.json',tmp_path/'fit/model.json')
    assert real['case']['realization']==str(tmp_path/'fit/realization')
    assert real['case']['evidence']==str(tmp_path/'fit/evidence.json')
    assert template['case']['evidence']=='@EVIDENCE'


def test_backend_is_explicit_distinct_and_targets_cannot_enter():
    import yaml
    old=yaml.safe_load((EXAMPLE.parent/'played_search/request.yaml').read_text())['fit']
    before=copy.deepcopy(old);core.validate_fit(old);assert old==before
    new=json.loads((EXAMPLE/'recipe.json').read_text());core.validate_fit(new)
    for field,value in [('target_hz',70),('backend','other'),('fit_points',6144)]:
        changed=dict(new);changed[field]=value
        with pytest.raises(ValueError):core.validate_fit(changed)


def test_failed_budget_never_refreshes_quotas(tmp_path):
    p=flow.preflight(EXAMPLE/'job.yaml');b=flow.Budget(p['job']['budgets'],tmp_path)
    for _ in range(2):b.reserve('fit',175,1);b.launched('fit',0)
    with pytest.raises(flow.BudgetStop):b.reserve('fit',175,1)
    assert b.counts['fits']==b.reserved['fits']==2


def test_partial_native_reader_success_is_not_complete(monkeypatch):
    from didgeridoo_optimizer.reporting import register_target
    result=dict(analysis=dict(criteria=[]),accepted_steps=48,
                plan=dict(context_sha256='identity',request=dict(case=dict(kind='saved_state'))))
    monkeypatch.setattr(register_target,'read_result',lambda path:dict(ok=False,status='partial',result=result))
    value=flow.worker(dict(kind='read',output='unused'))
    assert value['ok'] is False and value['read_confirmed'] is True and value['accepted_steps']==48


def test_real_two_geometry_models_chunks_and_native_criteria():
    import os
    from didgeridoo_optimizer.reporting import played_search as report
    root=os.environ.get('DCALC_GEOMETRY_STATE_SEARCH')
    if not root:pytest.skip('Requires separately acquired product demonstration, no implicit science')
    root=Path(root);got=report.read_result(root);assert got['ok'],got
    result=got['result'];assert len(result['search']['history'])==2
    assert result['counters']['accepted_steps']==result['counters']['steps_charged']==192
    assert result['counters']['fits']==2 and result['counters']['trajectories']==4
    identities=[]
    for summary in result['search']['history']:
        row=report.read_json(root/summary['file']);assert row['units_complete']
        unit=row['units'][0];assert unit['verified'] and len(unit['segments'])==2
        assert [(s['accepted_before'],s['accepted_after']) for s in unit['segments']]==[(0,48),(48,96)]
        assert [r['status'] for r in row['criteria'] if r['kind']=='played']==['unresolved']
        fit=root/unit['fit'];source=report.read_json(fit/'source.json');model=report.read_json(fit/'realization/model.json')
        identities.append((source['provenance']['inputs']['design'],model['sha256']))
    assert identities[0][0]!=identities[1][0] and identities[0][1]!=identities[1][1]
    assert not result['conforming']


def test_static_rehashed_value_is_recomputed_without_tmm():
    p=flow.preflight(EXAMPLE/'job.yaml');contract,_=flow.load_inputs(EXAMPLE/'job.yaml')
    d=contract.generate(p['initial']);rows=flow.fixed.criterion_rows(contract,d,{}, {})
    detail=dict(criteria=rows,geometry_state_acquisition=dict(design_sha256=flow.digest(d.as_dict()),spectra=[]))
    flow.verify_static_detail(detail,contract,d,'fixed')
    detail['criteria'][0]['value_si']+=.01
    with pytest.raises(ValueError,match='criterion arithmetic'):flow.verify_static_detail(detail,contract,d,'fixed')


def test_real_cache_association_refuses_other_profile_recipe_and_db(tmp_path):
    import os
    from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as strict
    root=os.environ.get('DCALC_GEOMETRY_STATE_SEARCH')
    if not root:pytest.skip('Requires separately acquired product demonstration')
    root=Path(root);fit=root/'candidate-001/fit-nominal';plan=strict.read_json(fit/'plan.json')
    design=root/'candidate-001/design-nominal.json';config=plan['inputs']['config']['path'];recipe=plan['recipe']
    a=flow.read_fit(fit,recipe,design,config);b=flow.read_fit(fit,recipe,design,config)
    assert a==b
    with pytest.raises(ValueError,match='geometry/context'):flow.read_fit(fit,recipe,root/'candidate-002/design-nominal.json',config)
    changed=dict(recipe,top_hz=5500)
    with pytest.raises(ValueError,match='recipe changed'):flow.read_fit(fit,changed,design,config)
    # A changed actual DB cannot be admitted by refreshing only the plan envelope.
    saved=tmp_path/'fit';saved.mkdir();db=tmp_path/'materials.yaml';db.write_bytes(strict.read_bytes(plan['inputs']['materials']['path'])+b'\n# mutation\n')
    plan['inputs']['materials']['path']=str(db);plan['sha256']=flow.digest({k:v for k,v in plan.items() if k!='sha256'})
    (saved/'plan.json').write_text(strict.strict_json(plan))
    with pytest.raises(ValueError,match='input changed'):flow.read_fit(saved,recipe,design,config)


def test_geometry_shared_receipt_reports_actual_collection(tmp_path,monkeypatch):
    from didgeridoo_optimizer.reporting import geometry_state as report
    class Child:
        pid=123;returncode=0
        def poll(self):return self.returncode
        def wait(self,timeout):return self.returncode
    def launch(*args,**kw):
        kw['stdout'].write(b'{"ok":true}\n');return Child()
    monkeypatch.setattr(flow.subprocess,'Popen',launch)
    limits=dict(candidates=1,scenarios=1,fits=1,trajectories=1,steps=96,seconds=180,
        memory_mib=768,output_mib=80,child_seconds=175)
    budget=flow.Budget(limits,tmp_path);children=[]
    value,receipt=flow.launch('fit',dict(kind='geometry',plan={}),tmp_path,budget,children,seconds=10,output_bytes=1024)
    assert value['ok'] and receipt['collected'] and receipt['pipes_closed'] and receipt['reaped']
    assert receipt['log_bytes']==(tmp_path/receipt['log']).stat().st_size
    assert report.validate_receipt(receipt,10)['pid']==123
