from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from app.crypto_btc_delta_options_probe_api import (
    ROUTE_PATH,
    architecture_contract,
    capture_one_delta_btc_options_snapshot,
    register_delta_options_probe_routes,
)


class _FakeProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    def capture_btc_options_snapshot(self):
        self.calls += 1
        return self.snapshot


class _FakeStore:
    def __init__(self):
        self.initialized = 0
        self.snapshots = []

    async def initialize(self):
        self.initialized += 1
        return {"status": "READY"}

    async def insert_first_seen(self, snapshot):
        self.snapshots.append(snapshot)
        return {
            "status": "INSERTED_FIRST_SEEN",
            "snapshot_id": "snapshot-1",
            "first_seen_at": "2026-09-06T05:00:00+00:00",
            "quote_count": len(snapshot.quotes),
        }


class _FakeApp:
    def __init__(self):
        self.routes = {}

    def post(self, path):
        def decorator(func):
            self.routes[("POST", path)] = func
            return func
        return decorator


class DeltaOptionsProbeApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_shot_capture_persists_fresh_candidate_snapshot_only(self):
        snapshot = SimpleNamespace(
            nearest_expiry=date(2026, 9, 7),
            reference_spot_price=80000.0,
            quotes=(object(), object()),
            selected_strike_count=1,
            full_chain_contract_count=200,
        )
        provider = _FakeProvider(snapshot)
        store = _FakeStore()
        result = await capture_one_delta_btc_options_snapshot(provider=provider, store=store)

        self.assertEqual(provider.calls, 1)
        self.assertEqual(store.initialized, 1)
        self.assertEqual(store.snapshots, [snapshot])
        self.assertEqual(result["status"], "DELTA_OPTIONS_SNAPSHOT_CAPTURED")
        self.assertTrue(result["point_in_time_proven"])
        self.assertTrue(result["candidate_only"])
        self.assertFalse(result["venue_promoted"])
        self.assertTrue(result["public_market_data_only"])
        self.assertFalse(result["api_key_used"])
        self.assertFalse(result["account_accessed"])
        self.assertFalse(result["options_trade_generated"])
        self.assertFalse(result["futures_trade_generated"])
        self.assertFalse(result["live_execution"])
        self.assertEqual(result["capital_committed"], 0)

    async def test_registered_route_authenticates_before_config_or_provider_work(self):
        app = _FakeApp()
        auth_calls = []

        def collector_auth(token):
            auth_calls.append(token)
            raise HTTPException(status_code=401, detail="Invalid collector token")

        register_delta_options_probe_routes(
            app,
            SimpleNamespace(database_url="postgresql://example.invalid/db"),
            collector_auth,
        )
        handler = app.routes[("POST", ROUTE_PATH)]
        with self.assertRaises(HTTPException) as caught:
            await handler(x_collector_token=None)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(auth_calls, [None])

    def test_architecture_contract_is_non_trading_and_candidate_only(self):
        contract = architecture_contract()
        self.assertTrue(contract["internal_collector_auth_required"])
        self.assertTrue(contract["fresh_public_delta_request_per_invocation"])
        self.assertTrue(contract["candidate_only"])
        self.assertFalse(contract["venue_promotion_automatic"])
        self.assertFalse(contract["api_key_required"])
        self.assertFalse(contract["account_access_allowed"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)

    def test_github_schedule_reuses_existing_secret_and_runs_every_five_minutes(self):
        workflow = Path(".github/workflows/scheduled-crypto-btc-options-collector.yml").read_text()
        self.assertIn('cron: "3,8,13,18,23,28,33,38,43,48,53,58 * * * *"', workflow)
        self.assertIn("secrets.COMMODITY_COLLECTOR_TOKEN", workflow)
        self.assertIn(ROUTE_PATH, workflow)
        self.assertIn("/health", workflow)
        self.assertNotIn("DELTA_API_KEY", workflow)
        self.assertNotIn("COINDCX_API_KEY", workflow)
        self.assertNotIn("place-order", workflow.lower())


if __name__ == "__main__":
    unittest.main()
