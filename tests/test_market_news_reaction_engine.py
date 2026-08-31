import unittest
from app.market_news_reaction_engine import assess_market_news_reaction as assess


class MarketNewsReactionEngineTests(unittest.TestCase):
    def event(self,stance="BULLISH"):return {"stance":stance,"headline":"event"}
    def p(self,x):return {"price":x}

    def test_bullish_news_with_follow_through_is_accepted(self):
        r=assess(self.event(),self.p(100),self.p(101),self.p(101.4))
        self.assertEqual(r["reaction_state"],"ACCEPTED_REACTION")

    def test_bullish_spike_then_failure_is_failed_reaction(self):
        r=assess(self.event(),self.p(100),self.p(101),self.p(100))
        self.assertEqual(r["reaction_state"],"FAILED_REACTION")

    def test_bullish_news_with_immediate_selloff_is_market_rejection(self):
        r=assess(self.event(),self.p(100),self.p(99))
        self.assertEqual(r["reaction_state"],"MARKET_REJECTION")

    def test_muted_then_flat_is_absorbed_or_priced_in(self):
        r=assess(self.event(),self.p(100),self.p(100.01),assimilation=self.p(100.02))
        self.assertEqual(r["reaction_state"],"ABSORBED_OR_PRICED_IN")

    def test_muted_then_expected_move_is_delayed_acceptance(self):
        r=assess(self.event(),self.p(100),self.p(100.01),assimilation=self.p(101))
        self.assertEqual(r["reaction_state"],"DELAYED_ACCEPTANCE")

    def test_bearish_news_is_symmetric(self):
        r=assess(self.event("BEARISH"),self.p(100),self.p(99),self.p(98.5))
        self.assertEqual(r["reaction_state"],"ACCEPTED_REACTION")

    def test_trade_outcome_field_is_irrelevant(self):
        a=self.event();b=self.event();a["outcome"]="TARGET";b["outcome"]="STOP"
        self.assertEqual(assess(a,self.p(100),self.p(101),self.p(101.2))["reaction_state"],
                         assess(b,self.p(100),self.p(101),self.p(101.2))["reaction_state"])


if __name__=="__main__":unittest.main()
