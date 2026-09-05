from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .copper_commodity_brain_prospective_v1 import STREAM_ID
from .copper_commodity_brain_shadow_v1 import CONTRACT_VERSION

IST = ZoneInfo("Asia/Kolkata")
TABLE_NAME = "copper_commodity_brain_shared_prospective_evaluations"
MODEL_ID = STREAM_ID
PROVENANCE_ID = "COPPER_COMMODITY_BRAIN_SHARED_FIRST_SEEN_EVALUATIONS_V1"
MAX_CAPTURE_LAG_SECONDS = 30.0
DIRECTIONS = {"BULLISH", "BEARISH", "UNKNOWN"}
FORBIDDEN_KEYS = {
    "outcome", "future_return", "future_price", "target_hit", "stop_hit",
    "pnl", "r_multiple", "realized_pnl", "forward_return",
}

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    model_id TEXT NOT NULL CHECK (model_id = '{MODEL_ID}'),
    contract_version TEXT NOT NULL CHECK (contract_version = '{CONTRACT_VERSION}'),
    evaluation_id TEXT NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    board_as_of TIMESTAMPTZ NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('BULLISH','BEARISH','UNKNOWN')),
    direction_confidence TEXT NOT NULL,
    thesis_state TEXT NOT NULL,
    supporting_families JSONB NOT NULL,
    opposing_families JSONB NOT NULL,
    counted_families JSONB NOT NULL,
    counted_origins JSONB NOT NULL,
    families JSONB NOT NULL,
    modifiers JSONB NOT NULL,
    dependency_audit JSONB NOT NULL,
    input_provenance JSONB NOT NULL,
    board_snapshot JSONB NOT NULL,
    evaluation_snapshot JSONB NOT NULL,
    record_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (model_id, board_as_of),
    UNIQUE (evaluation_id),
    UNIQUE (record_hash)
);
CREATE INDEX IF NOT EXISTS copper_shared_brain_prospective_time_idx
    ON {TABLE_NAME} (evaluated_at DESC);
CREATE INDEX IF NOT EXISTS copper_shared_brain_prospective_direction_idx
    ON {TABLE_NAME} (direction, thesis_state, evaluated_at DESC);
"""

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    model_id, contract_version, evaluation_id, evaluated_at, board_as_of,
    direction, direction_confidence, thesis_state, supporting_families,
    opposing_families, counted_families, counted_origins, families, modifiers,
    dependency_audit, input_provenance, board_snapshot, evaluation_snapshot, record_hash
) VALUES (
    %(model_id)s, %(contract_version)s, %(evaluation_id)s, %(evaluated_at)s, %(board_as_of)s,
    %(direction)s, %(direction_confidence)s, %(thesis_state)s,
    %(supporting_families)s::jsonb, %(opposing_families)s::jsonb,
    %(counted_families)s::jsonb, %(counted_origins)s::jsonb,
    %(families)s::jsonb, %(modifiers)s::jsonb, %(dependency_audit)s::jsonb,
    %(input_provenance)s::jsonb, %(board_snapshot)s::jsonb,
    %(evaluation_snapshot)s::jsonb, %(record_hash)s
)
ON CONFLICT (model_id, board_as_of) DO NOTHING
RETURNING evaluation_id;
"""

COVERAGE_SQL = f"""
SELECT board_as_of, direction, direction_confidence, thesis_state,
       supporting_families, opposing_families, counted_families, input_provenance
FROM {TABLE_NAME}
WHERE model_id = %s
ORDER BY board_as_of ASC
"""

JSON_FIELDS = {
    "supporting_families", "opposing_families", "counted_families",
    "counted_origins", "families", "modifiers", "dependency_audit",
    "input_provenance", "board_snapshot", "evaluation_snapshot",
}


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


def _stamp(value) -> datetime:
    if isinstance(value, datetime):
        stamp = value if value.tzinfo is not None else value.replace(tzinfo=IST)
        return stamp.astimezone(IST)
    return parse_ist_timestamp(value).astimezone(IST)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _forbidden_path(value, path: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            current = (*path, str(key))
            if normalized in FORBIDDEN_KEYS or normalized.startswith("future_"):
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


def _evaluation_snapshot(evaluation: dict) -> dict:
    keys = (
        "mode", "contract_version", "shared_core_version", "prospective_stream_id",
        "evaluation_class", "prospective", "product", "trade_instrument", "as_of",
        "research_only", "shadow_only", "direction", "direction_confidence",
        "thesis_state", "supporting_families", "opposing_families", "counted_families",
        "counted_origins", "dependency_audit", "families", "modifiers",
        "experience_memory_role", "current_mind_action", "entry_readiness",
        "setup_geometry_generated", "option_expression_generated",
        "sealed_current_mind_effect", "decision_effect", "option_expression_effect",
        "production_rules_changed", "historical_records_rewritten",
        "historical_backfill_used", "outcome_blind_at_decision_time",
        "future_return_read", "pnl_read", "live_execution_enabled",
        "broker_order_placement_enabled", "capital_committed", "promotion_eligible",
        "prospective_memory_eligible", "input_provenance", "integration_contract",
    )
    return {key: evaluation.get(key) for key in keys}


def build_prospective_record(board: dict, evaluation: dict, *, evaluated_at) -> dict:
    evaluated = _stamp(evaluated_at)
    board_as_of = _stamp(board.get("as_of"))
    lag_seconds = (evaluated - board_as_of).total_seconds()
    if lag_seconds < -1.0 or lag_seconds > MAX_CAPTURE_LAG_SECONDS:
        raise ValueError("Only exactly-now Copper shared observations may enter prospective storage")

    forbidden = _forbidden_path(board)
    if forbidden:
        raise ValueError(f"Future/outcome field is forbidden in prospective board: {forbidden}")
    forbidden_eval = _forbidden_path(evaluation)
    if forbidden_eval:
        raise ValueError(f"Future/outcome field is forbidden in prospective evaluation: {forbidden_eval}")

    if evaluation.get("prospective_stream_id") != STREAM_ID:
        raise ValueError("Unexpected Copper shared prospective stream id")
    if evaluation.get("evaluation_class") != "PROSPECTIVE_SHADOW" or evaluation.get("prospective") is not True:
        raise ValueError("Only prospective shared shadow evaluations may be persisted")
    if not evaluation.get("research_only") or not evaluation.get("shadow_only"):
        raise ValueError("Copper shared prospective storage is research-shadow only")
    if evaluation.get("decision_effect") != "NONE" or evaluation.get("sealed_current_mind_effect") != "NONE":
        raise ValueError("Copper shared prospective storage requires zero decision/Current Mind effect")
    if evaluation.get("live_execution_enabled") or evaluation.get("broker_order_placement_enabled"):
        raise ValueError("Execution-enabled evaluation cannot enter Copper shared prospective storage")
    if int(evaluation.get("capital_committed") or 0) != 0:
        raise ValueError("Copper shared prospective storage requires zero capital")
    if evaluation.get("historical_backfill_used") or evaluation.get("historical_records_rewritten"):
        raise ValueError("Historical reconstruction/rewrite cannot enter Copper shared prospective storage")
    if evaluation.get("outcome_blind_at_decision_time") is not True or evaluation.get("future_return_read") or evaluation.get("pnl_read"):
        raise ValueError("Copper shared prospective observation must be outcome blind")

    provenance = evaluation.get("input_provenance") or {}
    if provenance.get("status") != "VALID" or provenance.get("historical_backfill_used"):
        raise ValueError("Copper shared prospective input provenance is not valid")

    direction = str(evaluation.get("direction") or "UNKNOWN").upper()
    if direction not in DIRECTIONS:
        raise ValueError(f"Unsupported direction: {direction}")

    board_snapshot = json.loads(_canonical(board))
    evaluation_snapshot = json.loads(_canonical(_evaluation_snapshot(evaluation)))
    board_as_of_iso = board_as_of.isoformat()
    evaluation_id = hashlib.sha256(f"{MODEL_ID}|{board_as_of_iso}".encode()).hexdigest()
    payload = {
        "model_id": MODEL_ID,
        "contract_version": CONTRACT_VERSION,
        "evaluation_id": evaluation_id,
        "evaluated_at": evaluated.isoformat(),
        "board_as_of": board_as_of_iso,
        "direction": direction,
        "direction_confidence": str(evaluation.get("direction_confidence") or "UNKNOWN"),
        "thesis_state": str(evaluation.get("thesis_state") or "UNKNOWN"),
        "supporting_families": list(evaluation.get("supporting_families") or []),
        "opposing_families": list(evaluation.get("opposing_families") or []),
        "counted_families": list(evaluation.get("counted_families") or []),
        "counted_origins": list(evaluation.get("counted_origins") or []),
        "families": evaluation.get("families") or {},
        "modifiers": evaluation.get("modifiers") or {},
        "dependency_audit": evaluation.get("dependency_audit") or {},
        "input_provenance": provenance,
        "board_snapshot": board_snapshot,
        "evaluation_snapshot": evaluation_snapshot,
    }
    record_hash = hashlib.sha256(_canonical(payload).encode()).hexdigest()
    return {
        **payload,
        "record_hash": record_hash,
        "first_seen_immutable": True,
        "provenance_id": PROVENANCE_ID,
        "outcome_fields_stored": False,
        "historical_as_of_allowed": False,
        "prospective_memory_eligible": False,
    }


def _db_record(record: dict) -> dict:
    return {
        key: (_canonical(value) if key in JSON_FIELDS else value)
        for key, value in record.items()
        if key not in {
            "first_seen_immutable", "provenance_id", "outcome_fields_stored",
            "historical_as_of_allowed", "prospective_memory_eligible",
        }
    }


def _json(value, default):
    if isinstance(value, type(default)):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, type(default)) else default
        except Exception:
            return default
    return default


def initialize_store_sync(database_url: str) -> None:
    database_url = str(database_url or "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required for Copper shared prospective storage")
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)


async def initialize_store(database_url: str) -> None:
    await asyncio.to_thread(initialize_store_sync, database_url)


class CopperCommodityBrainProspectiveStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for Copper shared prospective storage")

    async def initialize(self) -> None:
        await initialize_store(self.database_url)

    def _insert_sync(self, record: dict) -> bool:
        with _connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(INSERT_SQL, _db_record(record))
                return cursor.fetchone() is not None

    async def insert_first_seen(self, record: dict) -> bool:
        return await asyncio.to_thread(self._insert_sync, record)

    def _coverage_sync(self) -> dict:
        with _connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(COVERAGE_SQL, (MODEL_ID,))
                rows = cursor.fetchall()
        by_direction: dict[str, int] = {}
        by_confidence: dict[str, int] = {}
        by_thesis: dict[str, int] = {}
        directional = 0
        for row in rows:
            direction = str(row[1] or "UNKNOWN").upper()
            confidence = str(row[2] or "UNKNOWN")
            thesis = str(row[3] or "UNKNOWN")
            by_direction[direction] = by_direction.get(direction, 0) + 1
            by_confidence[confidence] = by_confidence.get(confidence, 0) + 1
            by_thesis[thesis] = by_thesis.get(thesis, 0) + 1
            if direction in {"BULLISH", "BEARISH"}:
                directional += 1
        total = len(rows)
        return {
            "status": "ACTIVE",
            "model_id": MODEL_ID,
            "contract_version": CONTRACT_VERSION,
            "provenance_id": PROVENANCE_ID,
            "evaluations": total,
            "directional_evaluations": directional,
            "abstentions": by_direction.get("UNKNOWN", 0),
            "by_direction": by_direction,
            "by_confidence": by_confidence,
            "by_thesis_state": by_thesis,
            "first_board_as_of": rows[0][0].isoformat() if rows else None,
            "last_board_as_of": rows[-1][0].isoformat() if rows else None,
            "outcome_data_read": False,
            "performance_claim": None,
            "diagnostic_only": True,
            "first_seen_immutable": True,
            "historical_backfill_used": False,
            "historical_as_of_allowed": False,
            "sealed_current_mind_effect": "NONE",
            "direction_v2_history_effect": "NONE",
            "production_rules_changed": False,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "capital_committed": 0,
            "promotion_eligible": False,
        }

    async def coverage(self) -> dict:
        return await asyncio.to_thread(self._coverage_sync)
