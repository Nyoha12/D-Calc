from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from .models import AcousticParameter, Material, MaterialVariant


class MaterialVariantGenerator:
    def __init__(self, rules: dict[str, Any]):
        self.rules = rules
        self.policy = dict(rules.get("generation_policy", {}))

    @classmethod
    def from_yaml(cls, path: str | Path) -> "MaterialVariantGenerator":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data)

    def generate_variant(
        self,
        base_material: Material,
        *,
        humidity_state: str | None = None,
        finish: str | None = None,
        grade: str | None = None,
        knot_class: str | None = None,
        density_class: str | None = None,
    ) -> Material:
        resolved_variant = self._resolve_variant(
            base_material,
            humidity_state=humidity_state,
            finish=finish,
            grade=grade,
            knot_class=knot_class,
            density_class=density_class,
        )
        validation = self.validate_variant(base_material, resolved_variant)
        if validation["errors"]:
            raise ValueError("; ".join(validation["errors"]))

        if base_material.family not in self.policy.get("generate_variants_for_families", []):
            return replace(base_material, variant=resolved_variant)

        beta_factor, porosity_factor, wall_loss_factor, uncertainty_factor = self._combined_factors(resolved_variant)
        notes = base_material.notes
        if validation["warnings"]:
            notes = f"{notes} | variant_warnings: {'; '.join(validation['warnings'])}" if notes else "; ".join(validation["warnings"])

        return replace(
            base_material,
            id=self._variant_material_id(base_material, resolved_variant),
            variant=resolved_variant,
            beta=self.scale_parameter(base_material.beta, factor=beta_factor, widen_factor=uncertainty_factor),
            porosity_leak=self.scale_parameter(
                base_material.porosity_leak,
                factor=porosity_factor,
                widen_factor=uncertainty_factor,
            ),
            wall_loss=self.scale_parameter(
                base_material.wall_loss,
                factor=wall_loss_factor,
                widen_factor=uncertainty_factor,
            ),
            notes=notes,
        )

    def apply_modifiers(self, material: Material, variant: MaterialVariant) -> Material:
        return self.generate_variant(
            material,
            humidity_state=variant.humidity_state,
            finish=variant.finish,
            grade=variant.grade,
            knot_class=variant.knot_class,
            density_class=variant.density_class,
        )

    def validate_variant(self, base_material: Material, variant: MaterialVariant) -> dict[str, list[str]]:
        errors: list[str] = []
        warnings: list[str] = []

        if base_material.family not in self.policy.get("generate_variants_for_families", []):
            if not variant.is_empty():
                errors.append(
                    f"Material {base_material.id!r} is in family {base_material.family!r}; wood variants are not applicable."
                )
            return {"errors": errors, "warnings": warnings}

        allowed_humidity = set(self.rules.get("wood_humidity_states", {}).keys())
        allowed_finish = set(self.rules.get("wood_finishes", {}).keys())
        allowed_grade = set(self.rules.get("wood_grade", {}).keys())
        allowed_density = set(self.rules.get("density_class", {}).keys())

        if variant.humidity_state and variant.humidity_state not in allowed_humidity:
            errors.append(f"Unknown humidity_state={variant.humidity_state!r}")
        if variant.finish and variant.finish not in allowed_finish:
            errors.append(f"Unknown finish={variant.finish!r}")
        if variant.grade and variant.grade not in allowed_grade:
            errors.append(f"Unknown grade={variant.grade!r}")
        if variant.density_class and variant.density_class not in allowed_density:
            errors.append(f"Unknown density_class={variant.density_class!r}")

        active_values = {
            value
            for value in [variant.humidity_state, variant.finish, variant.grade, variant.knot_class, variant.density_class]
            if value is not None
        }
        for combination in self.policy.get("forbidden_combinations", []):
            if set(combination).issubset(active_values):
                errors.append(f"Forbidden wood variant combination: {combination}")
        for combination in self.policy.get("discouraged_combinations", []):
            if set(combination).issubset(active_values):
                warnings.append(f"Discouraged wood variant combination: {combination}")

        return {"errors": errors, "warnings": warnings}

    def scale_parameter(
        self,
        param: AcousticParameter,
        factor: float,
        widen_factor: float = 1.0,
    ) -> AcousticParameter:
        nominal = max(0.0, param.nominal * factor)
        if param.nominal <= 0.0:
            return AcousticParameter(
                nominal=nominal,
                min=max(0.0, param.min * factor),
                max=max(0.0, param.max * factor),
                status=param.status,
                confidence=param.confidence,
            )

        lower_rel = max(0.0, (param.nominal - param.min) / param.nominal)
        upper_rel = max(0.0, (param.max - param.nominal) / param.nominal)
        min_value = max(0.0, nominal * (1.0 - lower_rel * widen_factor))
        max_value = max(nominal, nominal * (1.0 + upper_rel * widen_factor))
        return AcousticParameter(
            nominal=nominal,
            min=min_value,
            max=max_value,
            status=param.status,
            confidence=param.confidence,
        )

    def _resolve_variant(
        self,
        base_material: Material,
        *,
        humidity_state: str | None,
        finish: str | None,
        grade: str | None,
        knot_class: str | None,
        density_class: str | None,
    ) -> MaterialVariant:
        defaults = base_material.variant.as_dict() if base_material.variant else {}
        return MaterialVariant(
            humidity_state=humidity_state or defaults.get("humidity_state") or self.policy.get("default_humidity"),
            finish=finish or defaults.get("finish") or self.policy.get("default_finish"),
            grade=grade or defaults.get("grade") or self.policy.get("default_grade"),
            knot_class=knot_class or defaults.get("knot_class"),
            density_class=density_class or defaults.get("density_class") or self.policy.get("default_density_class"),
        )

    def _combined_factors(self, variant: MaterialVariant) -> tuple[float, float, float, float]:
        humidity = self.rules.get("wood_humidity_states", {}).get(variant.humidity_state or "", {})
        finish = self.rules.get("wood_finishes", {}).get(variant.finish or "", {})
        grade = self.rules.get("wood_grade", {}).get(variant.grade or "", {})

        beta_factor = humidity.get("beta_factor", 1.0) * finish.get("beta_factor", 1.0) * grade.get("beta_factor", 1.0)
        porosity_factor = (
            humidity.get("porosity_factor", 1.0)
            * finish.get("porosity_factor", 1.0)
            * grade.get("porosity_factor", 1.0)
        )
        wall_loss_factor = (
            humidity.get("wall_loss_factor", 1.0)
            * finish.get("wall_loss_factor", 1.0)
            * grade.get("wall_loss_factor", 1.0)
        )
        uncertainty_factor = grade.get("uncertainty_factor", 1.0)
        return beta_factor, porosity_factor, wall_loss_factor, uncertainty_factor

    def _variant_material_id(self, material: Material, variant: MaterialVariant) -> str:
        parts = [material.base_material]
        for value in [variant.humidity_state, variant.finish, variant.grade, variant.density_class]:
            if value:
                parts.append(value)
        return "__".join(parts)
