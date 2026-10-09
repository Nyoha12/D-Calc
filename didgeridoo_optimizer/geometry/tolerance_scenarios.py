"""Finite prescribed dimensional perturbations of a fixed native instrument.

No search variable is adjusted here.  An uncertainty owns each physical field
exactly once; all configurations consequently see the same piece dimensions.
The strict native readers are responsible for file syntax and duplicate keys.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import json
import math

from ..optimization.design_contract import (
    InvalidRequest, expression, field_info as design_field_info,
    identifier, number, obj, quantity, set_field as design_set_field,
)
from ..pipeline.design_input import _validate_annotations
from .assemblies import (
    InvalidAssembly, exact_quantity, field_info as assembly_field_info,
    set_field as assembly_set_field,
)

MAX_UNCERTAINTIES = 8
MAX_SCENARIOS = 65
MAX_FIELDS = 64
MAX_SCENARIO_FIELDS = MAX_SCENARIOS * MAX_FIELDS


class ScenarioUnavailable(RuntimeError):
    """Native numeric representation unavailable; no physical violation proved."""


def _kind(kind):
    if kind not in ('fixed', 'assembly'):
        raise InvalidRequest('audit.kind: fixed ou assembly requis')


def _field_info(raw, path, kind):
    if kind == 'assembly':
        if not isinstance(path, str) or not path.startswith('pieces.'):
            raise InvalidRequest('incertitude: champ stable de pieces requis; q est constant')
        return assembly_field_info(raw, path, exact=True)
    value, dimension, factor = design_field_info(raw, path)
    if dimension != 'length':
        raise InvalidRequest('incertitude: seuls les champs dimensionnels physiques sont admis')
    if path.endswith('profile_params.throat_diameter_cm'):
        raise InvalidRequest('v1: throat_diameter_cm est sans représentation acoustique effective')
    return value, dimension, factor


def _strict_tree(value, where):
    try:
        _validate_annotations(value, where, set())
    except (ValueError, RecursionError) as exc:
        raise InvalidRequest(f'{where}: structure finie sans cycle requise: {exc}') from exc


def parse_scenarios(job, kind, nominal_raw, contract=None):
    """Validate bounded scenario data before materializing any perturbed geometry.

    Outer JOB schema, reference resolution and execution budgets belong to the
    pipeline. This function accepts its ``uncertainties`` and ``scenarios`` keys.
    ``contract`` is a validated native Contract or AssemblyContract; prior search
    bounds are deliberately not applied to manufacturing deviations.
    """
    _kind(kind)
    _strict_tree(job, 'audit')
    if not isinstance(job, dict):
        raise InvalidRequest('audit: objet requis')
    uncertainties = job.get('uncertainties')
    scenarios = job.get('scenarios')
    if not isinstance(uncertainties, list) or not 1 <= len(uncertainties) <= MAX_UNCERTAINTIES:
        raise InvalidRequest('uncertainties: 1..8 incertitudes explicites requises')
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= MAX_SCENARIOS:
        raise InvalidRequest('scenarios: 1..65 scénarios explicites requis')
    # Count before copying or allocating the scenario x field product.
    field_count = 0
    for u in uncertainties:
        obj(u, {'id', 'fields', 'delta', 'origin'}, 'incertitude',
            {'id', 'fields', 'delta', 'origin'})
        if not isinstance(u['fields'], list) or not u['fields']:
            raise InvalidRequest('incertitude.fields: liste non vide requise')
        field_count += len(u['fields'])
        if field_count > MAX_FIELDS:
            raise InvalidRequest('incertitude: budget 64 champs dépassé')
    if field_count * len(scenarios) > MAX_SCENARIO_FIELDS:
        raise InvalidRequest('audit: budget produit scénarios-champs dépassé avant allocation')

    parsed = {'uncertainties': [], 'scenarios': [], 'exposed_fields': [],
              'field_count': field_count, 'scenario_field_product': field_count * len(scenarios)}
    ids, fields = set(), set()
    for u in uncertainties:
        ident = identifier(u['id'], 'incertitude.id')
        if ident in ids:
            raise InvalidRequest('incertitude.id dupliqué: ' + ident)
        ids.add(ident)
        if not isinstance(u['origin'], str) or not u['origin'].strip() or len(u['origin']) > 4096:
            raise InvalidRequest('incertitude.origin: texte explicite non vide <=4096 caractères requis')
        delta, _ = quantity(u['delta'], 'length')
        if not math.isfinite(delta) or delta <= 0:
            raise InvalidRequest('incertitude.delta: amplitude dimensionnelle positive finie requise')
        for path in u['fields']:
            if not isinstance(path, str) or path in fields:
                raise InvalidRequest('incertitude.fields: référence textuelle unique requise')
            _field_info(nominal_raw, path, kind)
            fields.add(path)
        parsed['uncertainties'].append(dict(id=ident, fields=list(u['fields']),
            delta=copy.deepcopy(u['delta']), delta_si=delta, origin=u['origin']))
    parsed['exposed_fields'] = sorted(fields)
    scenario_ids, vectors = set(), set()
    nominal_count = 0
    ordered_ids = [u['id'] for u in parsed['uncertainties']]
    for scenario in scenarios:
        obj(scenario, {'id', 'coefficients'}, 'scénario', {'id', 'coefficients'})
        ident = identifier(scenario['id'], 'scénario.id')
        if ident in scenario_ids:
            raise InvalidRequest('scénario.id dupliqué: ' + ident)
        scenario_ids.add(ident)
        coefficients = scenario['coefficients']
        obj(coefficients, ids, 'scénario.coefficients', ids)
        vals = {key: number(coefficients[key], 'scénario.coefficients.' + key) for key in ordered_ids}
        if any(not -1 <= value <= 1 for value in vals.values()):
            raise InvalidRequest('scénario.coefficients: valeurs explicites dans [-1,1] requises')
        vector = tuple(vals[key] for key in ordered_ids)
        if vector in vectors:
            raise InvalidRequest('scénario: vecteur de coefficients répété')
        vectors.add(vector)
        nominal = all(value == 0 for value in vals.values())
        nominal_count += nominal
        parsed['scenarios'].append(dict(id=ident, coefficients=vals, nominal=nominal))
    if nominal_count != 1:
        raise InvalidRequest('un unique scénario nominal, tous coefficients nuls, est obligatoire')
    if contract is not None:
        validate_links(parsed, contract, kind, nominal_raw=nominal_raw)
    return parsed


def perturb(nominal_raw, parsed, scenario, kind):
    """Return a detached native geometry with only explicitly listed shifts.

    Decimal assembly quantities use the native exact rational setter. Fixed
    DESIGN dimensions use their native centimetre setter. A zero shift does not
    call either setter, preserving the exact nominal representation.
    """
    _kind(kind)
    if scenario not in parsed['scenarios']:
        raise InvalidRequest('scénario non validé dans le JOB')
    effective = copy.deepcopy(nominal_raw)
    for uncertainty in parsed['uncertainties']:
        coefficient = scenario['coefficients'][uncertainty['id']]
        if coefficient == 0:
            continue
        if kind == 'assembly':
            delta = Fraction(str(coefficient)) * exact_quantity(uncertainty['delta'], 'length')[0]
            for path in uncertainty['fields']:
                nominal = assembly_field_info(nominal_raw, path, exact=True)[0]
                try:
                    assembly_set_field(effective, path, nominal + delta)
                except InvalidAssembly as exc:
                    # The exact native setter must not round an unrepresentable
                    # decimal relation. This is a capacity limit of this one
                    # scenario, not evidence against its physical geometry.
                    if 'relation exacte non représentable en quantité numérique m/cm/mm' not in str(exc):
                        raise
                    raise ScenarioUnavailable(str(exc)) from exc
        else:
            delta = coefficient * uncertainty['delta_si']
            for path in uncertainty['fields']:
                nominal = design_field_info(nominal_raw, path)[0]
                value = nominal + delta
                if not math.isfinite(value):
                    raise InvalidRequest('écart effectif non fini: ' + path)
                design_set_field(effective, path, value)
    check_mask(nominal_raw, effective, parsed, kind)
    return effective


def _mask(raw, fields, kind):
    value = copy.deepcopy(raw)
    if kind == 'fixed':
        value.get('metadata', {}).pop('total_length_cm', None)
        for segment in value['segments']:
            segment.pop('position_start_cm', None)
            segment.pop('position_end_cm', None)
        setter = design_set_field
    else:
        setter = assembly_set_field
    for path in fields:
        setter(value, path, 0.)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def check_mask(nominal_raw, effective_raw, parsed, kind):
    """Check the audit's own locks independently of obsolete search bounds.

    Native fixed DesignBuilder recalculates total_length_cm and segment start/end
    positions. These three derived fields alone are excluded from the mask.
    Assembly q, topology, annotations, profile laws and every unlisted dimension
    remain included. Generated assembly metadata is owned by Assembly.generate.
    """
    _kind(kind)
    fields = parsed['exposed_fields']
    if _mask(nominal_raw, fields, kind) != _mask(effective_raw, fields, kind):
        raise InvalidRequest('masque propre des écarts dimensionnels violé')


def validate_links(parsed, contract, kind, nominal_raw=None):
    """Reject incompatible native links without repairing or dropping a relation.

    Equal variable fields must share one uncertainty identity (or all remain
    locked), even if separate scenario columns happen to agree. Derived formulas
    are checked for every finite scenario, with native arithmetic and semantics.
    This is explicitly no claim about the interior of a continuous domain.
    """
    _kind(kind)
    raw = contract.base if nominal_raw is None else nominal_raw
    ownership = {field: u['id'] for u in parsed['uncertainties'] for field in u['fields']}
    for variable in contract.variables:
        owners = {ownership.get(field) for field in variable['fields']}
        if len(owners) != 1:
            raise InvalidRequest('liaison native incompatible avec les groupes d’incertitude: ' + variable['id'])
    if not contract.derived:
        return
    if kind == 'assembly':
        from ..optimization.assembly_contract import physical_expression
        evaluate = physical_expression
    else:
        evaluate = expression
    for scenario in parsed['scenarios']:
        try:
            effective = perturb(raw, parsed, scenario, kind)
        except ScenarioUnavailable:
            # The pipeline records this scenario as unresolved while preserving
            # every other scenario. No relation is repaired, removed or certified.
            continue
        for relation in contract.derived:
            actual = (assembly_field_info(effective, relation['field'], exact=True)[0]
                      if kind == 'assembly' else design_field_info(effective, relation['field'])[0])
            try:
                expected = evaluate(relation['expression'], effective)[0]
            except (ValueError, ArithmeticError) as exc:
                raise InvalidRequest('relation dérivée non évaluable avant calcul: ' + relation['id']) from exc
            compatible = actual == expected if kind == 'assembly' else math.isclose(
                actual, expected, rel_tol=1e-12, abs_tol=1e-14)
            if not compatible:
                raise InvalidRequest('relation dérivée incompatible avec le scénario ' + scenario['id'] + ': ' + relation['id'])
