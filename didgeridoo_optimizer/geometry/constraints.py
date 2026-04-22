from __future__ import annotations

from typing import Any, Mapping

from .builders import DesignBuilder
from .models import BELL_KINDS, Design, Segment


class GeometryValidator:
    def __init__(self) -> None:
        self._builder = DesignBuilder()

    def validate(self, design: Design, config: Mapping[str, Any] | None) -> list[str]:
        errors: list[str] = []
        config = dict(config or {})

        if not design.segments:
            return ["Design has no segments."]

        positioned = self._builder.assign_positions(design)
        geometry_constraints = dict(config.get("geometry_constraints", {}) or {})
        topology = dict(config.get("topology", {}) or {})
        mouthpiece_cfg = dict(config.get("mouthpiece", {}) or {})
        bell_cfg = dict(config.get("bell", {}) or {})

        total_length = positioned.total_length_cm
        total_length_range = dict(geometry_constraints.get("total_length_cm", {}) or {})
        min_total = float(total_length_range.get("min", 0.0))
        max_total = float(total_length_range.get("max", float("inf")))
        if total_length < min_total or total_length > max_total:
            errors.append(
                f"Total length {total_length:.3f} cm outside allowed range [{min_total}, {max_total}] cm."
            )

        diameter_range = dict(geometry_constraints.get("diameter_cm", {}) or {})
        min_diameter = float(diameter_range.get("min", 0.0))
        max_diameter = float(diameter_range.get("max", float("inf")))

        body_constraints = dict(geometry_constraints.get("body_segments", {}) or {})
        min_body_count = int(body_constraints.get("min_count", 0))
        max_body_count = int(body_constraints.get("max_count", 10**9))
        min_body_length = float(body_constraints.get("min_length_cm", 0.0))
        max_body_length = float(body_constraints.get("max_length_cm", float("inf")))

        allow_steps = bool(geometry_constraints.get("allow_steps", True))
        allow_reverse_taper = bool(geometry_constraints.get("allow_reverse_taper", True))
        allow_local_constrictions = bool(geometry_constraints.get("allow_local_constrictions", True))
        allow_local_expansions = bool(geometry_constraints.get("allow_local_expansions", True))
        allow_bell = bool(topology.get("allow_bell", True))
        allowed_bell_types = {str(item) for item in topology.get("allow_bell_types", ["conical", "exponential", "powerlaw"])}

        body_segments = [segment for segment in positioned.segments if self._is_body_segment(segment)]
        if len(body_segments) < min_body_count or len(body_segments) > max_body_count:
            errors.append(
                f"Body segment count {len(body_segments)} outside allowed range [{min_body_count}, {max_body_count}]."
            )

        previous_segment: Segment | None = None
        for index, segment in enumerate(positioned.segments):
            if segment.length_cm <= 0.0:
                errors.append(f"Segment {index} has non-positive length {segment.length_cm} cm.")

            for label, diameter in (("d_in", segment.d_in_cm), ("d_out", segment.d_out_cm)):
                if diameter < min_diameter or diameter > max_diameter:
                    errors.append(
                        f"Segment {index} {label} diameter {diameter:.3f} cm outside allowed range [{min_diameter}, {max_diameter}] cm."
                    )

            if self._is_body_segment(segment):
                if segment.length_cm < min_body_length:
                    errors.append(
                        f"Body segment {index} length {segment.length_cm:.3f} cm below minimum {min_body_length} cm."
                    )

            if segment.kind in BELL_KINDS:
                if not allow_bell:
                    errors.append(f"Bell segment {index} is not allowed by topology.")
                allowed_map = {
                    "flare_conical": "conical",
                    "flare_exponential": "exponential",
                    "flare_powerlaw": "powerlaw",
                }
                if allowed_map[segment.kind] not in allowed_bell_types:
                    errors.append(f"Bell segment {index} kind '{segment.kind}' is not enabled in config.")
                errors.extend(self._validate_bell(index, segment, bell_cfg))

            if segment.kind == "mouthpiece":
                errors.extend(self._validate_mouthpiece(index, segment, mouthpiece_cfg))

            if segment.kind not in {"mouthpiece", "branch", "helmholtz_neck"}:
                if segment.d_out_cm < segment.d_in_cm and not allow_reverse_taper:
                    errors.append(
                        f"Segment {index} reverse taper is not allowed (d_in={segment.d_in_cm}, d_out={segment.d_out_cm})."
                    )

            if previous_segment is not None:
                jump = segment.d_in_cm - previous_segment.d_out_cm
                if abs(jump) > 1e-9 and not allow_steps:
                    errors.append(
                        f"Step between segments {index - 1} and {index} is not allowed (Δd={jump:.3f} cm)."
                    )
                if jump < -1e-9 and not allow_local_constrictions:
                    errors.append(
                        f"Local constriction between segments {index - 1} and {index} is not allowed (Δd={jump:.3f} cm)."
                    )
                if jump > 1e-9 and not allow_local_expansions:
                    errors.append(
                        f"Local expansion between segments {index - 1} and {index} is not allowed (Δd={jump:.3f} cm)."
                    )
                if abs(previous_segment.position_end_cm - segment.position_start_cm) > 1e-9:
                    errors.append(
                        f"Segment positions are not contiguous between {index - 1} and {index}."
                    )
            previous_segment = segment

        return errors

    def soft_penalties(self, design: Design, config: Mapping[str, Any] | None) -> dict[str, float]:
        config = dict(config or {})
        positioned = self._builder.assign_positions(design)
        geometry_constraints = dict(config.get("geometry_constraints", {}) or {})
        body_constraints = dict(geometry_constraints.get("body_segments", {}) or {})
        materials_cfg = dict(config.get("materials", {}) or {})
        complexity_cfg = dict(materials_cfg.get("complexity_penalty", {}) or {})

        max_body_count = int(body_constraints.get("max_count", len(positioned.segments)))
        body_count = len([segment for segment in positioned.segments if self._is_body_segment(segment)])
        segment_count_penalty = max(0, body_count - max_body_count) * 0.05
        body_length_penalty = 0.0
        max_body_length = float(body_constraints.get("max_length_cm", float("inf")))
        for segment in positioned.segments:
            if self._is_body_segment(segment) and segment.length_cm > max_body_length:
                body_length_penalty += 0.01 * (segment.length_cm - max_body_length)

        jumps = []
        reverse_taper_penalty = 0.0
        for prev, curr in zip(positioned.segments, positioned.segments[1:]):
            jumps.append(abs(curr.d_in_cm - prev.d_out_cm))
        for segment in positioned.segments:
            if segment.kind not in {"mouthpiece", "branch", "helmholtz_neck"} and segment.d_out_cm < segment.d_in_cm:
                reverse_taper_penalty += (segment.d_in_cm - segment.d_out_cm) / max(segment.d_in_cm, 1e-9)

        diameter_jump_penalty = 0.01 * sum(jumps)

        material_change_count = sum(
            1
            for prev, curr in zip(positioned.segments, positioned.segments[1:])
            if prev.material_id != curr.material_id
        )
        extra_penalty = float(complexity_cfg.get("extra_penalty_per_material_change", 0.03))
        material_layout_penalty = (
            material_change_count * extra_penalty if bool(complexity_cfg.get("enabled", False)) else 0.0
        )

        max_diameter = max(max(segment.d_in_cm, segment.d_out_cm) for segment in positioned.segments)
        mean_body_diameter = (
            sum(segment.average_diameter_cm for segment in positioned.segments) / max(len(positioned.segments), 1)
        )
        bell_exit_penalty = max(0.0, (max_diameter / max(mean_body_diameter, 1e-9)) - 2.5) * 0.05

        penalties = {
            "segment_count_penalty": float(segment_count_penalty),
            "body_length_penalty": float(body_length_penalty),
            "diameter_jump_penalty": float(diameter_jump_penalty),
            "reverse_taper_penalty": float(reverse_taper_penalty),
            "material_layout_penalty": float(material_layout_penalty),
            "bell_exit_penalty": float(bell_exit_penalty),
        }
        penalties["total_penalty"] = sum(penalties.values())
        return penalties

    def _is_body_segment(self, segment: Segment) -> bool:
        return segment.kind not in {"mouthpiece", "branch", "helmholtz_neck", *BELL_KINDS}

    def _validate_mouthpiece(self, index: int, segment: Segment, mouthpiece_cfg: Mapping[str, Any]) -> list[str]:
        constraints = dict(mouthpiece_cfg.get("geometry_constraints", {}) or {})
        errors: list[str] = []
        length_range = dict(constraints.get("total_length_cm", {}) or {})
        entry_range = dict(constraints.get("entry_diameter_cm", {}) or {})
        throat_range = dict(constraints.get("throat_diameter_cm", {}) or {})
        exit_range = dict(constraints.get("exit_diameter_cm", {}) or {})

        throat_diameter = float(segment.profile_params.get("throat_diameter_cm", min(segment.d_in_cm, segment.d_out_cm)))
        if not self._in_range(segment.length_cm, length_range):
            errors.append(f"Mouthpiece segment {index} length {segment.length_cm:.3f} cm outside configured range.")
        if not self._in_range(segment.d_in_cm, entry_range):
            errors.append(f"Mouthpiece segment {index} entry diameter {segment.d_in_cm:.3f} cm outside configured range.")
        if not self._in_range(throat_diameter, throat_range):
            errors.append(f"Mouthpiece segment {index} throat diameter {throat_diameter:.3f} cm outside configured range.")
        if not self._in_range(segment.d_out_cm, exit_range):
            errors.append(f"Mouthpiece segment {index} exit diameter {segment.d_out_cm:.3f} cm outside configured range.")
        return errors

    def _validate_bell(self, index: int, segment: Segment, bell_cfg: Mapping[str, Any]) -> list[str]:
        constraints = dict(bell_cfg.get("geometry_constraints", {}) or {})
        errors: list[str] = []
        if not self._in_range(segment.length_cm, dict(constraints.get("length_cm", {}) or {})):
            errors.append(f"Bell segment {index} length {segment.length_cm:.3f} cm outside configured range.")
        if not self._in_range(segment.d_in_cm, dict(constraints.get("throat_diameter_cm", {}) or {})):
            errors.append(f"Bell segment {index} throat diameter {segment.d_in_cm:.3f} cm outside configured range.")
        if not self._in_range(segment.d_out_cm, dict(constraints.get("exit_diameter_cm", {}) or {})):
            errors.append(f"Bell segment {index} exit diameter {segment.d_out_cm:.3f} cm outside configured range.")
        flare_parameter_range = dict(constraints.get("flare_parameter", {}) or {})
        if flare_parameter_range and segment.kind in BELL_KINDS:
            flare_parameter = segment.profile_params.get("flare_parameter", segment.profile_params.get("power"))
            if flare_parameter is not None and not self._in_range(float(flare_parameter), flare_parameter_range):
                errors.append(
                    f"Bell segment {index} flare parameter {float(flare_parameter):.3f} outside configured range."
                )
        return errors

    def _in_range(self, value: float, range_dict: Mapping[str, Any]) -> bool:
        if not range_dict:
            return True
        lower = float(range_dict.get("min", float("-inf")))
        upper = float(range_dict.get("max", float("inf")))
        return lower <= value <= upper
