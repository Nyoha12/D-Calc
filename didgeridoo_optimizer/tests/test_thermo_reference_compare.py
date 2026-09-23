"""Offline tests: diagnostics, bounded acquisition and external-data contracts."""
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import numpy as np
import pytest
import yaml

from tools import thermo_reference_compare as tool
from didgeridoo_optimizer.acoustics.thermoviscous import CK_DRY_20C, CK_DRY_25C, ZwikkerKostenLossModel, zk_coefficients
from didgeridoo_optimizer.acoustics.losses import LegacyBetaLossModel


def context_files(tmp_path):
    from didgeridoo_optimizer.tests.test_fixed_design_internal import FixedDesignInternalTests as _ConfigFixture
    config = _ConfigFixture()._minimal_linear_config()
    root = Path(__file__).resolve().parents[2]
    config['materials']['database_file'] = str(root/'project_specs/materials_base_v1.yaml')
    config['materials']['variant_rules_file'] = str(root/'project_specs/wood_variant_rules_v1.yaml')
    config['project'] = {'output_dir':str(tmp_path/'forbidden_fixed_output')}
    design = _ConfigFixture()._minimal_design_mapping()
    design['metadata'] = {'private_annotation':['kept',None]}
    cp, dp = tmp_path/'config.yaml', tmp_path/'design.json'
    cp.write_text(yaml.safe_dump(config),encoding='utf-8')
    dp.write_text(json.dumps(design),encoding='utf-8')
    return cp,dp


def test_same_geometry_air_radius_and_closed_analytic(monkeypatch):
    material = tool.synthetic_material()
    assert (material.beta.nominal,material.wall_loss.nominal,material.porosity_leak.nominal) == (2.4,0,0)
    physical = tool.builtin_design('body_bell')
    observed = []
    actual = tool.input_impedance
    def spy(freq,mesh,materials,air,**kwargs):
        observed.append((mesh.as_dict(),air,kwargs['exit_radius_m']))
        return actual(freq,mesh,materials,air,**kwargs)
    monkeypatch.setattr(tool,'input_impedance',spy)
    for model in (LegacyBetaLossModel(),ZwikkerKostenLossModel(CK_DRY_20C)):
        evaluate,_,_ = tool.make_evaluator(physical,{material.id:material},CK_DRY_20C,model,1)
        assert np.all(np.isfinite(evaluate(np.array([70.,400.]))))
    assert observed[0] == observed[1]
    assert observed[0][2] == .06 and observed[0][1].humidity_percent == 0
    closed = tool.builtin_design('closed_cylinder')
    frequency = np.array([10.,900.,950.,1000.])
    coeff = zk_coefficients(2*np.pi*frequency,closed.segments[0].d_in_cm/100,CK_DRY_20C)
    evaluate,_,_ = tool.make_evaluator(closed,{material.id:material},CK_DRY_20C,ZwikkerKostenLossModel(CK_DRY_20C),1,closed=True)
    monkeypatch.setattr(tool,'input_impedance',lambda *a,**k:pytest.fail('production radiation path called for closed tube'))
    np.testing.assert_allclose(evaluate(frequency),-1j*coeff.zc/np.tan(coeff.k*.18),rtol=3e-15)


def test_separate_peak_phase_q_resolution_and_frequency_refinement():
    # Analytic Lorentzian: fmax=phasezero=100 Hz, Q=50; not a production golden.
    def response(f):
        return 1/(1+1j*(f-100))
    f = np.linspace(80,120,81)
    modes = tool.extract_modes(response,f,response(f),refinement_points=(1025,2049))
    mode = modes[0]
    assert mode['frequency_max_abs_hz'] == pytest.approx(100,abs=1e-10)
    assert mode['magnitude_pa_s_m3'] == pytest.approx(1,rel=1e-10)
    assert mode['phase_rad'] == pytest.approx(0,abs=1e-10)
    assert mode['q_half_power'] == pytest.approx(50,rel=1e-4)
    assert mode['resonant_phase_zero_hz'] == pytest.approx(100)
    assert abs(mode['q_half_power']-50) < abs(mode['frequency_refinement'][0]['q_half_power']-50)
    sparse = tool.mode_metrics(f,response(f))
    assert sparse['q_half_power'] is None and sparse['q_unavailable_reason']
    phase = tool.benchmark_modes(np.linspace(80,120,10001),response(np.linspace(80,120,10001)))[0]
    assert phase['frequency_phase_zero_hz'] == pytest.approx(100,abs=1e-10)
    assert phase['observed_window_samples'] >= 11
    insufficient = tool.benchmark_modes(f,response(f))[0]
    assert insufficient['frequency_phase_zero_hz'] is None and insufficient['unavailable_reason']


def test_phase_definition_differs_from_magnitude_and_closed_dc_is_not_a_mode():
    f=np.linspace(90,110,10001)
    z=1/(1+1j*(f-100))+.2j
    peak=tool.mode_metrics(f,z)
    phase=tool.benchmark_modes(f,z)[0]
    assert abs(peak['frequency_max_abs_hz']-phase['frequency_phase_zero_hz']) > .1
    mat=tool.synthetic_material()
    evaluate,_,_=tool.make_evaluator(tool.builtin_design('closed_cylinder'),{mat.id:mat},CK_DRY_25C,ZwikkerKostenLossModel(CK_DRY_25C),1,closed=True)
    f=np.arange(.2,1400.,.2)
    z=evaluate(f)
    assert abs(z[0]) > max(abs(z[f>500]))
    mode=tool.benchmark_modes(f,z)[0]
    assert mode['q_half_power'] > 0 and mode['q_unavailable_reason'] is None


@pytest.mark.parametrize('model', [LegacyBetaLossModel(), ZwikkerKostenLossModel(CK_DRY_25C)], ids=['legacy','zk'])
def test_d1_closed_modes_survive_near_dc_and_lower_bound_change(model):
    material=tool.synthetic_material()
    evaluate,_,_=tool.make_evaluator(tool.builtin_design('closed_cylinder'),{material.id:material},CK_DRY_25C,model,1.,closed=True)
    by_lower_bound=[]
    for lower,points in ((.2,15000),(40.,14801)):
        frequency=np.linspace(lower,3000.,points)
        zin=evaluate(frequency)
        magnitude=abs(zin)
        candidates=np.flatnonzero((magnitude[1:-1]>magnitude[:-2]) & (magnitude[1:-1]>=magnitude[2:]))+1
        assert len(candidates) == 3
        rows=tool.extract_modes(evaluate,frequency,zin,max_modes=3)
        print('D1',model.name,'f_min',lower,'candidates',frequency[candidates].tolist(),
              'rows',[(row['mode_ordinal'],row.get('status'),row['frequency_max_abs_hz']) for row in rows])
        by_lower_bound.append((frequency,candidates,rows))
    for frequency,candidates,rows in by_lower_bound:
        assert [row['mode_ordinal'] for row in rows] == [1,2,3]
        for row,index in zip(rows,candidates,strict=True):
            assert row['status'] == 'resolved' and row['unavailable_reason'] is None
            assert row['candidate_frequency_hz'] == frequency[index]
            assert all(level['status']=='resolved' for level in row['frequency_refinement'])
            assert abs(row['frequency_max_abs_hz']-frequency[index]) <= row['step_hz']+(frequency[1]-frequency[0])
    first,second=[item[2][0] for item in by_lower_bound]
    # A conservative numerical-resolution bound: one of the actual finest
    # local grid intervals, not an empirical or claimed sub-bin precision.
    assert abs(first['frequency_max_abs_hz']-second['frequency_max_abs_hz']) <= max(first['step_hz'],second['step_hz'])
    assert first['resonant_phase_zero_hz'] != first['frequency_max_abs_hz']


def test_d1_unequal_neighbour_does_not_replace_candidate_or_max_modes_boundary():
    def response(f):
        return (1+np.exp(-((f-100)/.5)**2)+20*np.exp(-((f-110)/3)**2)).astype(complex)
    f=np.linspace(90,125,141)
    rows=tool.extract_modes(response,f,response(f),max_modes=2)
    assert [row['mode_ordinal'] for row in rows] == [1,2]
    for row,centre in zip(rows,(100.,110.),strict=True):
        assert row['status']=='resolved'
        for level in row['frequency_refinement']:
            assert level['status']=='resolved' and abs(level['frequency_max_abs_hz']-centre)<.05
    only_first=tool.extract_modes(response,f,response(f),max_modes=1)[0]
    assert only_first['bracket_hz']==rows[0]['bracket_hz']
    assert only_first['frequency_max_abs_hz']==rows[0]['frequency_max_abs_hz']


def test_d1_refinement_keeps_anchor_when_a_missed_stronger_neighbour_appears():
    def response(f):
        return (1+np.exp(-((f-100)/2)**2)+100*np.exp(-((f-105.25)/.025)**2)).astype(complex)
    f=np.arange(90.,116.)  # Narrow neighbour deliberately missed by survey.
    rows=tool.extract_modes(response,f,response(f),refinement_points=(2001,4001))
    assert len(rows)==1 and rows[0]['mode_ordinal']==1
    assert rows[0]['status']=='resolved'
    for level in rows[0]['frequency_refinement']:
        assert level['status']=='resolved' and abs(level['frequency_max_abs_hz']-100)<.02


def test_d1_unresolved_levels_are_explicit_and_never_reuse_stale_metrics():
    f=np.linspace(80,120,81)
    zin=1/(1+1j*(f-100))
    calls=[]
    def response(grid):
        calls.append(len(grid))
        # Controlled failure of resolution, without a nonfinite acoustic result.
        return 1/(1+1j*(grid-100)) if len(calls)==1 else np.ones(len(grid),dtype=complex)
    row=tool.extract_modes(response,f,zin,refinement_points=(65,129))[0]
    assert row['mode_ordinal']==1 and len(row['frequency_refinement'])==2
    assert row['frequency_refinement'][0]['status']=='resolved'
    assert row['frequency_refinement'][1]['status']=='unresolved'
    assert row['status']=='unresolved' and row['unavailable_reason']
    for key in ('frequency_max_abs_hz','magnitude_pa_s_m3','phase_rad','q_half_power','width_hz','resonant_phase_zero_hz'):
        assert row[key] is None
    assert row['q_unavailable_reason']
    lost=tool.extract_modes(lambda grid:np.ones(len(grid),dtype=complex),f,zin)[0]
    assert lost['status']=='unresolved' and lost['unavailable_reason']
    assert len(lost['frequency_refinement'])==2
    # Same analytic response at survey/refinement: three refinement samples
    # genuinely miss the interior peak and place the largest value at a border.
    def narrow(grid):
        return 1/(1+1j*(grid-89.5))
    underresolved=tool.extract_modes(narrow,f,narrow(f),refinement_points=(3,))[0]
    assert underresolved['status']=='unresolved' and underresolved['unavailable_reason']
    assert tool.extract_modes(lambda grid:pytest.fail('max_modes=0 evaluated'),f,zin,max_modes=0)==[]


def test_d1_ambiguous_refined_candidates_do_not_choose_the_taller_peak():
    def response(f):
        return (1+np.exp(-((f-99.75)/.12)**2)+2*np.exp(-((f-100.25)/.12)**2)).astype(complex)
    f=np.arange(95.,106.)
    rows=tool.extract_modes(response,f,response(f))
    assert len(rows)==1 and rows[0]['mode_ordinal']==1
    assert rows[0]['status']=='unresolved'
    assert 'ambiguous' in rows[0]['unavailable_reason'].lower()
    assert rows[0]['frequency_max_abs_hz'] is None


def test_d1_real_cli_near_dc_json_keeps_all_three_modes(tmp_path):
    output=tmp_path/'d1-cli'
    command=[sys.executable,'-B','-m','tools.thermo_reference_compare','--case','closed_cylinder',
             '--air-reference','ck_dry25','--f-min','.2','--f-max','3000','--points','15000',
             '--spatial-steps','1','--max-modes','3','--output-dir',str(output)]
    result=subprocess.run(command,cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stderr+result.stdout
    payload=json.loads((output/'thermo_comparison.json').read_text())
    models=payload['cases'][0]['models']
    for model in models:
        rows=model['levels'][0]['modes']
        print('D1 CLI',model['model'],[(row['mode_ordinal'],row.get('status'),row['frequency_max_abs_hz']) for row in rows])
    for model in models:
        level=model['levels'][0]
        assert level['h_cm']==1 and len(level['frequency_hz'])==15000
        rows=level['modes']
        assert [row['mode_ordinal'] for row in rows]==[1,2,3]
        assert all(row['status']=='resolved' and row['unavailable_reason'] is None for row in rows)
        assert 900 < rows[0]['frequency_max_abs_hz'] < 1000
        assert 1800 < rows[1]['frequency_max_abs_hz'] < 2000
        assert 2800 < rows[2]['frequency_max_abs_hz'] < 3000


def test_real_config_diagnostic_preserves_context_inputs_and_no_optimizer(tmp_path,monkeypatch):
    cp,dp = context_files(tmp_path)
    before = [p.read_bytes() for p in (cp,dp)]
    from didgeridoo_optimizer.pipeline.run_optimizer import OptimizerRunner
    from didgeridoo_optimizer.pipeline.evaluate_linear import LinearEvaluationPipeline
    for method in ('load_context','run','finalize'):
        monkeypatch.setattr(OptimizerRunner,method,lambda *a,**k:pytest.fail('optimizer phase executed'))
    monkeypatch.setattr(LinearEvaluationPipeline,'evaluate',lambda *a,**k:pytest.fail('full pipeline executed'))
    output=tmp_path/'diagnostic'
    args=['--config',str(cp),'--design',str(dp),'--air-reference','ck_dry25','--output-dir',str(output),'--spatial-steps','5','--max-modes','0']
    assert tool.main(args) == 0
    result=json.loads((output/'thermo_comparison.json').read_text())
    assert result['schema'] == 'dcalc.thermo.comparison.v1'
    assert result['air']['humidity_percent'] == 0
    assert result['original_context']['effective_parameters']['environment']['relative_humidity_percent'] == 50
    assert result['cases'][0]['design']['metadata']['private_annotation'] == ['kept',None]
    assert not (tmp_path/'forbidden_fixed_output').exists()
    assert [p.read_bytes() for p in (cp,dp)] == before
    assert len(list(output.iterdir())) == 3
    assert tool.main(args) == 1
    assert 'best_design' not in {p.name for p in output.iterdir()}


def test_real_module_cli(tmp_path):
    command=[sys.executable,'-B','-m','tools.thermo_reference_compare','--case','closed_cylinder','--air-reference','ck_dry20','--output-dir',str(tmp_path/'out'),'--points','64','--spatial-steps','5','--max-modes','0']
    result=subprocess.run(command,cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr+result.stdout
    payload=json.loads((tmp_path/'out/thermo_comparison.json').read_text())
    assert payload['cases'][0]['propagation_method'].startswith('analytic')
    for model in payload['cases'][0]['models']:
        assert len(model['levels'][0]['zin_real']) == len(model['levels'][0]['frequency_hz']) == 64
    assert payload['cases'][0]['materials_used']['thermo_test']['acoustic_model']['beta_nominal'] == 2.4


def test_config_allocation_budget_and_invalid_analysis_without_output(tmp_path,monkeypatch):
    cp,dp=context_files(tmp_path)
    config=yaml.safe_load(cp.read_text())
    config['frequency_analysis']['n_points']=100000001
    cp.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(tool.np,'linspace',lambda *a,**k:pytest.fail('large allocation reached'))
    output=tmp_path/'out'
    assert tool.main(['--config',str(cp),'--design',str(dp),'--air-reference','ck_dry20','--output-dir',str(output)]) == 1
    assert not output.exists()
    assert tool.main(['--output-dir',str(output)]) == 1
    assert not output.exists()


def test_strict_serialization_before_output_and_no_overwrite(tmp_path):
    output=tmp_path/'out'
    with pytest.raises(ValueError):
        tool.write_bundle({'bad':float('nan')},output)
    assert not output.exists()
    files=tool.write_bundle({'schema':tool.SCHEMA,'cases':[]},output)
    before=[p.read_bytes() for p in files]
    with pytest.raises(FileExistsError):
        tool.write_bundle({'cases':[]},output)
    assert [p.read_bytes() for p in files] == before


def make_zip(path,names):
    with zipfile.ZipFile(path,'w') as z:
        for name in names:
            info=zipfile.ZipInfo(name)
            # The constructor normalizes Windows separators: restore the
            # deliberately unsafe spelling rather than testing a safe member.
            info.filename=name
            z.writestr(info,'1 2 3\n')


@pytest.mark.parametrize('names', [['../bad'],['/absolute'],['a\\bad'],['C:/bad'],['a//b'],['a/./b'],['a. /bad'],['CON.txt'],['a','a/b'],['A.txt','a.txt']])
def test_archive_paths_rejected(tmp_path,names):
    archive=tmp_path/'data.zip'
    make_zip(archive,names)
    with pytest.raises(ValueError):
        tool.inspect_archive(archive)


def test_archive_bounds_selective_extraction_and_no_execution(tmp_path,monkeypatch):
    archive=tmp_path/'data.zip'
    make_zip(archive,['Raw/curve.txt','Raw/never_execute.py'])
    result=tool.extract_selected(archive,tmp_path/'selected',['Raw/curve.txt'])
    assert result[0]['sha256'] == hashlib.sha256(b'1 2 3\n').hexdigest()
    assert not (tmp_path/'selected/Raw/never_execute.py').exists()
    with pytest.raises(ValueError):
        tool.extract_selected(archive,tmp_path/'selected',['Raw/curve.txt'])
    monkeypatch.setattr(tool,'MAX_EXPANDED_BYTES',5)
    with pytest.raises(ValueError,match='budget'):
        tool.inspect_archive(archive)


def test_download_budget_checksum_no_overwrite_and_redirect_guard(tmp_path,monkeypatch):
    class Response(io.BytesIO):
        url='https://zenodo.org/test'
        headers={}
    class Opener:
        def open(self,*args,**kwargs):
            return Response(b'fixture-data')
    monkeypatch.setattr(tool.urllib.request,'build_opener',lambda *a:Opener())
    target=tmp_path/'data'
    result=tool.download_public('https://zenodo.org/test',target,max_bytes=20,expected_md5=hashlib.md5(b'fixture-data').hexdigest())
    assert result['sha256'] == hashlib.sha256(b'fixture-data').hexdigest()
    with pytest.raises(FileExistsError):
        tool.download_public('https://zenodo.org/test',target,max_bytes=20)
    with pytest.raises(ValueError,match='budget'):
        tool.download_public('https://zenodo.org/test',tmp_path/'small',max_bytes=5)
    with pytest.raises(ValueError,match='MD5'):
        tool.download_public('https://zenodo.org/test',tmp_path/'checksum',max_bytes=20,expected_md5='wrong')
    with pytest.raises(ValueError,match='outside'):
        tool._ZenodoRedirect().redirect_request(None,None,302,'',{},'https://foreign.invalid/file')


def curve_fiche(path,normalization='dimensional Pa.s/m3'):
    return dict(source=tool.RECORD,doi='10.5281/zenodo.20024938',version='v2',relative_path='fixture.txt',nature='simulation',
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),units='Hz, Pa.s/m3 ReZ/ImZ' if normalization.startswith('dimensional') else 'Hz, dimensionless ReZ/ImZ',
                normalization=normalization,convention='exp(+j*omega*t)',geometry={'length_m':.18,'diameter_m':.014,'normalization_diameter_m':.014,'status':'nominal'},
                environment=asdict(CK_DRY_25C),termination='closed',licence='test fixture',method='synthetic',role='held-out; no adjustment')


def test_external_normalization_and_contract(tmp_path):
    path=tmp_path/'curve.txt'
    path.write_text('10 1 -3\n20 2 -2\n30 3 -1\n')
    fiche=curve_fiche(path,'rho*c/S')
    f,z=tool.read_external_curve(path,fiche)
    np.testing.assert_array_equal(f,[10,20,30])
    z0=CK_DRY_25C.rho*CK_DRY_25C.c/(np.pi*.007**2)
    np.testing.assert_allclose(z/z0,[1-3j,2-2j,3-1j])
    for key,value in [('units','wrong'),('role','fitted'),('sha256','wrong'),('convention','unknown'),('normalization','unknown')]:
        with pytest.raises(ValueError):
            tool.read_external_curve(path,{**fiche,key:value})
    with pytest.raises(ValueError,match='matching'):
        tool.compare_external(path,fiche,CK_DRY_20C)
    result=tool.compare_external(path,fiche,CK_DRY_25C)
    assert len(result['models']) == 2 and 'observed_benchmark_modes' in result
    with pytest.raises(ValueError,match='uncertainty'):
        tool.compare_external(path,{**fiche,'nature':'measurement'},CK_DRY_25C)
    path.write_text('10 0 0\n20 0 0\n30 0 0\n')
    with pytest.raises(ValueError,match='zero'):
        tool.read_external_curve(path,curve_fiche(path))


def test_external_invalid_fiche_cli_controlled_no_output(tmp_path):
    path=tmp_path/'curve.txt'; path.write_text('10 1 1\n20 2 2\n30 3 3\n')
    fiche=curve_fiche(path); fiche['environment']={}
    info=tmp_path/'fiche.json'; info.write_text(json.dumps(fiche))
    output=tmp_path/'out'
    assert tool.main(['--case','closed_cylinder','--air-reference','ck_dry25','--points','3','--spatial-steps','5','--max-modes','0','--external-data',str(path),'--external-fiche',str(info),'--output-dir',str(output)]) == 1
    assert not output.exists()
