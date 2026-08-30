import unittest
from datetime import datetime,timedelta
from app.copper_event_path_backtest import _event_path

def rows(prices,day="2026-08-24"):
    start=datetime.fromisoformat(day+"T10:00:00+05:30")
    out=[]
    for i,(o,h,l,c) in enumerate(prices):
        out.append([(start+timedelta(minutes=5*i)).isoformat(),o,h,l,c,100])
    return out

class EventPathTests(unittest.TestCase):
    def test_buy_target_first(self):
        r=rows([(100,100.05,99.98,100),(100,100.25,99.99,100.2)])
        x=_event_path(r,0,"BUY",0.20,0.15)
        self.assertEqual(x["outcome"],"TARGET_FIRST")
        self.assertEqual(x["option_side_intent"],"CE")
        self.assertEqual(x["minutes_to_event"],5)

    def test_sell_target_first(self):
        r=rows([(100,100.02,99.98,100),(100,100.01,99.75,99.8)])
        x=_event_path(r,0,"SELL",0.20,0.15)
        self.assertEqual(x["outcome"],"TARGET_FIRST")
        self.assertEqual(x["option_side_intent"],"PE")

    def test_same_bar_ambiguity_is_conservative_stop(self):
        r=rows([(100,100.02,99.98,100),(100,100.25,99.80,100.1)])
        x=_event_path(r,0,"BUY",0.20,0.15)
        self.assertEqual(x["outcome"],"STOP_FIRST_CONSERVATIVE")
        self.assertTrue(x["ambiguous_same_bar"])

    def test_never_crosses_next_session(self):
        r=rows([(100,100.01,99.99,100)])
        r+=rows([(100,101,99,100)],"2026-08-25")
        x=_event_path(r,0,"BUY",0.20,0.15)
        self.assertEqual(x["outcome"],"SESSION_END_NO_EVENT")

if __name__=="__main__": unittest.main()
