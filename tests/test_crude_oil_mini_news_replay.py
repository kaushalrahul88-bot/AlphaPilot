from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.crude_news_intelligence import apply_crude_news_intelligence
from app.crude_oil_mini_news_replay import _decide_with_news, _news_lane

IST = ZoneInfo("Asia/Kolkata")


class CrudeOilMiniNewsReplayTests(unittest.TestCase):
    def _session(self):
        return {
            "start": datetime(2026, 8, 3, 9, 0, tzinfo=IST),
            "end": datetime(2026, 8, 3, 23, 30, tzinfo=IST),
            "previous_market_bar": datetime(2026, 7, 31, 23, 25, tzinfo=IST),
        }

    def _bullish_record(self, available_at="2026-08-03T09:30:00+05:30"):
        raw = [{
            "event_id": "x",
            "underlying_event_id": "x",
            "event_type": "HORMUZ_SHIPPING_DISRUPTION",
            "available_at": available_at,
            "source": "Reuters",
            "value": {"headline": "Strait of Hormuz shipping disruption after tanker attack"},
        }]
        return apply_crude_news_intelligence(raw)["records"]

    def test_future_news_is_invisible(self):
        lane = _news_lane("2026-08-03T09:00:00+05:30", self._session(), self._bullish_record())
        self.assertEqual(lane["stance"], "UNKNOWN")
        self.assertEqual(lane["active_record_count"], 0)

    def test_closed_market_news_carries_to_next_session(self):
        lane = _news_lane(
            "2026-08-03T10:00:00+05:30",
            self._session(),
            self._bullish_record("2026-08-02T12:00:00+05:30"),
        )
        self.assertEqual(lane["stance"], "BULLISH")
        self.assertEqual(lane["active_record_count"], 1)

    def test_older_session_news_is_not_revived(self):
        lane = _news_lane(
            "2026-08-03T10:00:00+05:30",
            self._session(),
            self._bullish_record("2026-07-31T18:00:00+05:30"),
        )
        self.assertEqual(lane["stance"], "UNKNOWN")
        self.assertEqual(lane["active_record_count"], 0)

    def test_news_cannot_create_trade_without_price_confirmation(self):
        baseline = [
            {"lane": "STRUCTURE", "stance": "UNKNOWN"},
            {"lane": "MOMENTUM", "stance": "UNKNOWN"},
            {"lane": "VALUE_LOCATION", "stance": "BULLISH"},
            {"lane": "PARTICIPATION", "stance": "BULLISH"},
            {"lane": "BREAKOUT", "stance": "UNKNOWN"},
            {"lane": "MEMORY", "stance": "UNKNOWN"},
        ]
        news = {"lane": "NEWS", "stance": "BULLISH"}
        decision = _decide_with_news(baseline, news)
        self.assertEqual(decision["action"], "WAIT")

    def test_news_can_complete_confirmed_market_setup(self):
        baseline = [
            {"lane": "STRUCTURE", "stance": "BULLISH"},
            {"lane": "MOMENTUM", "stance": "UNKNOWN"},
            {"lane": "VALUE_LOCATION", "stance": "BULLISH"},
            {"lane": "PARTICIPATION", "stance": "UNKNOWN"},
            {"lane": "BREAKOUT", "stance": "UNKNOWN"},
            {"lane": "MEMORY", "stance": "UNKNOWN"},
        ]
        news = {"lane": "NEWS", "stance": "BULLISH"}
        decision = _decide_with_news(baseline, news)
        self.assertEqual(decision["action"], "BUY_CE")
        self.assertTrue(decision["news_used"])

    def test_context_only_news_never_votes(self):
        raw = [{
            "event_id": "talks",
            "underlying_event_id": "talks",
            "event_type": "CEASEFIRE_DIPLOMACY",
            "available_at": "2026-08-03T09:30:00+05:30",
            "source": "Reuters",
            "value": {"headline": "Talks continue on a possible Strait of Hormuz reopening"},
        }]
        enriched = apply_crude_news_intelligence(raw)["records"]
        lane = _news_lane("2026-08-03T10:00:00+05:30", self._session(), enriched)
        self.assertEqual(lane["stance"], "UNKNOWN")
        self.assertEqual(lane["active_record_count"], 0)


if __name__ == "__main__":
    unittest.main()
