from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.fno_underlying_random_replay_v1 import (
    CLICKS_PER_DAY,
    HORIZONS_MINUTES,
    architecture_contract,
    deterministic_clicks,
    resolve_underlying_path,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


class FnoUnderlyingRandomReplayV1Tests(unittest.TestCase):
    def test_clicks_are_deterministic_unique_and_frozen_to_window(self):
        trade_date = date(2026, 8, 31)
        first = deterministic_clicks(trade_date)
        second = deterministic_clicks(trade_date)
        self.assertEqual(first, second)
        self.assertEqual(len(first), CLICKS_PER_DAY)
        self.assertEqual(len(set(first)), CLICKS_PER_DAY)
        for click in first:
            local = click.astimezone(IST)
            self.assertEqual(local.second, 0)
            self.assertEqual(local.minute % 5, 0)
            self.assertGreaterEqual((local.hour, local.minute), (9, 30))
            self.assertLessEqual((local.hour, local.minute), (14, 0))
            self.assertLessEqual(click + timedelta(minutes=90), datetime.combine(trade_date, datetime.min.time().replace(hour=15, minute=30), tzinfo=IST).astimezone(UTC))

    def test_outcome_uses_only_bars_after_click(self):
        trade_date = date(2026, 8, 31)
        click = datetime(2026, 8, 31, 10, 0, tzinfo=IST).astimezone(UTC)
        rows = []
        # Include enough completed pre-click bars so the 09:55 candle is the
        # reference at 10:00.  Future bars then rise by one point per interval.
        start = datetime(2026, 8, 31, 9, 0, tzinfo=IST)
        current = start
        price = 100.0
        while current < datetime(2026, 8, 31, 15, 30, tzinfo=IST):
            if current < datetime(2026, 8, 31, 10, 0, tzinfo=IST):
                close = 100.0
            else:
                close = price + 1.0
                price = close
            rows.append([
                current.isoformat(),
                close,
                close + 0.25,
                close - 0.25,
                close,
                1000.0,
            ])
            current += timedelta(minutes=5)

        result = resolve_underlying_path(rows, click, "LONG")
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["reference_price"], 100.0)
        self.assertEqual(result["checkpoints"]["15m"]["end_price"], 103.0)
        self.assertEqual(result["checkpoints"]["15m"]["directional_return_pct"], 3.0)
        self.assertGreater(result["checkpoints"]["90m"]["mfe_pct"], 0)
        self.assertLess(result["checkpoints"]["90m"]["mae_pct"], result["checkpoints"]["90m"]["mfe_pct"])
        self.assertEqual(tuple(HORIZONS_MINUTES), (15, 30, 60, 90))

    def test_short_direction_inverts_raw_return(self):
        click = datetime(2026, 8, 31, 10, 0, tzinfo=IST).astimezone(UTC)
        rows = []
        current = datetime(2026, 8, 31, 9, 0, tzinfo=IST)
        while current < datetime(2026, 8, 31, 15, 30, tzinfo=IST):
            close = 100.0 if current < datetime(2026, 8, 31, 10, 0, tzinfo=IST) else 99.0
            rows.append([current.isoformat(), close, close + 0.1, close - 0.1, close, 100.0])
            current += timedelta(minutes=5)
        result = resolve_underlying_path(rows, click, "SHORT")
        block = result["checkpoints"]["15m"]
        self.assertEqual(block["raw_return_pct"], -1.0)
        self.assertEqual(block["directional_return_pct"], 1.0)

    def test_safety_contract_excludes_derivatives_and_execution(self):
        contract = architecture_contract()
        self.assertTrue(contract["underlying_only"])
        self.assertFalse(contract["option_chain_read"])
        self.assertFalse(contract["option_premium_read"])
        self.assertFalse(contract["option_oi_iv_greeks_read"])
        self.assertFalse(contract["futures_generation"])
        self.assertFalse(contract["broker_orders"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)
        self.assertFalse(contract["strategy_policy_changed"])


if __name__ == "__main__":
    unittest.main()
