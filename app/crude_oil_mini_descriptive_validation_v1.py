from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_episode_ledger_v1 import (
    EPISODE_TABLE,
    OUTCOME_TABLE,
    initialize_episode_ledger,
)
from .crude_oil_mini_prospective_memory_v1 import MIN_READY_CASES
from .crude_oil_mini_research_protocol_v1 import (
    BASELINE_ID,
    PRIMARY_OUTCOME_HORIZON_MINUTES,
    PROTOCOL_ID,
)


IST = ZoneInfo("Asia/Kolkata")
MODEL_ID = "CRUDE_OIL_MINI_DESCRIPTIVE_VALIDATION_V1"
REPORT_VERSION = 1


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _stamp(value):
    if value in (None, ""):
        return None
    try:
        return parse_ist_timestamp(value).astimezone(IST)
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


def _avg(values):
    usable = [value for value in (_number(value) for value in values) if value is not None]
    return round(mean(usable), 6) if usable else None


def _read_primary_rows_sync(database_url: str, as_of: datetime) -> list[dict]:
    """Read only fully resolved primary-horizon outcomes knowable by ``as_of``."""
    sql = f"""
        SELECT
            e.episode_id,
            e.click_at,
            e.action,
            e.direction,
            e.evidence_quality,
            e.integrated_v2_direction,
            e.integrated_v2_confidence,
            o.available_at,
            o.geometry_outcome,
            o.diagnosis,
            o.underlying_return_pct,
            o.max_up_atr,
            o.max_down_atr,
            o.option_observations,
            o.option_return_pct
        FROM {EPISODE_TABLE} e
        JOIN {OUTCOME_TABLE} o ON o.episode_id = e.episode_id
        WHERE e.baseline_id = %s
          AND e.protocol_id = %s
          AND o.horizon_minutes = %s
          AND o.resolution_status = 'RESOLVED'
          AND o.available_at IS NOT NULL
          AND o.available_at <= %s
        ORDER BY e.click_at ASC
    """
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                (BASELINE_ID, PROTOCOL_ID, PRIMARY_OUTCOME_HORIZON_MINUTES, as_of),
            )
            rows = cursor.fetchall()
    keys = (
        "episode_id",
        "click_at",
        "action",
        "direction",
        "evidence_quality",
        "integrated_v2_direction",
        "integrated_v2_confidence",
        "available_at",
        "geometry_outcome",
        "diagnosis",
        "underlying_return_pct",
        "max_up_atr",
        "max_down_atr",
        "option_observations",
        "option_return_pct",
    )
    return [dict(zip(keys, row)) for row in rows]


def _trade_directional_return(row: dict):
    action = str(row.get("action") or "").upper()
    underlying = _number(row.get("underlying_return_pct"))
    if underlying is None:
        return None
    if action == "BUY_CE":
        return underlying
    if action == "BUY_PE":
        return -underlying
    return None


def _trade_favorable_atr(row: dict):
    action = str(row.get("action") or "").upper()
    if action == "BUY_CE":
        return _number(row.get("max_up_atr"))
    if action == "BUY_PE":
        return _number(row.get("max_down_atr"))
    return None


def _trade_adverse_atr(row: dict):
    action = str(row.get("action") or "").upper()
    if action == "BUY_CE":
        return _number(row.get("max_down_atr"))
    if action == "BUY_PE":
        return _number(row.get("max_up_atr"))
    return None


def _v2_relation(row: dict) -> str:
    current = str(row.get("direction") or "UNKNOWN").upper()
    v2 = str(row.get("integrated_v2_direction") or "UNKNOWN").upper()
    if current not in {"BULLISH", "BEARISH"}:
        return "CURRENT_MIND_NO_DIRECTION"
    if v2 not in {"BULLISH", "BEARISH"}:
        return "V2_UNKNOWN"
    return "ALIGNED" if current == v2 else "OPPOSED"


def build_descriptive_validation(rows: list[dict], *, as_of) -> dict:
    """Produce the preregistered report only after the minimum sample is complete.

    Before the gate, no partial performance diagnostics are returned. This prevents
    repeated peeking at a tiny sample from becoming an implicit tuning loop.
    """
    observed = _stamp(as_of) or datetime.now(IST)
    resolved = list(rows or [])
    count = len(resolved)
    progress = min(100.0, (count / MIN_READY_CASES * 100.0) if MIN_READY_CASES else 100.0)
    base = {
        "status": "READY_FOR_DESCRIPTIVE_VALIDATION" if count >= MIN_READY_CASES else "LOCKED_ACCUMULATING_DATA",
        "model_id": MODEL_ID,
        "report_version": REPORT_VERSION,
        "baseline_id": BASELINE_ID,
        "protocol_id": PROTOCOL_ID,
        "as_of": observed.isoformat(),
        "primary_horizon_minutes": PRIMARY_OUTCOME_HORIZON_MINUTES,
        "resolved_primary_cases": count,
        "minimum_ready_cases": MIN_READY_CASES,
        "progress_pct": round(progress, 2),
        "partial_performance_metrics_exposed": False,
        "historical_backfill_used": False,
        "decision_effect": "NONE",
        "current_mind_effect": "NONE",
        "integrated_v2_effect": "NONE",
        "option_expression_effect": "NONE",
        "improvement_unlocked": False,
        "holdout_test_unlocked": False,
        "prospective_test_unlocked": False,
        "promotion_eligible": False,
        "statistical_edge_claim_allowed": False,
    }
    if count < MIN_READY_CASES:
        return {
            **base,
            "report": None,
            "reason": "PREREGISTERED_20_CASE_GATE_NOT_REACHED; PARTIAL_RESULTS_WITHHELD",
        }

    action_counts = Counter(str(row.get("action") or "UNKNOWN").upper() for row in resolved)
    trade_rows = [row for row in resolved if str(row.get("action") or "").upper() in {"BUY_CE", "BUY_PE"}]
    abstention_rows = [row for row in resolved if str(row.get("action") or "").upper() in {"WAIT", "NO_TRADE"}]
    geometry_counts = Counter(str(row.get("geometry_outcome") or "UNKNOWN") for row in trade_rows)
    diagnosis_counts = Counter(str(row.get("diagnosis") or "UNKNOWN") for row in abstention_rows)
    option_rows = [
        row for row in trade_rows
        if int(row.get("option_observations") or 0) > 0 and _number(row.get("option_return_pct")) is not None
    ]
    trading_days = len({
        stamp.date().isoformat()
        for stamp in (_stamp(row.get("click_at")) for row in resolved)
        if stamp is not None
    })

    report = {
        "sample": {
            "resolved_primary_cases": count,
            "trading_days": trading_days,
            "action_counts": dict(action_counts),
            "trade_episodes": len(trade_rows),
            "abstention_episodes": len(abstention_rows),
            "exact_option_response_trade_episodes": len(option_rows),
        },
        "trade_geometry": {
            "counts": dict(geometry_counts),
            "target_first": int(geometry_counts.get("TARGET_FIRST", 0)),
            "stop_first": int(geometry_counts.get("STOP_FIRST", 0)),
            "no_entry": int(geometry_counts.get("NO_ENTRY", 0)),
            "open_at_horizon": int(geometry_counts.get("OPEN_AT_HORIZON", 0)),
            "ambiguous": int(geometry_counts.get("ENTRY_AND_EXIT_SAME_BAR_AMBIGUOUS", 0))
            + int(geometry_counts.get("STOP_TARGET_SAME_BAR_AMBIGUOUS", 0)),
            "average_directional_underlying_return_pct": _avg(_trade_directional_return(row) for row in trade_rows),
            "average_favorable_excursion_atr": _avg(_trade_favorable_atr(row) for row in trade_rows),
            "average_adverse_excursion_atr": _avg(_trade_adverse_atr(row) for row in trade_rows),
        },
        "abstention_diagnostics": {
            "counts": dict(diagnosis_counts),
            "missed_bullish_clean_expansion": int(diagnosis_counts.get("MISSED_BULLISH_CLEAN_EXPANSION", 0)),
            "missed_bearish_clean_expansion": int(diagnosis_counts.get("MISSED_BEARISH_CLEAN_EXPANSION", 0)),
            "two_sided_expansion": int(diagnosis_counts.get("TWO_SIDED_EXPANSION_AFTER_ABSTENTION", 0)),
            "no_large_clean_move": int(diagnosis_counts.get("NO_LARGE_CLEAN_MOVE_AFTER_ABSTENTION", 0)),
        },
        "option_translation_observation": {
            "eligible_trade_episodes": len(option_rows),
            "average_exact_contract_option_return_pct": _avg(row.get("option_return_pct") for row in option_rows),
            "interpretation": "DESCRIPTIVE_ONLY_NOT_A_PREMIUM_RISK_MODEL",
        },
        "current_mind_vs_v2": {
            "relation_counts": dict(Counter(_v2_relation(row) for row in resolved)),
            "v2_decision_effect": "NONE",
        },
        "interpretation_rules": [
            "No p-value, confidence interval, win-rate claim, or edge claim is authorized at the 20-case descriptive gate.",
            "Ambiguous same-bar geometry is never counted as a win.",
            "WAIT and NO_TRADE are evaluated for missed clean moves, not treated as zero-return trades.",
            "Option return is reported only when an exact selected contract has immutable future observations.",
            "This report may identify recurring failure modes but cannot change the frozen V1 baseline.",
        ],
    }
    return {
        **base,
        "partial_performance_metrics_exposed": True,
        "report": report,
        "reason": "PREREGISTERED_DESCRIPTIVE_GATE_REACHED",
    }


async def read_descriptive_validation(database_url: str, *, as_of=None) -> dict:
    database_url = str(database_url or "").strip()
    observed = _stamp(as_of) or datetime.now(IST)
    if not database_url:
        return {
            "status": "UNAVAILABLE",
            "model_id": MODEL_ID,
            "reason": "DATABASE_NOT_CONFIGURED",
            "decision_effect": "NONE",
            "promotion_eligible": False,
        }
    await initialize_episode_ledger(database_url)
    rows = await asyncio.to_thread(_read_primary_rows_sync, database_url, observed)
    return build_descriptive_validation(rows, as_of=observed)
