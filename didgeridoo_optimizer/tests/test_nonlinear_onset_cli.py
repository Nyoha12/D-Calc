from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import pytest
import yaml

from didgeridoo_optimizer.tests.test_fixed_design_input import minimal_config, minimal_design
from didgeridoo_optimizer.tests.test_onset_stability import P
from didgeridoo_optimizer.reporting.nonlinear_onset import export_bundle, source_fingerprints, strict_json
from tools import nonlinear_onset_audit as cli


@pytest.fixture
def inputs(tmp_path):
    config=minimal_config()
    config["nonlinear_simulation"]={**P.as_dict(),"lip_model_type":"dimensioned_v2","sample_rate_hz":4000,
        "resonator_model_type":"fir_long_logfit","resonator_kernel_duration_s":.02}
    c=tmp_path/"config.yaml"; d=tmp_path/"design.json"
    c.write_text(yaml.safe_dump(config),encoding="utf-8")
    d.write_text(json.dumps(minimal_design()),encoding="utf-8")
    return c,d


def argv(inputs,output,*extra):
    return ["--config",str(inputs[0]),"--design",str(inputs[1]),"--output-dir",str(output),
            "--upstream","ideal","--parameter-source","explicit","--pressure-min-pa","1000",
            "--pressure-max-pa","2000","--frequency-min-hz","50","--frequency-max-hz","100",
            "--pressure-seeds","2","--frequency-seeds","2","--max-iterations","4","--seconds","30",*extra]


def test_dry_run_no_acoustics_no_writes_and_reused_context(inputs,tmp_path):
    before={p:p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with patch.object(cli.LinearEvaluationPipeline,"evaluate",side_effect=AssertionError("acoustics")), patch.object(cli.TimeDomainResonator,"from_linear_result",side_effect=AssertionError("FIR")), patch.object(cli,"load_fixed_context",wraps=cli.load_fixed_context) as loader:
        result=cli.run(cli.parser().parse_args(argv(inputs,tmp_path/"out","--dry-run")))
    assert loader.call_count==1
    assert result["ok"] and result["dry_run"] and result["status"] == "preflight_valid"
    assert not (tmp_path/"out").exists()
    assert before=={p:p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert result["parameters"]["direct"]==P.as_dict()
    assert result["parameters"]["pending_pipeline_defaults"]==[]
    assert "acoustics" in result["not_executed"]


def test_pipeline_defaults_only_claimed_when_actually_chosen(inputs,tmp_path):
    cfg=yaml.safe_load(inputs[0].read_text())
    cfg["nonlinear_simulation"].pop("resonance_hz")
    cfg["nonlinear_simulation"].pop("mouth_pressure_kpa")
    inputs[0].write_text(yaml.safe_dump(cfg))
    args=argv(inputs,tmp_path/"out","--parameter-source","pipeline_defaults")
    dry=cli.run(cli.parser().parse_args([*args,"--dry-run"]))
    assert dry["parameters"]["pipeline_defaults_chosen"]=={}
    assert dry["parameters"]["pending_pipeline_defaults"]==["mouth_pressure_kpa","resonance_hz"]
    result=cli.run(cli.parser().parse_args(args))
    assert result["ok"]
    chosen=result["parameters"]["pipeline_defaults_chosen"]
    assert set(chosen)=={"mouth_pressure_kpa","resonance_hz"}
    assert chosen["resonance_hz"]==max(1.1*result["linear"]["f0_hz"],40.)


@pytest.mark.parametrize("key,value",[("lip_model_type","legacy"),("mass_kg",False),("pressure_force_sign",0),
    ("damping_ratio",float("inf")),("sample_rate_hz",True),("resonator_kernel_duration_s",float("nan")),
    ("resonator_model_type","surrogate"),("mass_kg_typo",.0001),("enabled",False)])
def test_strict_nonlinear_refusals_before_acoustics(inputs,tmp_path,key,value):
    cfg=yaml.safe_load(inputs[0].read_text())
    cfg["nonlinear_simulation"][key]=value
    inputs[0].write_text(yaml.safe_dump(cfg))
    with patch.object(cli.LinearEvaluationPipeline,"evaluate",side_effect=AssertionError("acoustics")),pytest.raises(ValueError):
        cli.run(cli.parser().parse_args(argv(inputs,tmp_path/"out")))
    assert not (tmp_path/"out").exists()


@pytest.mark.parametrize("option,value",[("--seconds","181"),("--seconds","nan"),("--max-evaluations","0"),
    ("--pressure-seeds","0"),("--memory-mib","769"),("--frequency-max-hz","2000"),
    ("--frequency-min-hz","20"),("--pressure-min-pa","-1"),("--pressure-max-pa","999")])
def test_strict_budget_and_domain_refusals(inputs,tmp_path,option,value):
    with pytest.raises(ValueError):
        cli.run(cli.parser().parse_args(argv(inputs,tmp_path/"out",option,value,"--dry-run")))
    assert not (tmp_path/"out").exists()


def test_missing_v2_fields_require_explicit_defaults_opt_in(inputs,tmp_path):
    cfg=yaml.safe_load(inputs[0].read_text()); del cfg["nonlinear_simulation"]["pressure_force_sign"]
    inputs[0].write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError,match="missing"):
        cli.run(cli.parser().parse_args(argv(inputs,tmp_path/"out","--dry-run")))


def test_actual_cli_exports_strict_json_csv_summary_provenance_and_no_signals(inputs,tmp_path):
    env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1","OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1"}
    command=[sys.executable,"-B","-m","tools.nonlinear_onset_audit",*argv(inputs,tmp_path/"out")]
    completed=subprocess.run(command,text=True,capture_output=True,timeout=40,env=env)
    assert completed.returncode==0,completed.stdout+completed.stderr
    result=json.loads(completed.stdout,parse_constant=lambda value: pytest.fail(value))
    assert result["ok"] and result["status"]=="numerical_model_only"
    data=json.loads((tmp_path/"out/onset_audit.json").read_text(),parse_constant=lambda value: pytest.fail(value))
    assert data["provenance"]["inputs"]["config"]["sha256"]==hashlib.sha256(inputs[0].read_bytes()).hexdigest()
    assert data["provenance"]["inputs_and_sources_unchanged"]
    assert data["fir"]["length"]==80
    assert data["budget"]["memory_mib"]==768 and data["budget"]["blas_threads"]==1
    assert len(data["searches"])==3
    assert len(data["materials_used"])==1
    assert all(len(h)==64 for h in data["provenance"]["sources_sha256"].values())
    for name in ("onset_equilibria.csv","onset_response.csv"):
        rows=list(csv.DictReader((tmp_path/"out"/name).open()))
        assert rows
    text=(tmp_path/"out/onset_summary_fr.txt").read_text()
    assert "Une grille positive ne certifie pas" in text and "seuil universel" in text
    assert not any(key in completed.stdout for key in ("pressure_signal","flow_signal","aggregate_score"))
    # The same command must refuse overwrite, preserving existing evidence.
    before=(tmp_path/"out/onset_audit.json").read_bytes()
    again=subprocess.run(command,text=True,capture_output=True,timeout=40,env=env)
    assert again.returncode==1 and "overwrite" in again.stdout
    assert (tmp_path/"out/onset_audit.json").read_bytes()==before


def test_real_cli_dry_run_and_parser_refusal(inputs,tmp_path):
    command=[sys.executable,"-B","-m","tools.nonlinear_onset_audit"]
    dry=subprocess.run([*command,*argv(inputs,tmp_path/"out","--dry-run")],capture_output=True,text=True,timeout=20)
    assert dry.returncode==0 and json.loads(dry.stdout)["dry_run"]
    assert not (tmp_path/"out").exists()
    bad=subprocess.run([*command,"--unknown"],capture_output=True,text=True,timeout=20)
    assert bad.returncode==1 and not json.loads(bad.stdout)["ok"]


@pytest.mark.parametrize("exception",[TimeoutError("test deadline"),KeyboardInterrupt(),MemoryError()])
def test_interruptions_keep_partial_and_no_child_process(inputs,tmp_path,exception):
    with patch.object(cli,"search_marginals",side_effect=exception),patch.object(subprocess,"Popen",wraps=subprocess.Popen) as spawn:
        result=cli.run(cli.parser().parse_args(argv(inputs,tmp_path/"out")))
    assert not result["ok"] and result["partial"] and result["status"]=="not_resolved"
    assert "fir" in result and result["equilibria"]["fir_dc"]
    stored=json.loads((tmp_path/"out/onset_audit.json").read_text())
    assert stored["partial"]
    # Only existing provenance's bounded git reads may spawn; no diagnostic child.
    assert all(call.args[0][0]=="git" for call in spawn.call_args_list)


def test_evaluation_budget_retains_trace(inputs,tmp_path):
    result=cli.run(cli.parser().parse_args(argv(inputs,tmp_path/"out","--max-evaluations","2")))
    assert not result["ok"] and result["partial"]
    assert result["searches"][0]["budget_exhausted"]
    assert result["searches"][0]["trace"]


def test_input_mutation_refused_and_sources_are_actual(inputs,tmp_path):
    original=cli.LinearEvaluationPipeline.evaluate
    def changed(self,*a,**kw):
        result=original(self,*a,**kw)
        inputs[0].write_text(inputs[0].read_text()+"\n# changed during diagnostic\n")
        return result
    with patch.object(cli.LinearEvaluationPipeline,"evaluate",changed),pytest.raises(ValueError,match="Input changed"):
        cli.run(cli.parser().parse_args(argv(inputs,tmp_path/"out")))
    assert not (tmp_path/"out/onset_audit.json").exists()
    assert (tmp_path/"out/onset_partial.json").exists()
    sources=source_fingerprints()
    root=Path(__file__).resolve().parents[2]
    key="didgeridoo_optimizer/nonlinear/lips.py"
    assert sources[key]==hashlib.sha256((root/key).read_bytes()).hexdigest()


def test_strict_export_refuses_nonfinite_without_writing(tmp_path):
    with pytest.raises(ValueError):
        strict_json({"number":float("nan")})
    with pytest.raises(ValueError):
        export_bundle({"status":"not_resolved","number":float("inf")},tmp_path/"out")
    assert not (tmp_path/"out").exists()


def test_real_cli_termination_retains_durable_partial_and_waits_owned_child(inputs,tmp_path):
    cfg=yaml.safe_load(inputs[0].read_text())
    cfg["nonlinear_simulation"].update(sample_rate_hz=12000,resonator_kernel_duration_s=1.)
    cfg["frequency_analysis"]["n_points"]=8192
    inputs[0].write_text(yaml.safe_dump(cfg))
    command=[sys.executable,"-B","-m","tools.nonlinear_onset_audit",*argv(inputs,tmp_path/"out",
        "--pressure-seeds","32","--frequency-seeds","32","--max-iterations","32","--max-evaluations","20000")]
    proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+10
        partial=tmp_path/"out/onset_partial.json"
        while not partial.exists() and proc.poll() is None and time.monotonic()<deadline:
            time.sleep(.005)
        assert partial.exists(),"child did not reach durable preflight"
        proc.terminate()  # Only this test's own child, never a global kill.
        out,err=proc.communicate(timeout=10)
        assert proc.returncode==1,out+err
        assert not json.loads(out)["ok"]
        assert json.loads(partial.read_text())["partial"]
        trace=tmp_path/"out/onset_trace.jsonl"
        assert trace.exists()
        for line in trace.read_text().splitlines():
            assert "stage" in json.loads(line)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_continuation_budget_failure_is_partial(inputs,tmp_path):
    original=cli.search_marginals
    def inject(*a,**kw):
        answer=original(*a,**kw)
        if kw["kernel"] is not None:
            answer["candidates"]=[{"pressure_pa":1500.,"frequency_hz":70.,"branch_id":"free:0","status":"marginal_candidate","scaled_residual":1e-10,"crossing":"not_resolved"}]
        return answer
    with patch.object(cli,"search_marginals",inject),patch.object(cli,"verify_discrete_crossing",return_value={"status":"not_resolved","budget_exhausted":True}):
        result=cli.run(cli.parser().parse_args(argv(inputs,tmp_path/"out")))
    assert not result["ok"] and result["partial"]
    assert result["searches"][-1]["candidates"][0]["continuation"]["budget_exhausted"]
