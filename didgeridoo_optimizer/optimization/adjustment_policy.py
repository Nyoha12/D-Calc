"""Command-only scalar adapters; fabrication and historical requests stay fixed."""
from __future__ import annotations

import copy
from fractions import Fraction
from types import SimpleNamespace

from .design_contract import InvalidRequest, obj, integer, number
from .assembly_contract import physical_expression
from ..geometry.assemblies import exact_quantity, field_info, set_field

SCHEMA = 'dcalc.adjustment_policy.v1'
LIMITS = dict(candidates=65, evaluations=300, iterations=30, projections=2048,
              spectral_calls=10000, frequencies=2000000,
              frequency_segment_product=200000000, segments=200000,
              seconds=165, memory_mib=700, output_mib=60)
ROUNDING = 'float(Fraction(str(q_nominal_si))+Fraction(str(delta_si))); native float setter'


def interval(raw, label):
    obj(raw, {'unit', 'bounds'}, label, {'unit', 'bounds'})
    if raw['unit'] != 'm' or not isinstance(raw['bounds'], list) or len(raw['bounds']) != 2:
        raise InvalidRequest(label+': bornes absolues SI en m requises')
    lo, hi = [number(x, label) for x in raw['bounds']]
    if not lo < hi:
        raise InvalidRequest(label+': bornes strictement ordonnées')
    return [lo, hi]


def job_contract(raw):
    obj(raw, {'schema_version','audit_job','policy','information','budgets'}, 'JOB',
        {'schema_version','audit_job','policy','information','budgets'})
    if raw['schema_version'] != SCHEMA:
        raise InvalidRequest('version JOB adjustment inconnue')
    obj(raw['budgets'], LIMITS, 'budgets', LIMITS)
    budgets = {k:integer(raw['budgets'][k], 1, v, 'budgets.'+k) for k,v in LIMITS.items()}
    p = raw['policy']; obj(p, {'kind','block','q','delta'}, 'policy', {'kind'})
    kind = p['kind']
    expected = {'kind'} if kind == 'fixed' else {'kind','block','q'}
    if kind == 'shared_offset': expected.add('delta')
    if kind not in ('fixed','per_configuration','shared_offset') or set(p) != expected:
        raise InvalidRequest('politique/champs non pris en charge')
    if raw['information'] not in ('none','scenario_known') or (kind != 'fixed' and raw['information'] != 'scenario_known'):
        raise InvalidRequest('politique adaptative exige information=scenario_known; autre portée unsupported')
    policy = dict(kind=kind, information=raw['information'], rounding=ROUNDING if kind=='shared_offset' else 'native float setter')
    if kind != 'fixed':
        if not isinstance(p['block'], str): raise InvalidRequest('block explicite requis')
        policy.update(block=p['block'], q_bounds=interval(p['q'],'policy.q'))
        if policy['q_bounds'][0] < 0: raise InvalidRequest('q négatif')
    if kind == 'shared_offset':
        policy['delta_bounds'] = interval(p['delta'],'policy.delta')
        if not policy['delta_bounds'][0] <= 0 <= policy['delta_bounds'][1]:
            raise InvalidRequest('graine delta=0 hors bornes')
    return policy, budgets


def bind_policy(policy, kind, nominal, request, projections):
    """Reject historical couplings we cannot preserve for every scalar trial."""
    p = copy.deepcopy(policy)
    if kind == 'assembly':
        catalogue = request.get('catalogue', [{'id':'nominal','values':{}}])
        if len(catalogue) != 1 or catalogue[0]['values'] or catalogue[0].get('orientations'):
            raise InvalidRequest('catalogue physique non trivial: fixité non garantie')
    if p['kind'] == 'fixed':
        p['nominal_q'] = {}; return p
    if kind != 'assembly': raise InvalidRequest('politique adaptative réservée à ASSEMBLY')
    if not any(b['id']==p['block'] and b['kind']=='telescope' for b in nominal['blocks']):
        raise InvalidRequest('UN bloc télescopique déclaré requis')
    fields = {f'configurations.{c}.q.{p["block"]}' for c in nominal['configurations']}
    for v in request['variables']:
        if fields.intersection(v['fields']) and len(v['fields']) > 1:
            raise InvalidRequest('liaison historique touchée: capacité de commande liée non implémentée')
    for d in request.get('derived', []):
        deps = physical_expression(d['expression'], nominal)[2]
        if d['field'] in fields or fields.intersection(deps):
            raise InvalidRequest('relation dérivée touchée: capacité non implémentée, relation conservée')
    p['nominal_q'] = {}
    for c in nominal['configurations']:
        q = field_info(nominal, f'configurations.{c}.q.{p["block"]}', exact=True)[0]
        if Fraction(str(float(q))) != q:
            raise InvalidRequest('graine nominale SI non représentable')
        if not Fraction(str(p['q_bounds'][0])) <= q <= Fraction(str(p['q_bounds'][1])):
            raise InvalidRequest('graine nominale hors bornes q; aucun clamp')
        p['nominal_q'][c] = float(q)
    return p


def groups(policy, projections):
    if policy['kind'] != 'per_configuration':
        return [dict(id='all', configurations=list(dict.fromkeys(p['configuration'] for p in projections)),
                     projections=[p['id'] for p in projections])]
    return [dict(id=c, configurations=[c], projections=[p['id'] for p in projections if p['configuration']==c])
            for c in dict.fromkeys(p['configuration'] for p in projections)]


def commands(policy, configurations, values):
    """Exact decimal sum then binary64 serialization; bounds use serialized law."""
    if policy['kind']=='fixed':
        if len(values): raise InvalidRequest('fixed: zéro commande variable')
        return []
    if len(values)!=1: raise InvalidRequest('UN scalaire requis')
    x=number(values[0],'commande'); xf=Fraction(str(x))
    bounds = policy['delta_bounds'] if policy['kind']=='shared_offset' else policy['q_bounds']
    if not Fraction(str(bounds[0])) <= xf <= Fraction(str(bounds[1])):
        raise InvalidRequest('commande hors bornes')
    rows=[]
    for c in configurations:
        nominal=policy['nominal_q'][c]
        rational=Fraction(str(nominal))+xf if policy['kind']=='shared_offset' else xf
        q=float(rational); serialized=Fraction(str(q))
        if not Fraction(str(policy['q_bounds'][0])) <= serialized <= Fraction(str(policy['q_bounds'][1])):
            raise InvalidRequest('q effectif hors bornes absolues')
        rows.append(dict(configuration=c,block=policy['block'],nominal_q_si=nominal,
            q_si=q,delta_si=x if policy['kind']=='shared_offset' else None,
            rational_sum=str(rational),serialization_error=str(serialized-rational),unit='m'))
    return rows


def apply_commands(raw, rows):
    result=copy.deepcopy(raw)
    for row in rows:
        set_field(result,f'configurations.{row["configuration"]}.q.{row["block"]}',row['q_si'])
    return result


def adapter(policy, group, projections, limits, remaining, seconds):
    selected=[p for p in projections if p['id'] in group['projections']]
    criteria=[dict(c,id=p['id']+':'+c['id']) for p in selected for c in p['template'].criteria]
    if policy['kind']=='fixed': variables=[]; initial=[]
    else:
        low,high=policy['delta_bounds'] if policy['kind']=='shared_offset' else policy['q_bounds']
        variables=[dict(low=low,high=high)]
        initial=[0. if policy['kind']=='shared_offset' else policy['nominal_q'][group['configurations'][0]]]
    return SimpleNamespace(variables=variables,initial=initial,criteria=criteria,
        preference_supported=all(p['template'].preference_supported for p in selected),
        budgets=dict(evaluations=remaining,
            iterations=0 if any(c['role']=='hard' and c['unsupported_reason'] for c in criteria) else limits['iterations'],
            seconds=max(0.,seconds)))


def candidate_status(observations):
    hard=[r for o in observations for r in o['criteria'] if r['role']=='hard']
    valid=all(o['geometry_status']=='valid' for o in observations)
    witness=valid and bool(hard) and all(r['status']=='satisfied' for r in hard)
    if witness: return 'witness_found'
    if any(o['geometry_status']=='geometry_violated' for o in observations) or any(r['status']=='violated' for r in hard):
        return 'candidate_violated'
    if any(r['status']=='unsupported' for r in hard): return 'unsupported'
    if any(r['status']=='out_of_domain' for r in hard): return 'out_of_domain'
    return 'search_not_resolved'
