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
