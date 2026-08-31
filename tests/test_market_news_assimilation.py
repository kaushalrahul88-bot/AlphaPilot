import unittest

from app.market_news_assimilation import assess_market_news_assimilation


class MarketNewsAssimilationTests(unittest.TestCase):
    @staticmethod
    def observed(path_state):
        return {"observation_status":"OBSERVED","path_state":path_state,"outcome_blind":True}

    @staticmethod
    def volatility(ratio=None,status="AVAILABLE"):
        return {"segments":{"assimilation":{"status":status,"normalized_abs_move":ratio}}}

    def test_highly_elevated_follow_through_is_descriptive_only(self):
        result=assess_market_news_assimilation(
            self.observed("UP_FOLLOW_THROUGH"),self.volatility(2.4),
            {"participation_state":"PARTIAL_PARTICIPATION"},
        )
        self.assertEqual(result["assimilation_state"],"DIRECTIONAL_FOLLOW_THROUGH_HIGHLY_ELEVATED")
        self.assertEqual(result["motion_band"],"AT_LEAST_2X_PRIOR_MEDIAN")
        self.assertTrue(result["shadow_only"])
        self.assertTrue(result["classification_unchanged"])
        self.assertTrue(result["outcome_blind"])
        self.assertNotIn("action",result)

    def test_reversal_is_preserved_even_when_motion_is_large(self):
        result=assess_market_news_assimilation(self.observed("UP_THEN_REVERSED"),self.volatility(5.0))
        self.assertEqual(result["assimilation_state"],"REVERSAL_COUNTERFORCE")
        self.assertEqual(result["motion_band"],"AT_LEAST_2X_PRIOR_MEDIAN")

    def test_delayed_path_does_not_require_headline_direction(self):
        result=assess_market_news_assimilation(self.observed("DELAYED_DOWN"),self.volatility(1.3))
        self.assertEqual(result["assimilation_state"],"DELAYED_ASSIMILATION")
        self.assertEqual(result["motion_band"],"PRIOR_MEDIAN_TO_2X")

    def test_missing_volatility_history_does_not_erase_observed_path(self):
        result=assess_market_news_assimilation(
            self.observed("DOWN_FOLLOW_THROUGH"),self.volatility(None,"INSUFFICIENT_HISTORY")
        )
        self.assertEqual(result["assimilation_state"],"DIRECTIONAL_FOLLOW_THROUGH_VOLATILITY_UNKNOWN")
        self.assertEqual(result["motion_band"],"UNAVAILABLE")

    def test_incomplete_observation_fails_closed(self):
        result=assess_market_news_assimilation(
            {"observation_status":"INCOMPLETE","path_state":"UNOBSERVED"},self.volatility(4.0)
        )
        self.assertEqual(result["assimilation_state"],"UNOBSERVED")

    def test_reference_bands_are_simple_prior_median_multiples(self):
        below=assess_market_news_assimilation(self.observed("UP_FOLLOW_THROUGH"),self.volatility(0.99))
        baseline=assess_market_news_assimilation(self.observed("UP_FOLLOW_THROUGH"),self.volatility(1.0))
        double=assess_market_news_assimilation(self.observed("UP_FOLLOW_THROUGH"),self.volatility(2.0))
        self.assertEqual(below["motion_band"],"BELOW_PRIOR_MEDIAN")
        self.assertEqual(baseline["motion_band"],"PRIOR_MEDIAN_TO_2X")
        self.assertEqual(double["motion_band"],"AT_LEAST_2X_PRIOR_MEDIAN")


if __name__=="__main__":unittest.main()
