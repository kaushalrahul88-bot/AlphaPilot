import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.mcx_calendar import mcx_metal_day_schedule, mcx_metal_session_status

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


if __name__=="__main__":
    unittest.main()
