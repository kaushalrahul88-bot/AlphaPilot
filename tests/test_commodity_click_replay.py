import unittest

from app.commodity_click_replay import CLICK_TIMES, _summary


class CommodityClickReplayTests(unittest.TestCase):
    def test_frozen_click_times_are_unchanged(self):
        self.assertEqual(CLICK_TIMES, ("09:35", "10:55", "11:05", "13:20", "13:35", "15:15", "15:25", "16:15", "16:40", "18:35"))

    def test_summary_does_not_present_overlapping_clicks_as_additive_pnl(self):
        decisions = [
            {"status": "READY", "outcome": {"r_multiple": 1.4}},
            {"status": "READY", "outcome": {"r_multiple": -1.0}},
            {"status": "WAIT", "outcome": None},
            {"status": "NO_TRADE", "outcome": None},
        ]
        result = _summary(decisions)
        self.assertEqual(result["ready_setups"], 2)
        self.assertEqual(result["average_resolved_r_proxy"], 0.2)
        self.assertNotIn("total_r", result)
        self.assertTrue(result["non_additive"])


if __name__ == "__main__":
    unittest.main()
