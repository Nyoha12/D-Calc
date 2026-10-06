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
    value=reporting.checkpoint_record(c,candidate,reporting.provenance())
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


# R41 correction regressions: prescribed peaks/control observations, not physics.
@pytest.mark.parametrize('dependency',['observe','unused','preference','hard','ratio'])
def test_optional_mode_refinement_is_local(tmp_path,context,request_data,monkeypatch,dependency):
    request_data['variables']=[]
    request_data['modes']=[request_data['modes'][0],dict(id='missing',order=10,window_hz=[500.,690.])]
    request_data['criteria']=request_data['criteria'][:1]
    if dependency!='unused':
        extra=dict(request_data['criteria'][0],id='extra',mode='missing',role=dependency,
                   target={'value':600.,'unit':'Hz'})
        if dependency=='preference':extra['priority']=0
        if dependency=='ratio':
            extra.pop('mode');extra.update(observable='resonance_ratio',role='hard',unit='1',
                numerator='missing',denominator='m1',target={'value':2.,'unit':'1'})
        request_data['criteria'].append(extra)
    c=Contract(request_data,context)
    class Prescribed(pipeline.Evaluator):
        def spectrum(self,design,step,h,subdivide=False):
            return [dict(order=i+1,frequency_hz=f,status='resolved',reason=None,
                         frequency_estimate_hz=1.e-7) for i,f in enumerate([70.,164.,297.])],design
    monkeypatch.setattr(pipeline,'Evaluator',Prescribed)
    plan={'provenance':reporting.provenance()}
    final=pipeline.execute(c,plan,tmp_path)
    hard=final['final']['criteria'][0]
    assert hard['status']=='satisfied' and hard['value_si']==70.
    assert hard['margin']==.1 and hard['convergence_estimate']>0
    assert bool(final['solutions'])==(dependency not in ('hard','ratio'))
    assert final['request_fully_covered']==(dependency=='unused')
    if dependency!='unused':
        extra=final['final']['criteria'][1]
        assert extra['status']=='unresolved' and extra['value_si'] is None
        assert 'missing' in extra['reason']
    if dependency=='preference':assert final['optimization_status']=='unresolved'
    if dependency in ('observe','preference'):assert final['status']=='hard_feasible_partial_coverage'
    assert '70.0' in (tmp_path/'criteria.csv').read_text()


@pytest.mark.parametrize('fault',['lost','ambiguous','propagation'])
def test_refinement_common_failure_still_blocks(context,request_data,monkeypatch,fault):
    request_data['variables']=[];request_data['criteria']=request_data['criteria'][:1]
    c=Contract(request_data,context);ev=pipeline.Evaluator(c)
    def prescribed(design,step,h,subdivide=False):
        peaks=[dict(order=i+1,frequency_hz=f,status='resolved',reason=None,
                    frequency_estimate_hz=1.e-7) for i,f in enumerate([70.,164.,297.])]
        if subdivide:
            if fault=='propagation':raise ArithmeticError('global propagation failure')
            if fault=='lost':peaks=peaks[1:]
            if fault=='ambiguous':peaks[0].update(status='unresolved',reason='merged maxima')
        return peaks,design
    monkeypatch.setattr(ev,'spectrum',prescribed)
    final=ev.verify([])
    assert not final['conforming']
    assert final['criteria'][0]['status']=='unresolved'


def _correction_cli(args,cwd=ROOT):
    env=dict(os.environ,PYTHONPATH=str(ROOT),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',
             MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable,'-B',*args],cwd=cwd,env=env,text=True,
                          capture_output=True,timeout=180)


@pytest.mark.parametrize('dry',[True,False])
@pytest.mark.parametrize('altered',[True,False])
def test_copied_cli_refused_before_inputs_or_output(tmp_path,dry,altered):
    source=(ROOT/'tools/constrained_design.py').read_text()
    if altered:source=source.replace('def main(argv=None):','COPY_MARKER = True\n\ndef main(argv=None):')
    copied=tmp_path/'copied_cli.py';copied.write_text(source)
    output=tmp_path/'out'
    # Missing input establishes refusal before input parsing, calculation or output.
    done=_correction_cli([str(copied),'--config',str(tmp_path/'missing-config'),
        '--design',str(EXAMPLE/'design.json'),'--request',str(EXAMPLE/'request.yaml'),
        '--output-dir',str(output)]+(['--dry-run'] if dry else []),cwd=tmp_path)
    assert done.returncode==2
    response=json.loads(done.stdout)
    assert not response['ok'] and 'provenance CLI' in response['reason']
    assert not output.exists()


def test_canonical_cli_and_client_api_dry_run(tmp_path):
    args=['--config',str(EXAMPLE/'config.yaml'),'--design',str(EXAMPLE/'design.json'),
          '--request',str(EXAMPLE/'request.yaml'),'--output-dir',str(tmp_path/'out'),'--dry-run']
    cli=_correction_cli(['-m','tools.constrained_design',*args])
    assert cli.returncode==0,cli.stdout+cli.stderr
    response=json.loads(cli.stdout)
    assert response['ok'] and not (tmp_path/'out').exists()
    assert 'tools/constrained_design.py' in response['plan']['provenance']['loaded_sources_sha256']
    client=tmp_path/'client.py'
    client.write_text('import json\nfrom didgeridoo_optimizer.pipeline.constrained_design import run\n'
        f'print(json.dumps(run({str(EXAMPLE/"config.yaml")!r}, {str(EXAMPLE/"design.json")!r}, '
        f'{str(EXAMPLE/"request.yaml")!r}, {str(tmp_path/"api")!r}, dry_run=True)))\n')
    api=_correction_cli([str(client)],cwd=tmp_path)
    assert api.returncode==0,api.stdout+api.stderr
    assert json.loads(api.stdout)['ok'] and not (tmp_path/'api').exists()


def test_cli_checkpoint_new_api_process_without_cli_import(cli_bundle,tmp_path):
    out,_,_=cli_bundle
    code=f'''import sys
from didgeridoo_optimizer.pipeline.constrained_design import load_inputs
from didgeridoo_optimizer.reporting.constrained_design import read_checkpoint
c,_=load_inputs({str(EXAMPLE/'config.yaml')!r},{str(EXAMPLE/'design.json')!r},{str(EXAMPLE/'request.yaml')!r})
assert 'tools.constrained_design' not in sys.modules
value=read_checkpoint({str(out)!r},c)
assert value and value['status']=='search_witness_not_final_conformity'
import didgeridoo_optimizer.tests.test_design_contract
assert read_checkpoint({str(out)!r},c)==value
assert 'tools.constrained_design' not in sys.modules
'''
    done=_correction_cli(['-c',code],cwd=tmp_path)
    assert done.returncode==0,done.stdout+done.stderr


def test_api_checkpoint_new_process(tmp_path,context,request_data):
    request_data['variables']=[]
    request_data['criteria']=[geometry_criterion(target=context['design'].total_length_cm/100)]
    req=tmp_path/'request.json';req.write_text(json.dumps(request_data))
    out=tmp_path/'out';out.mkdir()
    c,plan=pipeline.load_inputs(EXAMPLE/'config.yaml',EXAMPLE/'design.json',req)
    pipeline.execute(c,plan,out)
    code=f'''from didgeridoo_optimizer.pipeline.constrained_design import load_inputs
from didgeridoo_optimizer.reporting.constrained_design import read_checkpoint
c,_=load_inputs({str(EXAMPLE/'config.yaml')!r},{str(EXAMPLE/'design.json')!r},{str(req)!r})
assert read_checkpoint({str(out)!r},c)['candidate']['search_feasible']
'''
    done=_correction_cli(['-c',code],cwd=tmp_path)
    assert done.returncode==0,done.stdout+done.stderr


@pytest.mark.parametrize('dimensions',[1,2])
def test_best_keeps_probe_before_budget_or_incomplete_jacobian(dimensions):
    from types import SimpleNamespace
    c=SimpleNamespace(budgets=dict(evaluations=2,iterations=1,seconds=5),
        variables=[dict(low=0.,high=1.)]*dimensions,initial=[0.]*dimensions,
        criteria=[dict(id='f',role='hard',unsupported_reason=None)],preference_supported=True)
    def prescribed(x):
        r=(float(x[0])-.0001)/1.e-6
        return dict(search_feasible=abs(r)<=1,criteria=[dict(id='f',status='satisfied' if abs(r)<=1 else 'violated',residual_normalized=r)])
    result=search(c,prescribed)
    assert result['evaluations']==2 and len(result['accepted'])==1
    assert result['best'] is result['accepted'][0]
    assert result['best']['variables_si'][0]==.0001


@pytest.mark.parametrize('stage',['backtracking','restart_seed_0'])
def test_best_prefers_feasible_probe_over_later_current(stage):
    from types import SimpleNamespace
    c=SimpleNamespace(budgets=dict(evaluations=10,iterations=1,seconds=5),initial=[.5],
        variables=[dict(low=0.,high=1.)],preference_supported=True,
        criteria=[dict(id='h',role='hard',unsupported_reason=None),dict(id='p',role='preference',priority=0)])
    observations=iter([(2.,.01),(.5,.1)]+([(3.,0.)]*7 if stage=='restart_seed_0' else [])+[(.1,.9)])
    def prescribed(x):
        h,p=next(observations)
        return dict(search_feasible=abs(h)<=1,criteria=[
            dict(id='h',status='satisfied' if abs(h)<=1 else 'violated',residual_normalized=h),
            dict(id='p',status='satisfied',residual_normalized=p)])
    result=search(c,prescribed)
    assert result['history'][-1]['stage']==stage
    assert result['best'] is result['history'][1]
    assert len(result['accepted'])==2


def test_pipeline_final_verifies_probe_before_budget(tmp_path,context,request_data,monkeypatch):
    # Real geometry/lock/final verification; target is exactly the first probe.
    request_data['variables']=request_data['variables'][:1]
    request_data['budgets']['evaluations']=2
    hard=dict(geometry_criterion(target=.040002),expression={'field':'segments.1.d_in_cm'})
    hard['tolerance']['value']=1.e-9
    request_data['criteria']=[hard]
    c=Contract(request_data,context)
    result=pipeline.execute(c,{'provenance':reporting.provenance()},tmp_path)
    assert result['solutions'] and result['final']['final_verified']
    assert result['search']['accepted'][0]['stage']=='finite_difference'
    assert result['final']['criteria'][0]['status']=='satisfied'
    c.check_locks(result['final']['physical_design'])


@pytest.mark.parametrize('label',['config','design','materials','variant_rules'])
def test_checkpoint_rejects_each_input_fingerprint(tmp_path,context,request_data,label):
    c=Contract(request_data,context)
    value=reporting.checkpoint_record(c,{'physical_design':c.base},reporting.provenance())
    reporting.write_json(tmp_path/'checkpoint_0001.json',value)
    other=copy.deepcopy(context)
    other['provenance']['files'][label]['sha256']='0'*64
    with pytest.raises(ValueError,match='contexte'):
        reporting.read_checkpoint(tmp_path,Contract(request_data,other))


def test_checkpoint_rejects_request_bytes_even_if_parsing_identical(tmp_path):
    raw=(EXAMPLE/'request.yaml').read_bytes()
    req=tmp_path/'request.yaml';req.write_bytes(raw)
    c,_=pipeline.load_inputs(EXAMPLE/'config.yaml',EXAMPLE/'design.json',req)
    reporting.write_json(tmp_path/'checkpoint_0001.json',
        reporting.checkpoint_record(c,{'physical_design':c.base},reporting.provenance()))
    req.write_bytes(raw+b'\n')
    other,_=pipeline.load_inputs(EXAMPLE/'config.yaml',EXAMPLE/'design.json',req)
    assert other.request==c.request
    with pytest.raises(ValueError,match='contexte'):reporting.read_checkpoint(tmp_path,other)


@pytest.mark.parametrize('change',['request','models','locks','candidate','checksum','manifest','versions','source_bytes'])
def test_checkpoint_rejects_context_and_producer_changes(tmp_path,context,request_data,monkeypatch,change):
    bundle=tmp_path/'bundle';bundle.mkdir()
    c=Contract(request_data,context)
    value=reporting.checkpoint_record(c,{'physical_design':copy.deepcopy(c.base)},reporting.provenance())
    if change=='request':c.request['metadata']={'changed':True}
    elif change=='models':c.options=dict(c.options,radiation_model='silva_unflanged')
    elif change=='locks':c.lock_mask={'changed':True}
    elif change=='candidate':
        # Even recomputed checksums cannot turn a changed locked field into a valid witness.
        value['candidate']['physical_design']['segments'][0]['length_cm']+=1
        value['candidate_sha256']=reporting.fingerprint(value['candidate'])
        value['checkpoint_sha256']=reporting.fingerprint({k:v for k,v in value.items() if k!='checkpoint_sha256'})
    elif change=='checksum':value['checkpoint_sha256']='0'*64
    elif change=='manifest':value['producer_provenance']['loaded_sources_sha256'].pop(next(iter(value['producer_provenance']['loaded_sources_sha256'])))
    elif change=='versions':value['producer_provenance']['versions']['numpy']='different'
    elif change=='source_bytes':
        # Change private copies of producer bytes, never the checkout under test.
        mirror=tmp_path/'sources';mirror.mkdir()
        for name in value['producer_provenance']['loaded_sources_sha256']:
            path=mirror/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes((ROOT/name).read_bytes())
        target=mirror/'tools/constrained_design.py'
        if not target.exists():target=mirror/next(iter(value['producer_provenance']['loaded_sources_sha256']))
        target.write_bytes(target.read_bytes()+b'\n# altered producer bytes\n')
        monkeypatch.setattr(reporting,'ROOT',mirror)
    reporting.write_json(bundle/'checkpoint_0001.json',value)
    with pytest.raises(ValueError):reporting.read_checkpoint(bundle,c)


def test_checkpoint_legacy_explicitly_incompatible(tmp_path,context,request_data):
    c=Contract(request_data,context)
    reporting.write_json(tmp_path/'checkpoint_0001.json',dict(request_sha256=reporting.fingerprint(c.request),
        context_sha256=reporting.context_identity(c),candidate={'physical_design':c.base}))
    with pytest.raises(ValueError,match='incompatible.*v2'):reporting.read_checkpoint(tmp_path,c)


@pytest.mark.parametrize('name',['../outside.py','/absolute.py','not-source.json'])
def test_producer_manifest_paths_bounded(name):
    with pytest.raises(ValueError,match='manifeste'):reporting.verify_sources({name:'0'*64})


@pytest.mark.parametrize('phase',['before','during'])
def test_late_sources_cannot_silently_change_producer(tmp_path,context,request_data,monkeypatch,phase):
    request_data['variables']=[]
    request_data['criteria']=[geometry_criterion(target=context['design'].total_length_cm/100)]
    c=Contract(request_data,context);plan={'provenance':reporting.provenance()}
    original=reporting.source_files
    changed=dict(original(),**{'tools/late_module.py':'0'*64})
    if phase=='before':monkeypatch.setattr(reporting,'source_files',lambda:changed)
    else:
        real=pipeline.Evaluator.__call__
        def late(self,values):
            result=real(self,values)
            monkeypatch.setattr(reporting,'source_files',lambda:changed)
            return result
        monkeypatch.setattr(pipeline.Evaluator,'__call__',late)
    with pytest.raises(ValueError,match='sources chargées modifiées'):pipeline.execute(c,plan,tmp_path)
    assert not list(tmp_path.glob('checkpoint_*.json'))
    assert not (tmp_path/'result.json').exists()


def test_worker_rejects_parent_source_change_before_execute(tmp_path,monkeypatch):
    _,plan=pipeline.load_inputs(EXAMPLE/'config.yaml',EXAMPLE/'design.json',EXAMPLE/'request.yaml')
    plan['provenance']['loaded_sources_sha256']['tools/constrained_design.py']='0'*64
    monkeypatch.setattr(pipeline,'execute',lambda *a:pytest.fail('execute before parent check'))
    with pytest.raises(ValueError,match='entrées/sources'):
        pipeline.worker(dict(config=EXAMPLE/'config.yaml',design=EXAMPLE/'design.json',
                             request=EXAMPLE/'request.yaml',output=tmp_path,plan=plan))


def test_cli_loaded_bytes_change_refused_before_run(tmp_path,monkeypatch,capsys):
    import tools.constrained_design as cli
    monkeypatch.setattr(cli,'ENTRY_SHA256','0'*64)
    monkeypatch.setattr(cli,'run',lambda *a,**k:pytest.fail('run before provenance guard'))
    assert cli.main(['--config','missing','--design','missing','--request','missing',
                     '--output-dir',str(tmp_path/'out'),'--dry-run'])==2
    assert 'provenance CLI' in json.loads(capsys.readouterr().out)['reason']
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('phase',['before','during'])
def test_calculation_snapshot_rejects_in_place_mutation(tmp_path,context,request_data,monkeypatch,phase):
    request_data['variables']=[]
    request_data['criteria']=[geometry_criterion(target=context['design'].total_length_cm/100)]
    c=Contract(request_data,copy.deepcopy(context))
    snapshot=reporting.calculation_context(c)
    snapshot_hash=reporting.fingerprint(snapshot)
    plan={'provenance':reporting.provenance(),'calculation_context':snapshot}
    def mutate():c.request['metadata']={'unexpected':True}
    if phase=='before':mutate()
    else:
        real=pipeline.Evaluator.__call__
        def late(self,values):
            result=real(self,values);mutate();return result
        monkeypatch.setattr(pipeline.Evaluator,'__call__',late)
    with pytest.raises(ValueError,match='contexte'):pipeline.execute(c,plan,tmp_path)
    assert reporting.fingerprint(snapshot)==snapshot_hash
    assert not (tmp_path/'result.json').exists()


@pytest.mark.parametrize('dry',[True,False])
def test_copied_cli_valid_inputs_still_refused(tmp_path,dry):
    copied=tmp_path/'copied_cli.py';copied.write_bytes((ROOT/'tools/constrained_design.py').read_bytes())
    out=tmp_path/'out'
    done=_correction_cli([str(copied),'--config',str(EXAMPLE/'config.yaml'),
        '--design',str(EXAMPLE/'design.json'),'--request',str(EXAMPLE/'request.yaml'),
        '--output-dir',str(out)]+(['--dry-run'] if dry else []),cwd=tmp_path)
    assert done.returncode==2
    assert 'provenance CLI' in json.loads(done.stdout)['reason']
    assert not out.exists()


def test_cli_checkpoint_altered_new_api_process(cli_bundle,tmp_path):
    out,_,_=cli_bundle
    latest=sorted(out.glob('checkpoint_*.json'))[-1]
    value=json.loads(latest.read_text());value['candidate']['variables_si'][0]+=.001
    (tmp_path/latest.name).write_text(json.dumps(value))
    code=f'''import sys
from didgeridoo_optimizer.pipeline.constrained_design import load_inputs
from didgeridoo_optimizer.reporting.constrained_design import read_checkpoint
c,_=load_inputs({str(EXAMPLE/'config.yaml')!r},{str(EXAMPLE/'design.json')!r},{str(EXAMPLE/'request.yaml')!r})
try:
    read_checkpoint({str(tmp_path)!r},c)
except ValueError as exc:
    assert 'altéré' in str(exc)
else:
    raise AssertionError('altered checkpoint accepted')
assert 'tools.constrained_design' not in sys.modules
'''
    done=_correction_cli(['-c',code],cwd=tmp_path)
    assert done.returncode==0,done.stdout+done.stderr


def test_client_api_run_preserves_parent_and_worker_manifests(tmp_path,context,request_data):
    request_data['variables']=[]
    played=dict(request_data['criteria'][0],observable='played_frequency',level='played',role='observe')
    played.pop('mode')
    request_data['criteria']=[geometry_criterion(target=context['design'].total_length_cm/100),played]
    request=tmp_path/'request.json';request.write_text(json.dumps(request_data))
    out=tmp_path/'result'
    code=f'''import json,sys
from didgeridoo_optimizer.pipeline.constrained_design import run
assert 'tools.constrained_design' not in sys.modules
result=run({str(EXAMPLE/'config.yaml')!r},{str(EXAMPLE/'design.json')!r},{str(request)!r},{str(out)!r})
assert 'tools.constrained_design' not in sys.modules
print(json.dumps(result))
'''
    done=_correction_cli(['-c',code],cwd=tmp_path)
    assert done.returncode==0,done.stdout+done.stderr
    response=json.loads(done.stdout)
    assert response['ok'] and response['child_exit_code']==0 and response['child_reaped']
    assert response['hard_conforming'] and not response['request_fully_covered']
    result=json.loads((out/'result.json').read_text())
    assert result['final']['criteria'][1]['observable']=='played_frequency'
    assert result['final']['criteria'][1]['status']=='unsupported'
    assert 'tools/constrained_design.py' not in result['plan']['parent_sources_checked_sha256']
    assert 'tools/constrained_design.py' in result['plan']['provenance']['loaded_sources_sha256']
