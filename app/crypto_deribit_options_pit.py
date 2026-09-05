"""Point-in-time archival adapter for Deribit BTC global options context.

Each current chain snapshot is a new AlphaPilot first-seen observation because
Deribit's periodic summary endpoint describes current state rather than proving
that the exact same summary was historically available at an earlier click.
The archived payload is global options context only and is explicitly barred from
CoinDCX contract selection, fills or economic replay.
"""
from __future__ import annotations

from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture
from app.deribit_btc_options_context_provider import DeribitBtcOptionsContextCapture

DATASET = "BTC_GLOBAL_OPTIONS_CONTEXT"
PROVIDER = "DERIBIT_PUBLIC_API"


def deribit_options_context_archive_record(capture: DeribitBtcOptionsContextCapture) -> BtcPitArchiveRecord:
    row = capture.validated()
    payload = {
        "currency": "BTC",
        "underlying_price_usd": float(row.underlying_price_usd),
        "nearest_expiry_at": row.nearest_expiry_at.isoformat(),
        "next_expiry_at": None if row.next_expiry_at is None else row.next_expiry_at.isoformat(),
        "atm_mark_iv_pct": float(row.atm_mark_iv_pct),
        "next_expiry_atm_mark_iv_pct": None if row.next_expiry_atm_mark_iv_pct is None else float(row.next_expiry_atm_mark_iv_pct),
        "term_structure_slope_iv_points": None if row.term_structure_slope_iv_points is None else float(row.term_structure_slope_iv_points),
        "total_call_open_interest_btc": float(row.total_call_open_interest_btc),
        "total_put_open_interest_btc": float(row.total_put_open_interest_btc),
        "put_call_open_interest_ratio": None if row.put_call_open_interest_ratio is None else float(row.put_call_open_interest_ratio),
        "matched_contract_count": int(row.matched_contract_count),
        "active_contract_count": int(row.active_contract_count),
        "valid_expiry_count": int(row.valid_expiry_count),
        "skew_25d": None,
        "skew_25d_inferred": False,
        "global_options_context_only": True,
        "coindcx_contract_data": False,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "underlying_direction_assigned": False,
        "trade_generation_allowed": False,
    }
    source_key = f"BTC_OPTIONS_CONTEXT:{row.first_seen_at.isoformat()}"
    return archive_record_from_capture(
        dataset=DATASET,
        provider=PROVIDER,
        source_key=source_key,
        first_seen_at=row.first_seen_at,
        event_at=None,
        source_version="DERIBIT_BTC_OPTIONS_CONTEXT_V1",
        payload=payload,
    )


def architecture_contract() -> dict:
    return {
        "version": "DERIBIT_BTC_OPTIONS_PIT_V1",
        "dataset": DATASET,
        "current_snapshot_is_backdated_history": False,
        "first_seen_controls_click_visibility": True,
        "new_poll_is_new_first_seen_snapshot": True,
        "skew_25d_inferred": False,
        "global_options_context_only": True,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "underlying_direction_assigned": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
