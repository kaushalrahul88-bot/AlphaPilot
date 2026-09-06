from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.fno_prospective_capture_v1 import assess_market_liveness
from app.fno_prospective_protocol_v1 import protocol_manifest

UTC = timezone.utc


class FnoProspectiveMarketLivenessTests(unittest.TestCase):
    def test_recent_groww_last_trade_time_proves_market_live(self):
        now = datetime(2026, 9, 7, 4, 30, tzinfo=UTC)
        last_trade = now - timedelta(seconds=20)
        quote = {
            "data": {
                "status": "SUCCESS",
                "payload": {"last_trade_time": int(last_trade.timestamp() * 1000)},
            }
        }
        result = assess_market_liveness(quote, now=now)
        self.assertTrue(result["live"])
        self.assertEqual(result["status"], "LIVE")
        self.assertEqual(result["source"], "GROWW_LIVE_QUOTE_LAST_TRADE_TIME")

    def test_stale_previous_session_quote_fails_closed(self):
        now = datetime(2026, 9, 7, 4, 30, tzinfo=UTC)
        stale = now - timedelta(days=3)
        quote = {"data": {"payload": {"last_trade_time": int(stale.timestamp() * 1000)}}}
        result = assess_market_liveness(quote, now=now)
        self.assertFalse(result["live"])
        self.assertEqual(result["status"], "STALE")

    def test_missing_trade_time_fails_closed(self):
        now = datetime(2026, 9, 7, 4, 30, tzinfo=UTC)
        result = assess_market_liveness({"data": {"payload": {"last_price": 100}}}, now=now)
        self.assertFalse(result["live"])
        self.assertEqual(result["status"], "UNPROVEN")
        self.assertEqual(result["reason"], "MISSING_LAST_TRADE_TIME")

    def test_protocol_requires_liveness_not_just_weekday_clock(self):
        manifest = protocol_manifest()
        self.assertTrue(manifest["market_liveness_required_before_freeze"])
        self.assertFalse(manifest["weekday_clock_alone_sufficient"])
        self.assertFalse(manifest["live_execution"])
        self.assertEqual(manifest["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
