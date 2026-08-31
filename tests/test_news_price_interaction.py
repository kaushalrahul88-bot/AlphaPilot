import unittest

from app.current_mind_thesis_builder import build_current_mind_decision
from app.trader_evidence_synthesis import evidence_quality, synthesize_evidence


def _news(stance, status="ACTIVE"):
    return {
        "lane":"NEWS",
        "stance":stance,
        "source":"news_intelligence+persistence",
        "detail":{
            "visible":1,
            "persistence":[{"status":status,"weight":0.8}],
        },
    }


class NewsPriceInteractionTests(unittest.TestCase):
    def test_fresh_news_opposed_by_price_is_context_only(self):
        s=synthesize_evidence([
            {"lane":"STRUCTURE","stance":"BEARISH","source":"market_structure"},
            {"lane":"STRUCTURE","stance":"BEARISH","source":"short_term_momentum"},
            {"lane":"MACRO","stance":"BEARISH","source":"macro"},
            _news("BULLISH","ACTIVE"),
        ])
        self.assertEqual(s["independent_bullish_lanes"],[])
        self.assertEqual(s["independent_bearish_lanes"],["MACRO","STRUCTURE"])
        self.assertEqual(s["news_price_interactions"][0]["state"],"NEWS_NOT_CONFIRMED_BY_PRICE")
        self.assertEqual(evidence_quality(s),"MODERATE")

    def test_decayed_news_cannot_supply_second_confirmation(self):
        s=synthesize_evidence([
            {"lane":"STRUCTURE","stance":"BULLISH","source":"market_structure"},
            {"lane":"STRUCTURE","stance":"BULLISH","source":"short_term_momentum"},
            _news("BULLISH","ACTIVE_DECAYED"),
        ])
        self.assertEqual(s["independent_bullish_lanes"],["STRUCTURE"])
        self.assertEqual(s["news_price_interactions"][0]["state"],"NEWS_ABSORBED")
        self.assertEqual(evidence_quality(s),"WEAK")

    def test_decayed_news_opposed_by_price_yields_price_override_state(self):
        s=synthesize_evidence([
            {"lane":"STRUCTURE","stance":"BEARISH","source":"market_structure"},
            {"lane":"STRUCTURE","stance":"BEARISH","source":"short_term_momentum"},
            _news("BULLISH","ACTIVE_DECAYED"),
        ])
        self.assertEqual(s["news_price_interactions"][0]["state"],"PRICE_OVERRIDES_STALE_CATALYST")
        self.assertEqual(s["independent_bullish_lanes"],[])

    def test_fresh_price_confirmed_news_can_strengthen_but_not_replace_price(self):
        s=synthesize_evidence([
            {"lane":"STRUCTURE","stance":"BULLISH","source":"market_structure"},
            {"lane":"STRUCTURE","stance":"BULLISH","source":"short_term_momentum"},
            _news("BULLISH","ACTIVE"),
        ])
        self.assertEqual(s["independent_bullish_lanes"],["NEWS","STRUCTURE"])
        self.assertEqual(s["news_price_interactions"][0]["state"],"NEWS_CONFIRMED_BY_PRICE")
        self.assertEqual(evidence_quality(s),"MODERATE")

    def test_contextual_news_does_not_veto_coherent_opposite_trade(self):
        e=synthesize_evidence([
            {"lane":"STRUCTURE","stance":"BEARISH","source":"market_structure"},
            {"lane":"STRUCTURE","stance":"BEARISH","source":"short_term_momentum"},
            {"lane":"MACRO","stance":"BEARISH","source":"macro"},
            _news("BULLISH","ACTIVE"),
        ])
        market={
            "confirmation":"price confirms bearish structure",
            "entry_trigger":"break below support",
            "invalidation":"close above swing high",
            "target_logic":"1.5R structural target",
            "risk_reward_basis":"1.5R",
            "entry_price":100.0,
            "stop_price":101.0,
            "target_price":98.5,
            "price":100.0,
            "atr":1.0,
        }
        r=build_current_mind_decision({}, {"regime_labels":["TRENDING"]}, e, {}, [], market)
        self.assertNotEqual(r["reason"],"EVIDENCE_NOT_COHERENT")


if __name__=="__main__":
    unittest.main()
