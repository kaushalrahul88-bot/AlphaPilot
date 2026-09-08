from __future__ import annotations

from datetime import datetime, timezone

import pytest

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


def test_normalize_delta_microstructure_snapshot_is_point_in_time_and_non_tradeable():
    ticker, orderbook, trades = _payloads()
    first_seen = datetime(2026, 9, 8, 18, 30, 1, tzinfo=UTC)
    snapshot = normalize_delta_microstructure_snapshot(
        ticker, orderbook, trades, first_seen_at=first_seen
    )
    payload = snapshot["payload"]

    assert snapshot["first_seen_at"] == first_seen
    assert snapshot["event_at"] <= first_seen
    assert payload["ticker"]["open_interest_contracts"] == 15250.0
    assert payload["ticker"]["open_interest_value_usd"] == 1200500000.0
    assert payload["orderbook"]["best_bid"] == 78959.5
    assert payload["orderbook"]["best_ask"] == 78961.0
    assert payload["orderbook"]["bid_size_top_levels"] == 12.0
    assert payload["orderbook"]["ask_size_top_levels"] == 9.0
    assert payload["orderbook"]["depth_imbalance"] == pytest.approx(3.0 / 21.0)
    assert payload["orderbook"]["spread_bps"] > 0
    assert payload["recent_trades"]["sample_count"] == 3
    assert payload["recent_trades"]["buy_count"] == 2
    assert payload["recent_trades"]["sell_count"] == 1
    assert payload["recent_trades"]["notional_imbalance"] > 0
    assert payload["provenance"]["historical_reconstruction"] is False
    assert payload["provenance"]["future_rows_used"] is False
    assert payload["may_generate_options_trade"] is False
    assert payload["may_generate_futures_trade"] is False
    assert payload["may_satisfy_options_contract_quote"] is False
    assert payload["live_execution"] is False
    assert payload["capital_committed_inr"] == 0


def test_delta_microstructure_archive_record_uses_irrecoverable_pit_registry():
    ticker, orderbook, trades = _payloads()
    first_seen = datetime(2026, 9, 8, 18, 30, 1, tzinfo=UTC)
    snapshot = normalize_delta_microstructure_snapshot(
        ticker, orderbook, trades, first_seen_at=first_seen
    )
    record = delta_microstructure_archive_record(snapshot)
    frozen = record.frozen_dict()

    assert frozen["dataset"] == DATASET
    assert frozen["provider"] == PROVIDER
    assert frozen["point_in_time_proven"] is True
    assert frozen["first_seen_at"] == first_seen.isoformat()
    assert frozen["payload_hash"]
    assert frozen["record_fingerprint"]
    capability = capability_for(DATASET)
    assert capability.can_reconstruct_later is False
    assert capability.historical_mode == "FIRST_SEEN_ARCHIVE_REQUIRED"
    assert capability.decision_role == "CONTEXT_ONLY"
    assert DATASET in live_capture_plan()["capture_first"]


def test_delta_microstructure_runtime_is_explicitly_gated():
    disabled = DeltaMicrostructureRuntimeConfig.from_env({})
    assert disabled.enabled is False
    assert disabled.poll_seconds == 60

    with pytest.raises(ValueError, match="DATABASE_URL"):
        DeltaMicrostructureRuntimeConfig.from_env(
            {"ALPHAPILOT_CRYPTO_BTC_DELTA_MICROSTRUCTURE_ENABLED": "true"}
        )

    with pytest.raises(ValueError, match="poll_seconds"):
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
    assert enabled.enabled is True
    assert enabled.poll_seconds == 60


def test_delta_microstructure_architecture_contract_preserves_instrument_separation():
    contract = architecture_contract()
    assert contract["collection_enabled_by_default"] is False
    assert contract["historical_reconstruction"] is False
    assert contract["options_quote_substitution_allowed"] is False
    assert contract["options_trade_generation_allowed"] is False
    assert contract["futures_trade_generation_allowed"] is False
    assert contract["live_execution"] is False
    assert contract["capital_committed_inr"] == 0
    assert contract["research_only"] is True
