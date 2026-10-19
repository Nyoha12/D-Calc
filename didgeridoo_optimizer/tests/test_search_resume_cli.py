"""Fresh-process API/CLI proofs; native geometry screening, zero tube fits."""
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
from didgeridoo_optimizer.reporting import played_search as report


def invoke(job, out, *args, api=False):
    if api:
        options = dict(session='--session' in args)
        for name,key in [('--resume','resume'),('--pause-after','pause_after')]:
            if name in args:
                v=args[args.index(name)+1]; options[key]=int(v) if key=='pause_after' else str(v)
        code='import json,sys; from didgeridoo_optimizer.tests.test_search_resume import install_local_screening; install_local_screening(); from didgeridoo_optimizer.pipeline.played_search import run; r=run(sys.argv[1],sys.argv[2],**json.loads(sys.argv[3])); print(json.dumps(r)); sys.exit(3 if r.get("status")=="paused" else 0 if r.get("ok") else 1)'
        cmd=[sys.executable,'-B','-c',code,str(job),str(out),json.dumps(options)]
    else:
        cmd=[sys.executable,'-B','-m','tools.played_search','--job',str(job),'--output-dir',str(out),*map(str,args)]
    p=subprocess.run(cmd,cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=90)
    assert p.stdout,p.stderr
    return p,json.loads(p.stdout)


def scientific(out):
    r=report.read_json(out/'result.json')
    rows=[report.read_json(out/x['file']) for x in r['search']['history']]
    return dict(search=r['search'],rows=rows,conforming=r['conforming'],covered=r['request_fully_covered'])


@pytest.mark.parametrize('mode,api',[('evaluate_only',False),('poll',True)])
def test_fresh_no_session_whole_and_two_pauses(job,tmp_path,mode,api,record_property):
    # All proposals violate a real geometric hard, preventing any tube fit.
    change(job.parent/'request.yaml',lambda r:r['criteria'][0].update(bounds=[dict(value=88.,unit='cm'),dict(value=89.,unit='cm')]))
    if mode=='poll':
        # Explicit static-only fixture, defined before any run. Unacquired
        # played observations cannot certify feasibility or rank as hard rows.
        # Keep native screening and verdicts; do not invent played results.
        change(job.parent/'experiment.json',lambda r:[c.update(role='observe') for c in r['criteria']])
    third=json.loads((job.parent/'long_wide.json').read_text());third['segments'][0]['length_cm']=95.
    write(job.parent/'third.json',third)
    change(job,lambda r:(r['search']['starts'].update(mode=mode,profiles=r['search']['starts']['profiles']+[
        dict(id='duplicate',design='short_wide.json'),dict(id='third',design='third.json')]),
        r['search'].update(normalized_steps=[.5,.25],directions=[1,-1]),
        r['budgets'].update(candidates=8 if mode=='poll' else 4)))
    # Persist all prepared inputs before the first fresh process, and ensure
    # every continuation uses these exact bytes (no retargeting between runs).
    inputs={p.name:report.file_sha256(p) for p in job.parent.iterdir() if p.is_file()}
    write(tmp_path/'fixture-prepared.json',dict(prepared_ns=time.time_ns(),mode=mode,
        nature='native geometric rejection; no played conformity',inputs=inputs))
    outputs=[]
    for label,args in [('plain',()),('whole',('--session',)),('split',('--session','--pause-after',1))]:
        out=tmp_path/label;p,r=invoke(job,out,*args,api=api)
        assert p.returncode==(3 if label=='split' else 0),(p.stdout,p.stderr)
        if label=='split':
            hashes={str(x.relative_to(out)):report.file_sha256(x) for x in (out/'candidate-001').rglob('*') if x.is_file()}
            assert not (out/'result.json').exists() and r['counters']['static_units']==1
            first=r['resume_point']
            # Dry run is read-only; it cannot claim a native semantic read.
            before=report.session_inventory(out)
            q,v=invoke(job,out,'--resume',first,'--dry-run')
            assert q.returncode==0 and v['status']=='planned' and report.session_inventory(out)==before
            p,r=invoke(job,out,'--resume',first,'--pause-after',1,api=api)
            assert p.returncode==3,(p.stdout,p.stderr)
            assert r['counters']['static_units']==2
            stale,v=invoke(job,out,'--resume',first)
            assert stale.returncode==2 and 'Stale' in v['reason']
            p,r=invoke(job,out,'--resume',r['resume_point'],api=api)
            assert p.returncode==0,(p.stdout,p.stderr)
            assert all(report.file_sha256(out/name)==sha for name,sha in hashes.items())
        outputs.append(scientific(out))
        read=subprocess.run([sys.executable,'-B','-m','tools.played_search','--read-result',str(out)],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=90)
        assert read.returncode==0,(read.stdout,read.stderr)
        if label!='plain':
            again,v=invoke(job,out,'--resume',r['resume_point'])
            assert again.returncode==2 and 'Terminal' in v['reason']
        counters=report.read_json(out/'result.json')['counters']
        assert counters['candidates']==counters['static_units'] and counters['fits']==counters['steps_charged']==0
        assert counters['candidates']==(8 if mode=='poll' else 3)
        assert counters['trajectories']==counters['accepted_steps']==counters.get('fit_attempts',0)==0
        record_property(label+'_candidates',counters['candidates'])
    assert all(report.file_sha256(job.parent/name)==sha for name,sha in inputs.items())
    assert outputs[0]==outputs[1]==outputs[2]
    assert not outputs[0]['conforming'] and not outputs[0]['covered']
    decisions=outputs[0]['search']['decisions']
    assert any(d['action']=='skip_duplicate' for d in decisions)
    if mode=='poll':
        assert any(r['stage']=='coordinate_poll' for r in outputs[0]['rows'])
        assert any(d['action']=='skip_bounds' for d in decisions)
        assert sum(d['action']=='select' for d in decisions)>1
        assert any(d['action']=='select' and d['stage']=='coordinate_poll' for d in decisions)
        # At step .5 the short_wide anchor improves from .93 to .91 m.
        # Its .25 diameter neighbor must therefore use .91, not the old .93.
        assert any(r['stage']=='coordinate_poll' and r['start_origin']['id']=='short_wide'
                   and r['variables_si']==pytest.approx([.91,.03375]) for r in outputs[0]['rows'])
        assert any(d['action']=='skip_bounds' and d['step']==.25 and d['variable']=='length'
                   and d['variables_si']==pytest.approx([.90,.03125]) for d in decisions)
        record_property('poll_decisions',json.dumps(decisions))


def test_cli_strict_modes_before_destination(job,tmp_path):
    for args in [('--pause-after','1'),('--session','--pause-after','0'),('--session','--pause-after','1.0'),
                 ('--session','--resume','absent'),('--session','--pause-after','True')]:
        p=subprocess.run([sys.executable,'-B','-m','tools.played_search','--job',str(job),'--output-dir',str(tmp_path/'absent'),*args],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=30)
        assert p.returncode==2 and not (tmp_path/'absent').exists()


def native_fixture_process(job, out, options):
    """Fresh-process routing fixture, NOT an instrument fit.

    Only geometry preparation/fit receipts use the existing synthetic_bundle
    oracle. Static geometry, native advance/coupling/checkpoints, trajectory
    calls and all semantic readers are real, routed in the fresh owning process.
    The six historical suites separately test child process supervision. Each candidate has its own
    geometry association; its two scenarios share one fit and fit reader.
    """
    from unittest.mock import patch
    from contextlib import ExitStack
    from didgeridoo_optimizer.pipeline import played_search as flow
    from didgeridoo_optimizer.pipeline import geometry_state as geometry
    from didgeridoo_optimizer.reporting import geometry_state as gr
    from didgeridoo_optimizer.tests import test_geometry_state as fixture
    prepared = {}; calls = []; step_calls = []; active_output = [None]
    preflight = geometry.preflight; mkdir = Path.mkdir
    json_new = gr.json_new; write_json = report.write_json; close = gr.close
    launch = flow.launch
    def receipt(kind, index):
        r=fixture._receipt(played=True,reader=kind=='read')
        r.update(kind=kind,log=f'child-{index:04d}.log')
        return r
    pending_children = [None]
    def prepare(config, design, recipe, dest=None):
        p=preflight(config,design,recipe,dest)
        if dest is None:return p
        dest=Path(dest); children=pending_children[0]
        fit_receipt=receipt('fit',len(children)+1); read_receipt=receipt('read',len(children)+2)
        for child in (fit_receipt,read_receipt): (dest.parent.parent/child['log']).write_bytes(b'')
        recipe_path=Path(job).parent/'fixture-recipe.json'
        if not recipe_path.exists():write(recipe_path,recipe)
        class Example:
            def __truediv__(self,name):
                return {'config.yaml':Path(config),'design.json':Path(design),'recipe.json':recipe_path}[name]
        class FixturePaths:
            def __truediv__(self,name):
                return {'synthetic':dest,'design.json':Path(design)}[name]
        with patch.object(geometry,'preflight',preflight),patch.object(fixture,'EXAMPLE',Example()),patch.object(
                fixture,'_receipt',lambda **kw:read_receipt if kw.get('reader') else fit_receipt):
            bundle=fixture.synthetic_bundle.__wrapped__(FixturePaths())
        prepared[dest]=(fit_receipt,read_receipt)
        return report.read_json(bundle/'plan.json')
    def directory(path,*a,**k):
        if path in prepared:return None
        return mkdir(path,*a,**k)
    def put(path,value,*a,**k):
        if Path(path).parent in prepared and Path(path).name in ('plan.json','child.json'):
            assert report.read_json(path)==value
            return None
        return write_json(path,value,*a,**k)
    def new(path,value):
        if Path(path).parent in prepared and Path(path).name=='plan.json':
            assert report.read_json(path)==value;return None
        return json_new(path,value)
    def fit_close(dest,child):
        if Path(dest) in prepared:
            assert prepared[Path(dest)][0]==child;return None
        return close(dest,child)
    def routed(kind,task,root,budget,children,**kw):
        pending_children[0]=children
        if kind=='fit' or task.get('kind')=='read_geometry':
            dest=Path(task['plan']['output'] if kind=='fit' else task['output'])
            budget.reserve(kind,kw['seconds'],kw['output_bytes'],kw.get('steps',0));budget.launched(kind,0)
            child=copy.deepcopy(prepared[dest][kind=='read'])
            if kind=='fit':value=dict(ok=True,synthetic_fixture=True)
            else:value=gr.read_result(dest,config=task['config'],design=task['design'],recipe=task['recipe'])
            children.append(child)  # Empty logs belong to the explicit synthetic receipts.
        else:
            import time
            budget.reserve(kind,kw['seconds'],kw['output_bytes'],kw.get('steps',0));budget.launched(kind,kw.get('steps',0))
            started=time.monotonic();active_output[0]=task.get('plan',{}).get('output')
            value=flow.rt.calculate(task['plan']) if kind=='trajectory' else flow.worker(task)
            child=dict(kind=kind,pid=os.getpid(),exit_code=0,launched=True,reaped=True,
                timeout_or_error=None,steps_charged=kw.get('steps',0),seconds=time.monotonic()-started,
                log=f'child-{len(children)+1:04d}.log')
            (Path(root)/child['log']).write_text(json.dumps(value)+'\n');children.append(child)
            active_output[0]=None
        calls.append(dict(kind=kind,task=task.get('kind'),output=task.get('output',task.get('plan',{}).get('output')),
                          steps=kw.get('steps',0),log=child['log']))
        return value,child
    # The first static launch supplies the shared children object before fit prep.
    from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
    native_step=SimultaneousCoupling.step
    def counted_step(self,*a,**k):
        step_calls.append(active_output[0]);return native_step(self,*a,**k)
    with ExitStack() as stack:
        stack.enter_context(patch.object(SimultaneousCoupling,'step',counted_step))
        for obj,name,fn in [(geometry,'preflight',prepare),(Path,'mkdir',directory),
                (gr,'json_new',new),(gr,'close',fit_close),(report,'write_json',put),(flow,'launch',routed)]:
            stack.enter_context(patch.object(obj,name,fn))
        result=flow.run(job,out,**options)
    return dict(result=result,calls=calls,step_calls=step_calls,pid=os.getpid(),fixture='synthetic_bundle; synthetic fit targets/receipts only')



def scientific_experiment(envelope, out):
    """Separate verified path-bound identities from exact scientific state.

    Native readers verify the original hashes in each bundle. These identity
    hashes include the different output paths; none is rewritten on disk.
    """
    p=copy.deepcopy(envelope['payload'])
    p.pop('request_sha256')
    for name in ('experiment_initial','native'):
        p[name]=p[name]['payload']
        p[name]['identity'].pop('model_sha256')
    for name in ('context_sha256','model_sha256'):p['source'].pop(name)
    for name in ('design','evidence','realization'):
        p['request']['case'][name]='@OUTPUT/'+str(Path(p['request']['case'][name]).relative_to(out))
    return p


def test_native_two_candidates_shared_fit_fresh_resume(job,tmp_path,record_property):
    import numpy as np
    from didgeridoo_optimizer.pipeline import played_search as flow
    # Passing geometric hard: the actual saved-state dynamics now run.
    change(job.parent/'request.yaml',lambda r:r['criteria'][0].update(bounds=[dict(value=90.,unit='cm'),dict(value=96.,unit='cm')]))
    change(job.parent/'experiment.json',lambda r:(r['plateaus'][0].update(steps=80,duration_s=80/12000),r['windows'][0].update(stop_step=80)))
    change(job,lambda r:(r['scenarios'].append(dict(r['scenarios'][0],id='second')),
        [s.update(steps_per_call=96) for s in r['scenarios']],r['budgets'].update(steps=320,trajectories=4)))
    code='import json,sys; from didgeridoo_optimizer.tests.test_search_resume_cli import native_fixture_process; print(json.dumps(native_fixture_process(sys.argv[1],sys.argv[2],json.loads(sys.argv[3]))))'
    def fresh(out,**opts):
        p=subprocess.run([sys.executable,'-B','-c',code,str(job),str(out),json.dumps(opts)],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=150)
        assert p.returncode==0,p.stdout+p.stderr
        v=json.loads(p.stdout);assert v['result']['ok'],v
        return v
    whole=tmp_path/'whole'; split=tmp_path/'split'
    a=fresh(whole,session=True)
    first=fresh(split,session=True,pause_after=1)
    assert first['result']['status']=='paused'
    hashes={str(p.relative_to(split)):report.file_sha256(p) for p in (split/'candidate-001').rglob('*') if p.is_file()}
    second=fresh(split,resume=first['result']['resume_point'])
    assert first['pid']!=second['pid'] and second['result']['status']=='complete'
    assert all(report.file_sha256(split/p)==sha for p,sha in hashes.items())
    assert [c['kind'] for c in first['calls']]==['static','fit','read','trajectory','read','trajectory','read']
    assert [c['kind'] for c in second['calls']]==['read','static','fit','read','trajectory','read','trajectory','read']
    assert sum(c['steps'] for c in second['calls'])==160
    assert len(first['step_calls'])==len(second['step_calls'])==160
    assert all('candidate-002' in str(p) for p in second['step_calls'])
    assert all('candidate-001' not in str(c['output']) for c in second['calls'])
    for out in (whole,split):
        p=subprocess.run([sys.executable,'-B','-m','tools.played_search','--read-result',str(out)],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=90)
        assert p.returncode==0,p.stdout+p.stderr
        result=report.read_json(out/'result.json');c=result['counters']
        assert c['candidates']==c['fits']==c['fit_attempts']==2
        assert c['trajectories']==4 and c['steps_charged']==c['accepted_steps']==320
        assert c['reader_units']==(6 if out==whole else 7)
        assert c['reserved']==dict(fits=2,trajectories=4,steps_charged=320,children=14 if out==whole else 15)
        assert 'reprise globale unsupported' not in (out/'summary.txt').read_text()
    def native(out):
        result=report.read_json(out/'result.json'); answer=[]
        for summary in result['search']['history']:
            row=report.read_json(out/summary['file']); units=[]
            for unit in row['units']:
                r=report.read_json(out/unit['output']/'result.json')
                chain=flow.rtreport.inspect_checkpoint(r['checkpoint']['path'])
                units.append((r['sums'],r['events'],r['analysis']['criteria'],scientific_experiment(chain[-1][1]['experiment'],out),flow.rtreport.load_series(chain)))
            answer.append(({k:v for k,v in summary.items() if k!='units'},row['criteria'],units))
        return answer
    for (summary1,criteria1,units1),(summary2,criteria2,units2) in zip(native(whole),native(split)):
        assert summary1==summary2 and criteria1==criteria2
        for u,v in zip(units1,units2):
            assert u[:4]==v[:4]
            assert u[4].keys()==v[4].keys()
            for key in u[4]:assert np.array_equal(u[4][key],v[4][key]),key
    assert second['result']['counters']['elapsed_seconds']>first['result']['counters']['elapsed_seconds']+15
    # Disposable native evidence attacks, including a rehashed session envelope.
    tip=Path(second['result']['resume_point']); envelope=report.read_json(tip)
    unit=report.read_json(split/'candidate-001/candidate.json')['units'][0]
    checkpoint=Path(report.read_json(split/unit['output']/'result.json')['checkpoint']['path'])
    for target in (split/'candidate-001/fit-nominal/realization/model.json', checkpoint):
        saved=target.read_bytes();point_bytes=tip.read_bytes()
        try:
            target.write_bytes(saved+b'\n')
            forged=copy.deepcopy(envelope);payload=forged['payload']
            payload['files'][target.relative_to(split).as_posix()]=report.file_sha256(target)
            forged['sha256']=flow.digest(payload);write(tip,forged)
            # boundary is the same inode; its checksum also changes deliberately.
            value=report.read_session(split)
            assert not value['ok']
        finally:target.write_bytes(saved);tip.write_bytes(point_bytes)
    record_property('fresh_process_native_calls',json.dumps(dict(whole=a,first=first,second=second)))
