from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.crypto_btc_delta_options_probe_postgres import SCHEMA_SQL, architecture_contract as store_contract
from app.crypto_btc_delta_options_probe_runtime import DeltaOptionsProbeRuntimeConfig
from app.delta_india_btc_options_public_provider import (
    DELTA_INDIA_TICKERS_URL,
    DeltaIndiaBtcOptionsPublicProvider,
    DeltaIndiaOptionsProbePolicy,
    architecture_contract,
    normalize_delta_btc_options_snapshot,
)


def ticker(symbol, contract_type, strike, *, expiry_spot=80010, mark=230, mark_iv=0.46, bid=225, ask=235, oi=1000, ts=1788634200):
    return {
        "symbol": symbol,
        "contract_type": contract_type,
        "strike_price": str(strike),
        "spot_price": str(expiry_spot),
        "mark_price": str(mark),
        "mark_vol": str(mark_iv),
        "product_id": abs(hash(symbol)) % 100000,
        "timestamp": ts,
        "oi": str(oi),
        "volume": 250,
        "quotes": {
            "best_bid": str(bid),
            "best_ask": str(ask),
            "bid_size": "10",
            "ask_size": "12",
            "bid_iv": "0.45",
            "ask_iv": "0.47",
        },
        "greeks": {
            "delta": "0.52" if contract_type == "call_options" else "-0.48",
            "gamma": "0.0002",
            "theta": "-18.2",
            "vega": "7.3",
            "rho": "0.1",
        },
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class DeltaIndiaOptionsProbeTests(unittest.TestCase):
    def setUp(self):
        rows = []
        for strike in (79000, 79500, 80000, 80500, 81000):
            rows.append(ticker(f"C-BTC-{strike}-060926", "call_options", strike, mark=200 + (strike - 79000) / 10))
            rows.append(ticker(f"P-BTC-{strike}-060926", "put_options", strike, mark=220 + (81000 - strike) / 10))
        rows.append(ticker("C-BTC-80000-070926", "call_options", 80000))
        rows.append(ticker("P-BTC-80000-070926", "put_options", 80000))
        self.payload = {"success": True, "result": rows}
        self.seen = datetime(2026, 9, 5, 18, 50, 5, tzinfo=timezone.utc)

    def test_normalizes_nearest_expiry_atm_slice_with_real_quotes_and_greeks(self):
        snapshot = normalize_delta_btc_options_snapshot(self.payload, first_seen_at=self.seen, atm_strikes=3)
        self.assertEqual(snapshot.nearest_expiry.isoformat(), "2026-09-06")
        self.assertEqual(snapshot.full_chain_contract_count, 12)
        self.assertEqual(snapshot.nearest_expiry_contract_count, 10)
        self.assertEqual(snapshot.selected_strike_count, 3)
        self.assertEqual(len(snapshot.quotes), 6)
        self.assertEqual({row.option_type for row in snapshot.quotes}, {"CALL", "PUT"})
        self.assertTrue(all(row.best_bid is not None and row.best_ask is not None for row in snapshot.quotes))
        self.assertTrue(all(row.open_interest == 1000 for row in snapshot.quotes))
        self.assertTrue(all(row.mark_iv == 0.46 for row in snapshot.quotes))
        self.assertTrue(all(row.delta is not None for row in snapshot.quotes))
        frozen_quotes = snapshot.frozen_dict()["quotes"]
        self.assertTrue(all(row["mark_iv"] == 0.46 for row in frozen_quotes))
        self.assertEqual(snapshot.frozen_dict()["candidate_only"], True)
        self.assertEqual(snapshot.frozen_dict()["execution_enabled"], False)

    def test_public_provider_uses_documented_unauthenticated_tickers_filter(self):
        client = FakeClient(self.payload)
        provider = DeltaIndiaBtcOptionsPublicProvider(
            policy=DeltaIndiaOptionsProbePolicy(enabled=True, atm_strikes=3),
            client=client,
            clock=lambda: self.seen,
        )
        snapshot = provider.capture_btc_options_snapshot()
        self.assertEqual(len(client.calls), 1)
        url, kwargs = client.calls[0]
        self.assertEqual(url, DELTA_INDIA_TICKERS_URL)
        self.assertEqual(kwargs["params"]["underlying_asset_symbols"], "BTC")
        self.assertEqual(kwargs["params"]["contract_types"], "call_options,put_options")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertGreater(len(snapshot.quotes), 0)

    def test_provider_is_disabled_by_default(self):
        client = FakeClient(self.payload)
        provider = DeltaIndiaBtcOptionsPublicProvider(client=client)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            provider.capture_btc_options_snapshot()
        self.assertEqual(client.calls, [])

    def test_runtime_is_fail_closed_and_requires_database_only_when_enabled(self):
        disabled = DeltaOptionsProbeRuntimeConfig.from_env({})
        self.assertFalse(disabled.enabled)
        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            DeltaOptionsProbeRuntimeConfig.from_env(
                {"ALPHAPILOT_CRYPTO_DELTA_OPTIONS_PROBE_ENABLED": "true"}
            )
        enabled = DeltaOptionsProbeRuntimeConfig.from_env(
            {
                "ALPHAPILOT_CRYPTO_DELTA_OPTIONS_PROBE_ENABLED": "true",
                "DATABASE_URL": "postgresql://example",
                "ALPHAPILOT_CRYPTO_DELTA_OPTIONS_PROBE_ATM_STRIKES": "5",
            }
        )
        self.assertTrue(enabled.enabled)
        self.assertEqual(enabled.atm_strikes, 5)

    def test_probe_storage_is_database_immutable(self):
        contract = store_contract()
        self.assertFalse(contract["database_update_allowed"])
        self.assertFalse(contract["database_delete_allowed"])
        self.assertFalse(contract["database_truncate_allowed"])
        self.assertIn("BEFORE UPDATE OR DELETE", SCHEMA_SQL)
        self.assertIn("BEFORE TRUNCATE", SCHEMA_SQL)

    def test_architecture_has_no_trading_path_and_ui_parity_is_verified(self):
        contract = architecture_contract()
        self.assertFalse(contract["authentication_required"])
        self.assertFalse(contract["trading_permission_required"])
        self.assertFalse(contract["order_placement_enabled"])
        self.assertFalse(contract["live_execution_enabled"])
        self.assertTrue(contract["ui_cross_check_completed"])
        self.assertFalse(contract["candidate_only_until_ui_cross_check"])
        self.assertTrue(contract["candidate_only_until_explicit_user_adoption"])
        self.assertTrue(contract["mark_iv_persisted"])


if __name__ == "__main__":
    unittest.main()
