import copy
import unittest

from scripts.audit_current_mind_news_geometry import audit_geometry


def _row(ts, action, evidence_items, outcome=None):
    lanes = {}
    for item in evidence_items:
        lanes.setdefault(item["lane"], []).append(copy.deepcopy(item))
    return {
        "click_timestamp": ts,
        "decision": {"action": action},
        "evidence": {"lanes": lanes},
        "outcome": outcome,
    }


def _report(baseline_row, news_row):
    common = {
        "clicks_per_complete_session": 1,
        "reference_contract": "COPPERTESTFUT",
        "scheduled_clicks": 1,
        "evaluated_clicks": 1,
        "complete_session_dates": ["2026-08-07"],
    }
    return audit_geometry({**common, "decisions": [baseline_row]}, {**common, "decisions": [news_row]})


def _news(status="ACTIVE", stance="BULLISH"):
    return {
        "lane": "NEWS",
        "stance": stance,
        "source": "news_intelligence+persistence",
        "detail": {
            "visible": 1,
            "persistence": [{"status": status, "weight": 0.8}],
        },
    }


class NewsGeometryCounterfactualAuditTests(unittest.TestCase):
    def test_unconfirmed_news_restores_baseline_bearish_geometry(self):
        ts = "2026-08-07T13:20:00+05:30"
        items = [
            {"lane": "STRUCTURE", "stance": "BEARISH", "source": "market_structure"},
            {"lane": "MACRO", "stance": "BEARISH", "source": "macro"},
            {"lane": "EXPERIENCE", "stance": "BULLISH", "source": "memory"},
            _news("ACTIVE", "BULLISH"),
        ]
        report = _report(_row(ts, "BUY_PE", items), _row(ts, "NO_TRADE", items))
        row = report["diagnostic_rows"][0]
        self.assertEqual(row["legacy_geometry_direction"], None)
        self.assertEqual(row["current_geometry_direction"], "BEARISH")
        self.assertEqual(row["classification"], "CURRENT_GEOMETRY_ALIGNS_BASELINE_TRADE")
        self.assertIn("NEWS_NOT_CONFIRMED_BY_PRICE", row["news_price_interaction_states"])

    def test_decayed_news_created_trade_is_removed(self):
        ts = "2026-08-10T16:55:00+05:30"
        items = [
            {"lane": "STRUCTURE", "stance": "BULLISH", "source": "short_term_momentum"},
            {"lane": "MACRO", "stance": "BEARISH", "source": "macro"},
            _news("ACTIVE_DECAYED", "BULLISH"),
        ]
        report = _report(_row(ts, "NO_TRADE", items), _row(ts, "BUY_CE", items))
        row = report["diagnostic_rows"][0]
        self.assertEqual(row["legacy_geometry_direction"], "BULLISH")
        self.assertIsNone(row["current_geometry_direction"])
        self.assertEqual(row["classification"], "CURRENT_GEOMETRY_REMOVES_NEWS_CREATED_TRADE")

    def test_outcomes_do_not_change_audit(self):
        ts = "2026-08-07T16:30:00+05:30"
        items = [
            {"lane": "STRUCTURE", "stance": "BULLISH", "source": "market_structure"},
            _news("ACTIVE", "BULLISH"),
        ]
        first = _report(_row(ts, "NO_TRADE", items, {"result": "TARGET"}), _row(ts, "NO_TRADE", items, {"result": "STOP"}))
        second = _report(_row(ts, "NO_TRADE", items, {"result": "STOP"}), _row(ts, "NO_TRADE", items, {"result": "TARGET"}))
        self.assertEqual(first, second)
        self.assertTrue(first["outcome_blind"])

    def test_click_mismatch_fails_closed(self):
        common = {
            "clicks_per_complete_session": 1,
            "reference_contract": "COPPERTESTFUT",
            "scheduled_clicks": 1,
            "evaluated_clicks": 1,
            "complete_session_dates": ["2026-08-07"],
        }
        items = [{"lane": "STRUCTURE", "stance": "BULLISH", "source": "market_structure"}]
        baseline = {**common, "decisions": [_row("2026-08-07T10:00:00+05:30", "NO_TRADE", items)]}
        news = {**common, "decisions": [_row("2026-08-07T10:05:00+05:30", "NO_TRADE", items)]}
        with self.assertRaisesRegex(ValueError, "Click timestamp mismatch"):
            audit_geometry(baseline, news)


if __name__ == "__main__":
    unittest.main()
