import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.mcx_calendar import mcx_metal_day_schedule, mcx_metal_reaction_anchor, mcx_metal_session_status

IST=ZoneInfo("Asia/Kolkata")


class McxCalendarTests(unittest.TestCase):
    def test_august_2026_weekday_is_full_session(self):
        d=mcx_metal_day_schedule(date(2026,8,28))
        self.assertEqual(d["calendar_class"],"REGULAR_WEEKDAY")
        self.assertTrue(d["expected_open"])
        self.assertEqual(d["expected_5m_bars"],174)

    def test_august_2026_weekend_is_closed(self):
        d=mcx_metal_day_schedule(date(2026,8,29))
        self.assertEqual(d["calendar_class"],"WEEKEND")
        self.assertFalse(d["expected_open"])
        self.assertEqual(d["expected_5m_bars"],0)

    def test_partial_trading_holiday_is_identified(self):
        d=mcx_metal_day_schedule(date(2026,9,14))
        self.assertEqual(d["holiday_name"],"GANESH CHATURTHI")
        self.assertFalse(d["morning_open"])
        self.assertTrue(d["evening_open"])
        self.assertGreater(d["expected_5m_bars"],0)

    def test_full_trading_holiday_is_closed(self):
        d=mcx_metal_day_schedule(date(2026,10,2))
        self.assertEqual(d["holiday_name"],"MAHATMA GANDHI JAYANTI")
        self.assertFalse(d["expected_open"])
        self.assertEqual(d["expected_5m_bars"],0)

    def test_session_status_uses_exchange_calendar(self):
        closed=mcx_metal_session_status(datetime(2026,10,2,18,0,tzinfo=IST))
        self.assertFalse(closed["is_open"])
        open_day=mcx_metal_session_status(datetime(2026,8,28,18,0,tzinfo=IST))
        self.assertTrue(open_day["is_open"])

    def test_in_session_reaction_anchor_preserves_event_time(self):
        event=datetime(2026,8,7,14,59,tzinfo=IST)
        self.assertEqual(mcx_metal_reaction_anchor(event),event)

    def test_weekend_reaction_anchor_moves_to_monday_open(self):
        event=datetime(2026,8,8,9,45,tzinfo=IST)
        self.assertEqual(mcx_metal_reaction_anchor(event),datetime(2026,8,10,9,0,tzinfo=IST))

    def test_partial_holiday_reaction_anchor_moves_to_evening_open(self):
        event=datetime(2026,9,14,10,0,tzinfo=IST)
        self.assertEqual(mcx_metal_reaction_anchor(event),datetime(2026,9,14,17,0,tzinfo=IST))

    def test_full_holiday_reaction_anchor_skips_weekend(self):
        event=datetime(2026,10,2,10,0,tzinfo=IST)
        self.assertEqual(mcx_metal_reaction_anchor(event),datetime(2026,10,5,9,0,tzinfo=IST))


if __name__=="__main__":
    unittest.main()
