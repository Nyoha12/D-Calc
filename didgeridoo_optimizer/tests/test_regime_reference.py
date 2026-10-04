import copy
import csv
from dataclasses import replace
import json
from pathlib import Path
import signal
import subprocess
import sys
from unittest.mock import patch
import numpy as np
import pytest

from didgeridoo_optimizer.pipeline import regime_reference as workflow
from didgeridoo_optimizer.reporting import regime_reference as report
from didgeridoo_optimizer.nonlinear.regime_observables import ObservationPlan
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters
from didgeridoo_optimizer.nonlinear.passive_resonator import digest
from didgeridoo_optimizer.tests.test_passive_resonator import modal

ROOT=Path(__file__).resolve().parents[2]
CONFIG=ROOT/'project_specs/examples/design_pitch/config.yaml'
DESIGN=ROOT/'project_specs/examples/design_pitch/cylinder.json'
P=DimensionedLipParameters()


def obs():return ObservationPlan(windows=((0.,.1),),scales=(.001,1.,1e-8,1e-6),section_index=0,section_level=0.)
def coupling(**kw):return SimultaneousCoupling(modal(),params=P,rho=1.204,**kw)


@pytest.mark.parametrize('stop_at',[0,1,3])
def test_stop_before_after_accepted_steps_and_restart(stop_at):
    c=coupling();rows=[]
    result=workflow.advance(c,10,seconds=10,stop=lambda:len(rows)>=stop_at,after_accept=rows.append)
    assert not result['ok'] and result['accepted_steps']==stop_at
    b=coupling();b.import_checkpoint(c.export_checkpoint())
    assert c.step()==b.step()


def test_timeout_and_native_refusal_keep_state(monkeypatch):
    c=coupling();before=c.export_checkpoint()
    result=workflow.advance(c,5,seconds=0)
    assert not result['ok'] and result['reason']=='timeout_before_step' and before==c.export_checkpoint()
    c=coupling(max_iterations=1);before=c.export_checkpoint()
    result=workflow.advance(c,5,seconds=10)
    assert not result['ok'] and result['accepted_steps']==0 and before==c.export_checkpoint()
    c=coupling();ticks=iter([0.,0.,2.])
    monkeypatch.setattr(workflow.time,'monotonic',lambda:next(ticks))
    result=workflow.advance(c,1,seconds=1)
    assert result['accepted_steps']==1 and result['reason']=='timeout_after_accepted_step'


@pytest.mark.parametrize('options',[dict(target_steps=72001),dict(target_steps=6001,sample_rate_hz=1000),dict(target_steps=True),dict(seconds=180),dict(checkpoint_steps=0)])
def test_budget_refused_before_native_preflight(tmp_path,options):
    kwargs=dict(model_in='absent',params=P,rho=1.204,observation=obs(),target_steps=12);kwargs.update(options)
    with patch.object(workflow.td,'preflight',side_effect=AssertionError('must not reach native preflight')):
        with pytest.raises(ValueError):workflow.preflight(CONFIG,DESIGN,tmp_path/'out',**kwargs)
    assert not (tmp_path/'out').exists()


def test_synthetic_model_cannot_claim_real_recipe_or_run_cli(tmp_path):
    model=modal();path=tmp_path/'model.json';model.save(path)
    with patch.object(workflow.td,'calculate',side_effect=AssertionError('acoustics')):
        with pytest.raises(ValueError):workflow.run(CONFIG,DESIGN,tmp_path/'out',dry_run=True,model_in=path,params=P,rho=1.204,observation=obs(),target_steps=12)
    assert not (tmp_path/'out').exists()
    lips=tmp_path/'lips.json';lips.write_text(json.dumps(P.as_dict()))
    observation=tmp_path/'obs.json';observation.write_text(json.dumps(obs().as_dict()))
    proc=subprocess.run([sys.executable,'-B','-m','tools.regime_reference','--config',str(CONFIG),'--design',str(DESIGN),'--output',str(tmp_path/'cli'),'--model-in',str(path),'--lip-parameters',str(lips),'--rho','1.204','--observation-plan',str(observation),'--target-steps','12','--dry-run'],capture_output=True,text=True,timeout=20)
    assert proc.returncode==2 and not json.loads(proc.stdout)['ok'] and not (tmp_path/'cli').exists()


def test_path_links_existing_outputs_and_unexpected_files(tmp_path):
    dangling=tmp_path/'dangling';dangling.symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError):report.safe_path(dangling,exists=False)
    output=tmp_path/'out';output.mkdir();report.write_json(output/'x.json',{'x':None})
    with pytest.raises(FileExistsError):report.write_json(output/'x.json',{'x':1})
    nested=tmp_path/'link';nested.symlink_to(output,target_is_directory=True)
    with pytest.raises(ValueError):report.write_json(nested/'new.json',{})
    with pytest.raises(ValueError):report.verify_chain(output/'x.json')


def record(c,sequence=0,previous=None,accepted=0):
    return dict(schema='dcalc.regime_checkpoint.v1',sequence=sequence,previous_sha256=previous,parent=None,
        chain_id='test-chain',compatibility={'model':'test'},producer_sources={'test':'abc'},
        origin_time_s=0.,accepted_steps=accepted,checkpoint=c.export_checkpoint(),plan_identity=digest({'model':'test'}),series=None)


def test_atomic_checkpoint_serialization_interruption_keeps_previous(tmp_path,monkeypatch):
    c=coupling();first=report.checkpoint(tmp_path,record(c));assert c.step()['ok']
    native=report.os.link
    def interrupt(*a,**kw):raise KeyboardInterrupt()
    monkeypatch.setattr(report.os,'link',interrupt)
    with pytest.raises(KeyboardInterrupt):report.checkpoint(tmp_path,record(c,1,first,1))
    assert not (tmp_path/'checkpoint-000001.json').exists()
    assert report.verify_chain(tmp_path/'checkpoint-000000.json')['sha256']==first
    monkeypatch.setattr(report.os,'link',native)
    report.checkpoint(tmp_path,record(c,1,first,1))
    assert report.verify_chain(tmp_path/'checkpoint-000001.json')['payload']['accepted_steps']==1


def test_chain_tampering_and_unexpected_file(tmp_path):
    c=coupling();report.checkpoint(tmp_path,record(c))
    path=tmp_path/'checkpoint-000000.json';value=report.read_json(path)
    value['payload']['accepted_steps']=1;path.write_text(json.dumps(value))
    with pytest.raises(ValueError):report.verify_chain(path)
    path.write_text(json.dumps(dict(payload=record(c),sha256=digest(record(c)))))
    (tmp_path/'unexpected').write_text('x')
    with pytest.raises(ValueError):report.verify_chain(path)


def test_exports_null_identity_groups_and_npz_no_pickle(tmp_path):
    value=dict(status='partial',ok=False,reason='timeout',identity='abc',observations=dict(fundamental_hz=None,orbital_stability=None,groups=[{'group':2,'status':'not_established'}],windows=[dict(candidates=[dict(group=2,reason='shape',frequency=None)])]))
    report.export(tmp_path,value)
    assert report.read_json(tmp_path/'result.json')==value
    with (tmp_path/'summary.csv').open() as f:assert json.loads(next(csv.DictReader(f))['record_json'])==value
    assert 'not_established' in (tmp_path/'summary.md').read_text()
    report.write_npz(tmp_path/'samples-000001.npz',dict(states=np.zeros((3,4))))
    with np.load(tmp_path/'samples-000001.npz',allow_pickle=False) as archive:assert archive['states'].shape==(3,4)
    with pytest.raises(ValueError):report.write_json(tmp_path/'bad.json',{'x':np.nan})
    with pytest.raises(ValueError):report.write_npz(tmp_path/'bad.npz',dict(x=np.array([{}],object)))


@pytest.mark.parametrize('mode',['jet-only','conjugate'])
def test_fractional_native_energy_uses_actual_parameters(mode):
    params=replace(P,mass_kg=2e-4,resonance_hz=90.)
    m=modal();c=SimultaneousCoupling(m,params=params,rho=1.204,v2_port_model=mode)
    times=[c.time_s];states=[c.state];rows=[]
    for _ in range(30):
        d=c.step();assert d['ok'];rows.append([d[k] for k in workflow.COLUMNS]);times.append(c.time_s);states.append(c.state)
    out=report.energy_intervals(np.array(times),np.array(states),np.array(rows),list(workflow.COLUMNS),[times[3]+c.dt*.3,times[20]+c.dt*.6],params=params,model=m,port_model=mode,start=0.,end=times[-1])
    assert out['nonconjugate_mechanical_term_included']==(mode=='jet-only')
    assert [r['kind'] for r in out['intervals']]==['initial_fraction','return','terminal_fraction']
    assert max(abs(r['balance_defect_j']) for r in out['intervals'])<1e-15


@pytest.mark.parametrize('sig',['SIGINT','SIGTERM'])
@pytest.mark.parametrize('stage',['before','after','serialization'])
def test_real_signals_are_flags_and_keep_complete_native_checkpoint(tmp_path,sig,stage):
    script="""
import json,os,pathlib,signal,sys
from didgeridoo_optimizer.tests.test_regime_reference import coupling,record
from didgeridoo_optimizer.pipeline.regime_reference import advance
from didgeridoo_optimizer.reporting import regime_reference as r
out=pathlib.Path(sys.argv[1]);sig=getattr(signal,sys.argv[2]);stage=sys.argv[3];flag=[False]
def handler(s,f):flag[0]=True
signal.signal(sig,handler)
c=coupling();old=r.checkpoint(out,record(c));rows=[]
if stage=='before':os.kill(os.getpid(),sig)
def collect(row):
    rows.append(row)
    if stage=='after':os.kill(os.getpid(),sig)
result=advance(c,1,seconds=10,stop=lambda:flag[0],after_accept=collect)
if rows:
    if stage=='serialization':
        original=r.os.link
        def link(*a,**kw):
            os.kill(os.getpid(),sig)
            return original(*a,**kw)
        r.os.link=link
    r.checkpoint(out,record(c,1,old,len(rows)))
    restored=coupling();restored.import_checkpoint(r.verify_chain(out/'checkpoint-000001.json')['payload']['checkpoint'])
    assert restored.snapshot()==c.snapshot()
print(json.dumps(dict(flag=flag[0],accepted=len(rows),snapshot=c.snapshot())))
sys.exit(2 if flag[0] else 0)
"""
    process=subprocess.run([sys.executable,'-B','-c',script,str(tmp_path),sig,stage],capture_output=True,text=True,timeout=20)
    assert process.returncode==2,process.stderr
    value=json.loads(process.stdout);assert value['flag'] and value['accepted']==(0 if stage=='before' else 1)
    tip=tmp_path/f"checkpoint-{value['accepted']:06d}.json"
    assert report.verify_chain(tip)['payload']['checkpoint']['payload']['snapshot']==value['snapshot']


def test_chain_parent_cannot_be_forged_or_missing(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir();c=coupling()
    zero=report.checkpoint(a,record(c));assert c.step()['ok']
    final=report.checkpoint(a,record(c,1,zero,1))
    saved=record(c,accepted=1);saved['parent']=dict(path=str(a/'checkpoint-000001.json'),sha256=final,producer_sources={'test':'abc'})
    report.checkpoint(b,saved)
    assert report.verify_chain(b/'checkpoint-000000.json')['payload']['accepted_steps']==1
    bad=copy.deepcopy(saved);bad['parent']['sha256']='false'
    path=b/'checkpoint-000000.json';path.write_text(json.dumps(dict(payload=bad,sha256=digest(bad))))
    with pytest.raises(ValueError):report.verify_chain(path)


def test_worker_plan_cannot_bypass_budgets_before_allocation():
    with pytest.raises(ValueError):workflow.RunPlan(json.dumps(dict(target_steps=10**20))).as_dict()
    with pytest.raises(ValueError):workflow.RunPlan(' '* (report.MAX_JSON_BYTES+1)).as_dict()


def test_hostile_npy_header_refused_before_numpy_allocation(tmp_path,monkeypatch):
    import io,zipfile
    file=tmp_path/'samples-000001.npz'
    with zipfile.ZipFile(file,'w') as archive:
        for key,shape in [('time_s',(2,)),('states',(10**12,4)),('data',(1,len(workflow.COLUMNS)))]:
            buf=io.BytesIO();np.lib.format.write_array_header_1_0(buf,dict(descr='<f8',fortran_order=False,shape=shape))
            if key!='states':buf.write(bytes(8*np.prod(shape)))
            archive.writestr(key+'.npy',buf.getvalue())
    entry=dict(name=file.name,columns=list(workflow.COLUMNS),start_steps=0,end_steps=1,sha256='unused')
    monkeypatch.setattr(workflow,'_history',lambda _: [(tmp_path,dict(series=entry))])
    monkeypatch.setattr(np,'load',lambda *a,**kw:pytest.fail('header must be checked before np.load'))
    with pytest.raises(ValueError):workflow._load_history(tmp_path,np.zeros((2,4)),np.zeros(2),np.zeros((1,len(workflow.COLUMNS))),workflow.COLUMNS)


def test_output_budget_accounts_for_ancestors_before_publication(tmp_path):
    with pytest.raises(ValueError):report.write_json(tmp_path/'result.json',{'x':'too big'},budget_bytes=2)
    assert not (tmp_path/'result.json').exists()
