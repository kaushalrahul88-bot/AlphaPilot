import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.copper_market_brain_direction_audit import (
    _outcome,
    _same_session_future,
    evaluate_market_brain_direction,
)

IST=ZoneInfo("Asia/Kolkata")


def make_rows(day="2026-08-24", count=174, start_price=1380.0, drift=0.02):
    start=datetime.fromisoformat(day+"T09:00:00+05:30")
    rows=[]
    price=start_price
    for i in range(count):
        stamp=start+timedelta(minutes=5*i)
        open_price=price
        close=price+drift
        high=max(open_price,close)+0.05
        low=min(open_price,close)-0.05
        rows.append([stamp.isoformat(),open_price,high,low,close,100+i,None])
        price=close
    return rows


class CopperMarketBrainDirectionAuditTests(unittest.TestCase):
    def test_forward_horizon_cannot_cross_session_boundary(self):
        friday=make_rows("2026-08-28",count=174)
        monday=make_rows("2026-08-31",count=20,start_price=1390)
        rows=friday+monday
        self.assertIsNone(_same_session_future(rows,170,12))

    def test_buy_and_sell_outcomes_map_to_option_side_intent(self):
        rows=make_rows(count=20,drift=0.10)
        buy=_outcome(rows,0,"BUY",30)
        sell=_outcome(rows,0,"SELL",30)
        self.assertEqual(buy["option_side_intent"],"CE")
        self.assertEqual(sell["option_side_intent"],"PE")
        self.assertGreater(buy["signed_forward_pct"],0)
        self.assertLess(sell["signed_forward_pct"],0)

    def test_primary_report_is_options_context_not_futures_pnl(self):
        rows=[]
        for day in ("2026-08-24","2026-08-25","2026-08-26"):
            rows.extend(make_rows(day,count=174,drift=0.03))
        result=evaluate_market_brain_direction(rows,sample_every_bars=3)
        self.assertEqual(result["trade_instrument"],"OPTIONS")
        self.assertEqual(result["underlying_reference_role"],"REFERENCE_ONLY")
        self.assertFalse(result["futures_pnl_calculated"])
        self.assertFalse(result["synthetic_option_premium_used"])
        self.assertTrue(result["same_session_only"])
        self.assertEqual(result["sample_interval_minutes"],15)
        self.assertTrue(result["primary_score_days"])

    def test_sparse_session_is_excluded_from_primary_score(self):
        rows=make_rows("2026-08-27",count=149,drift=0.03)
        result=evaluate_market_brain_direction(rows,sample_every_bars=3)
        self.assertIn("2026-08-27",result["excluded_partial_days"])
        self.assertNotIn("2026-08-27",result["primary_score_days"])


if __name__=="__main__":
    unittest.main()
