from __future__ import annotations

import asyncio
import json
from collections import Counter
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_episode_ledger_v1 import (
    EPISODE_TABLE,
    OUTCOME_TABLE,
    initialize_episode_ledger,
)
from .crude_oil_mini_research_protocol_v1 import (
    BASELINE_ID,
    PRIMARY_OUTCOME_HORIZON_MINUTES,
    PROTOCOL_ID,
)
from .trader_experience_memory import retrieve_similar


MODEL_ID = "CRUDE_OIL_MINI_PROSPECTIVE_EXPERIENCE_MEMORY_V1"
MIN_READY_CASES = 20
MAX_ANALOGUES = 5


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _stamp(value):
    if value in (None, ""):
        return None
    try:
        return parse_ist_timestamp(value)
    except Exception:
        return None


def _number(value):
    try:
        if value in (None, ""):
            return None
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError, OverflowError):
        return None


def _load_cases_sync(database_url: str, as_of) -> list[dict]:
    """Load only primary-horizon outcomes that were knowable before ``as_of``.

    Current/same-timestamp episodes are excluded. Outcome availability is the gate,
    not merely the episode timestamp, so a decision can never retrieve its own future.
    """
    cutoff = _stamp(as_of)
    if cutoff is None:
        raise ValueError("A point-in-time memory cutoff is required")
    sql = f"""
        SELECT e.episode_id, e.click_at, e.action, e.direction,
               e.evidence_quality, e.payload,
               o.available_at, o.resolution_status, o.underlying_return_pct,
               o.max_up_atr, o.max_down_atr, o.directional_favorable_points,
               o.directional_adverse_points, o.geometry_outcome, o.diagnosis,
               o.option_return_pct, o.payload
        FROM {EPISODE_TABLE} e
        JOIN {OUTCOME_TABLE} o ON o.episode_id = e.episode_id
        WHERE e.baseline_id = %s
          AND e.protocol_id = %s
          AND o.horizon_minutes = %s
          AND o.resolution_status = 'RESOLVED'
          AND e.click_at < %s
          AND o.available_at IS NOT NULL
          AND o.available_at < %s
        ORDER BY e.click_at ASC
    """
    params = (
        BASELINE_ID,
        PROTOCOL_ID,
        PRIMARY_OUTCOME_HORIZON_MINUTES,
        cutoff,
        cutoff,
    )
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

    cases = []
    for row in rows:
        (
            episode_id,
            click_at,
            action,
            direction,
            evidence_quality,
            episode_payload,
            available_at,
            resolution_status,
            underlying_return_pct,
            max_up_atr,
            max_down_atr,
            directional_favorable_points,
            directional_adverse_points,
            geometry_outcome,
            diagnosis,
            option_return_pct,
            outcome_payload,
        ) = row
        try:
            frozen = json.loads(episode_payload or "{}")
        except Exception:
            frozen = {}
        try:
            outcome_detail = json.loads(outcome_payload or "{}")
        except Exception:
            outcome_detail = {}
        decision = frozen.get("decision") or {}
        journal = decision.get("journal") or {}
        cases.append(
            {
                "episode_id": episode_id,
                "click_at": click_at.isoformat(),
                "available_at": available_at.isoformat(),
                "regime": journal.get("regime") or {},
                "evidence": journal.get("evidence") or {},
                "thesis": journal.get("thesis"),
                "action": action,
                "direction": direction,
                "evidence_quality": evidence_quality,
                "outcome": {
                    "resolution_status": resolution_status,
                    "underlying_return_pct": _number(underlying_return_pct),
                    "max_up_atr": _number(max_up_atr),
                    "max_down_atr": _number(max_down_atr),
                    "directional_favorable_points": _number(directional_favorable_points),
                    "directional_adverse_points": _number(directional_adverse_points),
                    "geometry_outcome": geometry_outcome,
                    "diagnosis": diagnosis,
                    "option_return_pct": _number(option_return_pct),
                    "detail": outcome_detail,
                },
            }
        )
    return cases


def _average(values):
    usable = [value for value in (_number(value) for value in values) if value is not None]
    return round(mean(usable), 6) if usable else None


def summarize_prospective_memory(cases: list[dict], current_result: dict) -> dict:
    """Describe prior comparable episodes without creating a new trading signal."""
    current_journal = (current_result or {}).get("journal") or {}
    current = {
        "regime": current_journal.get("regime") or {},
        "evidence": current_journal.get("evidence") or {},
    }
    prior = list(cases or [])
    analogues = retrieve_similar(prior, current, limit=MAX_ANALOGUES)
    outcomes = [case.get("outcome") or {} for case in analogues]
    status = "READY_FOR_DESCRIPTIVE_REVIEW" if len(prior) >= MIN_READY_CASES else "COLLECTING"

    return {
        "status": status,
        "model_id": MODEL_ID,
        "baseline_id": BASELINE_ID,
        "protocol_id": PROTOCOL_ID,
        "primary_horizon_minutes": PRIMARY_OUTCOME_HORIZON_MINUTES,
        "prior_resolved_cases": len(prior),
        "minimum_ready_cases": MIN_READY_CASES,
        "analogues_used": len(analogues),
        "max_analogues": MAX_ANALOGUES,
        "analogue_episode_ids": [case.get("episode_id") for case in analogues],
        "analogue_similarity_scores": [case.get("similarity_score") for case in analogues],
        "analogue_action_counts": dict(Counter(str(case.get("action") or "UNKNOWN") for case in analogues)),
        "geometry_counts": dict(Counter(str(outcome.get("geometry_outcome") or "UNKNOWN") for outcome in outcomes)),
        "diagnosis_counts": dict(Counter(str(outcome.get("diagnosis") or "UNKNOWN") for outcome in outcomes)),
        "average_underlying_return_pct": _average(outcome.get("underlying_return_pct") for outcome in outcomes),
        "average_max_up_atr": _average(outcome.get("max_up_atr") for outcome in outcomes),
        "average_max_down_atr": _average(outcome.get("max_down_atr") for outcome in outcomes),
        "average_option_return_pct": _average(outcome.get("option_return_pct") for outcome in outcomes),
        "retrieval_basis": "SHARED_REGIME_LABELS_AND_EVIDENCE_LANES_ONLY",
        "outcome_used_for_similarity_ranking": False,
        "strictly_prior_episode_required": True,
        "outcome_available_before_current_click_required": True,
        "same_timestamp_allowed": False,
        "historical_backfill_used": False,
        "current_mind_effect": "NONE",
        "integrated_v2_effect": "NONE",
        "option_expression_effect": "NONE",
        "decision_effect": "NONE",
        "promotion_eligible": False,
        "next_stage": "VALIDATE_MEMORY_INFORMATION_VALUE" if status == "READY_FOR_DESCRIPTIVE_REVIEW" else "ACCUMULATE_PROSPECTIVE_EPISODES",
    }


async def read_prospective_experience_memory(
    database_url: str,
    *,
    current_result: dict,
    as_of,
) -> dict:
    database_url = str(database_url or "").strip()
    if not database_url:
        return {
            "status": "UNAVAILABLE",
            "model_id": MODEL_ID,
            "reason": "DATABASE_NOT_CONFIGURED",
            "decision_effect": "NONE",
            "promotion_eligible": False,
        }
    await initialize_episode_ledger(database_url)
    cases = await asyncio.to_thread(_load_cases_sync, database_url, as_of)
    return summarize_prospective_memory(cases, current_result)
