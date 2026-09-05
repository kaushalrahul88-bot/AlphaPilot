import unittest
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException

from app.crypto_macro_live_availability_api import (
    ROUTE_PATH,
    architecture_contract,
    register_crypto_macro_live_availability_routes,
)
from app.crypto_macro_live_availability_audit import MacroLiveAvailabilityAttempt
from app.crypto_macro_live_availability_runtime import MacroLiveAvailabilityRuntimeConfig


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
WINDOW_END = RELEASE + timedelta(minutes=10)
DATABASE_URL = "postgresql://example/alphapilot"


def _attempt():
    return MacroLiveAvailabilityAttempt(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        release_at=RELEASE,
        reaction_window_end=WINDOW_END,
        attempted_at=WINDOW_END + timedelta(seconds=30),
        completed_at=WINDOW_END + timedelta(seconds=31),
        status="AVAILABLE_WITHIN_LATENCY",
        availability_latency_seconds=31.0,
        nasdaq_contract_ticker="NQU6",
        euro_fx_contract_ticker="6EU6",
    )


class _Settings:
    database_url = DATABASE_URL


class _ReadOnlyStore:
    def __init__(self, rows=None, *, error=None):
        self.rows = list(rows or [])
        self.error = error
        self.list_calls = 0
        self.initialize_calls = 0
        self.insert_calls = 0

    async def list_attempts(self):
        self.list_calls += 1
        if self.error is not None:
            raise self.error
        return list(self.rows)

    async def initialize(self):
        self.initialize_calls += 1
        raise AssertionError("GET report must not initialize storage")

    async def insert_attempt(self, attempt):
        self.insert_calls += 1
        raise AssertionError("GET report must not write storage")


def _config(*, store_enabled=True, database_url=DATABASE_URL):
    return MacroLiveAvailabilityRuntimeConfig(
        store_enabled=store_enabled,
        database_url=database_url,
        audit_enabled=False,
        poll_seconds=15,
        max_latency_seconds=120.0,
        min_unique_events=3,
    )


class CryptoMacroLiveAvailabilityApiTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, *, store, config=None, auth=None):
        app = FastAPI()
        auth_calls = []
        config_calls = []

        def collector_auth(token):
            auth_calls.append(token)
            if auth is not None:
                return auth(token)
            if token != "TOKEN":
                raise HTTPException(status_code=401, detail="Invalid collector token")

        def config_loader():
            config_calls.append(True)
            return config if config is not None else _config()

        def store_factory(database_url):
            self.assertEqual(database_url, DATABASE_URL)
            return store

        register_crypto_macro_live_availability_routes(
            app,
            _Settings(),
            collector_auth,
            store_factory=store_factory,
            config_loader=config_loader,
        )
        return app, auth_calls, config_calls

    async def _get(self, app, *, token="TOKEN"):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(ROUTE_PATH, headers={"x-collector-token": token})

    async def test_valid_request_reads_only_persisted_attempts_and_exposes_no_secrets(self):
        store = _ReadOnlyStore([_attempt()])
        app, auth_calls, config_calls = self._app(store=store)
        response = await self._get(app)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "MACRO_LIVE_AVAILABILITY_REPORT_READY")
        self.assertEqual(payload["source"], "PERSISTED_MACRO_LIVE_AVAILABILITY_AUDIT")
        self.assertEqual(payload["coverage"]["unique_events_observed"], 1)
        self.assertEqual(payload["coverage"]["successful_event_count"], 1)
        self.assertFalse(payload["database_url_exposed"])
        self.assertFalse(payload["api_key_exposed"])
        self.assertFalse(payload["store_initialized_by_request"])
        self.assertFalse(payload["network_request_performed"])
        self.assertFalse(payload["live_confirmation_enabled"])
        self.assertFalse(payload["direction_generated"])
        self.assertFalse(payload["options_trade_generated"])
        self.assertFalse(payload["futures_trade_generated"])
        rendered = response.text
        self.assertNotIn(DATABASE_URL, rendered)
        self.assertNotIn("MASSIVE_API_KEY", rendered)
        self.assertEqual(auth_calls, ["TOKEN"])
        self.assertEqual(len(config_calls), 1)
        self.assertEqual(store.list_calls, 1)
        self.assertEqual(store.initialize_calls, 0)
        self.assertEqual(store.insert_calls, 0)

    async def test_authentication_happens_before_configuration_or_store_access(self):
        store = _ReadOnlyStore([_attempt()])
        app, auth_calls, config_calls = self._app(store=store)
        response = await self._get(app, token="WRONG")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(auth_calls, ["WRONG"])
        self.assertEqual(config_calls, [])
        self.assertEqual(store.list_calls, 0)

    async def test_disabled_store_is_explicit_503_not_empty_history(self):
        store = _ReadOnlyStore([_attempt()])
        app, _, _ = self._app(store=store, config=_config(store_enabled=False, database_url=""))
        response = await self._get(app)
        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "MACRO_LIVE_AVAILABILITY_REPORT_DISABLED")
        self.assertFalse(detail["live_confirmation_enabled"])
        self.assertEqual(store.list_calls, 0)

    async def test_report_database_must_match_authenticated_application_database(self):
        store = _ReadOnlyStore([_attempt()])
        config = _config(database_url="postgresql://other/database")
        app, _, _ = self._app(store=store, config=config)
        response = await self._get(app)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "MACRO_LIVE_AVAILABILITY_REPORT_DISABLED")
        self.assertEqual(store.list_calls, 0)

    async def test_invalid_runtime_configuration_is_not_silently_reported_as_empty(self):
        store = _ReadOnlyStore([_attempt()])

        class InvalidConfig:
            def validated(self):
                raise ValueError("invalid audit policy")

        app, _, _ = self._app(store=store, config=InvalidConfig())
        response = await self._get(app)
        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "MACRO_LIVE_AVAILABILITY_REPORT_DISABLED")
        self.assertIn("invalid audit policy", detail["message"])
        self.assertEqual(store.list_calls, 0)

    async def test_database_read_failure_is_explicit_unavailable_not_zero_coverage(self):
        store = _ReadOnlyStore(error=RuntimeError("audit table missing"))
        app, _, _ = self._app(store=store)
        response = await self._get(app)
        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "MACRO_LIVE_AVAILABILITY_REPORT_UNAVAILABLE")
        self.assertIn("audit table missing", detail["message"])
        self.assertFalse(detail["provider_network_called"])
        self.assertFalse(detail["store_initialized"])
        self.assertFalse(detail["store_written"])
        self.assertEqual(store.list_calls, 1)

    async def test_empty_existing_table_is_a_real_insufficient_sample_not_error(self):
        store = _ReadOnlyStore([])
        app, _, _ = self._app(store=store)
        response = await self._get(app)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["coverage"]["unique_events_observed"], 0)
        self.assertEqual(payload["qualification"]["status"], "INSUFFICIENT_PROSPECTIVE_EVENTS")
        self.assertFalse(payload["manual_review_required"])
        self.assertFalse(payload["live_confirmation_enabled"])

    def test_architecture_contract_keeps_endpoint_internal_read_only_and_nontrading(self):
        contract = architecture_contract()
        self.assertEqual(contract["route"], ROUTE_PATH)
        self.assertEqual(contract["method"], "GET")
        self.assertTrue(contract["internal_collector_auth_required"])
        self.assertTrue(contract["database_url_match_required"])
        self.assertTrue(contract["store_feature_switch_required"])
        self.assertTrue(contract["read_only"])
        self.assertFalse(contract["get_request_may_initialize_store"])
        self.assertFalse(contract["get_request_may_write_store"])
        self.assertFalse(contract["provider_network_call_allowed"])
        self.assertFalse(contract["database_url_exposed"])
        self.assertFalse(contract["api_key_exposed"])
        self.assertFalse(contract["configuration_error_is_empty_history"])
        self.assertFalse(contract["database_error_is_empty_history"])
        self.assertFalse(contract["qualification_auto_enables_live_confirmation"])
        self.assertFalse(contract["live_confirmation_enabled"])
        self.assertFalse(contract["direction_generated"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
