"""Public saved-context adapter fixtures are metadata stubs, never fit validation."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from didgeridoo_optimizer.tests.test_paired_onset_cli import saved_plan
from didgeridoo_optimizer.tests.test_register_target import request
from didgeridoo_optimizer.pipeline import register_target as workflow
from didgeridoo_optimizer.reporting import register_target as report
from didgeridoo_optimizer.nonlinear import register_target as core
from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator,digest

ROOT=Path(__file__).resolve().parents[2]
ENV={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}


@pytest.fixture
def plan(saved_plan):
    path,old=saved_plan;r=request(fs=12000,steps=96);r['case']=dict(old['cases'][0],kind='saved')
    r['observation'].update(frame_steps=3600,hop_steps=1800)
    r['plateaus'][0].update(steps=48,duration_s=.004)
    r['plateaus'].append(dict(r['plateaus'][0],id='b',resonance_hz=100.,pressure_pa=1700.))
    r['windows'][0]['stop_step']=48
    path.write_text(json.dumps(r));return path,r


def cli(path,out,*args):
    return subprocess.run([sys.executable,'-B','-m','tools.register_target','--plan',str(path),'--output-dir',str(out),*args],
        cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=50)


def reader(out):
    return subprocess.run([sys.executable,'-B','-c',
        'import json,sys; from didgeridoo_optimizer.reporting.register_target import read_result; print(json.dumps(read_result(sys.argv[1])))',str(out)],
        cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=50)


def test_dryrun_no_science_no_destination_and_immutable(plan,tmp_path,monkeypatch):
    path,r=plan;out=tmp_path/'out'
    def no(*a,**kw):pytest.fail('Scientific computation/write during dry-run')
    monkeypatch.setattr(np,'roots',no);monkeypatch.setattr(np.linalg,'eigvals',no)
    monkeypatch.setattr(PassiveResonator,'load',no);monkeypatch.setattr(report,'write_json',no)
    p=workflow.preflight(path,out);v=p.as_dict();v['request']['rho_kg_m3']=9
    assert p.as_dict()['request']['rho_kg_m3']==1.204 and not out.exists()
    assert workflow.run(path,out,dry_run=True)['ok'] and not out.exists()


def test_real_cli_partial_resume_fresh_reader_and_exact_native(plan,tmp_path):
    path,r=plan;out=tmp_path/'full';p=cli(path,out)
    assert p.returncode==0,p.stdout+p.stderr
    fresh=reader(out);assert fresh.returncode==0,fresh.stderr
    result=json.loads(fresh.stdout);assert result['ok'],result
    full=result['result'];cp=full['checkpoint']['path'];chain=report.inspect_checkpoint(cp);a=report.load_series(chain)
    assert a['states'].shape==(97,4) and len(full['events'])==1
    p=cli(path,tmp_path/'partial','--steps-this-run','48');assert p.returncode==1,p.stdout
    partial=report.read_json(tmp_path/'partial'/'result.json');checkpoint=partial['checkpoint']['path']
    p=cli(path,tmp_path/'resume','--resume',checkpoint);assert p.returncode==0,p.stdout+p.stderr
    resumed=report.read_result(tmp_path/'resume');assert resumed['ok'],resumed
    b=report.load_series(report.inspect_checkpoint(resumed['result']['checkpoint']['path']))
    for k in a:assert np.array_equal(a[k],b[k])
    assert resumed['result']['events']==full['events'] and resumed['result']['sums']==full['sums']
    assert resumed['result']['new_steps_this_run']==48 and resumed['result']['accepted_steps']==96


def test_imported_api_from_outside_checkout_and_copied_cli(plan,tmp_path):
    path,r=plan;env={**ENV,'PYTHONPATH':str(ROOT)}
    code='import sys; from didgeridoo_optimizer.pipeline.register_target import run; assert run(sys.argv[1],sys.argv[2],dry_run=True)["ok"]'
    p=subprocess.run([sys.executable,'-c',code,str(path),str(tmp_path/'api')],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    copied=tmp_path/'copied.py';shutil.copy(ROOT/'tools/register_target.py',copied)
    p=subprocess.run([sys.executable,str(copied),'--plan',str(path),'--output-dir',str(tmp_path/'copy'),'--dry-run'],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    assert p.returncode==2 and 'copied' in p.stdout and not (tmp_path/'copy').exists()


@pytest.mark.parametrize('text,suffix',[('{"x":1,"x":2}', '.json'),('a: 1\na: 2\n','.yaml'),('a: &a [*a]\n','.yaml'),('{"x":NaN}', '.json')])
def test_strict_reader_duplicate_cycle_nan(tmp_path,text,suffix):
    p=tmp_path/('bad'+suffix);p.write_text(text)
    with pytest.raises(ValueError):workflow.preflight(p,tmp_path/'none')
    assert not (tmp_path/'none').exists()


@pytest.mark.parametrize('mutation',['bool','quota','duration','fs','plateau_field','source','initial'])
def test_bad_plan_before_context(plan,tmp_path,monkeypatch,mutation):
    path,r=plan
    if mutation=='bool':r['plateaus'][0]['steps']=True
    if mutation=='quota':r['budgets']['new_steps']=10
    if mutation=='duration':r['plateaus'][0]['duration_s']=1.
    if mutation=='fs':r['case']['recipe']['sample_rate_hz']=24000
    if mutation=='plateau_field':r['plateaus'][0]['mass_kg']=2.
    if mutation=='source':r['source']='impedance'
    if mutation=='initial':r['initial']['kind']='R36'
    path.write_text(json.dumps(r))
    monkeypatch.setattr(workflow,'_case_context',lambda *a:pytest.fail('Too late'))
    with pytest.raises(ValueError):workflow.preflight(path,tmp_path/'bad')


@pytest.mark.parametrize('stage',['export','prepare','publish'])
def test_interrupted_export_closure_never_false_terminal(plan,tmp_path,monkeypatch,stage):
    path,r=plan;out=tmp_path/stage
    if stage=='export':
        p=workflow.preflight(path,out).as_dict();out.mkdir();report.write_json(out/'plan.json',p)
        monkeypatch.setattr(report,'export',lambda *a:(_ for _ in ()).throw(OSError('injected export failure')))
        with pytest.raises(OSError):workflow.calculate(p)
        assert list(out.glob('checkpoint-*.json'))
    else:
        name='prepare_completion' if stage=='prepare' else 'publish_completion'
        monkeypatch.setattr(report,name,lambda *a:(_ for _ in ()).throw(OSError('injected closure failure')))
        with pytest.raises(OSError):workflow.run(path,out)
    assert not (out/'execution.completed.json').exists()
    assert report.read_result(out)['ok'] is False


def test_checkpoint_and_manifest_falsification(plan,tmp_path):
    path,r=plan;out=tmp_path/'out';p=cli(path,out);assert p.returncode==0,p.stdout
    result=report.read_json(out/'result.json');cp=Path(result['checkpoint']['path']);env=report.read_json(cp)
    env['payload']['experiment']['payload']['accepted_steps']+=1
    env['payload']['experiment']['sha256']=digest(env['payload']['experiment']['payload']);env['sha256']=digest(env['payload'])
    cp.write_text(json.dumps(env))
    with pytest.raises(ValueError):report.inspect_checkpoint(cp)
    assert not report.read_result(out)['ok']
    with pytest.raises(ValueError):workflow.preflight(path,tmp_path/'resume',resume=cp)


def test_changed_model_or_plan_refuses_resume(plan,tmp_path):
    path,r=plan;out=tmp_path/'partial';p=cli(path,out,'--steps-this-run','32');assert p.returncode==1,p.stdout
    cp=report.read_json(out/'result.json')['checkpoint']['path'];r['plateaus'][0]['pressure_pa']=1600.;path.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='Identical resume'):workflow.preflight(path,tmp_path/'resume',resume=cp)


@pytest.mark.parametrize('kind',['huge_shape','object_dtype','float_segments','fortran'])
def test_npz_hostile_header_before_allocation(tmp_path,monkeypatch,kind):
    import io,zipfile
    n,d=1,4
    shapes={'states.npy':(2,4),'times.npy':(2,),'values.npy':(1,len(core.COLUMNS)),'midpoint_times.npy':(1,),'segments.npy':(1,)}
    path=tmp_path/'hostile.npz'
    with zipfile.ZipFile(path,'w') as z:
        for name,shape in shapes.items():
            h=io.BytesIO();dtype='<i8' if name=='segments.npy' else '<f8';fortran=False;header_shape=shape
            if name=='states.npy' and kind=='huge_shape':header_shape=(10**12,4)
            if name=='states.npy' and kind=='object_dtype':dtype='|O'
            if name=='segments.npy' and kind=='float_segments':dtype='<f8'
            if name=='states.npy' and kind=='fortran':fortran=True
            np.lib.format.write_array_header_1_0(h,dict(descr=dtype,fortran_order=fortran,shape=header_shape))
            h.write(bytes(math_prod(shape)*8));z.writestr(name,h.getvalue())
    monkeypatch.setattr(np,'load',lambda *a,**kw:pytest.fail('Untrusted header allocated'))
    with pytest.raises(ValueError,match='header'):report._zip_budget(path,n,d)


def math_prod(shape):
    result=1
    for n in shape:result*=n
    return result


def test_empty_provenance_rejected(plan,tmp_path):
    path,r=plan;p=workflow.preflight(path,tmp_path/'out').as_dict();p['producer']['loaded_sources_sha256']={}
    p['context_sha256']=digest({k:v for k,v in p.items() if k!='context_sha256'})
    with pytest.raises(ValueError,match='source manifest'):workflow.check_inputs(p)


def test_checkpoint_stop_and_resume_after_acquired_chunk(plan,tmp_path,monkeypatch):
    path,r=plan;r['budgets']['chunk_steps']=32;path.write_text(json.dumps(r));out=tmp_path/'stopped'
    p=workflow.preflight(path,out).as_dict();out.mkdir();report.write_json(out/'plan.json',p)
    stopped=[];original=report.save_checkpoint
    def save(*args,**kwargs):
        result=original(*args,**kwargs)
        if args[2].accepted==32:stopped.append(True)
        return result
    monkeypatch.setattr(report,'save_checkpoint',save)
    result=workflow.calculate(p,stop=lambda:bool(stopped));assert not result['ok']
    cp=report.read_json(out/'result.json')['checkpoint']['path']
    result=cli(path,tmp_path/'resumed','--resume',cp);assert result.returncode==0,result.stdout
    verified=report.read_result(tmp_path/'resumed');assert verified['ok'],verified
    assert verified['result']['new_steps_this_run']==64 and len(verified['result']['events'])==1


def test_tampered_internal_times_and_reset_chain(plan,tmp_path):
    path,r=plan;out=tmp_path/'out';p=cli(path,out);assert p.returncode==0,p.stdout
    result=report.read_json(out/'result.json');chain=report.inspect_checkpoint(result['checkpoint']['path'])
    _,tip=chain[-1];sp=Path(tip['series']['file']['path'])
    with np.load(sp,allow_pickle=False) as z:arrays={k:z[k] for k in z.files}
    arrays['midpoint_times'][5]+=.00001
    with sp.open('wb') as f:np.savez(f,**arrays)
    tip['series']['file']['sha256']=report.file_sha256(sp)
    with pytest.raises(ValueError,match='time grid'):report.load_series(chain)


def test_late_cancellation_during_handler_restoration(plan,tmp_path,monkeypatch):
    import signal
    path,r=plan;out=tmp_path/'cancelled';native=signal.signal;installed={};fired=[]
    def instrument(sig,handler):
        if sig==signal.SIGTERM:
            if sig not in installed:installed[sig]=handler
            elif handler is not installed[sig] and not fired:
                fired.append(True);installed[sig](sig,None)
        return native(sig,handler)
    monkeypatch.setattr(signal,'signal',instrument)
    result=workflow.run(path,out);assert fired and not result['ok']
    assert not report.read_result(out)['ok']


def test_huge_npy_header_length_rejected_before_parser(tmp_path,monkeypatch):
    import io,zipfile
    n,d=1,4;path=tmp_path/'huge-header.npz'
    arrays=dict(states=np.zeros((2,4)),times=np.zeros(2),values=np.zeros((1,len(core.COLUMNS))),midpoint_times=np.zeros(1),segments=np.zeros(1,dtype=np.int64))
    with zipfile.ZipFile(path,'w') as z:
        for name,array in arrays.items():
            buf=io.BytesIO();np.save(buf,array,allow_pickle=False);raw=buf.getvalue()
            if name=='states':raw=b'\x93NUMPY\x02\x00'+(1000000000).to_bytes(4,'little')+bytes(array.nbytes)
            z.writestr(name+'.npy',raw)
    monkeypatch.setattr(np.lib.format,'read_array_header_2_0',lambda *a,**kw:pytest.fail('Oversized header reached parser'))
    with pytest.raises(ValueError,match='header budget'):report._zip_budget(path,n,d)


def test_missing_observation_artifact_not_confirmed(plan,tmp_path):
    path,r=plan;out=tmp_path/'out';p=cli(path,out);assert p.returncode==0,p.stdout
    manifest=report.read_json(out/'manifest.json');manifest.pop('window-hold.json')
    (out/'manifest.json').write_text(json.dumps(manifest))
    execution=report.read_json(out/'execution.json');execution['manifest_sha256']=report.file_sha256(out/'manifest.json')
    (out/'execution.json').write_text(json.dumps(execution))
    closed=dict(execution_sha256=report.file_sha256(out/'execution.json'));(out/'execution.closed.json').write_text(json.dumps(closed))
    marker=dict(schema='dcalc.register_target.completion.v1',execution_sha256=report.file_sha256(out/'execution.json'),closure_sha256=report.file_sha256(out/'execution.closed.json'))
    (out/'execution.completed.json').write_text(json.dumps(marker))
    result=report.read_result(out)
    assert not result['ok'] and 'observation' in result['reason']
