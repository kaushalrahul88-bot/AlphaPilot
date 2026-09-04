from __future__ import annotations

import asyncio

from .crude_oil_mini_episode_ledger_v1 import (
    OUTCOME_TABLE,
    read_episode_ledger_summary,
)
from .crude_oil_mini_prospective_memory_v1 import MIN_READY_CASES
from .crude_oil_mini_research_protocol_v1 import (
    BASELINE_ID,
    PRIMARY_OUTCOME_HORIZON_MINUTES,
    baseline_manifest,
)


MODEL_ID = "CRUDE_OIL_MINI_RESEARCH_STATUS_V1"


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _validation_counts_sync(database_url: str) -> dict:
    sql = f"""
        SELECT
            COUNT(*) FILTER (
                WHERE horizon_minutes = %s AND resolution_status = 'RESOLVED'
            ) AS primary_resolved,
            COUNT(*) FILTER (
                WHERE horizon_minutes = %s AND resolution_status <> 'RESOLVED'
            ) AS primary_non_resolved,
            COUNT(*) FILTER (
                WHERE horizon_minutes = %s
                  AND diagnosis IN (
                      'MISSED_BULLISH_CLEAN_EXPANSION',
                      'MISSED_BEARISH_CLEAN_EXPANSION'
                  )
            ) AS primary_missed_clean_moves
        FROM {OUTCOME_TABLE}
    """
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    PRIMARY_OUTCOME_HORIZON_MINUTES,
                    PRIMARY_OUTCOME_HORIZON_MINUTES,
                    PRIMARY_OUTCOME_HORIZON_MINUTES,
                ),
            )
            row = cursor.fetchone()
    return {
        "primary_resolved_cases": int(row[0] or 0),
        "primary_non_resolved_cases": int(row[1] or 0),
        "primary_missed_clean_moves": int(row[2] or 0),
    }


def build_research_status(ledger: dict, validation_counts: dict) -> dict:
    resolved = int(validation_counts.get("primary_resolved_cases") or 0)
    ready = resolved >= MIN_READY_CASES
    progress = min(100.0, (resolved / MIN_READY_CASES * 100.0) if MIN_READY_CASES else 100.0)
    validate_stage = "READY_FOR_DESCRIPTIVE_VALIDATION" if ready else "ACCUMULATING_PROSPECTIVE_CASES"

    return {
        "status": "ACTIVE",
        "model_id": MODEL_ID,
        "baseline_id": BASELINE_ID,
        "research_protocol": baseline_manifest(),
        "episode_ledger": dict(ledger or {}),
        "validation": {
            **validation_counts,
            "primary_horizon_minutes": PRIMARY_OUTCOME_HORIZON_MINUTES,
            "minimum_ready_cases": MIN_READY_CASES,
            "progress_pct": round(progress, 2),
            "stage": validate_stage,
            "descriptive_validation_ready": ready,
            "improvement_unlocked": False,
            "holdout_test_unlocked": False,
            "prospective_test_unlocked": False,
            "promotion_eligible": False,
        },
        "pipeline": {
            "freeze_v1": "COMPLETE",
            "capture": "ACTIVE",
            "observe_outcome": "ACTIVE",
            "diagnose": "ACTIVE",
            "build_memory": "ACTIVE",
            "validate": "READY" if ready else "LOCKED_ACCUMULATING_DATA",
            "improve": "LOCKED",
            "holdout_test": "LOCKED",
            "prospective_test": "LOCKED",
            "promote": "LOCKED",
        },
        "historical_backfill_used": False,
        "decision_effect": "NONE",
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "promotion_eligible": False,
    }


async def read_crude_oil_mini_research_status(database_url: str) -> dict:
    database_url = str(database_url or "").strip()
    if not database_url:
        return {
            "status": "UNAVAILABLE",
            "model_id": MODEL_ID,
            "reason": "DATABASE_NOT_CONFIGURED",
            "decision_effect": "NONE",
            "promotion_eligible": False,
        }
    ledger = await read_episode_ledger_summary(database_url)
    counts = await asyncio.to_thread(_validation_counts_sync, database_url)
    return build_research_status(ledger, counts)
