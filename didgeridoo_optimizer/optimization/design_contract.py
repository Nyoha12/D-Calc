"""Versioned, dimensioned static design contract; no acoustic equations here."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re

from ..pipeline.design_input import (finite_real, _load_yaml_design,
    _unique_json_mapping, _reject_json_constant, _validate_annotations, validate_design)

SCHEMA = 'dcalc.design_request.v1'
UNITS = {'m': ('length', 1.), 'cm': ('length', .01), 'mm': ('length', .001),
         'Hz': ('frequency', 1.), '1': ('scalar', 1.), 'cent': ('cent', 1.),
         'Pa': ('pressure', 1.)}
CAPABILITIES = {'geometry': 'supported', 'resonance_frequency': 'supported',
    'resonance_ratio': 'supported', 'played_frequency': 'unsupported',
    'toot_accessibility': 'unsupported', 'threshold': 'unsupported',
    'regime': 'unsupported', 'material_property': 'unsupported'}
LIMITS = {'evaluations': 300, 'iterations': 40, 'variables': 16,
          'frequencies': 10000, 'segments': 2048, 'history': 300,
          'seconds': 170, 'frequency_segment_product': 2000000}


class InvalidRequest(ValueError):
    """Malformed or directly contradictory request, before search."""


def obj(value, allowed, where, required=()):
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise InvalidRequest(f'{where}: objet requis')
    unknown = set(value)-set(allowed)
    missing = set(required)-set(value)
    if unknown or missing:
        raise InvalidRequest(f'{where}: champs inconnus {sorted(unknown)}, manquants {sorted(missing)}')
    return value


def number(x, where, positive=False):
    try:
        return finite_real(x, where, positive=positive)
    except ValueError as exc:
        raise InvalidRequest(str(exc)) from exc


def integer(x, lo, hi, where):
    if type(x) is not int or not lo <= x <= hi:
        raise InvalidRequest(f'{where}: entier dans [{lo}, {hi}] requis')
    return x


def identifier(x, where):
    if not isinstance(x, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', x):
        raise InvalidRequest(f'{where}: identifiant invalide')
    return x


def quantity(q, dimension=None):
    obj(q, {'value', 'unit'}, 'quantité', {'value', 'unit'})
    unit = q['unit']
    if not isinstance(unit, str) or unit not in UNITS:
        raise InvalidRequest(f'unité explicite inconnue: {unit!r}')
    dim, factor = UNITS[unit]
    if dimension is not None and dim != dimension:
        raise InvalidRequest(f'unité {unit}: dimension {dimension} requise')
    return number(q['value'], 'value')*factor, dim


def target_quantity(q, dimension):
    if isinstance(q, dict) and 'note' in q:
        obj(q, {'note', 'reference_hz', 'cents'}, 'note', {'note', 'reference_hz', 'cents'})
        match = re.fullmatch(r'([A-G])([#b]?)(-?\d)', str(q['note']))
        if not match or dimension != 'frequency':
            raise InvalidRequest('note: nom avec octave requis, par exemple A3')
        note, accidental, octave = match.groups()
        midi = (int(octave)+1)*12 + {'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}[note]
        midi += {'':0, '#':1, 'b':-1}[accidental]
        return number(q['reference_hz'], 'référence A4 Hz', True)*2**((midi-69)/12+number(q['cents'],'cents')/1200)
    return quantity(q, dimension)[0]


def read_request(path):
    p = Path(path).resolve(strict=True)
    if not p.is_file() or p.stat().st_size > 2*1024**2:
        raise InvalidRequest('REQUEST: fichier régulier <=2 Mio requis')
    raw = p.read_bytes()
    if len(raw) > 2*1024**2:
        raise InvalidRequest('REQUEST trop volumineux')
    text = raw.decode('utf-8-sig')
    if p.suffix.lower() == '.json':
        value = json.loads(text, object_pairs_hook=_unique_json_mapping, parse_constant=_reject_json_constant)
    elif p.suffix.lower() in {'.yaml', '.yml'}:
        value = _load_yaml_design(text)
    else:
        raise InvalidRequest('REQUEST doit être YAML ou JSON')
    _validate_annotations(value, 'request', set())
    return value, {'sha256': hashlib.sha256(raw).hexdigest(), 'name': p.name, 'read': True}


def field_info(raw, path):
    if not isinstance(path, str):
        raise InvalidRequest('référence de champ textuelle requise')
    parts = path.split('.')
    if len(parts) not in (3,4) or parts[0] != 'segments' or not parts[1].isdigit():
        raise InvalidRequest(f'référence invalide: {path}')
    index = int(parts[1])
    if str(index) != parts[1] or index >= len(raw['segments']):
        raise InvalidRequest(f'segment absent: {path}')
    key = '.'.join(parts[2:])
    allowed = {'length_cm','d_in_cm','d_out_cm','profile_params.throat_diameter_cm',
               'profile_params.flare_parameter','profile_params.power'}
    if key not in allowed:
        raise InvalidRequest(f'champ non variable: {path}')
    entry = raw['segments'][index]
    try:
        for p in parts[2:]:
            entry = entry[p]
    except KeyError as exc:
        raise InvalidRequest(f'champ absent: {path}; déclarer le paramètre natif dans DESIGN') from exc
    dim, factor = ('length', .01) if key.endswith('_cm') else ('scalar', 1.)
    return number(entry, path)*factor, dim, factor


def set_field(raw, path, si):
    _, _, factor = field_info(raw, path)
    parts = path.split('.')
    entry = raw['segments'][int(parts[1])]
    for p in parts[2:-1]:
        entry = entry[p]
    entry[parts[-1]] = float(si/factor)


def expression(expr, raw, depth=0):
    if depth > 16:
        raise InvalidRequest('expression trop profonde')
    obj(expr, {'field','total_length','constant','affine','offset','ratio'}, 'expression')
    keys = set(expr)
    if keys == {'field'}:
        value, dim, _ = field_info(raw, expr['field'])
        return value, dim, {expr['field']}
    if keys == {'total_length'} and expr['total_length'] is True:
        return sum(s['length_cm'] for s in raw['segments'])/100, 'length', {
            f'segments.{i}.length_cm' for i in range(len(raw['segments']))}
    if keys == {'constant'}:
        v, d = quantity(expr['constant'])
        return v, d, set()
    if keys == {'affine','offset'}:
        value, dim = quantity(expr['offset'])
        deps = set()
        terms = expr['affine']
        if not isinstance(terms,list) or not 1 <= len(terms) <= 64:
            raise InvalidRequest('affine: 1..64 termes requis')
        for t in terms:
            obj(t, {'coefficient','expression'}, 'terme', {'coefficient','expression'})
            v, d, refs = expression(t['expression'], raw, depth+1)
            if d != dim:
                raise InvalidRequest('combinaison affine de dimensions incompatibles')
            value += number(t['coefficient'], 'coefficient sans dimension')*v
            deps |= refs
        return value, dim, deps
    if keys == {'ratio'}:
        pair = expr['ratio']
        if not isinstance(pair, list) or len(pair) != 2:
            raise InvalidRequest('ratio: deux expressions requises')
        a, da, ra = expression(pair[0], raw, depth+1)
        b, db, rb = expression(pair[1], raw, depth+1)
        if da != db or b == 0:
            raise InvalidRequest('ratio: dimensions identiques et dénominateur non nul requis')
        return a/b, 'scalar', ra|rb
    raise InvalidRequest('expression typée invalide')


def scope_reason(scope):
    obj(scope, {'configurations','registers','q','uncertain_parameters','common_conditions'}, 'scope')
    for key in ('configurations','registers','common_conditions'):
        if key in scope and (not isinstance(scope[key], list) or not scope[key] or
                             any(not isinstance(v,str) or not v for v in scope[key])):
            raise InvalidRequest(f'scope.{key}: liste de noms non vide requise')
    q = scope.get('q')
    if q is not None:
        obj(q, {'kind','unit','values','bounds'}, 'q', {'kind','unit'})
        if q['kind'] not in ('fixed','discrete','continuous') or q['unit'] not in UNITS:
            raise InvalidRequest('q: kind/unit invalide')
        key = 'bounds' if q['kind'] == 'continuous' else 'values'
        if set(q) != {'kind','unit',key} or not isinstance(q[key],list) or not q[key]:
            raise InvalidRequest('q: valeurs/bornes explicites requises')
        for v in q[key]: number(v,'q')
        if q['kind'] == 'fixed' and len(q[key]) != 1:
            raise InvalidRequest('q fixed: une valeur')
        if key == 'bounds' and (len(q[key]) != 2 or q[key][0] >= q[key][1]):
            raise InvalidRequest('q continuous: bornes ordonnées')
    uncertain = scope.get('uncertain_parameters', [])
    if not isinstance(uncertain,list) or len(uncertain)>64:
        raise InvalidRequest('uncertain_parameters: liste bornée requise')
    for u in uncertain:
        obj(u, {'id','field','piece_id','variation'}, 'incertitude', {'id','field','piece_id','variation'})
        identifier(u['id'],'incertitude.id'); identifier(u['piece_id'],'piece_id')
        if not isinstance(u['field'],str): raise InvalidRequest('incertitude.field invalide')
        v,_ = quantity(u['variation'])
        if v <= 0: raise InvalidRequest('variation positive requise')
    if (scope.get('configurations',['nominal']) != ['nominal'] or q is not None or uncertain or
        scope.get('common_conditions', ['CONFIG']) != ['CONFIG'] or
        scope.get('registers', ['passive']) != ['passive']):
        return 'Portée conservée, seul le scénario CONFIG nominal passif est calculé; aucune garantie multi-configuration/continue/robuste.'
    return None


def _stripped(raw, fields):
    value = copy.deepcopy(raw)
    value['metadata'].pop('total_length_cm', None)
    for s in value['segments']:
        s.pop('position_start_cm',None); s.pop('position_end_cm',None)
    for f in fields:
        set_field(value, f, 0.)
    return value


class Contract:
    """Validated immutable request, explicit degrees of freedom and lock mask."""
    def __init__(self, request, context):
        try:
            self._initialize(request,context)
        except (TypeError,KeyError,OverflowError,RecursionError) as exc:
            raise InvalidRequest('structure/valeur de demande invalide: '+str(exc)) from exc

    def _initialize(self, request, context):
        self.request = copy.deepcopy(request)
        self.context = context
        self.base = copy.deepcopy(context['design'].as_dict())
        r = self.request
        obj(r, {'schema_version','variables','derived','criteria','modes','spectrum','budgets',
                'models','preference_method','scope','metadata'}, 'request',
            {'schema_version','variables','criteria','spectrum','budgets'})
        _validate_annotations(r, 'request', set())
        if r['schema_version'] != SCHEMA: raise InvalidRequest('version REQUEST inconnue')
        self.scope_issue = scope_reason(r.get('scope',{}))
        b = r['budgets']; obj(b, LIMITS, 'budgets', LIMITS)
        self.budgets = {k: integer(b[k],1,v,'budgets.'+k) for k,v in LIMITS.items()}
        if b['history'] < b['evaluations']: raise InvalidRequest('history doit couvrir evaluations')
        self.spectrum = r['spectrum']
        obj(self.spectrum, {'min_hz','max_hz','step_hz','final_step_hz','refinement_hz','h_cm'},
            'spectrum', {'min_hz','max_hz','step_hz','final_step_hz','refinement_hz','h_cm'})
        for k,v in self.spectrum.items(): number(v,'spectrum.'+k,True)
        s = self.spectrum
        if not s['min_hz'] < s['max_hz'] or not s['refinement_hz'] < s['final_step_hz'] <= s['step_hz']:
            raise InvalidRequest('domaine/résolutions spectraux incohérents')
        if math.ceil((s['max_hz']-s['min_hz'])/s['final_step_hz'])+1 > b['frequencies']:
            raise InvalidRequest('budget fréquences dépassé avant allocation')
        self.options = {'loss_model':'legacy','radiation_model':'legacy','air_reference':None}
        obj(r.get('models',{}), self.options,'models')
        self.options.update(r.get('models',{}))
        if self.options['loss_model'] not in ('legacy','zk') or self.options['radiation_model'] not in ('legacy','silva_unflanged','silva_flanged'):
            raise InvalidRequest('modèle inconnu')
        if ((self.options['loss_model']=='zk' and self.options['air_reference'] not in ('ck_dry20','ck_dry25')) or
            (self.options['loss_model']=='legacy' and self.options['air_reference'] is not None)):
            raise InvalidRequest('ZK exige CKdry explicite; legacy utilise CONFIG')
        self.variables=[]; self.derived=[]; self.fields=set(); ids=set()
        if not isinstance(r['variables'],list) or len(r['variables'])>b['variables']:
            raise InvalidRequest('budget variables dépassé')
        for v in r['variables']:
            obj(v, {'id','fields','unit','bounds'},'variable',{'id','fields','unit','bounds'})
            ident=identifier(v['id'],'variable.id')
            if ident in ids: raise InvalidRequest('id variable dupliqué')
            ids.add(ident)
            if not isinstance(v['fields'],list) or not v['fields']: raise InvalidRequest('fields non vide requis')
            lo,hi,dim = self.bounds(v)
            initial=[]
            for f in v['fields']:
                x,d,_=field_info(self.base,f)
                if d=='length' and lo<=0: raise InvalidRequest('borne géométrique strictement positive requise')
                if f in self.fields or d != dim: raise InvalidRequest('champ lié deux fois ou unité incohérente: '+str(f))
                self.fields.add(f); initial.append(x)
            if any(x != initial[0] for x in initial) or not lo <= initial[0] <= hi:
                raise InvalidRequest('valeur initiale hors bornes ou champs liés différents: '+ident)
            self.variables.append(dict(v,low=lo,high=hi,initial=initial[0]))
        derived=r.get('derived',[])
        if not isinstance(derived,list) or len(derived)>64: raise InvalidRequest('derived: liste <=64')
        pending={}
        for d in derived:
            obj(d,{'id','field','expression','unit','bounds'},'derived',{'id','field','expression','unit','bounds'})
            ident=identifier(d['id'],'derived.id')
            if ident in ids: raise InvalidRequest('id dérivé dupliqué')
            ids.add(ident)
            lo,hi,dim=self.bounds(d); initial,fd,_=field_info(self.base,d['field'])
            v,ed,deps=expression(d['expression'],self.base)
            if fd!=dim or ed!=dim or d['field'] in self.fields:
                raise InvalidRequest('définition dérivée contradictoire')
            if not lo <= initial <= hi or not math.isclose(v,initial,rel_tol=1e-12,abs_tol=1e-14):
                raise InvalidRequest('définition dérivée incompatible avec DESIGN initial')
            self.fields.add(d['field']); pending[d['field']]=dict(d,low=lo,high=hi,deps=deps)
        while pending:
            ready=[f for f,d in pending.items() if not d['deps'] & pending.keys()]
            if not ready: raise InvalidRequest('cycle de définitions dérivées')
            for f in ready: self.derived.append(pending.pop(f))
        # The native profile's numerical domain is explicit, never silently clamped.
        self.unrepresented_fields=[]
        for v in self.variables+self.derived:
            for f in v.get('fields',[v.get('field')]):
                if f.endswith('profile_params.throat_diameter_cm'):
                    self.unrepresented_fields.append(f)
                if f.endswith('profile_params.power') and v['low']<.05:
                    raise InvalidRequest('power natif: borne minimale .05 pour éviter projection')
                if f.endswith('profile_params.flare_parameter'):
                    segment=self.base['segments'][int(f.split('.')[1])]
                    if segment['kind']=='flare_powerlaw' and 'power' in segment['profile_params']:
                        self.unrepresented_fields.append(f)
                    floor=.05 if segment['kind']=='flare_powerlaw' else 1.e-6
                    if v['low']<floor: raise InvalidRequest('flare_parameter: borne sous le domaine natif')
        self.lock_mask=_stripped(self.base,self.fields)
        self.modes={}; orders=set()
        modes=r.get('modes',[])
        if not isinstance(modes,list) or len(modes)>16: raise InvalidRequest('modes: liste <=16')
        for m in modes:
            obj(m,{'id','order','window_hz'},'mode',{'id','order','window_hz'})
            ident=identifier(m['id'],'mode.id'); order=integer(m['order'],1,32,'mode.order')
            w=m['window_hz']
            if ident in self.modes or order in orders: raise InvalidRequest('assignation injective: id/ordre de mode dupliqué')
            if not isinstance(w,list) or len(w)!=2: raise InvalidRequest('mode.window_hz: deux bornes')
            a,z=[number(v,'window_hz',True) for v in w]
            if not s['min_hz'] < a < z < s['max_hz']: raise InvalidRequest('fenêtre de mode hors domaine')
            self.modes[ident]=m; orders.add(order)
        method=r.get('preference_method','lexicographic')
        if method not in ('lexicographic','weighted'): raise InvalidRequest('méthode de préférence inconnue')
        self.preference_supported=method=='lexicographic'
        self.criteria=[]; ids=set(); intersections={}; priorities=set(); frequency_targets={}
        if not isinstance(r['criteria'],list) or not 1<=len(r['criteria'])<=64: raise InvalidRequest('criteria: 1..64 requis')
        for c in r['criteria']:
            obj(c, {'id','observable','expression','mode','numerator','denominator','target','bounds',
                    'unit','tolerance','level','scope','role','priority','weight','metadata'},'critère',
                {'id','observable','unit','tolerance','level','scope','role'})
            ident=identifier(c['id'],'critère.id')
            if ident in ids: raise InvalidRequest('id critère dupliqué')
            ids.add(ident)
            obs=c['observable']
            if not isinstance(obs,str) or obs not in CAPABILITIES: raise InvalidRequest('observable inconnu')
            if c['role'] not in ('hard','preference','observe') or c['level'] not in ('geometry','passive','played'):
                raise InvalidRequest('rôle/niveau inconnu')
            if ('target' in c)==('bounds' in c): raise InvalidRequest('un target OU bounds requis')
            if c['unit'] not in UNITS: raise InvalidRequest('unité critère inconnue')
            dim=UNITS[c['unit']][0]; deps=set()
            local_scope_reason=scope_reason(c['scope'])
            reason=self.scope_issue or local_scope_reason
            for scope in (r.get('scope',{}),c['scope']):
                for u in scope.get('uncertain_parameters',[]):
                    _,ud,_=field_info(self.base,u['field'])
                    quantity(u['variation'],ud)
            if obs=='geometry':
                _,ed,deps=expression(c.get('expression'),self.base)
                if ed!=dim: raise InvalidRequest('unité géométrique incohérente')
                if c['level']!='geometry': reason='niveau géométrique requis pour ce solveur'
            elif obs in ('resonance_frequency','resonance_ratio'):
                keys=('mode',) if obs=='resonance_frequency' else ('numerator','denominator')
                for key in keys:
                    if c.get(key) not in self.modes: raise InvalidRequest('référence mode absente')
                if dim!=('frequency' if obs=='resonance_frequency' else 'scalar'): raise InvalidRequest('unité résonance incohérente')
                if c['level']!='passive': reason='Le niveau joué ne se déduit pas des maxima passifs.'
            else:
                reason='Capacité connue non calculée par le solveur statique: '+obs
            for key in ('expression','mode','numerator','denominator'):
                applicable=({'expression'} if obs=='geometry' else {'mode'} if obs=='resonance_frequency' else
                            {'numerator','denominator'} if obs=='resonance_ratio' else set())
                if key in c and key not in applicable: raise InvalidRequest('champ non applicable au critère: '+key)
            if obs=='played_frequency' and dim!='frequency': raise InvalidRequest('played_frequency exige Hz')
            if c['role']=='preference':
                if method=='lexicographic':
                    integer(c.get('priority'),0,1000,'priority')
                    if c['priority'] in priorities: raise InvalidRequest('priorités distinctes requises')
                    priorities.add(c['priority'])
                    if 'weight' in c: raise InvalidRequest('poids ≠ priorité lexicographique')
                else:
                    number(c.get('weight'),'weight',True)
                    if 'priority' in c: raise InvalidRequest('priorité ≠ poids')
                    reason='Méthode weighted conservée mais unsupported, aucune substitution.'
            elif 'priority' in c or 'weight' in c: raise InvalidRequest('préférence explicite requise')
            tol,td=quantity(c['tolerance'])
            if tol<=0 or (td!=dim and not(td=='cent' and dim in ('frequency','scalar'))):
                raise InvalidRequest('tolérance utilisateur positive de dimension correcte requise')
            if 'target' in c:
                target=target_quantity(c['target'],dim)
                if td=='cent' and target<=0: raise InvalidRequest('cents exigent cible positive')
                lo,hi=(target*2**(-tol/1200),target*2**(tol/1200)) if td=='cent' else (target-tol,target+tol)
            else:
                if td=='cent': raise InvalidRequest('bornes: tolérance SI requise')
                v=c['bounds']
                if not isinstance(v,list) or len(v)!=2: raise InvalidRequest('bounds: deux quantités')
                low,high=[quantity(q,dim)[0] for q in v]
                if low>high: raise InvalidRequest('bornes contradictoires')
                target=None; lo,hi=low-tol,high+tol
            if obs=='resonance_frequency' and target is not None:
                w=self.modes[c['mode']]['window_hz']
                if not w[0]<target<w[1]: raise InvalidRequest('cible hors fenêtre de mode')
                previous=frequency_targets.get(c['mode'],target)
                if previous!=target: raise InvalidRequest('assignation injective: deux cibles distinctes pour le même mode')
                frequency_targets[c['mode']]=target
            row=dict(c,dimension=dim,target_si=target,lower_si=lo,upper_si=hi,tolerance_si=tol,
                     tolerance_dimension=td,unsupported_reason=reason)
            self.criteria.append(row)
            if c['role']=='hard' and not reason:
                key=json.dumps({k:c[k] for k in ('observable','expression','mode','numerator','denominator') if k in c},sort_keys=True)
                old=intersections.get(key,(-math.inf,math.inf)); new=max(old[0],lo),min(old[1],hi)
                if new[0]>new[1]: raise InvalidRequest('contradiction directe entre obligations: '+ident)
                intersections[key]=new
                if obs=='geometry' and not deps & self.fields:
                    value=expression(c['expression'],self.base)[0]
                    if not lo<=value<=hi: raise InvalidRequest('obligation contradictoire avec champ verrouillé: '+ident)
                if obs=='geometry' and set(c['expression'])=={'field'}:
                    ref=c['expression']['field']
                    for v in self.variables+self.derived:
                        if ref in v.get('fields',[v.get('field')]) and (v['high']<lo or v['low']>hi):
                            raise InvalidRequest('contradiction directe obligation/bornes: '+ident)
        self.generate(self.initial)

    def bounds(self,v):
        if v['unit'] not in UNITS: raise InvalidRequest('unité variable inconnue')
        dim,factor=UNITS[v['unit']]
        b=v['bounds']
        if not isinstance(b,list) or len(b)!=2: raise InvalidRequest('bounds: deux nombres')
        lo,hi=[number(x,'borne')*factor for x in b]
        if not lo<hi: raise InvalidRequest('bornes strictement ordonnées requises')
        return lo,hi,dim

    @property
    def initial(self): return [v['initial'] for v in self.variables]

    def check_locks(self, raw):
        if _stripped(raw,self.fields)!=self.lock_mask:
            raise InvalidRequest('masque de verrous violé')
        for v in self.variables:
            vals=[field_info(raw,f)[0] for f in v['fields']]
            if any(x!=vals[0] for x in vals) or not v['low']<=vals[0]<=v['high']:
                raise InvalidRequest('liaison/bornes variable violées')
        for d in self.derived:
            value=field_info(raw,d['field'])[0]
            expected=expression(d['expression'],raw)[0]
            if not d['low']<=value<=d['high'] or not math.isclose(value,expected,rel_tol=1e-12,abs_tol=1e-14):
                raise InvalidRequest('relation dérivée violée')

    def generate(self,values):
        if len(values)!=len(self.variables): raise InvalidRequest('nombre de variables incorrect')
        raw=copy.deepcopy(self.base)
        for v,x in zip(self.variables,values):
            x=number(x,'essai')
            if not v['low']<=x<=v['high']: raise InvalidRequest('essai hors bornes')
            for f in v['fields']: set_field(raw,f,x)
        for d in self.derived:
            value=expression(d['expression'],raw)[0]
            if not d['low']<=value<=d['high']: raise InvalidRequest('dérivée hors bornes')
            set_field(raw,d['field'],value)
        self.check_locks(raw)
        design=validate_design(raw,self.context['material_db'],self.context['config'])
        self.check_locks(design.as_dict())
        return design

    def plan(self):
        return dict(capabilities=CAPABILITIES, criteria=[dict(id=c['id'],status='unsupported' if c['unsupported_reason'] else 'unresolved',reason=c['unsupported_reason']) for c in self.criteria],
            degrees_of_freedom=len(self.variables),derived_count=len(self.derived),lock_mask=self.lock_mask,
            acoustically_unrepresented_fields=self.unrepresented_fields,
            variable_fields=sorted(self.fields),spectrum=self.spectrum,budgets=self.budgets,models=self.options,
            preference_method=self.request.get('preference_method','lexicographic'),global_optimum_proven=False)
