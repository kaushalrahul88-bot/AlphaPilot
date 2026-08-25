import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.session_close_momentum import (
    _session_variants,
    _simulate_close_window,
    run_session_close_momentum,
)


IST = ZoneInfo("Asia/Kolkata")


class EmptyProvider:
    async def historical_candles(self, symbol, timeframe, start, end):
        return []


def session_rows(opening_direction=1, late_direction=1):
    rows = []
    stamp = datetime(2026, 4, 13, 9, 15, tzinfo=IST)
    price = 100.0
    while stamp.time() <= datetime(2026, 4, 13, 15, 25, tzinfo=IST).time():
        move = 0.02
        if stamp.time() < datetime(2026, 4, 13, 9, 45, tzinfo=IST).time():
            move = 0.10 * opening_direction
        elif stamp.time() >= datetime(2026, 4, 13, 15, 0, tzinfo=IST).time():
            move = 0.12 * late_direction
        row_open = price
        close = price + move
        rows.append([stamp.isoformat(), row_open, max(row_open, close) + 0.02, min(row_open, close) - 0.02, close, 1000])
        price = close
        stamp += timedelta(minutes=5)
    return rows


class SessionCloseMomentumTests(unittest.IsolatedAsyncioTestCase):
    def test_variants_use_frozen_session_times_without_lookahead(self):
        rows = session_rows(opening_direction=1, late_direction=1)
        signal = _session_variants(rows, list(range(len(rows))))
        self.assertIsNotNone(signal)
        directions, features = signal
        self.assertEqual(directions["OPENING_SIGN"], "LONG")
        self.assertEqual(directions["PRE_CLOSE_SIGN"], "LONG")
        self.assertEqual(directions["OPENING_PRE_CLOSE_AGREEMENT"], "LONG")
        self.assertEqual(datetime.fromisoformat(rows[features["entry_index"]][0]).time().isoformat(), "15:00:00")

    def test_close_window_applies_cost_and_never_enables_execution(self):
        rows = session_rows(opening_direction=1, late_direction=1)
        indices = list(range(len(rows)))
        signal = _session_variants(rows, indices)
        _, features = signal
        result = _simulate_close_window(rows, indices, features["entry_index"], features["exit_index"], "LONG", 1.0)
        self.assertIsNotNone(result)
        self.assertLess(result["net_r"], result["gross_r"])

    async def test_protocol_metadata_and_empty_result_are_frozen(self):
        result = await run_session_close_momentum(EmptyProvider(), "2026-04-13", "2026-04-24")
        self.assertEqual(result["mode"], "ALPHAPILOT_SESSION_CLOSE_MOMENTUM_V1")
        self.assertEqual(result["symbols"], ["NIFTY", "BANKNIFTY"])
        self.assertEqual(len(result["summaries"]), 3)
        self.assertEqual(result["fixed_protocol"]["replication_blocks_required"], 4)
        self.assertFalse(result["production_rules_changed"])
        self.assertFalse(result["paper_trading_permission_changed"])
        self.assertFalse(result["live_execution_enabled"])

    async def test_rejects_dates_outside_frozen_development_book(self):
        with self.assertRaisesRegex(ValueError, "frozen"):
            await run_session_close_momentum(EmptyProvider(), "2026-08-11", "2026-08-21")

    async def test_rejects_blocks_longer_than_twelve_days(self):
        with self.assertRaisesRegex(ValueError, "12 calendar days"):
            await run_session_close_momentum(EmptyProvider(), "2026-04-13", "2026-04-27")
