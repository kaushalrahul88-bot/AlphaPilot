import unittest
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.commodity_click_brain import _timestamp, _valid_rows, evaluate_commodity_click, premium_plan


IST = ZoneInfo("Asia/Kolkata")


def session(day, bullish=True, volume=200.0):
    rows = []
    stamp = datetime.combine(day, time(9, 0), tzinfo=IST)
    price = 100.0
    while stamp.time() <= time(10, 15):
        if stamp.time() < time(10, 0):
            close = price + (0.02 if bullish else -0.02)
            high = max(price, close) + 0.08
            low = min(price, close) - 0.08
        else:
            move = 0.35 if bullish else -0.35
            close = price + move
            high = max(price, close) + 0.10
            low = min(price, close) - 0.10
        rows.append([stamp.isoformat(), price, high, low, close, volume])
        price = close
        stamp += timedelta(minutes=5)
    return rows


def history(through_day, bullish=True, volume=100.0):
    output = []
    for offset in range(1, 6):
        output += session(through_day - timedelta(days=offset), bullish, volume)
    return output


def prior(direction):
    return {"status": "SETUP", "underlying_direction": direction}


def mtf(action):
    return {"action": action, "alpha_score": 76.0, "fresh_market_data": True}


def benchmark(symbol, direction, click):
    return {"symbol": symbol, "direction": direction, "fresh": True, "as_of": (click - timedelta(minutes=1)).isoformat()}


class CommodityClickBrainTests(unittest.TestCase):
    def test_groww_epoch_seconds_milliseconds_numeric_string_and_iso_match(self):
        expected = "2026-08-25T09:00:00+05:30"
        seconds = 1787628600
        values = [seconds, seconds * 1000, str(seconds), expected]
        self.assertEqual([_timestamp(value).isoformat() for value in values], [expected] * 4)

    def test_valid_rows_accept_groww_epoch_timestamp(self):
        row = [1787628600, 8080, 8090, 8070, 8085, 1000]
        result = _valid_rows([row])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0].isoformat(), "2026-08-25T09:00:00+05:30")

    def test_bullish_click_returns_buy_ce_with_1_5r_premium_plan(self):
        click = datetime(2026, 8, 25, 10, 15, tzinfo=IST)
        result = evaluate_commodity_click(
            "CRUDEOIL", click, prior("BULLISH"), mtf("BUY"), session(click.date()),
            history(click.date()), benchmark("WTI", "BULLISH", click), option_premium=100.0,
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["action"], "BUY CE")
        self.assertEqual(result["premium_setup"]["stop_loss"], 80.0)
        self.assertEqual(result["premium_setup"]["target"], 130.0)
        self.assertEqual(result["premium_setup"]["risk_reward"], 1.5)

    def test_bearish_click_returns_buy_pe_not_sell(self):
        click = datetime(2026, 8, 25, 10, 15, tzinfo=IST)
        result = evaluate_commodity_click(
            "NATURALGAS", click, prior("BEARISH"), mtf("SELL"), session(click.date(), False),
            history(click.date(), False), benchmark("HENRY_HUB", "BEARISH", click), option_premium=20.0,
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["action"], "BUY PE")
        self.assertEqual(result["premium_setup"]["stop_loss"], 15.0)
        self.assertEqual(result["premium_setup"]["target"], 27.5)

    def test_previous_and_current_direction_must_agree(self):
        click = datetime(2026, 8, 25, 10, 15, tzinfo=IST)
        result = evaluate_commodity_click(
            "CRUDEOIL", click, prior("BULLISH"), mtf("SELL"), session(click.date()),
            history(click.date()), benchmark("WTI", "BULLISH", click), option_premium=100.0,
        )
        self.assertEqual(result["status"], "NO_TRADE")
        self.assertIn("previous_current_alignment", result["blockers"])

    def test_click_before_opening_range_completion_waits(self):
        click = datetime(2026, 8, 25, 9, 35, tzinfo=IST)
        result = evaluate_commodity_click(
            "CRUDEOIL", click, prior("BULLISH"), mtf("BUY"), session(click.date()),
            history(click.date()), benchmark("WTI", "BULLISH", click), option_premium=100.0,
        )
        self.assertEqual(result["status"], "WAIT")
        self.assertIn("opening_range", result["blockers"])

    def test_future_candles_are_never_used(self):
        click = datetime(2026, 8, 25, 10, 15, tzinfo=IST)
        rows = session(click.date())
        base = evaluate_commodity_click(
            "CRUDEOIL", click, prior("BULLISH"), mtf("BUY"), rows,
            history(click.date()), benchmark("WTI", "BULLISH", click), option_premium=100.0,
        )
        rows.append([datetime(2026, 8, 25, 18, 0, tzinfo=IST).isoformat(), 1, 9999, 1, 1, 99999999])
        repeated = evaluate_commodity_click(
            "CRUDEOIL", click, prior("BULLISH"), mtf("BUY"), rows,
            history(click.date()), benchmark("WTI", "BULLISH", click), option_premium=100.0,
        )
        self.assertEqual(base, repeated)

    def test_missing_global_benchmark_blocks_setup(self):
        click = datetime(2026, 8, 25, 10, 15, tzinfo=IST)
        result = evaluate_commodity_click(
            "CRUDEOIL", click, prior("BULLISH"), mtf("BUY"), session(click.date()),
            history(click.date()), None, option_premium=100.0,
        )
        self.assertEqual(result["status"], "NO_TRADE")
        self.assertIn("global_benchmark", result["blockers"])

    def test_low_relative_volume_blocks_setup(self):
        click = datetime(2026, 8, 25, 10, 15, tzinfo=IST)
        result = evaluate_commodity_click(
            "CRUDEOIL", click, prior("BULLISH"), mtf("BUY"), session(click.date(), volume=50.0),
            history(click.date(), volume=100.0), benchmark("WTI", "BULLISH", click), option_premium=100.0,
        )
        self.assertEqual(result["status"], "NO_TRADE")
        self.assertIn("time_adjusted_relative_volume", result["blockers"])

    def test_premium_risk_bands_remain_deterministic(self):
        self.assertEqual(premium_plan(5.0)["risk_percent"], 30.0)
        self.assertEqual(premium_plan(20.0)["risk_percent"], 25.0)
        self.assertEqual(premium_plan(50.0)["risk_percent"], 20.0)

    def test_phase_a_can_bypass_unavailable_premium_without_claiming_a_plan(self):
        click = datetime(2026, 8, 25, 10, 15, tzinfo=IST)
        result = evaluate_commodity_click(
            "CRUDEOIL", click, prior("BULLISH"), mtf("BUY"), session(click.date()),
            history(click.date()), benchmark("WTI", "BULLISH", click), option_premium=None,
            require_option_premium=False,
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["action"], "BUY CE")
        self.assertIsNone(result["premium_setup"])
        self.assertFalse(result["gates"]["option_premium"]["required"])


if __name__ == "__main__":
    unittest.main()
