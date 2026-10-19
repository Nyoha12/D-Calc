"""Session integrity, cumulative resources and no acoustic prefix replay."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from didgeridoo_optimizer.tests.test_seeded_played_search import job, change, write, ROOT
from didgeridoo_optimizer.tests.test_seeded_played_search_cli import ENV
from didgeridoo_optimizer.pipeline import played_search as flow
from didgeridoo_optimizer.reporting import played_search as report


def install_local_screening():
    """Cheap routing double only: native static and prefix reader code still run.

    Fresh CLI and temporal tests separately cover real process supervision.
    """
    original=flow.launch
    def routed(kind,task,out,budget,children,**kw):
        if kind!='static' and task.get('kind')!='read_session_prefix':
            return original(kind,task,out,budget,children,**kw)
        budget.reserve(kind,kw['seconds'],kw['output_bytes'],kw.get('steps',0))
        budget.launched(kind,kw.get('steps',0));started=time.monotonic()
        value=flow.worker(task)
        child=dict(kind=kind,pid=os.getpid(),exit_code=0,launched=True,reaped=True,
            timeout_or_error=None,steps_charged=kw.get('steps',0),seconds=time.monotonic()-started,
            log=f'child-{len(children)+1:04d}.log',test_routing='in-process native screening/reader')
        (Path(out)/child['log']).write_text(json.dumps(value)+'\n');children.append(child)
        return value,child
    flow.launch=routed
    return original


@pytest.fixture(autouse=True)
def local_screening(monkeypatch):
    original=install_local_screening(); replacement=flow.launch;flow.launch=original
    monkeypatch.setattr(flow,'launch',replacement)


@pytest.fixture
def paused(job,tmp_path):
    out=tmp_path/'session'
    r=flow.run(job,out,session=True,pause_after=1)
    assert r['status']=='paused',r
    return job,out,Path(r['resume_point'])


@pytest.mark.parametrize('value',[True,False,1.,0,-1,'1',float('nan'),float('inf')])
def test_strict_pause_before_effects(job,tmp_path,value):
    with pytest.raises(ValueError):flow.run(job,tmp_path/'absent',session=True,pause_after=value)
    assert not (tmp_path/'absent').exists()


def test_full_reader_no_new_science_and_exact_continuation(paused,monkeypatch,record_property):
    job,out,point=paused
    calls=[];original=flow.launch
    def launch(kind,*args,**kw):calls.append(kind);return original(kind,*args,**kw)
    monkeypatch.setattr(flow,'launch',launch)
    # Prefix reconstruction and native readers cannot invoke these entry points.
    from didgeridoo_optimizer.nonlinear import scalar_fit
    from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
    from didgeridoo_optimizer.acoustics import transfer_matrix
    def no(*a,**k):raise AssertionError('prefix science')
    monkeypatch.setattr(scalar_fit,'fit',no);monkeypatch.setattr(SimultaneousCoupling,'step',no)
    monkeypatch.setattr(transfer_matrix,'input_impedance',no)
    first=report.read_session(out);assert first['ok'] and first['resumable'],first
    before=first['result']['counters']['elapsed_seconds']
    time.sleep(.05)
    r=flow.run(job,out,resume=point)
    assert r['status']=='complete' and calls==['read','static']
    assert r['counters']['candidates']==r['counters']['static_units']==2
    assert r['counters']['elapsed_seconds']>before+15
    assert report.read_session(out)['ok']
    record_property('continued_launch_kinds',str(calls))


def test_rehashed_attacks_refused_before_next_child(paused,monkeypatch):
    job,out,point=paused
    original={p:p.read_bytes() for p in out.rglob('*') if p.is_file()}
    original_launch=flow.launch
    def reader_only(kind,*a,**k):
        if kind!='read':pytest.fail('tampering reached science')
        return original_launch(kind,*a,**k)
    monkeypatch.setattr(flow,'launch',reader_only)
    for attack in ('counter','time','decision','vector','origin','generation','cache','plan','source','static'):
        try:
            record=report.read_json(point);p=record['payload'];r=p['result']
            if attack=='counter':r['counters']['static_units']+=1
            elif attack=='time':r['counters']['elapsed_seconds']-=1
            elif attack=='decision':r['search']['decisions'][0]['reason']='forged'
            elif attack in ('vector','origin'):
                row=r['search']['history'][0]
                if attack=='vector':row['variables_si'][0]=.94
                else:row['start_origin']['id']='long_wide'
            elif attack=='generation':p['generation']=True
            elif attack=='cache':p['cache']['fake']={}
            elif attack=='plan':r['plan']['initial'][0]+=.001
            elif attack=='source':r['plan']['producer']['loaded_sources_sha256']['tools/played_search.py']='0'*64
            else:
                path=out/'candidate-001/static.json';v=report.read_json(path);v['rows'][0]['value_si']+=1;write(path,v)
                p['files'][path.relative_to(out).as_posix()]=report.file_sha256(path)
            record['sha256']=flow.digest(p);write(point,record)
            value=report.read_session(out)
            assert not value['ok'],attack
            if attack in ('cache','static'):
                refused=flow.run(job,out,resume=point)
                assert not refused['ok'] and not refused['resumable']
            else:
                with pytest.raises((ValueError,flow.BudgetStop)):
                    flow.run(job,out,resume=point,dry_run=True)
        finally:
            # Restore only this disposable adversarial test's own artifacts.
            for path,raw in original.items():path.write_bytes(raw)
            # Native refusal after admission may leave an honest started record.
            for path in out.glob('invocation-00002.json'):path.unlink()
            for path in out.glob('failure-00002.json'):path.unlink()
            for path in out.glob('child-0002.log'):path.unlink()


def test_later_file_links_and_writer_exclusivity(paused,tmp_path):
    job,out,point=paused
    with report.session_lock(out):
        p=subprocess.run([sys.executable,'-B','-m','tools.played_search','--job',str(job),'--output-dir',str(out),'--resume',str(point)],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=30)
        assert p.returncode==2 and 'active writer' in p.stdout
    extra=out/'later';extra.write_text('unaccounted')
    assert not report.read_session(out)['ok'];extra.unlink()
    extra.symlink_to(point)
    with pytest.raises(ValueError):flow.run(job,out,resume=point,dry_run=True)
    extra.unlink()
    linked=tmp_path/'linked';linked.symlink_to(out,target_is_directory=True)
    with pytest.raises(ValueError):flow.run(job,linked,resume=point)


def test_interrupted_invocation_never_falls_back(paused,monkeypatch):
    job,out,point=paused
    original=flow.launch
    def interrupted(kind,*a,**k):
        if kind=='read':return original(kind,*a,**k)
        raise KeyboardInterrupt('candidate in progress')
    monkeypatch.setattr(flow,'launch',interrupted)
    with pytest.raises(KeyboardInterrupt):flow.run(job,out,resume=point)
    assert (out/'candidate-002').is_dir() and (out/'invocation-00002.json').exists()
    assert not report.read_session(out)['ok']
    with pytest.raises(ValueError,match='Unclosed'):flow.run(job,out,resume=point)


def test_remaining_reservation_and_all_session_storage(paused,monkeypatch):
    job,out,point=paused
    original=flow.launch
    def refuse(kind,task,out,budget,children,**kw):
        assert budget.counts['candidates']==2 and budget.counts['static_units']==1
        assert budget.reserved['children']==1
        budget.start-=budget.limits['seconds']-16
        return original(kind,task,out,budget,children,**kw)
    monkeypatch.setattr(flow,'launch',refuse)
    monkeypatch.setattr(flow.subprocess,'Popen',lambda *a,**k:pytest.fail('reserve must refuse before launch'))
    # The subprocess mock also blocks provenance Git: use a direct Budget oracle
    # for the reservation, then actual failure test below covers orchestration.
    budget=flow.Budget(dict(flow.core.LIMITS,seconds=40),out);budget.start-=25
    with pytest.raises(flow.BudgetStop,match='remaining_wall'):budget.reserve('static',1,1)
    with pytest.raises(flow.BudgetStop,match='remaining_storage'):
        flow.Budget(dict(flow.core.LIMITS,output_mib=1),out).reserve('static',1,1)


@pytest.mark.parametrize('fault',['partial','no_output','failed'])
def test_incomplete_operation_is_terminal_without_retry(job,tmp_path,monkeypatch,fault):
    out=tmp_path/fault;original=flow.launch;calls=[]
    def fail(kind,*a,**k):
        calls.append(kind)
        value,child=original(kind,*a,**k)
        value.update(ok=False,reason=fault)
        if fault=='failed':child['exit_code']=2
        return value,child
    monkeypatch.setattr(flow,'launch',fail)
    r=flow.run(job,out,session=True,pause_after=1)
    assert not r['ok'] and not r['resumable'] and calls==['static']
    assert (out/'failure-00001.json').is_file() and not list(out.glob('session-*.json'))
    with pytest.raises(ValueError):flow.run(job,out,resume=out/'session-00001.json')


def test_shared_fit_counter_reference_and_reader_bijection():
    # Pilot C1-A oracle: one candidate, two scenarios, one fit, 7 children.
    plan=dict(job=dict(scenarios=[dict(steps_per_call=80)],budgets=dict(flow.core.LIMITS)))
    def receipt(kind,index,steps=0):
        return dict(kind=kind,launched=True,reaped=True,exit_code=0,timeout_or_error=None,
                    steps_charged=steps,seconds=.01,log=f'child-{index:04d}.log')
    children=[receipt(k,i+1,80 if k=='trajectory' else 0) for i,k in enumerate(
        ['static','fit','read','trajectory','read','trajectory','read'])]
    rows=[dict(units=[dict(fit='fit-a',fit_reader=children[2],segments=[dict(child=children[i],reader=children[i+1],
        quota=80,accepted_before=0,accepted_after=80)]) for i in (3,5)])]
    def counts(rows,children):
        return dict(candidates=len(rows),children=len(children),fits=sum(c['kind']=='fit' for c in children),
            fit_attempts=sum(c['kind']=='fit' for c in children),trajectories=sum(c['kind']=='trajectory' for c in children),
            steps_charged=sum(c['steps_charged'] for c in children),accepted_steps=160*len(rows),static_units=len(rows),
            reader_units=sum(c['kind']=='read' for c in children),elapsed_seconds=10.,
            reserved=dict(fits=sum(c['kind']=='fit' for c in children),trajectories=2*len(rows),children=len(children),steps_charged=160*len(rows)))
    c=counts(rows,children); assert c['reader_units']==3 and c['children']==7
    flow.verify_session_counters(plan,rows,c,children)
    for attack in ('omit','duplicate','segment_duplicate','generation_bool','generation_skip','generation_duplicate'):
        rs=copy.deepcopy(rows);cs=copy.deepcopy(children)
        if attack=='omit':cs.pop(2)
        elif attack=='duplicate':cs.append(dict(cs[2],log=f'child-{len(cs)+1:04d}.log'))
        elif attack=='segment_duplicate':rs[0]['units'][1]['segments'][0]['reader']=rs[0]['units'][0]['segments'][0]['reader']
        else:
            cs.append(dict(receipt('read',8),session_generation={'generation_bool':True,'generation_skip':3,'generation_duplicate':2}[attack]))
            if attack=='generation_duplicate':cs.append(dict(receipt('read',9),session_generation=2))
        with pytest.raises((ValueError,KeyError)):flow.verify_session_counters(plan,rs,counts(rs,cs),cs)
    # Three invocations with distinct candidates; each candidate shares its fit.
    for generation in (2,3):
        children.append(dict(receipt('read',len(children)+1),session_generation=generation))
        offset=len(children); new=[receipt(k,offset+i+1,80 if k=='trajectory' else 0) for i,k in enumerate(
            ['static','fit','read','trajectory','read','trajectory','read'])]
        children.extend(new)
        rows.append(dict(units=[dict(fit=f'fit-{generation}',fit_reader=new[2],segments=[dict(child=new[i],reader=new[i+1],
            quota=80,accepted_before=0,accepted_after=80)]) for i in (3,5)]))
        flow.verify_session_counters(plan,rows,counts(rows,children),children)


@pytest.mark.parametrize('mode',['evaluate_only','poll'])
@pytest.mark.parametrize('initial',[False,True])
@pytest.mark.parametrize('stop',[False,True])
@pytest.mark.parametrize('descriptive',[False,True])
@pytest.mark.parametrize('maximum',[1,3,8,16])
def test_pure_poll_continuation_reference(mode,initial,stop,descriptive,maximum):
    # Prescribed observations only, analogous to the pilot's 64 references.
    from didgeridoo_optimizer.tests.test_seeded_played_search import spec,synthetic
    starts,search=spec([[.25,.5],[.75,.5],[.25,.5]],mode=mode,initial=initial,stop=stop)
    search['normalized_steps']=[.5,.25];search['directions']=[1,-1]
    variables=[dict(id='x',low=0.,high=1.),dict(id='y',low=0.,high=1.)]
    calls=[]
    def observed(x,a,i,stage,**kw):
        calls.append(i); score=abs(1-x[0])+abs(.5-x[1])
        return dict(synthetic(score,score==0),units_complete=True)
    def run(evaluate):return flow.core.poll(variables,[.5,.5],['nominal'],search,maximum,evaluate,descriptive=descriptive,starts=starts)
    whole=run(observed); expected_calls=list(calls);prefix=[];new_calls=[]
    while True:
        before=len(prefix)
        def continued(x,a,i,stage,**kw):
            if i<=before:
                r=prefix[i-1]
                assert (r['variables_si'],r['alternative'],r['stage'],r['start_origin'])==(x,a,stage,kw['start_origin'])
                return copy.deepcopy(r)
            if i>before+1:raise StopIteration('session_paused')
            new_calls.append(i);return observed(x,a,i,stage,**kw)
        result=run(continued);prefix=result['history']
        if result['termination']!='session_paused':break
    assert result==whole and new_calls==expected_calls


def test_legacy_rehashed_pause_is_refused(paused):
    _,out,point=paused
    p=report.read_json(point)['payload']; assert p['result']['search']['termination']=='session_paused'
    flow.verify_search(p['result']['plan'],p['result']['search'],session=True)
    with pytest.raises(ValueError):flow.verify_search(p['result']['plan'],p['result']['search'])
    # A fully re-enveloped historical publication still has no session opt-in.
    old=out.parent/'old';old.mkdir(); plan=p['result']['plan']
    report.write_json(old/'plan.json',plan);report.export_starts(old,plan)
    result=copy.deepcopy(p['result']);result['global_resume']='unsupported'
    result['search']['history']=[report.read_json(out/r['file']) for r in result['search']['history']]
    report.export(old,result)
    report.prepare_completion(old,dict(ok=True,children=p['children']));report.publish_completion(old)
    value=report.read_result(old)
    assert not value['ok'] and 'sequence' in value['reason']
    assert 'reprise globale unsupported' in (old/'summary.txt').read_text()


@pytest.mark.parametrize('phase',['inventory','prefix_read','point_write','final_write','invocation_write'])
@pytest.mark.parametrize('fault',['slow','error'])
def test_session_deadline_failures_never_publish_boundary(paused,monkeypatch,phase,fault):
    import signal
    job,out,point=paused
    old_handler=signal.getsignal(signal.SIGALRM);old_timer=signal.getitimer(signal.ITIMER_REAL)
    def injected():
        assert (out/'invocation-00002.json').exists() or phase=='invocation_write'
        if fault=='error':raise OSError('injected '+phase)
        signal.setitimer(signal.ITIMER_REAL,.01);time.sleep(.04)
    if phase=='inventory':
        fn=report.session_inventory
        def inventory(*a,**k):injected();return fn(*a,**k)
        monkeypatch.setattr(report,'session_inventory',inventory)
    elif phase=='prefix_read':
        fn=flow.launch
        def read(kind,*a,**k):
            if kind=='read':injected()
            return fn(kind,*a,**k)
        monkeypatch.setattr(flow,'launch',read)
    elif phase=='final_write':
        fn=report.export
        def export(*a,**k):injected();return fn(*a,**k)
        monkeypatch.setattr(report,'export',export)
    else:
        fn=report.session_write
        def put(path,*a,**k):
            if Path(path).name==('session-00002.json' if phase=='point_write' else 'invocation-00002.json'):
                # Preserve a failed write's visible effect, never fall back.
                if phase=='invocation_write':Path(path).write_text('{')
                injected()
            return fn(path,*a,**k)
        monkeypatch.setattr(report,'session_write',put)
    signal.setitimer(signal.ITIMER_REAL,600)
    try:
        start=time.monotonic();r=flow.run(job,out,resume=point)
        assert not r['ok'] and not r['resumable'] and time.monotonic()-start<40
        assert not (out/'boundary-00002.json').exists()
        assert (out/'failure-00002.json').exists()
        assert signal.getsignal(signal.SIGALRM)==old_handler
        assert 550<signal.getitimer(signal.ITIMER_REAL)[0]<=600
    finally:signal.setitimer(signal.ITIMER_REAL,*old_timer)
    assert not report.read_session(out)['ok']
    with pytest.raises(ValueError,match='Unclosed'):flow.run(job,out,resume=point)


def test_session_remaining_budget_before_admission_launch(paused,monkeypatch):
    job,out,point=paused;p=report.read_json(point)['payload'];prior=p['result']['counters'];seen=[]
    original=flow.launch
    def exhausted(kind,task,out,budget,children,**kw):
        assert kind=='read' and budget.counts['candidates']==prior['candidates']==1
        assert budget.counts['static_units']==prior['static_units']==1
        assert budget.reserved==prior['reserved'] and children==p['children']
        assert time.monotonic()-budget.start>=prior['elapsed_seconds']
        seen.append(kind);budget.start-=budget.limits['seconds']-16
        return original(kind,task,out,budget,children,**kw)
    monkeypatch.setattr(flow,'launch',exhausted)
    result=flow.run(job,out,resume=point)
    assert not result['ok'] and seen==['read']
    assert result['counters']['reserved']==prior['reserved']
    assert result['counters']['static_units']==1 and result['counters']['reader_units']==0
    assert result['counters']['artifact_bytes']>=prior['artifact_bytes']
    assert not (out/'candidate-002').exists()


@pytest.mark.parametrize('source',report.NEW_SOURCES)
def test_after_import_source_change_before_api_and_cli(job,tmp_path,monkeypatch,source):
    from tools import played_search as cli
    original=Path.read_bytes
    def changed(path):
        raw=original(path)
        return raw+b'\n# changed after import' if path==ROOT/source else raw
    monkeypatch.setattr(Path,'read_bytes',changed)
    original_sha=report.file_sha256
    monkeypatch.setattr(report,'file_sha256',lambda path,**kw:'0'*64 if Path(path)==ROOT/source else original_sha(path,**kw))
    monkeypatch.setattr(flow,'launch',lambda *a,**k:pytest.fail('changed source reached science'))
    with pytest.raises(ValueError):flow.run(job,tmp_path/'new',session=True)
    assert cli.main(['--job',str(job),'--output-dir',str(tmp_path/'cli'),'--session'])==2
    assert not (tmp_path/'new').exists() and not (tmp_path/'cli').exists()


def test_bootstrap_original_job_and_strict_bounds(paused,monkeypatch):
    job,out,point=paused;raw=report.read_json(out/'plan.json')
    original=(out/'plan.json').read_bytes()
    for value in (True,900.0,3601,901):
        forged=copy.deepcopy(raw);forged['job']['budgets']['seconds']=value
        forged['context_sha256']=flow.digest({k:v for k,v in forged.items() if k!='context_sha256'})
        write(out/'plan.json',forged)
        with pytest.raises(ValueError):flow.run(job,out,resume=point)
        assert not (out/'invocation-00002.json').exists()
    (out/'plan.json').write_bytes(original)
    # Admission follows the journal; provenance may not advance before it.
    original_provenance=report.provenance
    def provenance(**kw):
        assert (out/'invocation-00002.json').exists()
        return original_provenance(**kw)
    monkeypatch.setattr(report,'provenance',provenance)
    assert flow.run(job,out,resume=point)['ok']


def test_after_import_source_change_before_resume(paused,monkeypatch):
    job,out,point=paused;original=report.file_sha256
    for source in report.NEW_SOURCES:
        with monkeypatch.context() as m:
            m.setattr(report,'file_sha256',lambda path,**kw:'0'*64 if Path(path)==ROOT/source else original(path,**kw))
            with pytest.raises(ValueError,match='Source changed'):flow.run(job,out,resume=point)
            assert not (out/'invocation-00002.json').exists()
