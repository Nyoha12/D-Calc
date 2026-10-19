"""Real CLI admission/screening and rehashed batch evidence refusal; zero fits."""
import copy
import json
import shutil

import pytest

from didgeridoo_optimizer.tests.test_seeded_played_search import job,change,write
from didgeridoo_optimizer.tests.test_seeded_played_search_cli import cli,rehash
from didgeridoo_optimizer.pipeline import played_search as flow
from didgeridoo_optimizer.reporting import played_search as report


def test_real_cli_preflight_screening_and_refusals(job,tmp_path):
    change(job,lambda r:r['scenarios'][0].update(steps_per_call=96))
    out=tmp_path/'result'
    planned=cli('--job',job,'--output-dir',out,'--dry-run')
    assert planned.returncode==0,planned.stdout+planned.stderr
    assert not out.exists()
    assert json.loads(planned.stdout)['plan']['scenarios'][0]['steps_per_call']==96
    ran=cli('--job',job,'--output-dir',out)
    assert ran.returncode==0,ran.stdout+ran.stderr
    read=cli('--read-result',out)
    assert read.returncode==0,read.stdout+read.stderr
    result=json.loads(read.stdout)['result']
    assert result['counters']['steps_charged']==result['counters']['trajectories']==0
    assert [x['start_origin']['id'] for x in result['search']['history']]==['short_wide','long_wide']
    for attack in ('quota','charged','accepted','calls','readers','reserved'):
        dest=tmp_path/attack;shutil.copytree(out,dest)
        value=report.read_json(dest/'result.json')
        if attack=='quota':
            value['plan']['scenarios'][0]['steps_per_call']=48
            value['plan']['context_sha256']=flow.digest({k:v for k,v in value['plan'].items() if k!='context_sha256'})
            write(dest/'plan.json',value['plan'])
        elif attack=='reserved':value['counters']['reserved']['steps_charged']+=1
        else:value['counters'][dict(charged='steps_charged',accepted='accepted_steps',calls='trajectories',readers='reader_units')[attack]]+=1
        write(dest/'result.json',value);rehash(dest)
        bad=report.read_result(dest)
        assert bad['status']=='unconfirmed',bad
        if attack=='quota':
            refused=cli('--read-result',dest)
            assert refused.returncode==1,refused.stdout
    # Check the initial current candidate and the final acquired checkpoint.
    checkpoints=sorted(out.glob('checkpoint-*.json'))
    for index,checkpoint in enumerate((checkpoints[0],checkpoints[-1])):
        dest=tmp_path/f'cp{index}';shutil.copytree(out,dest)
        for other in dest.glob('checkpoint-*.json'):
            if other.name!=checkpoint.name:other.unlink()
        report.read_checkpoint(dest)
        record=report.read_json(dest/checkpoint.name)
        record['payload']['counters']['steps_charged']+=1
        record['sha256']=flow.digest(record['payload']);write(dest/checkpoint.name,record)
        with pytest.raises(ValueError,match='counter'):report.read_checkpoint(dest)
    for value in (True,48.,49,72001):
        change(job,lambda r:r['scenarios'][0].update(steps_per_call=value))
        absent=tmp_path/'absent'
        refused=cli('--job',job,'--output-dir',absent,'--dry-run')
        assert refused.returncode==2 and not absent.exists(),refused.stdout
    change(job, lambda r: (r.update(fit=[]), r['scenarios'][0].update(steps_per_call=96)))
    refused=cli('--job',job,'--output-dir',absent,'--dry-run')
    assert refused.returncode==2 and not absent.exists(),refused.stdout
    assert 'ValueError' in refused.stdout and 'AttributeError' not in refused.stdout


def test_batch_without_starts_reconstructs_plan(job,tmp_path):
    change(job,lambda r:(r['search'].pop('starts'),r['scenarios'][0].update(steps_per_call=96)))
    p=flow.preflight(job)
    bad=copy.deepcopy(p);bad['scenarios'][0]['steps_per_call']=48
    bad['context_sha256']=flow.digest({k:v for k,v in bad.items() if k!='context_sha256'})
    with pytest.raises(ValueError):flow.check_plan(bad)
