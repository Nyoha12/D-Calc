"""Self-contained CLI/provenance/closure contracts; fixture fits are metadata stubs.

The stub below tests historical identity routing, not acoustic fit certification.
Scientific acceptance uses the independent modal tests, never this fabricated history.
"""
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import yaml

from didgeridoo_optimizer.pipeline import paired_onset as workflow
from didgeridoo_optimizer.reporting import paired_onset as report
from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator, digest
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters
from didgeridoo_optimizer.tests.test_paired_onset import modal

ROOT=Path(__file__).resolve().parents[2]
CONFIG=ROOT/'project_specs/examples/design_pitch/config.yaml'
DESIGN=ROOT/'project_specs/examples/design_pitch/cylinder.json'
ENV={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}


def request(model):
    recipe=dict(sample_rate_hz=12000,h_cm=.5,loss_model='zk',radiation_model='legacy',
        air_reference='ck_dry20',R0=0.,dc_origin='Synthetic historical metadata stub, NOT fitted or measured',
        basis_completion='observed-only',fit_min_hz=40.,fit_max_hz=3000.,fit_points=32,
        guard_max_hz=3500.,guard_points=32,audit_points=32)
    return dict(schema=workflow.SCHEMA,cases=[dict(id='a',config=str(CONFIG),design=str(DESIGN),model_in=str(model),recipe=recipe)],
        reference_lips=DimensionedLipParameters().as_dict(),scenarios=[dict(id='central',changes={})],
        rho_kg_m3=1.204,source='ideal',port_model='conjugate',pressure_grid=dict(fractions=[.02,.08,.16,.28,.42,.58,.74,.90,.98]),
        windows=[dict(id='modal',pressure_pa=[3000,5000],frequency_hz=[20,150])],criteria=[],
        budgets=dict(refinements=23,evaluations_per_unit=64,total_evaluations=128,child_seconds=20,
                     orchestrator_seconds=60,memory_mib=768,blas_threads=1,output_mib=20),tmm=None)


@pytest.fixture
def saved_plan(tmp_path):
    path=tmp_path/'model.json';raw=request(path);case=raw['cases'][0]
    base,context=workflow.td.preflight(CONFIG,DESIGN,tmp_path/'not-created',**case['recipe'])
    m=modal();mp=m.parameters();mp['domain']='discrete_prewarped'
    mp['dc_origin']=dict(kind='explicit',description=case['recipe']['dc_origin'])
    inputs=workflow.td._inputs(context)
    identity=digest(dict(inputs=inputs,effective=base['effective'],h_cm=.5,fs=12000,R0=0.,
                         dc=mp['dc_origin'],basis_completion=base['basis_completion']))
    mp['quality']=dict(status='converged',candidate_inventory={k:mp[k] for k in ('a','gamma','omega')},
        fit_frequency_hz=np.linspace(40,3000,32).tolist(),fit_spectrum_sha256='a'*64,guard_spectrum_sha256='b'*64,
        certificate=dict(converged=True,candidate_count=1,basis_completion=base['basis_completion']),
        basis_completion=base['basis_completion'])
    mp['provenance']=dict(inputs=inputs,context_identity=identity,basis_completion=base['basis_completion'],
                         fit_spectrum_sha256='a'*64,guard_spectrum_sha256='b'*64,sources_sha256={})
    PassiveResonator.from_parameters(mp).save(path)
    plan=tmp_path/'plan.json';plan.write_text(json.dumps(raw))
    return plan,raw


def cli(plan,out,*args):
    return subprocess.run([sys.executable,'-B','-m','tools.paired_onset_reference','--plan',str(plan),
        '--output-dir',str(out),*args],env=ENV,cwd=ROOT,text=True,capture_output=True,timeout=40)


def test_dryrun_no_eigensolve_fit_step_tmm_write_and_immutable(saved_plan,tmp_path,monkeypatch):
    path,raw=saved_plan;before={p:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    def refused(*a,**kw): pytest.fail('Scientific execution or write during dry-run')
    monkeypatch.setattr(workflow.np.linalg,'eigvals',refused)
    monkeypatch.setattr(workflow.np,'roots',refused)
    monkeypatch.setattr(workflow,'input_impedance',refused)
    monkeypatch.setattr(PassiveResonator,'load',refused)
    monkeypatch.setattr(workflow.td,'_reload_certificate',refused)
    monkeypatch.setattr(report,'write_json',refused)
    plan=workflow.preflight(path,tmp_path/'out');p=plan.as_dict();p['request']['rho_kg_m3']=2
    assert plan.as_dict()['request']['rho_kg_m3']==1.204
    assert before=={p:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('bad',[True,float('nan'),float('inf'),-1.])
def test_invalid_numbers_before_context(tmp_path,bad):
    raw=request('absent');raw['rho_kg_m3']=bad
    path=tmp_path/'plan.json';path.write_text(json.dumps(raw))
    with patch.object(workflow,'_case_context',side_effect=AssertionError('too late')):
        with pytest.raises(ValueError):workflow.preflight(path,tmp_path/'out')


@pytest.mark.parametrize('kind',['unknown','duplicate_id','too_many_cases','too_many_scenarios','grid','budget','incomplete_lips','unknown_scenario','unsupported_port'])
def test_strict_plan_contract_before_allocation(kind,tmp_path):
    raw=request('absent')
    if kind=='unknown':raw['unexpected']=0
    if kind=='duplicate_id':raw['cases']*=2
    if kind=='too_many_cases':raw['cases']*=5
    if kind=='too_many_scenarios':raw['scenarios']*=17
    if kind=='grid':raw['pressure_grid']['fractions']=[.1]*34
    if kind=='budget':raw['budgets']['refinements']=33
    if kind=='incomplete_lips':del raw['reference_lips']['mass_kg']
    if kind=='unknown_scenario':raw['criteria']=[dict(id='x',role='hard',case='a',scenario='absent',window='modal',observable='played_frequency')]
    if kind=='unsupported_port':raw['port_model']='jet-only'
    with patch.object(workflow.np,'zeros',side_effect=AssertionError('allocation')):
        with pytest.raises(ValueError):workflow.validate_request(raw)


@pytest.mark.parametrize('text,suffix',[('{"schema":1,"schema":2}', '.json'),('schema: a\nschema: b\n','.yaml'),
                                       ('x: &x [*x]\n','.yaml'),('{"x":1e400}', '.json')])
def test_duplicates_cycles_nonfinite(tmp_path,text,suffix):
    p=tmp_path/('bad'+suffix);p.write_text(text)
    with pytest.raises(ValueError):report.read_plan_source(p)


def test_relative_yaml_paths_same_bytes_and_symlinks(saved_plan,tmp_path):
    path,raw=saved_plan;raw['cases'][0]['model_in']='model.json'
    y=tmp_path/'relative.yaml';y.write_text(yaml.safe_dump(raw))
    p=workflow.preflight(y,tmp_path/'out').as_dict()
    assert p['request_source']['sha256']==hashlib.sha256(y.read_bytes()).hexdigest()
    assert p['request']['cases'][0]['model_in']==str(tmp_path/'model.json')
    link=tmp_path/'link.yaml';link.symlink_to(y)
    with pytest.raises(ValueError):workflow.preflight(link,tmp_path/'out')
    link=tmp_path/'model-link.json';link.symlink_to(tmp_path/'model.json');raw['cases'][0]['model_in']=str(link)
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):workflow.preflight(path,tmp_path/'out')
    d=tmp_path/'dangling';d.symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError):report.safe_path(d,exists=False)


@pytest.mark.parametrize('change',['design','fs','coefficients','inventory','grid','certificate','context','completion'])
def test_saved_model_mismatch_refused(saved_plan,tmp_path,change):
    plan,raw=saved_plan;mpath=tmp_path/'model.json';envelope=json.loads(mpath.read_text());m=envelope['parameters']
    if change=='design':m['provenance']['inputs']['design']['sha256']='0'*64
    if change=='fs':m['sample_rate_hz']=4000
    if change=='coefficients':m['a'][0]*=1.1
    if change=='inventory':m['quality']['candidate_inventory']['omega'][0]*=1.1
    if change=='grid':m['quality']['fit_frequency_hz'][3]+=.1
    if change=='certificate':m['quality']['certificate']['converged']=False
    if change=='context':m['provenance']['context_identity']='0'*64
    if change=='completion':m['quality']['basis_completion']['mode']='r29'
    envelope['sha256']=digest(m);mpath.write_text(json.dumps(envelope))
    with pytest.raises(ValueError):workflow.preflight(plan,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_plain_synthetic_cli_refused_but_core_api_allowed(tmp_path):
    model=tmp_path/'model.json';modal().save(model);raw=request(model)
    path=tmp_path/'plan.json';path.write_text(json.dumps(raw))
    result=cli(path,tmp_path/'out','--dry-run')
    assert result.returncode==2 and not (tmp_path/'out').exists()


def test_worker_rechecks_plan_hash_and_source(saved_plan,tmp_path):
    path,_=saved_plan;p=workflow.preflight(path,tmp_path/'out').as_dict()
    with patch.object(PassiveResonator,'load',side_effect=AssertionError('compute')):
        changed=copy.deepcopy(p);changed['producer']['loaded_sources_sha256'][report.NEW_SOURCES[0]]='0'*64
        changed['context_sha256']=digest({k:v for k,v in changed.items() if k!='context_sha256'})
        with pytest.raises(ValueError):workflow._check_plan(changed)
        path.write_text(path.read_text()+'\n')
        with pytest.raises(ValueError):workflow._check_plan(p)


def test_copied_cli_refused_before_plan_read(tmp_path):
    copied=tmp_path/'copied.py';shutil.copyfile(ROOT/'tools/paired_onset_reference.py',copied)
    result=subprocess.run([sys.executable,str(copied),'--plan','absent','--output-dir',str(tmp_path/'out')],
        env=dict(ENV,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=20)
    assert result.returncode==2 and 'copied' in result.stdout and not (tmp_path/'out').exists()


def test_real_cli_modified_scenario_exports_and_fresh_reader(saved_plan,tmp_path):
    path,raw=saved_plan;raw['scenarios'].append(dict(id='fl70',changes=dict(resonance_hz=70.)))
    path.write_text(json.dumps(raw));out=tmp_path/'out';proc=cli(path,out)
    assert proc.returncode==0,proc.stdout+proc.stderr
    script='from didgeridoo_optimizer.reporting.paired_onset import read_result; import json,sys; print(json.dumps(read_result(sys.argv[1])))'
    fresh=subprocess.run([sys.executable,'-c',script,str(out)],env=ENV,cwd=ROOT,capture_output=True,text=True,timeout=20)
    result=json.loads(fresh.stdout)
    assert result['ok'] and len(result['result']['units'])==2
    assert all(c['reaped'] and c['exit_code']==0 for c in result['execution']['children'])
    with (out/'scenarios.csv').open() as stream:scenarios=list(csv.DictReader(stream))
    assert [float(s['resonance_hz']) for s in scenarios]==[80.,70.]
    assert set(p.name for p in out.glob('*.csv'))=={'cases.csv','scenarios.csv','grid.csv','candidates.csv','sides.csv','differentials.csv','criteria.csv'}
    for unit in result['result']['units']:
        data=json.loads((out/unit['file']).read_text())
        assert data['reference_mouth_pressure_kpa']==1.5 and data['counters']['evaluations']>9
    assert not report.read_result(out,expected_context='0'*64)['ok']
    assert cli(path,out).returncode==2
    (out/'summary.txt').write_text('tampered')
    assert not report.read_result(out)['ok']


@pytest.mark.parametrize('failure',['export','prepare','terminal','restore'])
def test_preterminal_failure_cannot_leave_success(saved_plan,tmp_path,monkeypatch,failure):
    path,_=saved_plan;out=tmp_path/'out'
    def fail(*args,**kwargs): raise OSError('injected preterminal failure')
    if failure=='export':monkeypatch.setattr(report,'export',fail)
    if failure=='prepare':monkeypatch.setattr(report,'prepare_completion',fail)
    if failure=='terminal':monkeypatch.setattr(report,'publish_completion',fail)
    if failure=='restore':
        original=signal.signal;calls=[0]
        def signal_fn(sig,handler):
            calls[0]+=1
            if calls[0]==4: raise OSError('injected restoration failure')
            return original(sig,handler)
        monkeypatch.setattr(workflow.signal,'signal',signal_fn)
    with pytest.raises(OSError):workflow.run(path,out)
    assert not (out/'execution.completed.json').exists()
    assert not report.read_result(out)['ok']
    assert list(out.glob('checkpoint-*.json'))


def test_interruption_timeout_keeps_acquired_checkpoints(saved_plan,tmp_path):
    path,raw=saved_plan;raw['budgets']['evaluations_per_unit']=3
    path.write_text(json.dumps(raw));out=tmp_path/'out';proc=cli(path,out)
    assert proc.returncode==1
    read=report.read_result(out)
    assert not read['ok'] and read['status']=='partial'
    checkpoints=list(out.glob('checkpoint-*.json'));assert checkpoints
    payloads=[json.loads(p.read_text())['payload'] for p in checkpoints]
    assert max(x['counters']['evaluations'] for x in payloads)==3
    assert json.loads((out/'unit-00-00.json').read_text())['status']=='partial'


def test_notes_cents_units_and_no_hidden_compensation(saved_plan):
    _,raw=saved_plan
    raw['criteria']=[dict(id='f',case='a',scenario='central',window='modal',role='hard',observable='onset_frequency',
                         target=dict(note='C2',reference_hz=440.,cents=3.),tolerance=dict(value=10.,unit='cent'))]
    assert workflow.validate_request(raw)==raw
    raw['criteria'][0]['tolerance']['unit']='mm'
    with pytest.raises(ValueError):workflow.validate_request(raw)


def test_loaded_source_guard_before_plan_read(saved_plan,tmp_path,monkeypatch):
    path,_=saved_plan
    monkeypatch.setitem(workflow.LOADED_SOURCES,report.NEW_SOURCES[0],'0'*64)
    with pytest.raises(ValueError,match='sources'):workflow.preflight(path,tmp_path/'out')


def test_orchestrator_deadline_covers_preflight(saved_plan,tmp_path,monkeypatch):
    import time
    path,raw=saved_plan;raw['budgets']['orchestrator_seconds']=.05;path.write_text(json.dumps(raw))
    monkeypatch.setattr(workflow,'preflight',lambda *a:time.sleep(.2))
    with pytest.raises(workflow.core.BudgetExhausted,match='orchestrator'):workflow.run(path,tmp_path/'out')
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('sig',[signal.SIGINT,signal.SIGTERM])
def test_real_interruption_reaps_owned_child_and_keeps_partial(saved_plan,tmp_path,sig):
    import time
    path,raw=saved_plan
    raw['scenarios']=[dict(id=f's{i}',changes={}) for i in range(8)]
    path.write_text(json.dumps(raw));out=tmp_path/'interrupted'
    command=[sys.executable,'-B','-m','tools.paired_onset_reference','--plan',str(path),'--output-dir',str(out)]
    proc=subprocess.Popen(command,env=ENV,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+15
        while not list(out.glob('checkpoint-*.json')) and proc.poll() is None and time.monotonic()<deadline:time.sleep(.01)
        assert list(out.glob('checkpoint-*.json'))
        proc.send_signal(sig)
        stdout,stderr=proc.communicate(timeout=15)
        assert proc.returncode!=0,stdout+stderr
        assert not report.read_result(out)['ok']
        if (out/'execution.json').exists():
            execution=json.loads((out/'execution.json').read_text())
            assert all(c['reaped'] for c in execution['children']) and not execution['ok']
    finally:
        if proc.poll() is None:proc.terminate();proc.wait(timeout=5)


def test_example_is_strict_and_has_only_missing_user_models():
    raw,_=report.read_plan_source(ROOT/'project_specs/examples/paired_onset/plan.yaml')
    workflow.validate_request(raw)
    parent=ROOT/'project_specs/examples/paired_onset'
    for case in raw['cases']:
        assert (parent/case['config']).is_file() and (parent/case['design']).is_file()
