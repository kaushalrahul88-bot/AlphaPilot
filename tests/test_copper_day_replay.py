import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.copper_day_replay import (
    DAILY_STARTING_CAPITAL,
    DAILY_TARGET_PROFIT,
    RISK_PER_TRADE_RUPEES,
    deterministic_click_times,
    replay_contract_rows,
)


IST = ZoneInfo("Asia/Kolkata")


def _rows(days=4, bars_per_day=120):
    rows = []
    base_day = datetime(2026, 8, 24, 9, 0, tzinfo=IST)
    price = 900.0
    for d in range(days):
        day_start = base_day + timedelta(days=d)
        for i in range(bars_per_day):
            # Smooth trend with mild intraday oscillation; enough history for analysis.
            drift = 0.05 if d % 2 == 0 else -0.04
            price += drift + (0.02 if i % 7 < 4 else -0.01)
            rows.append([
                (day_start + timedelta(minutes=5 * i)).isoformat(),
                price - 0.05,
                price + 0.20,
                price - 0.20,
                price,
                1000 + i * 3,
                50000 + i,
            ])
    return rows


class CopperDayReplayTests(unittest.TestCase):
    def test_click_schedule_is_reproducible_and_in_window(self):
        day = datetime(2026, 8, 28, tzinfo=IST).date()
        first = deterministic_click_times(day)
        second = deterministic_click_times(day)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 10)
        self.assertEqual(len(set(first)), 10)
        self.assertTrue(all((t.hour, t.minute) >= (10, 0) for t in first))
        self.assertTrue(all((t.hour, t.minute) <= (22, 0) for t in first))

    def test_daily_capital_resets_and_monthly_summary_exists(self):
        result = replay_contract_rows(_rows(), {"trading_symbol": "COPPERTESTFUT"})
        self.assertEqual(result["capital_model"]["starting_capital_each_day_rupees"], DAILY_STARTING_CAPITAL)
        self.assertEqual(result["capital_model"]["daily_target_profit_rupees"], DAILY_TARGET_PROFIT)
        self.assertEqual(result["capital_model"]["planned_risk_per_trade_rupees"], RISK_PER_TRADE_RUPEES)
        self.assertGreaterEqual(len(result["daily_results"]), 4)
        self.assertTrue(result["monthly_summary"])
        for day in result["daily_results"]:
            self.assertEqual(day["starting_capital_rupees"], DAILY_STARTING_CAPITAL)
            self.assertAlmostEqual(
                day["ending_capital_rupees"],
                DAILY_STARTING_CAPITAL + day["net_pnl_rupees"],
                places=2,
            )
            self.assertEqual(day["target_3000_achieved"], day["net_pnl_rupees"] >= DAILY_TARGET_PROFIT)

    def test_replay_never_uses_more_than_ten_click_decisions_per_day(self):
        result = replay_contract_rows(_rows(days=2), {"trading_symbol": "COPPERTESTFUT"})
        for day in result["daily_results"]:
            self.assertEqual(len(day["click_times_ist"]), 10)
            self.assertLessEqual(day["decisions"], 10)

    def test_margin_is_explicitly_not_modelled(self):
        result = replay_contract_rows(_rows(days=1), {"trading_symbol": "COPPERTESTFUT"})
        self.assertFalse(result["capital_model"]["broker_margin_feasibility_modelled"])
        self.assertTrue(result["research_only"])
        self.assertFalse(result["production_rules_changed"])


if __name__ == "__main__":
    unittest.main()
