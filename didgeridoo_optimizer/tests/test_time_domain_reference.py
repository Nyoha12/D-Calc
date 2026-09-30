import copy
import csv
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from didgeridoo_optimizer.pipeline import time_domain_reference as workflow
from didgeridoo_optimizer.reporting import time_domain_reference as reporting
from didgeridoo_optimizer.tests.test_passive_resonator import modal
from didgeridoo_optimizer.nonlinear.thresholds import OscillationThresholdEstimator
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters
from tools import time_domain_reference as cli

ROOT=Path(__file__).resolve().parents[2]
CONFIG=ROOT/'project_specs/examples/design_pitch/config.yaml'
DESIGN=ROOT/'project_specs/examples/design_pitch/cylinder.json'


def test_dry_run_reuses_context_without_acoustics_or_writes(tmp_path):
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with patch.object(workflow,'input_impedance',side_effect=AssertionError('acoustics')),patch.object(workflow.LinearEvaluationPipeline,'evaluate',side_effect=AssertionError('legacy')),patch.object(workflow,'load_fixed_context',wraps=workflow.load_fixed_context) as load:
        result=workflow.run(CONFIG,DESIGN,tmp_path/'out',dry_run=True)
    assert result['ok'] and result['status']=='preflight_valid' and load.call_count==1
    assert not (tmp_path/'out').exists()
    assert before=={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert result['plan']['gates_declared_before_fit']==dict(complex_nrmse=.005,relative_max=.08,phase_rms_deg=.3)


@pytest.mark.parametrize('options',[{'sample_rate_hz':44100},{'sample_rate_hz':True},{'guard_max_hz':6000.},
    {'fit_min_hz':0},{'fit_points':True},{'loss_model':'legacy','air_reference':None},
    {'R0':0,'dc_origin':None},{'seconds':181},{'fit_seconds':180},{'h_cm':0},{'flow_peak_m3_s':float('nan')},
    {'unknown':1}])
def test_bad_preflight_never_launches(tmp_path,options):
    with patch.object(workflow,'input_impedance',side_effect=AssertionError('acoustics')),pytest.raises(ValueError):
        workflow.run(CONFIG,DESIGN,tmp_path/'out',dry_run=True,**options)
    assert not (tmp_path/'out').exists()


def test_duplicate_config_and_nonregular_geometry_refused(tmp_path):
    config=tmp_path/'bad.yaml'; config.write_text('materials: {}\nmaterials: {}\n')
    with pytest.raises(ValueError):workflow.preflight(config,DESIGN,tmp_path/'out')
    design=json.loads(DESIGN.read_text());design['segments'][0]['kind']='branch'
    bad=tmp_path/'bad.json';bad.write_text(json.dumps(design))
    with pytest.raises((ValueError,NotImplementedError)):workflow.preflight(CONFIG,bad,tmp_path/'out')


def test_native_v2_same_schedule_exact_signals_and_no_surrogate():
    params=DimensionedLipParameters()
    result=workflow.simulate_v2(modal(4000),pressure_pa=4000,duration_s=.02,params=params)
    config=dict(nonlinear_simulation=dict(lip_model_type='dimensioned_v2',sample_rate_hz=4000,
        simulation_duration_s=.02,warmup_duration_s=0.))
    reference=OscillationThresholdEstimator().simulate_at_pressure(modal(4000),params,4.,config,reference_freq_hz=70.)
    np.testing.assert_array_equal(result['midpoint_pressure_pa'],reference['pressure_signal'])
    np.testing.assert_array_equal(result['flow_m3_s'],reference['flow_signal'])
    assert result['sample_rate_hz']==4000 and not result['surrogate_excitation_used']
    assert result['status']=='experimental_not_coupled_energy_validated'
    with pytest.raises(ValueError):workflow.simulate_v2(modal(16000),pressure_pa=4000)


def test_model_fs_mismatch_and_analytic_without_tmm(tmp_path):
    path=tmp_path/'modal.json'
    with patch.object(workflow,'input_impedance',side_effect=AssertionError('TMM')):
        m=modal(4000);m.save(path)
        with pytest.raises(ValueError,match='fs'):
            workflow.preflight(CONFIG,DESIGN,tmp_path/'out',model_in=path)


def test_source_guard_rejects_mixed_loaded_roots(tmp_path):
    source=tmp_path/'foreign.py';source.write_text('pass\n')
    with patch.dict(sys.modules,{'didgeridoo_optimizer.foreign':SimpleNamespace(__file__=str(source))}):
        with pytest.raises(ValueError,match='Mixed'):reporting.sources()


def test_context_change_between_preflight_and_calculation_keeps_partial(tmp_path):
    plan,context=workflow.preflight(CONFIG,DESIGN,tmp_path/'out')
    plan['input_files']['design']['sha256']='0'*64
    Path(plan['output']).mkdir()
    value=workflow.calculate(plan,context)
    assert not value['ok'] and value['status']=='failed'
    assert value['statuses']['fidelity']=='not_accepted'
    partial=json.loads((tmp_path/'out/partial.json').read_text())
    assert 'Inputs changed' in partial['error']


@pytest.mark.parametrize('exception',[TimeoutError('bounded test'),KeyboardInterrupt(),MemoryError()])
def test_partial_json_csv_summary_keep_all_statuses(tmp_path,exception):
    plan,context=workflow.preflight(CONFIG,DESIGN,tmp_path/'out')
    output=Path(plan['output']);output.mkdir()
    with patch.object(workflow,'input_impedance',side_effect=exception):
        value=workflow.calculate(plan,context)
    assert not value['ok'] and value['statuses']['physical']=='not_validated'
    reporting.export(output,value)
    parsed=json.loads((output/'reference.json').read_text())
    row=list(csv.DictReader((output/'reference.csv').open()))[0]
    for key in reporting.STATUS_KEYS:
        assert row[key]==parsed['statuses'][key]
        assert key+': '+row[key] in (output/'summary.txt').read_text()
    assert (output/'partial.json').is_file()


def test_real_cli_dry_run_and_parser_refusal(tmp_path):
    command=[sys.executable,'-B','-m','tools.time_domain_reference']
    args=['--config',str(CONFIG),'--design',str(DESIGN),'--output-dir',str(tmp_path/'out'),'--air-reference','ck_dry20','--dry-run']
    result=subprocess.run(command+args,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['dry_run'] and not (tmp_path/'out').exists()
    bad=subprocess.run(command+['--unknown'],capture_output=True,text=True,timeout=20)
    assert bad.returncode==1 and json.loads(bad.stdout)['status']=='refused'


def test_nonzero_child_cannot_claim_acceptance(tmp_path):
    plan,context=workflow.preflight(CONFIG,DESIGN,tmp_path/'out')
    claimed=dict(ok=True,status='numerically_accepted',statuses=dict(passivity='structural',fitter='converged',fidelity='accepted',mesh='accepted',physical='not_validated'))
    with patch.object(workflow,'preflight',return_value=(plan,context)),patch.object(workflow.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps(claimed),returncode=9)):
        result=workflow.run(CONFIG,DESIGN,tmp_path/'out')
    assert result['status']=='failed' and not result['ok']
    assert result['statuses']['fidelity']=='not_accepted' and result['child']['exit_code']==9


def test_parent_timeout_retains_partial_statuses(tmp_path):
    plan,context=workflow.preflight(CONFIG,DESIGN,tmp_path/'out')
    def timeout(*args,**kwargs):
        reporting.checkpoint(Path(plan['output']),dict(ok=False,status='partial',statuses=dict(passivity='structural',fitter='not_converged',fidelity='not_accepted',mesh='not_checked',physical='not_validated')),'fit')
        raise subprocess.TimeoutExpired('bounded child',.001)
    with patch.object(workflow,'preflight',return_value=(plan,context)),patch.object(workflow.subprocess,'run',side_effect=timeout):
        value=workflow.run(CONFIG,DESIGN,tmp_path/'out')
    assert value['status']=='interrupted' and value['statuses']['fitter']=='not_converged'
    assert value['child']['reaped'] and (tmp_path/'out/reference.csv').exists()


def test_copied_cli_producer_refused(tmp_path):
    import importlib.util
    path=tmp_path/'copied.py';path.write_text(Path(cli.__file__).read_text())
    spec=importlib.util.spec_from_file_location('copied_td_cli',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.main(['--help'])==1


def test_v2_effective_parameters_and_nonregular_boundary_preserved():
    got=workflow.simulate_v2(modal(4000),pressure_pa=0,duration_s=.001)
    assert got['parameters']['mouth_pressure_kpa']==0
    assert got['duration_s']==32/4000
    assert got['equilibrium']['branches'][0]['non_regular_reasons']
    with pytest.raises(ValueError):workflow.simulate_v2(modal(),pressure_pa=4000,params={'mass_kg':True})
    with pytest.raises(ValueError):workflow.simulate_v2(modal(),pressure_pa=4000,params=replace(DimensionedLipParameters(),mass_kg=-1))


def test_reload_recertifies_all_candidates_and_refuses_modified_inventory():
    from didgeridoo_optimizer.nonlinear.passive_fit import fit_passive,DEFAULT_GATES
    from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator
    from didgeridoo_optimizer.tests.test_passive_resonator import DC
    reference=modal(4000);f=np.linspace(30,200,128);fg=np.linspace(200,250,32)
    z=reference.discrete_response(f);zg=reference.discrete_response(fg)
    model,info=fit_passive(f,z,R0=0,dc_origin=DC,sample_rate_hz=4000,gates=DEFAULT_GATES,
        guard_frequency_hz=fg,guard_impedance=zg)
    assert workflow._reload_certificate(model,f,z,fg,zg)['converged']
    original=model.parameters()
    for mutation in ('bool','negative','omitted','pole','active'):
        p=copy.deepcopy(original);inv=p['quality']['candidate_inventory']
        if mutation=='bool':inv['a'][0]=False
        if mutation=='negative':inv['a'][0]=-1
        if mutation=='pole':inv['omega'][0]*=1.01
        if mutation=='active':p['a'][0]*=1.01
        if mutation=='omitted':
            for key in inv:inv[key].pop()
        altered=PassiveResonator.from_parameters(p)
        with pytest.raises(ValueError):workflow._reload_certificate(altered,f,z,fg,zg)


def test_tiny_mesh_is_refused_before_parent_allocation(tmp_path):
    with patch.object(workflow.GeometryDiscretizer,'discretize',side_effect=AssertionError('must pre-count')):
        with pytest.raises(ValueError,match='before allocation'):
            workflow.preflight(CONFIG,DESIGN,tmp_path/'out',h_cm=1e-100)
    with pytest.raises(ValueError):workflow.preflight(CONFIG,DESIGN,tmp_path/'out',v2_duration_s=.0001)


def test_mutation_between_strict_validation_and_native_loading_refused(tmp_path):
    design=tmp_path/'design.json';design.write_bytes(DESIGN.read_bytes())
    original=workflow.load_fixed_context
    def changed(*args,**kwargs):
        data=json.loads(design.read_text());data['id']='changed_after_strict'
        design.write_text(json.dumps(data))
        return original(*args,**kwargs)
    with patch.object(workflow,'load_fixed_context',side_effect=changed),pytest.raises(ValueError,match='between strict'):
        workflow.preflight(CONFIG,design,tmp_path/'out')
    assert not (tmp_path/'out').exists()
