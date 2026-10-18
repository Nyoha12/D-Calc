"""Genuine CLI with portable synthetic metadata, never private acquisitions."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from didgeridoo_optimizer.tests.test_design_dossier import synthetic_job
from didgeridoo_optimizer.reporting import design_dossier_schema as s

ENV={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
     'NUMEXPR_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}


def cli(*args):
    return subprocess.run([sys.executable,'-B','-m','tools.design_dossier',*map(str,args)],cwd=s.ROOT,
                          env=ENV,capture_output=True,text=True,timeout=30)


def test_real_cli_dry_run_and_copied_entry(tmp_path):
    job=synthetic_job(tmp_path);out=tmp_path/'out'
    p=cli('--job',job,'--output-dir',out,'--dry-run')
    assert p.returncode==0,p.stdout+p.stderr
    assert json.loads(p.stdout)['status']=='preflight' and not out.exists()
    copied=tmp_path/'copied.py';shutil.copyfile(s.ROOT/'tools/design_dossier.py',copied)
    env={**ENV,'PYTHONPATH':str(s.ROOT)}
    p=subprocess.run([sys.executable,'-B',str(copied),'--job',str(job),'--output-dir',str(out),'--dry-run'],
                     cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    assert p.returncode==1 and 'copiée' in p.stdout and not out.exists()


@pytest.mark.parametrize('args',[[],['--read-result','missing','--dry-run'],['--read-result','missing','--job','x'],['--job','x']])
def test_cli_exclusive_modes(args):
    p=cli(*args);assert p.returncode==1 and not json.loads(p.stdout)['ok']


def test_real_cli_refused_native_proofs_retained_and_reread(tmp_path):
    job=synthetic_job(tmp_path);out=tmp_path/'out'
    p=cli('--job',job,'--output-dir',out)
    assert p.returncode==1,p.stdout+p.stderr
    value=json.loads(p.stdout);assert value['status']=='partial' and not value['ok']
    assert len(value['children'])==3 and all(c['reaped'] for c in value['children'])
    assert (out/'execution.completed.json').is_file()
    p=cli('--read-result',out)
    assert p.returncode==1,p.stdout+p.stderr
    result=json.loads(p.stdout);assert result['status']=='partial' and result['semantic_verified']
    assert result['payload']['candidates'][0]['hard_status']=='unknown'
