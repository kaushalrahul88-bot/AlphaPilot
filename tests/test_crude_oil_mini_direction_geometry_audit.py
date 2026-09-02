from __future__ import annotations

import copy
import inspect
import unittest

from app import crude_oil_mini_direction_geometry_audit as audit


def _row(*, stamp, action, outcome, r60, r15=None, r30=None, playbook="TREND_PULLBACK"):
    direction = "BULLISH" if action == "BUY_CE" else "BEARISH"
    return {
        "session": stamp[:10],
        "click_timestamp": stamp,
        "action": action,
        "direction": direction,
        "features": {"structure": "UPTREND" if action == "BUY_CE" else "DOWNTREND"},
        "regime": {
            "observations": {
                "volatility_regime": "NORMAL",
                "location": "IN_VALUE",
                "participation": "NORMAL",
                "opening_behavior": "BALANCED",
            }
        },
        "evidence": {"lanes": {"EXPERIENCE": [{"stance": direction}]}},
        "decision": {"action": action, "playbook": playbook},
        "outcome": outcome,
        "future_returns_pct": {
            "15": r60 if r15 is None else r15,
            "30": r60 if r30 is None else r30,
            "60": r60,
        },
    }


def _fixture():
    return {
        "reference_contract": "CRUDEOILM21SEP26FUT",
        "evaluated_clicks": 80,
        "decisions": [
            # Direction is correct at 60m, but the frozen structural stop hit first.
            _row(
                stamp="2026-06-16T12:00:00+05:30",
                action="BUY_CE",
                outcome={"result": "STOP", "realized_r": -1.0, "mfe_r": 0.4, "mae_r": 1.0},
                r60=0.60,
                r15=-0.20,
                r30=0.10,
            ),
            # Target was reached before a later 60m reversal.
            _row(
                stamp="2026-07-15T13:00:00+05:30",
                action="BUY_PE",
                outcome={"result": "TARGET", "realized_r": 1.5, "mfe_r": 1.6, "mae_r": 0.3},
                r60=0.50,
                r15=-0.30,
                r30=-0.10,
                playbook="BREAKOUT_RETEST",
            ),
            _row(
                stamp="2026-08-10T17:00:00+05:30",
                action="BUY_PE",
                outcome={"result": "NO_ENTRY", "realized_r": 0.0, "mfe_r": 0.0, "mae_r": 0.0},
                r60=-0.40,
                playbook="RANGE_EDGE_REVERSAL",
            ),
            _row(
                stamp="2026-08-25T18:00:00+05:30",
                action="BUY_CE",
                outcome={"result": "SESSION_END", "realized_r": 0.0, "mfe_r": 0.7, "mae_r": 0.6},
                r60=-0.20,
                playbook="BREAKOUT_RETEST",
            ),
            {
                "session": "2026-08-25",
                "click_timestamp": "2026-08-25T19:00:00+05:30",
                "action": "WAIT",
                "outcome": {"result": "WAIT"},
                "future_returns_pct": {"15": 1.0, "30": 1.0, "60": 1.0},
            },
        ],
    }


class CrudeOilMiniDirectionGeometryAuditTests(unittest.TestCase):
    def test_separates_direction_from_geometry_without_mutating_baseline(self):
        baseline = _fixture()
        frozen = copy.deepcopy(baseline)
        report = audit.evaluate_direction_geometry_audit(baseline)

        self.assertEqual(baseline, frozen)
        self.assertEqual(report["trade_observations"], 4)
        self.assertEqual(report["horizon_direction"]["60"]["correct"], 2)
        self.assertEqual(report["horizon_direction"]["60"]["wrong"], 2)
        self.assertEqual(report["outcome_by_60m_direction"]["STOP"]["CORRECT"], 1)
        self.assertEqual(report["outcome_by_60m_direction"]["TARGET"]["WRONG"], 1)
        self.assertEqual(report["geometry_realization"]["direction_correct_stops"], 1)
        self.assertEqual(report["geometry_realization"]["direction_wrong_targets"], 1)
        self.assertEqual(report["geometry_realization"]["direction_correct_stops_by_playbook"]["TREND_PULLBACK"], 1)

    def test_put_direction_is_signed_from_the_trade_perspective(self):
        correct_put = _row(
            stamp="2026-08-18T12:00:00+05:30",
            action="BUY_PE",
            outcome={"result": "STOP", "realized_r": -1.0},
            r60=-0.5,
        )
        wrong_put = copy.deepcopy(correct_put)
        wrong_put["click_timestamp"] = "2026-08-18T13:00:00+05:30"
        wrong_put["future_returns_pct"]["60"] = 0.5
        report = audit.evaluate_direction_geometry_audit({
            "reference_contract": "CRUDEOILM21SEP26FUT",
            "evaluated_clicks": 2,
            "decisions": [correct_put, wrong_put],
        })
        self.assertEqual(report["horizon_direction"]["60"]["correct"], 1)
        self.assertEqual(report["horizon_direction"]["60"]["wrong"], 1)

    def test_wait_is_not_reclassified_as_a_trade(self):
        report = audit.evaluate_direction_geometry_audit(_fixture())
        self.assertEqual(report["trade_observations"], 4)
        self.assertFalse(report["decision_path_changed"])
        self.assertFalse(report["geometry_changed"])
        self.assertFalse(report["promotion_allowed"])
        self.assertFalse(report["threshold_search_performed"])

    def test_audit_has_no_copper_market_dependency(self):
        source = inspect.getsource(audit)
        self.assertNotIn("from .copper", source)
        self.assertNotIn("import copper", source)


if __name__ == "__main__":
    unittest.main()
