"""Opt-in SI passive impedance realization; output is the midpoint pressure port.

The bilinear realization certifies this subsystem, not the historical lip coupling.
Model files are strict JSON parameters/provenance only; never executable objects.
"""
from __future__ import annotations

import copy
import hashlib
import json
from numbers import Real
from pathlib import Path

import numpy as np

SCHEMA = 'dcalc.passive_resonator.v1'
UNITS = dict(flow='m^3/s', pressure='Pa', impedance='Pa.s/m^3', a='Pa/m^3',
             gamma='1/s', omega='rad/s', q='m^3.s', v='m^3', energy='J', frequency='Hz')
DOMAINS = {'continuous', 'discrete_prewarped'}
MAX_TERMS = 256


def real(value, name, *, minimum=None):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(name + ': finite real scalar required')
    value = float(value)
    if not np.isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError(name + ': finite scalar outside domain')
    return value


def integer(value, name, lo, hi):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or not lo <= value <= hi:
        raise ValueError(f'{name}: integer in [{lo},{hi}] required')
    return int(value)


def vector(value, name, *, empty=False):
    raw = np.asarray(value, dtype=object)
    if raw.ndim != 1 or (not empty and not raw.size):
        raise ValueError(name + ': real vector required')
    return np.array([real(x, name) for x in raw], dtype=float)


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError('Duplicate JSON key: ' + key)
        out[key] = value
    return out


def _constant(value):
    raise ValueError('Nonfinite JSON constant: ' + value)


def json_copy(value):
    # Reject non-JSON and nonfinite annotations too, before a file is written.
    return json.loads(json.dumps(value, allow_nan=False), object_pairs_hook=_pairs, parse_constant=_constant)


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class PassiveResonator:
    """Immutable coefficients, independent state, midpoint input/output at each step.

    ``continuous`` labels the coefficient interpretation. A prewarped fit's
    continuous response is only its rational approximation, not physical modes.
    """
    def __init__(self, a, gamma, omega, *, R0, sample_rate_hz, dc_origin,
                 domain='continuous', provenance=None, quality=None):
        a, gamma, omega = (vector(x, n, empty=True) for x, n in
                           ((a, 'a'), (gamma, 'gamma'), (omega, 'omega')))
        if not (a.size == gamma.size == omega.size <= MAX_TERMS):
            raise ValueError('Coefficient shape/order mismatch')
        if np.any(a < 0) or np.any(gamma <= 0) or np.any(omega <= 0):
            raise ValueError('Passive coefficients require a>=0, gamma>0, omega>0')
        if len(set(zip(gamma, omega))) != len(a):
            raise ValueError('Duplicate modal poles')
        self._R0 = real(R0, 'R0', minimum=0)
        self._fs = integer(sample_rate_hz, 'sample_rate_hz', 1, 192000)
        if domain not in DOMAINS:
            raise ValueError('Unknown coefficient domain')
        if not isinstance(dc_origin, dict) or set(dc_origin) != {'kind', 'description'} or dc_origin['kind'] not in {'explicit', 'imposed_reference', 'zk_local_1d'} or not isinstance(dc_origin['description'], str) or not dc_origin['description'].strip():
            raise ValueError('Explicit DC origin required')
        self._domain = domain
        self._dc_origin = json_copy(dc_origin)
        self._provenance = json_copy(provenance or {})
        self._quality = json_copy(quality or {})
        if not isinstance(self._provenance, dict) or not isinstance(self._quality, dict):
            raise ValueError('Provenance/quality must be mappings')
        for name, values in (('_a', a), ('_gamma', gamma), ('_omega', omega)):
            values.flags.writeable = False
            setattr(self, name, values)
        self._h = 1/(2*self._fs)
        with np.errstate(over='raise', invalid='raise'):
            try:
                self._w2 = omega**2
                self._den = 1+self._h*gamma+self._h**2*self._w2
                if not np.all(np.isfinite(a*gamma)):
                    raise ValueError('Unrepresentable coefficient products')
            except FloatingPointError as exc:
                raise ValueError('Unrepresentable realization') from exc
        # Explicit pole stability, including floating-point representability.
        for g, w in zip(gamma, omega):
            poles = np.roots([1., g, w*w])
            if not np.all(np.isfinite(poles)) or np.any(poles.real >= 0):
                raise ValueError('Poles not representably strictly stable')
        self._loaded_path = None
        self._loaded_hash = None
        self.reset()

    @property
    def sample_rate_hz(self):
        return self._fs

    @property
    def R0(self):
        return self._R0

    @property
    def a(self):
        return self._a.copy()

    @property
    def gamma(self):
        return self._gamma.copy()

    @property
    def omega(self):
        return self._omega.copy()

    @property
    def state(self):
        return self._q.copy(), self._v.copy()

    @property
    def metadata(self):
        return dict(experimental=True, pressure_port='midpoint', coefficient_domain=self._domain,
                    state_count=2*len(self._a), dc_origin=copy.deepcopy(self._dc_origin),
                    coefficient_meaning='numerical approximation; not material or lip identification')

    def reset(self):
        self._q = np.zeros_like(self._a)
        self._v = np.zeros_like(self._a)
        self.last_dissipation_w = 0.

    def energy(self):
        return float(np.sum(self._a*(self._v**2+self._w2*self._q**2))/2)

    def step(self, u_t):
        u = real(u_t, 'flow')
        with np.errstate(over='raise', invalid='raise'):
            try:
                vm = (self._v-self._h*self._w2*self._q+self._h*u)/self._den
                qn = self._q+2*self._h*vm
                vn = 2*vm-self._v
                p = float(self._R0*u+self._a@vm)
                loss = float(self._R0*u*u+np.sum(self._a*self._gamma*vm**2))
                if not np.isfinite(p+loss) or not np.all(np.isfinite(qn+vn)):
                    raise ValueError('Nonfinite state; step not committed')
            except FloatingPointError as exc:
                raise ValueError('State overflow; step not committed') from exc
        self._q, self._v, self.last_dissipation_w = qn, vn, loss
        return p

    def _response(self, s):
        # Frequency batching bounds allocation even for the full 256-mode inventory.
        out = np.empty(len(s), complex)
        for i in range(0, len(s), 256):
            z = s[i:i+256, None]
            out[i:i+256] = self._R0+np.sum(self._a*z/(z*z+self._gamma*z+self._w2), axis=1)
        if not np.all(np.isfinite(out)):
            raise ValueError('Nonfinite frequency response')
        return out

    def continuous_response(self, frequency_hz):
        f = vector(frequency_hz, 'frequency')
        if np.any(f < 0):
            raise ValueError('Frequency must be nonnegative')
        self.verify_file_unchanged()
        result = self._response(2j*np.pi*f)
        self.verify_file_unchanged()
        return result

    def discrete_response(self, frequency_hz):
        f = vector(frequency_hz, 'frequency')
        if np.any(f < 0) or np.any(f >= self._fs/2):
            raise ValueError('Discrete frequency outside [0, Nyquist)')
        self.verify_file_unchanged()
        result = self._response(2j*self._fs*np.tan(np.pi*f/self._fs))
        self.verify_file_unchanged()
        return result

    def pressure_from_flow(self, flow_signal):
        """Zero-state prescribed-flow response; leaves this instance's state intact."""
        flow = vector(flow_signal, 'flow', empty=True)
        if len(flow) > 192000:
            raise ValueError('Prescribed-flow sample budget exceeded')
        self.verify_file_unchanged()
        other = self.copy()
        response = np.array([other.step(x) for x in flow])
        self.verify_file_unchanged()
        return response

    def impulse_response(self, sample_count):
        """Requested unit-sample impulse experiment; no implicit FIR kernel property."""
        n = integer(sample_count, 'sample_count', 1, 192000)
        flow = np.zeros(n)
        flow[0] = 1.
        return self.pressure_from_flow(flow)

    def parameters(self):
        return dict(schema=SCHEMA, units=UNITS.copy(), pressure_port='midpoint',
                    sample_rate_hz=self._fs, domain=self._domain, R0=self._R0,
                    dc_origin=copy.deepcopy(self._dc_origin), a=self._a.tolist(),
                    gamma=self._gamma.tolist(), omega=self._omega.tolist(),
                    provenance=copy.deepcopy(self._provenance), quality=copy.deepcopy(self._quality))

    @classmethod
    def from_parameters(cls, data):
        keys = {'schema', 'units', 'pressure_port', 'sample_rate_hz', 'domain', 'R0',
                'dc_origin', 'a', 'gamma', 'omega', 'provenance', 'quality'}
        if not isinstance(data, dict) or set(data) != keys or data['schema'] != SCHEMA or data['units'] != UNITS or data['pressure_port'] != 'midpoint':
            raise ValueError('Invalid passive model schema/units/port')
        return cls(**{k: v for k, v in data.items() if k not in {'schema', 'units', 'pressure_port'}})

    def copy(self):
        return self.from_parameters(self.parameters())

    def save(self, path):
        self.verify_file_unchanged()
        payload = self.parameters()
        text = json.dumps(dict(parameters=payload, sha256=digest(payload)), allow_nan=False, indent=2)+'\n'
        with Path(path).open('x', encoding='utf-8') as stream:
            stream.write(text)

    @classmethod
    def load(cls, path, *, expected_sha256=None):
        path = Path(path).resolve(strict=True)
        if not path.is_file() or path.stat().st_size > 2*1024**2:
            raise ValueError('Model must be a regular JSON file <=2 MiB')
        raw = path.read_bytes()
        hashed = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and hashed != expected_sha256:
            raise ValueError('Model file changed since preflight')
        data = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(data, dict) or set(data) != {'parameters', 'sha256'} or data['sha256'] != digest(data['parameters']):
            raise ValueError('Model content fingerprint mismatch')
        model = cls.from_parameters(data['parameters'])
        model._loaded_path, model._loaded_hash = path, hashed
        model.verify_file_unchanged()
        return model

    def verify_file_unchanged(self):
        if self._loaded_path is not None and hashlib.sha256(self._loaded_path.read_bytes()).hexdigest() != self._loaded_hash:
            raise ValueError('Loaded model file changed during processing')
