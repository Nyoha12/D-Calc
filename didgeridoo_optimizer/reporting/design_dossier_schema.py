"""Strict, bounded documentary admission. This module uses only the standard library."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

import hashlib
_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'dcalc.design_dossier.job.v1'
RESULT_SCHEMA = 'dcalc.design_dossier.v2'
CHILD_SCHEMA = 'dcalc.design_dossier.child.v2'
EXECUTION_SCHEMA = 'dcalc.design_dossier.execution.v2'
MIB = 1024 ** 2
FILE_LIMIT = 8 * MIB
LIMITS = dict(experiments=64, native_reads=80, input_files=4096, input_mib=512,
              child_seconds=175, total_seconds=550, closure_seconds=20,
              memory_mib=768, file_mib=8, output_mib=160, blas_threads=1)
OWN_SOURCES = ('didgeridoo_optimizer/reporting/design_dossier_schema.py',
               'didgeridoo_optimizer/reporting/design_dossier.py',
               'didgeridoo_optimizer/pipeline/design_dossier.py', 'tools/design_dossier.py')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def exact(value, keys, label):
    require(type(value) is dict and set(value) == set(keys), label + ': clés strictes requises')


def number(value, label):
    require(type(value) in (int, float) and math.isfinite(value), label + ': nombre fini non booléen')
    return value


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'clé JSON dupliquée: ' + key)
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('JSON non fini: ' + value)


def safe(path, *, exists=True):
    original = Path(path)
    walk = Path(original.anchor) if original.is_absolute() else Path.cwd()
    for part in original.parts[1:] if original.is_absolute() else original.parts:
        walk = walk/part
        require(not walk.is_symlink(), 'lien symbolique interdit: ' + str(walk))
    p = Path(os.path.abspath(os.fspath(path)))
    for part in (p, *p.parents):
        require(not part.is_symlink(), 'lien symbolique interdit: ' + str(part))
    if exists:
        p.stat()
    return p


def data(path, limit=FILE_LIMIT):
    p = safe(path)
    info = p.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'fichier/type/taille: ' + str(p))
    with p.open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'fichier agrandi pendant lecture')
    return raw


def sha(path):
    return hashlib.sha256(data(path)).hexdigest()


def loads(raw):
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=_nonfinite)
        pending = [(value, 0)]; nodes = 0
        while pending:
            item, depth = pending.pop(); nodes += 1
            require(depth <= 64 and nodes <= 250000, 'budget structure JSON')
            if type(item) is float:
                require(math.isfinite(item), 'JSON nombre non fini')
            elif type(item) is dict:
                pending.extend((v, depth+1) for v in item.values())
            elif type(item) is list:
                pending.extend((v, depth+1) for v in item)
        return value
    except (RecursionError, UnicodeError) as exc:
        raise ValueError('JSON trop profond ou encodage invalide') from exc


def read(path, limit=FILE_LIMIT):
    return loads(data(path, limit))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + '\n').encode()


def same(a, b):
    return encoded(a) == encoded(b)


def identifier(value, label):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value), label + ': identifiant')
    return value


def validate_job(raw):
    exact(raw, ('schema_version', 'candidates', 'budgets'), 'JOB')
    require(raw['schema_version'] == SCHEMA, 'version JOB non supportée')
    b = raw['budgets']
    exact(b, LIMITS, 'budgets')
    for key, cap in LIMITS.items():
        require(type(b[key]) is int and 1 <= b[key] <= cap, 'budget ' + key)
    require(b['closure_seconds'] >= 5 and b['total_seconds'] > b['closure_seconds'], 'réserve de clôture >=5s')
    require(b['memory_mib'] >= 128 and b['output_mib'] >= 4, 'budget minimum mémoire/sortie')
    cs = raw['candidates']
    require(type(cs) is list and 1 <= len(cs) <= 8, '1..8 candidats fixes')
    ids = set(); total = 0
    for c in cs:
        exact(c, ('id', 'passive', 'geometry', 'played'), 'candidat fixe')
        ident = identifier(c['id'], 'candidat.id')
        require(ident not in ids, 'candidat dupliqué'); ids.add(ident)
        exact(c['passive'], ('bundle', 'scenario', 'projection'), 'passive')
        identifier(c['passive']['scenario'], 'scenario')
        require(c['passive']['projection'] == 'fixed', 'projection non supportée: fixed requis')
        exact(c['geometry'], ('bundle',), 'geometry')
        require(type(c['played']) is list and 1 <= len(c['played']) <= 8, '1..8 expériences par candidat')
        expids = set()
        for e in c['played']:
            exact(e, ('id', 'bundle'), 'played')
            ident = identifier(e['id'], 'played.id')
            require(ident not in expids, 'expérience dupliquée'); expids.add(ident)
        total += len(c['played'])
        for entry in [c['passive'], c['geometry'], *c['played']]:
            path = entry['bundle']
            require(type(path) is str and 0 < len(path) <= 1024 and '\x00' not in path and '\\' not in path
                    and not Path(path).is_absolute(), 'bundle: chemin relatif au JOB requis')
    require(total <= b['experiments'], 'budget total expériences')
    return raw


def profile_name(selection):
    s = selection['scenario']; p = selection['projection']
    return f'profile_{len(s)}_{s}__{p}.json'


class Inventory:
    """Pins bounded bytes and follows explicit native references, never arrays."""
    def __init__(self, budgets):
        self.budgets = budgets
        self.pins = {}
        self.sizes = {}
        self.parsed = set()
        self.total = 0

    def add(self, path, expected=None, *, follow=True):
        p = safe(path); key = str(p)
        if key not in self.pins:
            size = p.stat().st_size
            require(len(self.pins) < self.budgets['input_files'], 'budget total fichiers')
            require(size <= self.budgets['file_mib'] * MIB, 'budget fichier: ' + key)
            require(self.total + size <= self.budgets['input_mib'] * MIB, 'budget total entrées')
            raw = data(p, self.budgets['file_mib'] * MIB)
            self.pins[key] = hashlib.sha256(raw).hexdigest()
            self.sizes[key] = len(raw); self.total += len(raw)
        else:
            raw = None
        if expected is not None:
            require(type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected), 'empreinte native invalide')
            require(self.pins[key] == expected, 'empreinte source divergente: ' + key)
        if follow and p.suffix == '.json' and key not in self.parsed:
            self.parsed.add(key)
            self.references(loads(raw if raw is not None else data(p)), p.parent)
        return self.pins[key]

    def references(self, value, parent, depth=0):
        require(depth <= 64, 'profondeur métadonnées')
        if type(value) is dict:
            if 'path' in value and 'sha256' in value and value['sha256'] is not None:
                ref = value['path']
                require(type(ref) is str and len(ref) <= 4096, 'référence native')
                self.add(Path(ref) if Path(ref).is_absolute() else parent/ref, value['sha256'])
            for key in ('producer_sources', 'loaded_sources_sha256', 'executed_sources_sha256'):
                if key in value:
                    refs = value[key]
                    require(type(refs) is dict and len(refs) <= 256, 'inventaire sources')
                    for name, h in refs.items():
                        require(type(name) is str and name.endswith('.py') and not Path(name).is_absolute()
                                and '..' not in Path(name).parts, 'source Python native')
                        self.add(ROOT/name, h, follow=False)
            for child in value.values():
                self.references(child, parent, depth+1)
        elif type(value) is list:
            for child in value:
                self.references(child, parent, depth+1)

    def bundle(self, path):
        root = safe(path)
        require(root.is_dir(), 'bundle répertoire requis')
        names = []
        def visit(directory, depth):
            require(depth <= 4, 'profondeur bundle')
            with os.scandir(directory) as entries:
                for entry in entries:
                    # Native pending hardlinks are not published artifacts.
                    if entry.name.startswith('.'):
                        continue
                    p = safe(entry.path)
                    if p.is_dir():
                        visit(p, depth+1)
                    else:
                        self.add(p)
                        names.append(p.relative_to(root).as_posix())
        visit(root, 0)
        require({'plan.json', 'result.json', 'manifest.json'} <= set(names), 'bundle natif incomplet')
        return sorted(names)


def verify_pins(pins):
    for path, expected in pins.items():
        require(sha(path) == expected, 'source modifiée à la frontière: ' + path)


def destinations(output, bundle_paths, pins):
    if output is None:
        return
    out = safe(output, exists=False)
    require(not out.exists() and out.parent.is_dir(), 'destination nouvelle avec parent existant requise')
    for path in [*bundle_paths, *map(Path, pins)]:
        require(out != path and not out.is_relative_to(path) and not path.is_relative_to(out), 'destination recouvre une source')


def static_association(candidate, base):
    """Cheap contradictory-association rejection; native readers remain authoritative."""
    pb = safe(base/candidate['passive']['bundle'])
    gb = safe(base/candidate['geometry']['bundle'])
    tp = read(pb/'plan.json'); gp = read(gb/'plan.json'); evidence = read(gb/'evidence.json')
    require(tp['job']['input']['kind'] == 'fixed', 'type passif non supporté')
    require(gp['schema'] == 'dcalc.geometry_state.plan.v1', 'type geometry non supporté')
    matches = [g for g in tp['geometry'] if (g['scenario'], g['projection']) ==
               (candidate['passive']['scenario'], candidate['passive']['projection'])]
    require(len(matches) == 1 and not matches[0]['issue'], 'projection native absente/indisponible')
    profile_path = pb/profile_name(candidate['passive'])
    profile = read(profile_path)
    require(type(profile.get('segments')) is list and 1 <= len(profile['segments']) <= 128, 'sections physiques 1..128')
    for segment in profile['segments']:
        for key in ('length_cm', 'd_in_cm', 'd_out_cm'):
            require(number(segment[key], key) > 0, 'dimension positive')
    inputs = {k: gp['inputs'][k]['sha256'] for k in ('config', 'design', 'materials', 'variant_rules')}
    require(sha(profile_path) == inputs['design'], 'association profil passif / geometry DESIGN')
    for key in ('config', 'materials', 'variant_rules'):
        require(tp['input_files'][key]['sha256'] == inputs[key], 'association CONFIG/DB/règles: ' + key)
    require(tp['effective_models'] == [gp['effective']], 'association modèles effectifs passifs/géométriques')
    require(evidence['inputs'] == inputs and evidence['effective'] == gp['effective'] and
            evidence['physical_radius_m'] == gp['physical_radius_m'], 'association preuve géométrique')
    for exp in candidate['played']:
        rb = safe(base/exp['bundle']); rp = read(rb/'plan.json'); request = rp['request']; meta = rp['metadata']
        case = request['case']
        require(case['kind'] == meta['kind'] == 'saved_state', 'expérience non supportée: saved_state requis')
        for key in ('config', 'design'):
            p = Path(case[key]); p = p if p.is_absolute() else rb/p
            require(sha(p) == inputs[key], 'association cas joué ' + key)
        require({k:v['sha256'] for k,v in rp['input_files'].items()} == inputs == meta['inputs'], 'association entrées jouées')
        require(meta['recipe'] == case['recipe'] == evidence['recipe'] ==
                {k:gp['recipe'][k] for k in evidence['recipe']}, 'association recette jouée')
        require(meta['effective'] == gp['effective'] and meta['evidence'] == evidence, 'association fidélité/modèles/rayon')
        require(meta['model_sha256'] == sha(gb/'realization/model.json') and
                meta['model_parameters_sha256'] == evidence['model_sha256'] and
                evidence['source_sha256'] == sha(gb/'source.json'), 'association modèle/source scalaire')
        require(read(rb/'result.json')['context_checks']['finite_fidelity'] == read(gb/'result.json')['fidelity'],
                'association fidélité finie')
    return dict(profile=str(profile_path), profile_sha256=sha(profile_path), inputs=inputs,
                model_sha256=sha(gb/'realization/model.json'))


_COMPOSITION_SOURCES = {}
_NATIVE_CODE_CACHE = {}


def loaded_sources():
    """Compare disk with import-time identities; never refresh to fit new bytes."""
    import importlib
    import sys
    result = {}
    for name in OWN_SOURCES:
        module = importlib.import_module(name[:-3].replace('/', '.'))
        modules = [module]
        main = sys.modules.get('__main__')
        if getattr(main, '__file__', None) and Path(main.__file__).resolve() == ROOT/name:
            modules.append(main)
        h = sha(ROOT/name)
        for item in modules:
            require(Path(item.__file__).resolve() == ROOT/name, 'module possédé étranger: '+name)
            snapshot = getattr(item, '_LOADED_SOURCE_SHA256', getattr(item, 'ENTRY_SHA', None))
            require(snapshot == h, 'source modifiée après import: '+name)
        result[name] = h
    return result


def composition_sources():
    """Verify the exact loaded native helpers and their scalar dependencies.

    Compilation compares code, without running producers or replacing sys.modules.
    Literal globals used by these functions are checked too. Test reader doubles
    are outside this check and remain explicitly synthetic test substitutions.
    """
    import ast
    import importlib
    import sys
    from types import CodeType, FunctionType
    played = importlib.import_module('didgeridoo_optimizer.optimization.played_search')
    geometry = importlib.import_module('didgeridoo_optimizer.pipeline.geometry_state')
    rt = importlib.import_module('didgeridoo_optimizer.nonlinear.register_target')
    pending = [played.played_rows, geometry.state_recipe, rt.metric_target, rt.metric_quantity]
    seen = set(); sources = {}; compiled = {}; literals = {}
    while pending:
        function = pending.pop()
        require(isinstance(function, FunctionType), 'fonction native substituée')
        if id(function) in seen: continue
        seen.add(id(function))
        module = sys.modules[function.__module__]
        path = ROOT/(function.__module__.replace('.', '/')+'.py')
        require(Path(module.__file__).resolve() == path and Path(function.__code__.co_filename).resolve() == path,
                'origine fonction native incohérente')
        name = path.relative_to(ROOT).as_posix()
        if name not in sources:
            raw = data(path); h = hashlib.sha256(raw).hexdigest()
            snapshot = getattr(module, '_LOADED_SOURCE_SHA256', h)
            require(h == snapshot, 'source native modifiée après import: '+name)
            require(_COMPOSITION_SOURCES.get(name, h) == h, 'source de composition modifiée: '+name)
            sources[name] = h
            if (name, h) not in _NATIVE_CODE_CACHE:
                codes = {}
                def collect(code):
                    codes[code.co_qualname] = code
                    for item in code.co_consts:
                        if isinstance(item, CodeType): collect(item)
                collect(compile(raw, str(path), 'exec', dont_inherit=True, optimize=sys.flags.optimize))
                constants = {}
                for node in ast.parse(raw).body:
                    if isinstance(node, ast.Assign):
                        try: value = ast.literal_eval(node.value)
                        except (ValueError, TypeError): continue
                        for target in node.targets:
                            if isinstance(target, ast.Name): constants[target.id] = value
                _NATIVE_CODE_CACHE[name, h] = codes, constants
            compiled[name], literals[name] = _NATIVE_CODE_CACHE[name, h]
            # Refuse other stale functions in the same native module too.
            for item in vars(module).values():
                if isinstance(item, FunctionType) and item.__module__ == module.__name__:
                    require(item.__code__ == compiled[name].get(item.__qualname__),
                            'module natif préchargé incohérent: '+name)
        require(function.__code__ == compiled[name].get(function.__qualname__),
                'code natif préchargé incohérent: '+name)
        for key in function.__code__.co_names:
            value = function.__globals__.get(key)
            if key in literals[name]:
                require(value == literals[name][key], 'constante native préchargée incohérente: '+key)
            if isinstance(value, FunctionType) and value.__module__.startswith('didgeridoo_optimizer.'):
                pending.append(value)
    # played_rows accesses the native module's DIMENSIONS via rt.
    require(played.rt is rt and rt.DIMENSIONS == literals['didgeridoo_optimizer/nonlinear/register_target.py']['DIMENSIONS'],
            'dimensions natives préchargées incohérentes')
    for name,h in sources.items():
        require(sha(ROOT/name) == h, 'source modifiée pendant contrôle composition')
    _COMPOSITION_SOURCES.update({name:h for name,h in sources.items() if name not in _COMPOSITION_SOURCES})
    return sources


def preflight(job, output=None):
    """Bounded documentary inspection only. No destination, arrays or native reader."""
    own = loaded_sources()
    native = composition_sources()
    job_path = safe(job)
    raw = validate_job(read(job_path, 256 * 1024)); b = raw['budgets']
    base = job_path.parent
    bundles = []
    seen = set()
    for c in raw['candidates']:
        for kind, entry in [('tolerance_audit', c['passive']), ('geometry_state', c['geometry']),
                            *[('register_target', e) for e in c['played']]]:
            path = safe(base/entry['bundle'])
            key = kind, str(path)
            if key not in seen:
                seen.add(key); bundles.append(dict(kind=kind, path=str(path)))
    require(len(bundles) <= b['native_reads'], 'budget total lecteurs')
    # Reserve at least one second per admitted read plus mandatory final closure.
    require(b['total_seconds'] >= len(bundles) + b['closure_seconds'], 'budget total lecture/clôture')
    require((len(bundles)+2)*256*1024 <= b['output_mib']*MIB, 'budget représentation sortie')
    inv = Inventory(b)
    inv.add(job_path, follow=False)
    for rel,h in {**own, **native}.items():
        inv.add(ROOT/rel, h, follow=False)
    for bundle in bundles:
        bundle['files'] = inv.bundle(bundle['path'])
    associations = [static_association(c, base) for c in raw['candidates']]
    destinations(output, [Path(x['path']) for x in bundles], inv.pins)
    verify_pins(inv.pins)
    loaded_sources(); composition_sources()
    plan = dict(schema=SCHEMA, job_path=str(job_path), job_sha256=sha(job_path), job=raw,
                bundles=bundles, associations=associations, pins=inv.pins, sizes=inv.sizes,
                input_bytes=inv.total, native_read_count=len(bundles), science_performed=False)
    metadata = sum(inv.sizes[str(Path(x['path'])/name)] for x in bundles for name in ('plan.json','result.json'))
    repeated = sum(inv.sizes[str(safe(base/entry['bundle'])/name)] for c in raw['candidates']
                   for entry in [c['passive'], c['geometry'], *c['played']] for name in ('plan.json','result.json'))
    # Conservative representation allowance: repeated requests, per-window rows,
    # JSON and CSV views plus plans, profiles and terminal authority. Admission
    # rejects excessive metadata rather than discovering it after readers run.
    render_bound = 256*1024 + 4*metadata + 8*repeated
    require(render_bound <= FILE_LIMIT, 'budget représentation résultat par fichier')
    plan['closure_bytes'] = 4*render_bound + 2*len(encoded(plan)) + MIB
    require(plan['closure_bytes'] + len(bundles)*128*1024 <= b['output_mib']*MIB,
            'budget sortie total avant destination')
    return plan
