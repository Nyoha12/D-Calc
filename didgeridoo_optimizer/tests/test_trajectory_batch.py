"""Explicit call batching: scheduling doubles and short native synthetic evidence.

The imported geometry fixture simulates fit receipts/targets; it is not an
instrument fit. Only REGISTER-TARGET advances real native temporal states here.
"""
import copy
import json
from pathlib import Path
import time

import numpy as np
import pytest

from didgeridoo_optimizer.optimization import played_search as core
from didgeridoo_optimizer.pipeline import played_search as flow
from didgeridoo_optimizer.reporting import played_search as report
from didgeridoo_optimizer.tests.test_seeded_played_search import job, change, write
from didgeridoo_optimizer.tests.test_geometry_state import synthetic_bundle, EXAMPLE


@pytest.mark.parametrize('value',[True,False,48.,96.,float('nan'),float('inf'),'96',None,0,-48,47,49,72001,72048])
def test_strict_optional_quota_before_science_destination(job,monkeypatch,value):
    change(job,lambda r:r['scenarios'][0].update(steps_per_call=value))
    def no(*a,**k):pytest.fail('science/output before quota refusal')
    monkeypatch.setattr(flow.td,'input_impedance',no)
    monkeypatch.setattr(flow.td,'fit_passive',no)
    monkeypatch.setattr(np.linalg,'eigvals',no)
    monkeypatch.setattr(report,'write_json',no)
    out=job.parent/'absent'
    with pytest.raises(ValueError):flow.run(job,out,dry_run=True)
    assert not out.exists()


def test_modal_backend_refused_at_schema_without_sources(job):
    raw=report.read_json(EXAMPLE/'recipe.json')
    import yaml
    value=yaml.safe_load(job.read_text())
    value['fit']=yaml.safe_load((EXAMPLE.parent/'played_search/request.yaml').read_text())['fit']
    value['scenarios'][0]['steps_per_call']=48
    with pytest.raises(ValueError,match='certified_scalar_v1'):core.validate_job(value)
    template=json.loads((EXAMPLE/'experiment.json').read_text())
    template['case']['kind']='saved'
    with pytest.raises(ValueError,match='saved_state'):core.validate_steps_per_call(dict(steps_per_call=48),template)


def test_declarations_distinct_seed_origins_and_template_unchanged(job):
    original=flow.preflight(job)
    change(job,lambda r:(r['scenarios'][0].update(steps_per_call=72000),
        r['scenarios'].append(dict(r['scenarios'][0],id='second',steps_per_call=48))))
    p=flow.preflight(job)
    assert [s['steps_per_call'] for s in p['scenarios']]==[72000,48]
    assert p['starts']==original['starts']
    assert p['scenarios'][0]['template']==original['scenarios'][0]['template']
    assert 'steps_per_call' not in original['job']['scenarios'][0]
    assert 'steps_per_call' not in original['scenarios'][0]
    for where in ('job','scenario'):
        bad=copy.deepcopy(p)
        (bad['job']['scenarios'][0] if where=='job' else bad['scenarios'][0])['steps_per_call']=96
        bad['context_sha256']=flow.digest({k:v for k,v in bad.items() if k!='context_sha256'})
        with pytest.raises(ValueError):flow.check_plan(bad)


def scheduling(monkeypatch,tmp_path,*,fault=None):
    """No numerical work: exercise the actual common Budget and helper loop."""
    experiment=dict(case=dict(kind='saved_state'),budgets=dict(chunk_steps=48,child_seconds=175,output_mib=1),
                    plateaus=[dict(steps=113)])
    calls=[];cursor=[0];reads=[0]
    def preflight(ep,local,*,resume,steps_this_run):
        before=0 if resume is None else int(Path(resume).name)
        cursor[0]=before
        p=dict(accepted_before=before,steps_this_run=steps_this_run,context_sha256='context',output=str(local))
        class P:
            def as_dict(self):return p
        return P()
    def launch(kind,task,out,budget,children,*,seconds,output_bytes,steps=0):
        budget.reserve(kind,seconds,output_bytes,steps);budget.launched(kind,steps)
        receipt=dict(kind=kind,exit_code=0,reaped=True,launched=True,timeout_or_error=None,steps_charged=steps)
        children.append(receipt)
        if kind=='trajectory':
            calls.append(steps)
            got=cursor[0]+(17 if fault=='partial' else 0 if fault=='no_progress' else steps)
            if fault=='loss':got=cursor[0]-1
            if fault in ('failed','timeout'):receipt.update(exit_code=2 if fault=='failed' else -15,timeout_or_error=fault)
            local=Path(task['plan']['output'])
            if fault!='missing':write(local/'result.json',dict(accepted_steps=got,checkpoint=dict(path=str(local/str(got))),reason='interrupted' if fault=='partial' else None))
            cursor[0]=got
            return dict(ok=got==113,status='complete' if got==113 else 'partial',accepted_steps=got),receipt
        reads[0]+=1
        return dict(ok=cursor[0]==113,status='unconfirmed' if fault=='reader' else 'complete' if cursor[0]==113 else 'partial',
                    accepted_steps=cursor[0],criteria=[]),receipt
    monkeypatch.setattr(flow.rt,'preflight',preflight)
    monkeypatch.setattr(flow,'launch',launch)
    monkeypatch.setattr(flow,'check_plan',lambda p,**kw:None)
    monkeypatch.setattr(flow.rtreport,'prepare_completion',lambda *a:None)
    monkeypatch.setattr(flow.rtreport,'publish_completion',lambda *a:None)
    limits=dict(core.LIMITS,steps=1000,trajectories=16)
    budget=flow.Budget(limits,tmp_path);children=[]
    def run(name,quota=None,progress=None):
        return flow.state_trajectory(tmp_path/'unused',tmp_path/name,experiment,tmp_path,budget,children,
                                    dict(job=dict(budgets=limits)),steps_per_call=quota,progress=progress)
    return run,budget,calls,reads,children


def test_exact_absent_default_and_terminal_quota(monkeypatch,tmp_path):
    run,b,calls,reads,_=scheduling(monkeypatch,tmp_path)
    legacy=run('legacy')
    assert calls==[48,48,17] and reads[0]==3
    assert set(legacy)=={'verified','status','output','context_sha256','segments','criteria'}
    calls.clear()
    batch=run('batch',96)
    assert calls==[96,17] and reads[0]==5
    assert set(batch)==set(legacy) and batch['criteria']==legacy['criteria']
    assert b.counts['steps_charged']==b.counts['accepted_steps']==226
    assert b.counts['trajectories']==b.counts['reader_units']==5
    calls.clear();run('larger_than_total',72000)
    assert calls==[113]


@pytest.mark.parametrize('fault',['failed','timeout','partial','no_progress','reader','missing','loss'])
def test_full_charge_no_retry_on_failure_and_partial(monkeypatch,tmp_path,fault):
    run,b,calls,reads,_=scheduling(monkeypatch,tmp_path,fault=fault)
    progress={}
    if fault=='loss':
        with pytest.raises(ValueError,match='outside charged quota'):run('bad',96,progress)
    else:
        result=run('bad',96,progress)
        assert not result['verified'] and result['reason']
    assert calls==[96] and b.counts['steps_charged']==b.reserved['steps_charged']==96
    assert b.counts['accepted_steps']==(17 if fault=='partial' else 0 if fault in ('loss','no_progress','missing') else 96)
    assert len(progress['segments'])==1


def test_shared_budget_distinct_scenarios_and_seeded_starts(monkeypatch,tmp_path,job):
    run,b,calls,reads,_=scheduling(monkeypatch,tmp_path)
    b.limits['steps']=350
    p=flow.preflight(job)  # check_plan double only; admission stays native
    visited=[]
    def evaluate(values,alternative,index,stage,*,start_origin):
        row=dict(conforming=False,max_violation_normalized=None,status='partial',
                 observed_violation_lower_bound=None,comparable_hard_rows=[])
        visited.append(start_origin['id'])
        try:
            for ident,quota in [('a',96),('b',48)]:run(f'{index}-{ident}',quota)
        except flow.BudgetStop as exc:row['stop_reason']=str(exc)
        return row
    result=core.poll(p['variables'],p['initial'],p['alternatives'],p['job']['search'],3,evaluate,starts=p['starts'])
    assert visited==['short_wide','long_wide']
    assert calls==[96,17,48,48,17,96,17]
    assert b.counts['steps_charged']==339 and b.counts['accepted_steps']==339
    assert result['termination']=='total_steps_charged'


@pytest.fixture
def native_batch(job,synthetic_bundle):
    # Reuse native fixture unchanged, including its honest status as a numerical
    # fixture with simulated fit provenance. No instrument acquisition happens.
    change(job.parent/'experiment.json',lambda r:(r['plateaus'][0].update(steps=80,duration_s=80/12000),
        r['windows'][0].update(stop_step=80)))
    change(job,lambda r:r['scenarios'][0].update(steps_per_call=96))
    p=flow.preflight(job)
    gp=report.read_json(synthetic_bundle/'plan.json')
    r=flow.instantiate(p['scenarios'][0]['template'],gp['inputs']['config']['path'],
                       gp['inputs']['design']['path'],synthetic_bundle/'model.json')
    ep=job.parent/'native-experiment.json';write(ep,r)
    return p,r,ep


def test_real_native_groupings_bitwise_and_rehashed_guards(native_batch,tmp_path,record_property,monkeypatch):
    p,r,ep=native_batch
    acquired=[]
    for quota in (48,96):
        out=tmp_path/f'native-{quota}';out.mkdir()
        b=flow.Budget(p['job']['budgets'],out);children=[];start=time.monotonic()
        u=flow.state_trajectory(ep,out/f'q{quota}',r,out,b,children,p,steps_per_call=quota)
        elapsed=time.monotonic()-start
        assert u['verified'],u
        s=dict(p['scenarios'][0],steps_per_call=quota)
        if quota==48:s.pop('steps_per_call')  # Mixed JOB: absent means native chunk.
        # Count only after acquisition: producer readers remain one per call.
        semantic_reads=[]; native_reader=flow.rtreport.read_result
        def counted(*args, **kwargs):
            semantic_reads.append(str(args[0])); return native_reader(*args, **kwargs)
        with monkeypatch.context() as m:
            m.setattr(flow.rtreport, 'read_result', counted)
            terminal=flow.verify_batch_unit(s,u,out,r,native_read=True)
        assert semantic_reads == [str(out/u['output'])]
        record_property(f'quota_{quota}_final_semantic_reads', len(semantic_reads))
        # Dispatch integration only uses geometry doubles; the native terminal
        # chain/criteria reader above and below is real, with no new steps.
        candidate_plan=dict(job=dict(fit={},scenarios=[dict(s,steps_per_call=quota)]),
                            job_source=dict(path='unused'),scenarios=[s],paths=dict(config='unused'),unsupported=[])
        candidate_unit=dict(u,scenario=s['id'],fit='unused-fit')
        (out/'unused-fit').mkdir(); write(out/'unused-design', {})
        criteria=core.played_rows(s['template'],terminal['result']['analysis']['criteria'],s['configuration'],s['id'])
        row=dict(units=[candidate_unit],criteria=criteria,file='candidate.json',geometry_valid=False,
                 configurations={s['configuration']:dict(design='unused-design')},units_complete=False)
        row.update(core.verdict(criteria,units_complete=False,unsupported=[]))
        with monkeypatch.context() as m:
            m.setattr(flow,'load_inputs',lambda *a:(None,candidate_plan))
            m.setattr(flow,'instantiate',lambda *a:r)
            m.setattr(flow,'read_fit',lambda *a:None)
            m.setattr(flow.rtreport,'read_result',counted)
            flow.verify_candidate(candidate_plan,out,row,native_read=True)
        assert semantic_reads == [str(out/u['output'])]*2
        record_property(f'quota_{quota}_candidate_semantic_reads', 1)
        flow.verify_batch_counters(p,[dict(units=[u])],dict(b.snapshot(),candidates=1),children)
        result=report.read_json(out/u['output']/'result.json')
        chain=flow.rtreport.inspect_checkpoint(result['checkpoint']['path'])
        arrays=flow.rtreport.load_series(chain)
        chunks=[(c['series']['start_step'],c['series']['stop_step']) for _,c in chain if c['series']]
        assert chunks==[(0,48),(48,80)]
        assert b.counts['accepted_steps']==b.counts['steps_charged']==80
        assert b.counts['trajectories']==b.counts['reader_units']==(2 if quota==48 else 1)
        assert all(c['reaped'] and c['exit_code']==0 for c in children)
        record_property(f'quota_{quota}_seconds',elapsed)
        record_property(f'quota_{quota}_calls',b.counts['trajectories'])
        acquired.append((u,result,arrays,chain,b,children,s))
    a,b=acquired
    for key in a[2]:assert np.array_equal(a[2][key],b[2][key]),key
    for key in ('accepted_steps','sums','events','context_checks'):
        assert a[1][key]==b[1][key],key
    assert a[1]['analysis']['criteria']==b[1]['analysis']['criteria']
    assert a[3][-1][1]['experiment']==b[3][-1][1]['experiment']
    # Scientific entry points cannot run during orchestration verification.
    from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
    monkeypatch.setattr(SimultaneousCoupling,'step',lambda *a,**k:pytest.fail('reader stepped'))
    # Earlier local publications are not covered by the terminal manifest.
    from didgeridoo_optimizer.tests.test_state_register_cli import reclose
    earlier=tmp_path/'native-48'/a[0]['segments'][0]['output']
    saved={path:path.read_bytes() for path in earlier.iterdir() if path.is_file()}
    for attack in ('result','receipt','manifest','marker','bound'):
        bad=copy.deepcopy(a[0])
        try:
            if attack=='bound':bad['segments'][0]['accepted_after']=47
            elif attack=='result':
                value=report.read_json(earlier/'result.json');value['accepted_steps']=47
                write(earlier/'result.json',value);reclose(earlier)
            elif attack=='receipt':
                value=report.read_json(earlier/'execution.json');value['children'][0]['exit_code']=False
                write(earlier/'execution.json',value);reclose(earlier)
            elif attack=='manifest':
                value=report.read_json(earlier/'manifest.json');value.pop('result.json')
                write(earlier/'manifest.json',value);reclose(earlier)
            else:write(earlier/'execution.completed.json',{})
            with pytest.raises(ValueError):
                flow.verify_batch_unit(a[6],bad,tmp_path/'native-48',r,native_read=True)
        finally:
            for path,content in saved.items():path.write_bytes(content)
    u,result,arrays,chain,budget,children,s=b
    for key,value in [('quota',48),('accepted_before',1),('accepted_after',79)]:
        bad=copy.deepcopy(u);bad['segments'][0][key]=value
        envelope=dict(payload=bad,sha256=flow.digest(bad))
        assert envelope['sha256']==flow.digest(envelope['payload'])
        with pytest.raises(ValueError):flow.verify_batch_unit(s,bad,out,r,native_read=True)
    bad=copy.deepcopy(u);bad['segments'][0]['child']['steps_charged']=48
    with pytest.raises(ValueError):flow.verify_batch_unit(s,bad,out,r,native_read=True)
    bad=copy.deepcopy(u);bad['segments'][0]['reader']['launched']=False
    with pytest.raises(ValueError):flow.verify_batch_unit(s,bad,out,r,native_read=True)
    bad=copy.deepcopy(u);bad['segments'][0]['reader']['steps_charged']=1
    with pytest.raises(ValueError):flow.verify_batch_unit(s,bad,out,r,native_read=True)
    for key in ('steps_charged','accepted_steps','trajectories','reader_units'):
        counters=dict(budget.snapshot(),candidates=1);counters[key]+=1
        with pytest.raises(ValueError):flow.verify_batch_counters(p,[dict(units=[u])],counters,children)
    # Rehashed native proof alteration is checked by the full native reader.
    from didgeridoo_optimizer.tests.test_state_register_cli import reclose
    path=out/u['output']/'result.json';changed=copy.deepcopy(result)
    changed['analysis']['criteria'][0]['status']='pass';write(path,changed);reclose(path.parent)
    with pytest.raises(ValueError,match='unconfirmed'):flow.verify_batch_unit(s,u,out,r,native_read=True)


def test_real_interruption_retains_partial_charge_and_fresh_reader(native_batch,tmp_path,monkeypatch):
    p,r,ep=native_batch;out=tmp_path/'interrupted';out.mkdir()
    real_launch=flow.launch;real_experiment=flow.rt.experiment_for;active=[]
    def capture(*a,**k):
        e=real_experiment(*a,**k);active.append(e);return e
    monkeypatch.setattr(flow.rt,'experiment_for',capture)
    def launch(kind,task,out,budget,children,**kw):
        if kind!='trajectory':return real_launch(kind,task,out,budget,children,**kw)
        # Deterministic genuine stop in native advance, not fabricated progress.
        budget.reserve(kind,kw['seconds'],kw['output_bytes'],kw['steps']);budget.launched(kind,kw['steps'])
        value=flow.rt.calculate(task['plan'],stop=lambda:bool(active) and active[-1].accepted>=17)
        child=dict(kind=kind,pid=None,exit_code=0,launched=True,reaped=True,timeout_or_error=None,
                   steps_charged=kw['steps'],synthetic_test_memory_caller=True)
        children.append(child);return value,child
    monkeypatch.setattr(flow,'launch',launch)
    b=flow.Budget(p['job']['budgets'],out);children=[]
    u=flow.state_trajectory(ep,out/'partial',r,out,b,children,p,steps_per_call=96)
    assert not u['verified'] and 'interruption' in u['reason']
    assert b.counts['steps_charged']==80 and b.counts['accepted_steps']==17
    assert b.counts['trajectories']==b.counts['reader_units']==1
    assert u['segments'][0]['accepted_after']==17 and u['segments'][0]['quota']==80
    native_reader=flow.rtreport.read_result; semantic_reads=[]
    def counted(*args,**kwargs):
        semantic_reads.append(str(args[0]));return native_reader(*args,**kwargs)
    monkeypatch.setattr(flow.rtreport,'read_result',counted)
    terminal=flow.verify_batch_unit(p['scenarios'][0],u,out,r,native_read=True)
    assert semantic_reads==[str(out/u['output'])] and not terminal['ok']
    without_reader=copy.deepcopy(u);without_reader['segments'][0].pop('reader')
    semantic_reads.clear()
    flow.verify_batch_unit(p['scenarios'][0],without_reader,out,r,native_read=True)
    assert semantic_reads==[str(out/u['output'])] and not without_reader['verified']
    flow.verify_batch_counters(p,[dict(units=[u])],dict(b.snapshot(),candidates=1),children)
    bad=copy.deepcopy(u);bad['segments'][0]['accepted_after']=18
    with pytest.raises(ValueError):flow.verify_batch_unit(p['scenarios'][0],bad,out,r,native_read=True)


def test_late_launch_and_reader_budget_keep_segment(monkeypatch,tmp_path):
    run,b,calls,reads,children=scheduling(monkeypatch,tmp_path)
    original=flow.launch
    def failed(kind,*args,**kwargs):
        value,receipt=original(kind,*args,**kwargs)
        if kind=='trajectory':raise OSError('late collection error')
        return value,receipt
    monkeypatch.setattr(flow,'launch',failed)
    progress={}
    with pytest.raises(OSError):run('late',96,progress)
    assert len(progress['segments'])==1 and progress['segments'][0]['child']==children[0]
    assert progress['output']=='late' and progress['segments'][0]['accepted_after']==96
    assert b.counts['steps_charged']==b.counts['accepted_steps']==96
    def refused_reader(kind,*args,**kwargs):
        if kind=='read':raise flow.BudgetStop('child_does_not_fit_remaining_wall_budget')
        return original(kind,*args,**kwargs)
    monkeypatch.setattr(flow,'launch',refused_reader)
    progress={}
    with pytest.raises(flow.BudgetStop):run('no-reader',96,progress)
    assert len(progress['segments'])==1 and 'reader' not in progress['segments'][0]
    assert progress['output']=='no-reader'
    assert b.counts['steps_charged']==b.counts['accepted_steps']==192


@pytest.mark.parametrize('fit', [None, [], 'scalar', 1, True])
def test_non_mapping_fit_rejected_before_science(job, monkeypatch, fit):
    change(job, lambda r: (r.update(fit=fit), r['scenarios'][0].update(steps_per_call=96)))
    monkeypatch.setattr(flow.td, 'input_impedance', lambda *a, **k: pytest.fail('science'))
    out = job.parent/'absent'
    with pytest.raises(ValueError, match='certified_scalar_v1'):
        flow.run(job, out, dry_run=True)
    assert not out.exists()


@pytest.mark.parametrize('key,value', [('exit_code', False), ('exit_code', True),
    ('launched', 1), ('launched', None), ('steps_charged', False), ('reaped', 1)])
@pytest.mark.parametrize('kind', ['trajectory', 'read'])
def test_batch_receipt_exact_types(kind, key, value):
    receipt = dict(kind=kind, exit_code=0, launched=True, reaped=True, steps_charged=0)
    receipt[key] = value
    with pytest.raises(ValueError, match='receipt'):
        flow.batch_receipt(receipt, kind)


@pytest.mark.parametrize('content', [b'', b'\n', b'{', b'[]', b'null', b'\xff', b'{"a":1e400}',
                                    b'{"a":0,"a":1}', b'['*2000])
def test_reader_logs_refused_without_unbounded_read(tmp_path, content):
    path = tmp_path/'reader.log'; path.write_bytes(content)
    with pytest.raises(ValueError):
        flow.batch_reader_response(tmp_path, dict(log=path.name))


def test_reader_log_native_ceiling_before_read(tmp_path, monkeypatch):
    path = tmp_path/'reader.log'
    with path.open('wb') as stream:
        stream.truncate(8*flow.MIB+1)
    monkeypatch.setattr(Path, 'open', lambda *a, **k: pytest.fail('oversized allocation'))
    with pytest.raises(ValueError, match='quota'):
        flow.batch_reader_response(tmp_path, dict(log=path.name))


def test_not_started_reservation_and_no_result_no_reader(monkeypatch, tmp_path):
    run, budget, calls, reads, children = scheduling(monkeypatch, tmp_path)
    def unstarted(kind, task, out, budget, children, *, seconds, output_bytes, steps=0):
        budget.reserve(kind, seconds, output_bytes, steps)
        receipt = dict(kind=kind, launched=False, reaped=True, exit_code=None,
                       timeout_or_error='not started', steps_charged=0)
        children.append(receipt)
        return dict(accepted_steps=96), receipt  # Unbacked response cannot create progress.
    monkeypatch.setattr(flow, 'launch', unstarted)
    unit = run('not-started', 96)
    assert not unit['verified'] and unit['segments'][0]['accepted_after'] == 0
    assert budget.reserved['steps_charged'] == 96 and budget.counts['steps_charged'] == 0
    assert budget.counts['accepted_steps'] == budget.counts['reader_units'] == 0
    assert len(children) == 1 and reads == [0] and not calls
    monkeypatch.setattr(flow.rtreport, 'read_result', lambda *a, **k: pytest.fail('invented read'))
    monkeypatch.setattr(flow.rt, 'check_inputs', lambda *a, **k: None)
    local = tmp_path/'not-started'
    rp = report.read_json(local/'plan.json')
    experiment = dict(case=dict(kind='saved_state'), budgets=dict(chunk_steps=48), plateaus=[dict(steps=113)])
    rp.update(request=experiment, resume=None);write(local/'plan.json', rp)
    assert flow.verify_batch_unit(dict(steps_per_call=96), unit, tmp_path, experiment, native_read=True) is None


def test_reader_boolean_accepted_one_cannot_complete(monkeypatch, tmp_path):
    run, budget, calls, reads, children = scheduling(monkeypatch, tmp_path)
    original = flow.launch
    def reader_bool(kind, *a, **k):
        value, receipt = original(kind, *a, **k)
        if kind == 'read': value['accepted_steps'] = True
        return value, receipt
    monkeypatch.setattr(flow, 'launch', reader_bool)
    with pytest.raises(ValueError, match='accepted_steps'):
        run('reader-bool', 96)
    assert calls == [96] and budget.counts['steps_charged'] == 96


@pytest.mark.parametrize('attack', ['reader_accepted', 'result_accepted', 'quota', 'before', 'after'])
def test_boolean_one_rejected_in_acquired_unit(tmp_path, monkeypatch, attack):
    # Minimal trace double isolates bool == 1; no scientific calculation.
    expected=dict(case=dict(kind='saved_state'),budgets=dict(chunk_steps=1),plateaus=[dict(steps=1)])
    rp=dict(request=expected,steps_this_run=1,accepted_before=0,resume=None,context_sha256='context')
    child=dict(kind='trajectory',launched=True,reaped=True,exit_code=0,steps_charged=1,timeout_or_error=None)
    reader=dict(kind='read',launched=True,reaped=True,exit_code=0,steps_charged=0,timeout_or_error=None,log='reader.log')
    segment=dict(output='native',quota=1,accepted_before=0,accepted_after=1,child=child,reader=reader)
    unit=dict(verified=True,segments=[segment],output='native',context_sha256='context')
    result=dict(plan=rp,accepted_steps=1,new_steps_this_run=1,checkpoint={},sums={},events=[])
    response=dict(read_confirmed=True,accepted_steps=1,context_sha256='context')
    if attack=='reader_accepted':response['accepted_steps']=True
    elif attack=='result_accepted':result['accepted_steps']=True
    else:segment[dict(quota='quota',before='accepted_before',after='accepted_after')[attack]]=False if attack=='before' else True
    local=tmp_path/'native';local.mkdir();write(local/'plan.json',rp);write(local/'result.json',result)
    write(tmp_path/'reader.log',response)
    monkeypatch.setattr(flow.rt,'check_inputs',lambda *a:None)
    monkeypatch.setattr(flow.rtreport,'verify_reference',lambda *a:None)
    monkeypatch.setattr(flow.rtreport,'inspect_checkpoint',lambda *a,**k:[(None,dict(context_sha256='context',experiment=dict(payload=dict(accepted_steps=1,sums={},events=[]))))])
    monkeypatch.setattr(report,'batch_artifact_pins',lambda *a:{})
    monkeypatch.setattr(flow.rtreport,'read_result',lambda *a,**k:pytest.fail('invalid trace reached semantic read'))
    with pytest.raises(ValueError):
        flow.verify_batch_unit(dict(steps_per_call=1),unit,tmp_path,expected,native_read=True)
