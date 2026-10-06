"""Shared physical stock and exact nominal serial telescopes, without acoustic laws.

All tube quantities are dimensioned R41 values. Fixed pieces carry complete native
profiles (not slices). Decimal input numbers define the rational geometry model;
there is no tolerance, clamping or automatic shortening at contact boundaries.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

from ..optimization.design_contract import identifier, obj, quantity, UNITS
from ..pipeline.design_input import (validate_design, _load_yaml_design,
    _unique_json_mapping, _reject_json_constant, _validate_annotations)
from .discretization import GeometryDiscretizer

SCHEMA = 'dcalc.assembly.v1'
MAX_PIECES = 256
MAX_CONFIGURATIONS = 128
MAX_SEGMENTS = 2048


class InvalidAssembly(ValueError):
    """Invalid input, unsupported topology, or inadmissible nominal geometry."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_assembly(path):
    source = Path(path).resolve(strict=True)
    if not source.is_file() or source.stat().st_size > 2 * 1024**2:
        raise InvalidAssembly('ASSEMBLY: fichier régulier <=2 Mio requis')
    data = source.read_bytes()
    if len(data) > 2 * 1024**2:
        raise InvalidAssembly('ASSEMBLY trop volumineux')
    text = data.decode('utf-8-sig')
    if source.suffix.lower() == '.json':
        raw = json.loads(text, object_pairs_hook=_unique_json_mapping,
                         parse_constant=_reject_json_constant)
    elif source.suffix.lower() in {'.yaml', '.yml'}:
        raw = _load_yaml_design(text)
    else:
        raise InvalidAssembly('ASSEMBLY doit être YAML ou JSON')
    _validate_annotations(raw, 'assembly', set())
    return raw, {'name': source.name, 'sha256': hashlib.sha256(data).hexdigest(), 'read': True}


def exact_quantity(value, dimension=None):
    """Native quantity validation, with the decimal input interpreted exactly."""
    _, dim = quantity(value, dimension)
    return Fraction(str(value['value'])) * Fraction(str(UNITS[value['unit']][1])), dim


def _q(value, where, *, positive=True):
    si, _ = exact_quantity(value, 'length')
    if not math.isfinite(si) or (si <= 0 if positive else si < 0):
        raise InvalidAssembly(f'{where}: longueur {"positive" if positive else "non négative"} finie requise')
    return si


def _annotations(value, where):
    obj(value, set(value) if isinstance(value, dict) else set(), where)
    _validate_annotations(value, where, set())


def _collection(value, where, limit):
    if not isinstance(value, dict) or not 1 <= len(value) <= limit:
        raise InvalidAssembly(f'{where}: objet contenant 1..{limit} identités requis')
    for key in value:
        identifier(key, where)


def _field(raw, path):
    if not isinstance(path, str):
        raise InvalidAssembly('référence de champ textuelle requise')
    p = path.split('.')
    try:
        if len(p) == 3 and p[0] == 'pieces':
            piece = raw['pieces'][p[1]]
            if piece['kind'] != 'tube' or p[2] not in {'length','inner_diameter','outer_diameter','wall'}:
                raise KeyError(path)
            return piece, p[2], True
        if len(p) == 5 and p[0] == 'pieces' and p[2] == 'segments':
            piece = raw['pieces'][p[1]]
            if piece['kind'] != 'fixed' or p[4] not in {'length','diameter_in','diameter_out'}:
                raise KeyError(path)
            segment = next(s for s in piece['segments'] if s['id'] == p[3])
            return segment, p[4], True
        if len(p) == 4 and p[0] == 'configurations' and p[2] == 'q':
            return raw['configurations'][p[1]]['q'], p[3], True
    except (KeyError, StopIteration, TypeError) as exc:
        raise InvalidAssembly(f'champ physique absent/non variable: {path}') from exc
    raise InvalidAssembly(f'champ physique absent/non variable: {path}')


def field_info(raw, path, *, exact=False):
    """Return SI value, dimension and source-unit factor for a stable physical ID."""
    container, key, _ = _field(raw, path)
    try:
        value = container[key]
    except KeyError as exc:
        raise InvalidAssembly(f'champ non déclaré: {path}') from exc
    si, dim = exact_quantity(value, 'length')
    return si if exact else float(si), dim, UNITS[value['unit']][1]


def set_field(raw, path, si):
    """Set an exposed field; prefer SI, but never round an exact relation."""
    current, _, _ = field_info(raw, path, exact=True)
    container, key, _ = _field(raw, path)
    if isinstance(si, Fraction):
        if si == current:
            return  # Keep an already exact nominal quantity and its notation.
        for unit in ('m', 'cm', 'mm'):
            factor = Fraction(str(UNITS[unit][1]))
            converted = float(si / factor)
            if math.isfinite(converted) and Fraction(str(converted)) * factor == si:
                container[key] = {'value': converted, 'unit': unit}
                return
        raise InvalidAssembly(f'{path}: relation exacte non représentable en quantité numérique m/cm/mm')
    if isinstance(si, bool) or not isinstance(si, (int, float)) or not math.isfinite(si):
        raise InvalidAssembly(f'{path}: valeur SI finie requise')
    container[key] = {'value': float(si), 'unit': 'm'}


class Assembly:
    """Immutable input snapshot. Constructor validates structure; generate checks fit.

    Mechanically invalid catalogue alternatives can therefore remain explicit
    candidates until generation reports their rejection.
    """
    def __init__(self, raw, material_db, config):
        try:
            _validate_annotations(raw, 'assembly', set())
            self._raw = json.loads(json.dumps(raw, allow_nan=False))
            self.material_db = material_db
            self.config = copy.deepcopy(config)
            self._validate()
            self.sha256 = digest(self._raw)
        except (KeyError, TypeError, OverflowError, RecursionError) as exc:
            raise InvalidAssembly('structure/valeur d’assemblage invalide: '+str(exc)) from exc

    @property
    def raw(self):
        return copy.deepcopy(self._raw)

    @property
    def configurations(self):
        return copy.deepcopy(self._raw['configurations'])

    def field_info(self, path):
        return field_info(self._raw, path)

    def _validate(self):
        raw = self._raw
        obj(raw, {'schema_version','id','pieces','blocks','configurations','paths','annotations'},
            'assembly', {'schema_version','id','pieces','blocks','configurations'})
        if raw['schema_version'] != SCHEMA:
            raise InvalidAssembly(f'assembly.schema_version: {SCHEMA} requis')
        identifier(raw['id'], 'assembly.id')
        _annotations(raw.get('annotations', {}), 'assembly.annotations')
        _collection(raw['pieces'], 'pieces', MAX_PIECES)
        segment_count = 0
        for pid, piece in raw['pieces'].items():
            obj(piece, {'kind','material_id','length','inner_diameter','outer_diameter','wall','segments','stock','annotations'},
                f'pieces.{pid}', {'kind','material_id'})
            identifier(piece['material_id'], f'pieces.{pid}.material_id')
            self.material_db.get(piece['material_id'])
            _annotations(piece.get('annotations', {}), f'pieces.{pid}.annotations')
            if piece['kind'] == 'tube':
                segment_count += 1
                required = {'kind','material_id','length','inner_diameter'}
                if not required <= set(piece) or ('wall' in piece) == ('outer_diameter' in piece):
                    raise InvalidAssembly(f'{pid}: tube exige length, inner_diameter et exactement wall ou outer_diameter')
                if set(piece) - required - {'wall','outer_diameter','annotations'}:
                    raise InvalidAssembly(f'{pid}: champ incompatible avec un tube cylindrique')
                for key in ('length','inner_diameter','outer_diameter','wall'):
                    if key in piece: _q(piece[key], f'{pid}.{key}')
            elif piece['kind'] == 'fixed':
                if set(piece) - {'kind','material_id','segments','stock','annotations'}:
                    raise InvalidAssembly(f'{pid}: champ incompatible avec une pièce fixe')
                segments = piece.get('segments')
                if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
                    raise InvalidAssembly(f'{pid}: segments natifs entiers requis')
                segment_count += len(segments)
                if segment_count > MAX_SEGMENTS:
                    raise InvalidAssembly('assemblage: nombre total de segments physiques excessif')
                ids = set()
                for segment in segments:
                    obj(segment, {'id','kind','length','diameter_in','diameter_out','profile_params'},
                        f'{pid}.segment', {'id','kind','length','diameter_in','diameter_out'})
                    sid = identifier(segment['id'], 'segment.id')
                    if sid in ids: raise InvalidAssembly(f'{pid}: segment id dupliqué: {sid}')
                    ids.add(sid)
                    for key in ('length','diameter_in','diameter_out'): _q(segment[key], f'{pid}.{sid}.{key}')
                    params = segment.get('profile_params', {})
                    if isinstance(params, dict):
                        if segment['kind'] == 'flare_powerlaw' and params.get('power', params.get('flare_parameter', 2.)) < .05:
                            raise InvalidAssembly('profil hors domaine natif: aucun clamp de power')
                        if segment['kind'] == 'flare_exponential' and params.get('flare_parameter', 3.) < 1.e-6:
                            raise InvalidAssembly('profil hors domaine natif: aucun clamp exponentiel')
                # Native profile semantics and parameters are validated without
                # applying whole-instrument CONFIG constraints to this one piece.
                validate_design({'id':pid, 'segments':self._fixed_segments(piece), 'metadata':{}}, self.material_db, {})
                if 'stock' in piece:
                    obj(piece['stock'], {'length','outer_diameter'}, f'{pid}.stock', {'length','outer_diameter'})
                    for key in piece['stock']: _q(piece['stock'][key], f'{pid}.stock.{key}')
            else:
                raise InvalidAssembly(f'{pid}: kind non couvert; seuls fixed et tube sont pris en charge')
        if segment_count > MAX_SEGMENTS:
            raise InvalidAssembly('assemblage: nombre total de segments physiques excessif')
        blocks = raw['blocks']
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_PIECES:
            raise InvalidAssembly('blocks: série explicite non vide requise')
        ids, used, telescopes = set(), set(), set()
        for block in blocks:
            obj(block, {'id','kind','piece','inner','outer','orientation','seal','min_overlap','annotations'},
                'block', {'id','kind'})
            bid = identifier(block['id'], 'block.id')
            if bid in ids: raise InvalidAssembly(f'block id dupliqué: {bid}')
            ids.add(bid)
            _annotations(block.get('annotations', {}), f'{bid}.annotations')
            if block['kind'] == 'fixed':
                if set(block)-{'id','kind','piece','annotations'} or 'piece' not in block:
                    raise InvalidAssembly(f'{bid}: raccord fixe exige piece seulement')
                refs = [block['piece']]
                kinds = ['fixed']
            elif block['kind'] == 'telescope':
                required = {'inner','outer','orientation','seal','min_overlap'}
                if not required <= set(block) or set(block)-required-{'id','kind','annotations'}:
                    raise InvalidAssembly(f'{bid}: définition télescopique incomplète')
                if block['orientation'] not in {'outer_first','inner_first'}:
                    raise InvalidAssembly(f'{bid}: orientation non couverte')
                if block['seal'] != 'inner_tip_isolated':
                    raise InvalidAssembly(f'{bid}: annulus communicant/joint non couvert; inner_tip_isolated requis')
                _q(block['min_overlap'], f'{bid}.min_overlap')
                refs, kinds = [block['inner'],block['outer']], ['tube','tube']
                telescopes.add(bid)
            else:
                raise InvalidAssembly(f'{bid}: topologie non couverte (branche, coude, insertion multiple)')
            for ref, kind in zip(refs, kinds):
                identifier(ref, 'piece reference')
                if ref not in raw['pieces'] or raw['pieces'][ref]['kind'] != kind:
                    raise InvalidAssembly(f'{bid}: référence de pièce incompatible: {ref}')
                if ref in used:
                    raise InvalidAssembly(f'{bid}: double emploi/insertion de la pièce {ref} non couvert')
                used.add(ref)
        if used != set(raw['pieces']):
            raise InvalidAssembly('chaque pièce déclarée doit être utilisée exactement une fois')
        _collection(raw['configurations'], 'configurations', MAX_CONFIGURATIONS)
        for cid, cfg in raw['configurations'].items():
            obj(cfg, {'q','annotations'}, f'configurations.{cid}')
            _annotations(cfg.get('annotations', {}), f'{cid}.annotations')
            obj(cfg.get('q', {}), telescopes, f'{cid}.q', telescopes)
            for bid, q in cfg.get('q', {}).items(): _q(q, f'{cid}.q.{bid}', positive=False)
        paths = raw.get('paths', [])
        if not isinstance(paths, list) or len(paths) > 128:
            raise InvalidAssembly('paths: liste bornée requise')
        ids = set()
        for path in paths:
            obj(path, {'id','kind','from','to'}, 'path', {'id','kind','from','to'})
            pid = identifier(path['id'], 'path.id')
            if pid in ids: raise InvalidAssembly('path.id dupliqué')
            ids.add(pid)
            if path['kind'] != 'affine' or path['from'] not in raw['configurations'] or path['to'] not in raw['configurations']:
                raise InvalidAssembly('path: affine entre configurations déclarées requis')
            if path['from'] == path['to']:
                raise InvalidAssembly('path: extrémités distinctes requises')

    @staticmethod
    def _tube(piece):
        length = _q(piece['length'], 'length')
        di = _q(piece['inner_diameter'], 'inner_diameter')
        de = _q(piece['outer_diameter'], 'outer_diameter') if 'outer_diameter' in piece else di + 2*_q(piece['wall'], 'wall')
        if not 0 < di < de:
            raise InvalidAssembly('tube: 0 < diamètre intérieur < diamètre extérieur requis')
        return length, di, de

    @staticmethod
    def _fixed_segments(piece):
        return [{'kind':s['kind'], 'length_cm':float(_q(s['length'],'length')*100),
                 'd_in_cm':float(_q(s['diameter_in'],'diameter_in')*100),
                 'd_out_cm':float(_q(s['diameter_out'],'diameter_out')*100),
                 'material_id':piece['material_id'], 'profile_params':copy.deepcopy(s.get('profile_params', {}))}
                for s in piece['segments']]

    def _mechanics(self, block, cid):
        inner = self._raw['pieces'][block['inner']]
        outer = self._raw['pieces'][block['outer']]
        li, di, de = self._tube(inner)
        lo, do, doe = self._tube(outer)
        q = _q(self._raw['configurations'][cid]['q'][block['id']], 'q', positive=False)
        overlap = lo-q
        omin = _q(block['min_overlap'], 'min_overlap')
        margins = {'diametral_clearance':do-de, 'minimum_overlap':overlap-omin,
                   'inner_stock':li-overlap, 'outer_stock':lo-overlap, 'q':q}
        if not di < de < do or any(v < 0 for v in margins.values()):
            raise InvalidAssembly(f'{cid}/{block["id"]}: géométrie incompatible; marges SI exactes '+
                                  json.dumps({k:str(v) for k,v in margins.items()}))
        return li, di, de, lo, do, doe, q, overlap, margins

    def certify_paths(self):
        """Exact affine geometry on [0,1]; deliberately no acoustic certificate."""
        self._bom()  # Fixed stock and all tube walls participate in the certificate.
        certificates = []
        for path in self._raw.get('paths', []):
            endpoints = []
            for cid in (path['from'],path['to']):
                checks = {}
                for block in self._raw['blocks']:
                    if block['kind'] == 'telescope':
                        *_, margins = self._mechanics(block,cid)
                        checks[block['id']] = {k:str(v) for k,v in margins.items()}
                endpoints.append({'configuration_id':cid,'margins_m_exact':checks})
            certificates.append({'path_id':path['id'], 'kind':'affine', 'parameter_interval':[0,1],
                'geometry_certified':True,'acoustics_certified':False,
                'scope':'nominal telescope fit and fixed stock; no fabrication tolerance',
                'arithmetic':'exact rational decimal input numbers', 'endpoint_checks':endpoints,
                'reason':'Les dimensions sont constantes et chaque inégalité est affine en q; ses extrêmes sont aux extrémités.'})
        return certificates

    def generate(self, configuration_id):
        try:
            return self._generate(configuration_id)
        except (KeyError, TypeError, OverflowError, RecursionError) as exc:
            raise InvalidAssembly('génération d’assemblage invalide: '+str(exc)) from exc

    def _generate(self, configuration_id):
        if configuration_id not in self._raw['configurations']:
            raise InvalidAssembly(f'configuration absente: {configuration_id}')
        raw = self._raw
        segments, spans, positions = [], [], []
        z = Fraction(0)
        checks = {}
        def append(piece_id, segment_id, native, length):
            nonlocal z
            if length == 0: return
            index = len(segments)
            segments.append(native)
            spans.append({'piece_id':piece_id,'segment_id':segment_id,'native_segment_index':index,
                          'start_m':float(z),'end_m':float(z+length),'exposed_length_m':float(length)})
            z += length
        for block in raw['blocks']:
            if block['kind'] == 'fixed':
                piece = raw['pieces'][block['piece']]
                for s, native in zip(piece['segments'],self._fixed_segments(piece)):
                    append(block['piece'],s['id'],native,_q(s['length'],'length'))
                continue
            li, di, de, lo, do, doe, q, overlap, margins = self._mechanics(block,configuration_id)
            checks[block['id']] = {k:str(v) for k,v in margins.items()}
            start = z
            ordered = [(block['outer'],q,do),(block['inner'],li,di)]
            if block['orientation'] == 'inner_first': ordered.reverse()
            for pid, length, diameter in ordered:
                append(pid,'lumen',{'kind':'cylinder','length_cm':float(length*100),
                    'd_in_cm':float(diameter*100),'d_out_cm':float(diameter*100),
                    'material_id':raw['pieces'][pid]['material_id'],'profile_params':{}},length)
            inner_start = start+q if block['orientation'] == 'outer_first' else start
            outer_start = start if block['orientation'] == 'outer_first' else start+li-overlap
            positions.append({'configuration_id':configuration_id,'block_id':block['id'],
                'q_m':float(q),'overlap_m':float(overlap),'deployed_m':float(li+q),
                'inner_start_m':float(inner_start),'inner_end_m':float(inner_start+li),
                'outer_start_m':float(outer_start),'outer_end_m':float(outer_start+lo),
                'orientation':block['orientation'],'seal':block['seal'],
                'radial_clearance_m':float((do-de)/2)})
        profile_hash = digest(segments)
        design_raw = {'id':raw['id']+'_'+configuration_id,'segments':segments,'metadata':{
            'assembly_schema_version':SCHEMA,'assembly_id':raw['id'],'assembly_sha256':self.sha256,
            'configuration_id':configuration_id,'generated_profile_sha256':profile_hash,
            'component_ids':list(raw['pieces']), 'spans':copy.deepcopy(spans),
            'source_kind':'generated_in_memory', 'physical_stock_is_not_analysis_mesh':True}}
        design = validate_design(design_raw,self.material_db,self.config)
        volume, volume_method = _lumen_volume(design)
        bom = self._bom()
        for row in positions: row['assembly_deployed_m'] = float(z)
        return {'design':design,'bom':bom,'positions':positions,'spans':spans,
                'lumen_volume_m3':volume,'lumen_volume_method':volume_method,
                'deployed_length_m':float(z),'profile_sha256':profile_hash,
                'geometry_certificate':{'configuration_id':configuration_id,'geometry_certified':True,
                    'arithmetic':'exact rational decimal input numbers','margins_m_exact':checks,
                    'scope':'nominal telescope fit; native CONFIG geometry also validated',
                    'mechanical_guidance_seal_wear_certified':False},
                'terminal_radius_m':design.segments[-1].d_out_cm/200}

    def _bom(self):
        result = []
        for pid,piece in self._raw['pieces'].items():
            if piece['kind'] == 'tube':
                length,di,de = self._tube(piece)
                material_volume = math.pi/4*float((de*de-di*di)*length)
                outer_diameter = float(de)
                stock_length = float(length)
            else:
                length = sum((_q(s['length'],'length') for s in piece['segments']),Fraction(0))
                material_volume = None
                stock_length,outer_diameter = float(length),None
                if 'stock' in piece:
                    stock_length_q = _q(piece['stock']['length'],'stock.length')
                    de = _q(piece['stock']['outer_diameter'],'stock.outer_diameter')
                    largest = max(_q(s[k],k) for s in piece['segments'] for k in ('diameter_in','diameter_out'))
                    if stock_length_q < length or de <= largest:
                        raise InvalidAssembly(f'{pid}: stock insuffisant pour le profil fixe entier')
                    native = validate_design({'id':pid,'segments':self._fixed_segments(piece)},self.material_db,{})
                    lumen,_ = _lumen_volume(native)
                    # Stock allowance is retained, not silently cut or bored.
                    # Material volume is only defined when full stock is the part.
                    if stock_length_q == length:
                        material_volume = math.pi/4*float(de*de*length)-lumen
                    stock_length,outer_diameter = float(stock_length_q),float(de)
            if material_volume is not None and not math.isfinite(material_volume):
                raise InvalidAssembly('volume matière non fini')
            # Native Material has no effective density property. A qualitative
            # mass_level/density_class must never manufacture a numeric density.
            result.append({'piece_id':pid,'kind':piece['kind'],'material_id':piece['material_id'],
                'stock_length_m':stock_length,'outer_diameter_m':outer_diameter,
                'inner_diameter_m':float(di) if piece['kind']=='tube' else None,
                'density_source':None,
                'material_volume_m3':material_volume,'density_kg_m3':None,'mass_kg':None,
                'mass_availability':'Native Material ne fournit pas de densité effective documentée.',
                'input_dimensions':copy.deepcopy(piece),'annotations':copy.deepcopy(piece.get('annotations',{}))})
        return result


def _lumen_volume(design):
    """Integrate the existing native diameter profile, never a replacement shape.

    Cylinders/linear profiles use their polynomial integral. Other native profiles
    are integrated by deterministic Simpson quadrature, reported as approximate.
    """
    total = 0.
    approximate = False
    native = GeometryDiscretizer()
    for s in design.segments:
        a,b,length = s.d_in_cm/100,s.d_out_cm/100,s.length_cm/100
        if s.kind in {'cylinder','mouthpiece','cone','flare_conical'} or a == b:
            total += math.pi*length*(a*a+a*b+b*b)/12
        else:
            approximate = True
            n = 256
            values = [(native._diameter_at(s,i/n)/100)**2 for i in range(n+1)]
            integral = (values[0]+values[-1]+4*sum(values[1:-1:2])+2*sum(values[2:-1:2]))/(3*n)
            total += math.pi/4*length*integral
    if not math.isfinite(total): raise InvalidAssembly('volume lumen non fini')
    return total, ('native profile Simpson-256 approximation' if approximate else 'exact polynomial profile integral, rounded binary64')
