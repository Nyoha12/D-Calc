from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from ..acoustics.air import AirProperties


@dataclass(frozen=True, slots=True)
class LipParameters:
    mouth_pressure_kpa: float = 1.5
    resonance_hz: float = 80.0
    q_factor: float = 5.0
    rest_opening_mm: float = 1.2
    width_mm: float = 12.0
    mass_kg: float = 3.0e-4
    damping_ratio: float = 0.12
    spring_bias_mm: float = 0.0
    coupling_pa_per_m: float = 9.0e5
    flow_coefficient: float = 0.72
    self_oscillation_gain: float = 85.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DimensionedLipParameters:
    """Experimental one-mass lip parameters.

    All physical defaults are provisional to_calibrate values from the
    LipModelV2 design spike, not validated player or material coefficients.
    """

    mouth_pressure_kpa: float = 1.5
    resonance_hz: float = 80.0
    effective_area_m2: float = 3.0e-6
    mass_kg: float = 1.0e-4
    rest_opening_m: float = 8.0e-4
    lip_width_m: float = 0.012
    damping_ratio: float = 0.20
    contact_stiffness_n_per_m: float = 1.0e4
    contact_damping_n_s_per_m: float = 0.04
    min_opening_m: float = 1.0e-6
    flow_coefficient: float = 0.72
    pressure_force_sign: float = -1.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class LipModel:
    """Minimal 1-DOF lips model: oscillator + Bernoulli flow with h+ clipping."""

    def __init__(self, params: LipParameters | Mapping[str, Any] | None = None) -> None:
        if params is None:
            self.params = LipParameters()
        elif isinstance(params, LipParameters):
            self.params = params
        else:
            self.params = LipParameters(**dict(params))

    def opening(self, state: Sequence[float] | np.ndarray) -> float:
        return opening(state)

    def flow(
        self,
        state: Sequence[float] | np.ndarray,
        params: LipParameters | Mapping[str, Any] | None,
        p_acoustic_pa: float,
        air: AirProperties,
    ) -> float:
        active_params = self.params if params is None else (params if isinstance(params, LipParameters) else LipParameters(**dict(params)))
        return flow(state, active_params, p_acoustic_pa, air)

    def derivatives(
        self,
        t: float,
        state: Sequence[float] | np.ndarray,
        params: LipParameters | Mapping[str, Any] | None,
        p_acoustic_pa: float,
    ) -> np.ndarray:
        active_params = self.params if params is None else (params if isinstance(params, LipParameters) else LipParameters(**dict(params)))
        return derivatives(t, state, active_params, p_acoustic_pa)

    def energy_features(self, state: Sequence[float] | np.ndarray, params: LipParameters | Mapping[str, Any] | None = None) -> dict[str, float]:
        active_params = self.params if params is None else (params if isinstance(params, LipParameters) else LipParameters(**dict(params)))
        return energy_features(state, active_params)


def opening(state: Sequence[float] | np.ndarray) -> float:
    state_arr = np.asarray(state, dtype=float)
    if state_arr.size < 1:
        return 0.0
    return float(max(state_arr[0], 0.0))


def flow(
    state: Sequence[float] | np.ndarray,
    params: LipParameters,
    p_acoustic_pa: float,
    air: AirProperties,
) -> float:
    h_m = opening(state)
    if h_m <= 0.0:
        return 0.0
    mouth_pressure_pa = 1000.0 * max(float(params.mouth_pressure_kpa), 0.0)
    delta_p = max(mouth_pressure_pa - float(p_acoustic_pa), 0.0)
    width_m = max(float(params.width_mm), 0.1) / 1000.0
    area_m2 = width_m * h_m
    coefficient = max(float(params.flow_coefficient), 0.0)
    return float(coefficient * area_m2 * np.sqrt(2.0 * delta_p / max(float(air.rho), 1e-9)))


def derivatives(
    t: float,
    state: Sequence[float] | np.ndarray,
    params: LipParameters,
    p_acoustic_pa: float,
) -> np.ndarray:
    del t
    state_arr = np.asarray(state, dtype=float)
    if state_arr.size < 2:
        raise ValueError("Lip state must have at least [opening_m, velocity_m_s].")

    x = float(state_arr[0])
    v = float(state_arr[1])
    h0 = max(float(params.rest_opening_mm), 0.0) / 1000.0
    omega = 2.0 * np.pi * max(float(params.resonance_hz), 1e-6)
    zeta = max(float(params.damping_ratio), 1e-6)
    mass = max(float(params.mass_kg), 1e-8)
    coupling = float(params.coupling_pa_per_m)
    spring_bias_m = float(params.spring_bias_mm) / 1000.0
    mouth_pressure_pa = 1000.0 * max(float(params.mouth_pressure_kpa), 0.0)
    delta_p = mouth_pressure_pa - float(p_acoustic_pa)

    restoring = -omega**2 * (x - h0 - spring_bias_m)
    damping = -2.0 * zeta * omega * v
    pressure_drive = -delta_p / coupling
    van_der_pol_gain = max(0.0, float(params.self_oscillation_gain) * (float(params.mouth_pressure_kpa) - 0.8))
    amplitude_scale = max(h0, 2.5e-4)
    self_excitation = van_der_pol_gain * (1.0 - (x / amplitude_scale) ** 2) * v
    nonlinear_limit = -0.15 * omega**2 * ((x - h0) / amplitude_scale) ** 3 * amplitude_scale
    xdot = v
    vdot = restoring + damping + self_excitation + nonlinear_limit + pressure_drive / mass
    return np.asarray([xdot, vdot], dtype=float)


def energy_features(state: Sequence[float] | np.ndarray, params: LipParameters) -> dict[str, float]:
    state_arr = np.asarray(state, dtype=float)
    if state_arr.size < 2:
        return {"opening_m": 0.0, "velocity_m_s": 0.0, "kinetic_energy_j": 0.0, "potential_energy_j": 0.0}
    x = max(float(state_arr[0]), 0.0)
    v = float(state_arr[1])
    omega = 2.0 * np.pi * max(float(params.resonance_hz), 1e-6)
    mass = max(float(params.mass_kg), 1e-8)
    h0 = max(float(params.rest_opening_mm), 0.0) / 1000.0
    return {
        "opening_m": x,
        "velocity_m_s": v,
        "kinetic_energy_j": float(0.5 * mass * v * v),
        "potential_energy_j": float(0.5 * mass * omega**2 * (x - h0) ** 2),
    }


class LipModelV2:
    """Experimental dimensioned 1-DOF lip model.

    Convention: state[0] is displacement, x > 0 opens the lips, and
    opening = rest_opening_m + x. This model is opt-in only.
    """

    def __init__(self, params: DimensionedLipParameters | Mapping[str, Any] | None = None) -> None:
        if params is None:
            self.params = DimensionedLipParameters()
        elif isinstance(params, DimensionedLipParameters):
            self.params = params
        else:
            self.params = DimensionedLipParameters(**dict(params))

    def opening(self, state: Sequence[float] | np.ndarray) -> float:
        return dimensioned_opening(state, self.params)

    def stiffness_n_per_m(self, params: DimensionedLipParameters | Mapping[str, Any] | None = None) -> float:
        active_params = self._active_params(params)
        return dimensioned_stiffness_n_per_m(active_params)

    def damping_n_s_per_m(self, params: DimensionedLipParameters | Mapping[str, Any] | None = None) -> float:
        active_params = self._active_params(params)
        return dimensioned_damping_n_s_per_m(active_params)

    def pressure_force(
        self,
        params: DimensionedLipParameters | Mapping[str, Any] | None,
        p_acoustic_pa: float,
    ) -> float:
        active_params = self._active_params(params)
        return dimensioned_pressure_force(active_params, p_acoustic_pa)

    def contact_force(
        self,
        state: Sequence[float] | np.ndarray,
        params: DimensionedLipParameters | Mapping[str, Any] | None = None,
    ) -> float:
        active_params = self._active_params(params)
        return dimensioned_contact_force(state, active_params)

    def contact_active(
        self,
        state: Sequence[float] | np.ndarray,
        params: DimensionedLipParameters | Mapping[str, Any] | None = None,
    ) -> bool:
        active_params = self._active_params(params)
        return bool(dimensioned_opening(state, active_params) < max(float(active_params.min_opening_m), 0.0))

    def flow(
        self,
        state: Sequence[float] | np.ndarray,
        params: DimensionedLipParameters | Mapping[str, Any] | None,
        p_acoustic_pa: float,
        air: AirProperties,
    ) -> float:
        active_params = self._active_params(params)
        return dimensioned_flow(state, active_params, p_acoustic_pa, air)

    def derivatives(
        self,
        t: float,
        state: Sequence[float] | np.ndarray,
        params: DimensionedLipParameters | Mapping[str, Any] | None,
        p_acoustic_pa: float,
    ) -> np.ndarray:
        active_params = self._active_params(params)
        return dimensioned_derivatives(t, state, active_params, p_acoustic_pa)

    def energy_features(
        self,
        state: Sequence[float] | np.ndarray,
        params: DimensionedLipParameters | Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        active_params = self._active_params(params)
        return dimensioned_energy_features(state, active_params)

    def integration_substeps(
        self,
        dt: float,
        params: DimensionedLipParameters | Mapping[str, Any] | None = None,
    ) -> int:
        active_params = self._active_params(params)
        mass = max(float(active_params.mass_kg), 1e-12)
        stiffness = max(dimensioned_stiffness_n_per_m(active_params), 0.0)
        contact_stiffness = max(float(active_params.contact_stiffness_n_per_m), 0.0)
        omega = np.sqrt((stiffness + contact_stiffness) / mass)
        return max(1, min(48, int(np.ceil(max(float(dt), 0.0) * omega / 0.45))))

    def _active_params(
        self,
        params: DimensionedLipParameters | Mapping[str, Any] | None,
    ) -> DimensionedLipParameters:
        if params is None:
            return self.params
        if isinstance(params, DimensionedLipParameters):
            return params
        return DimensionedLipParameters(**dict(params))


def dimensioned_opening(state: Sequence[float] | np.ndarray, params: DimensionedLipParameters) -> float:
    state_arr = np.asarray(state, dtype=float)
    x = float(state_arr[0]) if state_arr.size >= 1 else 0.0
    return float(max(float(params.rest_opening_m), 0.0) + x)


def dimensioned_stiffness_n_per_m(params: DimensionedLipParameters) -> float:
    mass = max(float(params.mass_kg), 1e-12)
    omega = 2.0 * np.pi * max(float(params.resonance_hz), 1e-6)
    return float(mass * omega * omega)


def dimensioned_damping_n_s_per_m(params: DimensionedLipParameters) -> float:
    mass = max(float(params.mass_kg), 1e-12)
    omega = 2.0 * np.pi * max(float(params.resonance_hz), 1e-6)
    damping_ratio = max(float(params.damping_ratio), 0.0)
    return float(2.0 * damping_ratio * mass * omega)


def dimensioned_pressure_force(params: DimensionedLipParameters, p_acoustic_pa: float) -> float:
    mouth_pressure_pa = 1000.0 * max(float(params.mouth_pressure_kpa), 0.0)
    delta_p = mouth_pressure_pa - float(p_acoustic_pa)
    return float(float(params.pressure_force_sign) * max(float(params.effective_area_m2), 0.0) * delta_p)


def dimensioned_contact_force(
    state: Sequence[float] | np.ndarray,
    params: DimensionedLipParameters,
) -> float:
    state_arr = np.asarray(state, dtype=float)
    v = float(state_arr[1]) if state_arr.size >= 2 else 0.0
    opening_m = dimensioned_opening(state_arr, params)
    min_opening_m = max(float(params.min_opening_m), 0.0)
    penetration = max(min_opening_m - opening_m, 0.0)
    if penetration <= 0.0:
        return 0.0
    contact_stiffness = max(float(params.contact_stiffness_n_per_m), 0.0)
    contact_damping = max(float(params.contact_damping_n_s_per_m), 0.0)
    return float(contact_stiffness * penetration + contact_damping * max(-v, 0.0))


def dimensioned_flow(
    state: Sequence[float] | np.ndarray,
    params: DimensionedLipParameters,
    p_acoustic_pa: float,
    air: AirProperties,
) -> float:
    h_m = max(dimensioned_opening(state, params), 0.0)
    if h_m <= 0.0:
        return 0.0
    mouth_pressure_pa = 1000.0 * max(float(params.mouth_pressure_kpa), 0.0)
    delta_p = max(mouth_pressure_pa - float(p_acoustic_pa), 0.0)
    if delta_p <= 0.0:
        return 0.0
    width_m = max(float(params.lip_width_m), 0.0)
    coefficient = max(float(params.flow_coefficient), 0.0)
    return float(coefficient * width_m * h_m * np.sqrt(2.0 * delta_p / max(float(air.rho), 1e-9)))


def dimensioned_derivatives(
    t: float,
    state: Sequence[float] | np.ndarray,
    params: DimensionedLipParameters,
    p_acoustic_pa: float,
) -> np.ndarray:
    del t
    state_arr = np.asarray(state, dtype=float)
    if state_arr.size < 2:
        raise ValueError("Dimensioned lip state must have at least [displacement_m, velocity_m_s].")

    x = float(state_arr[0])
    v = float(state_arr[1])
    mass = max(float(params.mass_kg), 1e-12)
    stiffness = dimensioned_stiffness_n_per_m(params)
    damping = dimensioned_damping_n_s_per_m(params)
    force = (
        dimensioned_pressure_force(params, p_acoustic_pa)
        + dimensioned_contact_force(state_arr, params)
        - stiffness * x
        - damping * v
    )
    return np.asarray([v, force / mass], dtype=float)


def dimensioned_energy_features(state: Sequence[float] | np.ndarray, params: DimensionedLipParameters) -> dict[str, float]:
    state_arr = np.asarray(state, dtype=float)
    if state_arr.size < 2:
        return {
            "opening_m": max(float(params.rest_opening_m), 0.0),
            "displacement_m": 0.0,
            "velocity_m_s": 0.0,
            "kinetic_energy_j": 0.0,
            "potential_energy_j": 0.0,
        }
    x = float(state_arr[0])
    v = float(state_arr[1])
    mass = max(float(params.mass_kg), 1e-12)
    stiffness = dimensioned_stiffness_n_per_m(params)
    return {
        "opening_m": dimensioned_opening(state_arr, params),
        "displacement_m": x,
        "velocity_m_s": v,
        "kinetic_energy_j": float(0.5 * mass * v * v),
        "potential_energy_j": float(0.5 * stiffness * x * x),
    }
