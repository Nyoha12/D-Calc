from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True, slots=True)
class VocalTractPreset:
    id: str
    center_frequency_hz: float
    bandwidth_hz: float
    control_gain: float
    brightness_bias: float
    impedance_sensitivity: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class VocalTractLibrary:
    def get_preset(self, name: str) -> VocalTractPreset:
        presets = {
            "neutral": VocalTractPreset(
                id="neutral_tract",
                center_frequency_hz=1800.0,
                bandwidth_hz=1400.0,
                control_gain=0.55,
                brightness_bias=0.50,
                impedance_sensitivity=0.50,
            ),
            "tongue_high": VocalTractPreset(
                id="tongue_high",
                center_frequency_hz=2200.0,
                bandwidth_hz=900.0,
                control_gain=0.90,
                brightness_bias=0.85,
                impedance_sensitivity=0.95,
            ),
            "tongue_low": VocalTractPreset(
                id="tongue_low",
                center_frequency_hz=1100.0,
                bandwidth_hz=1200.0,
                control_gain=0.65,
                brightness_bias=0.35,
                impedance_sensitivity=0.55,
            ),
        }
        try:
            return presets[name]
        except KeyError as exc:
            known = ", ".join(sorted(presets))
            raise KeyError(f"Unknown vocal tract preset {name!r}. Known presets: {known}") from exc
