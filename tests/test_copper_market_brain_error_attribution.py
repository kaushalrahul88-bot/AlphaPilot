import unittest
from datetime import datetime,timedelta

from app.copper_market_brain_error_attribution import _attribute,_stats


class CopperMarketBrainErrorAttributionTests(unittest.TestCase):
    def test_stats_measure_direction_not_pnl(self):
        rows=[
            {"signed_forward_pct":0.2,"favorable_excursion_pct":0.3,"adverse_excursion_pct":0.1},
            {"signed_forward_pct":-0.1,"favorable_excursion_pct":0.1,"adverse_excursion_pct":0.2},
        ]
        s=_stats(rows)
        self.assertEqual(s["direction_accuracy_pct"],50.0)
        self.assertEqual(s["observations"],2)

    def test_stability_requires_each_chronological_window(self):
        start=datetime.fromisoformat("2026-08-03T10:00:00+05:30")
        rows=[]
        for i in range(30):
            rows.append({
                "timestamp":(start+timedelta(minutes=15*i)).isoformat(),
                "signal":"BUY","session":"MORNING","structure":"UPTREND",
                "atr_bucket":"NORMAL","momentum_bucket":"NORMAL",
                "relative_volume_bucket":"NORMAL","session_location":"UPPER_MIDDLE",
                "vwap_location":"ABOVE_NEAR","opening_range_break":"ABOVE",
                "price_oi_state":"UNKNOWN",
                "signed_forward_pct":0.1,
                "favorable_excursion_pct":0.2,
                "adverse_excursion_pct":0.05,
            })
        result=_attribute(rows)
        state=result["dimensions"]["signal"]["BUY"]
        self.assertTrue(state["stable_above_50_pct"])
        self.assertEqual(len(state["windows"]),3)


if __name__=="__main__":
    unittest.main()
