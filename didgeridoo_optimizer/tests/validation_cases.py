from __future__ import annotations

from typing import Any


def cylinder_design(design_id: str, length_cm: float, diameter_cm: float, material_id: str = "pvc_pressure") -> dict[str, Any]:
    return {
        "id": design_id,
        "segments": [
            {
                "kind": "cylinder",
                "length_cm": float(length_cm),
                "d_in_cm": float(diameter_cm),
                "d_out_cm": float(diameter_cm),
                "material_id": material_id,
            }
        ],
    }


def validation_cases() -> dict[str, Any]:
    a_ref = cylinder_design("A_ref", 140.0, 3.8)
    return {
        "A": {
            "reference": a_ref,
            "longer": cylinder_design("A_longer", 160.0, 3.8),
            "shorter": cylinder_design("A_shorter", 120.0, 3.8),
            "narrower": cylinder_design("A_narrower", 140.0, 3.2),
            "wider": cylinder_design("A_wider", 140.0, 4.5),
        },
        "B": {
            "reference": {
                "id": "B_truncated_cone",
                "segments": [
                    {
                        "kind": "cone",
                        "length_cm": 140.0,
                        "d_in_cm": 3.0,
                        "d_out_cm": 7.0,
                        "material_id": "pvc_pressure",
                    }
                ],
            },
            "cylinder_reference": a_ref,
        },
        "C": {
            "reference": {
                "id": "C_cylinder_only",
                "segments": [
                    {
                        "kind": "cylinder",
                        "length_cm": 120.0,
                        "d_in_cm": 3.8,
                        "d_out_cm": 3.8,
                        "material_id": "pvc_pressure",
                    }
                ],
            },
            "with_bell": {
                "id": "C_cylinder_plus_bell",
                "segments": [
                    {
                        "kind": "cylinder",
                        "length_cm": 120.0,
                        "d_in_cm": 3.8,
                        "d_out_cm": 3.8,
                        "material_id": "pvc_pressure",
                    },
                    {
                        "kind": "flare_conical",
                        "length_cm": 20.0,
                        "d_in_cm": 3.8,
                        "d_out_cm": 12.0,
                        "material_id": "pvc_pressure",
                    },
                ],
            },
        },
        "D": {
            "stepped": {
                "id": "D_multisegment_stepped",
                "segments": [
                    {"kind": "cylinder", "length_cm": 30.0, "d_in_cm": 3.4, "d_out_cm": 3.4, "material_id": "pvc_pressure"},
                    {"kind": "cylinder", "length_cm": 30.0, "d_in_cm": 4.8, "d_out_cm": 4.8, "material_id": "pvc_pressure"},
                    {"kind": "cylinder", "length_cm": 30.0, "d_in_cm": 3.2, "d_out_cm": 3.2, "material_id": "pvc_pressure"},
                    {"kind": "cylinder", "length_cm": 30.0, "d_in_cm": 5.3, "d_out_cm": 5.3, "material_id": "pvc_pressure"},
                    {"kind": "cylinder", "length_cm": 20.0, "d_in_cm": 4.5, "d_out_cm": 4.5, "material_id": "pvc_pressure"},
                ],
            },
            "smoothed": {
                "id": "D_smoothed_reference",
                "segments": [
                    {
                        "kind": "cone",
                        "length_cm": 140.0,
                        "d_in_cm": 3.4,
                        "d_out_cm": 4.5,
                        "material_id": "pvc_pressure",
                    }
                ],
            },
        },
        "E": {
            "dissipative": {
                "id": "E_irregular_dissipative",
                "segments": [
                    {"kind": "cone", "length_cm": 50.0, "d_in_cm": 3.2, "d_out_cm": 4.2, "material_id": "birch__humid__raw__irregular__medium"},
                    {"kind": "cone", "length_cm": 45.0, "d_in_cm": 4.2, "d_out_cm": 5.7, "material_id": "birch__humid__raw__irregular__medium"},
                    {"kind": "cone", "length_cm": 45.0, "d_in_cm": 5.7, "d_out_cm": 7.8, "material_id": "birch__humid__raw__irregular__medium"},
                ],
            },
            "epoxy_lined": {
                "id": "E_irregular_epoxy_lined",
                "segments": [
                    {"kind": "cone", "length_cm": 50.0, "d_in_cm": 3.2, "d_out_cm": 4.2, "material_id": "birch__airdry__epoxy_lined__standard__medium"},
                    {"kind": "cone", "length_cm": 45.0, "d_in_cm": 4.2, "d_out_cm": 5.7, "material_id": "birch__airdry__epoxy_lined__standard__medium"},
                    {"kind": "cone", "length_cm": 45.0, "d_in_cm": 5.7, "d_out_cm": 7.8, "material_id": "birch__airdry__epoxy_lined__standard__medium"},
                ],
            },
        },
    }
