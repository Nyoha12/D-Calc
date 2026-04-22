from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

SegmentKind = Literal[
    "mouthpiece",
    "cylinder",
    "cone",
    "flare_conical",
    "flare_exponential",
    "flare_powerlaw",
    "branch",
    "helmholtz_neck",
]

SUPPORTED_SEGMENT_KINDS: tuple[str, ...] = (
    "mouthpiece",
    "cylinder",
    "cone",
    "flare_conical",
    "flare_exponential",
    "flare_powerlaw",
    "branch",
    "helmholtz_neck",
)

BELL_KINDS: tuple[str, ...] = (
    "flare_conical",
    "flare_exponential",
    "flare_powerlaw",
)


@dataclass(slots=True)
class Segment:
    kind: SegmentKind
    length_cm: float
    d_in_cm: float
    d_out_cm: float
    material_id: str
    profile_params: dict[str, Any] = field(default_factory=dict)
    position_start_cm: float = 0.0
    position_end_cm: float = 0.0

    def __post_init__(self) -> None:
        if self.kind not in SUPPORTED_SEGMENT_KINDS:
            raise ValueError(
                f"Unsupported segment kind '{self.kind}'. Supported kinds: {SUPPORTED_SEGMENT_KINDS}."
            )
        if self.length_cm <= 0.0:
            raise ValueError(f"Segment length must be > 0 cm, got {self.length_cm}.")
        if self.d_in_cm <= 0.0 or self.d_out_cm <= 0.0:
            raise ValueError(
                "Segment diameters must be > 0 cm, "
                f"got d_in={self.d_in_cm}, d_out={self.d_out_cm}."
            )
        if not self.material_id:
            raise ValueError("Segment material_id must be non-empty.")
        if self.position_end_cm and self.position_end_cm < self.position_start_cm:
            raise ValueError(
                f"Segment end position {self.position_end_cm} cm precedes start {self.position_start_cm} cm."
            )
        self.length_cm = float(self.length_cm)
        self.d_in_cm = float(self.d_in_cm)
        self.d_out_cm = float(self.d_out_cm)
        self.position_start_cm = float(self.position_start_cm)
        self.position_end_cm = float(self.position_end_cm)
        self.profile_params = dict(self.profile_params or {})

    @property
    def average_diameter_cm(self) -> float:
        return 0.5 * (self.d_in_cm + self.d_out_cm)

    @property
    def radius_in_cm(self) -> float:
        return 0.5 * self.d_in_cm

    @property
    def radius_out_cm(self) -> float:
        return 0.5 * self.d_out_cm

    @property
    def is_uniform(self) -> bool:
        return abs(self.d_out_cm - self.d_in_cm) <= 1e-12

    @property
    def is_bell(self) -> bool:
        return self.kind in BELL_KINDS

    def with_positions(self, start_cm: float, end_cm: float) -> "Segment":
        return replace(self, position_start_cm=float(start_cm), position_end_cm=float(end_cm))

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "length_cm": self.length_cm,
            "d_in_cm": self.d_in_cm,
            "d_out_cm": self.d_out_cm,
            "material_id": self.material_id,
            "profile_params": dict(self.profile_params),
            "position_start_cm": self.position_start_cm,
            "position_end_cm": self.position_end_cm,
        }


@dataclass(slots=True)
class Design:
    id: str
    segments: list[Segment]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Design id must be non-empty.")
        if not isinstance(self.segments, list):
            raise TypeError("Design.segments must be a list of Segment objects.")
        for idx, segment in enumerate(self.segments):
            if not isinstance(segment, Segment):
                raise TypeError(f"Design.segments[{idx}] is not a Segment: {type(segment)!r}")
        self.metadata = dict(self.metadata or {})

    @property
    def total_length_cm(self) -> float:
        return sum(segment.length_cm for segment in self.segments)

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def material_ids(self) -> list[str]:
        return [segment.material_id for segment in self.segments]

    def copy(self, *, segments: list[Segment] | None = None, metadata: dict[str, Any] | None = None) -> "Design":
        return Design(
            id=self.id,
            segments=list(self.segments if segments is None else segments),
            metadata=dict(self.metadata if metadata is None else metadata),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "segments": [segment.as_dict() for segment in self.segments],
            "metadata": dict(self.metadata),
        }
