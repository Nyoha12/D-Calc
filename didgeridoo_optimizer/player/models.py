from __future__ import annotations

from dataclasses import dataclass, asdict
import random
from typing import Any


@dataclass(frozen=True, slots=True)
class PlayerProfile:
    id: str
    skill_level: str
    mouth_pressure_kpa: float
    pressure_variability: float
    embouchure_stability: float
    tract_control: float
    preferred_q: float
    q_tolerance: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlayerProfileSampler:
    def __init__(self, rng_seed: int | None = None) -> None:
        self._rng = random.Random(rng_seed)

    def beginner_preset(self) -> PlayerProfile:
        return PlayerProfile(
            id="beginner_profile",
            skill_level="beginner",
            mouth_pressure_kpa=1.1,
            pressure_variability=0.28,
            embouchure_stability=0.45,
            tract_control=0.35,
            preferred_q=8.0,
            q_tolerance=5.0,
        )

    def expert_preset(self) -> PlayerProfile:
        return PlayerProfile(
            id="expert_profile",
            skill_level="expert",
            mouth_pressure_kpa=2.2,
            pressure_variability=0.12,
            embouchure_stability=0.85,
            tract_control=0.85,
            preferred_q=14.0,
            q_tolerance=9.0,
        )

    def sample_beginner(self, n: int) -> list[PlayerProfile]:
        return [
            PlayerProfile(
                id=f"beginner_{idx}",
                skill_level="beginner",
                mouth_pressure_kpa=self._rng.uniform(0.8, 1.5),
                pressure_variability=self._rng.uniform(0.20, 0.35),
                embouchure_stability=self._rng.uniform(0.30, 0.55),
                tract_control=self._rng.uniform(0.20, 0.45),
                preferred_q=self._rng.uniform(6.0, 10.0),
                q_tolerance=self._rng.uniform(3.0, 6.0),
            )
            for idx in range(max(0, n))
        ]

    def sample_expert(self, n: int) -> list[PlayerProfile]:
        return [
            PlayerProfile(
                id=f"expert_{idx}",
                skill_level="expert",
                mouth_pressure_kpa=self._rng.uniform(1.6, 3.0),
                pressure_variability=self._rng.uniform(0.05, 0.18),
                embouchure_stability=self._rng.uniform(0.70, 0.95),
                tract_control=self._rng.uniform(0.65, 0.95),
                preferred_q=self._rng.uniform(10.0, 18.0),
                q_tolerance=self._rng.uniform(6.0, 10.0),
            )
            for idx in range(max(0, n))
        ]
