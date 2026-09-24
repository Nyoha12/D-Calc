"""Loaded response integration: frozen physics, boundary flow, ODE and real CLI."""
from dataclasses import replace
import csv
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from didgeridoo_optimizer.acoustics import forced_response as fr, transfer_matrix as tm
from didgeridoo_optimizer.acoustics.radiation import radiation_impedance
from didgeridoo_optimizer.acoustics.radiation_models import get_radiation_model, LegacyRadiationModel, SilvaRadiationModel
from didgeridoo_optimizer.acoustics.losses import LegacyBetaLossModel
from didgeridoo_optimizer.acoustics.thermoviscous import CK_DRY_20C as AIR, ZwikkerKostenLossModel
from didgeridoo_optimizer.materials.database import MaterialDatabase
from didgeridoo_optimizer.reporting import forced_response as report
from didgeridoo_optimizer.tests.test_forced_response_cli import inputs, forbid_phases
from tools import forced_response_compare as tool

ROOT=Path(__file__).resolve().parents[2]
REFERENCE=json.loads((Path(__file__).parent/'fixtures/radiation_reference.json').read_text())


class BoundarySpy:
    def __init__(self, name, events):
        self.actual=get_radiation_model(name); self.events=events; self.results=[]
    def evaluate(self, omega, radius, air):
        self.events.append(('radiation',radius,air))
        result=self.actual.evaluate(omega,radius,air); self.results.append(result)
        return result


class LossSpy:
    def __init__(self, model, events):
        self.actual=model; self.name=model.name; self.events=events; self.rows=[]
    def evaluate(self, omega, diameter, material, nominal, air):
        self.events.append(('loss',diameter))
        value=self.actual.evaluate(omega,diameter,material,nominal,air)
        self.rows.append((diameter,material.id,nominal,value.alpha_total.copy(),value.k_complex.copy(),value.zc_complex.copy()))
        return value


@pytest.mark.parametrize('database', [False,True])
@pytest.mark.parametrize('lossname', ['legacy','zk'])
def test_single_boundary_physical_radius_frozen_propagation_and_parity(database,lossname,monkeypatch):
    design=tool.builtin_design('body_bell')
    mat=tool.synthetic_material(); materials={mat.id:mat}
    if database:
        materials=MaterialDatabase.from_yaml(ROOT/'project_specs/materials_base_v1.yaml')
        design=design.copy(segments=[replace(s,material_id='pvc_pressure') for s in design.segments])
    f=np.array([40.,67.65,70.,400.,1000.,1500.]); air=AIR.as_air_properties()
    loss=LegacyBetaLossModel() if lossname=='legacy' else ZwikkerKostenLossModel(AIR)
    actual_backend=fr.transfer_from_slices
    captured=[]
    def capture(freq,slices,load,**kwargs):
        rows=list(slices)
        captured.append((load.copy(),[length for length,_,_ in rows]))
        return actual_backend(freq,iter(rows),load,**kwargs)
    monkeypatch.setattr(fr,'transfer_from_slices',capture)
    for h in (1.,.5,.25):
        mesh=fr.prepare_mesh(design,h,len(f)); before=mesh.as_dict(); common=None
        for name in ('legacy','silva_unflanged','silva_flanged','legacy'):
            events=[]; boundary=BoundarySpy(name,events); spy=LossSpy(loss,events)
            transfer=fr.loaded_transfer(f,mesh,materials,air,exit_radius_m=.06,loss_model=spy,radiation_model=boundary)
            assert events[0]==('radiation',.06,air) and len(boundary.results)==1
            np.testing.assert_array_equal(captured[-1][0],boundary.results[0].impedance)
            assert captured[-1][1]==[s.length_cm/100 for s in reversed(mesh.segments)]
            row=spy.rows
            if common is None: common=row
            for a,b in zip(common,row,strict=True):
                assert a[:3]==b[:3]
                for x,y in zip(a[3:],b[3:]): np.testing.assert_array_equal(x,y)
            events=[]; boundary2=BoundarySpy(name,events)
            expected=tm.input_impedance(f,mesh,materials,air,exit_radius_m=.06,loss_model=LossSpy(loss,events),radiation_model=boundary2)
            assert events[0]==('radiation',.06,air) and len(boundary2.results)==1
            np.testing.assert_array_equal(transfer['load'],boundary2.results[0].impedance)
            np.testing.assert_allclose(transfer['transfers']['Zin'].values(),expected,rtol=1e-10,atol=1e-7)
        assert mesh.as_dict()==before


@pytest.mark.parametrize('name',['legacy','silva_unflanged','silva_flanged'])
@pytest.mark.parametrize('lossname',['lossless','legacy','zk'])
def test_analytic_cylinder_abcd_power_and_sources(name,lossname):
    f=np.array([40.,69.5,70.,140.,400.,1000.,1500.]); air=AIR.as_air_properties()
    z0=AIR.rho*AIR.c/(np.pi*.015**2)
    load=get_radiation_model(name).evaluate(2*np.pi*f,.015,air).impedance
    if lossname=='lossless':
        k=2*np.pi*f/AIR.c; zc=np.full(len(f),z0)
    else:
        model=LegacyBetaLossModel() if lossname=='legacy' else ZwikkerKostenLossModel(AIR)
        pair=model.evaluate(2*np.pi*f,.03,tool.synthetic_material(),z0,air)
        k,zc=pair.k_complex,pair.zc_complex
    # Independent loaded cylinder and moderate two-section ABCD products.
    for slices in ([ (1.21,k,zc) ],[(.6,k,zc),(.61,k*1.02,zc*.8)]):
        a,d=np.ones(len(f),complex),np.ones(len(f),complex)
        b,c=np.zeros(len(f),complex),np.zeros(len(f),complex)
        for length,wave,z in slices:
            co=np.cos(wave*length); si=np.sin(wave*length)
            bi=1j*z*si; ci=1j*si/z
            a,b,c,d=a*co+b*ci,a*bi+b*co,c*co+d*ci,c*bi+d*co
        p=a*load+b; u=c*load+d
        tr=fr.transfer_from_slices(f,reversed(slices),load,zref=z0)
        for key,value in dict(Zin=p/u,Hu=1/u,Yt=1/p,Zt=load/u,Hp=load/p).items():
            np.testing.assert_allclose(tr['transfers'][key].values(),value,rtol=1e-10,atol=1e-13)
        flow=fr.apply_source(tr,'volume_flow',1e-6)
        pressure=fr.apply_source(tr,'pressure',flow['ports']['p1'].values())
        for key in flow['ports']:
            np.testing.assert_allclose(flow['ports'][key].values(),pressure['ports'][key].values(),rtol=1e-11)
        np.testing.assert_allclose(flow['powers']['Pload']['value'],.5*load.real*abs(flow['ports']['U2'].values())**2,rtol=1e-11)
        np.testing.assert_allclose(flow['powers']['eta']['value'],pressure['powers']['eta']['value'],rtol=1e-11)
        if lossname in {'lossless','zk'}:
            assert all(v>=-e for v,e in zip(flow['powers']['Pdiss']['value'],flow['powers']['Pdiss']['roundoff_tolerance_w']))


def test_legacy_selection_exact_and_interleaved_defaults():
    f=np.array([40.,70.,300.,500.,1000.,1500.]); air=AIR.as_air_properties()
    mat=tool.synthetic_material(); materials={mat.id:mat}
    for case in tool.PROFILES:
        design=tool.builtin_design(case); mesh=fr.prepare_mesh(design,.5,len(f)); radius=design.segments[-1].d_out_cm/200
        base=tm.input_impedance(f,mesh,materials,air,exit_radius_m=radius)
        first=fr.loaded_transfer(f,mesh,materials,air,exit_radius_m=radius)
        for name in ('silva_unflanged','silva_flanged','legacy'):
            tr=fr.loaded_transfer(f,mesh,materials,air,exit_radius_m=radius,radiation_model=get_radiation_model(name))
            zin=tm.input_impedance(f,mesh,materials,air,exit_radius_m=radius,radiation_model=get_radiation_model(name))
            if name=='legacy':
                np.testing.assert_array_equal(zin,base)
                np.testing.assert_array_equal(tr['load'],radiation_impedance(2*np.pi*f,radius,air))
                for key in tr['transfers']: assert tr['transfers'][key].payload()==first['transfers'][key].payload()
                for kind in ('pressure','volume_flow'):
                    a,b=fr.apply_source(tr,kind,1.),fr.apply_source(first,kind,1.)
                    assert a['powers']==b['powers']
                    for key in a['ports']: assert a['ports'][key].payload()==b['ports'][key].payload()
        np.testing.assert_array_equal(base,tm.input_impedance(f,mesh,materials,air,exit_radius_m=radius))


def test_new_boundary_independent_cone_ode_convergence():
    rows=REFERENCE['cone_reference']['rows']; f=[r['frequency_hz'] for r in rows]
    design=tool.builtin_design('body_bell'); mat=tool.synthetic_material()
    errors={key:[] for key in ('Zin','Hu','Yt')}
    for h in (1.,.5,.25):
        mesh=fr.prepare_mesh(design,h,len(f))
        tr=fr.loaded_transfer(f,mesh,{mat.id:mat},AIR.as_air_properties(),exit_radius_m=.06,
                             loss_model=ZwikkerKostenLossModel(AIR),radiation_model=SilvaRadiationModel())
        for key in errors:
            expected=np.array([complex(r[key]['real'],r[key]['imag']) for r in rows])
            errors[key].append(np.linalg.norm(tr['transfers'][key].values()-expected)/np.linalg.norm(expected))
    print('New-boundary ODE relative L2:',errors)
    for key,bound in [('Zin',2e-4),('Hu',2e-4),('Yt',6e-4)]:
        e=errors[key]; assert e[-1]<bound and e[0]/e[1]>3 and e[1]/e[2]>3


@pytest.mark.parametrize('suffix',['.yaml','.json'])
@pytest.mark.parametrize('name',['silva_unflanged','silva_flanged'])
def test_real_cli_radiation_context_exports_and_overwrite(suffix,name,tmp_path):
    cp,dp,design=inputs(tmp_path,suffix); before=[p.read_bytes() for p in (cp,dp)]
    out=tmp_path/'output'
    args=[sys.executable,'-B','-m','tools.forced_response_compare','--config',str(cp),'--design',str(dp),
          '--pressure-peak-pa','1','--loss-model','both','--air-reference','ck_dry20','--radiation-model',name,
          '--f-min','40','--f-max','9000','--points','5','--h-cm','10','--output-dir',str(out)]
    run=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stdout+run.stderr
    raw=(out/'forced_response.json').read_text(encoding='utf-8')
    result=json.loads(raw,parse_constant=lambda x:pytest.fail('nonfinite JSON'))
    with (out/'forced_response.csv').open(encoding='utf-8',newline='') as stream: rows=list(csv.DictReader(stream))
    assert len(rows)==10
    assert result['original_context']['config']['frequency_analysis']['n_points']==12
    assert 'radiation' not in result['original_context']['config']
    assert result['effective_parameters']['radiation']['name']==name
    assert result['effective_parameters']['original_air']['humidity_percent']==50
    for model in result['cases'][0]['models']:
        rad=model['radiation']
        assert rad['radius_m']==.015 and rad['ka_out']==model['ka_out']
        assert rad['model_status'][-1]=='extrapolation' and rad['model_reason'][-1]
        assert rad['termination']['last_physical_segment_kind']=='cylinder'
        assert rad['termination']['wall_thickness']=='unknown'
        assert model['numerically_complete'] is True  # extrapolation is not a numerical failure
        assert model['model'] in {'legacy_beta','zwikker_kosten_circular'}
        for row in (r for r in rows if r['model']==model['model']):
            index=model['frequency_hz'].index(float(row['frequency_hz']))
            assert row['radiation_model']==name and row['radiation_model_status']==rad['model_status'][index]
            assert float(row['radiation_n1'])==rad['coefficients']['n1']
            assert float(row['Pload'])==model['powers']['Pload']['value'][index]
    text=(out/'forced_response.txt').read_text(encoding='utf-8')
    assert name in text and 'extrapolation' in text and 'charge legacy' not in text
    assert result['cases'][0]['physical_design']['metadata']==tool.DesignBuilder().build(design).metadata
    assert before==[p.read_bytes() for p in (cp,dp)]
    again=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=45)
    assert again.returncode==1 and (out/'forced_response.json').read_text(encoding='utf-8')==raw


@pytest.mark.parametrize('name',['legacy','silva_unflanged','silva_flanged'])
def test_dry_run_describes_without_any_acoustics(name,tmp_path,monkeypatch,capsys):
    forbid_phases(monkeypatch)
    def forbidden(*a,**k): pytest.fail('Dry-run evaluated acoustics')
    for cls in (LegacyRadiationModel,SilvaRadiationModel,LegacyBetaLossModel,ZwikkerKostenLossModel):
        monkeypatch.setattr(cls,'evaluate',forbidden)
    monkeypatch.setattr(fr,'loaded_transfer',forbidden)
    out=tmp_path/'new'
    assert tool.main(['--case','body_bell','--pressure-peak-pa','1','--radiation-model',name,
                      '--output-dir',str(out),'--dry-run'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['effective']['radiation']['name']==name
    assert result['terminations'][0]['last_physical_segment_kind']=='cone'
    assert 'transposée' in result['terminations'][0]['interpretation']
    assert not out.exists()


def test_export_radiation_alignment_guard():
    design=tool.builtin_design('cylinder'); mat=tool.synthetic_material()
    tr=fr.loaded_transfer([40.,70.],fr.prepare_mesh(design,10,2),{mat.id:mat},exit_radius_m=.015,radiation_model=SilvaRadiationModel())
    model=report.model_payload(tr,fr.apply_source(tr,'pressure',1.),0.)
    model['radiation']['model_reason'].append('bad')
    with pytest.raises(ValueError,match='radiation alignment'):
        report.render_bundle(dict(schema=report.SCHEMA,cases=[dict(physical_design={'id':'test'},models=[model])]))
