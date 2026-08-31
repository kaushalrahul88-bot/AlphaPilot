import unittest

from app.market_news_observed_path import assess_observed_market_path as assess


class MarketNewsObservedPathTests(unittest.TestCase):
    def p(self, value):
        return {"price": value}

    def test_up_follow_through(self):
        r=assess(self.p(100), self.p(100.6), self.p(100.4), self.p(100.8))
        self.assertEqual(r["observation_status"], "OBSERVED")
        self.assertEqual(r["path_state"], "UP_FOLLOW_THROUGH")
        self.assertEqual(r["directions"]["immediate"], "UP")
        self.assertEqual(r["directions"]["assimilation"], "UP")

    def test_up_then_reversed(self):
        r=assess(self.p(100), self.p(100.1), self.p(99.8), self.p(99.7))
        self.assertEqual(r["path_state"], "UP_THEN_REVERSED")

    def test_delayed_down(self):
        r=assess(self.p(100), self.p(99.98), self.p(99.95), self.p(99.7))
        self.assertEqual(r["path_state"], "DELAYED_DOWN")

    def test_missing_assimilation_is_unobserved(self):
        r=assess(self.p(100), self.p(101), self.p(101.1), None)
        self.assertEqual(r["observation_status"], "INCOMPLETE")
        self.assertEqual(r["path_state"], "UNOBSERVED")

    def test_negative_noise_floor_fails_closed(self):
        with self.assertRaises(ValueError):
            assess(self.p(100), self.p(101), self.p(101), self.p(101), noise_floor=-0.001)


if __name__ == "__main__":
    unittest.main()
