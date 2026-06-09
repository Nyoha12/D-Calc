from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..materials.models import Material, ParameterStatus
from .air import AirProperties


DEFAULT_AIR = AirProperties(rho=1.204, c=343.0, temperature_c=20.0, humidity_percent=50.0)
COMPONENT_V2_EXPERIMENTAL_COMPONENT_NAMES = (
    "air_thermoviscous",
    "wall_damping",
    "surface_roughness",
    "porosity_leak",
    "wall_compliance_reactive",
)
COMPONENT_V2_EXPERIMENTAL_WARNING = (
    "component_v2_experimental is an internal experimental skeleton; "
    "components are inferred/to_calibrate placeholders and are not validation evidence."
)


@dataclass(frozen=True)
class LossComponent:
    name: str
    alpha: np.ndarray
    provenance_status: ParameterStatus
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LossResult:
    alpha_total: np.ndarray
    k_complex: np.ndarray
    zc_complex: np.ndarray
    components: tuple[LossComponent, ...]
    provenance_status: ParameterStatus
    warnings: tuple[str, ...] = ()


class LossModel(Protocol):
    name: str

    def evaluate(
        self,
        omega: float | np.ndarray,
        diameter_m: float,
        material: Material,
        zc_nominal: float | np.ndarray,
        air: AirProperties | None = None,
    ) -> LossResult:
        ...


def effective_beta(material: Material) -> float:
    return float(material.beta.nominal)


def attenuation_alpha(omega: float | np.ndarray, diameter_m: float, material: Material) -> np.ndarray:
    diameter_m = max(float(diameter_m), 1e-9)
    omega_arr = np.asarray(omega, dtype=float)
    beta = effective_beta(material)
    alpha = 1e-5 * beta * np.sqrt(np.maximum(omega_arr, 0.0)) / diameter_m
    alpha_eff = alpha * (1.0 + float(material.wall_loss.nominal) + float(material.porosity_leak.nominal))
    return np.nan_to_num(alpha_eff, nan=0.0, posinf=0.0, neginf=0.0)


def complex_wavenumber(
    omega: float | np.ndarray,
    diameter_m: float,
    material: Material,
    air: AirProperties | None = None,
) -> np.ndarray:
    air = air or DEFAULT_AIR
    omega_arr = np.asarray(omega, dtype=float)
    alpha_eff = attenuation_alpha(omega_arr, diameter_m, material)
    return omega_arr / float(air.c) - 1j * alpha_eff


class LegacyBetaLossModel:
    name = "legacy_beta"

    def alpha_total(self, omega: float | np.ndarray, diameter_m: float, material: Material) -> np.ndarray:
        return attenuation_alpha(omega, diameter_m, material)

    def k_complex(
        self,
        omega: float | np.ndarray,
        diameter_m: float,
        material: Material,
        air: AirProperties | None = None,
    ) -> np.ndarray:
        return complex_wavenumber(omega, diameter_m, material, air)

    def zc_complex(
        self,
        zc_nominal: float | np.ndarray,
        omega: float | np.ndarray,
        diameter_m: float,
        material: Material,
        air: AirProperties | None = None,
    ) -> np.ndarray:
        alpha = self.alpha_total(omega, diameter_m, material)
        return _lossy_characteristic_impedance(zc_nominal, omega, alpha, air)

    def evaluate(
        self,
        omega: float | np.ndarray,
        diameter_m: float,
        material: Material,
        zc_nominal: float | np.ndarray,
        air: AirProperties | None = None,
    ) -> LossResult:
        alpha = self.alpha_total(omega, diameter_m, material)
        provenance_status = _legacy_beta_provenance_status(material)
        component = LossComponent(
            name=self.name,
            alpha=alpha,
            provenance_status=provenance_status,
            notes=("Strict adapter over attenuation_alpha; no material coefficient changes.",),
        )
        return LossResult(
            alpha_total=alpha,
            k_complex=self.k_complex(omega, diameter_m, material, air),
            zc_complex=_lossy_characteristic_impedance(zc_nominal, omega, alpha, air),
            components=(component,),
            provenance_status=provenance_status,
        )


class ComponentV2ExperimentalLossModel:
    """Internal skeleton for a future componentized loss model.

    The model deliberately preserves legacy propagation values while exposing
    placeholder components for calibration work. It is not a public config
    option and must not be treated as a validated physical model.
    """

    name = "component_v2_experimental"
    component_names = COMPONENT_V2_EXPERIMENTAL_COMPONENT_NAMES

    def __init__(self, legacy_model: LegacyBetaLossModel | None = None) -> None:
        self._legacy_model = legacy_model or LegacyBetaLossModel()

    def evaluate(
        self,
        omega: float | np.ndarray,
        diameter_m: float,
        material: Material,
        zc_nominal: float | np.ndarray,
        air: AirProperties | None = None,
    ) -> LossResult:
        alpha = self._legacy_model.alpha_total(omega, diameter_m, material)
        zero_alpha = np.zeros_like(alpha, dtype=float)
        component_warning = (
            "Experimental component placeholder; status is to_calibrate/inferred and no calibration is claimed.",
        )
        components = (
            LossComponent(
                name="air_thermoviscous",
                alpha=zero_alpha.copy(),
                provenance_status="to_calibrate",
                notes=("Stub only; no air thermoviscous formula is implemented.",),
                warnings=component_warning,
            ),
            LossComponent(
                name="wall_damping",
                alpha=alpha,
                provenance_status="inferred",
                notes=(
                    "Legacy beta aggregate passthrough for numerical compatibility; "
                    "not a physical wall damping component split.",
                ),
                warnings=component_warning,
            ),
            LossComponent(
                name="surface_roughness",
                alpha=zero_alpha.copy(),
                provenance_status="to_calibrate",
                notes=("Stub only; no surface roughness formula is implemented.",),
                warnings=component_warning,
            ),
            LossComponent(
                name="porosity_leak",
                alpha=zero_alpha.copy(),
                provenance_status="to_calibrate",
                notes=("Stub only; no component-v2 porosity leak formula is implemented.",),
                warnings=component_warning,
            ),
            LossComponent(
                name="wall_compliance_reactive",
                alpha=zero_alpha.copy(),
                provenance_status="to_calibrate",
                notes=("Reactive placeholder only; not injected into alpha_total.",),
                warnings=component_warning,
            ),
        )
        return LossResult(
            alpha_total=alpha,
            k_complex=self._legacy_model.k_complex(omega, diameter_m, material, air),
            zc_complex=self._legacy_model.zc_complex(zc_nominal, omega, diameter_m, material, air),
            components=components,
            provenance_status="to_calibrate",
            warnings=(COMPONENT_V2_EXPERIMENTAL_WARNING,),
        )


def _lossy_characteristic_impedance(
    zc_nominal: float | np.ndarray,
    omega: float | np.ndarray,
    alpha: float | np.ndarray,
    air: AirProperties | None,
) -> np.ndarray:
    from .transfer_matrix import lossy_characteristic_impedance

    return lossy_characteristic_impedance(zc_nominal, omega, alpha, air)


def _legacy_beta_provenance_status(material: Material) -> ParameterStatus:
    statuses = (str(material.beta.status), str(material.wall_loss.status), str(material.porosity_leak.status))
    if any(status not in {"sourced", "inferred", "to_calibrate"} for status in statuses):
        return "to_calibrate"
    if any(status == "to_calibrate" for status in statuses):
        return "to_calibrate"
    if any(status != "sourced" for status in statuses):
        return "inferred"
    return "sourced"
