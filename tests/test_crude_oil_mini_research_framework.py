from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import crude_oil_mini_abstention_audit as abstention
from app import crude_oil_mini_memory_evidence_audit as memory_audit
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
                        "trend_structure": "RANGE",
                        "volatility_regime": "NORMAL",
                        "location": "IN_VALUE",
                        "participation": "NORMAL",
                        "opening_behavior": "BALANCED",
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
        for module in (context, memory_audit, abstention):
            source = inspect.getsource(module)
            self.assertNotIn("from .copper", source)
            self.assertNotIn("import copper", source)


if __name__ == "__main__":
    unittest.main()
