import unittest

from app.market_news_path_materiality import assess_observed_path_materiality


class MarketNewsPathMaterialityTests(unittest.TestCase):
    @staticmethod
    def path(directions, status="OBSERVED", state="UP_FOLLOW_THROUGH"):
        return {
            "observation_status": status,
            "path_state": state,
            "directions": directions,
            "moves_from_pre": {"immediate":0.001,"confirmation":0.002,"assimilation":0.003},
        }

    @staticmethod
    def volatility(immediate=1.5, confirmation=2.5, assimilation=0.8):
        def segment(value):
            return {"status":"AVAILABLE","normalized_abs_move":value}
        return {"segments":{
            "immediate":segment(immediate),
            "confirmation":segment(confirmation),
            "assimilation":segment(assimilation),
        }}

    def test_mixed_directional_materiality_is_shadow_only(self):
        result=assess_observed_path_materiality(
            self.path({"immediate":"UP","confirmation":"UP","assimilation":"UP"}),
            self.volatility(),
        )
        self.assertEqual(result["materiality_state"],"MIXED_DIRECTIONAL_MATERIALITY")
        self.assertEqual(result["directional_segments"],3)
        self.assertEqual(result["directional_at_or_above_baseline"],2)
        self.assertEqual(result["directional_sub_baseline"],1)
        self.assertTrue(result["shadow_only"])
        self.assertTrue(result["classification_unchanged"])
        self.assertTrue(result["outcome_blind"])
        self.assertNotIn("action",result)

    def test_all_fixed_floor_directions_can_be_sub_baseline(self):
        result=assess_observed_path_materiality(
            self.path({"immediate":"UP","confirmation":"MUTED","assimilation":"DOWN"},state="UP_THEN_REVERSED"),
            self.volatility(0.7,0.2,0.5),
        )
        self.assertEqual(result["materiality_state"],"ALL_DIRECTIONAL_SEGMENTS_SUB_BASELINE")
        self.assertEqual(result["directional_segments"],2)
        self.assertEqual(result["directional_sub_baseline"],2)

    def test_all_directional_segments_can_be_supported_by_baseline_motion(self):
        result=assess_observed_path_materiality(
            self.path({"immediate":"DOWN","confirmation":"DOWN","assimilation":"DOWN"},state="DOWN_FOLLOW_THROUGH"),
            self.volatility(1.0,2.0,1.2),
        )
        self.assertEqual(result["materiality_state"],"ALL_DIRECTIONAL_SEGMENTS_AT_OR_ABOVE_BASELINE")
        self.assertEqual(result["segments"]["immediate"]["materiality_band"],"BASELINE_TO_2X")
        self.assertEqual(result["segments"]["confirmation"]["materiality_band"],"ELEVATED_2X_PLUS")

    def test_missing_reference_does_not_reclassify_path(self):
        volatility={"segments":{
            "immediate":{"status":"INSUFFICIENT_HISTORY"},
            "confirmation":{"status":"INSUFFICIENT_HISTORY"},
            "assimilation":{"status":"INSUFFICIENT_HISTORY"},
        }}
        result=assess_observed_path_materiality(
            self.path({"immediate":"UP","confirmation":"UP","assimilation":"UP"}),volatility
        )
        self.assertEqual(result["materiality_state"],"VOLATILITY_REFERENCE_UNAVAILABLE")
        self.assertEqual(result["observed_path_state"],"UP_FOLLOW_THROUGH")
        self.assertEqual(result["directional_with_reference"],0)

    def test_partial_reference_is_explicit(self):
        volatility=self.volatility(1.2,1.4,1.6)
        volatility["segments"]["confirmation"]={"status":"INSUFFICIENT_HISTORY"}
        result=assess_observed_path_materiality(
            self.path({"immediate":"UP","confirmation":"UP","assimilation":"UP"}),volatility
        )
        self.assertEqual(result["materiality_state"],"PARTIAL_VOLATILITY_REFERENCE")
        self.assertEqual(result["directional_with_reference"],2)

    def test_incomplete_observed_path_fails_closed(self):
        result=assess_observed_path_materiality(
            self.path({"immediate":"UNKNOWN","confirmation":"UNKNOWN","assimilation":"UNKNOWN"},status="INCOMPLETE",state="UNOBSERVED"),
            self.volatility(3.0,3.0,3.0),
        )
        self.assertEqual(result["materiality_state"],"UNOBSERVED")


if __name__=="__main__":unittest.main()
