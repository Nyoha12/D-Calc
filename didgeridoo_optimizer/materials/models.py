from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ParameterStatus = Literal["sourced", "inferred", "to_calibrate"]
ConfidenceLevel = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class AcousticParameter:
    nominal: float
    min: float
    max: float
    status: ParameterStatus
    confidence: ConfidenceLevel

    def __post_init__(self) -> None:
        if self.min > self.nominal or self.nominal > self.max:
            raise ValueError(
                f"Invalid parameter range: expected min <= nominal <= max, got {self.min} <= {self.nominal} <= {self.max}"
            )
        if self.min < 0.0 or self.nominal < 0.0 or self.max < 0.0:
            raise ValueError("Acoustic parameters must be non-negative.")

    @property
    def span(self) -> float:
        return self.max - self.min

    @property
    def relative_uncertainty(self) -> float:
        if self.nominal <= 0.0:
            return 0.0
        return self.span / (2.0 * self.nominal)

    def as_dict(self) -> dict[str, Any]:
        return {
            "nominal": self.nominal,
            "min": self.min,
            "max": self.max,
            "status": self.status,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class MaterialVariant:
    humidity_state: str | None = None
    finish: str | None = None
    grade: str | None = None
    knot_class: str | None = None
    density_class: str | None = None

    def is_empty(self) -> bool:
        return not any(
            [
                self.humidity_state,
                self.finish,
                self.grade,
                self.knot_class,
                self.density_class,
            ]
        )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "humidity_state": self.humidity_state,
            "finish": self.finish,
            "grade": self.grade,
            "knot_class": self.knot_class,
            "density_class": self.density_class,
        }


@dataclass(frozen=True)
class Material:
    id: str
    base_material: str
    family: str
    subtype: str
    variant: MaterialVariant | None

    beta: AcousticParameter
    porosity_leak: AcousticParameter
    wall_loss: AcousticParameter

    manufacturability: str
    cost_level: str
    mass_level: str

    recommended_for_mouthpiece: bool
    recommended_for_body: bool
    recommended_for_bell: bool

    source_status: dict[str, str] = field(default_factory=dict)
    confidence_overall: ConfidenceLevel = "low"
    research_priority: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Material":
        acoustic = data.get("acoustic_model", {})
        confidence = data.get("confidence", {})
        practical = data.get("practical", {})
        recommended = practical.get("recommended_for", {})

        status_field_by_prefix = {"beta": "beta_status", "porosity_leak": "porosity_status", "wall_loss": "wall_loss_status"}

        def build_parameter(prefix: str) -> AcousticParameter:
            confidence_key = "porosity_leak" if prefix == "porosity_leak" else prefix
            return AcousticParameter(
                nominal=float(acoustic[f"{prefix}_nominal"]),
                min=float(acoustic[f"{prefix}_min"]),
                max=float(acoustic[f"{prefix}_max"]),
                status=str(acoustic[status_field_by_prefix[prefix]]),
                confidence=str(confidence.get(confidence_key, confidence.get("overall", "low"))),
            )

        variant_data = data.get("generated_variant") or data.get("variant_defaults")
        variant = MaterialVariant(**variant_data) if variant_data else None

        return cls(
            id=str(data["id"]),
            base_material=str(data.get("base_material", data["id"])),
            family=str(data["family"]),
            subtype=str(data["subtype"]),
            variant=variant,
            beta=build_parameter("beta"),
            porosity_leak=build_parameter("porosity_leak"),
            wall_loss=build_parameter("wall_loss"),
            manufacturability=str(practical["manufacturability"]),
            cost_level=str(practical["cost_level"]),
            mass_level=str(practical["mass_level"]),
            recommended_for_mouthpiece=bool(recommended["mouthpiece"]),
            recommended_for_body=bool(recommended["body"]),
            recommended_for_bell=bool(recommended["bell"]),
            source_status=dict(data.get("source_status", {})),
            confidence_overall=str(confidence.get("overall", "low")),
            research_priority=dict(data.get("research_priority", {})),
            notes=str(data.get("notes", "")),
            raw=dict(data),
        )

    def as_dict(self) -> dict[str, Any]:
        generated_variant = self.variant.as_dict() if self.variant and not self.variant.is_empty() else None
        data = {
            "id": self.id,
            "base_material": self.base_material,
            "family": self.family,
            "subtype": self.subtype,
            "practical": {
                "manufacturability": self.manufacturability,
                "cost_level": self.cost_level,
                "mass_level": self.mass_level,
                "recommended_for": {
                    "mouthpiece": self.recommended_for_mouthpiece,
                    "body": self.recommended_for_body,
                    "bell": self.recommended_for_bell,
                },
            },
            "acoustic_model": {
                "beta_nominal": self.beta.nominal,
                "beta_min": self.beta.min,
                "beta_max": self.beta.max,
                "beta_status": self.beta.status,
                "porosity_leak_nominal": self.porosity_leak.nominal,
                "porosity_leak_min": self.porosity_leak.min,
                "porosity_leak_max": self.porosity_leak.max,
                "porosity_status": self.porosity_leak.status,
                "wall_loss_nominal": self.wall_loss.nominal,
                "wall_loss_min": self.wall_loss.min,
                "wall_loss_max": self.wall_loss.max,
                "wall_loss_status": self.wall_loss.status,
            },
            "confidence": {
                "overall": self.confidence_overall,
                "beta": self.beta.confidence,
                "porosity_leak": self.porosity_leak.confidence,
                "wall_loss": self.wall_loss.confidence,
            },
            "research_priority": dict(self.research_priority),
            "source_status": dict(self.source_status),
            "notes": self.notes,
        }
        if generated_variant:
            data["generated_variant"] = generated_variant
        return data

    def parameter_map(self) -> dict[str, AcousticParameter]:
        return {
            "beta": self.beta,
            "porosity_leak": self.porosity_leak,
            "wall_loss": self.wall_loss,
        }
