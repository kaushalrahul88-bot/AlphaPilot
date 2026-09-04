from __future__ import annotations

import unittest

from app.commodity_direction_core import (
    architecture_contract,
    build_direction_thesis,
    normalize_family,
)


def _family(
    name: str,
    origin: str,
    stance: str,
    *,
    role: str = "PERCEPTION",
    depends_on=None,
    registered: bool = False,
):
    return normalize_family(
        {
            "family": name,
            "causal_origin": origin,
            "stance": stance,
            "counts_for_direction": True,
            "state": "TEST",
        },
        role=role,
        depends_on_origins=depends_on or [],
        independent_vote_registered=registered,
    )


class CommodityDirectionCoreTests(unittest.TestCase):
    def test_two_distinct_aligned_origins_create_moderate_direction(self):
        result = build_direction_thesis(
            [
                _family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH"),
                _family("PARTICIPATION", "OPTION_MARKET_POSITIONING", "BULLISH", role="POSITIONING"),
            ]
        )
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["confidence"], "MODERATE")

    def test_three_aligned_origins_create_strong_direction(self):
        result = build_direction_thesis(
            [
                _family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BEARISH"),
                _family("PARTICIPATION", "OPTION_MARKET_POSITIONING", "BEARISH", role="POSITIONING"),
                _family("GLOBAL", "GLOBAL_PRICE_DISCOVERY", "BEARISH"),
            ]
        )
        self.assertEqual(result["direction"], "BEARISH")
        self.assertEqual(result["confidence"], "STRONG")

    def test_single_origin_is_insufficient(self):
        result = build_direction_thesis(
            [_family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH")]
        )
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["confidence"], "WEAK")

    def test_duplicate_origin_does_not_manufacture_second_confirmation(self):
        result = build_direction_thesis(
            [
                _family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH"),
                _family("LOCAL_MOMENTUM", "LOCAL_PRICE_STRUCTURE", "BULLISH"),
            ]
        )
        self.assertEqual(result["direction"], "UNKNOWN")
        reasons = {row["reason"] for row in result["dependency_audit"]["suppressed"]}
        self.assertIn("DUPLICATE_CAUSAL_ORIGIN", reasons)

    def test_dependent_origin_cannot_double_count(self):
        result = build_direction_thesis(
            [
                _family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH"),
                _family(
                    "EVENT_REACTION",
                    "EVENT_CAUSAL_REACTION",
                    "BULLISH",
                    role="EVENT",
                    depends_on=["LOCAL_PRICE_STRUCTURE"],
                ),
            ]
        )
        self.assertEqual(result["direction"], "UNKNOWN")
        reasons = {row["reason"] for row in result["dependency_audit"]["suppressed"]}
        self.assertIn("DEPENDENT_ON_COUNTED_CAUSAL_ORIGIN", reasons)

    def test_opposing_independent_origin_forces_conflict(self):
        result = build_direction_thesis(
            [
                _family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH"),
                _family("GLOBAL", "GLOBAL_PRICE_DISCOVERY", "BEARISH"),
            ]
        )
        self.assertEqual(result["direction"], "UNKNOWN")
        self.assertEqual(result["confidence"], "CONFLICTED")

    def test_memory_is_context_only_by_default(self):
        memory = _family(
            "EXPERIENCE_MEMORY",
            "HISTORICAL_ANALOGUE",
            "BULLISH",
            role="MEMORY",
        )
        self.assertFalse(memory["counts_for_direction"])
        result = build_direction_thesis(
            [
                _family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH"),
                memory,
            ]
        )
        self.assertEqual(result["direction"], "UNKNOWN")

    def test_registered_memory_still_respects_declared_local_dependency(self):
        memory = _family(
            "EXPERIENCE_MEMORY",
            "HISTORICAL_ANALOGUE",
            "BULLISH",
            role="MEMORY",
            depends_on=["LOCAL_PRICE_STRUCTURE"],
            registered=True,
        )
        result = build_direction_thesis(
            [
                _family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH"),
                memory,
            ]
        )
        self.assertEqual(result["direction"], "UNKNOWN")
        reasons = {row["reason"] for row in result["dependency_audit"]["suppressed"]}
        self.assertIn("DEPENDENT_ON_COUNTED_CAUSAL_ORIGIN", reasons)

    def test_contract_preserves_research_only_no_score_semantics(self):
        contract = architecture_contract()
        self.assertTrue(contract["shared_across_commodities"])
        self.assertFalse(contract["weighted_score_used"])
        self.assertTrue(contract["research_only"])
        self.assertFalse(contract["promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
