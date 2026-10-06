"""Shared physical degrees of freedom and traced native R41 projections."""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

from .design_contract import (Contract, InvalidRequest, obj, number, integer, identifier,
                              quantity, UNITS, expression, _stripped)
from ..geometry.assemblies import Assembly, field_info, set_field

SCHEMA = 'dcalc.assembly_request.v1'
LIMITS = dict(candidates=32, evaluations=300, projections=4096, spectral_calls=10000,
              frequencies=2000000, frequency_segment_product=200000000, segments=200000,
              seconds=165, memory_mib=700, output_mib=100)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def physical_expression(expr, raw, depth=0):
    """Typed affine physical links only; derived values add no freedom."""
    if depth > 16:
        raise InvalidRequest('relation trop profonde')
    obj(expr, {'field', 'constant', 'affine', 'offset'}, 'relation')
    if set(expr) == {'field'}:
        value, dim, _ = field_info(raw, expr['field'])
        return value, dim, {expr['field']}
    if set(expr) == {'constant'}:
        value, dim = quantity(expr['constant'])
        return value, dim, set()
    if set(expr) != {'affine', 'offset'}:
        raise InvalidRequest('relation affine typée requise')
    value, dim = quantity(expr['offset']); deps = set()
    terms = expr['affine']
    if not isinstance(terms, list) or not 1 <= len(terms) <= 64:
        raise InvalidRequest('relation: 1..64 termes')
    for term in terms:
        obj(term, {'coefficient', 'expression'}, 'terme', {'coefficient', 'expression'})
        v, d, refs = physical_expression(term['expression'], raw, depth + 1)
        if d != dim:
            raise InvalidRequest('relation: unités incompatibles')
        value += number(term['coefficient'], 'coefficient') * v
        deps |= refs
    return value, dim, deps


def bounds(v):
    if v['unit'] not in UNITS or not isinstance(v['bounds'], list) or len(v['bounds']) != 2:
        raise InvalidRequest('bornes dimensionnées requises')
    lo, dim = quantity(dict(value=v['bounds'][0], unit=v['unit']))
    hi, _ = quantity(dict(value=v['bounds'][1], unit=v['unit']))
    if not lo < hi:
        raise InvalidRequest('bornes strictement ordonnées requises')
    return lo, hi, dim


def mask(raw, fields):
    value = copy.deepcopy(raw)
    for path in fields:
        set_field(value, path, 0.)
    return value


def has_field(value):
    if isinstance(value, dict):
        return 'field' in value or any(has_field(v) for v in value.values())
    return isinstance(value, list) and any(has_field(v) for v in value)


def native_contract(request, context):
    """R41 validation/criteria exactly; assembly owns shared geometry locks.

    R41's early fixed-field contradiction is a candidate violation here, not a
    malformed parent request. Only that precheck is deferred. The native rows,
    target conversion, scopes, roles and tolerance computation are retained.
    """
    try:
        return Contract(request, context)
    except InvalidRequest as exc:
        if 'obligation contradictoire avec champ verrouillé' not in str(exc):
            raise
        semantic = copy.deepcopy(request)
        # One validation-only native length variable defers the fixed-total-length
        # precheck while keeping R41 hard intersection checks active. This is never
        # a search freedom or an exported effective request.
        length = context['design'].segments[0].length_cm
        semantic['variables'] = [dict(id='assembly_semantic_length',
            fields=['segments.0.length_cm'], unit='cm', bounds=[length/2, length*2])]
        contract = Contract(semantic, context)
        contract.request = copy.deepcopy(request)
        contract.variables = []; contract.fields = set()
        contract.lock_mask = _stripped(contract.base, set())
        return contract


class AssemblyContract:
    def __init__(self, request, assembly, context):
        from ..pipeline.design_input import _validate_annotations
        _validate_annotations(request, 'assembly request', set())
        self._request = copy.deepcopy(request)
        self.base = assembly.raw
        self.context = context
        self._assembly = assembly
        r = self._request
        obj(r, {'schema_version', 'variables', 'derived', 'projections', 'catalogue',
                'coverage', 'budgets', 'metadata'}, 'assembly request',
            {'schema_version', 'variables', 'projections', 'budgets'})
        if r['schema_version'] != SCHEMA:
            raise InvalidRequest('version assembly request inconnue')
        obj(r['budgets'], LIMITS, 'budget global', LIMITS)
        self.budgets = {k: integer(r['budgets'][k], 1, v, 'budget global.' + k) for k, v in LIMITS.items()}
        self.parent_sha256 = digest(r)
        self.coverage = copy.deepcopy(r.get('coverage', {'kind': 'discrete'}))
        obj(self.coverage, {'kind', 'path'}, 'coverage', {'kind'})
        if self.coverage['kind'] not in ('discrete', 'continuous'):
            raise InvalidRequest('coverage inconnue')
        if self.coverage['kind'] == 'continuous':
            paths = {p['id'] for p in self.base.get('paths', [])}
            if self.coverage.get('path') not in paths:
                raise InvalidRequest('coverage continue exige un chemin affine explicite')
        elif 'path' in self.coverage:
            raise InvalidRequest('path réservé à coverage continuous')
        self.variables = []; self.derived = []; self.fields = set(); ids = set()
        if not isinstance(r['variables'], list) or len(r['variables']) > 16:
            raise InvalidRequest('variables: liste <=16')
        for v in r['variables']:
            obj(v, {'id', 'fields', 'unit', 'bounds'}, 'variable', {'id', 'fields', 'unit', 'bounds'})
            ident = identifier(v['id'], 'variable.id')
            if ident in ids or not isinstance(v['fields'], list) or not v['fields']:
                raise InvalidRequest('variables dupliquées/vides')
            ids.add(ident); lo, hi, dim = bounds(v); initial = []
            for f in v['fields']:
                val, d, _ = field_info(self.base, f)
                if f in self.fields or d != dim or lo < 0 or (lo == 0 and not f.startswith('configurations.')):
                    raise InvalidRequest('liaison double, borne ou unité incorrecte')
                self.fields.add(f); initial.append(val)
            if any(val != initial[0] for val in initial) or not lo <= initial[0] <= hi:
                raise InvalidRequest('liaison/valeur initiale hors bornes')
            self.variables.append(dict(v, low=lo, high=hi, initial=initial[0]))
        pending = {}
        derived = r.get('derived', [])
        if not isinstance(derived, list) or len(derived) > 64:
            raise InvalidRequest('derived: liste <=64')
        for d in derived:
            obj(d, {'id', 'field', 'expression', 'unit', 'bounds'}, 'derived',
                {'id', 'field', 'expression', 'unit', 'bounds'})
            ident = identifier(d['id'], 'derived.id'); lo, hi, dim = bounds(d)
            value, fd, _ = field_info(self.base, d['field'])
            expected, ed, deps = physical_expression(d['expression'], self.base)
            if ident in ids or d['field'] in self.fields or fd != dim or ed != dim or value != expected or not lo <= value <= hi:
                raise InvalidRequest('relation dérivée incompatible avec assemblage')
            ids.add(ident); self.fields.add(d['field'])
            pending[d['field']] = dict(d, low=lo, high=hi, deps=deps)
        while pending:
            ready = [f for f, d in pending.items() if not d['deps'] & pending.keys()]
            if not ready:
                raise InvalidRequest('cycle de relations')
            for f in ready:
                self.derived.append(pending.pop(f))
        self.lock_mask = mask(self.base, self.fields)
        projections = r['projections']
        if not isinstance(projections, list) or not 1 <= len(projections) <= 64:
            raise InvalidRequest('projections: 1..64')
        self.projections = []; self.criteria = []; ids = set(); priorities = set()
        self.preference_supported = True
        for p in projections:
            obj(p, {'id', 'configuration', 'request'}, 'projection', {'id', 'configuration', 'request'})
            ident = identifier(p['id'], 'projection.id')
            if ident in ids or p['configuration'] not in assembly.configurations:
                raise InvalidRequest('projection dupliquée/configuration inconnue')
            ids.add(ident)
            # A physical piece is never referenced by a removable acoustic slice.
            if p['request'].get('variables') != [] or p['request'].get('derived', []):
                raise InvalidRequest('libertés exclusivement dans le conteneur physique partagé')
            for c in p['request'].get('criteria', []):
                if c.get('observable') == 'geometry' and has_field(c.get('expression')):
                    raise InvalidRequest('référence de tranche instable: utiliser total_length; dimensions de pièces via variables/relations physiques')
            generated = assembly.generate(p['configuration'])
            local = native_contract(p['request'], dict(context, design=generated['design']))
            self.preference_supported &= local.preference_supported
            for c in local.criteria:
                row = dict(c, id=ident + ':' + c['id'], projection=ident)
                if c['role'] == 'preference' and local.preference_supported:
                    if c['priority'] in priorities:
                        raise InvalidRequest('priorités globales distinctes requises')
                    priorities.add(c['priority'])
                self.criteria.append(row)
            self.projections.append(dict(p, template=local))
        declared = set(assembly.configurations)
        if {p['configuration'] for p in self.projections} != declared:
            raise InvalidRequest('chaque configuration déclarée exige sa projection')
        catalogue = r.get('catalogue', [{'id': 'nominal', 'values': {}}])
        if not isinstance(catalogue, list) or not 1 <= len(catalogue) <= 32:
            raise InvalidRequest('catalogue: 1..32 alternatives explicites')
        self.catalogue = []; ids = set()
        for alternative in catalogue:
            obj(alternative, {'id', 'values', 'orientations'}, 'alternative', {'id', 'values'})
            ident = identifier(alternative['id'], 'alternative.id')
            if ident in ids:
                raise InvalidRequest('alternative dupliquée')
            ids.add(ident)
            if not isinstance(alternative['values'], dict):
                raise InvalidRequest('alternative.values: objet')
            raw = copy.deepcopy(self.base)
            for f, q in alternative['values'].items():
                _, dim, _ = field_info(raw, f)
                if f in self.fields:
                    raise InvalidRequest('catalogue et variable écrivent le même champ')
                set_field(raw, f, quantity(q, dim)[0])
            orientations = alternative.get('orientations', {})
            if not isinstance(orientations, dict):
                raise InvalidRequest('orientations: objet')
            for ident, direction in orientations.items():
                blocks = [b for b in raw['blocks'] if b['id'] == ident and b['kind'] == 'telescope']
                if len(blocks) != 1 or direction not in ('outer_first', 'inner_first'):
                    raise InvalidRequest('orientation catalogue inconnue')
                blocks[0]['orientation'] = direction
            self.catalogue.append(dict(alternative, base=raw, lock_mask=mask(raw, self.fields)))
        self.initial = [v['initial'] for v in self.variables]
        self.check_locks(self.base, None)

    @property
    def request(self):
        return copy.deepcopy(self._request)

    def check_locks(self, raw, alternative=None):
        expected = self.lock_mask if alternative is None else alternative['lock_mask']
        if mask(raw, self.fields) != expected:
            raise InvalidRequest('verrous physiques violés')
        for v in self.variables:
            values = [field_info(raw, f)[0] for f in v['fields']]
            if any(x != values[0] for x in values) or not v['low'] <= values[0] <= v['high']:
                raise InvalidRequest('liaison/bornes physiques violées')
        for d in self.derived:
            val = field_info(raw, d['field'])[0]
            if val != physical_expression(d['expression'], raw)[0] or not d['low'] <= val <= d['high']:
                raise InvalidRequest('relation physique violée')

    def generate(self, values, alternative):
        if len(values) != len(self.variables):
            raise InvalidRequest('nombre de paramètres incorrect')
        raw = copy.deepcopy(alternative['base'])
        for v, value in zip(self.variables, values):
            value = number(value, 'candidat')
            if not v['low'] <= value <= v['high']:
                raise InvalidRequest('candidat hors bornes')
            for f in v['fields']:
                set_field(raw, f, value)
        for d in self.derived:
            set_field(raw, d['field'], physical_expression(d['expression'], raw)[0])
        self.check_locks(raw, alternative)
        return Assembly(raw, self.context['material_db'], self.context['config'])

    def search_adapter(self, remaining_evaluations, seconds):
        return SimpleNamespace(variables=self.variables, initial=self.initial, criteria=self.criteria,
            preference_supported=self.preference_supported,
            budgets=dict(evaluations=remaining_evaluations, seconds=seconds,
                         iterations=max(p['template'].budgets['iterations'] for p in self.projections)))

    def plan(self):
        return dict(parent_request=self.request, parent_sha256=self.parent_sha256,
            degrees_of_freedom=len(self.variables), derived_count=len(self.derived),
            shared_fields=sorted(self.fields), lock_mask=self.lock_mask, budgets=self.budgets,
            criteria=[dict(id=c['id'], role=c['role'], reason=c['unsupported_reason']) for c in self.criteria],
            coverage=self.coverage, continuous_acoustics_certified=False,
            preference_method='lexicographic', alternatives=[a['id'] for a in self.catalogue])
