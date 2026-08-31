import unittest
from datetime import datetime, timedelta, timezone

from app.market_news_participation import assess_news_participation
from app.market_news_reaction_windows import build_reaction_window, infer_volume_semantics


IST=timezone(timedelta(hours=5,minutes=30))


def candle(ts,price,volume):
    return {"timestamp":ts.isoformat(),"close":price,"volume":volume}


class MarketNewsVolumeNormalizationTests(unittest.TestCase):
    def test_multi_session_monotonic_volume_is_inferred_as_cumulative(self):
        rows=[]
        for day in (3,4,5):
            start=datetime(2026,8,day,9,0,tzinfo=IST)
            cumulative=0
            for i in range(12):
                cumulative+=100+i
                rows.append(candle(start+timedelta(minutes=5*i),100+i/10,cumulative))
        result=infer_volume_semantics(rows)
        self.assertEqual(result["mode"],"SESSION_CUMULATIVE_INFERRED")
        self.assertEqual(result["qualifying_sessions"],3)
        self.assertEqual(result["nondecreasing_sessions"],3)

    def test_intraday_volume_decrease_keeps_semantics_unknown(self):
        rows=[]
        for day in (3,4,5):
            start=datetime(2026,8,day,9,0,tzinfo=IST)
            values=[100*(i+1) for i in range(12)]
            if day==4:values[8]=values[7]-1
            rows.extend(candle(start+timedelta(minutes=5*i),100+i/10,v) for i,v in enumerate(values))
        self.assertEqual(infer_volume_semantics(rows)["mode"],"UNKNOWN")

    def test_cumulative_volume_is_converted_to_selected_bar_activity(self):
        start=datetime(2026,8,7,9,0,tzinfo=IST)
        increments=[10,20,30,20,20,20,40,25,25,25,25,25,25,25,25,25,25,25,25]
        cumulative=0;rows=[]
        for i,inc in enumerate(increments):
            cumulative+=inc
            rows.append(candle(start+timedelta(minutes=5*i),100+i/10,cumulative))
        event={"available_at":"2026-08-07T09:30:00+05:30","stance":"BULLISH"}
        window=build_reaction_window(event,rows,as_of="2026-08-07T10:30:00+05:30",
                                     volume_semantics="SESSION_CUMULATIVE_INFERRED",
                                     volume_baseline_bars=5)
        self.assertEqual(window["status"],"READY")
        self.assertEqual(window["immediate"]["bar_volume"],25.0)
        self.assertEqual(window["volume_activity"]["median_bar_volume"],20.0)
        self.assertEqual(window["volume_activity"]["baseline_bars_used"],5)

    def test_participation_uses_bar_volume_not_cumulative_snapshot_change(self):
        window={"pre_event":{"volume":1000,"open_interest":None},
                "immediate":{"volume":1100,"bar_volume":40,"open_interest":None},
                "confirmation":{"volume":1300,"open_interest":None},
                "volume_activity":{"semantics":"SESSION_CUMULATIVE_INFERRED",
                                   "median_bar_volume":20,"baseline_bars_used":12}}
        reaction={"reaction_state":"ACCEPTED_REACTION"}
        result=assess_news_participation(window,reaction)
        self.assertEqual(result["volume"]["basis"],"NORMALIZED_BAR_VOLUME")
        self.assertEqual(result["volume"]["state"],"EXPANDED")
        self.assertAlmostEqual(result["volume"]["change"],1.0)
        self.assertEqual(result["participation_state"],"PARTIAL_PARTICIPATION")

    def test_ambiguous_declared_volume_does_not_fall_back_to_raw_change(self):
        window={"pre_event":{"volume":100},"immediate":{"volume":1000,"bar_volume":None},
                "confirmation":{"volume":1500},
                "volume_activity":{"semantics":"UNKNOWN","median_bar_volume":None,"baseline_bars_used":0}}
        result=assess_news_participation(window,{"reaction_state":"ACCEPTED_REACTION"})
        self.assertEqual(result["volume"]["state"],"UNKNOWN")
        self.assertIsNone(result["volume"]["change"])
        self.assertEqual(result["participation_state"],"PRICE_ONLY_ACCEPTANCE")


if __name__=="__main__":
    unittest.main()
