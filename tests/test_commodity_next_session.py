import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.commodity_next_session import build_next_session_plan, score_next_session
from app.commodities import MCX_TICK_SIZE_RUPEES


IST = ZoneInfo("Asia/Kolkata")


def make_session(day, direction=1, start=100.0, late_boost=True):
    rows = []
    stamp = datetime.combine(day, datetime.min.time(), tzinfo=IST).replace(hour=9)
    price = start
    while stamp.time() <= datetime.min.time().replace(hour=23, minute=30):
        move = 0.015 * direction
        if late_boost and stamp.time() >= datetime.min.time().replace(hour=22, minute=30):
            move = 0.04 * direction
        row_open = price
        close = price + move
        rows.append([stamp.isoformat(), row_open, max(row_open, close) + 0.01, min(row_open, close) - 0.01, close, 1000])
        price = close
        stamp += timedelta(minutes=5)
    return rows


class CommodityNextSessionTests(unittest.TestCase):
    def test_mcx_tick_sizes_are_normalized_to_rupees(self):
        self.assertEqual(MCX_TICK_SIZE_RUPEES["CRUDEOIL"], 1.0)
        self.assertEqual(MCX_TICK_SIZE_RUPEES["NATURALGAS"], 0.10)

    def test_completed_session_builds_next_day_buy_breakout(self):
        monday = date(2026, 8, 24)
        rows = make_session(date(2026, 8, 21), direction=1, start=95.0)
        rows += make_session(monday, direction=1, start=100.0)
        plan = build_next_session_plan("CRUDEOIL", rows, monday, date(2026, 8, 25), 0.1)
        self.assertEqual(plan["status"], "SETUP")
        self.assertEqual(plan["action"], "BUY")
        self.assertGreaterEqual(plan["features"]["directional_score"], 3)
        self.assertEqual(plan["risk_reward"], 1.5)
        self.assertGreater(plan["entry"], plan["features"]["session_high"])
        self.assertAlmostEqual(plan["entry"] / 0.1, round(plan["entry"] / 0.1))
        self.assertAlmostEqual(plan["stop_loss"] / 0.1, round(plan["stop_loss"] / 0.1))
        self.assertAlmostEqual(plan["target1"] / 0.1, round(plan["target1"] / 0.1))

    def test_mixed_session_returns_no_trade(self):
        monday = date(2026, 8, 24)
        rows = []
        for session_date in (date(2026, 8, 21), monday):
            stamp = datetime.combine(session_date, datetime.min.time(), tzinfo=IST).replace(hour=9)
            while stamp.time() <= datetime.min.time().replace(hour=23, minute=30):
                rows.append([stamp.isoformat(), 100.0, 100.1, 99.9, 100.0, 1000])
                stamp += timedelta(minutes=5)
        plan = build_next_session_plan("NATURALGAS", rows, monday, date(2026, 8, 25), 0.1)
        self.assertEqual(plan["status"], "NO_TRADE")
        self.assertEqual(plan["action"], "NO TRADE")
        self.assertEqual(plan["features"]["directional_score"], 0)

    def test_target_is_scored_only_after_entry(self):
        plan = {"status": "SETUP", "action": "BUY", "entry": 101.0, "stop_loss": 100.0, "target1": 102.5}
        stamp = datetime(2026, 8, 25, 9, 0, tzinfo=IST)
        rows = [
            [stamp.isoformat(), 100.0, 100.8, 99.8, 100.5, 100],
            [(stamp + timedelta(minutes=5)).isoformat(), 100.5, 101.2, 100.4, 101.1, 100],
            [(stamp + timedelta(minutes=10)).isoformat(), 101.1, 102.6, 101.0, 102.5, 100],
        ]
        result = score_next_session(plan, rows)
        self.assertEqual(result["outcome"], "TARGET_HIT")
        self.assertLess(result["r_multiple"], 1.5)

    def test_same_entry_bar_stop_or_target_is_ambiguous(self):
        plan = {"status": "SETUP", "action": "BUY", "entry": 101.0, "stop_loss": 100.0, "target1": 102.5}
        row = ["2026-08-25T09:00:00+05:30", 100.5, 102.6, 99.9, 101.5, 100]
        result = score_next_session(plan, [row])
        self.assertEqual(result["outcome"], "AMBIGUOUS_ENTRY_BAR")

    def test_no_trigger_is_not_counted_as_loss(self):
        plan = {"status": "SETUP", "action": "SELL", "entry": 99.0, "stop_loss": 100.0, "target1": 97.5}
        row = ["2026-08-25T23:30:00+05:30", 100.5, 101.0, 99.5, 100.0, 100]
        result = score_next_session(plan, [row])
        self.assertEqual(result["outcome"], "NO_ENTRY")
        self.assertEqual(result["r_multiple"], 0.0)


if __name__ == "__main__":
    unittest.main()
