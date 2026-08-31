import unittest

from scripts.compare_current_mind_news_clicks import build_report, news_interaction


def _decision(ts, action, structure, news_stance=None, persistence_status="ACTIVE", outcome=None):
    lanes={
        "STRUCTURE":[
            {"lane":"STRUCTURE","stance":structure,"source":"market_structure"},
            {"lane":"STRUCTURE","stance":structure,"source":"short_term_momentum"},
        ],
        "MACRO":[],
        "NEWS":[],
    }
    if news_stance:
        lanes["NEWS"]=[{
            "lane":"NEWS",
            "stance":news_stance,
            "source":"news_intelligence+persistence",
            "detail":{
                "visible":1,
                "persistence":[{"status":persistence_status,"weight":0.8}],
            },
        }]
    return {
        "click_timestamp":ts,
        "evidence":{"lanes":lanes},
        "decision":{"action":action},
        "thesis":None,
        "outcome":outcome,
        "decision_fingerprint":f"fp-{ts}-{action}",
    }


class NewsClickForensicV2Tests(unittest.TestCase):
    def test_reclassification_does_not_read_outcome(self):
        a=_decision("2026-08-07T13:20:00+05:30","NO_TRADE","BEARISH","BULLISH","ACTIVE",{"result":"TARGET"})
        b=_decision("2026-08-07T13:20:00+05:30","NO_TRADE","BEARISH","BULLISH","ACTIVE",{"result":"STOP"})
        self.assertEqual(news_interaction(a),news_interaction(b))
        self.assertEqual(news_interaction(a)["state"],"NEWS_NOT_CONFIRMED_BY_PRICE")

    def test_report_classifies_old_action_changes_without_recomputing_them(self):
        t1="2026-08-07T13:20:00+05:30"
        t2="2026-08-10T16:55:00+05:30"
        baseline={
            "actions":{"BUY_PE":1,"NO_TRADE":1},
            "expectancy_r_resolved":-0.1,
            "decisions":[
                _decision(t1,"BUY_PE","BEARISH"),
                _decision(t2,"NO_TRADE","BULLISH"),
            ],
        }
        variant={
            "actions":{"NO_TRADE":1,"BUY_CE":1},
            "expectancy_r_resolved":-0.2,
            "decisions":[
                _decision(t1,"NO_TRADE","BEARISH","BULLISH","ACTIVE"),
                _decision(t2,"BUY_CE","BULLISH","BULLISH","ACTIVE_DECAYED"),
            ],
        }
        report=build_report(baseline,variant)
        self.assertTrue(report["classification_is_outcome_blind"])
        self.assertEqual(report["news_visible_click_count"],2)
        self.assertEqual(report["action_changed_click_count"],2)
        self.assertEqual(report["news_interaction_state_counts"],{
            "NEWS_ABSORBED":1,
            "NEWS_NOT_CONFIRMED_BY_PRICE":1,
        })
        self.assertEqual(report["news_directional_role_counts"],{"CONTEXT_ONLY":2})
        self.assertEqual(report["changed_action_transition_counts"],{
            "NO_TRADE_TO_TRADE":1,
            "TRADE_TO_NO_TRADE":1,
        })

    def test_confirmed_fresh_news_is_reported_as_confirmation(self):
        row=_decision("2026-08-07T16:30:00+05:30","NO_TRADE","BULLISH","BULLISH","ACTIVE")
        interaction=news_interaction(row)
        self.assertEqual(interaction["state"],"NEWS_CONFIRMED_BY_PRICE")
        self.assertEqual(interaction["directional_role"],"CONFIRMATION")


if __name__=="__main__":
    unittest.main()
