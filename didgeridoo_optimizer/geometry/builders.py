from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from .models import Design, Segment


class DesignBuilder:
    """Build Design objects from normalized dict-like genomes."""

    def build(self, genome: Mapping[str, Any] | Design) -> Design:
        if isinstance(genome, Design):
            return self.assign_positions(genome)

        if not isinstance(genome, Mapping):
            raise TypeError(f"Genome must be a mapping or Design, got {type(genome)!r}.")

        raw_segments = genome.get("segments", [])
        if not isinstance(raw_segments, Iterable):
            raise TypeError("Genome 'segments' must be iterable.")

        segments = [self._coerce_segment(raw_segment) for raw_segment in raw_segments]
        metadata = dict(genome.get("metadata", {}))
        if "name" in genome and "name" not in metadata:
            metadata["name"] = genome["name"]

        design = Design(id=str(genome.get("id", "design")), segments=segments, metadata=metadata)
        return self.assign_positions(design)

    def assign_positions(self, design: Design) -> Design:
        x_cm = 0.0
        positioned: list[Segment] = []
        for segment in design.segments:
            positioned_segment = replace(
                segment,
                position_start_cm=x_cm,
                position_end_cm=x_cm + segment.length_cm,
            )
            positioned.append(positioned_segment)
            x_cm += segment.length_cm

        metadata = dict(design.metadata)
        metadata["total_length_cm"] = x_cm
        return Design(id=design.id, segments=positioned, metadata=metadata)

    def total_length_cm(self, design: Design) -> float:
        return sum(segment.length_cm for segment in design.segments)

    def _coerce_segment(self, raw_segment: Segment | Mapping[str, Any]) -> Segment:
        if isinstance(raw_segment, Segment):
            return raw_segment
        if not isinstance(raw_segment, Mapping):
            raise TypeError(f"Segment entry must be Segment or mapping, got {type(raw_segment)!r}.")
        return Segment(
            kind=str(raw_segment["kind"]),
            length_cm=float(raw_segment["length_cm"]),
            d_in_cm=float(raw_segment["d_in_cm"]),
            d_out_cm=float(raw_segment["d_out_cm"]),
            material_id=str(raw_segment["material_id"]),
            profile_params=dict(raw_segment.get("profile_params", {}) or {}),
            position_start_cm=float(raw_segment.get("position_start_cm", 0.0)),
            position_end_cm=float(raw_segment.get("position_end_cm", 0.0)),
        )
