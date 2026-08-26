import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.commodity_click_replay import CLICK_TIMES, _data_quality, _historical_mtf, _summary, validate_frozen_tuesday_phase_a_data


IST = ZoneInfo("Asia/Kolkata")


def _rows(day, start_hour, count, minutes):
    start = datetime(day.year, day.month, day.day, start_hour, 0, tzinfo=IST)
    return [[(start + timedelta(minutes=minutes * index)).isoformat(), 100, 101, 99, 100, 10] for index in range(count)]


class CommodityClickReplayTests(unittest.IsolatedAsyncioTestCase):
    def test_frozen_click_times_are_unchanged(self):
        self.assertEqual(CLICK_TIMES, ("09:35", "10:55", "11:05", "13:20", "13:35", "15:15", "15:25", "16:15", "16:40", "18:35"))

    def test_historical_mtf_always_returns_unpackable_snapshot(self):
        day = date(2026, 8, 25)
        rows = _rows(day, 9, 120, 5)
        frames, plan, snapshot = _historical_mtf(
            {"5m": rows, "15m": rows, "1h": rows},
            datetime(2026, 8, 25, 18, 35, tzinfo=IST),
        )
        self.assertEqual(set(frames), {"5m", "15m", "1h"})
        self.assertIn(snapshot["action"], {"BUY", "SELL", "NO TRADE"})
        self.assertTrue(snapshot["fresh_market_data"])
        self.assertTrue(plan is None or isinstance(plan, dict))

    def test_summary_does_not_present_overlapping_clicks_as_additive_pnl(self):
        decisions = [
            {"status": "READY", "outcome": {"r_multiple": 1.4}},
            {"status": "READY", "outcome": {"r_multiple": -1.0}},
            {"status": "WAIT", "outcome": None},
            {"status": "NO_TRADE", "outcome": None},
        ]
        result = _summary(decisions)
        self.assertEqual(result["ready_setups"], 2)
        self.assertEqual(result["average_resolved_r_proxy"], 0.2)
        self.assertNotIn("total_r", result)
        self.assertTrue(result["non_additive"])

    def test_data_quality_rejects_missing_target_session(self):
        target = date(2026, 8, 25)
        prior = _rows(date(2026, 8, 24), 9, 120, 5)
        quality = _data_quality(
            "CRUDEOIL",
            {"trading_symbol": "CRUDE"},
            {"5m": prior, "15m": prior[:40], "1h": prior[:10]},
            {"benchmark_symbol": "WTI", "candles": []},
            {"status": "SETUP", "underlying_direction": "BEARISH"},
            target,
        )
        self.assertEqual(quality["status"], "INVALID_TARGET_SESSION_SLICE")
        self.assertEqual(quality["target_candles"], {"5m": 0, "15m": 0, "1h": 0})

    def test_data_quality_accepts_complete_target_and_comparison_sessions(self):
        target = date(2026, 8, 25)
        comparison_5m = []
        for offset in range(1, 6):
            comparison_5m.extend(_rows(target - timedelta(days=offset), 9, 120, 5))
        target_5m = _rows(target, 9, 120, 5)
        quality = _data_quality(
            "CRUDEOIL",
            {"trading_symbol": "CRUDE"},
            {
                "5m": comparison_5m + target_5m,
                "15m": _rows(target, 9, 40, 15),
                "1h": _rows(target, 9, 10, 60),
            },
            {"benchmark_symbol": "WTI", "candles": []},
            {"status": "SETUP", "underlying_direction": "BEARISH"},
            target,
        )
        self.assertEqual(quality["status"], "VALID")
        self.assertTrue(all(quality["checks"].values()))

    async def test_data_validation_route_never_generates_trade_decisions(self):
        target = date(2026, 8, 25)
        comparison_5m = []
        for offset in range(1, 6):
            comparison_5m.extend(_rows(target - timedelta(days=offset), 9, 120, 5))
        five = comparison_5m + _rows(target, 9, 120, 5)
        fifteen = _rows(target, 9, 40, 15)
        hourly = _rows(target, 9, 10, 60)
        contract = {"trading_symbol": "TESTFUT", "tick_size": 1}
        previous = {"status": "SETUP", "underlying_direction": "BEARISH"}
        with (
            patch("app.commodity_click_replay.resolve_nearest_mcx_future", new=AsyncMock(side_effect=[contract, contract])),
            patch("app.commodity_click_replay._fetch_chunked", new=AsyncMock(side_effect=[five, fifteen, hourly, five, fifteen, hourly])),
            patch("app.commodity_click_replay.build_next_session_plan", return_value=previous),
        ):
            result = await validate_frozen_tuesday_phase_a_data(object())
        self.assertEqual(result["status"], "VALID")
        self.assertFalse(result["generates_trade_decisions"])
        self.assertNotIn("decisions", result)


if __name__ == "__main__":
    unittest.main()
