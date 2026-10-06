"""Native TMM/CLI acceptance, independent oracles and false-success regressions."""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from didgeridoo_optimizer.tests.test_design_contract import context, request_data, EXAMPLE, ROOT, geometry_criterion
from didgeridoo_optimizer.optimization.design_contract import Contract
from didgeridoo_optimizer.optimization.constrained_search import search, preference_key
from didgeridoo_optimizer.pipeline import constrained_design as pipeline
from didgeridoo_optimizer.pipeline.design_input import validate_design, load_design
from didgeridoo_optimizer.reporting import constrained_design as reporting
from didgeridoo_optimizer.acoustics.transfer_matrix import input_impedance


def test_dry_run_no_acoustics_or_output(tmp_path,monkeypatch):
    monkeypatch.setattr(pipeline,'input_impedance',lambda *a,**k:pytest.fail('dry-run acoustique'))
    out=tmp_path/'out'
    result=pipeline.run(EXAMPLE/'config.yaml',EXAMPLE/'design.json',EXAMPLE/'request.yaml',out,dry_run=True)
    assert result['ok'] and not out.exists()
    assert result['plan']['models']['loss_model']=='zk'
    assert result['plan']['effective_models']['air_substitution']!='none; exact CONFIG air'
    assert result['plan']['original_config']['environment']['relative_humidity_percent']==50.


def test_synthetic_lossless_140_maximum_not_zero_imaginary():
    length=343/(4*140)
    def analytic(freq):
        # Small positive attenuation removes the pole without moving its center.
        return 1j*np.tan((2*np.pi*np.asarray(freq)/343-1j*1.e-4)*length)
    spectrum=dict(min_hz=10,max_hz=300,step_hz=1,final_step_hz=.5,refinement_hz=1.e-6,h_cm=1)
    peaks=pipeline.extract_peaks(analytic,spectrum,1.)
    assert peaks[0]['status']=='resolved'
    assert peaks[0]['frequency_hz']==pytest.approx(140,abs=1.e-6)
    assert abs(analytic(np.array([70.])).imag[0])>0


def test_two_hidden_peaks_never_unimodal_success():
    def twin(f):
        f=np.asarray(f)
        return (1+np.exp(-((f-100.12)/.1)**2)+np.exp(-((f-99.88)/.1)**2)).astype(complex)
    spec=dict(min_hz=90,max_hz=110,refinement_hz=1.e-5)
    peaks=pipeline.extract_peaks(twin,spec,1.)
    assert peaks and peaks[0]['status']=='unresolved'


def test_injective_order_windows_no_nearest_substitution():
    peaks=[dict(order=i+1,status='resolved',reason=None,frequency_hz=f) for i,f in enumerate([60,140,280])]
    modes={'a':{'order':1,'window_hz':[120,160]},'b':{'order':2,'window_hz':[120,160]}}
    selected=pipeline.assign_modes(peaks,modes)
    assert selected['a']['status']=='unresolved' and selected['b']['status']=='resolved'
    assert pipeline.assign_modes(peaks,modes,4)['b']['status']=='unresolved'
    assert pipeline.assign_modes([],modes)['a']['peak'] is None


@pytest.mark.parametrize('bad',[float('nan'),float('inf')])
def test_nonfinite_not_zero(context,request_data,bad,monkeypatch):
    c=Contract(request_data,context)
    monkeypatch.setattr(pipeline,'input_impedance',lambda f,*a,**k:np.full(len(f),bad,dtype=complex))
    result=pipeline.Evaluator(c)(c.initial)
    assert not result['search_feasible']
    assert all(r['status']=='unresolved' and r['value_si'] is None for r in result['criteria'])


def test_no_variables_incompatible_does_not_mutate(context,request_data):
    request_data['variables']=[];request_data['criteria'][0]['target']['value']=80.
    c=Contract(request_data,context);result=search(c,pipeline.Evaluator(c))
    assert result['evaluations']==1 and result['termination']=='evaluation_only'
    assert not result['best']['search_feasible'] and not result['infeasibility_proven']
    assert result['best']['physical_design']==c.base


def test_budget_exhaustion_is_not_impossibility(context,request_data):
    request_data['budgets']['evaluations']=1
    c=Contract(request_data,context);result=search(c,pipeline.Evaluator(c))
    assert result['evaluations']==1 and not result['infeasibility_proven']


def test_refinement_error_exceeding_margin_is_unresolved(context,request_data):
    c=Contract(request_data,context); selected={}
    for i,f in enumerate([70,163.8704944609,297.7709899052]):
        selected[f'm{i+1}']=dict(status='resolved',reason=None,peak={'frequency_hz':f})
    rows=pipeline.criterion_rows(c,context['design'],selected,{'m1':1,'m2':1,'m3':1})
    assert all(r['status']=='unresolved' for r in rows)
    assert all(r['value_si'] is not None and r['certified_bound'] is None for r in rows)


def test_geometric_boundary_zero_uncertainty(context,request_data):
    value=context['design'].total_length_cm/100
    request_data['criteria']=[geometry_criterion(target=value-.001)]
    c=Contract(request_data,context)
    rows=pipeline.criterion_rows(c,context['design'],{}, {})
    assert rows[0]['status'] in ('satisfied','violated')


def test_lexicographic_key_not_weighted(context,request_data):
    for i,c in enumerate(request_data['criteria']):c.update(role='preference',priority=i)
    c=Contract(request_data,context)
    a={'criteria':[{'id':f'f{i+1}','residual_normalized':v,'status':'satisfied'} for i,v in enumerate([.1,1000,0])]}
    b={'criteria':[{'id':f'f{i+1}','residual_normalized':v,'status':'satisfied'} for i,v in enumerate([.2,0,0])]}
    assert preference_key(c,a)<preference_key(c,b)
    assert sum(preference_key(c,a))>sum(preference_key(c,b))


def test_preallocation_api_guard(context,request_data,monkeypatch):
    c=Contract(request_data,context);c.budgets['segments']=1
    monkeypatch.setattr(pipeline,'mesh_for',lambda *a,**kw:pytest.fail('allocation avant limite'))
    with pytest.raises(ValueError,match='avant allocation'):
        pipeline.Evaluator(c).spectrum(context['design'],.5,.01,True)


def test_exact_cylinders_mesh_and_physical_outlet(context,request_data,monkeypatch):
    c=Contract(request_data,context);ev=pipeline.Evaluator(c)
    expected=context['design'].segments[-1].d_out_cm/200
    real=pipeline.input_impedance
    counts=[]
    def spy(freq,mesh,*args,**kwargs):
        counts.append(len(mesh.segments));assert kwargs['exit_radius_m']==expected
        assert np.isrealobj(freq)
        return real(freq,mesh,*args,**kwargs)
    monkeypatch.setattr(pipeline,'input_impedance',spy)
    ev.spectrum(context['design'],1.,1.)
    assert set(counts)=={6}


def test_nonuniform_uses_native_discretizer(context,request_data):
    raw=copy.deepcopy(context['design'].as_dict());raw['segments'][1]['d_out_cm']=4.5;raw['segments'][1]['kind']='cone'
    d=validate_design(raw,context['material_db'],context['config'])
    a=pipeline.mesh_for(d,1.);b=pipeline.mesh_for(d,.5)
    assert len(a.segments)>6 and len(b.segments)>len(a.segments)
    assert a.segments[-1].d_out_cm==6


def test_output_existing_symlink_and_partial_refused(tmp_path):
    p=tmp_path/'partial';p.mkdir()
    with pytest.raises(FileExistsError):reporting.new_destination(p)
    q=tmp_path/'link';q.symlink_to(p,target_is_directory=True)
    with pytest.raises(ValueError):reporting.new_destination(q/'new')
    with pytest.raises(ValueError):reporting.new_destination(q)


def test_checkpoint_context_checksum_and_locks(tmp_path,context,request_data):
    c=Contract(request_data,context);candidate={'physical_design':c.generate(c.initial).as_dict()}
    value=dict(request_sha256=reporting.fingerprint(c.request),context_sha256=reporting.context_identity(c),
               candidate_sha256=reporting.fingerprint(candidate),candidate=candidate,status='search_witness_not_final_conformity')
    reporting.write_json(tmp_path/'checkpoint_0001.json',value)
    assert reporting.read_checkpoint(tmp_path,c)==value
    other=copy.deepcopy(context);other['config']['environment']['sound_speed_m_s']=344
    with pytest.raises(ValueError,match='contexte'):reporting.read_checkpoint(tmp_path,Contract(request_data,other))
    value['candidate']['physical_design']['segments'][0]['length_cm']+=1
    reporting.write_json(tmp_path/'checkpoint_0002.json',value)
    with pytest.raises(ValueError,match='altéré'):reporting.read_checkpoint(tmp_path,c)


def test_native_140_cylinder_homogeneous_equation(context,request_data):
    raw=copy.deepcopy(context['design'].as_dict());raw['segments']=[raw['segments'][0]]
    raw['segments'][0]['length_cm']=60.
    ctx=dict(context,design=validate_design(raw,context['material_db'],context['config']))
    request_data.update(variables=[dict(id='length',fields=['segments.0.length_cm'],unit='cm',bounds=[45.,75.])],
                        modes=[dict(id='m1',order=1,window_hz=[100.,190.])])
    request_data['criteria']=request_data['criteria'][:1];request_data['criteria'][0]['target']['value']=140.
    c=Contract(request_data,ctx);ev=pipeline.Evaluator(c); result=search(c,ev)
    final=ev.verify(result['best']['variables_si'])
    assert final['conforming']
    d=c.generate(result['best']['variables_si']);segment=d.segments[0]
    freq=np.linspace(139.99,140.01,2001);omega=2*np.pi*freq
    radius=segment.d_out_cm/200;zc=ev.air.rho*ev.air.c/(np.pi*radius**2)
    loss=ev.loss.evaluate(omega,radius*2,ctx['material_db'].get(segment.material_id),zc,ev.air)
    load=ev.radiation.evaluate(omega,radius,ev.air).impedance
    # Independent homogeneous transmission-line equation; same native loss/load.
    tan=np.tan(loss.k_complex*segment.length_cm/100)
    analytical=loss.zc_complex*(load+1j*loss.zc_complex*tan)/(loss.zc_complex+1j*load*tan)
    native=input_impedance(freq,d,ctx['material_db'],ev.air,exit_radius_m=radius,loss_model=ev.loss,radiation_model=ev.radiation)
    np.testing.assert_allclose(native,analytical,rtol=2.e-12,atol=1.e-6)
    peak=freq[np.argmax(np.abs(analytical))]
    assert abs(1200*np.log2(peak/140))<.1


@pytest.fixture(scope='module')
def cli_bundle(tmp_path_factory):
    out=tmp_path_factory.mktemp('cli')/'result'
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    command=[sys.executable,'-m','tools.constrained_design','--config',str(EXAMPLE/'config.yaml'),
             '--design',str(EXAMPLE/'design.json'),'--request',str(EXAMPLE/'request.yaml'),'--output-dir',str(out)]
    done=subprocess.run(command,text=True,capture_output=True,env=env,cwd=ROOT,timeout=180)
    assert done.returncode==0,done.stdout+done.stderr
    response=json.loads(done.stdout);assert response['ok'] and response['conforming']
    return out,json.loads((out/'result.json').read_text()),response


def test_real_six_cylinder_cli_acceptance_and_provenance(cli_bundle,context,request_data):
    out,result,response=cli_bundle;c=Contract(request_data,context)
    assert response['child_exit_code']==0 and response['child_reaped']
    assert result['search']['evaluations']<=160
    assert result['search']['seed']==0 and result['optimization_status']=='no_preferences'
    assert result['materials']['acoustic_properties']=='omitted_by_zk_not_measured_zero'
    for row in result['final']['criteria']:
        assert row['status']=='satisfied' and abs(row['error_cents'])<.1
        assert row['margin']>row['convergence_estimate']
    for h in result['search']['history']:
        if h['physical_design'] is not None:c.check_locks(h['physical_design'])
    for solution in result['solutions']:
        _,d=load_design(out/solution['design_file'],context['material_db'],context['config']);c.check_locks(d.as_dict())
    assert (out/'criteria.csv').read_text().splitlines()[0].startswith('candidate,id,observable')
    assert 'loaded_sources_sha256' in result['plan']['provenance']
    assert result['plan']['input_files']['design']['sha256']
    assert not result['global_optimum_proven'] and not result['physical_validation']


def test_cli_dry_and_bad_request(tmp_path):
    from tools.constrained_design import main
    assert main(['--config',str(EXAMPLE/'config.yaml'),'--design',str(EXAMPLE/'design.json'),
        '--request',str(EXAMPLE/'request.yaml'),'--output-dir',str(tmp_path/'dry'),'--dry-run'])==0
    assert not (tmp_path/'dry').exists()
    bad=tmp_path/'r.json';bad.write_text('{"schema_version":"bad"}')
    assert main(['--config',str(EXAMPLE/'config.yaml'),'--design',str(EXAMPLE/'design.json'),
        '--request',str(bad),'--output-dir',str(tmp_path/'bad')])==2


def test_explicit_preferences_poll_only_feasible_witnesses(context,request_data):
    field={'field':'segments.1.d_in_cm'}
    hard=dict(geometry_criterion('hard',target=.04),expression=field)
    hard['tolerance']['value']=.00025
    pref=dict(geometry_criterion('prefer','preference',target=.08),expression=field,priority=0)
    request_data['criteria']=[hard,pref]
    c=Contract(request_data,context);ev=pipeline.Evaluator(c)
    initial=ev(c.initial);result=search(c,ev)
    assert preference_key(c,result['best'])<preference_key(c,initial)
    assert all(row['search_feasible'] for row in result['accepted'])
    assert all(row['criteria'][0]['status']=='satisfied' for row in result['accepted'])
    assert any(row['criteria'][0]['status']=='violated' for row in result['history'])


def test_config_cannot_implicitly_select_zk(context,request_data):
    request_data.pop('models')
    ctx=copy.deepcopy(context);ctx['config']['loss_model']='zk'
    c=Contract(request_data,ctx);ev=pipeline.Evaluator(c)
    assert c.options['loss_model']=='legacy'
    assert ev.air.c==context['config']['environment']['sound_speed_m_s']
    assert ev.effective['loss_model']==ev.loss.name


def test_lost_refinement_mode_never_conforms(context,request_data,monkeypatch):
    request_data['variables']=[];c=Contract(request_data,context);ev=pipeline.Evaluator(c)
    real=ev.spectrum
    def lost(design,step,h,subdivide=False):
        peaks,mesh=real(design,step,h,subdivide)
        if subdivide:peaks=peaks[1:]
        return peaks,mesh
    monkeypatch.setattr(ev,'spectrum',lost)
    final=ev.verify([])
    assert not final['conforming']
    assert all(r['status']=='unresolved' for r in final['criteria'])


def test_publication_failure_cannot_return_success(tmp_path,monkeypatch):
    class FailedChild:
        returncode=2
        stdout=json.dumps({'ok':False,'status':'failed','reason':'publication failed'})
        stderr=''
    monkeypatch.setattr(pipeline.subprocess,'run',lambda *a,**k:FailedChild())
    # Keep source discovery independent of the subprocess mock.
    monkeypatch.setattr(reporting,'provenance',lambda:{'software':{},'loaded_sources_sha256':{}})
    monkeypatch.setattr(pipeline,'load_inputs',lambda *a:(None,{'input_files':{},'provenance':{}}))
    result=pipeline.run(EXAMPLE/'config.yaml',EXAMPLE/'design.json',EXAMPLE/'request.yaml',tmp_path/'out')
    assert not result['ok'] and result['child_exit_code']==2


def test_parent_interrupt_retains_plan_and_failure_receipt(tmp_path,monkeypatch):
    monkeypatch.setattr(pipeline,'load_inputs',lambda *a:(None,{'input_files':{},'provenance':{}}))
    def interrupt(*a,**k):raise pipeline.RunInterrupted('test interrupt')
    monkeypatch.setattr(pipeline.subprocess,'run',interrupt)
    result=pipeline.run(EXAMPLE/'config.yaml',EXAMPLE/'design.json',EXAMPLE/'request.yaml',tmp_path/'out')
    assert result['status']=='interrupted' and not result['ok']
    assert (tmp_path/'out/plan.json').exists() and (tmp_path/'out/execution.json').exists()


def test_unresolved_preference_never_ranked(context,request_data):
    request_data['criteria']=request_data['criteria'][:1]
    request_data['criteria'][0].update(role='preference',priority=0)
    c=Contract(request_data,context)
    row={'criteria':[{'id':'f1','status':'unresolved','residual_normalized':.01,'value_si':70.}]}
    assert preference_key(c,row) is None


def test_all_unsupported_observations_not_a_conforming_solution(context,request_data):
    request_data['variables']=[]
    for row in request_data['criteria']:
        row.pop('mode');row.update(observable='played_frequency',level='played',role='observe')
    c=Contract(request_data,context);result=pipeline.Evaluator(c).verify([])
    assert not result['conforming'] and not result['request_fully_covered']


def test_native_immutable_json_publication(tmp_path):
    p=tmp_path/'immutable.json'
    reporting.write_json(p,{'value':1.})
    before=p.read_bytes()
    with pytest.raises(FileExistsError):reporting.write_json(p,{'value':2.})
    assert p.read_bytes()==before
    with pytest.raises(ValueError):reporting.write_json(tmp_path/'nonfinite.json',{'value':float('nan')})
    assert not (tmp_path/'nonfinite.json').exists()
