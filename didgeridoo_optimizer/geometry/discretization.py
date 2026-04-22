from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from .builders import DesignBuilder
from .models import Design, Segment


class GeometryDiscretizer:
    """Convert tapered/flared geometry into short uniform segments for the TMM."""

    def __init__(self) -> None:
        self._builder = DesignBuilder()

    def discretize(self, design: Design, max_segment_cm: float = 1.0) -> Design:
        if max_segment_cm <= 0.0:
            raise ValueError(f"max_segment_cm must be > 0, got {max_segment_cm}.")

        discretized_segments: list[Segment] = []
        for segment in design.segments:
            discretized_segments.extend(self._discretize_segment(segment, max_segment_cm=max_segment_cm))

        metadata = dict(design.metadata)
        metadata.update(
            {
                "is_discretized": True,
                "discretization_max_segment_cm": float(max_segment_cm),
                "source_segment_count": len(design.segments),
                "discretized_segment_count": len(discretized_segments),
            }
        )
        discretized_design = Design(id=design.id, segments=discretized_segments, metadata=metadata)
        return self._builder.assign_positions(discretized_design)

    def _discretize_segment(self, segment: Segment, *, max_segment_cm: float) -> list[Segment]:
        if segment.kind in {"branch", "helmholtz_neck"}:
            raise NotImplementedError(
                f"Geometry discretization for segment kind '{segment.kind}' is outside the linear MVP scope."
            )

        if segment.is_uniform:
            slice_count = max(1, math.ceil(segment.length_cm / max_segment_cm))
        else:
            slice_count = max(2, math.ceil(segment.length_cm / max_segment_cm))

        slice_length_cm = segment.length_cm / slice_count
        slices: list[Segment] = []

        for slice_index in range(slice_count):
            t0 = slice_index / slice_count
            t1 = (slice_index + 1) / slice_count
            d_start = self._diameter_at(segment, t0)
            d_end = self._diameter_at(segment, t1)
            d_mid = self._diameter_at(segment, 0.5 * (t0 + t1))
            slice_profile = dict(segment.profile_params)
            slice_profile.update(
                {
                    "source_kind": segment.kind,
                    "slice_index": slice_index,
                    "slice_count": slice_count,
                    "t0": t0,
                    "t1": t1,
                    "local_d_start_cm": d_start,
                    "local_d_end_cm": d_end,
                }
            )
            slices.append(
                Segment(
                    kind="mouthpiece" if segment.kind == "mouthpiece" else "cylinder",
                    length_cm=slice_length_cm,
                    d_in_cm=d_mid,
                    d_out_cm=d_mid,
                    material_id=segment.material_id,
                    profile_params=slice_profile,
                    position_start_cm=0.0,
                    position_end_cm=0.0,
                )
            )

        return slices

    def _diameter_at(self, segment: Segment, t: float) -> float:
        t = min(max(t, 0.0), 1.0)
        d_in = segment.d_in_cm
        d_out = segment.d_out_cm
        params: dict[str, Any] = dict(segment.profile_params)

        if segment.kind in {"cylinder", "mouthpiece"}:
            return d_in + (d_out - d_in) * t

        if segment.kind in {"cone", "flare_conical"}:
            return d_in + (d_out - d_in) * t

        if segment.kind == "flare_exponential":
            flare_parameter = float(params.get("flare_parameter", 3.0))
            flare_parameter = max(1e-6, flare_parameter)
            shape = (math.exp(flare_parameter * t) - 1.0) / (math.exp(flare_parameter) - 1.0)
            if d_in > 0.0 and d_out > 0.0:
                return d_in * (d_out / d_in) ** shape
            return d_in + (d_out - d_in) * shape

        if segment.kind == "flare_powerlaw":
            power = float(params.get("power", params.get("flare_parameter", 2.0)))
            power = max(0.05, power)
            shape = t**power
            return d_in + (d_out - d_in) * shape

        raise ValueError(f"Unsupported segment kind for discretization: {segment.kind}")
