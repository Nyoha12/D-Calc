from __future__ import annotations

import unittest

from didgeridoo_optimizer.optimization.objectives import (
    aggregate_score,
    objective_config_warnings,
)
from didgeridoo_optimizer.optimization.selector import FinalSelector


class ObjectiveActivationTests(unittest.TestCase):
    def test_absent_robustness_scores_do_not_affect_aggregate(self) -> None:
        config = {
            "objectives": {
                "drone_f0": {"enabled": True, "weight": 1.0},
                "impedance_peaks": {"enabled": True, "weight": 1.0},
            }
        }
        scores = {
            "drone_f0": 0.5,
            "impedance_peaks": 1.0,
            "beginner_robustness": 1.0,
            "expert_robustness": 1.0,
        }

        score = aggregate_score(scores, {"total_penalty": 0.0}, config)

        self.assertAlmostEqual(score, 0.75)

    def test_enabled_robustness_scores_affect_aggregate(self) -> None:
        config = {
            "objectives": {
                "drone_f0": {"enabled": True, "weight": 1.0},
                "impedance_peaks": {"enabled": True, "weight": 1.0},
                "beginner_robustness": {"enabled": True, "weight": 2.0},
            }
        }
        scores = {
            "drone_f0": 0.5,
            "impedance_peaks": 1.0,
            "beginner_robustness": 0.25,
            "expert_robustness": 1.0,
        }

        score = aggregate_score(scores, {"total_penalty": 0.0}, config)

        self.assertAlmostEqual(score, (0.5 + 1.0 + 2.0 * 0.25) / 4.0)

    def test_enabled_weight_zero_objective_is_ignored(self) -> None:
        config = {
            "objectives": {
                "drone_f0": {"enabled": True, "weight": 1.0},
                "impedance_peaks": {"enabled": True, "weight": 0.0},
            }
        }
        scores = {
            "drone_f0": 0.25,
            "impedance_peaks": 1.0,
        }

        score = aggregate_score(scores, {"total_penalty": 0.0}, config)

        self.assertAlmostEqual(score, 0.25)

    def test_unknown_enabled_objective_warns_and_does_not_score_or_rank(self) -> None:
        config = {
            "objectives": {
                "drone_f0": {"enabled": True, "weight": 1.0},
                "mystery_metric": {"enabled": True, "weight": 100.0},
            },
            "optimization": {"final_selector": "weighted_sum"},
        }
        low_drone_high_unknown = {
            "genome": {"id": "low_drone_high_unknown", "topology": "straight"},
            "result": {
                "design_id": "low_drone_high_unknown",
                "valid": True,
                "aggregate_score": 0.5,
                "objective_scores": {
                    "drone_f0": 0.5,
                    "mystery_metric": 1.0,
                },
            },
            "valid": True,
            "aggregate_score": 0.5,
            "normalized_objectives": {
                "drone_f0": 0.5,
                "mystery_metric": 1.0,
            },
        }
        high_drone_low_unknown = {
            "genome": {"id": "high_drone_low_unknown", "topology": "straight"},
            "result": {
                "design_id": "high_drone_low_unknown",
                "valid": True,
                "aggregate_score": 0.6,
                "objective_scores": {
                    "drone_f0": 0.6,
                    "mystery_metric": 0.0,
                },
            },
            "valid": True,
            "aggregate_score": 0.6,
            "normalized_objectives": {
                "drone_f0": 0.6,
                "mystery_metric": 0.0,
            },
        }

        score = aggregate_score(
            low_drone_high_unknown["result"]["objective_scores"],
            {"total_penalty": 0.0},
            config,
        )
        ranked = FinalSelector().rank_top_n(
            [low_drone_high_unknown, high_drone_low_unknown],
            2,
            "weighted_sum",
            config,
        )

        self.assertAlmostEqual(score, 0.5)
        self.assertIn("unknown_enabled_objective:mystery_metric", objective_config_warnings(config))
        self.assertEqual(ranked[0]["result"]["design_id"], "high_drone_low_unknown")
        self.assertEqual(ranked[0]["normalized_objectives"], {"drone_f0": 0.6})
        self.assertEqual(ranked[1]["normalized_objectives"], {"drone_f0": 0.5})


if __name__ == "__main__":
    unittest.main()
