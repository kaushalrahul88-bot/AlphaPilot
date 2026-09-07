import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_long_only_stability import run_long_only_day_stability

IST = ZoneInfo("Asia/Kolkata")


def _candles(days=4, bars_per_day=90):
    rows=[]
    price=6000.0
    day=datetime(2026,8,3,9,0,tzinfo=IST)
    made=0
    while made<days:
        if day.weekday()>=5:
            day += timedelta(days=1)
            continue
        for i in range(bars_per_day):
            stamp=day+timedelta(minutes=5*i)
            drift=0.6 if (i//20)%2==0 else -0.5
            o=price
            c=max(100.0,price+drift+(0.1 if i%3 else -0.05))
            rows.append([stamp.isoformat(),o,max(o,c)+0.4,min(o,c)-0.4,c,1000+(i%15)*20,None])
            price=c
        made += 1
        day=(day+timedelta(days=1)).replace(hour=9,minute=0)
    return rows


class CrudeLongOnlyStabilityTests(unittest.TestCase):
    def test_stability_audit_is_frozen_and_no_news(self):
        result=run_long_only_day_stability(
            _candles(),trading_symbol="CRUDEOIL21SEP26FUT",sample_every_bars=3
        )
        self.assertEqual(result["mode"],"ALPHAPILOT_CRUDE_LONG_ONLY_DAY_STABILITY_V1")
        self.assertTrue(result["candidate_frozen_before_this_audit"])
        self.assertFalse(result["candidate_rule_changed"])
        self.assertFalse(result["news_enabled"])
        self.assertGreater(result["coverage"]["sessions"],0)
        self.assertEqual(result["coverage"]["sessions"],len(result["sessions"]))


if __name__=="__main__":
    unittest.main()
