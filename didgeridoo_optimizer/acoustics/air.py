from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AirProperties:
    rho: float
    c: float
    temperature_c: float = 20.0
    humidity_percent: float = 50.0

    def __post_init__(self) -> None:
        if self.rho <= 0.0:
            raise ValueError(f"Air density must be > 0, got {self.rho}.")
        if self.c <= 0.0:
            raise ValueError(f"Sound speed must be > 0, got {self.c}.")

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "AirProperties":
        environment = dict((config or {}).get("environment", {}) or {})
        return cls(
            rho=float(environment.get("air_density_kg_m3", 1.204)),
            c=float(environment.get("sound_speed_m_s", 343.0)),
            temperature_c=float(environment.get("air_temperature_c", 20.0)),
            humidity_percent=float(environment.get("relative_humidity_percent", 50.0)),
        )
