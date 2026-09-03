import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_random_click_replay import (
    preregister_click_schedule,
    run_crude_random_click_replay,
)

IST=ZoneInfo("Asia/Kolkata")


def _candles(days=4):
    rows=[]
    price=6000.0
    day=datetime(2026,8,3,9,0,tzinfo=IST)
    made=0
    while made<days:
        if day.weekday()>=5:
            day += timedelta(days=1)
            continue
        for i in range(174):
            stamp=day+timedelta(minutes=5*i)
            drift=0.5 if (i//30)%2==0 else -0.4
            o=price
            c=max(100.0,price+drift+(0.08 if i%5 else -0.04))
            rows.append([stamp.isoformat(),o,max(o,c)+0.3,min(o,c)-0.3,c,1000+(i%20)*20,None])
            price=c
        made += 1
        day=(day+timedelta(days=1)).replace(hour=9,minute=0)
    return rows


class CrudeRandomClickReplayTests(unittest.TestCase):
    def test_click_schedule_is_deterministic(self):
        rows=_candles()
        a=preregister_click_schedule(rows,20)
        b=preregister_click_schedule(rows,20)
        self.assertEqual(a,b)
        self.assertEqual(len(a),80)

    def test_replay_is_no_news_and_exact_coverage(self):
        result=run_crude_random_click_replay(
            _candles(),trading_symbol="CRUDEOIL21SEP26FUT",clicks_per_session=20
        )
        self.assertFalse(result["news_enabled"])
        self.assertTrue(result["candidate_frozen_before_click_schedule"])
        self.assertTrue(result["click_schedule_outcome_blind"])
        self.assertTrue(result["coverage"]["exact_click_coverage"])
        self.assertEqual(result["coverage"]["scheduled_clicks"],80)
        self.assertEqual(result["coverage"]["evaluated_clicks"],80)
        self.assertTrue(all(x["candidate_action"] in {"BUY","NO_TRADE"} for x in result["decisions"]))

    def test_visible_bar_is_complete_at_click(self):
        result=run_crude_random_click_replay(
            _candles(),trading_symbol="CRUDEOIL21SEP26FUT",clicks_per_session=20
        )
        for row in result["decisions"]:
            self.assertLessEqual(
                datetime.fromisoformat(row["visible_bar_available_at"]),
                datetime.fromisoformat(row["click_at"]),
            )


if __name__=="__main__":
    unittest.main()
