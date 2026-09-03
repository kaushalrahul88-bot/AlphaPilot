import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_directional_asymmetry_candidate import long_only_shadow_signal
from app.crude_research_brain import build_crude_snapshot
from app.current_mind_crude_no_news import (
    CURRENT_MIND_ID,
    current_mind_crude_no_news_decision,
    replay_crude_no_news_current_mind,
)

IST = ZoneInfo("Asia/Kolkata")


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


class CrudeNoNewsCurrentMindTests(unittest.TestCase):
    def test_action_is_exact_frozen_candidate_mapping(self):
        rows=_candles()
        for index in (60,100,150,210,300):
            features=build_crude_snapshot(rows,index)
            expected="BUY" if long_only_shadow_signal(features)=="BUY" else "WAIT"
            decision=current_mind_crude_no_news_decision(features)
            self.assertEqual(decision["action"],expected)
            self.assertEqual(decision["current_mind_id"],CURRENT_MIND_ID)
            self.assertFalse(decision["news_enabled"])
            self.assertFalse(decision["decision_semantics"]["annotation_fields_can_change_action"])

    def test_annotation_changes_cannot_change_action(self):
        features={
            "bar_start":"2026-08-10T10:00:00+05:30",
            "available_at":"2026-08-10T10:05:00+05:30",
            "structure":"UPTREND",
            "return_15m_pct":0.2,
            "ema20_gap_pct":0.3,
            "ema50_gap_pct":0.4,
            "session_return_pct":0.1,
            "session_range_position":0.8,
            "session_vwap_gap_pct":0.2,
            "relative_volume":1.1,
            "price_oi_state":"UNKNOWN",
            "oi_change_15m_pct":None,
            "atr_pct":0.15,
        }
        first=current_mind_crude_no_news_decision(features)
        mutated=dict(features)
        mutated.update({
            "session_return_pct":-9.0,
            "session_range_position":0.01,
            "session_vwap_gap_pct":-5.0,
            "relative_volume":0.01,
            "atr_pct":9.0,
            "news_effect":"BEARISH",
        })
        second=current_mind_crude_no_news_decision(mutated)
        self.assertEqual(first["action"],"BUY")
        self.assertEqual(second["action"],"BUY")
        self.assertEqual(first["decision_fingerprint"],second["decision_fingerprint"])

    def test_replay_has_zero_parity_mismatches(self):
        result=replay_crude_no_news_current_mind(
            _candles(),trading_symbol="CRUDEOIL21SEP26FUT",clicks_per_session=20
        )
        self.assertTrue(result["coverage"]["exact_click_coverage"])
        self.assertEqual(result["coverage"]["parity_mismatches"],0)
        self.assertTrue(result["freeze_candidate"]["ready"])
        self.assertFalse(result["news_enabled"])
        self.assertTrue(result["parity_audit_only"])
        self.assertFalse(result["new_strategy_evidence_claimed"])


if __name__=="__main__":
    unittest.main()
