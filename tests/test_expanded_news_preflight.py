import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from scripts.build_expanded_68_news_payload import next_session_open

class ExpandedNewsPreflightTests(unittest.TestCase):
    def test_date_only_next_session_is_nine_am_ist(self):
        self.assertEqual(next_session_open("2026-08-06"), "2026-08-07T09:00:00+05:30")

    def test_friday_date_only_skips_weekend_in_ist(self):
        self.assertEqual(next_session_open("2026-08-07"), "2026-08-10T09:00:00+05:30")

    def test_timestamp_is_timezone_aware(self):
        dt=datetime.fromisoformat(next_session_open("2026-08-19"))
        self.assertIsNotNone(dt.tzinfo)
        self.assertEqual(dt.utcoffset().total_seconds(), 19800)

if __name__=="__main__":
    unittest.main()
