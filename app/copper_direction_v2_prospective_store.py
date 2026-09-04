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
DEFAULT_CONTRACT_VERSION = "COPPER_DIRECTION_BRAIN_V2_SHADOW_V1"
OPTION_PARTICIPATION_RULE_VERSION = "COPPER_OPTION_PARTICIPATION_V1"
PROVENANCE_ID = "COPPER_DIRECTION_V2_FIRST_SEEN_IMMUTABLE_EVALUATIONS_V1"
MAX_PROSPECTIVE_CAPTURE_LAG_SECONDS = 30.0
DIRECTIONS = {"BULLISH", "BEARISH", "UNKNOWN"}
DIRECTIONAL = {"BULLISH", "BEARISH"}
READY_OPTION_PARTICIPATION_STATES = {
    "CROSS_SIDE_NEW_OI_BULLISH",
    "CROSS_SIDE_NEW_OI_BEARISH",
    "OPPOSING_NEW_OI_OPTION_EVIDENCE",
    "INSUFFICIENT_CROSS_SIDE_NEW_OI_CONFIRMATION",
}
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
    contract_version TEXT NOT NULL DEFAULT '{DEFAULT_CONTRACT_VERSION}',
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
ALTER TABLE {TABLE_NAME}
    ADD COLUMN IF NOT EXISTS contract_version TEXT NOT NULL
    DEFAULT '{DEFAULT_CONTRACT_VERSION}';
CREATE INDEX IF NOT EXISTS copper_direction_v2_shadow_evaluated_idx
    ON {TABLE_NAME} (evaluated_at DESC);
CREATE INDEX IF NOT EXISTS copper_direction_v2_shadow_direction_idx
    ON {TABLE_NAME} (direction, thesis_state, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS copper_direction_v2_shadow_contract_idx
    ON {TABLE_NAME} (contract_version, evaluated_at DESC);
"""

INSERT_FIRST_SEEN_SQL = f"""
INSERT INTO {TABLE_NAME} (
    model_id, contract_version, evaluation_id, evaluated_at, board_as_of, direction,
    direction_confidence, thesis_state, supporting_families,
    opposing_families, counted_families, families, modifiers,
    board_snapshot, evaluation_snapshot, record_hash
) VALUES (
    %(model_id)s, %(contract_version)s, %(evaluation_id)s, %(evaluated_at)s,
    %(board_as_of)s, %(direction)s, %(direction_confidence)s, %(thesis_state)s,
    %(supporting_families)s::jsonb, %(opposing_families)s::jsonb,
    %(counted_families)s::jsonb, %(families)s::jsonb, %(modifiers)s::jsonb,
    %(board_snapshot)s::jsonb, %(evaluation_snapshot)s::jsonb, %(record_hash)s
)
ON CONFLICT (model_id, board_as_of) DO NOTHING
RETURNING evaluation_id;
"""

# Deliberately restricted to immutable prediction-time fields. Coverage diagnostics
# must never join to, select, or infer trade outcomes or future returns.
COVERAGE_ROWS_SQL = f"""
SELECT
    contract_version,
    board_as_of,
    direction,
    thesis_state,
    supporting_families,
    opposing_families,
    counted_families,
    families
FROM {TABLE_NAME}
WHERE model_id = %s
ORDER BY board_as_of ASC
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
    """Whitelist only fields produced at decision time."""
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


def _contract_version(evaluation: dict) -> str:
    contract = evaluation.get("integration_contract") or {}
    version = str(contract.get("version") or DEFAULT_CONTRACT_VERSION).strip()
    return version or DEFAULT_CONTRACT_VERSION


def build_prospective_record(
    board: dict,
    evaluation: dict,
    *,
    evaluated_at,
) -> dict:
    """Build one immutable, outcome-blind prospective Direction V2 record."""
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
    contract_version = _contract_version(evaluation)
    evaluation_id = hashlib.sha256(
        f"{MODEL_ID}|{board_as_of_iso}".encode("utf-8")
    ).hexdigest()
    record_payload = {
        "model_id": MODEL_ID,
        "contract_version": contract_version,
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


def _json_value(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, type(default)) else default
        except (TypeError, ValueError, json.JSONDecodeError):
            return default
    return default


def _pct(part: int, total: int) -> float:
    return round((int(part) / int(total)) * 100.0, 3) if total else 0.0


def _increment(mapping: dict[str, int], key) -> None:
    normalized = str(key or "UNKNOWN")
    mapping[normalized] = mapping.get(normalized, 0) + 1


def _combo(values) -> str:
    normalized = sorted({str(value) for value in (values or []) if str(value).strip()})
    return "+".join(normalized) if normalized else "NONE"


def _new_contract_diagnostics() -> dict:
    return {
        "evaluations": 0,
        "directional_evaluations": 0,
        "abstentions": 0,
        "by_direction": {},
        "by_thesis_state": {},
        "family_counted_vote_frequency": {},
        "family_stance_distribution": {},
        "family_state_distribution": {},
        "supporting_family_combinations": {},
        "opposing_family_combinations": {},
        "evaluations_with_at_least_two_counted_families": 0,
        "option_participation": {
            "rule_version": OPTION_PARTICIPATION_RULE_VERSION,
            "rule_version_observations": 0,
            "ready_evaluations": 0,
            "vote_evaluations": 0,
            "by_stance": {},
            "by_state": {},
        },
    }


def summarize_prospective_coverage_rows(rows: list[dict]) -> dict:
    """Describe immutable prospective predictions without reading any outcome data."""
    contracts: dict[str, dict] = {}
    first_board_as_of = None
    last_board_as_of = None

    for row in rows:
        contract_version = str(row.get("contract_version") or DEFAULT_CONTRACT_VERSION)
        diagnostic = contracts.setdefault(contract_version, _new_contract_diagnostics())
        diagnostic["evaluations"] += 1

        board_as_of = row.get("board_as_of")
        if board_as_of is not None:
            first_board_as_of = first_board_as_of or board_as_of
            last_board_as_of = board_as_of

        direction = str(row.get("direction") or "UNKNOWN").upper()
        if direction not in DIRECTIONS:
            direction = "UNKNOWN"
        thesis_state = str(row.get("thesis_state") or "UNKNOWN")
        _increment(diagnostic["by_direction"], direction)
        _increment(diagnostic["by_thesis_state"], thesis_state)
        if direction in DIRECTIONAL:
            diagnostic["directional_evaluations"] += 1
        else:
            diagnostic["abstentions"] += 1

        counted = _json_value(row.get("counted_families"), [])
        counted_unique = {str(value) for value in counted if str(value).strip()}
        if len(counted_unique) >= 2:
            diagnostic["evaluations_with_at_least_two_counted_families"] += 1

        supporting = _json_value(row.get("supporting_families"), [])
        opposing = _json_value(row.get("opposing_families"), [])
        if direction in DIRECTIONAL:
            _increment(diagnostic["supporting_family_combinations"], _combo(supporting))
        if opposing:
            _increment(diagnostic["opposing_family_combinations"], _combo(opposing))

        families = _json_value(row.get("families"), {})
        for family_name, family_payload in families.items():
            family = family_payload if isinstance(family_payload, dict) else {}
            name = str(family_name)
            stance = str(family.get("stance") or "UNKNOWN").upper()
            if stance not in DIRECTIONS:
                stance = "UNKNOWN"
            state = str(family.get("state") or "UNKNOWN")

            stance_distribution = diagnostic["family_stance_distribution"].setdefault(name, {})
            state_distribution = diagnostic["family_state_distribution"].setdefault(name, {})
            _increment(stance_distribution, stance)
            _increment(state_distribution, state)
            if family.get("counts_for_direction") and stance in DIRECTIONAL:
                _increment(diagnostic["family_counted_vote_frequency"], name)

            if name == "OPTION_PARTICIPATION":
                option = diagnostic["option_participation"]
                detail = family.get("detail") if isinstance(family.get("detail"), dict) else {}
                rule_version = str(detail.get("rule_version") or "")
                if rule_version == OPTION_PARTICIPATION_RULE_VERSION:
                    option["rule_version_observations"] += 1
                if state in READY_OPTION_PARTICIPATION_STATES:
                    option["ready_evaluations"] += 1
                if family.get("counts_for_direction") and stance in DIRECTIONAL:
                    option["vote_evaluations"] += 1
                _increment(option["by_stance"], stance)
                _increment(option["by_state"], state)

    total = sum(int(value["evaluations"]) for value in contracts.values())
    overall_direction: dict[str, int] = {}
    overall_thesis: dict[str, int] = {}
    for diagnostic in contracts.values():
        evaluations = int(diagnostic["evaluations"])
        diagnostic["directional_coverage_pct"] = _pct(
            diagnostic["directional_evaluations"], evaluations
        )
        diagnostic["at_least_two_counted_families_pct"] = _pct(
            diagnostic["evaluations_with_at_least_two_counted_families"], evaluations
        )
        option = diagnostic["option_participation"]
        option["readiness_pct"] = _pct(option["ready_evaluations"], evaluations)
        option["vote_rate_pct"] = _pct(option["vote_evaluations"], evaluations)
        for key, count in diagnostic["by_direction"].items():
            overall_direction[key] = overall_direction.get(key, 0) + int(count)
        for key, count in diagnostic["by_thesis_state"].items():
            overall_thesis[key] = overall_thesis.get(key, 0) + int(count)

    directional = overall_direction.get("BULLISH", 0) + overall_direction.get("BEARISH", 0)
    return {
        "evaluations": total,
        "first_board_as_of": (
            first_board_as_of.isoformat() if hasattr(first_board_as_of, "isoformat") else first_board_as_of
        ),
        "last_board_as_of": (
            last_board_as_of.isoformat() if hasattr(last_board_as_of, "isoformat") else last_board_as_of
        ),
        "by_contract_version": {
            version: int(diagnostic["evaluations"])
            for version, diagnostic in contracts.items()
        },
        "by_direction": overall_direction,
        "by_thesis_state": overall_thesis,
        "directional_evaluations": directional,
        "abstentions": overall_direction.get("UNKNOWN", 0),
        "directional_coverage_pct": _pct(directional, total),
        "contract_diagnostics": contracts,
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
    """Append-only point-in-time provenance for shadow Direction V2 evaluations."""

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
                cursor.execute(COVERAGE_ROWS_SQL, (MODEL_ID,))
                rows = [
                    {
                        "contract_version": row[0],
                        "board_as_of": row[1],
                        "direction": row[2],
                        "thesis_state": row[3],
                        "supporting_families": row[4],
                        "opposing_families": row[5],
                        "counted_families": row[6],
                        "families": row[7],
                    }
                    for row in cursor.fetchall()
                ]
        coverage = summarize_prospective_coverage_rows(rows)
        return {
            "status": "ACTIVE",
            "model_id": MODEL_ID,
            "provenance_id": PROVENANCE_ID,
            **coverage,
            "outcome_data_read": False,
            "performance_claim": None,
            "diagnostic_only": True,
            "thresholds_changed": False,
            "model_rules_changed": False,
            "first_seen_immutable": True,
            "production_rules_changed": False,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
        }

    async def coverage(self) -> dict:
        return await asyncio.to_thread(self._coverage_sync)
