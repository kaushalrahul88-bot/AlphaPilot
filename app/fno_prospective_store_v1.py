"""Insert-only Postgres store for prospective NSE F&O learning.

The decision tape, exact selected-contract observations, and later outcomes are
separate immutable tables. A decision is frozen before any forward outcome is
knowable. No UPDATE/DELETE/TRUNCATE path is exposed and Postgres triggers reject
those mutations as a second line of defense.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .fno_prospective_protocol_v1 import PRIMARY_HORIZON_MINUTES, PROTOCOL_ID

EPISODE_TABLE = "fno_prospective_episodes_v1"
OBSERVATION_TABLE = "fno_selected_contract_observations_v1"
OUTCOME_TABLE = "fno_prospective_outcomes_v1"
PROVENANCE_ID = "FNO_PROSPECTIVE_LEARNING_POSTGRES_V1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {EPISODE_TABLE} (
    episode_id TEXT PRIMARY KEY,
    protocol_id TEXT NOT NULL,
    capture_slot_at TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    decision_at TIMESTAMPTZ NOT NULL,
    outcome_due_at TIMESTAMPTZ NOT NULL,
    outcome_eligible BOOLEAN NOT NULL,
    underlying_symbol TEXT NOT NULL,
    expiry_date DATE,
    perception_fingerprint TEXT NOT NULL,
    research_action TEXT NOT NULL CHECK (research_action IN ('BUY_CE','BUY_PE','NO_TRADE')),
    selected_trading_symbol TEXT,
    selected_strike NUMERIC,
    selected_option_type TEXT CHECK (selected_option_type IS NULL OR selected_option_type IN ('CE','PE')),
    selected_reference_ltp NUMERIC,
    technical_status TEXT,
    technical_direction TEXT,
    execution_action TEXT NOT NULL CHECK (execution_action = 'NO_TRADE'),
    execution_eligible BOOLEAN NOT NULL CHECK (execution_eligible = FALSE),
    live_execution_enabled BOOLEAN NOT NULL CHECK (live_execution_enabled = FALSE),
    futures_trade_generated BOOLEAN NOT NULL CHECK (futures_trade_generated = FALSE),
    capital_committed NUMERIC NOT NULL CHECK (capital_committed = 0),
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (protocol_id, underlying_symbol, capture_slot_at)
);
CREATE INDEX IF NOT EXISTS fno_prospective_episodes_due_idx
    ON {EPISODE_TABLE} (outcome_due_at ASC);
CREATE INDEX IF NOT EXISTS fno_prospective_episodes_symbol_idx
    ON {EPISODE_TABLE} (underlying_symbol, decision_at DESC);

CREATE TABLE IF NOT EXISTS {OBSERVATION_TABLE} (
    observation_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES {EPISODE_TABLE}(episode_id),
    observed_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    underlying_symbol TEXT NOT NULL,
    expiry_date DATE NOT NULL,
    trading_symbol TEXT NOT NULL,
    strike NUMERIC NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('CE','PE')),
    ltp NUMERIC,
    best_bid NUMERIC,
    best_ask NUMERIC,
    volume NUMERIC,
    open_interest NUMERIC,
    iv NUMERIC,
    delta NUMERIC,
    gamma NUMERIC,
    theta NUMERIC,
    vega NUMERIC,
    underlying_ltp NUMERIC,
    source TEXT NOT NULL,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS fno_selected_contract_episode_time_idx
    ON {OBSERVATION_TABLE} (episode_id, observed_at ASC);
CREATE INDEX IF NOT EXISTS fno_selected_contract_symbol_time_idx
    ON {OBSERVATION_TABLE} (trading_symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS {OUTCOME_TABLE} (
    episode_id TEXT NOT NULL REFERENCES {EPISODE_TABLE}(episode_id),
    horizon_minutes INTEGER NOT NULL,
    outcome_due_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    resolution_status TEXT NOT NULL,
    classification TEXT NOT NULL,
    underlying_start_price NUMERIC,
    underlying_end_price NUMERIC,
    underlying_return_pct NUMERIC,
    max_up_pct NUMERIC,
    max_down_pct NUMERIC,
    option_observations INTEGER NOT NULL DEFAULT 0,
    option_end_ltp NUMERIC,
    option_return_pct NUMERIC,
    option_max_ltp NUMERIC,
    option_min_ltp NUMERIC,
    resolution_fingerprint TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    provenance_id TEXT NOT NULL DEFAULT '{PROVENANCE_ID}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (episode_id, horizon_minutes),
    CHECK (available_at >= outcome_due_at)
);
CREATE INDEX IF NOT EXISTS fno_prospective_outcomes_available_idx
    ON {OUTCOME_TABLE} (available_at ASC);
"""

IMMUTABILITY_FUNCTION = "fno_prospective_reject_mutation_v1"
IMMUTABILITY_SQL = f"""
CREATE OR REPLACE FUNCTION {IMMUTABILITY_FUNCTION}()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'prospective F&O learning tape is immutable';
END;
$$ LANGUAGE plpgsql;
"""
for _table, _prefix in (
    (EPISODE_TABLE, "fno_prospective_episode"),
    (OBSERVATION_TABLE, "fno_selected_contract"),
    (OUTCOME_TABLE, "fno_prospective_outcome"),
):
    IMMUTABILITY_SQL += f"""
DROP TRIGGER IF EXISTS {_prefix}_reject_row_mutation_v1 ON {_table};
CREATE TRIGGER {_prefix}_reject_row_mutation_v1
BEFORE UPDATE OR DELETE ON {_table}
FOR EACH ROW EXECUTE FUNCTION {IMMUTABILITY_FUNCTION}();

DROP TRIGGER IF EXISTS {_prefix}_reject_truncate_v1 ON {_table};
CREATE TRIGGER {_prefix}_reject_truncate_v1
BEFORE TRUNCATE ON {_table}
FOR EACH STATEMENT EXECUTE FUNCTION {IMMUTABILITY_FUNCTION}();
"""

INSERT_EPISODE_SQL = f"""
INSERT INTO {EPISODE_TABLE} (
    episode_id, protocol_id, capture_slot_at, captured_at, decision_at,
    outcome_due_at, outcome_eligible, underlying_symbol, expiry_date,
    perception_fingerprint, research_action, selected_trading_symbol,
    selected_strike, selected_option_type, selected_reference_ltp,
    technical_status, technical_direction, execution_action,
    execution_eligible, live_execution_enabled, futures_trade_generated,
    capital_committed, payload, payload_hash, provenance_id
) VALUES (
    %(episode_id)s, %(protocol_id)s, %(capture_slot_at)s, %(captured_at)s,
    %(decision_at)s, %(outcome_due_at)s, %(outcome_eligible)s,
    %(underlying_symbol)s, %(expiry_date)s, %(perception_fingerprint)s,
    %(research_action)s, %(selected_trading_symbol)s, %(selected_strike)s,
    %(selected_option_type)s, %(selected_reference_ltp)s, %(technical_status)s,
    %(technical_direction)s, %(execution_action)s, %(execution_eligible)s,
    %(live_execution_enabled)s, %(futures_trade_generated)s,
    %(capital_committed)s, %(payload)s::jsonb, %(payload_hash)s, %(provenance_id)s
)
ON CONFLICT (protocol_id, underlying_symbol, capture_slot_at) DO NOTHING
RETURNING episode_id;
"""

SELECT_EPISODE_BY_SLOT_SQL = f"""
SELECT episode_id, protocol_id, capture_slot_at, captured_at, decision_at,
       outcome_due_at, outcome_eligible, underlying_symbol, expiry_date,
       perception_fingerprint, research_action, selected_trading_symbol,
       selected_strike, selected_option_type, selected_reference_ltp,
       technical_status, technical_direction, execution_action,
       execution_eligible, live_execution_enabled, futures_trade_generated,
       capital_committed, payload, payload_hash, provenance_id
FROM {EPISODE_TABLE}
WHERE protocol_id=%s AND underlying_symbol=%s AND capture_slot_at=%s;
"""

INSERT_OBSERVATION_SQL = f"""
INSERT INTO {OBSERVATION_TABLE} (
    observation_id, episode_id, observed_at, collected_at, underlying_symbol,
    expiry_date, trading_symbol, strike, option_type, ltp, best_bid, best_ask,
    volume, open_interest, iv, delta, gamma, theta, vega, underlying_ltp,
    source, payload, payload_hash, provenance_id
) VALUES (
    %(observation_id)s, %(episode_id)s, %(observed_at)s, %(collected_at)s,
    %(underlying_symbol)s, %(expiry_date)s, %(trading_symbol)s, %(strike)s,
    %(option_type)s, %(ltp)s, %(best_bid)s, %(best_ask)s, %(volume)s,
    %(open_interest)s, %(iv)s, %(delta)s, %(gamma)s, %(theta)s, %(vega)s,
    %(underlying_ltp)s, %(source)s, %(payload)s::jsonb, %(payload_hash)s,
    %(provenance_id)s
)
ON CONFLICT (observation_id) DO NOTHING
RETURNING observation_id;
"""

INSERT_OUTCOME_SQL = f"""
INSERT INTO {OUTCOME_TABLE} (
    episode_id, horizon_minutes, outcome_due_at, resolved_at, available_at,
    resolution_status, classification, underlying_start_price,
    underlying_end_price, underlying_return_pct, max_up_pct, max_down_pct,
    option_observations, option_end_ltp, option_return_pct, option_max_ltp,
    option_min_ltp, resolution_fingerprint, payload, payload_hash, provenance_id
) VALUES (
    %(episode_id)s, %(horizon_minutes)s, %(outcome_due_at)s, %(resolved_at)s,
    %(available_at)s, %(resolution_status)s, %(classification)s,
    %(underlying_start_price)s, %(underlying_end_price)s,
    %(underlying_return_pct)s, %(max_up_pct)s, %(max_down_pct)s,
    %(option_observations)s, %(option_end_ltp)s, %(option_return_pct)s,
    %(option_max_ltp)s, %(option_min_ltp)s, %(resolution_fingerprint)s,
    %(payload)s::jsonb, %(payload_hash)s, %(provenance_id)s
)
ON CONFLICT (episode_id, horizon_minutes) DO NOTHING
RETURNING resolution_fingerprint;
"""

_EPISODE_COLUMNS = (
    "episode_id", "protocol_id", "capture_slot_at", "captured_at", "decision_at",
    "outcome_due_at", "outcome_eligible", "underlying_symbol", "expiry_date",
    "perception_fingerprint", "research_action", "selected_trading_symbol",
    "selected_strike", "selected_option_type", "selected_reference_ltp",
    "technical_status", "technical_direction", "execution_action",
    "execution_eligible", "live_execution_enabled", "futures_trade_generated",
    "capital_committed", "payload", "payload_hash", "provenance_id",
)


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("prospective F&O timestamps must be timezone-aware")
    return stamp.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _decode_payload(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise ValueError("stored F&O payload must be a JSON object")


def _episode_row(row) -> dict:
    values = dict(zip(_EPISODE_COLUMNS, row, strict=True))
    values["payload"] = _decode_payload(values["payload"])
    for key in ("capture_slot_at", "captured_at", "decision_at", "outcome_due_at"):
        if isinstance(values.get(key), datetime):
            values[key] = values[key].astimezone(timezone.utc)
    if values.get("expiry_date") is not None:
        values["expiry_date"] = str(values["expiry_date"])
    for key in ("selected_strike", "selected_reference_ltp", "capital_committed"):
        if values.get(key) is not None:
            values[key] = float(values[key])
    return values


def episode_params(record: dict) -> dict:
    if record.get("protocol_id") != PROTOCOL_ID:
        raise ValueError("unexpected F&O prospective protocol")
    decision = record.get("decision") or {}
    perception = record.get("perception") or {}
    if decision.get("execution_action") != "NO_TRADE":
        raise ValueError("prospective F&O episode persistence forbids execution")
    if decision.get("execution_eligible") is not False:
        raise ValueError("prospective F&O episode must be execution-ineligible")
    if float(decision.get("capital_committed") or 0) != 0:
        raise ValueError("prospective F&O episode persistence forbids capital")
    if decision.get("live_orders_created") is not False:
        raise ValueError("prospective F&O episode persistence forbids live orders")
    if record.get("future_outcome_present_in_decision") is not False:
        raise ValueError("future outcome may not enter a frozen F&O episode")
    if record.get("futures_trade_generated") is not False:
        raise ValueError("Futures trade generation must remain separate")
    action = str(decision.get("research_action") or "NO_TRADE").upper()
    if action not in {"BUY_CE", "BUY_PE", "NO_TRADE"}:
        raise ValueError("invalid prospective F&O research action")
    candidate = decision.get("research_candidate") or {}
    source = perception.get("source") or {}
    technical = perception.get("technical") or {}
    payload = _canonical(record)
    return {
        "episode_id": str(record["episode_id"]),
        "protocol_id": PROTOCOL_ID,
        "capture_slot_at": _utc(record["capture_slot_at"]),
        "captured_at": _utc(record["captured_at"]),
        "decision_at": _utc(record["decision_at"]),
        "outcome_due_at": _utc(record["outcome_due_at"]),
        "outcome_eligible": bool(record.get("outcome_eligible")),
        "underlying_symbol": str((perception.get("underlying") or {}).get("symbol") or "").upper(),
        "expiry_date": source.get("expiry_date") or None,
        "perception_fingerprint": str(perception["perception_fingerprint"]),
        "research_action": action,
        "selected_trading_symbol": candidate.get("trading_symbol") if action != "NO_TRADE" else None,
        "selected_strike": candidate.get("strike") if action != "NO_TRADE" else None,
        "selected_option_type": candidate.get("option_type") if action != "NO_TRADE" else None,
        "selected_reference_ltp": candidate.get("ltp") if action != "NO_TRADE" else None,
        "technical_status": technical.get("status"),
        "technical_direction": technical.get("direction"),
        "execution_action": "NO_TRADE",
        "execution_eligible": False,
        "live_execution_enabled": False,
        "futures_trade_generated": False,
        "capital_committed": 0,
        "payload": payload,
        "payload_hash": _hash(record),
        "provenance_id": PROVENANCE_ID,
    }


def observation_params(record: dict) -> dict:
    if record.get("protocol_id") != PROTOCOL_ID:
        raise ValueError("unexpected selected-contract observation protocol")
    payload = _canonical(record)
    required = ("episode_id", "observation_id", "observed_at", "collected_at",
                "underlying_symbol", "expiry_date", "trading_symbol", "strike", "option_type")
    for key in required:
        if record.get(key) in (None, ""):
            raise ValueError(f"selected-contract observation missing {key}")
    if record.get("option_type") not in {"CE", "PE"}:
        raise ValueError("invalid option_type")
    return {
        **{key: record.get(key) for key in (
            "observation_id", "episode_id", "underlying_symbol", "expiry_date",
            "trading_symbol", "strike", "option_type", "ltp", "best_bid", "best_ask",
            "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega",
            "underlying_ltp", "source"
        )},
        "observed_at": _utc(record["observed_at"]),
        "collected_at": _utc(record["collected_at"]),
        "payload": payload,
        "payload_hash": _hash(record),
        "provenance_id": PROVENANCE_ID,
    }


def outcome_params(record: dict) -> dict:
    if record.get("protocol_id") != PROTOCOL_ID:
        raise ValueError("unexpected F&O outcome protocol")
    if int(record.get("horizon_minutes") or 0) != PRIMARY_HORIZON_MINUTES:
        raise ValueError("unexpected F&O prospective outcome horizon")
    if record.get("decision_rewritten") is not False or record.get("outcome_used_for_decision") is not False:
        raise ValueError("F&O outcome may not rewrite the frozen decision")
    if record.get("live_execution") is not False or float(record.get("capital_committed") or 0) != 0:
        raise ValueError("F&O outcome persistence forbids live execution/capital")
    payload = _canonical(record)
    return {
        "episode_id": str(record["episode_id"]),
        "horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "outcome_due_at": _utc(record["outcome_due_at"]),
        "resolved_at": _utc(record["resolved_at"]),
        "available_at": _utc(record["available_at"]),
        "resolution_status": str(record["resolution_status"]),
        "classification": str(record["classification"]),
        "underlying_start_price": record.get("underlying_start_price"),
        "underlying_end_price": record.get("underlying_end_price"),
        "underlying_return_pct": record.get("underlying_return_pct"),
        "max_up_pct": record.get("max_up_pct"),
        "max_down_pct": record.get("max_down_pct"),
        "option_observations": int(record.get("option_observations") or 0),
        "option_end_ltp": record.get("option_end_ltp"),
        "option_return_pct": record.get("option_return_pct"),
        "option_max_ltp": record.get("option_max_ltp"),
        "option_min_ltp": record.get("option_min_ltp"),
        "resolution_fingerprint": str(record["resolution_fingerprint"]),
        "payload": payload,
        "payload_hash": _hash(record),
        "provenance_id": PROVENANCE_ID,
    }


class FnoProspectiveStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("database_url is required for F&O prospective learning")

    async def initialize(self) -> dict:
        await asyncio.to_thread(self._initialize_sync)
        return {
            "status": "FNO_PROSPECTIVE_SCHEMA_READY",
            "episode_table": EPISODE_TABLE,
            "observation_table": OBSERVATION_TABLE,
            "outcome_table": OUTCOME_TABLE,
            "database_immutable": True,
            "live_execution": False,
        }

    def _initialize_sync(self) -> None:
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
                cur.execute(IMMUTABILITY_SQL)
            conn.commit()

    async def insert_episode(self, record: dict) -> dict:
        return await asyncio.to_thread(self._insert_episode_sync, record)

    def _insert_episode_sync(self, record: dict) -> dict:
        params = episode_params(record)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_EPISODE_SQL, params)
                inserted = cur.fetchone()
                if inserted:
                    conn.commit()
                    return {"status": "INSERTED", "episode_id": inserted[0]}
                cur.execute(
                    SELECT_EPISODE_BY_SLOT_SQL,
                    (PROTOCOL_ID, params["underlying_symbol"], params["capture_slot_at"]),
                )
                existing = _episode_row(cur.fetchone())
            conn.commit()
        return {
            "status": "SLOT_ALREADY_FROZEN",
            "episode_id": existing["episode_id"],
            "existing_perception_fingerprint": existing["perception_fingerprint"],
        }

    async def insert_observation(self, record: dict) -> dict:
        return await asyncio.to_thread(self._insert_observation_sync, record)

    def _insert_observation_sync(self, record: dict) -> dict:
        params = observation_params(record)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_OBSERVATION_SQL, params)
                row = cur.fetchone()
            conn.commit()
        return {
            "status": "INSERTED" if row else "IDEMPOTENT",
            "observation_id": params["observation_id"],
        }

    async def insert_outcome(self, record: dict) -> dict:
        return await asyncio.to_thread(self._insert_outcome_sync, record)

    def _insert_outcome_sync(self, record: dict) -> dict:
        params = outcome_params(record)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_OUTCOME_SQL, params)
                row = cur.fetchone()
            conn.commit()
        return {
            "status": "INSERTED" if row else "IDEMPOTENT",
            "episode_id": params["episode_id"],
            "resolution_fingerprint": params["resolution_fingerprint"],
        }

    async def prior_cases(self, before: datetime, limit: int = 1000) -> list[dict]:
        return await asyncio.to_thread(self._prior_cases_sync, before, limit)

    def _prior_cases_sync(self, before: datetime, limit: int) -> list[dict]:
        sql = f"""
        SELECT e.payload, o.payload, o.available_at
        FROM {EPISODE_TABLE} e
        LEFT JOIN {OUTCOME_TABLE} o
          ON o.episode_id=e.episode_id
         AND o.horizon_minutes=%s
         AND o.resolution_status='RESOLVED'
         AND o.available_at < %s
        WHERE e.decision_at < %s
        ORDER BY e.decision_at DESC
        LIMIT %s
        """
        cutoff = _utc(before)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (PRIMARY_HORIZON_MINUTES, cutoff, cutoff, max(1, min(int(limit), 5000))))
                rows = cur.fetchall()
        cases = []
        for episode_payload, outcome_payload, available_at in rows:
            episode = _decode_payload(episode_payload)
            case = {
                "perception": episode.get("perception") or {},
                "research_action": (episode.get("decision") or {}).get("research_action"),
            }
            if outcome_payload is not None and available_at is not None:
                case["outcome"] = _decode_payload(outcome_payload)
                case["outcome_available_at"] = available_at.astimezone(timezone.utc).isoformat()
            cases.append(case)
        return cases

    async def active_actionable_contracts(self, as_of: datetime, limit: int = 24) -> list[dict]:
        return await asyncio.to_thread(self._active_actionable_contracts_sync, as_of, limit)

    def _active_actionable_contracts_sync(self, as_of: datetime, limit: int) -> list[dict]:
        sql = f"""
        SELECT episode_id, decision_at, outcome_due_at, underlying_symbol,
               expiry_date, selected_trading_symbol, selected_strike,
               selected_option_type, selected_reference_ltp
        FROM {EPISODE_TABLE}
        WHERE research_action IN ('BUY_CE','BUY_PE')
          AND selected_trading_symbol IS NOT NULL
          AND decision_at <= %s
          AND outcome_due_at >= %s
        ORDER BY decision_at ASC
        LIMIT %s
        """
        stamp = _utc(as_of)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (stamp, stamp, max(1, min(int(limit), 100))))
                rows = cur.fetchall()
        keys = (
            "episode_id", "decision_at", "outcome_due_at", "underlying_symbol",
            "expiry_date", "trading_symbol", "strike", "option_type", "reference_ltp",
        )
        result = []
        for row in rows:
            item = dict(zip(keys, row, strict=True))
            for key in ("decision_at", "outcome_due_at"):
                item[key] = item[key].astimezone(timezone.utc)
            item["expiry_date"] = str(item["expiry_date"])
            for key in ("strike", "reference_ltp"):
                if item.get(key) is not None:
                    item[key] = float(item[key])
            result.append(item)
        return result

    async def due_episodes(self, as_of: datetime, limit: int = 50) -> list[dict]:
        return await asyncio.to_thread(self._due_episodes_sync, as_of, limit)

    def _due_episodes_sync(self, as_of: datetime, limit: int) -> list[dict]:
        sql = f"""
        SELECT e.episode_id, e.decision_at, e.outcome_due_at, e.outcome_eligible,
               e.underlying_symbol, e.expiry_date, e.research_action,
               e.selected_trading_symbol, e.selected_strike, e.selected_option_type,
               e.selected_reference_ltp, e.payload
        FROM {EPISODE_TABLE} e
        LEFT JOIN {OUTCOME_TABLE} o
          ON o.episode_id=e.episode_id AND o.horizon_minutes=%s
        WHERE o.episode_id IS NULL
          AND e.outcome_due_at <= %s
        ORDER BY e.outcome_due_at ASC
        LIMIT %s
        """
        stamp = _utc(as_of)
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (PRIMARY_HORIZON_MINUTES, stamp, max(1, min(int(limit), 500))))
                rows = cur.fetchall()
        keys = (
            "episode_id", "decision_at", "outcome_due_at", "outcome_eligible",
            "underlying_symbol", "expiry_date", "research_action",
            "selected_trading_symbol", "selected_strike", "selected_option_type",
            "selected_reference_ltp", "payload",
        )
        result = []
        for row in rows:
            item = dict(zip(keys, row, strict=True))
            item["decision_at"] = item["decision_at"].astimezone(timezone.utc)
            item["outcome_due_at"] = item["outcome_due_at"].astimezone(timezone.utc)
            item["expiry_date"] = str(item["expiry_date"]) if item["expiry_date"] else None
            item["payload"] = _decode_payload(item["payload"])
            for key in ("selected_strike", "selected_reference_ltp"):
                if item.get(key) is not None:
                    item[key] = float(item[key])
            result.append(item)
        return result

    async def observations_for_episode(self, episode_id: str, until: datetime) -> list[dict]:
        return await asyncio.to_thread(self._observations_for_episode_sync, episode_id, until)

    def _observations_for_episode_sync(self, episode_id: str, until: datetime) -> list[dict]:
        sql = f"""
        SELECT observed_at, ltp, best_bid, best_ask, volume, open_interest, iv, delta,
               gamma, theta, vega, underlying_ltp, source
        FROM {OBSERVATION_TABLE}
        WHERE episode_id=%s AND observed_at <= %s
        ORDER BY observed_at ASC, collected_at ASC
        """
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (episode_id, _utc(until)))
                rows = cur.fetchall()
        keys = (
            "observed_at", "ltp", "best_bid", "best_ask", "volume", "open_interest",
            "iv", "delta", "gamma", "theta", "vega", "underlying_ltp", "source",
        )
        result = []
        for row in rows:
            item = dict(zip(keys, row, strict=True))
            item["observed_at"] = item["observed_at"].astimezone(timezone.utc)
            for key in keys[1:-1]:
                if item.get(key) is not None:
                    item[key] = float(item[key])
            result.append(item)
        return result

    async def status(self) -> dict:
        return await asyncio.to_thread(self._status_sync)

    def _status_sync(self) -> dict:
        sql = f"""
        SELECT
          (SELECT COUNT(*)::bigint FROM {EPISODE_TABLE}) AS episodes,
          (SELECT COUNT(*)::bigint FROM {EPISODE_TABLE} WHERE research_action='BUY_CE') AS buy_ce,
          (SELECT COUNT(*)::bigint FROM {EPISODE_TABLE} WHERE research_action='BUY_PE') AS buy_pe,
          (SELECT COUNT(*)::bigint FROM {EPISODE_TABLE} WHERE research_action='NO_TRADE') AS no_trade,
          (SELECT COUNT(*)::bigint FROM {OBSERVATION_TABLE}) AS selected_observations,
          (SELECT COUNT(*)::bigint FROM {OUTCOME_TABLE}) AS outcomes,
          (SELECT COUNT(*)::bigint FROM {OUTCOME_TABLE} WHERE resolution_status='RESOLVED') AS resolved_outcomes,
          (SELECT MAX(decision_at) FROM {EPISODE_TABLE}) AS last_decision_at,
          (SELECT MAX(observed_at) FROM {OBSERVATION_TABLE}) AS last_selected_observation_at,
          (SELECT MAX(available_at) FROM {OUTCOME_TABLE}) AS last_outcome_available_at
        """
        with _connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
        keys = (
            "episodes", "buy_ce", "buy_pe", "no_trade", "selected_observations",
            "outcomes", "resolved_outcomes", "last_decision_at",
            "last_selected_observation_at", "last_outcome_available_at",
        )
        result = dict(zip(keys, row, strict=True))
        for key in ("last_decision_at", "last_selected_observation_at", "last_outcome_available_at"):
            if isinstance(result.get(key), datetime):
                result[key] = result[key].astimezone(timezone.utc).isoformat()
        return {
            "status": "FNO_PROSPECTIVE_LEARNING",
            "protocol_id": PROTOCOL_ID,
            **result,
            "database_immutable": True,
            "live_execution": False,
            "capital_committed": 0,
        }


def architecture_contract() -> dict:
    return {
        "version": "FNO_PROSPECTIVE_POSTGRES_V1",
        "tables": [EPISODE_TABLE, OBSERVATION_TABLE, OUTCOME_TABLE],
        "insert_only": True,
        "database_update_allowed": False,
        "database_delete_allowed": False,
        "database_truncate_allowed": False,
        "decision_outcome_separation": True,
        "memory_reads_only_resolved_outcomes_available_before_current": True,
        "options_research_only": True,
        "futures_trade_generation": False,
        "live_execution": False,
        "capital_committed": 0,
    }
