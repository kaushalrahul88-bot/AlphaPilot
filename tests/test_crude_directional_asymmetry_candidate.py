import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_directional_asymmetry_candidate import (
    CANDIDATE_ID,
    long_only_shadow_signal,
    validate_long_only_shadow,
)

IST = ZoneInfo("Asia/Kolkata")


def _candles(count=260):
    start = datetime(2026, 8, 3, 9, 0, tzinfo=IST)
    rows = []
    price = 6000.0
    for i in range(count):
        stamp = start + timedelta(minutes=5 * i)
        drift = 0.7 if (i // 30) % 2 == 0 else -0.6
        open_price = price
        close = max(100.0, price + drift + (0.1 if i % 4 else -0.05))
        rows.append([
            stamp.isoformat(), open_price,
            max(open_price, close) + 0.4,
            min(open_price, close) - 0.4,
            close, 1000 + (i % 20) * 25, None,
        ])
        price = close
    return rows


class CrudeDirectionalAsymmetryCandidateTests(unittest.TestCase):
    def test_shadow_never_creates_or_reverses(self):
        buy = {"structure":"UPTREND","return_15m_pct":0.2,"ema20_gap_pct":0.2,"ema50_gap_pct":0.3}
        sell = {"structure":"DOWNTREND","return_15m_pct":-0.2,"ema20_gap_pct":-0.2,"ema50_gap_pct":-0.3}
        neutral = {"structure":"RANGE","return_15m_pct":0.2,"ema20_gap_pct":0.2,"ema50_gap_pct":0.3}
        self.assertEqual(long_only_shadow_signal(buy), "BUY")
        self.assertEqual(long_only_shadow_signal(sell), "NO_TRADE")
        self.assertEqual(long_only_shadow_signal(neutral), "NO_TRADE")

    def test_news_fields_are_irrelevant(self):
        features = {
            "structure":"UPTREND","return_15m_pct":0.2,
            "ema20_gap_pct":0.2,"ema50_gap_pct":0.3,
            "news_effect":"BEARISH","headline":"must not vote",
        }
        self.assertEqual(long_only_shadow_signal(features), "BUY")

    def test_validation_marks_rule_as_preregistered(self):
        result = validate_long_only_shadow(
            _candles(),
            trading_symbol="CRUDEOIL21SEP26FUT",
            validation_window={"start":"2026-08-03","end":"2026-08-14"},
        )
        self.assertEqual(result["candidate"]["id"], CANDIDATE_ID)
        self.assertFalse(result["candidate"]["validation_outcomes_used_to_choose_rule"])
        self.assertFalse(result["news_enabled"])
        self.assertEqual(result["validation_type"], "INDEPENDENT_EARLIER_HISTORICAL_WINDOW_NOT_FORWARD")


if __name__ == "__main__":
    unittest.main()
