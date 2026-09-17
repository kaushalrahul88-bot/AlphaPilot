import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_research_brain import (
    brain_a_signal,
    build_crude_snapshot,
    experiment_manifest,
    run_crude_experiment_001,
)


IST = ZoneInfo("Asia/Kolkata")


def _candles(count=140, start=None, drift=0.7):
    start = start or datetime(2026, 8, 18, 9, 0, tzinfo=IST)
    rows = []
    price = 6000.0
    for i in range(count):
        stamp = start + timedelta(minutes=5 * i)
        # Alternating noise with a positive drift gives non-degenerate structure.
        move = drift + (0.15 if i % 3 else -0.05)
        open_price = price
        close = price + move
        high = max(open_price, close) + 0.4
        low = min(open_price, close) - 0.4
        rows.append([stamp.isoformat(), open_price, high, low, close, 1000 + i * 3, 50000 + i * 5])
        price = close
    return rows


class CrudeResearchBrainTests(unittest.TestCase):
    def test_manifest_forbids_news_in_baseline(self):
        manifest = experiment_manifest()
        self.assertFalse(manifest["news_enabled"])
        self.assertEqual(manifest["news_policy"], "FORBIDDEN_IN_BASELINE")
        self.assertIn("FREEZE_NO_NEWS_BASELINE", manifest["path"])
        self.assertIn("ADD_POINT_IN_TIME_NEWS_INTELLIGENCE", manifest["path"])

    def test_snapshot_uses_bar_completion_availability(self):
        rows = _candles()
        snapshot = build_crude_snapshot(rows, 60)
        bar_start = datetime.fromisoformat(snapshot["bar_start"])
        available = datetime.fromisoformat(snapshot["available_at"])
        self.assertEqual(available - bar_start, timedelta(minutes=5))

    def test_future_candle_changes_do_not_change_same_snapshot(self):
        rows_a = _candles()
        rows_b = [list(row) for row in rows_a]
        # Mutate only future data after the tested snapshot.
        for row in rows_b[61:]:
            row[1] *= 1.20
            row[2] *= 1.20
            row[3] *= 1.20
            row[4] *= 1.20
        self.assertEqual(build_crude_snapshot(rows_a, 60), build_crude_snapshot(rows_b, 60))

    def test_duplicate_timestamp_fails_closed(self):
        rows = _candles()
        rows.insert(30, list(rows[30]))
        with self.assertRaisesRegex(ValueError, "Duplicate Crude candle timestamp"):
            run_crude_experiment_001(rows, trading_symbol="CRUDEOIL21SEP26FUT")

    def test_brain_a_rule_is_technical_only(self):
        bullish = {
            "structure": "UPTREND",
            "return_15m_pct": 0.2,
            "ema20_gap_pct": 0.3,
            "ema50_gap_pct": 0.5,
            "headline": "ignored by design",
            "news_effect": "BEARISH",
        }
        self.assertEqual(brain_a_signal(bullish), "BUY")
        bearish = {
            "structure": "DOWNTREND",
            "return_15m_pct": -0.2,
            "ema20_gap_pct": -0.3,
            "ema50_gap_pct": -0.5,
            "news_effect": "BULLISH",
        }
        self.assertEqual(brain_a_signal(bearish), "SELL")

    def test_experiment_001_is_no_news_and_chronological(self):
        result = run_crude_experiment_001(
            _candles(220),
            trading_symbol="CRUDEOIL21SEP26FUT",
            sample_every_bars=3,
            round_trip_cost_bps=4.0,
        )
        self.assertEqual(result["mode"], "ALPHAPILOT_CRUDE_EXPERIMENT_001")
        self.assertFalse(result["news_enabled"])
        self.assertFalse(result["production_rules_changed"])
        self.assertFalse(result["live_execution_enabled"])
        split = result["chronological_split"]
        self.assertGreater(split["train_experiences"], 0)
        self.assertGreater(split["holdout_experiences"], 0)
        self.assertEqual(
            split["train_experiences"] + split["holdout_experiences"],
            result["coverage"]["experiences"],
        )
        self.assertFalse(result["full_sample"]["news_enabled"])


if __name__ == "__main__":
    unittest.main()
