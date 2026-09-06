from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.crypto_btc_prospective_proof_bridge import _reconcile_positioning_evidence
from app.crypto_market_intelligence import Evidence

UTC = timezone.utc
NOW = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)


def _evidence(source: str, stance: str, *, confidence: float = 0.75, context_only: bool = False) -> Evidence:
    return Evidence(
        family="DERIVATIVES_POSITIONING",
        causal_origin="LEVERAGED_POSITIONING",
        stance=stance,
        strength="MEDIUM" if not context_only else "LOW",
        confidence=confidence,
        observed_at=NOW,
        reason="test",
        context_only=context_only,
        source=source,
        metadata={"may_generate_futures_trade": False},
    )


class PositioningReconciliationTests(unittest.TestCase):
    def test_same_direction_is_one_vote_not_two(self):
        result = _reconcile_positioning_evidence(
            _evidence("COINGLASS", "BULLISH", confidence=0.70),
            _evidence("DELTA", "BULLISH", confidence=0.75),
        )
        self.assertEqual(result.stance, "BULLISH")
        self.assertFalse(result.context_only)
        self.assertEqual(result.metadata["same_origin_corroborating_sources"], ["COINGLASS", "DELTA"])
        self.assertFalse(result.metadata["double_counted"])

    def test_same_origin_provider_conflict_cancels_direction(self):
        result = _reconcile_positioning_evidence(
            _evidence("COINGLASS", "BULLISH"),
            _evidence("DELTA", "BEARISH"),
        )
        self.assertEqual(result.stance, "UNKNOWN")
        self.assertTrue(result.context_only)
        self.assertTrue(result.metadata["conflict_cancels_origin"])
        self.assertFalse(result.metadata["double_counted"])

    def test_directional_candidate_beats_unknown_context(self):
        result = _reconcile_positioning_evidence(
            _evidence("PIT", "UNKNOWN", context_only=True),
            _evidence("DELTA", "BEARISH"),
        )
        self.assertEqual(result.stance, "BEARISH")
        self.assertEqual(result.source, "DELTA")


if __name__ == "__main__":
    unittest.main()
