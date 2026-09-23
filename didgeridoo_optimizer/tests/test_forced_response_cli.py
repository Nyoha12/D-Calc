"""Real CLI, strict exports, no hidden phases, input/provenance preservation."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import yaml

from tools import forced_response_compare as tool
from didgeridoo_optimizer.acoustics import forced_response as fr
from didgeridoo_optimizer.reporting import forced_response as report
from didgeridoo_optimizer.tests.test_fixed_design_internal import FixedDesignInternalTests as _Config

ROOT=Path(__file__).resolve().parents[2]


def inputs(tmp_path, suffix='.yaml'):
    config=_Config()._minimal_linear_config()
    config['materials'].update(database_file=str(ROOT/'project_specs/materials_base_v1.yaml'),
                               variant_rules_file=str(ROOT/'project_specs/wood_variant_rules_v1.yaml'))
    config['frequency_analysis'].update(n_points=12,discretization_max_segment_cm=10.)
    config['project']={'output_dir':str(tmp_path/'forbidden')}
    design=_Config()._minimal_design_mapping()
    design['metadata']={'observations': [{'1':'annotation', 'null':None}], '<<':'text'}
    cp,dp=tmp_path/'config.yaml',tmp_path/('design'+suffix)
    cp.write_text(yaml.safe_dump(config),encoding='utf-8')
    dp.write_text(json.dumps(design) if suffix=='.json' else yaml.safe_dump(design),encoding='utf-8')
    return cp,dp,design


def read_bundle(output):
    payload=json.loads((output/'forced_response.json').read_text(encoding='utf-8'),
                       parse_constant=lambda s:pytest.fail('Nonfinite JSON '+s))
    with (output/'forced_response.csv').open(encoding='utf-8',newline='') as stream:
        rows=list(csv.DictReader(stream))
    assert set(p.name for p in output.iterdir())=={'forced_response.json','forced_response.csv','forced_response.txt'}
    assert len(rows)==sum(len(m['frequency_hz']) for c in payload['cases'] for m in c['models'])
    for row in rows:
        model=next(m for c in payload['cases'] if c['physical_design']['id']==row['case'] for m in c['models'] if m['model']==row['model'])
        index=model['frequency_hz'].index(float(row['frequency_hz']))
        assert float(row['Hu_real'])==model['transfers']['Hu']['value'][index]['real']
        assert row['Pload_status']==model['powers']['Pload']['status'][index]
        assert row['U2_units']=='m^3/s peak'
    return payload


@pytest.mark.parametrize('suffix',['.yaml','.json'])
def test_real_config_cli_end_to_end(suffix,tmp_path):
    cp,dp,design=inputs(tmp_path,suffix); before=[p.read_bytes() for p in (cp,dp)]
    output=tmp_path/'result'
    command=[sys.executable,'-B','-m','tools.forced_response_compare','--config',str(cp),'--design',str(dp),
             '--pressure-peak-pa','1','--loss-model','both','--air-reference','ck_dry25','--output-dir',str(output)]
    run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=45)
    assert run.returncode==0,run.stdout+run.stderr
    assert json.loads(run.stdout)['ok'] is True
    payload=read_bundle(output)
    assert payload['schema']=='dcalc.forced_response.v1'
    assert payload['cases'][0]['physical_design']['metadata']['observations']==design['metadata']['observations']
    assert payload['cases'][0]['physical_design']['metadata']['<<']=='text'
    assert payload['effective_parameters']['air']['humidity_percent']==0
    assert payload['effective_parameters']['original_air']['humidity_percent']==50
    assert payload['original_context']['config']['environment']['relative_humidity_percent']==50
    assert payload['provenance']['sha'] is not None
    assert payload['provenance']['tool_sha256']==hashlib.sha256(Path(tool.__file__).read_bytes()).hexdigest()
    assert payload['original_context']['provenance']['files']['design']['path']==str(dp.resolve())
    assert [p.read_bytes() for p in (cp,dp)]==before
    assert not (tmp_path/'forbidden').exists()
    hashes={p.name:p.read_bytes() for p in output.iterdir()}
    again=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=45)
    assert again.returncode==1 and not json.loads(again.stdout)['ok']
    assert hashes=={p.name:p.read_bytes() for p in output.iterdir()}


def test_real_builtin_cli_and_default_air(tmp_path):
    out=tmp_path/'out'
    command=[sys.executable,'-B','-m','tools.forced_response_compare','--case','cylinder','expansion','constriction',
             '--flow-peak-m3-s','1e-6','--points','5','--h-cm','5','--output-dir',str(out)]
    run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    payload=read_bundle(out)
    assert len(payload['cases'])==3
    assert all(len(case['models'])==1 for case in payload['cases'])
    assert payload['effective_parameters']['air']['humidity_percent']==fr.DEFAULT_AIR.humidity_percent
    assert payload['effective_parameters']['air_substitution'].startswith('none')
    assert 'mu' not in payload['effective_parameters']['air']


def forbid_phases(monkeypatch):
    from didgeridoo_optimizer.pipeline.run_optimizer import OptimizerRunner
    from didgeridoo_optimizer.pipeline.evaluate_linear import LinearEvaluationPipeline
    from didgeridoo_optimizer.acoustics import transfer_matrix
    for name in ('load_context','run','finalize'):
        monkeypatch.setattr(OptimizerRunner,name,lambda *a,**k:pytest.fail('Optimizer phase called'))
    monkeypatch.setattr(LinearEvaluationPipeline,'evaluate',lambda *a,**k:pytest.fail('Linear scoring pipeline called'))
    monkeypatch.setattr(transfer_matrix,'input_impedance',lambda *a,**k:pytest.fail('Second propagation called'))


def test_one_propagation_each_model_source_reuse_and_no_forbidden_phases(tmp_path,monkeypatch):
    forbid_phases(monkeypatch)
    calls=[]; actual=fr.loaded_transfer
    transfers=[]
    def spy(*a,**k):
        calls.append(k['loss_model'].name)
        result=actual(*a,**k); transfers.append(result)
        return result
    monkeypatch.setattr(fr,'loaded_transfer',spy)
    args=['--case','cylinder','--loss-model','both','--air-reference','ck_dry20',
          '--pressure-peak-pa','1','--points','8','--h-cm','5','--output-dir',str(tmp_path/'out')]
    assert tool.main(args)==0
    assert calls==['legacy_beta','zwikker_kosten_circular']
    monkeypatch.setattr(fr.LegacyBetaLossModel,'evaluate',lambda *a,**k:pytest.fail('Source change recalculated losses'))
    monkeypatch.setattr(tool.ZwikkerKostenLossModel,'evaluate',lambda *a,**k:pytest.fail('Source change recalculated losses'))
    for transfer in transfers:
        fr.apply_source(transfer,'pressure',2.)
        fr.apply_source(transfer,'volume_flow',1e-6)


@pytest.mark.parametrize('source',['--flow-peak-m3-s','--pressure-peak-pa'])
def test_dry_run_no_acoustics_or_output(tmp_path,monkeypatch,source):
    cp,dp,_=inputs(tmp_path); forbid_phases(monkeypatch)
    monkeypatch.setattr(fr,'loaded_transfer',lambda *a,**k:pytest.fail('Dry-run propagated'))
    monkeypatch.setattr(tool.ZwikkerKostenLossModel,'evaluate',lambda *a,**k:pytest.fail('Dry-run losses'))
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    assert tool.main(['--config',str(cp),'--design',str(dp),source,'1','--loss-model','zk','--air-reference','ck_dry20',
                      '--output-dir',str(tmp_path/'new'/'out'),'--dry-run'])==0
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize('extra',[
    [], ['--pressure-peak-pa','1','--flow-peak-m3-s','1'], ['--pressure-peak-pa','nan'],
    ['--pressure-peak-pa','-1'], ['--pressure-peak-pa','0'],
    ['--pressure-peak-pa','1','--loss-model','zk'], ['--pressure-peak-pa','1','--points','20001'],
    ['--pressure-peak-pa','1','--points','20000','--h-cm','.01'],
    ['--pressure-peak-pa','1','--f-min','0'], ['--pressure-peak-pa','1','--f-max','1'],
])
@pytest.mark.parametrize('dry',[False,True])
def test_cli_invalid_and_budgets_precede_allocation(tmp_path,monkeypatch,extra,dry):
    monkeypatch.setattr(fr,'loaded_transfer',lambda *a,**k:pytest.fail('Invalid request propagated'))
    monkeypatch.setattr(tool.np,'linspace',lambda *a,**k:pytest.fail('Invalid request allocated grid'))
    out=tmp_path/'out'
    assert tool.main(['--output-dir',str(out),*extra,*(['--dry-run'] if dry else [])])==1
    assert not out.exists()


@pytest.mark.parametrize('dry',[False,True])
@pytest.mark.parametrize('bad',['duplicate','metadata','material','missing'])
def test_design_strict_contract_before_calculation(tmp_path,monkeypatch,dry,bad):
    cp,dp,_=inputs(tmp_path)
    if bad=='duplicate':
        dp.write_text('segments: []\nsegments: []\n')
    elif bad=='metadata':
        raw=yaml.safe_load(dp.read_text())
        raw['metadata']={'observations': [{1:'bad', '1':'other'}]}
        dp.write_text(yaml.safe_dump(raw))
    elif bad=='material':
        dp.write_text(dp.read_text().replace('pvc_pressure','missing_material'))
    else:
        dp=tmp_path/'missing'/'design.yaml'
    monkeypatch.setattr(fr,'loaded_transfer',lambda *a,**k:pytest.fail('Invalid DESIGN calculated'))
    assert tool.main(['--config',str(cp),'--design',str(dp),'--pressure-peak-pa','1',
                      '--output-dir',str(tmp_path/'out'),*(['--dry-run'] if dry else [])])==1
    assert not (tmp_path/'out').exists()


def test_source_membership_unknown_and_script_fingerprint(monkeypatch,tmp_path):
    monkeypatch.setattr(tool,'_software_source',lambda:dict(sha='claimed',origin='fixture',working_tree_dirty=False))
    def unavailable(*a,**k):
        raise OSError('Git unavailable')
    monkeypatch.setattr(tool.subprocess,'run',unavailable)
    result=tool.source_identity()
    assert result['sha'] is None and result['working_tree_dirty'] is None
    assert result['tool_sha256'] and 'unknown' in result['origin']


def test_bad_config_and_io_failure_are_controlled(tmp_path,monkeypatch,capsys):
    cp,dp,_=inputs(tmp_path)
    cp.write_text('invalid: [')
    args=['--config',str(cp),'--design',str(dp),'--pressure-peak-pa','1','--output-dir',str(tmp_path/'out')]
    assert tool.main(args)==1
    assert json.loads(capsys.readouterr().out)['ok'] is False
    assert not (tmp_path/'out').exists()
    def refused(*a,**k):
        raise PermissionError('fixture write refusal')
    monkeypatch.setattr(tool,'export_bundle',refused)
    assert tool.main(['--points','2','--h-cm','121','--pressure-peak-pa','1','--output-dir',str(tmp_path/'out')])==1
    assert 'fixture write refusal' in json.loads(capsys.readouterr().out)['error']


def test_strict_serialization_alignment_and_unavailable_not_zero(tmp_path):
    out=tmp_path/'out'
    with pytest.raises(ValueError):
        report.export_bundle(dict(schema=report.SCHEMA,cases=[],bad=np.nan),out)
    assert not out.exists()
    tr=fr.transfer_from_slices([1.],[(1.,2-1000j,3e5)],3e5,zref=1e5)
    tr.update(model='extreme_fixture',model_version='test',warnings=[],ka_out=np.array([0.]),
              load_type='matched finite',exit_radius_m=.01)
    model=report.model_payload(tr,fr.apply_source(tr,'volume_flow',1e-6),0.)
    payload=dict(schema=report.SCHEMA,cases=[dict(physical_design={'id':'extreme'},models=[model])])
    report.export_bundle(payload,out)
    restored=json.loads((out/'forced_response.json').read_text())
    hu=restored['cases'][0]['models'][0]['transfers']['Hu']
    assert hu['value']==[None] and hu['log_abs'][0]==pytest.approx(-1000)
    assert hu['reason'][0] and hu['status']==['underflow']
    assert restored['cases'][0]['models'][0]['powers']['Pload']['value']==[None]
    model['ports']['U2']['status'].append('bad')
    with pytest.raises(ValueError,match='alignment'):
        report.export_bundle(payload,tmp_path/'unaligned')
    assert not (tmp_path/'unaligned').exists()
