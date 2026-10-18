"""Real portable CLI, native geometric screening only: zero fits/played steps."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from didgeridoo_optimizer.tests.test_seeded_played_search import ROOT, EXAMPLE, job, write, change
from didgeridoo_optimizer.pipeline import played_search as flow
from didgeridoo_optimizer.reporting import played_search as report

ENV={**os.environ, 'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
     'NUMEXPR_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}


def cli(*args):
    return subprocess.run([sys.executable,'-B','-m','tools.played_search',*map(str,args)],cwd=ROOT,
                          env=ENV,capture_output=True,text=True,timeout=30)


def test_real_cli_preflight_refusal_and_copied_entry(job,tmp_path):
    out=tmp_path/'absent'
    p=cli('--job',job,'--output-dir',out,'--dry-run')
    assert p.returncode==0,p.stdout+p.stderr
    value=json.loads(p.stdout);assert value['ok'] and not out.exists()
    copied=tmp_path/'copied.py';shutil.copyfile(ROOT/'tools/played_search.py',copied)
    p=subprocess.run([sys.executable,'-B',str(copied),'--job',str(job),'--output-dir',str(out),'--dry-run'],
        cwd=tmp_path,env={**ENV,'PYTHONPATH':str(ROOT)},capture_output=True,text=True,timeout=30)
    assert p.returncode==2 and 'Copied' in p.stdout and not out.exists()
    change(job,lambda r:r['search']['starts']['profiles'][0].update(design='/not/relative.json'))
    p=cli('--job',job,'--output-dir',out,'--dry-run')
    assert p.returncode==2 and not out.exists()


@pytest.fixture(scope='module')
def screened(tmp_path_factory):
    root=tmp_path_factory.mktemp('native-screened');out=root/'result'
    p=cli('--job',EXAMPLE/'job-filter.yaml','--output-dir',out)
    (root/'command.stdout.json').write_text(p.stdout);(root/'command.stderr.txt').write_text(p.stderr)
    assert p.returncode==0,p.stdout+p.stderr
    value=json.loads(p.stdout);assert value['ok'] and not value['conforming']
    assert value['counters']['fits']==value['counters']['trajectories']==value['counters']['steps_charged']==0
    assert value['counters']['candidates']==value['counters']['static_units']==2
    return out


def test_real_native_screening_read_and_all_checkpoints(screened,tmp_path):
    p=cli('--read-result',screened)
    assert p.returncode==0,p.stdout+p.stderr
    result=json.loads(p.stdout)['result']
    assert [r['start_origin']['id'] for r in result['search']['history']]==['short_wide','long_wide']
    assert result['search']['termination']=='evaluation_only'
    for summary in result['search']['history']:
        row=report.read_json(screened/summary['file'])
        assert row['status']=='violated' and row['max_violation_normalized'] is None
        assert row['errors']==['proved_static_violation; played_not_evaluated']
        assert all(r['value_si'] is None and r['status']=='not_evaluated' for r in row['criteria'] if r['kind']=='played')
        assert row['geometry_valid'] is True and row['units_complete'] is False
        static=report.read_json(screened/Path(summary['file']).parent/'static.json')
        assert static['details']['nominal']['geometry_state_acquisition']['spectra']==[]
    assert all(c['reaped'] and c['exit_code']==0 for c in report.read_json(screened/'execution.json')['children'])
    # Re-read every intermediate checkpoint in isolation; no new science.
    for checkpoint in sorted(screened.glob('checkpoint-*.json')):
        view=tmp_path/checkpoint.stem;shutil.copytree(screened,view)
        for other in view.glob('checkpoint-*.json'):
            if other.name!=checkpoint.name:other.unlink()
        assert report.read_checkpoint(view)['plan']['starts']


def rehash(out):
    # Adversarial fixture only: refresh all envelopes, never production artifacts.
    excluded={'manifest.json','execution.json','execution.closed.json','execution.completed.json'}
    manifest={p.relative_to(out).as_posix():report.file_sha256(p,maximum=800*flow.MIB)
              for p in sorted(out.rglob('*')) if p.is_file() and p.name not in excluded and not any(x.startswith('.pending') for x in p.relative_to(out).parts)}
    write(out/'manifest.json',manifest)
    execution=report.read_json(out/'execution.json');execution['manifest_sha256']=report.file_sha256(out/'manifest.json');write(out/'execution.json',execution)
    write(out/'execution.closed.json',dict(execution_sha256=report.file_sha256(out/'execution.json')))
    write(out/'execution.completed.json',dict(schema='dcalc.played_search.completion.v1',execution_sha256=report.file_sha256(out/'execution.json'),closure_sha256=report.file_sha256(out/'execution.closed.json')))


@pytest.mark.parametrize('attack',['id','vector','profile','mode','decision','candidate_origin','candidate_vector','candidate_bool','embedded_bool','csv','supplied','native'])
def test_rehashed_copies_cannot_forge_seed_correspondence(screened,tmp_path,attack):
    out=tmp_path/'forged';shutil.copytree(screened,out)
    result=report.read_json(out/'result.json');plan=result['plan']
    if attack in ('id','vector','profile','mode'):
        start=plan['starts']['selected'][0]
        if attack=='id':start['origin']['id']='invented'
        if attack=='vector':start['variables_si'][0]=.92
        if attack=='profile':start['native_design']['segments'][0]['length_cm']=92.
        if attack=='mode':plan['job']['search']['starts']['mode']='poll'
        plan['context_sha256']=flow.digest({k:v for k,v in plan.items() if k!='context_sha256'})
        write(out/'plan.json',plan)
    elif attack=='embedded_bool':
        result['plan']=copy.deepcopy(plan)
        result['plan']['job']['search']['stop_on_witness']=0
    elif attack=='candidate_bool':
        path=out/result['search']['history'][0]['file'];row=report.read_json(path)
        row['candidate']=True;write(path,row)
    elif attack=='decision':result['search']['decisions'][1]['start_origin']['id']='invented'
    elif attack in ('candidate_origin','candidate_vector'):
        summary=result['search']['history'][0];row=report.read_json(out/summary['file'])
        if attack=='candidate_origin':row['start_origin']['id']='long_wide'
        else:row['variables_si'][0]=.92
        write(out/summary['file'],row)
        updated=report.summary(row);result['search']['history'][0]=updated
        if result['search']['best']['candidate']==1:result['search']['best']=updated
    elif attack=='csv':
        path=out/'candidates.csv';path.write_text(path.read_text().replace('short_wide','forged_id'))
    elif attack=='supplied':
        with (out/'starts/short_wide.supplied.json').open('ab') as stream:stream.write(b'\n')
    elif attack=='native':
        path=out/'starts/short_wide.native.json';change(path,lambda d:d['segments'][0].update(length_cm=92.))
    write(out/'result.json',result);rehash(out)
    got=report.read_result(out)
    assert not got['ok'] and got['status']=='unconfirmed',got


def test_rehashed_checkpoint_origin_and_decisions(screened,tmp_path):
    for attack in ('origin','decisions'):
        out=tmp_path/attack;shutil.copytree(screened,out)
        path=sorted(out.glob('checkpoint-*.json'))[-1];record=report.read_json(path)
        if attack=='origin':record['payload']['history'][0]['start_origin']['id']='invented'
        else:record['payload']['decisions']=[]
        record['sha256']=flow.digest(record['payload']);write(path,record)
        with pytest.raises(ValueError):report.read_checkpoint(out)


def test_changed_profile_between_preflight_and_run_is_refused_before_science(job,tmp_path,monkeypatch):
    original=flow.preflight
    def changed(*args,**kwargs):
        p=original(*args,**kwargs)
        with (job.parent/'short_wide.json').open('ab') as stream:stream.write(b'\n')
        return p
    monkeypatch.setattr(flow,'preflight',changed)
    monkeypatch.setattr(flow,'launch',lambda *a,**k:pytest.fail('changed source must not launch science'))
    with pytest.raises(ValueError,match='bytes changed'):flow.run(job,tmp_path/'refused')
