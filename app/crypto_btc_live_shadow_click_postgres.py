"""Insert-only persistence for explicit BTC Options live shadow clicks.

This ledger is separate from market-data PIT and from the underlying thesis tape.
It records exactly what happened when an explicit server-time shadow click was
requested: the frozen BTC thesis status plus, only when admissible, the exact
observed Options quote used for a hypothetical BUY entry. It never places an
order and never mutates a prior click.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

TABLE_NAME = "crypto_btc_live_shadow_clicks_v1"
PROVENANCE_ID = "BTC_LIVE_SHADOW_CLICK_POSTGRES_V1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    request_id TEXT PRIMARY KEY,
    decision_at TIMESTAMPTZ NOT NULL,
    outcome_due_at TIMESTAMPTZ NULL,
    market_direction TEXT NOT NULL CHECK (market_direction IN ('BULLISH','BEARISH','UNKNOWN')),
    shadow_status TEXT NOT NULL CHECK (shadow_status IN (
        'OPTIONS_SHADOW_ENTRY_FROZEN',
        'NO_TRADE_FROZEN',
        'PROOF_INPUT_UNRESOLVED'
    )),
    option_symbol TEXT NULL,
    entry_ask DOUBLE PRECISION NULL CHECK (entry_ask IS NULL OR entry_ask > 0),
    option_snapshot_first_seen_at TIMESTAMPTZ NULL,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (outcome_due_at IS NULL OR outcome_due_at > decision_at),
    CHECK (
        (shadow_status = 'OPTIONS_SHADOW_ENTRY_FROZEN' AND option_symbol IS NOT NULL AND entry_ask IS NOT NULL AND option_snapshot_first_seen_at IS NOT NULL)
        OR
        (shadow_status <> 'OPTIONS_SHADOW_ENTRY_FROZEN' AND option_symbol IS NULL AND entry_ask IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS crypto_btc_live_shadow_click_decision_idx
    ON {TABLE_NAME} (decision_at ASC);

CREATE OR REPLACE FUNCTION reject_crypto_btc_live_shadow_click_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'crypto_btc_live_shadow_clicks_v1 is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS crypto_btc_live_shadow_click_no_update_delete ON {TABLE_NAME};
CREATE TRIGGER crypto_btc_live_shadow_click_no_update_delete
BEFORE UPDATE OR DELETE ON {TABLE_NAME}
FOR EACH ROW EXECUTE FUNCTION reject_crypto_btc_live_shadow_click_mutation();

DROP TRIGGER IF EXISTS crypto_btc_live_shadow_click_no_truncate ON {TABLE_NAME};
CREATE TRIGGER crypto_btc_live_shadow_click_no_truncate
BEFORE TRUNCATE ON {TABLE_NAME}
FOR EACH STATEMENT EXECUTE FUNCTION reject_crypto_btc_live_shadow_click_mutation();
"""

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    request_id, decision_at, outcome_due_at, market_direction, shadow_status,
    option_symbol, entry_ask, option_snapshot_first_seen_at,
    payload, payload_hash, provenance_id
) VALUES (
    %(request_id)s, %(decision_at)s, %(outcome_due_at)s, %(market_direction)s,
    %(shadow_status)s, %(option_symbol)s, %(entry_ask)s,
    %(option_snapshot_first_seen_at)s, %(payload)s::jsonb, %(payload_hash)s,
    %(provenance_id)s
)
ON CONFLICT (request_id) DO NOTHING
RETURNING request_id;
"""

SELECT_SQL = f"""
SELECT request_id, decision_at, outcome_due_at, market_direction, shadow_status,
       option_symbol, entry_ask, option_snapshot_first_seen_at,
       payload, payload_hash, provenance_id
FROM {TABLE_NAME}
WHERE request_id = %s;
"""

MANIFEST_SQL = f"""
SELECT COUNT(*)::BIGINT,
       COUNT(*) FILTER (WHERE shadow_status = 'OPTIONS_SHADOW_ENTRY_FROZEN')::BIGINT,
       COUNT(*) FILTER (WHERE shadow_status = 'NO_TRADE_FROZEN')::BIGINT,
       COUNT(*) FILTER (WHERE shadow_status = 'PROOF_INPUT_UNRESOLVED')::BIGINT,
       MAX(decision_at)
FROM {TABLE_NAME};
"""

_COLUMNS = (
    "request_id", "decision_at", "outcome_due_at", "market_direction",
    "shadow_status", "option_symbol", "entry_ask",
    "option_snapshot_first_seen_at", "payload", "payload_hash", "provenance_id",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


def _decode(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise ValueError("stored live shadow click payload is not a JSON object")


def _row_dict(row) -> dict:
    values = dict(zip(_COLUMNS, row, strict=True))
    values["payload"] = _decode(values["payload"])
    for key in ("decision_at", "outcome_due_at", "option_snapshot_first_seen_at"):
        if isinstance(values.get(key), datetime):
            values[key] = _utc(values[key]).isoformat()
    return values


def _params(record: dict) -> dict:
    request_id = str(record.get("request_id") or "").strip()
    if not request_id:
        raise ValueError("live shadow click request_id is required")
    direction = str(record.get("market_direction") or "UNKNOWN").upper()
    if direction not in {"BULLISH", "BEARISH", "UNKNOWN"}:
        raise ValueError("unsupported live shadow market_direction")
    status = str(record.get("shadow_status") or "")
    if status not in {"OPTIONS_SHADOW_ENTRY_FROZEN", "NO_TRADE_FROZEN", "PROOF_INPUT_UNRESOLVED"}:
        raise ValueError("unsupported live shadow status")
    decision_at = _stamp(record.get("decision_at"))
    if decision_at is None:
        raise ValueError("decision_at is required")
    outcome_due_at = _stamp(record.get("outcome_due_at"))
    option = record.get("option_entry") if isinstance(record.get("option_entry"), dict) else None
    if status == "OPTIONS_SHADOW_ENTRY_FROZEN":
        if option is None:
            raise ValueError("Options shadow entry requires exact option_entry")
        symbol = str(option.get("symbol") or "").strip()
        entry_ask = float(option.get("entry_ask"))
        snapshot_seen = _stamp(option.get("snapshot_first_seen_at"))
        if not symbol or entry_ask <= 0 or snapshot_seen is None:
            raise ValueError("Options shadow entry requires symbol, positive ask, and snapshot first_seen_at")
        if snapshot_seen > decision_at:
            raise ValueError("Options snapshot cannot be first seen after decision_at")
    else:
        symbol = None
        entry_ask = None
        snapshot_seen = None
        if option is not None:
            raise ValueError("No-trade/unresolved shadow click may not contain an option entry")
    if record.get("live_execution") is not False or float(record.get("capital_committed", 0)) != 0:
        raise ValueError("live shadow click persistence rejects live execution/capital")
    if record.get("order_placed") is not False:
        raise ValueError("live shadow click persistence rejects order placement")
    payload = _canonical(record)
    return {
        "request_id": request_id,
        "decision_at": decision_at,
        "outcome_due_at": outcome_due_at,
        "market_direction": direction,
        "shadow_status": status,
        "option_symbol": symbol,
        "entry_ask": entry_ask,
        "option_snapshot_first_seen_at": snapshot_seen,
        "payload": payload,
        "payload_hash": sha256(payload.encode("utf-8")).hexdigest(),
        "provenance_id": PROVENANCE_ID,
    }


class PostgresBtcLiveShadowClickStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required for BTC live shadow clicks")

    async def initialize(self) -> dict:
        await asyncio.to_thread(self._initialize_sync)
        return {"status": "BTC_LIVE_SHADOW_CLICK_SCHEMA_READY", "table": TABLE_NAME}

    def _initialize_sync(self) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            conn.commit()

    async def get(self, request_id: str) -> dict | None:
        return await asyncio.to_thread(self._get_sync, request_id)

    def _get_sync(self, request_id: str) -> dict | None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_SQL, (str(request_id),))
                row = cur.fetchone()
        return None if row is None else _row_dict(row)

    async def insert(self, record: dict) -> dict:
        return await asyncio.to_thread(self._insert_sync, record)

    def _insert_sync(self, record: dict) -> dict:
        params = _params(record)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_SQL, params)
                inserted = cur.fetchone()
                if inserted is None:
                    cur.execute(SELECT_SQL, (params["request_id"],))
                    existing = _row_dict(cur.fetchone())
                else:
                    existing = None
            conn.commit()
        if inserted is not None:
            return {"status": "INSERTED_LIVE_SHADOW_CLICK", "request_id": params["request_id"]}
        if existing is not None and existing.get("payload_hash") == params["payload_hash"]:
            return {"status": "IDEMPOTENT_LIVE_SHADOW_CLICK", "request_id": params["request_id"]}
        raise ValueError("conflicting live shadow click cannot overwrite immutable request_id")

    async def manifest(self) -> dict:
        return await asyncio.to_thread(self._manifest_sync)

    def _manifest_sync(self) -> dict:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(MANIFEST_SQL)
                total, entries, no_trades, unresolved, latest = cur.fetchone()
        return {
            "version": "BTC_LIVE_SHADOW_CLICK_MANIFEST_V1",
            "click_count": int(total),
            "options_entry_count": int(entries),
            "no_trade_count": int(no_trades),
            "unresolved_count": int(unresolved),
            "latest_decision_at": None if latest is None else _utc(latest).isoformat(),
            "immutable": True,
            "live_execution": False,
        }


def architecture_contract() -> dict:
    return {
        "version": "BTC_LIVE_SHADOW_CLICK_POSTGRES_CONTRACT_V1",
        "insert_only": True,
        "database_update_allowed": False,
        "database_delete_allowed": False,
        "database_truncate_allowed": False,
        "exact_option_quote_required_for_entry": True,
        "future_option_snapshot_allowed": False,
        "live_execution": False,
        "research_only": True,
    }
