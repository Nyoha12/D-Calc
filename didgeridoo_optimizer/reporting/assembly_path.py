"""Immutable assembly witnesses; a manifest is never a command receipt."""
from __future__ import annotations

import copy
import csv
import io
import hashlib
import json
from pathlib import Path
import sys

from .constrained_design import (fingerprint, versions, verify_sources, csv_write,
                                 new_destination, source_files as native_sources)
from .regime_reference import (safe_path, atomic_bytes, write_json, read_json,
                               read_execution as legacy_read_execution, file_sha256)
from ..pipeline.fixed_design import _software_source

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_SCHEMA = 'dcalc.assembly_checkpoint.v1'
COMPLETION_PROTOCOL = 'dcalc.assembly_completion.v1'
PREPARED_COMPLETION = '.pending-execution-completion.json'


def completion_record(output):
    out = Path(output)
    return dict(schema=COMPLETION_PROTOCOL,
                execution_sha256=file_sha256(out/'execution.json'),
                closure_sha256=file_sha256(out/'execution.closed.json'))


def read_execution(output):
    """Versioned assembly authority; historical closures keep their old meaning."""
    value = legacy_read_execution(output)
    if not value.get('ok'):
        return value
    protocol = value.get('completion_protocol')
    if 'completion_protocol' not in value:
        return dict(value, completion_assurance='legacy_closure')
    try:
        if protocol != COMPLETION_PROTOCOL:
            raise ValueError('protocole de clôture inconnu')
        out = safe_path(output)
        completed = read_json(safe_path(out/'execution.completed.json'))
        if completed != completion_record(out):
            raise ValueError('commit terminal incohérent')
    except (OSError, ValueError, TypeError) as exc:
        return dict(ok=False, status='unconfirmed', reason='terminal_receipt_unavailable: '+str(exc))
    return dict(value, completion_assurance='terminal_commit')


def source_files():
    result = native_sources()
    main = getattr(sys.modules.get('__main__'), '__file__', None)
    if main and Path(main).resolve() == ROOT / 'tools/assembly_path.py':
        result['tools/assembly_path.py'] = file_sha256(main)
    return result


def provenance():
    return dict(software=_software_source(), versions=versions(), loaded_sources_sha256=source_files())


def calculation_context(contract):
    return copy.deepcopy(dict(request=contract.request, assembly=contract.base,
        config=contract.context['config'], inputs=getattr(contract, 'input_files', {}),
        effective_models=[p['template'].options for p in contract.projections],
        materials={k:contract.context['material_db'].get(k).as_dict()
                   for k in sorted({p['material_id'] for p in contract.base['pieces'].values()})},
        lock_mask=contract.lock_mask, variables=contract.variables,
        derived=[dict(d, deps=sorted(d['deps'])) for d in contract.derived],
        global_budgets=contract.budgets, criteria=contract.criteria, versions=versions()))


def checkpoint_record(contract, candidate, producer, counters):
    record = dict(schema_version=CHECKPOINT_SCHEMA, calculation_context=calculation_context(contract),
        producer_provenance=producer, candidate=candidate, counters=counters,
        status='witness_only_not_solver_resume', solver_state_complete=False)
    record = copy.deepcopy(record)
    record['checkpoint_sha256'] = fingerprint(record)
    return record


def validate_candidate(contract, row):
    from ..optimization.assembly_contract import native_contract
    alternatives = [a for a in contract.catalogue if a['id'] == row['alternative']]
    if len(alternatives) != 1:
        raise ValueError('alternative inconnue')
    try:
        assembly = contract.generate(row['variables_si'], alternatives[0])
    except (ValueError, ArithmeticError) as exc:
        if row.get('assembly') is not None or row.get('reason') != str(exc) or row['search_feasible']:
            raise ValueError('candidat rejeté altéré') from exc
        return
    if assembly.raw != row['assembly']:
        raise ValueError('paramètres/pièces du témoin altérés')
    contract.check_locks(row['assembly'], alternatives[0])
    # Rejected candidates retain reproducible geometry/projection failures.
    if row.get('reason') and not row['search_feasible'] and not row.get('verified'):
        try:
            generated = {p['configuration']:assembly.generate(p['configuration']) for p in contract.projections}
            assembly.certify_paths()
            for p in contract.projections:
                native_contract(p['request'], dict(contract.context, design=generated[p['configuration']]['design']))
        except (ValueError, ArithmeticError) as exc:
            if str(exc) != row['reason']:
                raise ValueError('raison du rejet altérée') from exc
            if row.get('projections') or row.get('bom') or row.get('positions'):
                raise ValueError('rejet incohérent')
            return
        raise ValueError('rejet non reproductible')
    actual = row.get('projections', [])
    expected_ids = {p['id'] for p in contract.projections}
    if len({p['id'] for p in actual}) != len(actual) or not {p['id'] for p in actual} <= expected_ids:
        raise ValueError('identités de projection altérées')
    if not row.get('partial') and {p['id'] for p in actual} != expected_ids:
        raise ValueError('projections manquantes')
    expected_positions = []
    expected_bom = assembly.generate(contract.projections[0]['configuration'])['bom']
    for projection in actual:
        parent = next(p for p in contract.projections if p['id'] == projection['id'])
        if projection['configuration'] != parent['configuration']:
            raise ValueError('correspondance parent/configuration altérée')
        generated = assembly.generate(projection['configuration'])
        physical = generated['design'].as_dict()
        if physical != projection['physical_design'] or fingerprint(physical) != projection['profile_sha256']:
            raise ValueError('profil/configuration du témoin altéré')
        if projection['effective_request'] != parent['request'] or projection['parent_sha256'] != contract.parent_sha256:
            raise ValueError('projection/demande parent altérée')
        if (projection['component_ids'] != sorted(assembly.raw['pieces']) or
                projection['spans'] != generated['spans'] or
                projection['geometry'] != generated['geometry_certificate'] or
                projection['lumen_volume_m3'] != generated['lumen_volume_m3'] or
                projection['lumen_volume_method'] != generated['lumen_volume_method']):
            raise ValueError('provenance géométrique du profil altérée')
        expected_positions.extend(dict(position, configuration=parent['configuration'],
            deployed_length_m=generated['deployed_length_m']) for position in generated['positions'])
        if not generated['positions']:
            expected_positions.append(dict(configuration=parent['configuration'], block_id=None,
                q_m=None, overlap_m=None, deployed_length_m=generated['deployed_length_m']))
    if row['bom'] != expected_bom or row['positions'] != expected_positions:
        raise ValueError('nomenclature/positions altérées')


def read_checkpoint(output, contract):
    out = safe_path(output); paths = sorted(out.glob('checkpoint_*.json'))
    if not paths:
        return None
    value = read_json(paths[-1])
    if value.get('schema_version') != CHECKPOINT_SCHEMA or value.get('checkpoint_sha256') != fingerprint(
            {k:v for k,v in value.items() if k != 'checkpoint_sha256'}):
        raise ValueError('checkpoint altéré/incompatible')
    if value['calculation_context'] != calculation_context(contract):
        raise ValueError('contexte/sources/options/versions de reprise différents')
    producer = value['producer_provenance']
    if producer['versions'] != versions() or not producer['loaded_sources_sha256']:
        raise ValueError('versions/manifeste producteur incompatibles')
    verify_sources(producer['loaded_sources_sha256'])
    validate_candidate(contract, value['candidate'])
    return value


def export_result(output, result, contract):
    output = Path(output); candidate = result.get('best')
    limit = contract.budgets['output_mib']*1024**2
    def write(path, value):
        write_json(path, value, budget_bytes=limit)
    def table(path, rows, fields):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)
        atomic_bytes(path, stream.getvalue().encode('utf-8'), budget_bytes=limit)
    if candidate and candidate.get('assembly'):
        validate_candidate(contract, candidate)
        write(output / 'assembly.json', candidate['assembly'])
        for p in candidate['projections']:
            design_name = 'design_' + p['id'] + '.json'
            request_name = 'request_' + p['id'] + '.json'
            write(output / design_name, p['physical_design'])
            write(output / request_name, p['effective_request'])
            if read_json(output / design_name) != p['physical_design']:
                raise ValueError('relecture Design différente')
            p['design_file'] = design_name; p['request_file'] = request_name
        table(output / 'pieces.csv', candidate['bom'],
            ['piece_id', 'kind', 'material_id', 'stock_length_m', 'inner_diameter_m',
             'outer_diameter_m', 'material_volume_m3', 'mass_kg', 'density_source'])
        table(output / 'positions.csv', candidate['positions'],
            ['configuration', 'block_id', 'q_m', 'overlap_m', 'deployed_length_m'])
    fields = ['id', 'projection', 'observable', 'role', 'status', 'value_si', 'unit_si',
              'target_si', 'lower_si', 'upper_si', 'tolerance_si', 'tolerance_dimension',
              'error_cents', 'margin', 'margin_unit', 'convergence_estimate', 'reason']
    table(output / 'criteria.csv', candidate['criteria'] if candidate else [], fields)
    write(output / 'result.json', result)
    atomic_bytes(output / 'report.txt', (
        'Assemblage de pièces partagées — modèle statique nominal\n'
        f"État : {result['status']} ; obligations échantillonnées : {result['sampled_hard_conforming']}\n"
        f"Demande entièrement couverte : {result['request_fully_covered']}\n"
        f"Compteurs cumulés : {json.dumps(result['counters'], ensure_ascii=False)}\n"
        'Catalogue borné ; recherche continue locale, aucune impossibilité globale ou optimalité prouvée.\n'
        'Géométrie nominale et critères acoustiques échantillonnés sont distincts.\n'
        'Joint inner_tip_isolated idéal ; guidage, étanchéité réelle et usure non validés.\n'
        'Aucune fréquence jouée, accessibilité toot ou robustesse certifiée.\n'
        'Un checkpoint conserve un témoin et des compteurs, pas un état de reprise du solveur.\n'
    ).encode(), budget_bytes=limit)
    manifest = {p.name:file_sha256(p) for p in output.iterdir()
                if p.is_file() and not p.name.startswith('.pending-')}
    write(output / 'manifest.json', manifest)


def read_result(output, contract=None):
    out = safe_path(output)
    execution = read_execution(out)
    if not execution.get('ok'):
        return dict(ok=False, status=execution.get('status', 'unconfirmed'), execution=execution)
    manifest = read_json(out / 'manifest.json')
    if execution.get('manifest_sha256') != file_sha256(out / 'manifest.json'):
        raise ValueError('manifest altéré')
    if not {'result.json', 'report.txt', 'plan.json'} <= manifest.keys():
        raise ValueError('livraison incomplète')
    for name, sha in manifest.items():
        if Path(name).name != name or file_sha256(safe_path(out / name)) != sha:
            raise ValueError('sortie altérée: ' + name)
    result = read_json(out / 'result.json')
    producer = result['plan']['provenance']
    if not producer['loaded_sources_sha256']:
        raise ValueError('manifeste producteur vide')
    if producer['versions'] != versions():
        raise ValueError('versions différentes')
    verify_sources(producer['loaded_sources_sha256'])
    verify_sources(result['plan'].get('parent_sources_checked_sha256', {}))
    if contract is not None:
        if result['plan']['calculation_context'] != calculation_context(contract):
            raise ValueError('contexte/sources différents')
        if result.get('best'):
            validate_candidate(contract, result['best'])
    return dict(ok=True, execution=execution, result=result)
