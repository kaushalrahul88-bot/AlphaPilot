from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.crypto_btc_delta_microstructure_capture import (
    DATASET,
    PROVIDER,
    DeltaMicrostructureRuntimeConfig,
    architecture_contract,
    delta_microstructure_archive_record,
    normalize_delta_microstructure_snapshot,
)
from app.crypto_btc_source_capabilities import capability_for, live_capture_plan

UTC = timezone.utc


def _payloads():
    ticker = {
        "success": True,
        "result": {
            "symbol": "BTCUSD",
            "close": 78950,
            "mark_price": "78960.5",
            "spot_price": "78942.0",
            "oi": "15250",
            "oi_value": "1200000000",
            "oi_value_usd": "1200500000",
            "volume": 25000,
            "turnover": 5000000,
            "turnover_usd": 5200000,
            "timestamp": 1788892200,
            "quotes": {"best_bid": "78959.5", "best_ask": "78961.0"},
        },
    }
    orderbook = {
        "success": True,
        "result": {
            "symbol": "BTCUSD",
            "last_updated_at": 1788892200123456,
            "buy": [
                {"depth": "12", "price": "78959.5", "size": 7},
                {"depth": "20", "price": "78958.0", "size": 5},
            ],
            "sell": [
                {"depth": "9", "price": "78961.0", "size": 3},
                {"depth": "15", "price": "78962.0", "size": 6},
            ],
        },
    }
    trades = {
        "success": True,
        "result": {
            "trades": [
                {"side": "buy", "size": 3, "price": "78960", "timestamp": 1788892199000000},
                {"side": "buy", "size": 2, "price": "78961", "timestamp": 1788892199500000},
                {"side": "sell", "size": 1, "price": "78959", "timestamp": 1788892199800000},
            ]
        },
    }
    return ticker, orderbook, trades


class DeltaMicrostructureCaptureTests(unittest.TestCase):
    def test_normalize_snapshot_is_point_in_time_and_non_tradeable(self):
        ticker, orderbook, trades = _payloads()
        first_seen = datetime(2026, 9, 8, 18, 30, 1, tzinfo=UTC)
        snapshot = normalize_delta_microstructure_snapshot(
            ticker, orderbook, trades, first_seen_at=first_seen
        )
        payload = snapshot["payload"]

        self.assertEqual(snapshot["first_seen_at"], first_seen)
        self.assertLessEqual(snapshot["event_at"], first_seen)
        self.assertEqual(payload["ticker"]["open_interest_contracts"], 15250.0)
        self.assertEqual(payload["ticker"]["open_interest_value_usd"], 1200500000.0)
        self.assertEqual(payload["orderbook"]["best_bid"], 78959.5)
        self.assertEqual(payload["orderbook"]["best_ask"], 78961.0)
        self.assertEqual(payload["orderbook"]["bid_size_top_levels"], 12.0)
        self.assertEqual(payload["orderbook"]["ask_size_top_levels"], 9.0)
        self.assertAlmostEqual(payload["orderbook"]["depth_imbalance"], 3.0 / 21.0)
        self.assertGreater(payload["orderbook"]["spread_bps"], 0)
        self.assertEqual(payload["recent_trades"]["sample_count"], 3)
        self.assertEqual(payload["recent_trades"]["buy_count"], 2)
        self.assertEqual(payload["recent_trades"]["sell_count"], 1)
        self.assertGreater(payload["recent_trades"]["notional_imbalance"], 0)
        self.assertFalse(payload["provenance"]["historical_reconstruction"])
        self.assertFalse(payload["provenance"]["future_rows_used"])
        self.assertFalse(payload["may_generate_options_trade"])
        self.assertFalse(payload["may_generate_futures_trade"])
        self.assertFalse(payload["may_satisfy_options_contract_quote"])
        self.assertFalse(payload["live_execution"])
        self.assertEqual(payload["capital_committed_inr"], 0)

    def test_archive_record_uses_irrecoverable_pit_registry(self):
        ticker, orderbook, trades = _payloads()
        first_seen = datetime(2026, 9, 8, 18, 30, 1, tzinfo=UTC)
        snapshot = normalize_delta_microstructure_snapshot(
            ticker, orderbook, trades, first_seen_at=first_seen
        )
        record = delta_microstructure_archive_record(snapshot)
        frozen = record.frozen_dict()

        self.assertEqual(frozen["dataset"], DATASET)
        self.assertEqual(frozen["provider"], PROVIDER)
        self.assertTrue(frozen["point_in_time_proven"])
        self.assertEqual(frozen["first_seen_at"], first_seen.isoformat())
        self.assertTrue(frozen["payload_hash"])
        self.assertTrue(frozen["record_fingerprint"])
        capability = capability_for(DATASET)
        self.assertFalse(capability.can_reconstruct_later)
        self.assertEqual(capability.historical_mode, "FIRST_SEEN_ARCHIVE_REQUIRED")
        self.assertEqual(capability.decision_role, "CONTEXT_ONLY")
        self.assertIn(DATASET, live_capture_plan()["capture_first"])

    def test_runtime_is_explicitly_gated(self):
        disabled = DeltaMicrostructureRuntimeConfig.from_env({})
        self.assertFalse(disabled.enabled)
        self.assertEqual(disabled.poll_seconds, 60)

        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            DeltaMicrostructureRuntimeConfig.from_env(
                {"ALPHAPILOT_CRYPTO_BTC_DELTA_MICROSTRUCTURE_ENABLED": "true"}
            )

        with self.assertRaisesRegex(ValueError, "poll_seconds"):
            DeltaMicrostructureRuntimeConfig.from_env(
                {
                    "ALPHAPILOT_CRYPTO_BTC_DELTA_MICROSTRUCTURE_ENABLED": "true",
                    "DATABASE_URL": "postgresql://example",
                    "ALPHAPILOT_CRYPTO_BTC_DELTA_MICROSTRUCTURE_POLL_SECONDS": "10",
                }
            )

        enabled = DeltaMicrostructureRuntimeConfig.from_env(
            {
                "ALPHAPILOT_CRYPTO_BTC_DELTA_MICROSTRUCTURE_ENABLED": "true",
                "DATABASE_URL": "postgresql://example",
                "ALPHAPILOT_CRYPTO_BTC_DELTA_MICROSTRUCTURE_POLL_SECONDS": "60",
            }
        )
        self.assertTrue(enabled.enabled)
        self.assertEqual(enabled.poll_seconds, 60)

    def test_architecture_contract_preserves_instrument_separation(self):
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertFalse(contract["historical_reconstruction"])
        self.assertFalse(contract["options_quote_substitution_allowed"])
        self.assertFalse(contract["options_trade_generation_allowed"])
        self.assertFalse(contract["futures_trade_generation_allowed"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed_inr"], 0)
        self.assertTrue(contract["research_only"])


if __name__ == "__main__":
    unittest.main()
