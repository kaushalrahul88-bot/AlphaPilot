"""Point-in-time archive adapter for Deribit BTC option Greeks / 25d skew.

The record represents a pair of *observed* Deribit ticker states chosen by their
actual option deltas. It is global options context only and is never treated as a
CoinDCX contract, quote or fill.
"""
from __future__ import annotations

from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture
from app.deribit_btc_options_ticker_greeks import DeribitBtcDeltaSkewSnapshot

DATASET = "BTC_GLOBAL_OPTIONS_GREEKS"
PROVIDER = "DERIBIT_TICKER"


def _leg_payload(row) -> dict:
    row.validated()
    return {
        "instrument_name": row.instrument_name,
        "option_type": row.option_type,
        "strike": float(row.strike),
        "expiry_at": row.expiry_at.isoformat(),
        "provider_time": row.provider_time.isoformat(),
        "first_seen_at": row.first_seen_at.isoformat(),
        "underlying_price_usd": float(row.underlying_price_usd),
        "mark_iv_pct": float(row.mark_iv_pct),
        "bid_iv_pct": None if row.bid_iv_pct is None else float(row.bid_iv_pct),
        "ask_iv_pct": None if row.ask_iv_pct is None else float(row.ask_iv_pct),
        "open_interest_btc": float(row.open_interest_btc),
        "delta": float(row.delta),
        "gamma": float(row.gamma),
        "theta": float(row.theta),
        "vega": float(row.vega),
        "rho": float(row.rho),
    }


def deribit_greeks_archive_record(snapshot: DeribitBtcDeltaSkewSnapshot) -> BtcPitArchiveRecord:
    row = snapshot.validated()
    payload = {
        "currency": "BTC",
        "expiry_at": row.expiry_at.isoformat(),
        "target_abs_delta": float(row.target_abs_delta),
        "call_delta_distance": float(row.call_delta_distance),
        "put_delta_distance": float(row.put_delta_distance),
        "put_call_skew_25d_iv_points": float(row.put_call_skew_25d_iv_points),
        "skew_25d_observed_from_ticker_delta": True,
        "skew_25d_inferred_from_strike": False,
        "call": _leg_payload(row.call),
        "put": _leg_payload(row.put),
        "global_options_context_only": True,
        "coindcx_contract_data": False,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "underlying_direction_assigned": False,
        "trade_generation_allowed": False,
    }
    source_key = (
        f"BTC_OPTIONS_GREEKS:{row.expiry_at.isoformat()}:"
        f"{row.call.instrument_name}:{row.put.instrument_name}:{row.first_seen_at.isoformat()}"
    )
    return archive_record_from_capture(
        dataset=DATASET,
        provider=PROVIDER,
        source_key=source_key,
        first_seen_at=row.first_seen_at,
        event_at=row.provider_time,
        source_version="DERIBIT_BTC_OPTIONS_GREEKS_V1",
        payload=payload,
    )


def architecture_contract() -> dict:
    return {
        "version": "DERIBIT_BTC_OPTIONS_GREEKS_PIT_V1",
        "dataset": DATASET,
        "first_seen_controls_click_visibility": True,
        "provider_time_preserved": True,
        "ticker_delta_observed": True,
        "delta_inferred_from_strike": False,
        "global_options_context_only": True,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "underlying_direction_assigned": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
