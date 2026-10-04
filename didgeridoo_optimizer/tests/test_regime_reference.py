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


# R36 review corrections: plumbing witnesses use an already-finished fake child,
# with actual signals to this process. They do not claim a scientific child run.
def _finished_run(tmp_path, monkeypatch, hook=lambda stage:None):
    from types import SimpleNamespace
    class Input:
        def write(self, text):pass
        def close(self):pass
    class Finished:
        pid=99999999
        returncode=0
        stdin=Input()
        def poll(self):return 0
        def communicate(self, timeout=None):
            hook('communicate')
            return json.dumps(dict(ok=True,status='completed',reason=None)),''
    p=dict(output=str(tmp_path/'run'),seconds=10,budgets={'inherited_result_bytes':0},
           compatibility=dict(parameters=P.as_dict(),observation=obs().as_dict()))
    plan=SimpleNamespace(as_dict=lambda:p,document=json.dumps(p))
    monkeypatch.setattr(workflow,'preflight',lambda *a,**kw:(plan,None))
    monkeypatch.setattr(workflow,'execution_ready',lambda:None)
    monkeypatch.setattr(workflow.subprocess,'Popen',lambda *a,**kw:Finished())
    return lambda:workflow.run('unused','unused',p['output']),Path(p['output'])


@pytest.mark.parametrize('sig',[signal.SIGINT,signal.SIGTERM])
@pytest.mark.parametrize('stage',['communicate','before_write','publication','after_write','fsync'])
def test_c1_late_real_signal_overrides_finished_fake_child(tmp_path,monkeypatch,sig,stage):
    import os
    fired=[];active=[False]
    originals={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
    def hook(where):
        if where==stage and not fired:
            fired.append(where);os.kill(os.getpid(),sig)
    run,out=_finished_run(tmp_path,monkeypatch,hook)
    native_write=report.write_json;native_link=report.os.link;native_fsync=report.os.fsync
    def write(path,value,**kw):
        if Path(path).name=='execution.json':
            hook('before_write');active[0]=True
        try:return native_write(path,value,**kw)
        finally:
            if Path(path).name=='execution.json':active[0]=False;hook('after_write')
    def link(src,dst,**kw):
        if Path(dst).name=='execution.json':hook('publication')
        return native_link(src,dst,**kw)
    def fsync(fd):
        if active[0]:hook('fsync')
        return native_fsync(fd)
    monkeypatch.setattr(report,'write_json',write)
    monkeypatch.setattr(report.os,'link',link)
    monkeypatch.setattr(report.os,'fsync',fsync)
    value=run()
    assert fired and not value['ok'] and value['reason']=='signal'
    assert report.read_execution(out)==value
    assert value['child']['reaped'] and value['child']['exit_code']==0
    assert {s:signal.getsignal(s) for s in originals}==originals


def test_c1_missing_or_failed_receipt_never_success(tmp_path,monkeypatch):
    run,out=_finished_run(tmp_path,monkeypatch)
    native=report.write_json
    def fail(path,value,**kw):
        if Path(path).name=='execution.json':raise OSError('receipt unavailable')
        return native(path,value,**kw)
    monkeypatch.setattr(report,'write_json',fail)
    with pytest.raises(OSError):run()
    assert not report.read_execution(out)['ok']


def test_c2_typed_exports_keep_exact_records_and_short_synthesis(tmp_path):
    candidate=dict(group=2,status='unresolved',reason='observation_insufficiently_resolved',returns=12,
        return_frequency_hz=33.2,shape_scaled_span=.02,checks={'shape_scaled_span':False},failed_checks=['shape_scaled_span'])
    window=dict(start_s=0.,end_s=.5,section_index=0,section_level=-.0006,status='unresolved',reason='all_fixed_candidates_retained',
        pressure_ac_rms_pa=700.,fft_auxiliary_hz=None,passage_frequency_hz=66.4,passages=33,candidates=[candidate])
    value=dict(status='completed',ok=True,reason=None,chain_id='chain',plan_identity='plan',elapsed_seconds=1.25,
        accepted_chain_steps=6000,target_steps=6000,observations=dict(status='unresolved',windows=[window],
        groups=[dict(group=2,status='not_established',reason='one_or_more_windows_or_persistence_checks_fail')]))
    report.export(tmp_path,value)
    def row(name):
        with (tmp_path/name).open() as f:return next(csv.DictReader(f))
    s=row('summary.csv');w=row('windows.csv');r=row('returns.csv')
    assert s['status']=='completed' and s['reason']=='' and s['accepted_chain_steps']=='6000'
    assert s['plan_identity']=='plan' and s['elapsed_seconds']=='1.25'
    assert w['fft_auxiliary_hz']=='' and w['pressure_ac_rms_pa']=='700.0' and w['section_index']=='0'
    assert r['group']=='2' and r['return_frequency_hz']=='33.2' and r['shape_scaled_span']=='0.02'
    assert json.loads(r['checks_json'])==candidate['checks']
    assert json.loads(s['record_json'])==value and json.loads(w['record_json'])==window
    assert json.loads(r['record_json'])==dict(window=0,**candidate)
    text=(tmp_path/'summary.md').read_text()
    assert 'not_established' in text and 'DONNÉES' in text and 'COMMANDE' in text
    assert 'read_execution' in text and '[Fenêtres](windows.csv)' in text
    assert 'observation_insufficiently_resolved' in text and '33.2' in text
    assert '"candidates"' not in text and len(text)<5000


@pytest.mark.parametrize('worker',[False,True])
def test_c3_copied_cli_refused_before_worker_or_parse(tmp_path,monkeypatch,capsys,worker):
    import importlib.util
    target=tmp_path/'copied.py';target.write_bytes((ROOT/'tools/regime_reference.py').read_bytes())
    spec=importlib.util.spec_from_file_location('copied_regime_cli',target)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    monkeypatch.setattr(workflow,'worker',lambda:pytest.fail('copied worker reached'))
    assert cli.main(['--worker'] if worker else [])==2
    assert json.loads(capsys.readouterr().out)['status']=='refused'
    assert sorted(p.name for p in tmp_path.iterdir())==['copied.py']


def test_c3_same_bytes_hash_parse_and_inline_identity(tmp_path):
    import hashlib
    path=tmp_path/'lips.json';raw=json.dumps(P.as_dict(),indent=3).encode();path.write_bytes(raw)
    fields,source=report.read_json_source(path)
    assert fields==P.as_dict() and source['sha256']==hashlib.sha256(raw).hexdigest()
    assert source['path']==str(path) and source['requested']==fields and source['format']=='json'
    sources=workflow.request_provenance(P,obs(),{'parameters':source,'observation':{'mode':'inline','requested':obs().as_dict()}})
    assert sources['parameters']['effective']==P.as_dict()
    inline=workflow.request_provenance(P,obs())
    assert inline['parameters']['mode']=='inline' and 'path' not in inline['parameters']
    path.write_text(json.dumps(replace(P,mouth_pressure_kpa=7.).as_dict()))
    with pytest.raises(ValueError):workflow.request_provenance(P,obs(),sources)


@pytest.mark.parametrize('kind',['duplicate','nan','overflow','directory','link','budget'])
def test_c3_source_reader_strict(tmp_path,kind):
    path=tmp_path/'source.json'
    if kind=='directory':path.mkdir()
    elif kind=='link':path.symlink_to(tmp_path/'absent')
    else:path.write_text({'duplicate':'{"a":1,"a":2}','nan':'{"a":NaN}','overflow':'{"a":1e400}','budget':' '* (report.MAX_JSON_BYTES+1)}[kind])
    with pytest.raises(ValueError):report.read_json_source(path)


@pytest.mark.parametrize('mutation',['values','hash','effective','inline_file','extra','missing'])
def test_c3_incoherent_worker_file_provenance_rejected(tmp_path,mutation):
    path=tmp_path/'lips.json';path.write_text(json.dumps(P.as_dict()))
    _,source=report.read_json_source(path)
    sources=workflow.request_provenance(P,obs(),{'parameters':source,'observation':{'mode':'inline','requested':obs().as_dict()}})
    if mutation=='values':sources['parameters']['requested']['mouth_pressure_kpa']=7.
    if mutation=='hash':sources['parameters']['sha256']='0'*64
    if mutation=='effective':sources['parameters']['effective']['mouth_pressure_kpa']=7.
    if mutation=='inline_file':sources['parameters']['mode']='inline'
    if mutation=='extra':sources['parameters']['surprise']=1
    if mutation=='missing':sources.pop('parameters')
    with pytest.raises(ValueError):workflow.request_provenance(P,obs(),sources)


@pytest.mark.parametrize('stage',['pending_snapshot','after_boundary'])
@pytest.mark.parametrize('sig',[signal.SIGINT,signal.SIGTERM])
def test_c1_explicit_linearization_point(tmp_path,monkeypatch,stage,sig):
    import os
    run,out=_finished_run(tmp_path,monkeypatch)
    native=workflow.signal.sigpending
    def snapshot():
        if stage=='pending_snapshot':os.kill(os.getpid(),sig)
        pending=native()
        if stage=='after_boundary':os.kill(os.getpid(),sig)
        return pending
    monkeypatch.setattr(workflow.signal,'sigpending',snapshot)
    value=run()
    assert value['ok']==(stage=='after_boundary')
    assert report.read_execution(out)==value
    assert (out/'execution.closed.json').is_file()


@pytest.mark.parametrize('failure',['execution.cancelled.json','execution.closed.json'])
def test_c1_failed_terminal_commit_never_accepts_candidate(tmp_path,monkeypatch,failure):
    import os
    run,out=_finished_run(tmp_path,monkeypatch)
    native=report.write_json
    def write(path,value,**kw):
        if Path(path).name==failure:raise OSError('durability unavailable')
        native(path,value,**kw)
        if Path(path).name=='execution.json':os.kill(os.getpid(),signal.SIGINT)
    monkeypatch.setattr(report,'write_json',write)
    with pytest.raises(OSError):run()
    assert not report.read_execution(out)['ok']
    assert report.read_json(out/'execution.json')['ok']  # candidate alone has no authority


def test_c1_worker_restores_handlers_on_refusal(monkeypatch,capsys):
    import io
    before={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
    monkeypatch.setattr(workflow.sys,'stdin',io.StringIO('{"duplicate":1,"duplicate":2}'))
    assert workflow.worker()==2
    assert not json.loads(capsys.readouterr().out)['ok']
    assert {s:signal.getsignal(s) for s in before}==before


def test_c3_hash_not_from_a_second_read(tmp_path,monkeypatch):
    import hashlib
    path=tmp_path/'source.json';raw=b'{"v":1}';path.write_bytes(raw)
    native=report.json.loads
    def parse(data,**kw):
        value=native(data,**kw);path.write_bytes(b'{"v":2}');return value
    monkeypatch.setattr(report.json,'loads',parse)
    value,source=report.read_json_source(path)
    assert value=={'v':1} and source['requested']==value
    assert source['sha256']==hashlib.sha256(raw).hexdigest()
    assert source['sha256']!=hashlib.sha256(path.read_bytes()).hexdigest()


def test_c3_change_since_cli_read_refused_before_native_preflight(tmp_path,monkeypatch):
    path=tmp_path/'lips.json';path.write_text(json.dumps(P.as_dict()))
    _,source=report.read_json_source(path)
    path.write_text(json.dumps(replace(P,mouth_pressure_kpa=7.).as_dict()))
    monkeypatch.setattr(workflow.td,'preflight',lambda *a,**kw:pytest.fail('changed source reached preflight'))
    with pytest.raises(ValueError):
        workflow.preflight(CONFIG,DESIGN,tmp_path/'out',model_in='unused',params=P,rho=1.204,
            observation=obs(),target_steps=12,request_sources=dict(parameters=source,
            observation=dict(mode='inline',requested=obs().as_dict())))
    assert not (tmp_path/'out').exists()


def test_c3_file_provenance_changed_during_receipt_is_cancelled(tmp_path,monkeypatch):
    run,out=_finished_run(tmp_path,monkeypatch)
    native=report.write_json;closed=[False]
    def check(p):
        if closed[0]:raise ValueError('request bytes changed')
    def write(path,value,**kw):
        native(path,value,**kw)
        if Path(path).name=='execution.json':closed[0]=True
    monkeypatch.setattr(workflow,'_check_request',check)
    monkeypatch.setattr(report,'write_json',write)
    value=run()
    assert not value['ok'] and 'request_source_changed' in value['reason']
    assert report.read_execution(out)==value


def test_c1_error_after_seal_publication_is_durably_invalidated(tmp_path,monkeypatch):
    run,out=_finished_run(tmp_path,monkeypatch)
    native=report.write_json
    def write(path,value,**kw):
        native(path,value,**kw)
        if Path(path).name=='execution.closed.json':raise OSError('after publication')
    monkeypatch.setattr(report,'write_json',write)
    with pytest.raises(OSError):run()
    assert not report.read_execution(out)['ok']
    assert 'closure_failed' in report.read_execution(out)['reason']


def test_c3_historical_api_chain_without_plan_retains_unknown_provenance(tmp_path):
    c=coupling();report.checkpoint(tmp_path,record(c))
    assert workflow._request_ancestry(tmp_path/'checkpoint-000000.json')==[
        dict(plan_path=None,plan_sha256=None,request_sources=None)]


@pytest.mark.parametrize('bad',[None,[],True,{'ok':True}])
def test_c1_invalid_cancellation_is_never_success(tmp_path,monkeypatch,bad):
    run,out=_finished_run(tmp_path,monkeypatch);assert run()['ok']
    report.write_json(out/'execution.cancelled.json',bad)
    assert not report.read_execution(out)['ok']


@pytest.mark.parametrize('sig',[signal.SIGINT,signal.SIGTERM])
def test_c1_worker_signal_during_stdout_is_nonzero(tmp_path,monkeypatch,capsys,sig):
    import builtins,io,os
    monkeypatch.setattr(workflow.sys,'stdin',io.StringIO('{}'))
    monkeypatch.setattr(workflow,'calculate',lambda *a,**kw:dict(ok=True,status='completed',reason=None))
    original=builtins.print
    def printing(*a,**kw):original(*a,**kw);os.kill(os.getpid(),sig)
    monkeypatch.setattr(builtins,'print',printing)
    assert workflow.worker()==2
    assert json.loads(capsys.readouterr().out)['status']=='completed'  # data, child code is authoritative
