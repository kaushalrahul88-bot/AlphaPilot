from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app import crude_oil_mini_research_framework as framework
from app import crude_oil_mini_research_tape as tape


IST = ZoneInfo("Asia/Kolkata")


def _complete_session(day: int, start_price: float = 8000.0) -> list[list]:
    start = datetime(2026, 8, day, 9, 0, tzinfo=IST)
    rows = []
    price = start_price
    for i in range(170):
        stamp = start + timedelta(minutes=5 * i)
        close = price + 0.2
        rows.append([stamp.isoformat(), price, close + 0.8, price - 0.8, close, 1000 + i, None])
        price = close
    return rows


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def _mini_fetch_chunk(self, contract, *, candle_interval, legacy_minutes, start, end):
        self.calls.append((contract["trading_symbol"], start, end, candle_interval, legacy_minutes))
        rows = []
        cursor = start
        price = 8000.0
        while cursor <= end:
            rows.append([cursor.isoformat(), price, price + 2, price - 2, price + 1, 1000])
            cursor += timedelta(minutes=5)
            price += 1
        return rows


class FakeStore:
    def __init__(self, candles=None):
        self.candles = list(candles or [])
        self.initialized = 0
        self.upserted = []

    async def initialize(self):
        self.initialized += 1

    async def latest_candle_at(self, trading_symbol, timeframe_minutes):
        if not self.candles:
            return None
        return datetime.fromisoformat(self.candles[-1][0])

    async def upsert(self, records):
        self.upserted.extend(records)
        return len(records)

    async def read_symbol_contract_segments(self, symbol, timeframe_minutes, start, end):
        rows = [
            row for row in self.candles
            if start <= datetime.fromisoformat(row[0]) <= end
        ]
        return [{
            "trading_symbol": tape.FROZEN_CURRENT_CONTRACT,
            "expiry_date": "2026-09-21",
            "candles": rows,
        }] if rows else []


class CrudeOilMiniResearchTapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_range_uses_bounded_certified_mini_chunks(self):
        provider = FakeProvider()
        contract = {
            "trading_symbol": tape.FROZEN_CURRENT_CONTRACT,
            "instrument_type": "FUT",
            "groww_symbol": "MCX-CRUDEOILM-21Sep26-FUT",
        }
        start = datetime(2026, 8, 3, 9, 0, tzinfo=IST)
        end = datetime(2026, 8, 12, 9, 0, tzinfo=IST)
        rows = await tape._fetch_exact_range(provider, contract, start, end)
        self.assertGreater(len(rows), 0)
        self.assertEqual(len(provider.calls), 2)
        self.assertTrue(all(call[3] == "5minute" and call[4] == 5 for call in provider.calls))
        self.assertEqual(datetime.fromisoformat(rows[0][0]), start)
        self.assertEqual(datetime.fromisoformat(rows[-1][0]), end)

    async def test_certification_uses_only_exact_frozen_current_contract(self):
        store = FakeStore(_complete_session(3))
        report = await tape.certify_frozen_research_tape(
            store,
            contract={
                "trading_symbol": tape.FROZEN_CURRENT_CONTRACT,
                "expiry_date": "2026-09-21",
            },
        )
        self.assertEqual(report["status"], "CERTIFIED")
        self.assertEqual(report["reference_contract"], tape.FROZEN_CURRENT_CONTRACT)
        self.assertEqual(report["complete_sessions"], 1)
        self.assertEqual(report["duplicate_timestamps"], 0)
        self.assertEqual(report["non_monotonic_pairs"], 0)
        self.assertEqual(report["off_grid_bars"], 0)
        self.assertEqual(report["ohlcv_errors"], 0)
        self.assertTrue(report["tape_sha256"])
        self.assertTrue(report["integrity"]["exact_current_contract_only"])
        self.assertFalse(report["integrity"]["regular_crude_used"])
        self.assertFalse(report["integrity"]["copper_data_used"])

    async def test_canonical_frozen_tape_is_reused_before_any_groww_call(self):
        canonical = {
            "status": "CERTIFIED",
            "reference_contract": tape.FROZEN_CURRENT_CONTRACT,
            "tape_sha256": tape.FROZEN_TAPE_SHA256,
            "candles": tape.FROZEN_TAPE_CANDLES,
            "first_at": tape.FROZEN_TAPE_FIRST_AT,
            "last_at": tape.FROZEN_TAPE_LAST_AT,
            "complete_sessions": tape.FROZEN_COMPLETE_SESSIONS,
            "complete_session_first": tape.FROZEN_COMPLETE_SESSION_FIRST,
            "complete_session_last": tape.FROZEN_COMPLETE_SESSION_LAST,
        }
        provider = FakeProvider()
        store = FakeStore([])
        with patch.object(
            tape,
            "certify_frozen_research_tape",
            new=AsyncMock(return_value=canonical),
        ), patch.object(
            tape,
            "_frozen_contract",
            new=AsyncMock(side_effect=AssertionError("Groww/master should not be touched for canonical tape")),
        ):
            report = await tape.refresh_frozen_research_tape(provider, store)
        self.assertEqual(report["refresh"]["mode"], "REUSED_CANONICAL_FROZEN_TAPE")
        self.assertFalse(report["refresh"]["provider_called"])
        self.assertEqual(report["refresh"]["canonical_sha256"], tape.FROZEN_TAPE_SHA256)
        self.assertEqual(provider.calls, [])

    async def test_read_fails_closed_if_exact_contract_is_absent(self):
        store = FakeStore([])
        with self.assertRaises(RuntimeError):
            await tape.read_frozen_research_tape(store)

    def test_framework_summary_does_not_promote_a_candidate(self):
        report = {
            "mode": "CRUDE_OIL_MINI_COPPER_FRAMEWORK_RESEARCH_V1",
            "status": "AUDIT_COMPLETE",
            "reference_contract": tape.FROZEN_CURRENT_CONTRACT,
            "research_tape": {"status": "CERTIFIED", "tape_sha256": "abc"},
            "click_schedule_sha256": "clicks",
            "no_news_replay": {
                "complete_sessions": 4,
                "scheduled_clicks": 80,
                "evaluated_clicks": 80,
                "click_coverage_exact": True,
                "performance": {"trades": 3},
            },
            "memory_evidence_audit": {"selected_setups": 5, "target_first_pct_resolved": 60.0},
            "abstention_audit": {"overall": {"waits": 75, "large_move_candidates": 10}},
            "error_attribution": {"trade_observations": 3, "stable_above_50_pct_states": [], "stable_below_50_pct_states": []},
            "no_news_brain_freeze_status": "HUMAN_REVIEW_REQUIRED",
            "next_step": "review",
        }
        summary = framework.framework_summary(report)
        self.assertEqual(summary["no_news_brain_freeze_status"], "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(summary["scheduled_clicks"], 80)

    def test_tape_and_framework_do_not_import_copper_market_modules(self):
        for module in (tape, framework):
            source = inspect.getsource(module)
            self.assertNotIn("from .copper_", source)
            self.assertNotIn("import copper_", source)


if __name__ == "__main__":
    unittest.main()
