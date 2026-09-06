"""Read-only dashboard projection for BTC Crypto prospective research.

This module exposes only sanitized research status needed by the AlphaPilot
command center. It never accepts decisions, never starts collection, never
places orders, and never exposes database credentials or collector secrets.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.crypto_btc_delta_options_probe_postgres import TABLE_NAME as DELTA_TABLE
from app.crypto_btc_live_shadow_click_postgres import TABLE_NAME as SHADOW_TABLE
from app.crypto_btc_prospective_thesis_postgres import (
    DECISION_TABLE,
    RESOLUTION_TABLE,
)

MODE = "BTC_CRYPTO_DASHBOARD_STATUS_V1"


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _utc_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _decode(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _safe_outcome(payload: dict | None) -> dict | None:
    row = (payload or {}).get("outcome")
    if not isinstance(row, dict):
        return None
    return {
        "status": row.get("status"),
        "classification": row.get("classification"),
        "realized_direction": row.get("realized_direction"),
        "directional_hit": row.get("directional_hit"),
        "entry_btc_price": row.get("entry_btc_price"),
        "terminal_btc_price": row.get("terminal_btc_price"),
        "terminal_return_pct": row.get("terminal_return_pct"),
        "max_up_pct": row.get("max_up_pct"),
        "max_down_pct": row.get("max_down_pct"),
        "max_abs_move_pct": row.get("max_abs_move_pct"),
        "large_move_after_click": row.get("large_move_after_click"),
        "large_move_missed_during_abstention": row.get("large_move_missed_during_abstention"),
        "performance_eligible": row.get("performance_eligible"),
    }


def _safe_shadow_row(row: tuple) -> dict:
    (
        request_id,
        decision_at,
        outcome_due_at,
        market_direction,
        shadow_status,
        option_symbol,
        entry_ask,
        option_snapshot_first_seen_at,
        click_payload_raw,
        resolution_classification,
        resolution_at,
        resolution_payload_raw,
    ) = row
    click_payload = _decode(click_payload_raw)
    proof = click_payload.get("proof_bridge") if isinstance(click_payload.get("proof_bridge"), dict) else {}
    option = click_payload.get("option_entry") if isinstance(click_payload.get("option_entry"), dict) else None
    resolution_payload = _decode(resolution_payload_raw)
    return {
        "request_id": request_id,
        "decision_at": _utc_iso(decision_at),
        "outcome_due_at": _utc_iso(outcome_due_at),
        "market_direction": str(market_direction or "UNKNOWN").upper(),
        "shadow_status": shadow_status,
        "reason": click_payload.get("reason"),
        "decision_btc_price": proof.get("decision_btc_price"),
        "delta_reference_spot_price": click_payload.get("delta_reference_spot_price"),
        "option": None if option is None else {
            "symbol": option_symbol or option.get("symbol"),
            "option_type": option.get("option_type"),
            "strike_price": option.get("strike_price"),
            "entry_ask": entry_ask if entry_ask is not None else option.get("entry_ask"),
            "entry_bid": option.get("entry_bid"),
            "entry_mark": option.get("entry_mark"),
            "expiry_date": option.get("expiry_date"),
            "snapshot_first_seen_at": _utc_iso(option_snapshot_first_seen_at) or option.get("snapshot_first_seen_at"),
            "relative_spread_pct": option.get("relative_spread_pct"),
            "open_interest": option.get("open_interest"),
            "volume": option.get("volume"),
            "delta": ((option.get("greeks") or {}).get("delta") if isinstance(option.get("greeks"), dict) else None),
        },
        "resolution": None if resolution_at is None else {
            "classification": resolution_classification,
            "resolution_at": _utc_iso(resolution_at),
            "outcome": _safe_outcome(resolution_payload),
        },
    }


def _dashboard_status_from_rows(
    *,
    delta_summary: tuple,
    delta_latest: tuple | None,
    thesis_summary: tuple,
    resolution_summary: tuple,
    shadow_summary: tuple,
    recent_shadow_rows: list[tuple],
) -> dict:
    delta_count, delta_first, delta_last = delta_summary
    decision_count, directional_count, unknown_count, pending_count, next_due = thesis_summary
    resolution_count, hit_count, miss_count, inconclusive_count, abstention_count, missed_large_count = resolution_summary
    shadow_count, option_entry_count, no_trade_count, unresolved_input_count = shadow_summary

    latest_delta = None
    if delta_latest is not None:
        first_seen_at, nearest_expiry, reference_spot_price, quote_count = delta_latest
        latest_delta = {
            "first_seen_at": _utc_iso(first_seen_at),
            "nearest_expiry": None if nearest_expiry is None else str(nearest_expiry),
            "reference_spot_price": reference_spot_price,
            "quote_count": int(quote_count or 0),
        }

    recent = [_safe_shadow_row(row) for row in recent_shadow_rows]
    scored = int(hit_count or 0) + int(miss_count or 0)
    return {
        "mode": MODE,
        "status": "ACTIVE",
        "research_only": True,
        "read_only": True,
        "asset": "BTC",
        "trade_instrument": "OPTIONS_PRIMARY",
        "collection": {
            "venue": "DELTA_EXCHANGE_INDIA",
            "candidate_only": True,
            "snapshot_count": int(delta_count or 0),
            "first_snapshot_at": _utc_iso(delta_first),
            "latest_snapshot_at": _utc_iso(delta_last),
            "latest": latest_delta,
            "public_market_data_only": True,
            "api_key_required": False,
        },
        "prospective_proof": {
            "decision_count": int(decision_count or 0),
            "directional_decision_count": int(directional_count or 0),
            "abstention_decision_count": int(unknown_count or 0),
            "pending_resolution_count": int(pending_count or 0),
            "next_outcome_due_at": _utc_iso(next_due),
            "resolved_count": int(resolution_count or 0),
            "directional_hit_count": int(hit_count or 0),
            "directional_miss_count": int(miss_count or 0),
            "directional_inconclusive_count": int(inconclusive_count or 0),
            "abstention_resolved_count": int(abstention_count or 0),
            "abstention_large_move_missed_count": int(missed_large_count or 0),
            "directional_accuracy": None if scored == 0 else int(hit_count or 0) / scored,
            "evaluation_horizon_hours": 4,
        },
        "live_shadow": {
            "click_count": int(shadow_count or 0),
            "options_entry_count": int(option_entry_count or 0),
            "no_trade_count": int(no_trade_count or 0),
            "proof_input_unresolved_count": int(unresolved_input_count or 0),
            "latest": recent[0] if recent else None,
            "recent": recent,
        },
        "safety": {
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "futures_trade_generation_enabled": False,
            "capital_committed": 0,
            "collector_credentials_exposed": False,
            "database_credentials_exposed": False,
        },
    }


def _read_dashboard_status_sync(database_url: str) -> dict:
    delta_summary_sql = f"SELECT COUNT(*)::BIGINT, MIN(first_seen_at), MAX(first_seen_at) FROM {DELTA_TABLE};"
    delta_latest_sql = f"""
        SELECT first_seen_at, nearest_expiry, reference_spot_price, quote_count
        FROM {DELTA_TABLE}
        ORDER BY first_seen_at DESC
        LIMIT 1;
    """
    thesis_summary_sql = f"""
        SELECT
            COUNT(d.*)::BIGINT,
            COUNT(*) FILTER (WHERE d.market_direction IN ('BULLISH','BEARISH'))::BIGINT,
            COUNT(*) FILTER (WHERE d.market_direction = 'UNKNOWN')::BIGINT,
            COUNT(*) FILTER (WHERE r.click_id IS NULL)::BIGINT,
            MIN(d.outcome_due_at) FILTER (WHERE r.click_id IS NULL)
        FROM {DECISION_TABLE} d
        LEFT JOIN {RESOLUTION_TABLE} r ON r.click_id = d.click_id;
    """
    resolution_summary_sql = f"""
        SELECT
            COUNT(*)::BIGINT,
            COUNT(*) FILTER (WHERE classification = 'DIRECTIONAL_HIT')::BIGINT,
            COUNT(*) FILTER (WHERE classification = 'DIRECTIONAL_MISS')::BIGINT,
            COUNT(*) FILTER (WHERE classification = 'DIRECTIONAL_INCONCLUSIVE')::BIGINT,
            COUNT(*) FILTER (WHERE classification = 'ABSTENTION_RESOLVED')::BIGINT,
            COUNT(*) FILTER (
                WHERE classification = 'ABSTENTION_RESOLVED'
                AND COALESCE((payload->'outcome'->>'large_move_missed_during_abstention')::BOOLEAN, FALSE)
            )::BIGINT
        FROM {RESOLUTION_TABLE};
    """
    shadow_summary_sql = f"""
        SELECT
            COUNT(*)::BIGINT,
            COUNT(*) FILTER (WHERE shadow_status = 'OPTIONS_SHADOW_ENTRY_FROZEN')::BIGINT,
            COUNT(*) FILTER (WHERE shadow_status = 'NO_TRADE_FROZEN')::BIGINT,
            COUNT(*) FILTER (WHERE shadow_status = 'PROOF_INPUT_UNRESOLVED')::BIGINT
        FROM {SHADOW_TABLE};
    """
    recent_shadow_sql = f"""
        SELECT
            c.request_id, c.decision_at, c.outcome_due_at, c.market_direction,
            c.shadow_status, c.option_symbol, c.entry_ask,
            c.option_snapshot_first_seen_at, c.payload,
            r.classification, r.resolution_at, r.payload
        FROM {SHADOW_TABLE} c
        LEFT JOIN {RESOLUTION_TABLE} r
          ON r.click_id = (c.payload->>'click_id')
        ORDER BY c.decision_at DESC
        LIMIT 10;
    """
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(delta_summary_sql)
            delta_summary = cursor.fetchone()
            cursor.execute(delta_latest_sql)
            delta_latest = cursor.fetchone()
            cursor.execute(thesis_summary_sql)
            thesis_summary = cursor.fetchone()
            cursor.execute(resolution_summary_sql)
            resolution_summary = cursor.fetchone()
            cursor.execute(shadow_summary_sql)
            shadow_summary = cursor.fetchone()
            cursor.execute(recent_shadow_sql)
            recent_shadow_rows = cursor.fetchall()
    return _dashboard_status_from_rows(
        delta_summary=delta_summary,
        delta_latest=delta_latest,
        thesis_summary=thesis_summary,
        resolution_summary=resolution_summary,
        shadow_summary=shadow_summary,
        recent_shadow_rows=recent_shadow_rows,
    )


async def read_crypto_btc_dashboard_status(database_url: str) -> dict:
    database_url = str(database_url or "").strip()
    if not database_url:
        return {
            "mode": MODE,
            "status": "UNAVAILABLE",
            "research_only": True,
            "read_only": True,
            "reason": "DATABASE_URL_NOT_CONFIGURED",
        }
    return await asyncio.to_thread(_read_dashboard_status_sync, database_url)


def register_crypto_btc_dashboard_routes(app, settings) -> None:
    @app.get("/v1/dashboard/crypto/btc/status")
    async def crypto_btc_dashboard_status():
        return await read_crypto_btc_dashboard_status(getattr(settings, "database_url", ""))


def architecture_contract() -> dict:
    return {
        "version": "BTC_CRYPTO_DASHBOARD_API_CONTRACT_V1",
        "read_only": True,
        "public_status_only": True,
        "decision_creation_allowed": False,
        "collection_start_allowed": False,
        "credentials_exposed": False,
        "options_trade_generation_allowed": False,
        "futures_trade_generation_allowed": False,
        "live_execution": False,
        "capital_committed": 0,
        "research_only": True,
    }
