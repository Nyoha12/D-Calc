from .database import MaterialDatabase
from .models import AcousticParameter, Material, MaterialVariant
from .uncertainty import MaterialUncertaintyManager
from .variants import MaterialVariantGenerator


def build_variant(material: Material, rules_path: str, **variant_kwargs) -> Material:
    """Convenience helper for one-shot wood variant generation."""
    generator = MaterialVariantGenerator.from_yaml(rules_path)
    return generator.generate_variant(material, **variant_kwargs)


def sample_material_parameters(material: Material, n_samples: int, rng_seed: int | None = None):
    """Sample uncertain material parameters around their nominal values."""
    manager = MaterialUncertaintyManager(rng_seed=rng_seed)
    return manager.sample_parameters(material, n_samples)


def rank_material_calibration_targets(material: Material, sensitivities):
    """Compute calibration priority scores for material parameters."""
    manager = MaterialUncertaintyManager()
    ranking = manager.calibration_priority_score(material, sensitivities)
    ranked = sorted(
        ranking["per_parameter"].items(),
        key=lambda item: item[1]["priority_score"],
        reverse=True,
    )
    return ranked


__all__ = [
    "AcousticParameter",
    "Material",
    "MaterialVariant",
    "MaterialDatabase",
    "MaterialVariantGenerator",
    "MaterialUncertaintyManager",
    "build_variant",
    "sample_material_parameters",
    "rank_material_calibration_targets",
]
