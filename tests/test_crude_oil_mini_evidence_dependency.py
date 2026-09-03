from __future__ import annotations

import unittest

from app.crude_oil_mini_evidence_dependency import audit_directional_independence


class CrudeOilMiniEvidenceDependencyTests(unittest.TestCase):
    def test_same_causal_origin_counts_once(self):
        rows = [
            {
                "family": "A",
                "stance": "BEARISH",
                "counts_for_direction": True,
                "independence_status": "INDEPENDENT",
                "causal_origin": "LOCAL_PRICE_STRUCTURE",
            },
            {
                "family": "B",
                "stance": "BEARISH",
                "counts_for_direction": True,
                "independence_status": "INDEPENDENT",
                "causal_origin": "LOCAL_PRICE_STRUCTURE",
            },
        ]
        result = audit_directional_independence(rows)
        self.assertEqual(result["counted_families"], ["A"])
        self.assertEqual(result["counted_origins"], ["LOCAL_PRICE_STRUCTURE"])
        self.assertEqual(result["suppressed"][0]["reason"], "DUPLICATE_CAUSAL_ORIGIN")

    def test_dependent_family_is_suppressed(self):
        rows = [{
            "family": "PARTICIPATION",
            "stance": "BEARISH",
            "counts_for_direction": True,
            "independence_status": "DEPENDENT_ON_LOCAL_PRICE",
            "causal_origin": "LOCAL_PRICE_VOLUME",
        }]
        result = audit_directional_independence(rows)
        self.assertEqual(result["counted"], [])
        self.assertEqual(result["suppressed"][0]["reason"], "FAMILY_NOT_INDEPENDENT")

    def test_secondary_confirmation_overlap_is_exposed_not_hidden(self):
        rows = [
            {
                "family": "GLOBAL_CRUDE",
                "stance": "BULLISH",
                "counts_for_direction": True,
                "independence_status": "INDEPENDENT",
                "causal_origin": "CROSS_MARKET_CRUDE",
                "depends_on": [],
            },
            {
                "family": "EVENT_REACTION",
                "stance": "BULLISH",
                "counts_for_direction": True,
                "independence_status": "INDEPENDENT",
                "causal_origin": "EXOGENOUS_INFORMATION",
                "depends_on": ["CROSS_MARKET_CRUDE"],
            },
        ]
        result = audit_directional_independence(rows)
        self.assertEqual(result["counted_origins"], ["CROSS_MARKET_CRUDE", "EXOGENOUS_INFORMATION"])
        self.assertEqual(len(result["shared_confirmation_dependencies"]), 1)
        self.assertEqual(
            result["shared_confirmation_dependencies"][0]["also_depends_on_counted_origins"],
            ["CROSS_MARKET_CRUDE"],
        )


if __name__ == "__main__":
    unittest.main()
