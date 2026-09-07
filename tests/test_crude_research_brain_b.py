import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_research_brain_b import (
    BRAIN_B_CONFIG,
    brain_b_signal,
    compare_crude_brains_a_b,
)

IST = ZoneInfo("Asia/Kolkata")


def _candles(count=260):
    start = datetime(2026, 8, 17, 9, 0, tzinfo=IST)
    rows = []
    price = 6000.0
    for i in range(count):
        stamp = start + timedelta(minutes=5 * i)
        cycle = (i // 30) % 2
        drift = 0.9 if cycle == 0 else -0.8
        noise = 0.2 if i % 4 else -0.1
        open_price = price
        close = max(100.0, price + drift + noise)
        high = max(open_price, close) + 0.5
        low = min(open_price, close) - 0.5
        volume = 900 + (i % 20) * 30
        oi = 50000 + i * 5
        rows.append([stamp.isoformat(), open_price, high, low, close, volume, oi])
        price = close
    return rows


class CrudeBrainBTests(unittest.TestCase):
    def test_config_is_frozen_starting_candidate(self):
        self.assertEqual(BRAIN_B_CONFIG["min_relative_volume"], 0.90)
        self.assertEqual(BRAIN_B_CONFIG["max_atr_pct"], 0.65)
        self.assertEqual(BRAIN_B_CONFIG["min_abs_return_15m_pct"], 0.02)
        self.assertTrue(BRAIN_B_CONFIG["oi_confirmation"])

    def test_news_fields_cannot_override_brain_b(self):
        features = {
            "structure": "UPTREND",
            "return_15m_pct": 0.20,
            "ema20_gap_pct": 0.30,
            "ema50_gap_pct": 0.50,
            "relative_volume": 1.20,
            "atr_pct": 0.20,
            "oi_change_15m_pct": 0.10,
            "news_effect": "BEARISH",
        }
        self.assertEqual(brain_b_signal(features), "BUY")

    def test_low_participation_blocks_base_signal(self):
        features = {
            "structure": "UPTREND",
            "return_15m_pct": 0.20,
            "ema20_gap_pct": 0.30,
            "ema50_gap_pct": 0.50,
            "relative_volume": 0.50,
            "atr_pct": 0.20,
            "oi_change_15m_pct": 0.10,
        }
        self.assertEqual(brain_b_signal(features), "NO_TRADE")

    def test_comparison_keeps_news_disabled(self):
        result = compare_crude_brains_a_b(
            _candles(),
            trading_symbol="CRUDEOIL21SEP26FUT",
            sample_every_bars=3,
            round_trip_cost_bps=4.0,
        )
        self.assertEqual(result["mode"], "ALPHAPILOT_CRUDE_EXPERIMENT_002")
        self.assertFalse(result["news_enabled"])
        self.assertTrue(result["baseline_frozen_before_brain_b"])
        self.assertFalse(result["brain_b_config_selected_from_crude_outcomes"])
        self.assertIn("brain_b_promoted", result["gate"])


if __name__ == "__main__":
    unittest.main()
