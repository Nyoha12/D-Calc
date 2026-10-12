"""Explicit energy-normalized real passive monoport; no fit or modal conversion.

The supplied numerical realization is structurally passive. Strict asymptotic
stability and floating-point representability are separate admission checks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import sys
from types import FunctionType
from pathlib import Path

_LOADED_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest()
_LOADED_SOURCE_STAT = _LOADED_SOURCE_PATH.stat()
_BLAS1_AT_IMPORT = all(os.environ.get(k) == '1' for k in
    ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'))

import numpy as np

from .passive_resonator import real, integer, digest, DOMAINS

SCHEMA = 'dcalc.passive_state.v1'
STATE_SCHEMA = 'dcalc.passive_state.state.v1'
MAX_STATES = 128
MAX_FREQUENCIES = 4096
UNITS = dict(state='m^3/s', flow='m^3/s', pressure='Pa', energy='J',
             omega_ref='rad/s', Zscale='Pa.s/m^3', frequency='Hz',
             J='1', g='1', L='1')



STATE_PORT_SOURCES = (
    'didgeridoo_optimizer/nonlinear/passive_state.py',
    'didgeridoo_optimizer/pipeline/state_port_reference.py',
    'didgeridoo_optimizer/reporting/state_port_reference.py',
    'tools/state_port_reference.py',
)


def verify_loaded_sources():
    """Relative integrity of each module's own import snapshot, as in R56.

    Never import an absent producer to check it. Referenced bytes alone do not
    establish loaded-code identity, nor authenticate a wholesale replacement.
    This is a workflow boundary check, not a per-sample hash of the codebase.
    """
    root = _LOADED_SOURCE_PATH.parents[2]
    hashes, roles = {}, {}
    for name in STATE_PORT_SOURCES:
        path = root/name
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[name] = current
        modules = [sys.modules.get(name[:-3].replace('/', '.'))]
        main = sys.modules.get('__main__')
        if getattr(main, '__file__', None) and Path(main.__file__).resolve() == path:
            modules.append(main)
        roles[name] = 'referenced'
        for module in modules:
            if module is None:
                continue
            if (Path(module.__file__).resolve() != path
                    or getattr(module, '_LOADED_SOURCE_PATH', None) != path
                    or getattr(module, '_LOADED_SOURCE_SHA256', None) != current):
                raise ValueError('Loaded source changed since import: '+name)
            for value in tuple(vars(module).values()):
                if isinstance(value, FunctionType) and value.__module__ == module.__name__:
                    if Path(value.__code__.co_filename).resolve() != path:
                        raise ValueError('Loaded function source path mismatch: '+name)
            roles[name] = 'loaded_snapshot_verified'
    return hashes, roles


def verify_model_source():
    # Fast per-operation identity guard; full workflow manifests are checked
    # only at their boundaries. Never refresh the import fingerprint from disk.
    current = _LOADED_SOURCE_PATH.stat()
    signature = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if (signature(current) != signature(_LOADED_SOURCE_STAT)
            and hashlib.sha256(_LOADED_SOURCE_PATH.read_bytes()).hexdigest() != _LOADED_SOURCE_SHA256):
        raise ValueError('Loaded model source changed since import')


def bounded_json(value, depth=0, nodes=None):
    """Bound plain data before copying, hashing or allocating dense arrays."""
    nodes = [0] if nodes is None else nodes
    nodes[0] += 1
    if depth > 12 or nodes[0] > 60000:
        raise ValueError('JSON structure budget exceeded')
    if type(value) is dict:
        if len(value) > 128 or any(type(k) is not str or len(k) > 128 for k in value):
            raise ValueError('JSON mapping budget exceeded')
        for item in value.values():
            bounded_json(item, depth+1, nodes)
    elif type(value) is list:
        if len(value) > 4096:
            raise ValueError('JSON sequence budget exceeded')
        for item in value:
            bounded_json(item, depth+1, nodes)
    elif type(value) is str:
        if len(value) > 4096:
            raise ValueError('JSON text budget exceeded')
    elif value is not None and type(value) not in (bool, int, float):
        raise ValueError('Plain JSON data required')
    elif type(value) in (int, float):
        real(value, 'JSON scalar')


def bounded_vector(value, name, limit, *, size=None):
    if type(value) not in (list, tuple, np.ndarray):
        raise ValueError(name+': bounded vector required')
    if type(value) is np.ndarray and (value.ndim != 1 or value.dtype.kind not in 'fiu'):
        raise ValueError(name+': real vector required')
    if len(value) > limit or (size is not None and len(value) != size):
        raise ValueError(name+': vector shape/budget mismatch')
    values = [real(v, name) for v in value]
    return np.array(values, dtype=float)


def _matrix(value, name, rows, cols=None):
    if type(value) not in (list, tuple, np.ndarray):
        raise ValueError(name+': bounded matrix required')
    if type(value) is np.ndarray and (value.ndim != 2 or value.dtype.kind not in 'fiu'):
        raise ValueError(name+': real matrix required')
    if len(value) != rows or rows > MAX_STATES+1:
        raise ValueError(name+': matrix row budget/shape mismatch')
    if cols is None:
        if type(value[0]) not in (list, tuple, np.ndarray):
            raise ValueError(name+': matrix row required')
        cols = len(value[0])
    if not 0 <= cols <= MAX_STATES+1:
        raise ValueError(name+': matrix column budget exceeded')
    # Validate every scalar and row before dense conversion.
    checked = [bounded_vector(row, name, MAX_STATES+1, size=cols).tolist() for row in value]
    return np.array(checked, dtype=float).reshape(rows, cols)


def _frozen(array):
    # Immutable backing bytes: setflags(write=True) cannot undo this guard.
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


def validate_parameter_data(data):
    """Strict representation checks only, reusable by offline artifact readers."""
    bounded_json(data)
    keys = {'schema','units','pressure_port','sample_rate_hz','domain','J','g','L',
            'omega_ref','Zscale','coefficient_origin','provenance'}
    if (type(data) is not dict or set(data)!=keys or data['schema']!=SCHEMA
            or data['units']!=UNITS or data['pressure_port']!='midpoint'):
        raise ValueError('Invalid passive state schema/units/port')
    g=bounded_vector(data['g'],'g',MAX_STATES)
    J=_matrix(data['J'],'J',len(g),len(g))
    _matrix(data['L'],'L',len(g)+1)
    if not np.array_equal(J,-J.T):raise ValueError('J must be explicitly antisymmetric')
    for key in ('omega_ref','Zscale'):
        if real(data[key],key,minimum=0.)==0:raise ValueError('Strictly positive scales required')
    integer(data['sample_rate_hz'],'sample_rate_hz',1,192000)
    if type(data['domain']) is not str or data['domain'] not in DOMAINS:raise ValueError('Invalid domain')
    if type(data['coefficient_origin']) is not str or not data['coefficient_origin'].strip():
        raise ValueError('Coefficient origin required')
    if type(data['provenance']) is not dict:raise ValueError('Provenance mapping required')
    return {k:v for k,v in data.items() if k not in {'schema','units','pressure_port'}}


class PassiveState:
    """Immutable coefficients and independent flat state, initialized at rest.

    ``copy()`` starts at rest like PassiveResonator; ``copy(with_state=True)``
    also copies the current state/loss. The native rate is never resampled.
    """
    def __setattr__(self, name, value):
        if getattr(self, '_sealed', False) and name not in {'_x', '_last_loss'}:
            raise AttributeError('PassiveState coefficients are immutable')
        object.__setattr__(self, name, value)

    def __init__(self, J, g, L, *, omega_ref, Zscale, sample_rate_hz,
                 coefficient_origin, domain='continuous', provenance=None):
        verify_model_source()
        g = bounded_vector(g, 'g', MAX_STATES)
        n = len(g)
        J, L = _matrix(J, 'J', n, n), _matrix(L, 'L', n+1)
        if not np.array_equal(J, -J.T):
            raise ValueError('J must be explicitly exactly antisymmetric; no symmetrization')
        self._omega_ref = real(omega_ref, 'omega_ref', minimum=0.)
        self._Zscale = real(Zscale, 'Zscale', minimum=0.)
        if self._omega_ref == 0 or self._Zscale == 0:
            raise ValueError('Strictly positive scales required')
        self._fs = integer(sample_rate_hz, 'sample_rate_hz', 1, 192000)
        if type(domain) is not str or domain not in DOMAINS:
            raise ValueError('Unknown coefficient domain')
        if type(coefficient_origin) is not str or not coefficient_origin.strip() or len(coefficient_origin)>4096:
            raise ValueError('Explicit numerical coefficient_origin required')
        provenance = {} if provenance is None else provenance
        bounded_json(provenance)
        if type(provenance) is not dict:
            raise ValueError('Provenance mapping required')
        self._origin, self._domain = coefficient_origin, domain
        self._provenance_text = json.dumps(provenance, allow_nan=False, sort_keys=True)
        self._J, self._g, self._L = map(_frozen, (J, g, L))
        try:
            with np.errstate(over='raise', invalid='raise', divide='raise', under='raise'):
                W = L@L.T
                A, B, C, D = J-W[:n, :n], g-W[:n, n], g+W[:n, n], float(W[n, n])
                rate = self._omega_ref*A
                poles = np.linalg.eigvals(rate)
                guard = 64*np.finfo(float).eps*float(np.linalg.norm(rate, ord=np.inf)) if n else 0.
                if not np.all(np.isfinite(poles)) or np.any(poles.real >= -guard):
                    raise ValueError('Asymptotic stability not representably established')
                tau = 1/(2*self._fs)
                M = np.linalg.solve(np.eye(n)-tau*rate, np.eye(n))
                gain = M@(tau*self._omega_ref*B)
                ph = self._Zscale*(C@M)
                Dh = float(self._Zscale*(D+C@gain))
                dc = float(self._Zscale*(D-C@np.linalg.solve(A, B))) if n else self._Zscale*D
                energy_scale = self._Zscale/self._omega_ref
                transition = 2*M-np.eye(n)
                if np.any(abs(np.linalg.eigvals(transition)) >= 1):
                    raise ValueError('Discrete asymptotic stability not representable')
                if energy_scale <= 0 or Dh < 0 or dc < 0:
                    raise ValueError('Unrepresentable energy/port/DC')
                for v in (W, A, B, C, D, M, gain, ph, Dh, dc, energy_scale, self._Zscale*D):
                    if not np.all(np.isfinite(v)):
                        raise ValueError('Unrepresentable realization')
        except (FloatingPointError, np.linalg.LinAlgError, OverflowError) as exc:
            raise ValueError('Unrepresentable realization') from exc
        self._A, self._B, self._C, self._M, self._gain, self._ph = map(_frozen, (A, B, C, M, gain, ph))
        self._D, self._Dh, self._dc, self._energy_scale = D, Dh, dc, energy_scale
        self._stability_guard = guard
        self._loaded_path = self._loaded_hash = None
        self._sealed = True
        self.reset()

    @property
    def sample_rate_hz(self): return self._fs
    @property
    def state_count(self): return len(self._g)
    @property
    def state(self): return self._x.copy()
    @property
    def last_dissipation_w(self): return self._last_loss
    @property
    def J(self): return self._J.copy()
    @property
    def g(self): return self._g.copy()
    @property
    def L(self): return self._L.copy()
    @property
    def A(self): return self._A.copy()
    @property
    def B(self): return self._B.copy()
    @property
    def C(self): return self._C.copy()
    @property
    def D(self): return self._D
    @property
    def omega_ref(self): return self._omega_ref
    @property
    def Zscale(self): return self._Zscale
    @property
    def feedthrough_pa_s_m3(self): return self._Zscale*self._D
    @property
    def dc_pa_s_m3(self): return self._dc
    @property
    def metadata(self):
        return dict(schema=SCHEMA, state_layout='flat_x', state_count=self.state_count,
            pressure_port='midpoint', coefficient_domain=self._domain,
            passivity='structural_L_LT', asymptotic_stability='strict_numerical_check',
            stability_roundoff_guard=self._stability_guard,
            feedthrough=dict(value=self.feedthrough_pa_s_m3, origin='Zscale * (L L^T)[n,n]'),
            dc=dict(value=self._dc, origin='Zscale * (D - C solve(A,B))' if self.state_count else 'Zscale * D'),
            coefficient_origin=self._origin, physical_validation='not_validated')

    def energy_of(self, state):
        x = bounded_vector(state, 'state', MAX_STATES, size=self.state_count)
        with np.errstate(over='raise', invalid='raise', under='raise'):
            try:
                energy = float((self._energy_scale*x)@x/2)
                if not np.isfinite(energy): raise ValueError('Unrepresentable energy')
            except FloatingPointError as exc:
                raise ValueError('Unrepresentable energy') from exc
        return energy

    def energy(self): return self.energy_of(self._x)

    def initialize(self, state, *, last_dissipation_w=0.):
        self.verify_file_unchanged()
        x = bounded_vector(state, 'state', MAX_STATES, size=self.state_count)
        loss = real(last_dissipation_w, 'last_dissipation_w', minimum=0.)
        self.energy_of(x)
        self.verify_file_unchanged()
        self._x, self._last_loss = x, loss

    def reset(self):
        self.initialize(np.zeros(self.state_count))

    def affine_port(self):
        self.verify_file_unchanged()
        with np.errstate(over='raise', invalid='raise', under='raise'):
            try: ph = float(self._ph@self._x)
            except FloatingPointError as exc: raise ValueError('Unrepresentable affine port') from exc
        if not np.isfinite(ph): raise ValueError('Unrepresentable affine port')
        return ph, self._Dh

    def step(self, flow):
        self.verify_file_unchanged()
        u = real(flow, 'flow')
        try:
            with np.errstate(over='raise', invalid='raise', divide='raise', under='raise'):
                xm = self._M@self._x+self._gain*u
                xn = 2*xm-self._x
                p = float(self._Zscale*(self._C@xm+self._D*u))
                dissipative = self._L.T@np.r_[xm, u]
                loss = float(self._Zscale*(dissipative@dissipative))
                e0, e1 = self.energy(), self.energy_of(xn)
                work, lost = p*u/self._fs, loss/self._fs
                defect = e1-e0-work+lost
                scale = max(e0, e1, abs(work), lost)
                # Compare the dimensionless residual without forming a subnormal
                # tolerance. Exact zero energy/work/loss has exact zero defect.
                if (not np.all(np.isfinite([p, loss, work, lost, defect]))
                        or (scale > 0. and abs(defect)/scale > 2e-10)):
                    raise ValueError('Unrepresentable step or energy balance')
        except FloatingPointError as exc:
            raise ValueError('Step overflow; state unchanged') from exc
        self.verify_file_unchanged()
        self._x, self._last_loss = xn, loss
        return p

    def _response(self, s):
        out = np.empty(len(s), complex)
        try:
            with np.errstate(over='raise', invalid='raise', divide='raise', under='raise'):
                for i, z in enumerate(s):
                    out[i] = self._Zscale*(self._D+self._C@np.linalg.solve(
                        (z/self._omega_ref)*np.eye(self.state_count)-self._A, self._B))
        except (FloatingPointError, np.linalg.LinAlgError) as exc:
            raise ValueError('Unrepresentable frequency response') from exc
        if not np.all(np.isfinite(out)): raise ValueError('Nonfinite frequency response')
        return out

    def continuous_response(self, frequency_hz):
        f = bounded_vector(frequency_hz, 'frequency', MAX_FREQUENCIES)
        if np.any(f < 0): raise ValueError('Frequency must be nonnegative')
        self.verify_file_unchanged()
        with np.errstate(over='raise', invalid='raise', under='raise'):
            try: out = self._response(2j*np.pi*f)
            except FloatingPointError as exc: raise ValueError('Frequency overflow') from exc
        self.verify_file_unchanged()
        return out

    def discrete_response(self, frequency_hz):
        f = bounded_vector(frequency_hz, 'frequency', MAX_FREQUENCIES)
        if np.any(f < 0) or np.any(f >= self._fs/2):
            raise ValueError('Discrete frequency outside [0, Nyquist)')
        self.verify_file_unchanged()
        out = self._response(2j*self._fs*np.tan(np.pi*f/self._fs))
        self.verify_file_unchanged()
        return out

    def parameters(self):
        return dict(schema=SCHEMA, units=UNITS.copy(), pressure_port='midpoint',
            sample_rate_hz=self._fs, domain=self._domain, J=self._J.tolist(),
            g=self._g.tolist(), L=self._L.tolist(), omega_ref=self._omega_ref,
            Zscale=self._Zscale, coefficient_origin=self._origin,
            provenance=json.loads(self._provenance_text))

    @classmethod
    def from_parameters(cls, data):
        return cls(**validate_parameter_data(data))

    def copy(self, *, with_state=False):
        self.verify_file_unchanged()
        other = copy.copy(self)
        other._x = self._x.copy() if with_state else np.zeros(self.state_count)
        other._last_loss = self._last_loss if with_state else 0.
        return other

    def export_state(self):
        self.verify_file_unchanged()
        p = dict(schema=STATE_SCHEMA, model_sha256=digest(self.parameters()),
                 x=self.state.tolist(), last_dissipation_w=self._last_loss)
        return dict(payload=p, sha256=digest(p))

    def import_state(self, data):
        bounded_json(data)
        if type(data) is not dict or set(data)!={'payload','sha256'}:
            raise ValueError('State envelope mismatch')
        p = data['payload']
        if (type(p) is not dict or set(p)!={'schema','model_sha256','x','last_dissipation_w'}
                or p['schema']!=STATE_SCHEMA or p['model_sha256']!=digest(self.parameters())
                or data['sha256']!=digest(p)):
            raise ValueError('State identity/checksum mismatch')
        self.initialize(p['x'], last_dissipation_w=p['last_dissipation_w'])

    def save(self, path):
        self.verify_file_unchanged()
        p = self.parameters()
        text = json.dumps(dict(parameters=p, sha256=digest(p)), allow_nan=False, indent=2)+'\n'
        with Path(path).open('x', encoding='utf8') as stream:
            stream.write(text)

    @classmethod
    def load(cls, path, *, expected_sha256=None):
        from ..reporting.regime_reference import read_json_source
        data, source = read_json_source(path)
        if expected_sha256 is not None and source['sha256']!=expected_sha256:
            raise ValueError('Model file changed since preflight')
        bounded_json(data)
        if (type(data) is not dict or set(data)!={'parameters','sha256'}
                or data['sha256']!=digest(data['parameters'])):
            raise ValueError('Model content fingerprint mismatch')
        model = cls.from_parameters(data['parameters'])
        object.__setattr__(model, '_loaded_path', Path(source['path']))
        object.__setattr__(model, '_loaded_hash', source['sha256'])
        model.verify_file_unchanged()
        return model

    def verify_file_unchanged(self):
        verify_model_source()
        if self._loaded_path is not None:
            from ..reporting.regime_reference import safe_path, MAX_JSON_BYTES
            path = safe_path(self._loaded_path)
            fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_JSON_BYTES:
                    raise ValueError('Loaded model file outside budget')
                raw = stream.read(MAX_JSON_BYTES+1)
                after = os.fstat(stream.fileno())
            current = safe_path(path).stat()
            signature = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
            if (len(raw)>MAX_JSON_BYTES or signature(before)!=signature(after)
                    or signature(after)!=signature(current)
                    or hashlib.sha256(raw).hexdigest()!=self._loaded_hash):
                raise ValueError('Loaded model file changed during processing')
