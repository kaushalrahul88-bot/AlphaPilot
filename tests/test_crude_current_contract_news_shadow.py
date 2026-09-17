from __future__ import annotations

import unittest
from datetime import datetime

from app.crude_current_contract_news_shadow import active_news_at, news_shadow_decision


class CrudeCurrentContractNewsShadowTests(unittest.TestCase):
    def test_preopen_news_uses_copper_eight_hour_policy(self):
        records = [{
            "event_id": "x",
            "underlying_event_id": "x",
            "available_at": "2026-08-31T03:42:07+05:30",
            "event_type": "WAR_ESCALATION",
            "effect": "BULLISH",
            "disposition": "ALLOW",
        }]
        active = active_news_at(datetime.fromisoformat("2026-08-31T15:30:00+05:30"), records)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["effective_start"], "2026-08-31T09:00:00+05:30")

    def test_future_news_is_not_visible(self):
        records = [{
            "event_id": "x",
            "underlying_event_id": "x",
            "available_at": "2026-08-18T20:48:24+05:30",
            "effect": "BULLISH",
            "disposition": "ALLOW",
        }]
        active = active_news_at(datetime.fromisoformat("2026-08-18T20:45:00+05:30"), records)
        self.assertEqual(active, [])

    def test_context_only_news_never_votes(self):
        records = [{
            "event_id": "eia",
            "underlying_event_id": "eia",
            "available_at": "2026-08-19T20:00:00+05:30",
            "effect": "UNKNOWN",
            "disposition": "CONTEXT_ONLY",
        }]
        active = active_news_at(datetime.fromisoformat("2026-08-19T20:30:00+05:30"), records)
        self.assertEqual(active, [])

    def test_bullish_news_only_upgrades_near_ready_wait(self):
        features = {
            "structure": "UPTREND",
            "return_15m_pct": 0.10,
            "ema20_gap_pct": 0.05,
            "ema50_gap_pct": -0.01,
        }
        active = [{"effect": "BULLISH"}]
        decision = news_shadow_decision(features, "WAIT", active)
        self.assertEqual(decision["technical_pass_count"], 3)
        self.assertEqual(decision["action"], "BUY")

    def test_bullish_news_does_not_override_weak_technicals(self):
        features = {
            "structure": "RANGE",
            "return_15m_pct": -0.10,
            "ema20_gap_pct": 0.05,
            "ema50_gap_pct": -0.01,
        }
        decision = news_shadow_decision(features, "WAIT", [{"effect": "BULLISH"}])
        self.assertEqual(decision["action"], "WAIT")

    def test_bearish_news_can_veto_but_not_create_short(self):
        features = {
            "structure": "UPTREND",
            "return_15m_pct": 0.10,
            "ema20_gap_pct": 0.05,
            "ema50_gap_pct": 0.05,
        }
        veto = news_shadow_decision(features, "BUY", [{"effect": "BEARISH"}])
        self.assertEqual(veto["action"], "WAIT")
        wait = news_shadow_decision(features, "WAIT", [{"effect": "BEARISH"}])
        self.assertEqual(wait["action"], "WAIT")
        self.assertFalse(wait["news_can_create_sell"])


if __name__ == "__main__":
    unittest.main()
