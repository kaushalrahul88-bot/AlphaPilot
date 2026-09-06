from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig
from app.crypto_btc_prospective_resolution_runtime import (
    BtcProspectiveResolutionRuntimeConfig,
    architecture_contract,
    resolve_due_btc_prospective_decisions_once,
)


class _FakeStore:
    def __init__(self, pending):
        self.pending = list(pending)
        self.attached = []
        self.initialized = 0
        self.pending_as_of_values = []

    async def initialize(self):
        self.initialized += 1
        return {"status": "READY"}

    async def pending_as_of(self, as_of):
        self.pending_as_of_values.append(as_of)
        return list(self.pending)

    async def attach_resolution(self, resolution):
        self.attached.append(resolution)
        return {"status": "ATTACHED_THESIS_RESOLUTION"}


class BtcProspectiveResolutionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_disabled_by_default_and_contract_is_research_only(self):
        config = BtcProspectiveResolutionRuntimeConfig.from_env({})
        self.assertFalse(config.enabled)
        self.assertEqual(config.poll_seconds, 60)
        contract = architecture_contract()
        self.assertFalse(contract["creates_decisions"])
        self.assertFalse(contract["changes_frozen_horizon"])
        self.assertTrue(contract["resolves_only_due_unresolved_decisions"])
        self.assertFalse(contract["options_pnl_measured"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)

    def test_poll_interval_fails_closed_below_30_seconds(self):
        with self.assertRaises(ValueError):
            BtcProspectiveResolutionRuntimeConfig(enabled=True, poll_seconds=29).validated()

    async def test_disabled_pass_does_not_touch_store(self):
        result = await resolve_due_btc_prospective_decisions_once(
            proof_config=BtcProspectiveProofRuntimeConfig(
                postgres_enabled=True,
                database_url="postgresql://example.invalid/db",
                evaluation_horizon_hours=4.0,
            ),
            resolution_config=BtcProspectiveResolutionRuntimeConfig(enabled=False, poll_seconds=60),
            store=_FakeStore([]),
            provider=object(),
        )
        self.assertEqual(result["status"], "BTC_PROSPECTIVE_RESOLUTION_DISABLED")
        self.assertEqual(result["due_count"], 0)

    async def test_resolved_due_row_is_attached_once(self):
        frozen = {"decision": {"click_id": "btc-click-1"}}
        store = _FakeStore([frozen])
        resolved = {
            "status": "THESIS_OUTCOME_RESOLVED",
            "click_id": "btc-click-1",
            "outcome": {"status": "OUTCOME_RESOLVED", "classification": "ABSTENTION_RESOLVED"},
        }
        now = datetime(2026, 9, 6, 7, 51, tzinfo=timezone.utc)
        with (
            patch(
                "app.crypto_btc_prospective_resolution_runtime.resolve_prospective_btc_thesis_from_coindcx",
                new=AsyncMock(return_value=resolved),
            ) as resolver,
            patch(
                "app.crypto_btc_prospective_resolution_runtime._resolution_for_persistence",
                side_effect=lambda value: value,
            ) as canonicalizer,
        ):
            result = await resolve_due_btc_prospective_decisions_once(
                proof_config=BtcProspectiveProofRuntimeConfig(
                    postgres_enabled=True,
                    database_url="postgresql://example.invalid/db",
                    evaluation_horizon_hours=4.0,
                ),
                resolution_config=BtcProspectiveResolutionRuntimeConfig(enabled=True, poll_seconds=60),
                now=now,
                store=store,
                provider=object(),
            )
        self.assertEqual(store.initialized, 1)
        self.assertEqual(store.pending_as_of_values, [now])
        self.assertEqual(store.attached, [resolved])
        self.assertEqual(result["due_count"], 1)
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["unresolved_count"], 0)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["resolved_click_ids"], ["btc-click-1"])
        resolver.assert_awaited_once()
        canonicalizer.assert_called_once_with(resolved)

    async def test_horizon_unresolved_row_remains_pending_for_retry(self):
        frozen = {"decision": {"click_id": "btc-click-2"}}
        store = _FakeStore([frozen])
        unresolved = {
            "status": "THESIS_OUTCOME_UNRESOLVED",
            "click_id": "btc-click-2",
            "outcome": {"status": "OUTCOME_UNRESOLVED"},
        }
        with patch(
            "app.crypto_btc_prospective_resolution_runtime.resolve_prospective_btc_thesis_from_coindcx",
            new=AsyncMock(return_value=unresolved),
        ):
            result = await resolve_due_btc_prospective_decisions_once(
                proof_config=BtcProspectiveProofRuntimeConfig(
                    postgres_enabled=True,
                    database_url="postgresql://example.invalid/db",
                    evaluation_horizon_hours=4.0,
                ),
                resolution_config=BtcProspectiveResolutionRuntimeConfig(enabled=True, poll_seconds=60),
                now=datetime(2026, 9, 6, 7, 51, tzinfo=timezone.utc),
                store=store,
                provider=object(),
            )
        self.assertEqual(store.attached, [])
        self.assertEqual(result["resolved_count"], 0)
        self.assertEqual(result["unresolved_count"], 1)
        self.assertEqual(result["unresolved_click_ids"], ["btc-click-2"])


if __name__ == "__main__":
    unittest.main()
