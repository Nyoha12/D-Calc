"""Actual bounded CLIs plus explicit synthetic orchestration fault injection."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from didgeridoo_optimizer.tests.test_played_search import job, change, ROOT, EXAMPLE
from didgeridoo_optimizer.tests.test_register_target_cli import plan
from didgeridoo_optimizer.tests.test_paired_onset_cli import saved_plan
from didgeridoo_optimizer.pipeline import played_search as workflow
from didgeridoo_optimizer.reporting import played_search as report
from didgeridoo_optimizer.nonlinear import register_target as native

ENV={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}


def cli(job,out,*args):
    return subprocess.run([sys.executable,'-B','-m','tools.played_search','--job',str(job),'--output-dir',str(out),*args],
        cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=35)


def screened(job):
    def static(r):
        c=r['criteria'][0];c.pop('bounds');c['target']=dict(value=118.,unit='cm')
    change(job.parent/'static_request.yaml',static)
    change(job,lambda r:r['budgets'].update(candidates=1))


def test_true_cli_static_child_fresh_reader_csv_and_fingerprints(job,tmp_path):
    screened(job);out=tmp_path/'out';completed=cli(job,out)
    assert completed.returncode==0,completed.stdout+completed.stderr
    response=json.loads(completed.stdout);assert not response['conforming']
    p=subprocess.run([sys.executable,'-B','-c',
        'import json,sys; from didgeridoo_optimizer.reporting.played_search import read_result; print(json.dumps(read_result(sys.argv[1])))',str(out)],
        cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=35)
    value=json.loads(p.stdout);assert p.returncode==0 and value['ok'],p.stdout+p.stderr
    assert value['result']['counters']['fits']==0
    assert 'residual_si' in (out/'criteria.csv').read_text() and 'margin_unit' in (out/'criteria.csv').read_text()
    assert report.read_checkpoint(out)['solver_state_complete'] is False
    candidate=out/'candidate-001/candidate.json';row=json.loads(candidate.read_text());row['criteria'].pop(0);candidate.write_text(json.dumps(row))
    assert not report.read_result(out)['ok']


def test_cli_dry_and_copied_cli_and_existing_destination(job,tmp_path):
    out=tmp_path/'dry';p=cli(job,out,'--dry-run');assert p.returncode==0,p.stdout
    assert not out.exists()
    copied=tmp_path/'copied.py';shutil.copy(ROOT/'tools/played_search.py',copied)
    p=subprocess.run([sys.executable,str(copied),'--job',str(job),'--output-dir',str(out),'--dry-run'],
        cwd=ROOT,env=dict(ENV,PYTHONPATH=str(ROOT)),capture_output=True,text=True,timeout=20)
    assert p.returncode==2 and 'Copied' in p.stdout
    out.mkdir();p=cli(job,out,'--dry-run');assert p.returncode==2


def test_actual_native_register_subcommand_and_authoritative_reader(plan,tmp_path):
    # Native public metadata stub: exercises orchestration, NOT physical fit acceptance.
    path,r=plan;dest=tmp_path/'native';p=workflow.rt.preflight(path,dest).as_dict();dest.mkdir()
    workflow.rtreport.write_json(dest/'plan.json',p)
    limits=dict(candidates=1,scenarios=1,fits=1,trajectories=1,steps=96,seconds=90,
                memory_mib=768,output_mib=80,child_seconds=60)
    budget=workflow.Budget(limits,tmp_path);children=[]
    result,receipt=workflow.launch('trajectory',dict(plan=p),tmp_path,budget,children,seconds=40,output_bytes=40*workflow.MIB,steps=96)
    assert result['ok'] and receipt['exit_code']==0 and receipt['reaped'],result
    workflow.rtreport.prepare_completion(dest,dict(ok=True,status='complete',children=[receipt],reason=None),40*workflow.MIB)
    workflow.rtreport.publish_completion(dest)
    value,_=workflow.launch('read',dict(kind='read',output=str(dest)),tmp_path,budget,children,seconds=30,output_bytes=workflow.MIB)
    assert value['ok'] and value['accepted_steps']==96
    assert budget.counts['trajectories']==1 and budget.counts['steps_charged']==96


@pytest.mark.parametrize('point',['prepare','publish','restore'])
def test_late_failure_no_false_completion(job,tmp_path,monkeypatch,point):
    screened(job);out=tmp_path/point
    def fail(*a,**kw):raise OSError('injected late failure')
    if point=='prepare':monkeypatch.setattr(report,'prepare_completion',fail)
    elif point=='publish':monkeypatch.setattr(report,'publish_completion',fail)
    else:
        original=workflow.signal.signal
        def restore(sig,handler):
            if sig==workflow.signal.SIGALRM and handler==workflow.signal.SIG_DFL:fail()
            return original(sig,handler)
        monkeypatch.setattr(workflow.signal,'signal',restore)
    with pytest.raises(OSError):workflow.run(job,out)
    assert not (out/'execution.completed.json').exists() and not report.read_result(out)['ok']


def test_child_refusal_preserves_code_and_charged_budget(job,tmp_path):
    p=workflow.preflight(job);out=tmp_path/'child';out.mkdir();budget=workflow.Budget(p['job']['budgets'],out);children=[]
    value,receipt=workflow.launch('read',dict(kind='unknown'),out,budget,children,seconds=10,output_bytes=workflow.MIB)
    assert not value['ok'] and receipt['exit_code']==2 and receipt['reaped']
    assert budget.counts['children']==1


def test_preallocation_refuses_process(job,tmp_path,monkeypatch):
    p=workflow.preflight(job);out=tmp_path/'out';out.mkdir();limits=dict(p['job']['budgets'],seconds=1)
    budget=workflow.Budget(limits,out)
    monkeypatch.setattr(workflow.subprocess,'Popen',lambda *a,**kw:pytest.fail('Child launched without reserved time'))
    with pytest.raises(workflow.BudgetStop):workflow.launch('read',{},out,budget,[],seconds=2,output_bytes=1)
    assert not list(out.iterdir())


@pytest.mark.parametrize('trajectory_limit',[1,2])
def test_synthetic_exact_cache_no_double_fit(job,tmp_path,monkeypatch,trajectory_limit):
    """Explicit injected synthetic native responses, never a fit validation oracle."""
    change(job,lambda r:(r['scenarios'].append(dict(r['scenarios'][0],id='other')),
                         r['budgets'].update(candidates=1,trajectories=trajectory_limit)))
    _,p=workflow.load_inputs(job);out=tmp_path/'synthetic';out.mkdir();calls=[]
    monkeypatch.setattr(workflow,'read_fit',lambda *a,**kw:dict(model_sha256='synthetic-test-only'))
    original=workflow.rt.preflight
    def preflight(path,dest):
        value=json.loads(Path(path).read_text())
        data=dict(request=value,output=str(dest),context_sha256=workflow.digest(value))
        class Plan:
            def as_dict(self):return data
        return Plan()
    monkeypatch.setattr(workflow.rt,'preflight',preflight)
    def launch(kind,task,out,budget,children,*,seconds,output_bytes,steps=0):
        budget.reserve(kind,seconds,output_bytes,steps);budget.launched(kind,steps);calls.append(kind)
        receipt=dict(kind=kind,exit_code=0,reaped=True,log='synthetic',steps_charged=steps)
        children.append(receipt)
        if kind=='static':return workflow.static_unit(task),receipt
        if kind=='fit':
            Path(task['output'],'model.json').write_text('{"synthetic_test_only":true}')
            return dict(ok=True,status='numerically_accepted',options=task['options'],statuses=workflow.tdreport.statuses()),receipt
        if kind=='trajectory':
            request=task['plan']['request'];dest=Path(task['plan']['output'])
            windows={w['id']:dict(frequency_hz=70.,pressure_max_pa=3500.,activity_rms_pa=1000.,duration_s=.5) for w in request['windows']}
            value=native.evaluate_criteria(request,windows)
            report.write_json(dest/'result.json',dict(synthetic_test_only=True,criteria=value['criteria']))
            return dict(ok=True,status='complete',accepted_steps=steps),receipt
        result=report.read_json(Path(task['output'])/'result.json')
        return dict(ok=True,status='complete',criteria=result['criteria']),receipt
    monkeypatch.setattr(workflow,'launch',launch)
    b=workflow.Budget(p['job']['budgets'],out);result=workflow.execute(p,out,b,[])
    assert calls.count('fit')==1 and calls.count('trajectory')==trajectory_limit
    assert b.counts['fits']==1 and b.counts['trajectories']==trajectory_limit
    if trajectory_limit==2:
        assert result['conforming'] and len(result['search']['witness']['units'])==2
    else:
        assert result['status']=='partial' and not result['conforming']
        assert result['search']['history'][0]['stop_reason']=='total_trajectories'
        assert report.read_checkpoint(out)['counters']['trajectories']==1


def test_actual_timeout_only_owned_child_reaped(job,tmp_path,monkeypatch):
    p=workflow.preflight(job);out=tmp_path/'timeout';out.mkdir()
    budget=workflow.Budget(p['job']['budgets'],out);children=[];original=workflow.subprocess.Popen
    def sleeper(args,**kwargs):
        return original([sys.executable,'-c','import time; time.sleep(5)'],**kwargs)
    monkeypatch.setattr(workflow.subprocess,'Popen',sleeper)
    value,receipt=workflow.launch('read',{},out,budget,children,seconds=.1,output_bytes=1024)
    assert not value['ok'] and receipt['timeout_or_error']=='child_timeout'
    assert receipt['reaped'] and receipt['exit_code']!=0
    assert len(children)==1


def test_untracked_source_provenance_refused(job,tmp_path,monkeypatch):
    from types import SimpleNamespace
    foreign=tmp_path/'untracked.py';foreign.write_text('pass\n')
    monkeypatch.setitem(sys.modules,'didgeridoo_optimizer.fake_untracked',SimpleNamespace(__file__=str(foreign)))
    with pytest.raises(ValueError):workflow.preflight(job)


def test_late_signal_after_preparation_leaves_no_terminal(job,tmp_path,monkeypatch):
    screened(job);out=tmp_path/'late-signal';original=report.prepare_completion
    def interrupt(*args,**kwargs):
        original(*args,**kwargs)
        workflow.signal.raise_signal(workflow.signal.SIGTERM)
    monkeypatch.setattr(report,'prepare_completion',interrupt)
    result=workflow.run(job,out)
    assert result['ok'] is False and not (out/'execution.completed.json').exists()
    assert report.read_checkpoint(out) is not None


@pytest.mark.parametrize('mutation',['recipe','design','model'])
def test_real_saved_adapter_rejects_mixed_recipe_geometry_or_model(plan,tmp_path,mutation):
    path,r=plan;case={k:v for k,v in r['case'].items() if k!='kind'}
    if mutation=='recipe':case['recipe']=dict(case['recipe'],sample_rate_hz=11000)
    if mutation=='design':case['design']=str(EXAMPLE/'design.json')
    if mutation=='model':
        bad=tmp_path/'bad-model.json';value=json.loads(Path(case['model_in']).read_text())
        value['sha256']='0'*64;bad.write_text(json.dumps(value));case['model_in']=str(bad)
    with pytest.raises(ValueError):workflow._case_context(case,tmp_path/'unused')


def launch_budget(out, **limits):
    return workflow.Budget(dict(dict(candidates=1,scenarios=1,fits=1,trajectories=1,steps=100,
        seconds=30,memory_mib=768,output_mib=30,child_seconds=5),**limits),out)


@pytest.mark.parametrize('payload_size',[32,1024**2])
def test_launch_nonreading_input_is_supervised(tmp_path,monkeypatch,payload_size):
    original=workflow.subprocess.Popen; owned=[]
    def sleeper(args,**kw):
        child=original([sys.executable,'-B','-c','import time; time.sleep(2)'],**kw)
        owned.append(child);return child
    monkeypatch.setattr(workflow.subprocess,'Popen',sleeper)
    budget=launch_budget(tmp_path);children=[];start=time.monotonic()
    try:
        value,receipt=workflow.launch('static',dict(payload='x'*payload_size),tmp_path,
            budget,children,seconds=.15,output_bytes=1024)
        assert time.monotonic()-start<1
        assert not value['ok'] and receipt['timeout_or_error']=='child_timeout'
        assert receipt['reaped'] and receipt['exit_code']!=0 and children==[receipt]
        assert budget.reserved['children']==budget.counts['children']==1
        assert not list(tmp_path.glob('.worker-input-*'))
    finally:
        for child in owned:
            if child.poll() is None:child.kill()
            child.wait(timeout=3)


@pytest.mark.parametrize('payload_size',[32,1024**2])
def test_launch_file_input_roundtrip_and_owned_cleanup(tmp_path,monkeypatch,payload_size):
    original=workflow.subprocess.Popen;streams=[]
    foreign=tmp_path/'.worker-input-foreign';foreign.write_bytes(b'keep another call')
    def reader(args,**kw):
        streams.extend([kw['stdin'],kw['stdout']])
        assert Path(kw['stdin'].name).parent==tmp_path and kw['stdin'].tell()==0
        return original([sys.executable,'-B','-c',
            'import json,sys; r=json.load(sys.stdin); print(json.dumps(dict(ok=True, length=len(r["payload"]), extra=r["extra"])))'],**kw)
    monkeypatch.setattr(workflow.subprocess,'Popen',reader)
    task=dict(payload='x'*payload_size,extra=['é\n"\\😀',None,True,1.25,{2:'value'}])
    budget=launch_budget(tmp_path);children=[]
    value,receipt=workflow.launch('read',task,tmp_path,budget,children,seconds=2,output_bytes=1024)
    assert value==dict(ok=True,length=payload_size,extra=json.loads(json.dumps(task['extra'])))
    assert receipt['input_bytes']==len(json.dumps(task,allow_nan=False).encode())
    assert receipt['exit_code']==0 and receipt['reaped'] and receipt['timeout_or_error'] is None
    assert receipt['collection_seconds']<receipt['collection_limit_seconds']==4
    assert all(s.closed for s in streams)
    assert list(tmp_path.glob('.worker-input-*'))==[foreign] and foreign.read_bytes()==b'keep another call'
    assert not (tmp_path/'execution.completed.json').exists()


def test_launch_popen_failure_closes_files_and_preserves_reservation(tmp_path,monkeypatch):
    streams=[];budget=launch_budget(tmp_path);children=[]
    def fail(*args,**kw):
        streams.extend([kw['stdin'],kw['stdout']]);raise OSError('injected spawn refusal')
    monkeypatch.setattr(workflow.subprocess,'Popen',fail)
    with pytest.raises(OSError,match='injected spawn refusal'):
        workflow.launch('trajectory',{},tmp_path,budget,children,seconds=1,output_bytes=1024,steps=40)
    assert all(s.closed for s in streams) and len(streams)==2
    assert not list(tmp_path.glob('.worker-input-*'))
    assert budget.reserved['children']==1 and budget.reserved['steps_charged']==40
    assert budget.counts['children']==budget.counts['steps_charged']==0
    assert len(children)==1 and children[0]['pid'] is None and children[0]['reaped']
    assert not children[0]['launched'] and children[0]['exit_code'] is None
    assert 'spawn refusal' in children[0]['timeout_or_error']
    assert not (tmp_path/'execution.completed.json').exists()


@pytest.mark.parametrize('stop_kind',['flag','exception'])
def test_launch_interruption_while_waiting_closes_and_reaps(tmp_path,monkeypatch,stop_kind):
    from types import SimpleNamespace
    original=workflow.subprocess.Popen;owned=[];streams=[];budget=launch_budget(tmp_path);children=[]
    def sleeper(args,**kw):
        streams.extend([kw['stdin'],kw['stdout']])
        child=original([sys.executable,'-B','-c','import time; time.sleep(2)'],**kw)
        owned.append(child);return child
    monkeypatch.setattr(workflow.subprocess,'Popen',sleeper)
    original_sleep=time.sleep
    def interrupt(seconds):
        original_sleep(seconds)
        if stop_kind=='exception':raise KeyboardInterrupt('injected interrupt')
        budget.stop=lambda:True
    monkeypatch.setattr(workflow,'time',SimpleNamespace(monotonic=time.monotonic,sleep=interrupt))
    try:
        if stop_kind=='exception':
            with pytest.raises(KeyboardInterrupt,match='injected interrupt'):
                workflow.launch('static',dict(payload='x'*1024**2),tmp_path,budget,children,seconds=1,output_bytes=1024)
        else:
            value,receipt=workflow.launch('static',dict(payload='x'*1024**2),tmp_path,budget,children,seconds=1,output_bytes=1024)
            assert not value['ok'] and receipt['timeout_or_error']=='signal_interrupted'
        assert len(children)==1 and children[0]['reaped'] and children[0]['exit_code']!=0
        assert children[0]['timeout_or_error'] and all(s.closed for s in streams)
        assert not list(tmp_path.glob('.worker-input-*'))
        assert budget.counts['children']==budget.reserved['children']==1
        assert not (tmp_path/'execution.completed.json').exists()
    finally:
        for child in owned:
            if child.poll() is None:child.kill()
            child.wait(timeout=3)


@pytest.mark.parametrize('quota',['raw','escaped','storage'])
def test_launch_input_quota_before_tempfile_or_large_encoding(tmp_path,monkeypatch,quota):
    budget=launch_budget(tmp_path,output_mib=3 if quota=='storage' else 30)
    payload='x'*(4*workflow.MIB) if quota=='raw' else '\x00'*workflow.MIB if quota=='escaped' else 'x'*workflow.MIB
    original=workflow.json.dumps;encoded=[]
    def bounded(value,**kw):
        if isinstance(value,str):
            assert len(value)<=8192;encoded.append(len(value))
        return original(value,**kw)
    monkeypatch.setattr(workflow.json,'dumps',bounded)
    def forbidden(*a,**kw):pytest.fail('Allocation or process before input quota')
    monkeypatch.setattr(workflow.tempfile,'NamedTemporaryFile',forbidden)
    monkeypatch.setattr(workflow.subprocess,'Popen',forbidden)
    with pytest.raises(workflow.BudgetStop if quota=='storage' else ValueError,
                       match='remaining_storage' if quota=='storage' else 'Worker input quota'):
        workflow.launch('static',dict(payload=payload),tmp_path,budget,[],seconds=2,output_bytes=1024)
    assert encoded and not list(tmp_path.iterdir()) and budget.reserved['children']==0


def test_launch_reserves_actual_input_once(tmp_path,monkeypatch):
    task=dict(payload='x'*1024**2);size=len(json.dumps(task).encode());output_bytes=1024
    budget=launch_budget(tmp_path,output_mib=(size+output_bytes+2*workflow.MIB)/workflow.MIB)
    original=workflow.subprocess.Popen;reservations=[];reserve=budget.reserve
    def checked(kind,seconds,output_bytes,steps=0):
        reservations.append(output_bytes);assert not list(tmp_path.iterdir())
        return reserve(kind,seconds,output_bytes,steps)
    monkeypatch.setattr(budget,'reserve',checked)
    def reader(args,**kw):
        return original([sys.executable,'-B','-c','import sys; sys.stdin.read(); print("{\\"ok\\": true}")'],**kw)
    monkeypatch.setattr(workflow.subprocess,'Popen',reader)
    value,receipt=workflow.launch('read',task,tmp_path,budget,[],seconds=2,output_bytes=output_bytes)
    assert value['ok'] and reservations==[size+output_bytes] and receipt['input_bytes']==size


@pytest.mark.parametrize('phase',['count','prepare','spawn','completed'])
@pytest.mark.parametrize('deadline',['child','global'])
def test_launch_deadline_covers_preparation_and_late_success(tmp_path,monkeypatch,phase,deadline):
    from types import SimpleNamespace
    budget=launch_budget(tmp_path);children=[];original_clock=time.monotonic;offset=[0.]
    monkeypatch.setattr(workflow,'time',SimpleNamespace(monotonic=lambda:original_clock()+offset[0],sleep=time.sleep))
    def expire():offset[0]=31 if deadline=='global' else 3
    chunks=workflow._input_chunks;calls=[0];original=workflow.subprocess.Popen;owned=[];streams=[]
    def delayed_chunks(task,*args):
        if not args:
            calls[0]+=1
            if (phase=='count' and calls[0]==1) or (phase=='prepare' and calls[0]==2):expire()
        yield from chunks(task,*args)
    monkeypatch.setattr(workflow,'_input_chunks',delayed_chunks)
    def completed(args,**kw):
        streams.extend([kw['stdin'],kw['stdout']])
        child=original([sys.executable,'-B','-c','print("{\\"ok\\": true}")'],**kw)
        owned.append(child)
        if phase=='spawn':child.wait(timeout=2);expire()
        return child
    monkeypatch.setattr(workflow.subprocess,'Popen',completed)
    if phase=='completed':
        def late_wait(seconds):
            owned[0].wait(timeout=2);expire()
        monkeypatch.setattr(workflow.time,'sleep',late_wait)
    reason='total_seconds' if deadline=='global' else 'child_timeout'
    try:
        if phase=='count':
            with pytest.raises(workflow.BudgetStop,match=reason):
                workflow.launch('static',{},tmp_path,budget,children,seconds=2,output_bytes=1024)
            assert not children and not list(tmp_path.iterdir())
        else:
            value,receipt=workflow.launch('static',{},tmp_path,budget,children,seconds=2,output_bytes=1024)
            assert not value['ok'] and value['status']=='partial' and receipt['timeout_or_error']==reason
            assert receipt['reaped'] and all(s.closed for s in streams)
            if phase in ('spawn','completed'):assert receipt['exit_code']==0 and receipt['launched']
            else:assert not receipt['launched'] and budget.counts['children']==0
        assert not list(tmp_path.glob('.worker-input-*'))
        assert not (tmp_path/'execution.completed.json').exists()
    finally:
        for child in owned:
            if child.poll() is None:child.kill()
            child.wait(timeout=3)


def test_run_keeps_saved_witness_after_later_stop(job,tmp_path,monkeypatch):
    out=tmp_path/'later-stop';witness=dict(candidate=1,conforming=True,units_complete=True)
    def stopped(plan,out,budget,children):
        budget.witness=witness
        report.checkpoint(out,1,dict(witness=witness,counters=budget.snapshot(),producer=plan['producer'],plan=plan))
        raise workflow.BudgetStop('total_seconds')
    monkeypatch.setattr(workflow,'execute',stopped)
    value=workflow.run(job,out)
    assert not value['ok'] and value['conforming'] and value['status']=='partial'
    assert report.read_checkpoint(out)['witness']==witness
    assert not (out/'execution.completed.json').exists() and not report.read_result(out)['ok']


def test_launch_preserves_partial_response_and_kills_after_ignored_term(tmp_path,monkeypatch):
    original=workflow.subprocess.Popen;owned=[];streams=[]
    def ignores_term(args,**kw):
        streams.extend([kw['stdin'],kw['stdout']])
        child=original([sys.executable,'-B','-c',
            'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            'print("{\\"ok\\": true, \\"accepted_steps\\": 7}",flush=True); time.sleep(5)'],**kw)
        owned.append(child);return child
    monkeypatch.setattr(workflow.subprocess,'Popen',ignores_term)
    budget=launch_budget(tmp_path);children=[]
    try:
        value,receipt=workflow.launch('trajectory',{},tmp_path,budget,children,seconds=.3,output_bytes=1024,steps=40)
        assert not value['ok'] and value['accepted_steps']==7 and value['reason']=='child_timeout'
        assert receipt['exit_code']==-workflow.signal.SIGKILL and receipt['reaped']
        assert 1.9<=receipt['collection_seconds']<4 and receipt['operation_seconds']<1
        assert budget.counts['steps_charged']==budget.reserved['steps_charged']==40
        assert all(s.closed for s in streams) and not list(tmp_path.glob('.worker-input-*'))
        assert 'accepted_steps' in (tmp_path/receipt['log']).read_text()
    finally:
        for child in owned:
            if child.poll() is None:child.kill()
            child.wait(timeout=3)


def test_launch_signal_during_collection_still_reaps(tmp_path,monkeypatch):
    original=workflow.subprocess.Popen;owned=[];children=[]
    def sleeper(args,**kw):
        child=original([sys.executable,'-B','-c','import time; time.sleep(3)'],**kw)
        wait=child.wait;calls=[0]
        def interrupted_wait(timeout=None):
            assert timeout is not None
            calls[0]+=1
            if calls[0]==1:raise workflow.BudgetStop('total_seconds')
            return wait(timeout=timeout)
        child.wait=interrupted_wait
        owned.append(child);return child
    monkeypatch.setattr(workflow.subprocess,'Popen',sleeper)
    try:
        value,receipt=workflow.launch('read',{},tmp_path,launch_budget(tmp_path),children,seconds=.1,output_bytes=1024)
        assert not value['ok'] and receipt['timeout_or_error']=='total_seconds'
        assert receipt['reaped'] and owned[0].poll() is not None
        assert not list(tmp_path.glob('.worker-input-*'))
    finally:
        for child in owned:
            if child.poll() is None:child.kill()
            child.wait(timeout=3)


def test_launch_exact_four_mib_input_is_allowed(tmp_path,monkeypatch):
    original=workflow.subprocess.Popen
    def reader(args,**kw):
        return original([sys.executable,'-B','-c',
            'import sys,json; raw=sys.stdin.buffer.read(); json.loads(raw); print(json.dumps(dict(ok=True,size=len(raw))))'],**kw)
    monkeypatch.setattr(workflow.subprocess,'Popen',reader)
    task=dict(payload='x'*(4*workflow.MIB-len(json.dumps(dict(payload='')).encode())))
    value,receipt=workflow.launch('read',task,tmp_path,launch_budget(tmp_path),[],seconds=3,output_bytes=1024)
    assert value['ok'] and value['size']==receipt['input_bytes']==4*workflow.MIB
    assert not list(tmp_path.glob('.worker-input-*'))


def test_run_refuses_completion_for_unreaped_receipt(job,tmp_path,monkeypatch):
    out=tmp_path/'unreaped';screened(job);execute=workflow.execute
    def unreaped(plan,out,budget,children):
        value=execute(plan,out,budget,children)
        children[-1]['reaped']=False  # Receipt fault only; actual child was reaped.
        return value
    monkeypatch.setattr(workflow,'execute',unreaped)
    with pytest.raises(RuntimeError,match='reap not confirmed'):workflow.run(job,out)
    assert not (out/'execution.completed.json').exists() and not report.read_result(out)['ok']
