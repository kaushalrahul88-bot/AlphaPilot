import unittest
from datetime import datetime, timedelta, timezone

from app.market_news_participation import assess_news_participation
from app.market_news_reaction_windows import build_reaction_window, infer_volume_semantics


IST=timezone(timedelta(hours=5,minutes=30))


def candle(ts,price,volume):
    return {"timestamp":ts.isoformat(),"close":price,"volume":volume}


def cumulative_session(day, increments, *, month=8):
    start=datetime(2026,month,day,9,0,tzinfo=IST)
    cumulative=0;rows=[]
    for i,inc in enumerate(increments):
        cumulative+=inc
        rows.append(candle(start+timedelta(minutes=5*i),100+i/10,cumulative))
    return rows


def slot_session(day, slot_increment, *, default_increment=10, bars=19):
    increments=[default_increment]*bars
    increments[7]=slot_increment  # 09:35
    return cumulative_session(day,increments)


class MarketNewsVolumeNormalizationTests(unittest.TestCase):
    def test_multi_session_monotonic_volume_is_inferred_as_cumulative(self):
        rows=[]
        for day in (3,4,5):
            rows.extend(cumulative_session(day,[100+i for i in range(12)]))
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

    def test_cumulative_volume_uses_prior_session_matched_clock_slot_baseline(self):
        rows=[]
        for day,slot_volume in ((3,20),(4,30),(5,40),(6,50)):
            rows.extend(slot_session(day,slot_volume))
        rows.extend(slot_session(7,80))
        event={"available_at":"2026-08-07T09:30:00+05:30","stance":"BULLISH"}
        window=build_reaction_window(event,rows,as_of="2026-08-07T10:30:00+05:30",
                                     volume_semantics="SESSION_CUMULATIVE_INFERRED")
        self.assertEqual(window["status"],"READY")
        self.assertEqual(window["immediate"]["timestamp"],"2026-08-07T09:35:00+05:30")
        self.assertEqual(window["immediate"]["bar_volume"],80.0)
        activity=window["volume_activity"]
        self.assertEqual(activity["baseline_method"],"PRIOR_SESSION_MATCHED_CLOCK_SLOT")
        self.assertEqual(activity["baseline_clock_slot"],"09:35:00")
        self.assertEqual(activity["baseline_dates"],["2026-08-03","2026-08-04","2026-08-05","2026-08-06"])
        self.assertEqual(activity["baseline_sessions_used"],4)
        self.assertEqual(activity["median_bar_volume"],35.0)

    def test_baseline_is_strictly_point_in_time_and_excludes_current_and_future_sessions(self):
        rows=[]
        for day,slot_volume in ((3,20),(4,30),(5,40)):
            rows.extend(slot_session(day,slot_volume))
        rows.extend(slot_session(7,999))
        rows.extend(slot_session(10,5000))
        event={"available_at":"2026-08-07T09:30:00+05:30","stance":"BULLISH"}
        window=build_reaction_window(event,rows,as_of="2026-08-10T11:00:00+05:30",
                                     volume_semantics="SESSION_CUMULATIVE_INFERRED")
        activity=window["volume_activity"]
        self.assertEqual(activity["baseline_dates"],["2026-08-03","2026-08-04","2026-08-05"])
        self.assertEqual(activity["median_bar_volume"],30.0)
        self.assertEqual(window["immediate"]["bar_volume"],999.0)

    def test_insufficient_prior_matched_sessions_fails_closed(self):
        rows=[]
        for day,slot_volume in ((5,20),(6,30)):
            rows.extend(slot_session(day,slot_volume))
        rows.extend(slot_session(7,100))
        event={"available_at":"2026-08-07T09:30:00+05:30","stance":"BULLISH"}
        window=build_reaction_window(event,rows,as_of="2026-08-07T10:30:00+05:30",
                                     volume_semantics="SESSION_CUMULATIVE_INFERRED")
        self.assertEqual(window["volume_activity"]["baseline_sessions_used"],2)
        self.assertIsNone(window["volume_activity"]["median_bar_volume"])
        result=assess_news_participation(window,{"reaction_state":"ACCEPTED_REACTION"})
        self.assertEqual(result["volume"]["state"],"UNKNOWN")
        self.assertEqual(result["participation_state"],"PRICE_ONLY_ACCEPTANCE")

    def test_session_open_event_uses_prior_opening_slot_not_previous_close(self):
        rows=[]
        for day,opening_0905 in ((3,100),(4,110),(5,120)):
            increments=[10]*19;increments[1]=opening_0905;increments[-1]=2
            rows.extend(cumulative_session(day,increments))
        today=[10]*19;today[1]=200
        rows.extend(cumulative_session(6,today))
        event={"available_at":"2026-08-06T09:00:00+05:30","stance":"BULLISH"}
        window=build_reaction_window(event,rows,as_of="2026-08-06T10:00:00+05:30",
                                     volume_semantics="SESSION_CUMULATIVE_INFERRED")
        activity=window["volume_activity"]
        self.assertEqual(activity["baseline_clock_slot"],"09:05:00")
        self.assertEqual(activity["median_bar_volume"],110.0)
        self.assertEqual(window["immediate"]["bar_volume"],200.0)

    def test_weekend_anchor_baseline_uses_only_pre_news_prior_sessions(self):
        rows=[]
        for day,opening_0905 in ((3,100),(4,110),(5,120),(6,130),(7,140)):
            increments=[10]*19;increments[1]=opening_0905
            rows.extend(cumulative_session(day,increments))
        monday=[10]*19;monday[1]=200
        rows.extend(cumulative_session(10,monday))
        event={"available_at":"2026-08-08T09:45:00+05:30","stance":"BULLISH"}
        window=build_reaction_window(event,rows,as_of="2026-08-10T10:00:00+05:30",
                                     reaction_anchor="2026-08-10T09:00:00+05:30",
                                     volume_semantics="SESSION_CUMULATIVE_INFERRED")
        activity=window["volume_activity"]
        self.assertEqual(activity["baseline_clock_slot"],"09:05:00")
        self.assertEqual(activity["baseline_dates"],["2026-08-03","2026-08-04","2026-08-05","2026-08-06","2026-08-07"])
        self.assertEqual(activity["median_bar_volume"],120.0)
        self.assertEqual(window["immediate"]["bar_volume"],200.0)
        self.assertNotIn("2026-08-10",activity["baseline_dates"])

    def test_participation_uses_time_matched_bar_volume_not_cumulative_snapshot_change(self):
        window={"pre_event":{"volume":1000,"open_interest":None},
                "immediate":{"volume":1100,"bar_volume":40,"open_interest":None},
                "confirmation":{"volume":1300,"open_interest":None},
                "volume_activity":{"semantics":"SESSION_CUMULATIVE_INFERRED",
                                   "baseline_method":"PRIOR_SESSION_MATCHED_CLOCK_SLOT",
                                   "baseline_clock_slot":"09:35:00",
                                   "median_bar_volume":20,"baseline_sessions_used":5,
                                   "baseline_dates":["2026-08-01","2026-08-02","2026-08-03"]}}
        reaction={"reaction_state":"ACCEPTED_REACTION"}
        result=assess_news_participation(window,reaction)
        self.assertEqual(result["volume"]["basis"],"NORMALIZED_BAR_VOLUME")
        self.assertEqual(result["volume"]["baseline_method"],"PRIOR_SESSION_MATCHED_CLOCK_SLOT")
        self.assertEqual(result["volume"]["state"],"EXPANDED")
        self.assertAlmostEqual(result["volume"]["change"],1.0)
        self.assertEqual(result["participation_state"],"PARTIAL_PARTICIPATION")

    def test_ambiguous_declared_volume_does_not_fall_back_to_raw_change(self):
        window={"pre_event":{"volume":100},"immediate":{"volume":1000,"bar_volume":None},
                "confirmation":{"volume":1500},
                "volume_activity":{"semantics":"UNKNOWN","median_bar_volume":None,
                                   "baseline_sessions_used":0}}
        result=assess_news_participation(window,{"reaction_state":"ACCEPTED_REACTION"})
        self.assertEqual(result["volume"]["state"],"UNKNOWN")
        self.assertIsNone(result["volume"]["change"])
        self.assertEqual(result["participation_state"],"PRICE_ONLY_ACCEPTANCE")


if __name__=="__main__":
    unittest.main()
