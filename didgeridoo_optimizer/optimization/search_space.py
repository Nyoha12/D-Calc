from __future__ import annotations

import copy
import random
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..geometry import DesignBuilder, GeometryValidator
from ..materials import MaterialDatabase


class SearchSpace:
    TOPOLOGIES: tuple[str, ...] = (
        "cylinder_only",
        "cylinder_plus_bell",
        "truncated_cone",
        "multisegment_body_plus_optional_bell",
    )

    def __init__(
        self,
        config: Mapping[str, Any],
        materials: MaterialDatabase | str | Path,
        rng_seed: int | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.material_db = materials if isinstance(materials, MaterialDatabase) else MaterialDatabase.from_yaml(materials)
        self.rng = random.Random(rng_seed if rng_seed is not None else int(dict(self.config.get("project", {}) or {}).get("random_seed", 0) or 0))
        self.builder = DesignBuilder()
        self.validator = GeometryValidator()

        geometry_cfg = dict(self.config.get("geometry_constraints", {}) or {})
        body_cfg = dict(geometry_cfg.get("body_segments", {}) or {})
        diam_cfg = dict(geometry_cfg.get("diameter_cm", {}) or {})
        bell_cfg = dict(dict(self.config.get("bell", {}) or {}).get("geometry_constraints", {}) or {})
        self.total_length_bounds = (float(dict(geometry_cfg.get("total_length_cm", {}) or {}).get("min", 80.0)), float(dict(geometry_cfg.get("total_length_cm", {}) or {}).get("max", 250.0)))
        self.body_count_bounds = (max(1, int(body_cfg.get("min_count", 1))), max(1, int(body_cfg.get("max_count", 12))))
        self.body_length_bounds = (float(body_cfg.get("min_length_cm", 2.0)), float(body_cfg.get("max_length_cm", 120.0)))
        self.diameter_bounds = (float(diam_cfg.get("min", 0.5)), float(diam_cfg.get("max", 50.0)))
        self.diameter_step = float(diam_cfg.get("step", 0.1) or 0.1)
        self.allow_steps = bool(geometry_cfg.get("allow_steps", True))
        self.allow_bell = bool(dict(self.config.get("topology", {}) or {}).get("allow_bell", True))
        self.allowed_bell_types = {str(v) for v in dict(self.config.get("topology", {}) or {}).get("allow_bell_types", ["conical", "exponential", "powerlaw"])}
        self.bell_length_bounds = (float(dict(bell_cfg.get("length_cm", {}) or {}).get("min", 0.0)), float(dict(bell_cfg.get("length_cm", {}) or {}).get("max", 80.0)))
        self.bell_exit_bounds = (float(dict(bell_cfg.get("exit_diameter_cm", {}) or {}).get("min", 1.0)), float(dict(bell_cfg.get("exit_diameter_cm", {}) or {}).get("max", 50.0)))
        self.bell_flare_bounds = (float(dict(bell_cfg.get("flare_parameter", {}) or {}).get("min", 0.01)), float(dict(bell_cfg.get("flare_parameter", {}) or {}).get("max", 20.0)))
        materials_cfg = dict(self.config.get("materials", {}) or {})
        self.max_distinct_materials = max(1, int(materials_cfg.get("max_distinct_materials_per_design", 1)))
        self.allowed_material_ids = self._resolve_allowed_material_ids(materials_cfg)
        self.allowed_body_material_ids = [mid for mid in self.allowed_material_ids if self.material_db.get(mid).recommended_for_body]
        self.allowed_bell_material_ids = [mid for mid in self.allowed_material_ids if self.material_db.get(mid).recommended_for_bell]
        if not self.allowed_body_material_ids:
            self.allowed_body_material_ids = list(self.allowed_material_ids)
        if not self.allowed_bell_material_ids:
            self.allowed_bell_material_ids = list(self.allowed_material_ids)

    def sample_random(self) -> dict[str, Any]:
        topology = self.rng.choice(self._allowed_topologies())
        genome = self._sample_topology(topology)
        return self.repair_genome(genome)

    def mutate(self, genome: Mapping[str, Any]) -> dict[str, Any]:
        mutated = copy.deepcopy(dict(genome))
        topology = str(mutated.get("topology", "cylinder_only"))
        bodies = list(mutated.get("body_segments", []) or [])
        if not bodies:
            return self.sample_random()

        action = self.rng.choice([
            "length_segment",
            "diameter_segment",
            "toggle_shape",
            "bell",
            "material",
            "split_merge",
        ])

        if action == "length_segment":
            idx = self.rng.randrange(len(bodies))
            factor = self.rng.uniform(0.8, 1.2)
            bodies[idx]["length_cm"] = float(bodies[idx].get("length_cm", 10.0)) * factor
        elif action == "diameter_segment":
            idx = self.rng.randrange(len(bodies))
            delta = self.rng.choice([-0.3, -0.2, -0.1, 0.1, 0.2, 0.3])
            if self.rng.random() < 0.5:
                bodies[idx]["d_in_cm"] = float(bodies[idx].get("d_in_cm", 3.8)) + delta
            else:
                bodies[idx]["d_out_cm"] = float(bodies[idx].get("d_out_cm", 3.8)) + delta
        elif action == "toggle_shape":
            idx = self.rng.randrange(len(bodies))
            current_kind = str(bodies[idx].get("kind", "cylinder"))
            bodies[idx]["kind"] = "cone" if current_kind == "cylinder" else "cylinder"
        elif action == "bell":
            mutated = self._mutate_bell(mutated)
        elif action == "material":
            idx = self.rng.randrange(len(bodies))
            bodies[idx]["material_id"] = self.rng.choice(self.allowed_body_material_ids)
        elif action == "split_merge":
            if topology == "multisegment_body_plus_optional_bell" and len(bodies) < min(6, self.body_count_bounds[1]) and self.rng.random() < 0.6:
                idx = self.rng.randrange(len(bodies))
                bodies = self._split_segment(bodies, idx)
            elif len(bodies) > self.body_count_bounds[0]:
                idx = self.rng.randrange(len(bodies) - 1)
                bodies = self._merge_segments(bodies, idx)
        mutated["body_segments"] = bodies
        return self.repair_genome(mutated)

    def crossover(self, genome_a: Mapping[str, Any], genome_b: Mapping[str, Any]) -> dict[str, Any]:
        a = copy.deepcopy(dict(genome_a))
        b = copy.deepcopy(dict(genome_b))
        topology = self.rng.choice([str(a.get("topology", "cylinder_only")), str(b.get("topology", "cylinder_only"))])
        child = {"id": self._candidate_id(), "topology": topology, "metadata": {"origin": "crossover"}}

        body_a = list(a.get("body_segments", []) or [])
        body_b = list(b.get("body_segments", []) or [])
        if topology in {"cylinder_only", "cylinder_plus_bell", "truncated_cone"}:
            base = copy.deepcopy(self.rng.choice([body_a, body_b])[:1] or [self._sample_body_segment(kind="cylinder")])
            child["body_segments"] = base
        else:
            split_a = self.rng.randint(1, max(1, len(body_a)))
            split_b = self.rng.randint(0, max(0, len(body_b) - 1)) if body_b else 0
            body = copy.deepcopy(body_a[:split_a]) + copy.deepcopy(body_b[split_b:])
            child["body_segments"] = body or copy.deepcopy(body_a or body_b or [self._sample_body_segment(kind="cylinder")])

        bell_source = self.rng.choice([a.get("bell"), b.get("bell")])
        if bell_source is not None:
            child["bell"] = copy.deepcopy(bell_source)
        return self.repair_genome(child)

    def decode(self, genome: Mapping[str, Any]) -> dict[str, Any]:
        repaired = self.repair_genome(genome)
        decoded = self.decode_unchecked(repaired)
        decoded["metadata"] = {
            **dict(decoded.get("metadata", {}) or {}),
            "topology": str(repaired.get("topology", "cylinder_only")),
            "source": "search_space_decode",
        }
        return decoded

    def repair_genome(self, genome: Mapping[str, Any]) -> dict[str, Any]:
        repaired = copy.deepcopy(dict(genome))
        topology = str(repaired.get("topology", "cylinder_only"))
        if topology not in self.TOPOLOGIES:
            topology = "cylinder_only"
        if topology in {"cylinder_plus_bell", "multisegment_body_plus_optional_bell"} and not self.allow_bell:
            topology = "cylinder_only" if topology == "cylinder_plus_bell" else "multisegment_body_plus_optional_bell"
        repaired["topology"] = topology
        repaired["id"] = str(repaired.get("id", self._candidate_id()))
        repaired["metadata"] = dict(repaired.get("metadata", {}) or {})

        bodies = [copy.deepcopy(seg) for seg in repaired.get("body_segments", []) if isinstance(seg, Mapping)]
        if not bodies:
            bodies = [self._sample_body_segment(kind="cone" if topology == "truncated_cone" else "cylinder")]

        if topology in {"cylinder_only", "cylinder_plus_bell", "truncated_cone"}:
            bodies = bodies[:1]
            body_kind = "cone" if topology == "truncated_cone" else "cylinder"
            bodies[0]["kind"] = body_kind
        else:
            target_n = min(max(len(bodies), 2), min(6, self.body_count_bounds[1]))
            bodies = bodies[:target_n]
            while len(bodies) < target_n:
                bodies.append(self._sample_body_segment(kind=self.rng.choice(["cylinder", "cone"])))

        bodies = self._repair_body_chain(bodies)
        repaired["body_segments"] = bodies

        if topology in {"cylinder_plus_bell", "multisegment_body_plus_optional_bell"} and self.allow_bell:
            bell = repaired.get("bell")
            repaired["bell"] = self._repair_bell(bell, bodies[-1])
        else:
            repaired["bell"] = None

        design = self.decode_unchecked(repaired)
        built = self.builder.build(design)
        total_length = built.total_length_cm
        min_total, max_total = self.total_length_bounds
        if total_length < min_total or total_length > max_total:
            target = min(max(total_length, min_total), max_total)
            scale = target / max(total_length, 1e-9)
            repaired = self._scale_lengths(repaired, scale)
        return repaired

    def is_valid_genome(self, genome: Mapping[str, Any]) -> bool:
        try:
            design = self.decode(genome)
            built = self.builder.build(design)
            return not self.validator.validate(built, self.config)
        except Exception:
            return False

    def decode_unchecked(self, genome: Mapping[str, Any]) -> dict[str, Any]:
        topology = str(genome.get("topology", "cylinder_only"))
        segments = [copy.deepcopy(seg) for seg in genome.get("body_segments", [])]
        if topology in {"cylinder_plus_bell", "multisegment_body_plus_optional_bell"} and genome.get("bell"):
            segments.append(copy.deepcopy(dict(genome["bell"])))
        return {"id": str(genome.get("id", self._candidate_id())), "segments": segments, "metadata": dict(genome.get("metadata", {}) or {})}

    def _allowed_topologies(self) -> list[str]:
        topologies = ["cylinder_only", "truncated_cone", "multisegment_body_plus_optional_bell"]
        if self.allow_bell:
            topologies.append("cylinder_plus_bell")
        return topologies

    def _sample_topology(self, topology: str) -> dict[str, Any]:
        if topology == "cylinder_only":
            body_segments = [self._sample_body_segment(kind="cylinder")]
            bell = None
        elif topology == "cylinder_plus_bell":
            body_segments = [self._sample_body_segment(kind="cylinder")]
            bell = self._sample_bell(body_segments[-1])
        elif topology == "truncated_cone":
            body_segments = [self._sample_body_segment(kind="cone")]
            bell = None
        else:
            n_segments = self.rng.randint(max(2, self.body_count_bounds[0]), min(5, self.body_count_bounds[1]))
            body_segments = [self._sample_body_segment(kind=self.rng.choice(["cylinder", "cone"])) for _ in range(n_segments)]
            bell = self._sample_bell(body_segments[-1]) if self.allow_bell and self.rng.random() < 0.6 else None

        return self.repair_genome(
            {
                "id": self._candidate_id(),
                "topology": topology,
                "body_segments": body_segments,
                "bell": bell,
                "metadata": {"origin": "random_sample"},
            }
        )

    def _repair_body_chain(self, bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        repaired: list[dict[str, Any]] = []
        distinct_materials: list[str] = []
        current_diameter = self._snap(self.rng.uniform(2.5, 5.5))
        target_total = self.rng.uniform(*self.total_length_bounds)
        raw_lengths = [max(self.body_length_bounds[0], float(seg.get("length_cm", self.rng.uniform(10.0, 60.0)))) for seg in bodies]
        raw_sum = sum(raw_lengths)
        scaled_lengths = [self._clamp(length * target_total / max(raw_sum, 1e-9), *self.body_length_bounds) for length in raw_lengths]

        for idx, seg in enumerate(bodies):
            kind = str(seg.get("kind", "cylinder"))
            if kind not in {"cylinder", "cone"}:
                kind = "cylinder"
            material_id = str(seg.get("material_id") or self.rng.choice(self.allowed_body_material_ids))
            if material_id not in self.allowed_body_material_ids:
                material_id = self.rng.choice(self.allowed_body_material_ids)
            if material_id not in distinct_materials:
                distinct_materials.append(material_id)
            if len(distinct_materials) > self.max_distinct_materials:
                material_id = distinct_materials[0]

            d_in = self._snap(float(seg.get("d_in_cm", current_diameter)))
            if idx == 0:
                d_in = self._clamp(d_in, 2.5, 5.5)
            else:
                previous_d_out = repaired[-1]["d_out_cm"]
                if not self.allow_steps:
                    d_in = previous_d_out
                else:
                    d_in = self._clamp(0.5 * (d_in + previous_d_out), *self.diameter_bounds)
            if kind == "cylinder":
                d_out = self._snap(float(seg.get("d_out_cm", d_in)))
                if not self.allow_steps and idx > 0:
                    d_out = d_in
            else:
                default_out = max(d_in + self.rng.uniform(0.4, 2.0), 3.5)
                d_out = self._snap(float(seg.get("d_out_cm", default_out)))
            d_in = self._clamp(d_in, *self.diameter_bounds)
            d_out = self._clamp(d_out, *self.diameter_bounds)
            if kind == "cylinder":
                d_out = d_in if abs(d_out - d_in) < self.diameter_step else self._clamp(d_out, d_in - 2.0, d_in + 2.0)
            else:
                d_out = self._clamp(d_out, 3.5, 10.0)
            repaired.append(
                {
                    "kind": kind,
                    "length_cm": self._snap_length(scaled_lengths[idx]),
                    "d_in_cm": self._snap(d_in),
                    "d_out_cm": self._snap(d_out),
                    "material_id": material_id,
                    "profile_params": dict(seg.get("profile_params", {}) or {}),
                }
            )
            current_diameter = repaired[-1]["d_out_cm"]
        return repaired

    def _repair_bell(self, bell: Mapping[str, Any] | None, last_body_segment: Mapping[str, Any]) -> dict[str, Any] | None:
        if not self.allow_bell:
            return None
        if bell is None:
            return self._sample_bell(last_body_segment)
        kind = str(bell.get("kind", "flare_conical"))
        allowed_map = {
            "conical": "flare_conical",
            "exponential": "flare_exponential",
            "powerlaw": "flare_powerlaw",
        }
        allowed_segment_kinds = {allowed_map[item] for item in self.allowed_bell_types if item in allowed_map}
        if kind not in allowed_segment_kinds:
            kind = next(iter(sorted(allowed_segment_kinds or {"flare_conical"})))
        throat = self._snap(float(last_body_segment["d_out_cm"]))
        exit_d = self._snap(self._clamp(float(bell.get("d_out_cm", self.rng.uniform(6.0, 20.0))), max(throat, 6.0), min(20.0, self.bell_exit_bounds[1])))
        length = self._snap_length(self._clamp(float(bell.get("length_cm", self.rng.uniform(6.0, 30.0))), *self.bell_length_bounds))
        material_id = str(bell.get("material_id") or self.rng.choice(self.allowed_bell_material_ids))
        if material_id not in self.allowed_bell_material_ids:
            material_id = self.rng.choice(self.allowed_bell_material_ids)
        profile_params = dict(bell.get("profile_params", {}) or {})
        if kind == "flare_exponential":
            profile_params["flare_parameter"] = self._clamp(float(profile_params.get("flare_parameter", self.rng.uniform(1.0, 5.0))), *self.bell_flare_bounds)
        elif kind == "flare_powerlaw":
            profile_params["power"] = self._clamp(float(profile_params.get("power", self.rng.uniform(1.2, 3.0))), *self.bell_flare_bounds)
        return {
            "kind": kind,
            "length_cm": length,
            "d_in_cm": throat,
            "d_out_cm": exit_d,
            "material_id": material_id,
            "profile_params": profile_params,
        }

    def _mutate_bell(self, genome: dict[str, Any]) -> dict[str, Any]:
        topology = str(genome.get("topology", "cylinder_only"))
        if topology not in {"cylinder_plus_bell", "multisegment_body_plus_optional_bell"}:
            if self.allow_bell and self.rng.random() < 0.5:
                genome["topology"] = "cylinder_plus_bell" if topology == "cylinder_only" else "multisegment_body_plus_optional_bell"
            return genome
        if genome.get("bell") is None or self.rng.random() < 0.25:
            genome["bell"] = self._sample_bell((genome.get("body_segments") or [self._sample_body_segment(kind="cylinder")])[-1])
            return genome
        if self.rng.random() < 0.2:
            genome["bell"] = None
            if topology == "cylinder_plus_bell":
                genome["topology"] = "cylinder_only"
            return genome
        bell = dict(genome.get("bell") or {})
        mutate_field = self.rng.choice(["length_cm", "d_out_cm", "kind", "material_id", "flare"])
        if mutate_field == "length_cm":
            bell["length_cm"] = float(bell.get("length_cm", 15.0)) * self.rng.uniform(0.8, 1.25)
        elif mutate_field == "d_out_cm":
            bell["d_out_cm"] = float(bell.get("d_out_cm", 10.0)) + self.rng.choice([-0.5, -0.2, 0.2, 0.5, 1.0])
        elif mutate_field == "kind":
            kind_map = {"conical": "flare_conical", "exponential": "flare_exponential", "powerlaw": "flare_powerlaw"}
            allowed = [kind_map[item] for item in self.allowed_bell_types if item in kind_map]
            bell["kind"] = self.rng.choice(allowed or ["flare_conical"])
        elif mutate_field == "material_id":
            bell["material_id"] = self.rng.choice(self.allowed_bell_material_ids)
        else:
            profile = dict(bell.get("profile_params", {}) or {})
            profile["flare_parameter"] = float(profile.get("flare_parameter", 2.5)) * self.rng.uniform(0.8, 1.2)
            bell["profile_params"] = profile
        genome["bell"] = bell
        return genome

    def _sample_body_segment(self, kind: str) -> dict[str, Any]:
        d_in = self._snap(self.rng.uniform(2.5, 5.5))
        if kind == "cone":
            d_out = self._snap(self.rng.uniform(max(d_in + 0.5, 3.5), 10.0))
        else:
            d_out = d_in
        return {
            "kind": kind,
            "length_cm": self._snap_length(self.rng.uniform(20.0, 80.0)),
            "d_in_cm": d_in,
            "d_out_cm": d_out,
            "material_id": self.rng.choice(self.allowed_body_material_ids),
            "profile_params": {},
        }

    def _sample_bell(self, last_body_segment: Mapping[str, Any]) -> dict[str, Any] | None:
        if not self.allow_bell:
            return None
        kind_map = {"conical": "flare_conical", "exponential": "flare_exponential", "powerlaw": "flare_powerlaw"}
        kind = self.rng.choice([kind_map[item] for item in self.allowed_bell_types if item in kind_map] or ["flare_conical"])
        bell = {
            "kind": kind,
            "length_cm": self._snap_length(self.rng.uniform(max(self.bell_length_bounds[0], 6.0), min(self.bell_length_bounds[1], 30.0))),
            "d_in_cm": self._snap(float(last_body_segment["d_out_cm"])),
            "d_out_cm": self._snap(self.rng.uniform(max(float(last_body_segment["d_out_cm"]), 6.0), min(self.bell_exit_bounds[1], 20.0))),
            "material_id": self.rng.choice(self.allowed_bell_material_ids),
            "profile_params": {},
        }
        if kind == "flare_exponential":
            bell["profile_params"] = {"flare_parameter": self.rng.uniform(1.0, 5.0)}
        elif kind == "flare_powerlaw":
            bell["profile_params"] = {"power": self.rng.uniform(1.2, 3.0)}
        return bell

    def _split_segment(self, bodies: list[dict[str, Any]], idx: int) -> list[dict[str, Any]]:
        segment = copy.deepcopy(bodies[idx])
        length = float(segment.get("length_cm", 10.0))
        half = max(self.body_length_bounds[0], length / 2.0)
        seg_a = copy.deepcopy(segment)
        seg_b = copy.deepcopy(segment)
        seg_a["length_cm"] = half
        seg_b["length_cm"] = max(self.body_length_bounds[0], length - half)
        return bodies[:idx] + [seg_a, seg_b] + bodies[idx + 1 :]

    def _merge_segments(self, bodies: list[dict[str, Any]], idx: int) -> list[dict[str, Any]]:
        a = copy.deepcopy(bodies[idx])
        b = copy.deepcopy(bodies[idx + 1])
        merged = {
            "kind": a.get("kind", "cylinder") if a.get("kind") == b.get("kind") else "cone",
            "length_cm": float(a.get("length_cm", 0.0)) + float(b.get("length_cm", 0.0)),
            "d_in_cm": float(a.get("d_in_cm", 3.8)),
            "d_out_cm": float(b.get("d_out_cm", a.get("d_out_cm", 3.8))),
            "material_id": str(a.get("material_id") or b.get("material_id") or self.rng.choice(self.allowed_body_material_ids)),
            "profile_params": {},
        }
        return bodies[:idx] + [merged] + bodies[idx + 2 :]

    def _scale_lengths(self, genome: dict[str, Any], scale: float) -> dict[str, Any]:
        scaled = copy.deepcopy(genome)
        for seg in scaled.get("body_segments", []):
            seg["length_cm"] = self._snap_length(self._clamp(float(seg.get("length_cm", 1.0)) * scale, *self.body_length_bounds))
        if scaled.get("bell") is not None:
            bell_bounds = self.bell_length_bounds
            scaled["bell"]["length_cm"] = self._snap_length(self._clamp(float(scaled["bell"].get("length_cm", 0.0)) * scale, *bell_bounds))
        return scaled

    def _resolve_allowed_material_ids(self, materials_cfg: Mapping[str, Any]) -> list[str]:
        configured = [str(mid) for mid in materials_cfg.get("allowed_materials", []) or []]
        valid = [mid for mid in configured if mid in self.material_db.materials]
        return valid or self.material_db.list_ids()

    def _candidate_id(self) -> str:
        return f"candidate_{uuid.uuid4().hex[:8]}"

    def _snap(self, value: float) -> float:
        step = max(self.diameter_step, 1e-6)
        snapped = round(value / step) * step
        return round(snapped, 6)

    def _snap_length(self, value: float) -> float:
        return round(max(value, self.body_length_bounds[0]), 3)

    def _clamp(self, value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))
