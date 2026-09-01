from __future__ import annotations

import copy
import inspect
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import crude_oil_mini_abstention_audit as abstention
from app import crude_oil_mini_memory_evidence_audit as memory_audit
from app import crude_oil_mini_playbook_pattern_shadow as pattern_shadow
from app import crude_oil_mini_point_in_time_context as context

IST = ZoneInfo("Asia/Kolkata")


def _session(day: int, *, start_price: float = 8000.0, drift: float = 0.20):
    start = datetime(2026, 8, day, 9, 0, tzinfo=IST)
    rows = []
    price = start_price
    for i in range(160):
        stamp = start + timedelta(minutes=5 * i)
        o = price
        c = price + drift + ((i % 9) - 4) * 0.04
        h = max(o, c) + 0.9
        l = min(o, c) - 0.9
        volume = 900 + (i % 24) * 20
        rows.append([stamp.isoformat(), o, h, l, c, volume])
        price = c
    return rows


def _trend_pullback_fixture():
    start = datetime(2026, 8, 18, 9, 0, tzinfo=IST)
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 100, 99, 105]
    rows = []
    previous = closes[0]
    for index, close in enumerate(closes):
        stamp = start + timedelta(minutes=5 * index)
        open_price = previous
        rows.append([
            stamp.isoformat(),
            open_price,
            max(open_price, close) + 0.5,
            min(open_price, close) - 0.5,
            float(close),
            1000 + index * 10,
        ])
        previous = close
    click = start + timedelta(minutes=60)
    decision = {
        "session": "2026-08-18",
        "click_timestamp": click.isoformat(),
        "latest_visible_bar_start": rows[-1][0],
        "action": "BUY_CE",
        "decision": {"action": "BUY_CE", "playbook": "TREND_PULLBACK"},
        "regime": {
            "mode": "MARKET_REGIME_OBSERVER_V1",
            "observations": {
                "trend_structure": "UPTREND",
                "volatility_regime": "NORMAL",
                "location": "IN_VALUE",
                "participation": "NORMAL",
                "opening_behavior": "BALANCED",
            },
        },
        "future_returns_pct": {"60": 0.50},
        "outcome": {"result": "TARGET", "realized_r": 1.5},
        "decision_fingerprint": "fixture",
    }
    return rows, {"reference_contract": "CRUDEOILM21SEP26FUT", "decisions": [decision]}


class CrudeOilMiniResearchFrameworkTests(unittest.TestCase):
    def test_point_in_time_context_never_exposes_future_record(self):
        records = [
            {
                "series": "WTI_CRUDE",
                "observed_at": "2026-08-18T11:20:00+05:30",
                "available_at": "2026-08-18T11:21:00+05:30",
                "source": "test",
                "value": {"price": 80.0},
            },
            {
                "series": "WTI_CRUDE",
                "observed_at": "2026-08-18T11:30:00+05:30",
                "available_at": "2026-08-18T11:31:00+05:30",
                "source": "test",
                "value": {"price": 81.0},
            },
        ]
        visible = context.visible_at(records, "2026-08-18T11:25:00+05:30")
        self.assertEqual(len(visible), 1)
        latest = context.latest_known_as_of(records, "2026-08-18T11:25:00+05:30")
        self.assertEqual(latest["WTI_CRUDE"]["value"]["price"], 80.0)

    def test_context_manifest_does_not_enable_context_or_news(self):
        manifest = context.acquisition_manifest()
        self.assertEqual(manifest["current_brain_effect"], "NONE")
        self.assertFalse(manifest["news_enabled"])
        self.assertFalse(manifest["option_translation_enabled"])
        self.assertTrue(context.SERIES_POLICY["MCX_CRUDEOILM"]["required"])

    def test_abstention_audit_consumes_frozen_wait_without_changing_it(self):
        replay = {
            "decisions": [
                {
                    "session": "2026-08-18",
                    "click_timestamp": "2026-08-18T12:00:00+05:30",
                    "action": "WAIT",
                    "outcome": {
                        "result": "WAIT",
                        "max_up_pct": 0.80,
                        "max_down_pct": 0.20,
                        "large_move_threshold_pct": 0.50,
                        "future_move_without_setup": True,
                    },
                    "regime": {
                        "mode": "MARKET_REGIME_OBSERVER_V1",
                        "observations": {
                            "trend_structure": "RANGE",
                            "volatility_regime": "NORMAL",
                            "location": "IN_VALUE",
                            "participation": "NORMAL",
                            "opening_behavior": "BALANCED",
                        },
                    },
                    "decision": {"reason": "INSUFFICIENT_ALIGNMENT"},
                },
                {
                    "session": "2026-08-18",
                    "click_timestamp": "2026-08-18T13:00:00+05:30",
                    "action": "BUY_CE",
                    "outcome": {"result": "TARGET"},
                },
            ]
        }
        report = abstention.evaluate_abstention_audit(replay)
        self.assertEqual(replay["decisions"][0]["action"], "WAIT")
        self.assertEqual(report["overall"]["waits"], 1)
        self.assertEqual(report["overall"]["large_move_candidates"], 1)
        self.assertEqual(report["large_move_candidates"][0]["dominant_path_direction"], "UP")
        self.assertEqual(report["large_move_candidates"][0]["regime"]["trend_structure"], "RANGE")
        self.assertIn("RANGE", report["by_regime"]["trend_structure"])
        self.assertNotIn("UNKNOWN", report["by_regime"]["trend_structure"])

    def test_abstention_audit_keeps_legacy_flat_regime_fixture_compatible(self):
        replay = {
            "decisions": [{
                "session": "2026-08-18",
                "click_timestamp": "2026-08-18T12:30:00+05:30",
                "action": "WAIT",
                "outcome": {
                    "result": "WAIT",
                    "max_up_pct": 0.2,
                    "max_down_pct": 0.4,
                    "large_move_threshold_pct": 0.5,
                    "future_move_without_setup": False,
                },
                "regime": {
                    "trend_structure": "DOWNTREND",
                    "volatility_regime": "HIGH",
                    "location": "IN_VALUE",
                    "participation": "NORMAL",
                    "opening_behavior": "BREAKDOWN",
                },
            }]
        }
        report = abstention.evaluate_abstention_audit(replay)
        self.assertIn("DOWNTREND", report["by_regime"]["trend_structure"])
        self.assertIn("HIGH", report["by_regime"]["volatility_regime"])

    def test_shared_playbook_pattern_shadow_is_outcome_blind_for_crude_action(self):
        candles, baseline = _trend_pullback_fixture()
        target_report = pattern_shadow.evaluate_crude_playbook_pattern_shadow(candles, baseline)
        target_row = target_report["rows"][0]
        self.assertTrue(target_row["pattern"]["confirmed"])
        self.assertEqual(target_row["pattern_gate_action"], "BUY_CE")
        self.assertFalse(target_row["changed"])

        altered = copy.deepcopy(baseline)
        altered["decisions"][0]["outcome"] = {"result": "STOP", "realized_r": -1.0}
        altered["decisions"][0]["future_returns_pct"] = {"60": -0.50}
        stop_report = pattern_shadow.evaluate_crude_playbook_pattern_shadow(candles, altered)
        stop_row = stop_report["rows"][0]
        self.assertEqual(stop_row["pattern"], target_row["pattern"])
        self.assertEqual(stop_row["pattern_gate_action"], target_row["pattern_gate_action"])
        self.assertNotEqual(stop_report["pattern_gate"]["expectancy_r_resolved"], target_report["pattern_gate"]["expectancy_r_resolved"])

    def test_shared_playbook_pattern_shadow_cannot_upgrade_wait(self):
        candles, baseline = _trend_pullback_fixture()
        baseline["decisions"][0]["action"] = "WAIT"
        baseline["decisions"][0]["decision"]["action"] = "WAIT"
        report = pattern_shadow.evaluate_crude_playbook_pattern_shadow(candles, baseline)
        self.assertEqual(report["rows"][0]["pattern_gate_action"], "WAIT")
        self.assertFalse(report["rows"][0]["changed"])
        self.assertEqual(report["pattern_gate"]["trades"], 0)

    def test_memory_evidence_audit_uses_crude_only_and_runs_causally(self):
        candles = (
            _session(3, start_price=8000, drift=0.20)
            + _session(4, start_price=8040, drift=-0.18)
            + _session(5, start_price=8010, drift=0.16)
            + _session(6, start_price=8050, drift=-0.12)
        )
        report = memory_audit.evaluate_memory_evidence(candles, sample_every_bars=6)
        self.assertEqual(report["mode"], "CRUDE_OIL_MINI_MEMORY_EVIDENCE_AUDIT_V1")
        self.assertTrue(report["current_contract_only"])
        self.assertFalse(report["news_used"])
        self.assertFalse(report["option_market_data_used"])
        for row in report["observations"]:
            self.assertLess(datetime.fromisoformat(row["click_at"]), datetime.fromisoformat(row["actual_resolved_at"]))

    def test_new_crude_layers_do_not_import_copper_modules(self):
        for module in (context, memory_audit, abstention, pattern_shadow):
            source = inspect.getsource(module)
            self.assertNotIn("from .copper", source)
            self.assertNotIn("import copper", source)


if __name__ == "__main__":
    unittest.main()
