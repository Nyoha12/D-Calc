"""Portable seeded orchestration tests. All scored evaluators are synthetic doubles.

Native admission and geometry-only screening remain real; no instrumental fit or
played integration is invoked by this module.
"""
import copy
import json
from pathlib import Path

import pytest
import yaml

from didgeridoo_optimizer.optimization import played_search as core
from didgeridoo_optimizer.pipeline import played_search as flow
from didgeridoo_optimizer.reporting import played_search as report

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT/'project_specs/examples/seeded_search'


def write(path, value):
    path.write_text(yaml.safe_dump(value, sort_keys=False) if path.suffix in ('.yaml','.yml') else
                    json.dumps(value, allow_nan=False, indent=2)+'\n')
    return path


def change(path, edit):
    value = json.loads(path.read_text()) if path.suffix=='.json' else yaml.safe_load(path.read_text())
    edit(value); write(path, value)
    return value


@pytest.fixture
def job(tmp_path):
    for name in ('reference.json','short_wide.json','long_wide.json','request.yaml','job-filter.yaml'):
        (tmp_path/name).write_bytes((EXAMPLE/name).read_bytes())
    native = EXAMPLE.parent/'geometry_state'
    config = yaml.safe_load((native/'config.yaml').read_text())
    config['materials'] = dict(database_file=str(ROOT/'project_specs/materials_base_v1.yaml'),
                              variant_rules_file=str(ROOT/'project_specs/wood_variant_rules_v1.yaml'))
    write(tmp_path/'config.yaml', config)
    (tmp_path/'experiment.json').write_bytes((native/'experiment.json').read_bytes())
    path = tmp_path/'job-filter.yaml'
    change(path, lambda r: (r.update(config='config.yaml'), r['scenarios'][0].update(template='experiment.json')))
    return path


def test_native_admission_units_groups_bytes_and_export_shape(job):
    contract, p = flow.load_inputs(job)
    assert p['starts']['selected'][0]['variables_si'] == [.93, .03125]
    assert p['variables'][1]['unit'] == 'mm'
    for s in p['starts']['selected']:
        assert s['native_design'] == contract.generate(s['variables_si']).as_dict()
        assert s['origin']['source']['sha256'] == report.file_sha256(s['origin']['source']['path'])
        # This is the native DESIGN shape also exported by constrained_design.
        write(job.parent/(s['origin']['id']+'.json'), s['native_design'])
    assert flow.preflight(job)['starts']['selected'][0]['variables_si'] == [.93, .03125]


@pytest.mark.parametrize('mutation',[
    lambda s:s.pop('mode'), lambda s:s.update(extra=True), lambda s:s.update(include_initial=1),
    lambda s:s.update(mode='adaptive'), lambda s:s.update(profiles=[]),
    lambda s:s.update(profiles=s['profiles']*5),
    lambda s:s['profiles'][0].update(id='initial'),lambda s:s['profiles'][0].update(id='nominal'),
    lambda s:s['profiles'][1].update(id=s['profiles'][0]['id']),
    lambda s:s['profiles'][0].update(id='../escape'),lambda s:s['profiles'][0].update(design=True),
    lambda s:s['profiles'][0].update(design='/tmp/absolute.json'),
    lambda s:s['profiles'][0].update(model_in='B.json'),lambda s:s['profiles'][0].pop('design'),
])
def test_strict_starts_schema_before_destination(job, mutation):
    change(job, lambda r:mutation(r['search']['starts']))
    out=job.parent/'never'
    with pytest.raises(ValueError):flow.run(job,out,dry_run=True)
    assert not out.exists()


@pytest.mark.parametrize('text',['{"x": NaN}', '{"x": Infinity}', 'x: .nan\n','x: 1\nx: 2\n','a: &a [*a]\n'])
def test_nonfinite_duplicate_and_cycles(job,text):
    (job.parent/'short_wide.json').write_text(text)
    with pytest.raises(ValueError):flow.preflight(job)


@pytest.mark.parametrize('mutation',[
    lambda d:d.update(id='different'),lambda d:d['metadata'].update(annotation='changed'),
    lambda d:d['segments'][0].update(material_id='wood'),lambda d:d['segments'][0].update(kind='cone'),
    lambda d:d['segments'][0].update(length_cm=96.),lambda d:d['segments'][0].update(length_cm=True),
    lambda d:d['segments'][0].update(d_out_cm=3.5),lambda d:d['segments'].append(copy.deepcopy(d['segments'][0])),
    lambda d:d['segments'][0].update(profile_params={'power':2}),
])
def test_original_locks_and_false_profiles(job,mutation):
    change(job.parent/'short_wide.json',mutation)
    with pytest.raises(ValueError):flow.preflight(job)


def test_annotation_types_cannot_hide_behind_python_numeric_equality(job):
    for name in ('reference.json','short_wide.json','long_wide.json'):
        change(job.parent/name,lambda d:d['metadata'].update(flag=1))
    change(job.parent/'short_wide.json',lambda d:d['metadata'].update(flag=True))
    with pytest.raises(ValueError,match='representable'):flow.preflight(job)


def test_paths_symlink_oversize_and_sections_before_allocation(job):
    source=job.parent/'short_wide.json';saved=source.read_bytes();source.unlink();source.symlink_to(job.parent/'long_wide.json')
    with pytest.raises(ValueError):flow.preflight(job)
    source.unlink();source.write_bytes(b' '*(2*flow.MIB+1))
    with pytest.raises(ValueError):flow.preflight(job)
    source.write_bytes(saved)
    change(source,lambda d:d.update(segments=d['segments']*129))
    with pytest.raises(ValueError,match='128'):flow.preflight(job)


def test_native_derived_and_exact_representation_no_new_tolerance(job):
    change(job.parent/'request.yaml',lambda r:(r['variables'][1].update(fields=['segments.0.d_in_cm']),
        r.update(derived=[dict(id='out',field='segments.0.d_out_cm',expression={'field':'segments.0.d_in_cm'},unit='mm',bounds=[30,40])])))
    p=flow.preflight(job);assert p['starts']['selected'][0]['variables_si']==[.93,.03125]
    # Within the native relation tolerance, but not the exact generated profile.
    change(job.parent/'short_wide.json',lambda d:d['segments'][0].update(d_out_cm=3.1250000000001))
    with pytest.raises(ValueError,match='representable'):flow.preflight(job)
    change(job.parent/'short_wide.json',lambda d:d['segments'][0].update(d_out_cm=3.5))
    with pytest.raises(ValueError,match='dérivée'):flow.preflight(job)


def test_native_derived_metadata_only_and_nonrepresentable_si(job):
    change(job.parent/'short_wide.json',lambda d:(d['metadata'].update(total_length_cm=-123),
        d['segments'][0].update(position_start_cm=12,position_end_cm=13)))
    assert flow.preflight(job)['starts']['selected'][0]['native_design']['metadata']['total_length_cm']==93
    # Decimal roundtrip changes these native dimensions; no silent normalization.
    change(job.parent/'short_wide.json',lambda d:d['segments'][0].update(d_in_cm=3.4,d_out_cm=3.4))
    with pytest.raises(ValueError,match='representable'):flow.preflight(job)


def test_assembly_starts_explicitly_refused(job):
    change(job,lambda r:r.update(input=dict(kind='assembly',assembly='absent.yaml',request='request.yaml')))
    with pytest.raises(ValueError,match='assembly with starts'):flow.preflight(job)


def test_source_change_after_preflight_and_rehashed_plan(job):
    p=flow.preflight(job)
    for edit in (lambda q:q['job']['search']['starts'].update(mode='poll'),
                 lambda q:q['starts']['selected'][0]['origin'].update(id='fake'),
                 lambda q:q['starts']['selected'][0]['variables_si'].__setitem__(0,.92),
                 lambda q:q['starts']['selected'][0]['native_design']['metadata'].update(nature='fake')):
        forged=copy.deepcopy(p);edit(forged);forged['context_sha256']=flow.digest({k:v for k,v in forged.items() if k!='context_sha256'})
        with pytest.raises(ValueError):flow.check_plan(forged)
    with (job.parent/'short_wide.json').open('ab') as stream:stream.write(b'\n')
    with pytest.raises(ValueError,match='bytes changed'):flow.check_plan(p)


def synthetic(score=None, witness=False):
    return dict(conforming=witness,max_violation_normalized=score,observed_violation_lower_bound=score,
                comparable_hard_rows=[],status='conforming' if witness else 'unresolved' if score is None else 'violated')


def spec(points, mode='poll', initial=False, stop=False):
    starts=dict(initial=dict(origin=dict(id='initial'),variables_si=[.5,.5],stage='initial'),selected=[])
    if initial:starts['selected'].append(starts['initial'])
    starts['selected'] += [dict(origin=dict(id='s'+str(i)),variables_si=list(x),stage='explicit_start') for i,x in enumerate(points)]
    search=dict(normalized_steps=[.25],directions=[-1,1],stop_on_witness=stop,
                starts=dict(include_initial=initial,mode=mode))
    return starts,search


def poll(points, *, mode='poll',initial=False,stop=False,maximum=16,descriptive=False,evaluator=None):
    starts,search=spec(points,mode,initial,stop);calls=[]
    def evaluate(x,a,i,stage,**kw):
        calls.append((x,stage,kw['start_origin']['id']))
        return synthetic() if evaluator is None else evaluator(x,a,i,stage)
    result=core.poll([dict(id='x',low=0.,high=1.),dict(id='y',low=0.,high=1.)],[.5,.5],['nominal'],search,
        maximum,evaluate,descriptive=descriptive,starts=starts)
    return result,calls


def test_multi_start_order_modes_initial_and_simultaneous_changes():
    r,calls=poll([[.25,.75],[.75,.25]],mode='evaluate_only',initial=True)
    assert [c[0] for c in calls]==[[.5,.5],[.25,.75],[.75,.25]]
    assert r['termination']=='evaluation_only'
    r,calls=poll([[.25,.75],[.75,.25]])
    assert [c[0] for c in calls[:6]]==[[.25,.75],[.75,.25],[0.,.75],[.5,.75],[.25,.5],[.25,1.]]
    assert all('start_origin' in d for d in r['decisions'])
    assert r['decisions'][0]['reason']=='initial_excluded'


def test_global_dedup_anchors_and_candidate_budget():
    r,calls=poll([[.5,.5],[.5,.5],[.75,.5]],maximum=3)
    assert [c[0] for c in calls]==[[.5,.5],[.75,.5],[.25,.5]]
    assert r['termination']=='candidate_budget'
    assert any(d.get('duplicate_of')==1 for d in r['decisions'])
    r,calls=poll([[.5,.5],[.5,.5]],maximum=16)
    assert len(calls)==5 and len([d for d in r['decisions'] if d['action']=='skip_duplicate'])==5


def test_witness_stop_nohard_unknown_and_no_compensation():
    r,calls=poll([[.25,.75],[.75,.25]],initial=True,stop=True,evaluator=lambda *a:synthetic(0.,True))
    assert len(calls)==1 and r['termination']=='feasible_witness_found'
    assert [d['reason'] for d in r['decisions'][1:]]==['feasible_witness_found']*2
    r,calls=poll([[.25,.75],[.75,.25]],descriptive=True)
    assert len(calls)==2 and r['termination']=='descriptive_initials_only'
    assert r['best']['max_violation_normalized'] is None and r['witness'] is None
    rows=[dict(id='a',role='hard',status='pass',violation_normalized=0.),dict(id='b',role='hard',status='fail',violation_normalized=2.)]
    assert core.verdict(rows,units_complete=True)['max_violation_normalized']==2.


def test_shared_native_budget_and_no_reservation_on_duplicate(tmp_path):
    limits=dict(core.LIMITS,fits=1,steps=3)
    b=flow.Budget(limits,tmp_path)
    def evaluate(*args):
        value=synthetic()
        try:b.reserve('fit',1,1);b.launched('fit',0)
        except flow.BudgetStop as exc:value['stop_reason']=str(exc)
        return value
    r,calls=poll([[.5,.5],[.5,.5],[.75,.5]],mode='evaluate_only',evaluator=evaluate)
    assert len(calls)==2 and b.counts['fits']==b.reserved['fits']==1
    assert r['termination']=='total_fits'
    with pytest.raises(flow.BudgetStop,match='steps'):b.reserve('trajectory',1,1,4)


def test_legacy_poll_exact_order_decisions_values_and_no_new_fields():
    calls=[]
    def evaluate(x,a,i,stage):calls.append((x,a,i,stage));return synthetic(float(i))
    result=core.poll([dict(id='x',low=0.,high=1.)],[.5],['a','b'],
        dict(normalized_steps=[.25,.125],directions=[-1,1],stop_on_witness=False),16,evaluate)
    assert calls==[([x],a,i+1,'initial' if j==0 else 'coordinate_poll') for i,(a,j,x) in enumerate(
        [(a,j,x) for a in ['a','b'] for j,x in enumerate([.5,.25,.75,.375,.625])])]
    assert result['termination']=='poll_exhausted' and result['best']['candidate']==1 and result['witness'] is None
    assert result['decisions']==[dict(candidate=i,action='select' if i==1 else 'retain_incumbent',score=float(i),status='violated') for i in range(1,11)]
    assert all('start_origin' not in row for row in result['history'])


def test_replay_checks_sequence_without_science(job,monkeypatch):
    p=flow.preflight(job)
    def evaluate(x,a,i,stage,**kw):return dict(synthetic(),start_origin=kw['start_origin'])
    r=core.poll(p['variables'],p['initial'],p['alternatives'],p['job']['search'],3,evaluate,starts=p['starts'])
    flow.verify_search(p,r)
    for edit in (lambda q:q['history'][0]['start_origin'].update(id='long_wide'),
                 lambda q:q['history'][0]['variables_si'].__setitem__(0,.92),
                 lambda q:q['decisions'][0].update(reason='invented'),
                 lambda q:q.update(termination='poll_exhausted')):
        forged=copy.deepcopy(r);edit(forged)
        with pytest.raises(ValueError):flow.verify_search(p,forged)
    prefix={k:copy.deepcopy(r[k]) for k in ('history','best','witness','decisions')}
    prefix['decisions']=[]
    with pytest.raises(ValueError,match='Checkpoint decisions'):flow.verify_search(p,prefix,prefix=True)


@pytest.mark.parametrize('resource,reason',[('trajectories','total_trajectories'),('steps','total_steps_charged'),
    ('seconds','total_seconds'),('storage','child_does_not_fit_remaining_storage')])
def test_shared_trajectory_time_and_storage_limits_across_starts(tmp_path,monkeypatch,resource,reason):
    limits=dict(core.LIMITS,trajectories=1 if resource=='trajectories' else 8,
                steps=3 if resource=='steps' else 100, output_mib=3 if resource=='storage' else 800)
    b=flow.Budget(limits,tmp_path);used=[0]
    if resource=='storage':monkeypatch.setattr(report,'size',lambda out:used[0])
    def evaluate(*args):
        value=synthetic()
        try:
            b.reserve('trajectory',1,flow.MIB,2);b.launched('trajectory',2)
            if resource=='seconds':b.start-=limits['seconds']
            if resource=='storage':used[0]+=flow.MIB
        except flow.BudgetStop as exc:value['stop_reason']=str(exc)
        return value
    r,calls=poll([[.5,.5],[.5,.5],[.75,.5]],mode='evaluate_only',evaluator=evaluate)
    assert len(calls)==2 and r['termination']==reason
    assert b.counts['trajectories']==1 and b.counts['steps_charged']==2
