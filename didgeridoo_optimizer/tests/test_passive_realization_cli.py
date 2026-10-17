"""Bounded actual CLI, offline reader, strict bundles and import identities."""
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

from didgeridoo_optimizer.nonlinear import scalar_passivity_certificate as certs
from didgeridoo_optimizer.nonlinear import passive_realization as numerical
from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
from didgeridoo_optimizer.pipeline import passive_realization as workflow
from didgeridoo_optimizer.reporting import passive_realization as report

ROOT=Path(__file__).resolve().parents[2]
EXAMPLES=ROOT/'project_specs/examples/passive_realization'


def cli(*args,timeout=25):
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT),
        OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    return subprocess.run([sys.executable,'-m','tools.passive_realization',*map(str,args)],
        cwd=ROOT,env=env,capture_output=True,text=True,timeout=timeout)


def inputs(tmp_path,name='relaxation'):
    src=tmp_path/'source.json';src.write_bytes((EXAMPLES/(name+'.json')).read_bytes())
    proof=tmp_path/'certificate.json';path=EXAMPLES/(name+'-certificate.json')
    if path.exists():proof.write_bytes(path.read_bytes())
    return src,proof if path.exists() else None,tmp_path/'result'


def test_dry_run_no_conversion_factorization_worker_or_output(tmp_path,monkeypatch):
    src,proof,out=inputs(tmp_path)
    def forbidden(*a,**kw):raise AssertionError('scientific/worker call during dry-run')
    monkeypatch.setattr(workflow,'convert',forbidden)
    monkeypatch.setattr(workflow.subprocess,'Popen',forbidden)
    monkeypatch.setattr(PassiveState,'__init__',forbidden)
    monkeypatch.setattr(certs,'verify_certificate',forbidden)
    result=workflow.run(src,proof,out,dry_run=True,seconds=5)
    assert result['ok'] and result['status']=='preflight_only' and not out.exists()


@pytest.mark.parametrize('name',['zero','resistance','relaxation','modal'])
def test_actual_cli_export_import_and_fresh_reader(tmp_path,name):
    src,proof,out=inputs(tmp_path,name)
    args=['--source',src,'--output-dir',out,'--seconds','15']
    if proof:args+=['--certificate',proof]
    result=cli(*args);assert result.returncode==0,result.stdout+result.stderr
    receipt=json.loads(result.stdout)
    assert receipt['child']['exit_code']==0 and receipt['child']['reaped']
    loaded=report.read_result(out);assert loaded['ok']
    model=PassiveState.load(out/'model.json');assert model.parameters()==loaded['model_parameters']
    src.unlink()
    if proof:proof.unlink()
    reader=cli('--read-result',out);assert reader.returncode==0,reader.stdout+reader.stderr
    assert json.loads(reader.stdout)['model_parameters']==model.parameters()
    assert 'not_assessed'==model.parameters()['provenance']['source_passivity']


@pytest.fixture
def bundle(tmp_path):
    src,proof,out=inputs(tmp_path)
    result=workflow.run(src,proof,out,seconds=15);assert result['ok'],result
    return out


def test_reader_fresh_forbids_producer_and_all_simulation(bundle):
    code=r'''
import builtins,sys
original=builtins.__import__
def guarded(name,*a,**kw):
    if name in ('didgeridoo_optimizer.pipeline.passive_realization','didgeridoo_optimizer.nonlinear.passive_realization'):
        raise AssertionError('reader imported producer')
    return original(name,*a,**kw)
builtins.__import__=guarded
from didgeridoo_optimizer.nonlinear.passive_state import PassiveState
import numpy as np
def forbidden(*a,**kw): raise AssertionError('scientific reader call')
for name in ('__init__','step','continuous_response','discrete_response'):setattr(PassiveState,name,forbidden)
for name in ('eig','eigvals','eigh','cholesky'):setattr(np.linalg,name,forbidden)
from didgeridoo_optimizer.reporting.passive_realization import read_result
assert read_result(sys.argv[1])['ok']
assert 'didgeridoo_optimizer.pipeline.passive_realization' not in sys.modules
assert 'didgeridoo_optimizer.nonlinear.passive_realization' not in sys.modules
'''
    p=subprocess.run([sys.executable,'-c',code,str(bundle)],env=os.environ.copy(),capture_output=True,text=True,timeout=15)
    assert p.returncode==0,p.stdout+p.stderr


@pytest.mark.parametrize('name',sorted(report.REQUIRED_FILES|{'manifest.json','execution.json','execution.closed.json'}))
def test_every_required_file_is_checked(bundle,name):
    path=bundle/name;path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError):report.read_result(bundle)


def rehash(bundle):
    manifest=certs.read_json(bundle/'manifest.json')
    manifest['files']={k:certs.sha(certs.read_bytes(bundle/k)) for k in manifest['files']}
    (bundle/'manifest.json').write_text(certs.strict_json(manifest))
    receipt=certs.read_json(bundle/'execution.json');receipt['manifest_sha256']=certs.sha(certs.read_bytes(bundle/'manifest.json'))
    (bundle/'execution.json').write_text(certs.strict_json(receipt))
    (bundle/'execution.closed.json').write_text(certs.strict_json(dict(execution_sha256=certs.sha(certs.read_bytes(bundle/'execution.json')),cancelled=False)))


@pytest.mark.parametrize('mutation',['negative_error','missing_grid','fake_dc','bad_units','provenance','model_coefficient','witness','plan'])
def test_semantic_tampering_refused_after_self_consistent_hashes(bundle,mutation):
    if mutation in ('negative_error','missing_grid','fake_dc'):
        p=certs.read_json(bundle/'diagnostics.json')
        if mutation=='negative_error':p['dc']['absolute']=-1.
        if mutation=='missing_grid':p.pop('grid')
        if mutation=='fake_dc':p['source_dc_pa_s_m3']*=2
        (bundle/'diagnostics.json').write_text(certs.strict_json(p))
    elif mutation=='plan':
        p=certs.read_json(bundle/'plan.json');p['budgets']['blas_threads']=True
        (bundle/'plan.json').write_text(certs.strict_json(p))
    elif mutation=='witness':
        p=certs.read_json(bundle/'witness.json');p['T'][0][0]*=2
        (bundle/'witness.json').write_text(certs.strict_json(p))
    else:
        from didgeridoo_optimizer.nonlinear.passive_resonator import digest
        data=certs.read_json(bundle/'model.json');p=data['parameters']
        if mutation=='bad_units':p['units']['state']='m'
        if mutation=='provenance':p['provenance']['source_passivity']='accepted'
        if mutation=='model_coefficient':p['g'][0]*=2
        data['sha256']=digest(p);(bundle/'model.json').write_text(certs.strict_json(data))
        r=certs.read_json(bundle/'result.json');r['model_sha256']=data['sha256'];(bundle/'result.json').write_text(certs.strict_json(r))
    rehash(bundle)
    with pytest.raises(ValueError):report.read_result(bundle)


@pytest.mark.parametrize('name',certs.SOURCES)
def test_each_module_snapshot_checked_at_boundary(name,monkeypatch):
    import importlib
    module=importlib.import_module(name[:-3].replace('/','.'))
    monkeypatch.setattr(module,'_LOADED_SOURCE_SHA256','0'*64)
    with pytest.raises(ValueError,match='changed'):certs.verify_sources()


def test_source_already_imported_before_pipeline_is_checked_in_new_process():
    code='''
import pathlib
from didgeridoo_optimizer.nonlinear import passive_realization as numerical
native=pathlib.Path.read_bytes
def changed(path):
    raw=native(path)
    return raw+b'\\n#changed' if path==numerical._LOADED_SOURCE_PATH else raw
pathlib.Path.read_bytes=changed
try:
    import didgeridoo_optimizer.pipeline.passive_realization
except ValueError as exc:
    assert 'changed since import' in str(exc)
else:raise AssertionError('early import was silently refreshed')
'''
    p=subprocess.run([sys.executable,'-c',code],env=os.environ.copy(),capture_output=True,text=True,timeout=10)
    assert p.returncode==0,p.stderr


@pytest.mark.parametrize('which',['source','certificate'])
def test_input_changed_during_conversion_is_refused(tmp_path,monkeypatch,which):
    src,proof,out=inputs(tmp_path);plan=workflow.preflight(src,proof,out,seconds=15);out.mkdir()
    monkeypatch.setattr(workflow,'worker_ready',lambda:None)
    native=workflow.convert
    def changed(*a,**kw):
        result=native(*a,**kw);path=src if which=='source' else proof;path.write_bytes(path.read_bytes()+b' ');return result
    monkeypatch.setattr(workflow,'convert',changed)
    assert workflow.worker(plan)==1
    assert not (out/'result.json').exists()
    assert 'changed' in certs.read_json(out/'failure.json')['error']


def test_worker_entry_refuses_unbounded_environment(tmp_path):
    src,proof,out=inputs(tmp_path);plan=workflow.preflight(src,proof,out);path=tmp_path/'plan.json';path.write_text(certs.strict_json(plan))
    p=cli('--worker',path)
    assert p.returncode!=0 and not out.exists()


def test_nonexistent_existing_and_symlink_paths(tmp_path):
    src,proof,out=inputs(tmp_path)
    alias=tmp_path/'alias';alias.symlink_to(src)
    with pytest.raises(ValueError,match='Symbolic'):workflow.preflight(alias,proof,out)
    out.mkdir()
    with pytest.raises(FileExistsError):workflow.preflight(src,proof,out)
    with pytest.raises(ValueError):workflow.preflight(tmp_path/'absent',proof,tmp_path/'new')


def test_timeout_reaps_real_child_and_never_admits(tmp_path):
    src,proof,out=inputs(tmp_path)
    result=workflow.run(src,proof,out,seconds=.03)
    assert not result['ok'] and result['status']=='refused'
    assert result['child']['created'] and result['child']['collected'] and result['child']['pipes_closed']
    with pytest.raises(ProcessLookupError):os.kill(result['child']['pid'],0)
    with pytest.raises(ValueError):report.read_result(out)


def test_cancellation_at_closure_revokes_reader(bundle,tmp_path,monkeypatch):
    # Use a separate explicit directory; bundle fixture already tests normal closure.
    p=tmp_path/'cancel';p.mkdir();src,proof,out=inputs(p)
    native=report.json_new
    def interrupted(path,value):
        native(path,value)
        if Path(path).name=='execution.closed.json':os.kill(os.getpid(),signal.SIGTERM)
    monkeypatch.setattr(report,'json_new',interrupted)
    result=workflow.run(src,proof,out,seconds=15)
    assert not result['ok'] and (out/'execution.cancelled.json').exists()
    with pytest.raises(ValueError):report.read_result(out)


def test_public_timer_restored_after_failure(tmp_path,monkeypatch):
    src,proof,out=inputs(tmp_path);before=signal.getsignal(signal.SIGALRM)
    def broken(*a,**kw):raise ValueError('preflight test failure')
    monkeypatch.setattr(workflow,'preflight',broken)
    with pytest.raises(ValueError):workflow.run(src,proof,out)
    assert signal.getitimer(signal.ITIMER_REAL)==(0.,0.) and signal.getsignal(signal.SIGALRM)==before
