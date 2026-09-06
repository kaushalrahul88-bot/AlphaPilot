from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.crypto_btc_live_shadow_click import (
    LiveShadowOptionSelectionPolicy,
    architecture_contract,
    select_delta_option_for_shadow_entry,
)
from app.crypto_btc_live_shadow_click_postgres import _params
from app.crypto_btc_live_shadow_click_startup import architecture_contract as startup_contract


def _snapshot(seen: datetime) -> dict:
    def quote(symbol: str, option_type: str, strike: float, bid: float, ask: float, delta: float) -> dict:
        return {
            "symbol": symbol,
            "product_id": 1,
            "option_type": option_type,
            "expiry_date": "2026-09-06",
            "strike_price": strike,
            "spot_price": 79950.0,
            "mark_price": (bid + ask) / 2,
            "best_bid": bid,
            "best_ask": ask,
            "bid_size": 1.0,
            "ask_size": 1.0,
            "bid_iv": 0.18,
            "ask_iv": 0.19,
            "open_interest": 10.0,
            "volume": 20.0,
            "greeks": {"delta": delta, "gamma": 0.001, "theta": -1.0, "vega": 1.0, "rho": 0.1},
            "provider_at": seen.isoformat(),
        }

    return {
        "version": "DELTA_INDIA_BTC_OPTIONS_LIVE_PROBE_V1",
        "venue": "DELTA_EXCHANGE_INDIA",
        "underlying": "BTC",
        "candidate_only": True,
        "primary_trade_instrument": "OPTIONS",
        "execution_enabled": False,
        "trading_auth_used": False,
        "public_market_data_only": True,
        "first_seen_at": seen.isoformat(),
        "nearest_expiry": "2026-09-06",
        "reference_spot_price": 79950.0,
        "quotes": [
            quote("C-BTC-79800-060926", "CALL", 79800, 250, 254, 0.58),
            quote("C-BTC-80000-060926", "CALL", 80000, 150, 153, 0.48),
            quote("P-BTC-79800-060926", "PUT", 79800, 105, 108, -0.42),
            quote("P-BTC-80000-060926", "PUT", 80000, 200, 203, -0.52),
        ],
    }


def test_unknown_direction_is_no_trade() -> None:
    seen = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
    result = select_delta_option_for_shadow_entry(_snapshot(seen), market_direction="UNKNOWN", decision_at=seen)
    assert result == {"status": "NO_TRADE", "reason": "UNDERLYING_THESIS_UNKNOWN", "option_entry": None}


def test_bullish_uses_real_call_ask_and_never_mark() -> None:
    seen = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
    result = select_delta_option_for_shadow_entry(
        _snapshot(seen), market_direction="BULLISH", decision_at=seen + timedelta(seconds=10)
    )
    entry = result["option_entry"]
    assert result["status"] == "OPTIONS_SHADOW_ENTRY"
    assert entry["symbol"] == "C-BTC-80000-060926"
    assert entry["entry_ask"] == 153
    assert entry["entry_mark"] == 151.5
    assert entry["entry_fill_basis"] == "OBSERVED_BEST_ASK"
    assert entry["mark_price_used_as_fill"] is False
    assert entry["model_price_used"] is False
    assert entry["futures_quote_used"] is False


def test_bearish_uses_put_side_only() -> None:
    seen = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
    result = select_delta_option_for_shadow_entry(
        _snapshot(seen), market_direction="BEARISH", decision_at=seen + timedelta(seconds=5)
    )
    assert result["option_entry"]["option_type"] == "PUT"
    assert result["option_entry"]["symbol"] == "P-BTC-80000-060926"
    assert result["option_entry"]["entry_ask"] == 203


def test_future_snapshot_is_rejected() -> None:
    decision = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="after decision_at"):
        select_delta_option_for_shadow_entry(
            _snapshot(decision + timedelta(seconds=1)), market_direction="BULLISH", decision_at=decision
        )


def test_stale_snapshot_is_no_trade() -> None:
    seen = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
    result = select_delta_option_for_shadow_entry(
        _snapshot(seen),
        market_direction="BULLISH",
        decision_at=seen + timedelta(seconds=121),
        policy=LiveShadowOptionSelectionPolicy(max_quote_age_seconds=120),
    )
    assert result["status"] == "NO_TRADE"
    assert result["reason"] == "DELTA_OPTION_SNAPSHOT_STALE"


def test_persistence_rejects_option_snapshot_after_decision() -> None:
    decision = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
    record = {
        "request_id": "test-1",
        "decision_at": decision.isoformat(),
        "outcome_due_at": (decision + timedelta(hours=4)).isoformat(),
        "market_direction": "BULLISH",
        "shadow_status": "OPTIONS_SHADOW_ENTRY_FROZEN",
        "option_entry": {
            "symbol": "C-BTC-80000-060926",
            "entry_ask": 153,
            "snapshot_first_seen_at": (decision + timedelta(seconds=1)).isoformat(),
        },
        "order_placed": False,
        "live_execution": False,
        "capital_committed": 0,
    }
    with pytest.raises(ValueError, match="after decision_at"):
        _params(record)


def test_no_trade_cannot_smuggle_option_entry() -> None:
    decision = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="may not contain an option entry"):
        _params({
            "request_id": "test-2",
            "decision_at": decision.isoformat(),
            "outcome_due_at": (decision + timedelta(hours=4)).isoformat(),
            "market_direction": "UNKNOWN",
            "shadow_status": "NO_TRADE_FROZEN",
            "option_entry": {"symbol": "fake", "entry_ask": 1, "snapshot_first_seen_at": decision.isoformat()},
            "order_placed": False,
            "live_execution": False,
            "capital_committed": 0,
        })


def test_contract_is_explicitly_shadow_only_and_server_time_only() -> None:
    contract = architecture_contract()
    startup = startup_contract()
    assert contract["decision_time_source"] == "SERVER_CLOCK_ONLY"
    assert contract["caller_backdating_allowed"] is False
    assert contract["exact_observed_ask_required_for_entry"] is True
    assert contract["exact_observed_bid_required_for_exit"] is True
    assert contract["live_order_placement"] is False
    assert contract["capital_committed"] == 0
    assert startup["automatic_recurring_clicks"] is False
    assert startup["caller_supplied_decision_at"] is False
