"""Offline contract and numeric edge cases; no acoustic fixture regeneration."""
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from tools import compare_forced_response as cli

cmp = cli.comparison
ROOT = Path(__file__).resolve().parents[2]


def complex_curve(values):
    points = []
    for value in values:
        value = complex(value)
        scale = max(abs(value.real), abs(value.imag))
        points.append(dict(value=dict(real=value.real, imag=value.imag),
                           status='ok' if scale else 'analytic_zero', reason=None,
                           log_abs=math.log(scale)+math.log(math.hypot(value.real/scale,value.imag/scale)) if scale else None,
                           phase_rad=math.atan2(value.imag,value.real) if scale else None))
    return {k:[p[k] for p in points] for k in points[0]}


def real_curve(values):
    return dict(value=values, status=['ok' if x else 'analytic_zero' for x in values],
                reason=[None]*len(values), log_abs=[math.log(abs(x)) if x else None for x in values],
                sign=[(x>0)-(x<0) for x in values], roundoff_tolerance_w=[0.]*len(values))


def payload():
    segment = dict(kind='cylinder', length_cm=121., d_in_cm=3., d_out_cm=3.,
                   position_start_cm=0., position_end_cm=121., profile_params={}, material_id='test')
    design = dict(id='cylinder', segments=[segment], metadata={})
    material = dict(id='test', acoustic_model={k+'_'+s: v for k,v in
                    [('beta',2.4),('porosity_leak',0.),('wall_loss',0.)] for s in ['nominal','min','max']})
    powers = {k:real_curve([1.,2.,3.]) for k in cmp.REAL_NAMES}
    powers['Pin']['positive_log_resolved'] = [True]*3
    rad = dict(name='legacy', version='test', variant='test', radius_m=.015, normalization_radius_m=.015,
               ka_out=[.1,.2,.3], model_status=['ok']*3, model_reason=[None]*3,
               coefficients=None, assumptions=['fixture'], termination={'interpretation':'fixture'})
    model = dict(model='legacy_beta', model_version='test', frequency_hz=[70.,1000.,1500.],
                 ka_out=[.1,.2,.3], load=[dict(real=1.,imag=2.)]*3, log_scale=[0.]*3,
                 log_scale_status=['ok']*3, exit_radius_m=.015, zref_pa_s_m3=1., load_type='fixture',
                 transfers={k:complex_curve([1+1j,2+2j,3+3j]) for k in ['Zin','Yin','Hu','Yt','Zt','Hp']},
                 ports={k:complex_curve([1.,1.,1.]) for k in ['p1','U1','p2','U2']}, powers=powers,
                 source=dict(kind='pressure', units='Pa peak', amplitude=complex_curve([1.]*3)), radiation=rad)
    return dict(schema=cmp.INPUT_SCHEMA, units=deepcopy(cmp.UNITS), convention=cmp.CONVENTION,
                provenance=dict(sha='producer', working_tree_dirty=False), materials_used={'test':material},
                effective_parameters=dict(air=dict(rho=1.204,c=343.,mu=1.8e-5,kappa=.025,cp=1004.,gamma=1.4,temperature_c=20.,humidity_percent=0.),
                                          h_cm=.5,n_points=3,f_min_hz=70.,f_max_hz=1500.),
                cases=[dict(physical_design=design, analysis_design=deepcopy(design), models=[model])])


def model(data):
    return data['cases'][0]['models'][0]


def loaded(data, label='fixture'):
    cmp.validate_export(data)
    return dict(payload=data, file=dict(path=label,sha256=hashlib.sha256(json.dumps(data).encode()).hexdigest(),bytes=1))


def compare(a, b, **kw):
    return cmp.compare_exports(loaded(a,'baseline'), loaded(b,'candidate'), **kw)


def snapshot(curve, i=0):
    return {k:v[i] for k,v in curve.items()}


def write(tmp_path, data, name='input.json'):
    path=tmp_path/name
    path.write_text(json.dumps(data,allow_nan=False),encoding='utf-8')
    return path


def args(a,b,out,*extra):
    return ['--baseline',str(a),'--candidate',str(b),'--output-dir',str(out),*extra]


def test_auto_select_explicit_partial_and_ambiguity():
    a=payload()
    assert compare(a,a)['changed_factors']==[]
    a['cases'].append(deepcopy(a['cases'][0]))
    a['cases'][1]['physical_design']['id']='bell'
    with pytest.raises(ValueError,match='ambiguë.*cylinder / legacy_beta.*bell / legacy_beta'):
        cmp.select(a)
    assert cmp.select(a,'bell')[0]['physical_design']['id']=='bell'
    with pytest.raises(ValueError,match='introuvable.*Choix disponibles'):
        cmp.select(a,'absent')
    a['cases'][0]['models'].append(deepcopy(model(a)))
    a['cases'][0]['models'][1]['model']='zk'
    with pytest.raises(ValueError,match='ambiguë'):
        cmp.select(a,'cylinder')
    assert cmp.select(a,None,'zk')[1]['model']=='zk'
    assert cmp.select(a,'cylinder','legacy_beta')[1]['model']=='legacy_beta'


def test_ratios_complex_relative_power_db_and_circular_phase():
    a,b=payload(),payload()
    for key in cmp.COMPLEX_NAMES:
        model(b)['transfers'][key]=complex_curve([2+2j,4+4j,6+6j])
    model(b)['powers']['Pload']=real_curve([4.,8.,12.])
    result=compare(a,b)
    hu=result['points'][0]['observables']['Hu']['metrics']
    assert hu['delta_real']['value']==1 and hu['delta_imag']['value']==1
    assert hu['relative_real']['value']==1 and hu['relative_imag']['value']==0
    assert hu['magnitude_ratio_db']['value']==pytest.approx(20*math.log10(2))
    assert result['points'][0]['observables']['Pload']['metrics']['power_ratio_db']['value']==pytest.approx(10*math.log10(4))
    av=complex_curve([complex(math.cos(math.radians(179)),math.sin(math.radians(179)))])
    bv=complex_curve([complex(math.cos(math.radians(-179)),math.sin(math.radians(-179)))])
    assert cmp.compare_complex(snapshot(av),snapshot(bv))['phase_delta_rad']['value']==pytest.approx(math.radians(2))
    r=cmp.compare_complex(snapshot(complex_curve([1j])),snapshot(complex_curve([1.])))
    assert r['relative_real']['value']==-1 and r['relative_imag']['value']==-1


@pytest.mark.parametrize('left,right',[(0,0),(0,1),(1,0)])
def test_analytic_zeros_are_not_unknown(left,right):
    a,b=snapshot(complex_curve([left])),snapshot(complex_curve([right]))
    r=cmp.compare_complex(a,b)
    assert r['delta_real']['value']==right-left
    assert r['magnitude_ratio_db']['value'] is None and r['phase_delta_rad']['value'] is None
    assert (r['relative_real']['value'] is None)==(left==0)
    if left==0:
        assert 'baseline_zero' in r['relative_real']['reason']
    p=cmp.compare_real(snapshot(real_curve([left])),snapshot(real_curve([right])),'Pload')
    assert p['delta']['value']==right-left
    assert p['power_ratio_db']['value'] is None


@pytest.mark.parametrize('status',['unavailable','roundoff_limited'])
def test_unavailable_and_roundoff_never_derive_metrics(status):
    a=snapshot(complex_curve([1.])); a.update(status=status,reason='original reason')
    r=cmp.compare_complex(a,snapshot(complex_curve([2.])))
    assert all(v['value'] is None and 'original reason' in v['reason'] for v in r.values())
    p=snapshot(real_curve([1.])); p.update(status=status,reason='original power reason')
    r=cmp.compare_real(p,snapshot(real_curve([2.])),'Pload')
    assert all(v['value'] is None for v in r.values())


@pytest.mark.parametrize('status,log',[('underflow',-1000.),('overflow',1000.)])
def test_finite_logs_enable_db_without_cartesian_reconstruction(status,log):
    a=dict(value=None,status=status,reason='projection absent',log_abs=log,phase_rad=.1)
    b=dict(a,log_abs=log+math.log(2),phase_rad=.3)
    r=cmp.compare_complex(a,b)
    assert r['delta_real']['value'] is None
    assert r['relative_real']['value'] is None
    assert r['magnitude_ratio_db']['value']==pytest.approx(20*math.log10(2))
    assert r['phase_delta_rad']['value']==pytest.approx(.2)
    a=dict(value=None,status=status,reason='projection absent',log_abs=log,sign=1.)
    b=dict(a,log_abs=log+math.log(4))
    r=cmp.compare_real(a,b,'Pload')
    assert r['delta']['value'] is None
    assert r['power_ratio_db']['value']==pytest.approx(10*math.log10(4))
    b['sign']=-1
    assert cmp.compare_real(a,b,'Pload')['power_ratio_db']['value'] is None


def test_signed_powers_roundoff_flags_and_eta():
    a=snapshot(real_curve([-2.])); a.update(status='passivity_violation',reason='negative residual')
    b=snapshot(real_curve([-3.])); b.update(status='passivity_violation',reason='negative residual')
    result=cmp.compare_real(a,b,'Pdiss')
    assert result['delta']['value']==-1 and result['relative']['value']==.5
    assert result['power_ratio_db']['value'] is None
    assert 'power_ratio_db' not in cmp.compare_real(a,b,'eta')
    a,b=snapshot(real_curve([1.])),snapshot(real_curve([2.]))
    a['positive_log_resolved']=False; b['positive_log_resolved']=True
    assert cmp.compare_real(a,b,'Pin')['power_ratio_db']['value'] is None


def test_extreme_complex_components_and_nonfinite_derived_results():
    # Their magnitudes exceed float.max, but each component is representable.
    a=snapshot(complex_curve([complex(1.3e308,1.3e308)]))
    b=snapshot(complex_curve([complex(-1.3e308,1.3e308)]))
    r=cmp.compare_complex(a,b)
    assert r['delta_real']['value'] is None
    assert r['delta_imag']['value']==0
    assert r['relative_real']['value']==-1 and r['relative_imag']['value']==1
    assert r['magnitude_ratio_db']['value']==0
    a=dict(value=None,status='underflow',reason='extreme log',log_abs=-1.7e308,phase_rad=0.)
    b=dict(a,status='overflow',log_abs=1.7e308)
    r=cmp.compare_complex(a,b)
    assert r['magnitude_ratio_db']['value'] is None
    assert 'nonfinite' in r['magnitude_ratio_db']['reason']
    a.update(sign=1); b.update(sign=1)
    assert cmp.compare_real(a,b,'Pload')['power_ratio_db']['value'] is None
    r=cmp.compare_complex(snapshot(complex_curve([complex(1e308,0)])),snapshot(complex_curve([complex(1e308,1e-300)])))
    assert r['relative_imag']['value'] is None
    assert 'underflow' in r['relative_imag']['reason']
    json.dumps(r,allow_nan=False)


def test_subnormals_and_extrapolation_are_separate_and_rejected_points_visible():
    a,b=payload(),payload()
    model(a)['transfers']['Hu']=complex_curve([1e-310]*3)
    model(a)['transfers']['Hu']['status']=['subnormal']*3
    model(b)['transfers']['Hu']=complex_curve([2e-310]*3)
    model(b)['transfers']['Hu']['status']=['subnormal']*3
    model(b)['radiation']['model_status'][2]='extrapolation'
    model(b)['radiation']['model_reason'][2]='outside reference band'
    model(a)['powers']['Pload']['status'][1]='roundoff_limited'
    model(a)['powers']['Pload']['reason'][1]='unresolved residual'
    result=compare(a,b)
    assert result['points'][2]['radiation']['candidate']['status']=='extrapolation'
    o=result['points'][2]['observables']['Hu']
    assert o['metrics']['delta_real']['status']=='subnormal'
    assert o['flags']==['baseline_subnormal','candidate_subnormal']
    counts=result['coverage']['Pload.delta']
    assert counts==dict(available=2,unavailable=1,subnormal=0,refused_indices=[1])
    assert result['points'][1]['observables']['Pload']['baseline']['value']==2
    assert result['points'][1]['observables']['Pload']['baseline']['reason']=='unresolved residual'


def test_factors_material_values_not_ids_metadata_or_subdivision():
    a,b=payload(),payload()
    b['cases'][0]['physical_design']['id']='renamed'
    b['cases'][0]['physical_design']['metadata']={'label':'decoration'}
    b['effective_parameters']['air'].update(identifier='decorative',provenance='label',thermoviscous_parameters_used=True)
    assert compare(a,b)['changed_factors']==[]
    b['materials_used']['test']['acoustic_model']['beta_nominal']=3.
    assert compare(a,b)['changed_factors']==['materials']
    b['cases'][0]['physical_design']['segments'][0]['d_out_cm']=4.
    model(b)['model']='zwikker_kosten_circular'
    model(b)['radiation']['name']='silva_unflanged'
    r=compare(a,b)
    assert set(r['changed_factors'])=={'geometry','materials','loss_model','radiation'}
    assert 'multifactorielle' in r['interpretation']
    assert r['contexts']['baseline']['loss_model']=='legacy_beta'
    assert r['contexts']['candidate']['radiation']['name']=='silva_unflanged'
    assert r['contexts']['baseline']['producer_provenance']['sha']=='producer'


@pytest.mark.parametrize('field',['source','source_kind','grid','air','step','units','convention'])
def test_incompatible_contexts_rejected_before_output(tmp_path,capsys,field):
    a,b=payload(),payload()
    if field=='source': model(b)['source']['amplitude']=complex_curve([1j]*3)
    elif field=='source_kind': model(b)['source'].update(kind='volume_flow',units='m^3/s peak')
    elif field=='grid': model(b)['frequency_hz'][1]=1000.000000001
    elif field=='air': b['effective_parameters']['air']['rho']+=.000001
    elif field=='step': b['effective_parameters']['h_cm']=1.
    elif field=='units': b['units']['Hu']='m'
    else: b['convention']='exp(-j omega t)'
    ap,bp=write(tmp_path,a,'a.json'),write(tmp_path,b,'b.json')
    out=tmp_path/'new'/'out'
    assert cli.main(args(ap,bp,out))==1
    assert json.loads(capsys.readouterr().out)['ok'] is False
    assert not out.parent.exists()


@pytest.mark.parametrize('raw',['{','{"schema": "x", "schema": "y"}','NaN','Infinity','-Infinity','1e9999','true','[]','{"schema":"wrong"}'])
def test_malformed_json_controlled(tmp_path,capsys,raw):
    path=tmp_path/'bad.json'; path.write_text(raw)
    assert cli.main(args(path,path,tmp_path/'out'))==1
    assert json.loads(capsys.readouterr().out)['ok'] is False
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('bad',['bool_frequency','bool_component','bool_air','bool_power','bool_step','bool_sign',
                                'bool_material','negative_frequency','duplicate_frequency','misaligned','duplicate_case',
                                'duplicate_model','zero_inconsistent','log_inconsistent','phase_inconsistent','sign_inconsistent',
                                'radiation_inconsistent','missing_material','unknown_status','source_status','overflow_with_value'])
def test_strict_structure_rejects_inconsistencies(tmp_path,bad):
    p=payload(); m=model(p)
    if bad=='bool_frequency': m['frequency_hz'][1]=True
    elif bad=='bool_component': m['transfers']['Hu']['value'][0]['real']=True
    elif bad=='bool_air': p['effective_parameters']['air']['rho']=True
    elif bad=='bool_power': m['powers']['Pload']['value'][0]=True
    elif bad=='bool_step': p['effective_parameters']['h_cm']=True
    elif bad=='bool_sign': m['powers']['Pload']['sign'][0]=True
    elif bad=='bool_material': p['materials_used']['test']['acoustic_model']['beta_nominal']=True
    elif bad=='negative_frequency': m['frequency_hz'][0]=-1
    elif bad=='duplicate_frequency': m['frequency_hz'][1]=70.
    elif bad=='misaligned': m['ports']['p1']['status'].pop()
    elif bad=='duplicate_case': p['cases'].append(deepcopy(p['cases'][0]))
    elif bad=='duplicate_model': p['cases'][0]['models'].append(deepcopy(m))
    elif bad=='zero_inconsistent': m['transfers']['Hu']['status'][0]='analytic_zero'
    elif bad=='log_inconsistent': m['transfers']['Hu']['log_abs'][0]=7.
    elif bad=='phase_inconsistent': m['transfers']['Hu']['phase_rad'][0]=2.
    elif bad=='sign_inconsistent': m['powers']['Pload']['sign'][0]=-1
    elif bad=='radiation_inconsistent': m['radiation']['ka_out'][0]=.7
    elif bad=='missing_material': p['materials_used']={}
    elif bad=='unknown_status': m['powers']['eta']['status'][0]='invented'
    elif bad=='source_status': m['source']['amplitude']['status'][0]='unavailable'; m['source']['amplitude']['reason'][0]='unknown'
    else: m['transfers']['Hu']['status'][0]='overflow'; m['transfers']['Hu']['reason'][0]='bad'
    with pytest.raises(ValueError): cmp.load_export(write(tmp_path,p))


def test_size_limit_bounded_read_and_invalid_utf8(tmp_path,monkeypatch):
    path=tmp_path/'input.json'; path.write_bytes(b'x'*1025)
    monkeypatch.setattr(cmp,'MAX_BYTES',1024)
    with pytest.raises(ValueError,match='taille maximale'): cmp.load_export(path)
    path.write_bytes(b'\xff')
    with pytest.raises(UnicodeError): cmp.load_export(path)


def test_output_bundle_preserves_inputs_csv_points_status_and_no_overwrite(tmp_path,capsys):
    a=write(tmp_path,payload()); before=a.read_bytes(); out=tmp_path/'out'
    assert cli.main(args(a,a,out))==0
    response=json.loads(capsys.readouterr().out)
    assert response['ok'] and not response['dry_run']
    assert {p.name for p in out.iterdir()}==set(cmp.ARTIFACTS)
    content={p.name:p.read_bytes() for p in out.iterdir()}
    r=json.loads(content['comparison.json'])
    assert r['contexts']['baseline']['file']['sha256']==hashlib.sha256(before).hexdigest()
    assert r['comparator_provenance']['sources_sha256']
    with (out/'comparison.csv').open(newline='') as stream: rows=list(csv.DictReader(stream))
    assert len(rows)==3*7 and float(rows[-1]['frequency_hz'])==1500
    assert rows[0]['baseline_status']=='ok'
    assert float(rows[0]['metrics_delta_real_value'])==0
    assert '1500' in content['comparison.md'].decode() and 'aucune supériorité' in content['comparison.md'].decode()
    assert cli.main(args(a,a,out))==1
    assert 'écrasement' in json.loads(capsys.readouterr().out)['error']
    assert {p.name:p.read_bytes() for p in out.iterdir()}==content
    assert a.read_bytes()==before


@pytest.mark.parametrize('artifact',cmp.ARTIFACTS)
def test_each_existing_artifact_blocks_before_writes(tmp_path,capsys,artifact):
    a=write(tmp_path,payload()); out=tmp_path/'out'; out.mkdir()
    (out/artifact).write_text('preserve')
    assert cli.main(args(a,a,out))==1
    assert json.loads(capsys.readouterr().out)['ok'] is False
    assert [p.name for p in out.iterdir()]==[artifact]
    assert (out/artifact).read_text()=='preserve'


def test_dry_run_no_directory_serialization_before_writes_and_io_failure(tmp_path,monkeypatch,capsys):
    a=write(tmp_path,payload()); out=tmp_path/'new'/'out'
    assert cli.main(args(a,a,out,'--dry-run'))==0
    assert json.loads(capsys.readouterr().out)['exports']=={}
    assert not out.parent.exists()
    actual=cmp.render_bundle
    def invalid(payload):
        payload['bad']=math.inf
        return actual(payload)
    monkeypatch.setattr(cmp,'render_bundle',invalid)
    assert cli.main(args(a,a,out))==1
    assert not out.parent.exists()
    capsys.readouterr()
    monkeypatch.setattr(cmp,'render_bundle',actual)
    def refused(*args): raise PermissionError('test write refusal')
    monkeypatch.setattr(cmp,'write_bundle',refused)
    assert cli.main(args(a,a,out))==1
    assert 'test write refusal' in json.loads(capsys.readouterr().out)['error']


def test_symlink_no_clobber_and_argument_errors(tmp_path,capsys):
    a=write(tmp_path,payload()); out=tmp_path/'out'; out.mkdir()
    (out/'comparison.csv').symlink_to(tmp_path/'absent')
    assert cli.main(args(a,a,out))==1
    assert json.loads(capsys.readouterr().out)['ok'] is False
    assert len(list(out.iterdir()))==1
    assert cli.main(['--base',str(a)])==1
    assert json.loads(capsys.readouterr().out)['ok'] is False


def test_no_site_packages_or_engine_imports_in_comparator(tmp_path):
    path=write(tmp_path,payload()); out=tmp_path/'out'
    script='''import sys,runpy
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('didgeridoo_optimizer', 'numpy', 'yaml', 'matplotlib')):
            raise RuntimeError('forbidden import: '+fullname)
sys.meta_path.insert(0,Guard())
sys.argv=['tools.compare_forced_response',*sys.argv[1:]]
runpy.run_module('tools.compare_forced_response',run_name='__main__')
'''
    p=subprocess.run([sys.executable,'-B','-S','-c',script,*args(path,path,out)],cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stdout+p.stderr
    assert json.loads(p.stdout)['ok']


def test_real_existing_cli_exports_compared_in_subprocess(tmp_path):
    exports=[]
    for radiation in ['legacy','silva_unflanged']:
        out=tmp_path/radiation
        command=[sys.executable,'-B','-m','tools.forced_response_compare','--case','cylinder','expansion',
                 '--pressure-peak-pa','1','--loss-model','both','--air-reference','ck_dry20',
                 '--radiation-model',radiation,'--f-min','70','--f-max','1500','--points','3','--h-cm','20',
                 '--output-dir',str(out)]
        p=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=45)
        assert p.returncode==0,p.stdout+p.stderr
        exports.append(out/'forced_response.json')
    before=[p.read_bytes() for p in exports]
    options=args(*exports,tmp_path/'compared','--baseline-case','expansion','--candidate-case','expansion',
                 '--baseline-model','zwikker_kosten_circular','--candidate-model','zwikker_kosten_circular')
    p=subprocess.run([sys.executable,'-B','-S','-m','tools.compare_forced_response',*options],
                     cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stdout+p.stderr
    assert json.loads(p.stdout)['changed_factors']==['radiation']
    result=json.loads((tmp_path/'compared/comparison.json').read_text())
    assert result['points'][-1]['frequency_hz']==1500
    assert result['points'][-1]['observables']['Zin']['metrics']['delta_real']['value'] is not None
    assert [p.read_bytes() for p in exports]==before


def test_material_boundary_moves_without_geometry_or_id_change():
    a=payload()
    a['materials_used']['other']=deepcopy(a['materials_used']['test'])
    a['materials_used']['other']['id']='other'
    a['materials_used']['other']['acoustic_model']['beta_nominal']=7.
    case=a['cases'][0]
    for key in ('physical_design','analysis_design'):
        segment=case[key]['segments'][0]
        case[key]['segments']=[dict(segment,position_start_cm=i*40.,position_end_cm=(i+1)*40.,length_cm=40.,
                                    material_id='test' if i<2 else 'other') for i in range(3)]
    b=deepcopy(a)
    for key in ('physical_design','analysis_design'):
        b['cases'][0][key]['segments'][1]['material_id']='other'
    assert compare(a,b)['changed_factors']==['materials']
    # A mismatch limited to the analysis material assignment is visible too.
    b=deepcopy(a)
    b['cases'][0]['analysis_design']['segments'][1]['material_id']='other'
    assert compare(a,b)['changed_factors']==['materials']


def test_homogeneous_subdivision_not_a_material_change():
    a,b=payload(),payload()
    for key in ('physical_design','analysis_design'):
        segment=b['cases'][0][key]['segments'][0]
        b['cases'][0][key]['segments']=[dict(segment,length_cm=60.5,position_start_cm=60.5*i,
                                           position_end_cm=60.5*(i+1)) for i in range(2)]
    assert compare(a,b)['changed_factors']==['geometry']


def test_source_zero_unknown_rad_and_producer_provenance():
    a,b=payload(),payload()
    for data in (a,b):
        model(data)['source']['amplitude']=complex_curve([0.]*3)
        del model(data)['radiation']
    b['provenance']['sha']='different producer'
    r=compare(a,b)
    assert r['changed_factors']==['producer_provenance']
    assert r['points'][0]['radiation']['baseline']['status']=='unidentified_explicit_load'
    assert r['contexts']['baseline']['source']['amplitude']['value'][0]==dict(real=0,imag=0)


def test_bundle_prevalidation_and_late_failure_do_not_claim_success(tmp_path,monkeypatch,capsys):
    with pytest.raises(ValueError,match='sérialisés'):
        cmp.write_bundle({'comparison.json':'{}'},tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()
    a=write(tmp_path,payload()); out=tmp_path/'out'
    original=Path.open
    def denied(path,*args,**kwargs):
        if path.name=='comparison.csv': raise PermissionError('late write refusal')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',denied)
    assert cli.main(args(a,a,out))==1
    assert 'late write refusal' in json.loads(capsys.readouterr().out)['error']
    assert {p.name for p in out.iterdir()}=={'comparison.json'}


def test_ambiguous_cli_dry_run_and_single_selector_auto_complete(tmp_path,capsys):
    a=payload(); a['cases'][0]['models'].append(deepcopy(model(a)))
    a['cases'][0]['models'][1]['model']='zk'
    path=write(tmp_path,a); out=tmp_path/'out'
    assert cli.main(args(path,path,out,'--dry-run'))==1
    assert 'ambiguë' in json.loads(capsys.readouterr().out)['error']
    assert not out.exists()
    assert cli.main(args(path,path,out,'--baseline-model','zk','--candidate-model','legacy_beta','--dry-run'))==0
    assert json.loads(capsys.readouterr().out)['changed_factors']==['loss_model']
    assert not out.exists()


def test_log_domain_extremes_and_unavailable_survive_all_exports(tmp_path,capsys):
    a,b=payload(),payload()
    for data,log in [(a,-1000.),(b,-999.)]:
        model(data)['transfers']['Hu']=dict(value=[None]*3,status=['underflow']*3,reason=['finite log only']*3,
                                           log_abs=[log]*3,phase_rad=[.1]*3)
        model(data)['powers']['Pload']=dict(value=[None]*3,status=['underflow']*3,reason=['finite log only']*3,
                                          log_abs=[2*log]*3,sign=[1.]*3,roundoff_tolerance_w=[None]*3)
        eta=model(data)['powers']['eta']
        for key in eta: eta[key][2]=None
        eta['status'][2]='unavailable'; eta['reason'][2]='power log outside finite range'
    ap,bp=write(tmp_path,a,'a.json'),write(tmp_path,b,'b.json'); out=tmp_path/'out'
    assert cli.main(args(ap,bp,out))==0
    assert json.loads(capsys.readouterr().out)['ok']
    r=json.loads((out/'comparison.json').read_text())
    hu=r['points'][0]['observables']['Hu']
    assert hu['baseline']['value'] is None and hu['metrics']['delta_real']['value'] is None
    assert hu['metrics']['magnitude_ratio_db']['value']==pytest.approx(20/math.log(10))
    assert r['coverage']['eta.delta']['refused_indices']==[2]
    assert 'power log outside finite range' in (out/'comparison.md').read_text()
    with (out/'comparison.csv').open(newline='') as stream: rows=list(csv.DictReader(stream))
    hurow=next(row for row in rows if row['observable']=='Hu')
    assert hurow['baseline_value']=='' and hurow['metrics_delta_real_value']==''
    assert float(hurow['metrics_magnitude_ratio_db_value'])==pytest.approx(20/math.log(10))


@pytest.mark.parametrize('section,bad', [
    ('power', None), ('power', []),
    ('coefficients', []), ('coefficients', [1.]),
    ('effective_radiation', [1.]), ('effective_radiation', None),
    ('reference_band', None), ('reference_band', []),
])
def test_malformed_nested_mappings_return_controlled_cli_error(section, bad, tmp_path, capsys):
    baseline = payload()
    candidate = deepcopy(baseline)
    if section == 'power':
        model(candidate)['powers']['Pin'] = bad
    elif section == 'coefficients':
        model(candidate)['radiation']['coefficients'] = bad
    elif section == 'effective_radiation':
        candidate['effective_parameters']['radiation'] = bad
    else:
        model(candidate)['radiation']['reference_band'] = bad
    a = write(tmp_path, baseline, 'baseline.json')
    b = write(tmp_path, candidate, 'candidate.json')
    destination = tmp_path/'no_output'
    assert cli.main(args(a, b, destination)) == 1
    response = json.loads(capsys.readouterr().out)
    assert response['ok'] is False and response['error']
    assert not destination.exists()
