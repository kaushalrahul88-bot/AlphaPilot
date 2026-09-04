from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp


IST = ZoneInfo("Asia/Kolkata")
TABLE_NAME = "copper_direction_v2_shadow_evaluations"
MODEL_ID = "COPPER_DIRECTION_BRAIN_V2_SHADOW_V1"
PROVENANCE_ID = "COPPER_DIRECTION_V2_FIRST_SEEN_IMMUTABLE_EVALUATIONS_V1"
MAX_PROSPECTIVE_CAPTURE_LAG_SECONDS = 30.0
DIRECTIONS = {"BULLISH", "BEARISH", "UNKNOWN"}
FORBIDDEN_BOARD_KEYS = {
    "outcome",
    "future_return",
    "future_price",
    "target_hit",
    "stop_hit",
    "pnl",
    "r_multiple",
}

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    model_id TEXT NOT NULL CHECK (model_id = '{MODEL_ID}'),
    evaluation_id TEXT NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    board_as_of TIMESTAMPTZ NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('BULLISH', 'BEARISH', 'UNKNOWN')),
    direction_confidence TEXT NOT NULL,
    thesis_state TEXT NOT NULL,
    supporting_families JSONB NOT NULL,
    opposing_families JSONB NOT NULL,
    counted_families JSONB NOT NULL,
    families JSONB NOT NULL,
    modifiers JSONB NOT NULL,
    board_snapshot JSONB NOT NULL,
    evaluation_snapshot JSONB NOT NULL,
    record_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (model_id, board_as_of),
    UNIQUE (evaluation_id),
    UNIQUE (record_hash)
);
CREATE INDEX IF NOT EXISTS copper_direction_v2_shadow_evaluated_idx
    ON {TABLE_NAME} (evaluated_at DESC);
CREATE INDEX IF NOT EXISTS copper_direction_v2_shadow_direction_idx
    ON {TABLE_NAME} (direction, thesis_state, evaluated_at DESC);
"""

INSERT_FIRST_SEEN_SQL = f"""
INSERT INTO {TABLE_NAME} (
    model_id, evaluation_id, evaluated_at, board_as_of, direction,
    direction_confidence, thesis_state, supporting_families,
    opposing_families, counted_families, families, modifiers,
    board_snapshot, evaluation_snapshot, record_hash
) VALUES (
    %(model_id)s, %(evaluation_id)s, %(evaluated_at)s, %(board_as_of)s,
    %(direction)s, %(direction_confidence)s, %(thesis_state)s,
    %(supporting_families)s::jsonb, %(opposing_families)s::jsonb,
    %(counted_families)s::jsonb, %(families)s::jsonb, %(modifiers)s::jsonb,
    %(board_snapshot)s::jsonb, %(evaluation_snapshot)s::jsonb, %(record_hash)s
)
ON CONFLICT (model_id, board_as_of) DO NOTHING
RETURNING evaluation_id;
"""


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _stamp(value) -> datetime:
    if isinstance(value, datetime):
        stamp = value
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        return stamp.astimezone(IST)
    return parse_ist_timestamp(value).astimezone(IST)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _forbidden_path(value, path: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            current = (*path, str(key))
            if normalized in FORBIDDEN_BOARD_KEYS or normalized.startswith("future_"):
                return ".".join(current)
            found = _forbidden_path(nested, current)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found = _forbidden_path(nested, (*path, str(index)))
            if found:
                return found
    return None


def _immutable_evaluation_snapshot(evaluation: dict) -> dict:
    """Whitelist only fields produced at decision time.

    Extra keys supplied later (for example an outcome label) are intentionally
    ignored so they cannot change the prospective prediction record.
    """
    keys = (
        "mode",
        "product",
        "trade_instrument",
        "as_of",
        "research_only",
        "shadow_only",
        "direction",
        "direction_confidence",
        "thesis_state",
        "supporting_families",
        "opposing_families",
        "counted_families",
        "duplicate_causal_origins_suppressed",
        "families",
        "modifiers",
        "current_mind_action",
        "setup_geometry_generated",
        "option_expression_generated",
        "sealed_current_mind_effect",
        "decision_effect",
        "option_expression_effect",
        "production_rules_changed",
        "historical_backfill_used",
        "live_execution_enabled",
        "broker_order_placement_enabled",
        "capital_committed",
        "promotion_eligible",
        "rules",
        "integration_contract",
    )
    return {key: evaluation.get(key) for key in keys}


def build_prospective_record(
    board: dict,
    evaluation: dict,
    *,
    evaluated_at,
) -> dict:
    """Build one immutable, outcome-blind prospective Direction V2 record.

    Prospective storage deliberately refuses historical ``as_of`` values. Historical
    Direction V2 reads remain available through the read-only endpoint, but they are
    not allowed into this provenance table.
    """
    evaluated = _stamp(evaluated_at)
    board_as_of = _stamp(board.get("as_of"))
    lag_seconds = (evaluated - board_as_of).total_seconds()
    if lag_seconds < -1.0:
        raise ValueError("Copper Direction V2 board_as_of cannot be after evaluated_at")
    if lag_seconds > MAX_PROSPECTIVE_CAPTURE_LAG_SECONDS:
        raise ValueError("Historical as_of cannot be stored as a prospective Direction V2 evaluation")

    forbidden = _forbidden_path(board)
    if forbidden:
        raise ValueError(f"Outcome/future field is forbidden in prospective board snapshot: {forbidden}")

    if not evaluation.get("research_only") or not evaluation.get("shadow_only"):
        raise ValueError("Only research-only shadow Direction V2 evaluations may be persisted")
    if evaluation.get("decision_effect") != "NONE":
        raise ValueError("Prospective Direction V2 storage requires decision_effect=NONE")
    if evaluation.get("live_execution_enabled") or evaluation.get("broker_order_placement_enabled"):
        raise ValueError("Execution-enabled evaluations cannot enter the shadow provenance store")
    if evaluation.get("production_rules_changed"):
        raise ValueError("Production-changing evaluations cannot enter the shadow provenance store")

    direction = str(evaluation.get("direction") or "UNKNOWN").upper()
    if direction not in DIRECTIONS:
        raise ValueError(f"Unsupported Direction V2 direction: {direction}")

    immutable_evaluation = _immutable_evaluation_snapshot(evaluation)
    board_snapshot = json.loads(_canonical(board))
    evaluation_snapshot = json.loads(_canonical(immutable_evaluation))
    board_as_of_iso = board_as_of.isoformat()
    evaluation_id = hashlib.sha256(
        f"{MODEL_ID}|{board_as_of_iso}".encode("utf-8")
    ).hexdigest()
    record_payload = {
        "model_id": MODEL_ID,
        "evaluation_id": evaluation_id,
        "evaluated_at": evaluated.isoformat(),
        "board_as_of": board_as_of_iso,
        "direction": direction,
        "direction_confidence": str(evaluation.get("direction_confidence") or "UNKNOWN"),
        "thesis_state": str(evaluation.get("thesis_state") or "UNKNOWN"),
        "supporting_families": list(evaluation.get("supporting_families") or []),
        "opposing_families": list(evaluation.get("opposing_families") or []),
        "counted_families": list(evaluation.get("counted_families") or []),
        "families": evaluation.get("families") or {},
        "modifiers": evaluation.get("modifiers") or {},
        "board_snapshot": board_snapshot,
        "evaluation_snapshot": evaluation_snapshot,
    }
    record_hash = hashlib.sha256(_canonical(record_payload).encode("utf-8")).hexdigest()
    return {
        **record_payload,
        "record_hash": record_hash,
        "first_seen_immutable": True,
        "provenance_id": PROVENANCE_ID,
        "outcome_fields_stored": False,
    }


def _db_record(record: dict) -> dict:
    json_fields = {
        "supporting_families",
        "opposing_families",
        "counted_families",
        "families",
        "modifiers",
        "board_snapshot",
        "evaluation_snapshot",
    }
    return {
        key: (_canonical(value) if key in json_fields else value)
        for key, value in record.items()
        if key not in {"first_seen_immutable", "provenance_id", "outcome_fields_stored"}
    }


def initialize_store_sync(database_url: str) -> None:
    database_url = str(database_url or "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required for Copper Direction V2 prospective storage")
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)


async def initialize_store(database_url: str) -> None:
    await asyncio.to_thread(initialize_store_sync, database_url)


class CopperDirectionV2ProspectiveStore:
    """Append-only point-in-time provenance for shadow Direction V2 evaluations.

    The unique ``(model_id, board_as_of)`` key plus ``ON CONFLICT DO NOTHING`` makes
    the first evaluation for a point-in-time board authoritative. There is no update
    method and no outcome column, so later labels cannot rewrite the stored thesis.
    """

    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for Copper Direction V2 prospective storage")

    async def initialize(self) -> None:
        await initialize_store(self.database_url)

    def _insert_first_seen_sync(self, record: dict) -> bool:
        with _connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(INSERT_FIRST_SEEN_SQL, _db_record(record))
                return cursor.fetchone() is not None

    async def insert_first_seen(self, record: dict) -> bool:
        return await asyncio.to_thread(self._insert_first_seen_sync, record)

    def _coverage_sync(self) -> dict:
        with _connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*), MIN(board_as_of), MAX(board_as_of)
                    FROM {TABLE_NAME}
                    WHERE model_id = %s
                    """,
                    (MODEL_ID,),
                )
                total_row = cursor.fetchone() or (0, None, None)
                cursor.execute(
                    f"""
                    SELECT direction, thesis_state, COUNT(*)
                    FROM {TABLE_NAME}
                    WHERE model_id = %s
                    GROUP BY direction, thesis_state
                    ORDER BY direction, thesis_state
                    """,
                    (MODEL_ID,),
                )
                grouped = cursor.fetchall()
        total = int(total_row[0] or 0)
        by_direction: dict[str, int] = {}
        by_thesis_state: dict[str, int] = {}
        for direction, thesis_state, count in grouped:
            count = int(count or 0)
            by_direction[str(direction)] = by_direction.get(str(direction), 0) + count
            by_thesis_state[str(thesis_state)] = by_thesis_state.get(str(thesis_state), 0) + count
        directional = by_direction.get("BULLISH", 0) + by_direction.get("BEARISH", 0)
        return {
            "status": "ACTIVE",
            "model_id": MODEL_ID,
            "provenance_id": PROVENANCE_ID,
            "evaluations": total,
            "first_board_as_of": total_row[1].isoformat() if total_row[1] else None,
            "last_board_as_of": total_row[2].isoformat() if total_row[2] else None,
            "by_direction": by_direction,
            "by_thesis_state": by_thesis_state,
            "directional_evaluations": directional,
            "abstentions": by_direction.get("UNKNOWN", 0),
            "directional_coverage_pct": round((directional / total) * 100.0, 3) if total else 0.0,
            "outcome_data_read": False,
            "performance_claim": None,
            "first_seen_immutable": True,
            "production_rules_changed": False,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
        }

    async def coverage(self) -> dict:
        return await asyncio.to_thread(self._coverage_sync)
