import unittest

from app.market_news_materiality_qualified_path import assess_materiality_qualified_path


class MaterialityQualifiedPathTests(unittest.TestCase):
    @staticmethod
    def observed(state, immediate, confirmation, assimilation):
        return {
            "observation_status":"OBSERVED",
            "path_state":state,
            "directions":{"immediate":immediate,"confirmation":confirmation,"assimilation":assimilation},
        }

    @staticmethod
    def materiality(immediate, confirmation, assimilation):
        def segment(band):
            return {
                "materiality_band":band,
                "at_or_above_prior_median":band in {"BASELINE_TO_2X","ELEVATED_2X_PLUS"},
            }
        return {"segments":{
            "immediate":segment(immediate),
            "confirmation":segment(confirmation),
            "assimilation":segment(assimilation),
        }}

    def test_robust_follow_through_survives_qualification(self):
        result=assess_materiality_qualified_path(
            self.observed("UP_FOLLOW_THROUGH","UP","UP","UP"),
            self.materiality("ELEVATED_2X_PLUS","BASELINE_TO_2X","ELEVATED_2X_PLUS"),
        )
        self.assertEqual(result["qualified_path_state"],"UP_FOLLOW_THROUGH")
        self.assertEqual(result["direction_changes"],0)
        self.assertFalse(result["path_state_changed"])
        self.assertTrue(result["shadow_only"])
        self.assertTrue(result["classification_unchanged"])
        self.assertTrue(result["outcome_blind"])
        self.assertNotIn("action",result)

    def test_weak_immediate_follow_through_becomes_delayed(self):
        result=assess_materiality_qualified_path(
            self.observed("UP_FOLLOW_THROUGH","UP","UP","UP"),
            self.materiality("SUB_BASELINE","BASELINE_TO_2X","BASELINE_TO_2X"),
        )
        self.assertEqual(result["qualified_directions"]["immediate"],"MUTED")
        self.assertEqual(result["qualified_path_state"],"DELAYED_UP")
        self.assertTrue(result["path_state_changed"])

    def test_all_sub_baseline_reversal_becomes_muted(self):
        result=assess_materiality_qualified_path(
            self.observed("UP_THEN_REVERSED","UP","MUTED","DOWN"),
            self.materiality("SUB_BASELINE","SUB_BASELINE","SUB_BASELINE"),
        )
        self.assertEqual(result["qualified_path_state"],"MUTED_PATH")
        self.assertEqual(result["direction_changes"],2)

    def test_missing_reference_fails_closed_to_unknown(self):
        observed=self.observed("DOWN_FOLLOW_THROUGH","DOWN","DOWN","DOWN")
        materiality=self.materiality("BASELINE_TO_2X","BASELINE_TO_2X","BASELINE_TO_2X")
        materiality["segments"]["assimilation"]={"materiality_band":"VOLATILITY_REFERENCE_UNAVAILABLE","at_or_above_prior_median":False}
        result=assess_materiality_qualified_path(observed,materiality)
        self.assertEqual(result["qualified_directions"]["assimilation"],"UNKNOWN")
        self.assertEqual(result["qualified_path_state"],"UNOBSERVED")

    def test_raw_muted_segment_stays_muted_without_needing_reference(self):
        result=assess_materiality_qualified_path(
            self.observed("DELAYED_DOWN","MUTED","MUTED","DOWN"),
            self.materiality("SUB_BASELINE","SUB_BASELINE","BASELINE_TO_2X"),
        )
        self.assertEqual(result["qualified_directions"]["immediate"],"MUTED")
        self.assertEqual(result["qualified_path_state"],"DELAYED_DOWN")

    def test_incomplete_path_stays_unobserved(self):
        result=assess_materiality_qualified_path(
            {"observation_status":"INCOMPLETE","path_state":"UNOBSERVED"},{}
        )
        self.assertEqual(result["qualified_path_state"],"UNOBSERVED")
        self.assertEqual(result["observation_status"],"INCOMPLETE")


if __name__=="__main__":unittest.main()
