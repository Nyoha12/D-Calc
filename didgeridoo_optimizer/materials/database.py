from __future__ import annotations

import copy
from pathlib import Path
from typing import Iterable, Mapping, Any

import yaml

from .models import Material
from .variants import MaterialVariantGenerator


class MaterialDatabase:
    REQUIRED_TOP_LEVEL_FIELDS = {"id", "family", "subtype", "practical", "acoustic_model"}
    REQUIRED_PARAMETER_FIELDS = {
        "beta_nominal",
        "beta_min",
        "beta_max",
        "beta_status",
        "porosity_leak_nominal",
        "porosity_leak_min",
        "porosity_leak_max",
        "porosity_status",
        "wall_loss_nominal",
        "wall_loss_min",
        "wall_loss_max",
        "wall_loss_status",
    }

    def __init__(self, raw: dict, *, variant_generator: MaterialVariantGenerator | None = None, source_path: str | Path | None = None):
        self.raw = raw
        self.source_path = Path(source_path) if source_path is not None else None
        self.variant_generator = variant_generator
        materials_raw = raw.get("materials", [])
        self._validate_raw_materials(materials_raw)
        self.materials = {entry["id"]: Material.from_dict(entry) for entry in materials_raw}

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        variant_rules_path: str | Path | None = None,
        auto_load_variant_rules: bool = True,
    ) -> "MaterialDatabase":
        source_path = Path(path)
        with open(source_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        generator = None
        candidate_paths: list[Path] = []
        if variant_rules_path is not None:
            candidate_paths.append(Path(variant_rules_path))
        elif auto_load_variant_rules:
            candidate_paths.append(source_path.with_name('wood_variant_rules_v1.yaml'))
            candidate_paths.append(Path('/mnt/data/wood_variant_rules_v1.yaml'))

        for candidate in candidate_paths:
            if candidate.exists():
                generator = MaterialVariantGenerator.from_yaml(candidate)
                break

        return cls(data, variant_generator=generator, source_path=source_path)

    def get(self, material_id: str) -> Material:
        try:
            return self.materials[material_id]
        except KeyError:
            generated = self._try_generate_variant(material_id)
            if generated is not None:
                self.materials[material_id] = generated
                return generated
            available = ", ".join(sorted(self.materials.keys())[:10])
            raise KeyError(f"Unknown material_id={material_id!r}. Known examples: {available}")

    def list_ids(self) -> list[str]:
        return sorted(self.materials.keys())

    def filter_allowed(self, ids: Iterable[str]) -> list[Material]:
        return [self.get(material_id) for material_id in ids]

    def clone_with_patch(self, patch: Mapping[str, Any] | None) -> "MaterialDatabase":
        raw = copy.deepcopy(self.raw)
        patch_materials = dict((patch or {}).get("materials", {}) or {})
        if not patch_materials:
            return MaterialDatabase(raw, variant_generator=self.variant_generator, source_path=self.source_path)

        for entry in raw.get("materials", []):
            material_id = str(entry.get("id"))
            updates = dict(patch_materials.get(material_id, {}) or {})
            if not updates:
                continue
            acoustic = entry.setdefault("acoustic_model", {})
            for key, value in updates.items():
                if key.endswith(("_nominal", "_min", "_max")):
                    acoustic[key] = float(value)
        return MaterialDatabase(raw, variant_generator=self.variant_generator, source_path=self.source_path)

    def _try_generate_variant(self, material_id: str) -> Material | None:
        if self.variant_generator is None or "__" not in material_id:
            return None
        parts = material_id.split("__")
        if len(parts) < 2:
            return None
        base_id = parts[0]
        base_material = self.materials.get(base_id)
        if base_material is None:
            return None

        humidity_state = parts[1] if len(parts) > 1 else None
        finish = parts[2] if len(parts) > 2 else None
        grade = parts[3] if len(parts) > 3 else None
        density_class = parts[4] if len(parts) > 4 else None
        return self.variant_generator.generate_variant(
            base_material,
            humidity_state=humidity_state,
            finish=finish,
            grade=grade,
            density_class=density_class,
        )

    def _validate_raw_materials(self, materials_raw: list[dict]) -> None:
        seen_ids: set[str] = set()
        for index, entry in enumerate(materials_raw):
            material_label = entry.get("id", f"index:{index}")
            missing = self.REQUIRED_TOP_LEVEL_FIELDS - set(entry.keys())
            if missing:
                raise ValueError(f"Material {material_label!r} missing fields: {sorted(missing)}")

            material_id = str(entry["id"])
            if material_id in seen_ids:
                raise ValueError(f"Duplicate material id detected: {material_id}")
            seen_ids.add(material_id)

            acoustic = entry.get("acoustic_model", {})
            acoustic_missing = self.REQUIRED_PARAMETER_FIELDS - set(acoustic.keys())
            if acoustic_missing:
                raise ValueError(f"Material {material_id!r} missing acoustic fields: {sorted(acoustic_missing)}")

            for prefix in ("beta", "porosity_leak", "wall_loss"):
                min_v = float(acoustic[f"{prefix}_min"])
                nominal_v = float(acoustic[f"{prefix}_nominal"])
                max_v = float(acoustic[f"{prefix}_max"])
                if not (min_v <= nominal_v <= max_v):
                    raise ValueError(
                        f"Material {material_id!r} has incoherent range for {prefix}: {min_v} <= {nominal_v} <= {max_v}"
                    )

            recommended = entry.get("practical", {}).get("recommended_for", {})
            for scope in ("mouthpiece", "body", "bell"):
                if scope not in recommended:
                    raise ValueError(f"Material {material_id!r} missing practical.recommended_for.{scope}")
                if not isinstance(recommended[scope], bool):
                    raise TypeError(
                        f"Material {material_id!r} field practical.recommended_for.{scope} must be a boolean"
                    )
