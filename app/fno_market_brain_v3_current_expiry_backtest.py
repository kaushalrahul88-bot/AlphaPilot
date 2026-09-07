"""Evaluate the frozen V3 current-expiry dataset with the original replay metrics.

This module deliberately reuses the established underlying-only outcome framework:
15m/30m/60m/90m/EOD direction, MFE/MAE, NO_TRADE >=0.5%/>=1.0% misses,
and the existing model-level barrier diagnostics when entry/SL/T1/T2 are present.
It never recomputes V3 decisions and never reads options or futures.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Mapping

from . import fno_candle_only_four_stock_backtest_v1 as old_four_stock
from . import fno_market_brain_v3_current_expiry_dataset as source_dataset
from . import fno_underlying_random_replay_v1 as old_replay

PROTOCOL_ID = "FNO_MARKET_BRAIN_V3_CURRENT_EXPIRY_BACKTEST_V1_2026-09-07"
SOURCE_PROTOCOL_ID = source_dataset.PROTOCOL_ID
STOCKS = source_dataset.STOCKS
EXPECTED_HORIZONS = ("15m", "30m", "60m", "90m", "EOD")
NO_TRADE_MOVE_THRESHOLDS_PCT = old_replay.NO_TRADE_MOVE_THRESHOLDS_PCT


def _source_contract(dataset: Mapping[str, Any]) -> None:
    if dataset.get("status") != "COMPLETED":
        raise ValueError("SOURCE_DATASET_NOT_COMPLETED")
    if dataset.get("protocol_id") != SOURCE_PROTOCOL_ID:
        raise ValueError("SOURCE_PROTOCOL_MISMATCH")

    experiment = dataset.get("experiment") or {}
    symbols = tuple(item.get("symbol") for item in experiment.get("stocks") or [])
    if symbols != STOCKS:
        raise ValueError("SOURCE_STOCK_UNIVERSE_MISMATCH")
    if int(experiment.get("clicks_per_session") or 0) != source_dataset.CLICKS_PER_DAY:
        raise ValueError("SOURCE_CLICK_COUNT_MISMATCH")
    if int(experiment.get("observations") or 0) != len(dataset.get("rows") or []):
        raise ValueError("SOURCE_OBSERVATION_COUNT_MISMATCH")

    safety = dataset.get("safety") or {}
    required_true = (
        "completed_candles_only",
        "point_in_time_context_only",
        "clicks_deterministic_and_frozen",
        "future_tape_frozen",
    )
    if not all(safety.get(key) is True for key in required_true):
        raise ValueError("SOURCE_SAFETY_CONTRACT_MISMATCH")
    if safety.get("future_outcomes_resolved") is not False:
        raise ValueError("SOURCE_ALREADY_OUTCOME_RESOLVED")
    if safety.get("evaluation_metrics_computed") is not False:
        raise ValueError("SOURCE_ALREADY_EVALUATED")
    if safety.get("options_read_for_decision") is not False:
        raise ValueError("SOURCE_OPTIONS_DECISION_INPUT_PRESENT")
    if safety.get("futures_read_for_decision") is not False:
        raise ValueError("SOURCE_FUTURES_DECISION_INPUT_PRESENT")

    tapes = dataset.get("frozen_5m_tape_by_stock") or {}
    hashes = dataset.get("frozen_5m_tape_hashes") or {}
    for symbol in STOCKS:
        if symbol not in tapes or symbol not in hashes:
            raise ValueError(f"SOURCE_FROZEN_TAPE_MISSING:{symbol}")
        if source_dataset._tape_hash(list(tapes[symbol] or [])) != hashes[symbol]:
            raise ValueError(f"SOURCE_FROZEN_TAPE_HASH_MISMATCH:{symbol}")


def _model_decision(source_row: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve V3's frozen action while exposing already-snapshotted model levels."""
    decision = source_row.get("decision") or {}
    technical = source_row.get("technical") or {}
    return {
        "action": str(decision.get("action") or "NO_TRADE").upper(),
        "model_entry": technical.get("entry"),
        "model_stop_loss": technical.get("stop_loss"),
        "model_target1": technical.get("target1"),
        "model_target2": technical.get("target2"),
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = old_replay._summarize(rows)
    result["barrier_outcomes"] = old_four_stock._barrier_summary(rows)
    return result


def _source_decision_diagnostics(source_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    expansion = Counter()
    direction = Counter()
    reasons = Counter()
    for row in source_rows:
        decision = row.get("decision") or {}
        expansion["READY" if (decision.get("expansion") or {}).get("ready") else "NOT_READY"] += 1
        direction[str((decision.get("direction") or {}).get("side") or "UNKNOWN")] += 1
        for reason in decision.get("reasons") or []:
            reasons[str(reason)] += 1
    return {
        "expansion_state_counts": dict(sorted(expansion.items())),
        "direction_side_counts": dict(sorted(direction.items())),
        "reason_counts": dict(sorted(reasons.items())),
    }


def evaluate_frozen_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve outcomes for the frozen V3 clicks without changing any decision."""
    _source_contract(dataset)
    source_rows = list(dataset.get("rows") or [])
    tapes = dataset.get("frozen_5m_tape_by_stock") or {}
    evaluated: list[dict[str, Any]] = []

    for source_row in source_rows:
        symbol = str(source_row.get("symbol") or "").upper()
        if symbol not in STOCKS:
            raise ValueError(f"UNEXPECTED_SOURCE_SYMBOL:{symbol}")
        click = datetime.fromisoformat(str(source_row["click_at"]))
        model_decision = _model_decision(source_row)
        action = model_decision["action"]
        direction = action if action in {"LONG", "SHORT"} else None
        tape = list(tapes[symbol] or [])
        outcome = old_replay.resolve_underlying_path(tape, click, direction)
        barrier = old_four_stock._barrier(tape, click, model_decision)
        evaluated.append({
            "trade_date": source_row.get("trade_date"),
            "click_at": source_row.get("click_at"),
            "symbol": symbol,
            "category": source_row.get("category"),
            "decision": {
                "action": action,
                "protocol_id": (source_row.get("decision") or {}).get("protocol_id"),
            },
            "outcome": outcome,
            "barrier": barrier,
        })

    overall = _summary(evaluated)
    by_stock = {
        symbol: _summary([row for row in evaluated if row["symbol"] == symbol])
        for symbol in STOCKS
    }
    dates = sorted({str(row.get("trade_date")) for row in evaluated})
    by_day = {
        day: _summary([row for row in evaluated if str(row.get("trade_date")) == day])
        for day in dates
    }

    source_experiment = dataset.get("experiment") or {}
    return {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED",
        "source": {
            "protocol_id": SOURCE_PROTOCOL_ID,
            "brain_protocol_id": (dataset.get("safety") or {}).get("brain_protocol_id"),
            "stocks": list(STOCKS),
            "expiry_cycle_start": source_experiment.get("expiry_cycle_start"),
            "expiry_date": source_experiment.get("expiry_date"),
            "data_frozen_through": source_experiment.get("data_frozen_through"),
            "completed_sessions": source_experiment.get("completed_sessions") or [],
            "session_count": source_experiment.get("session_count"),
            "clicks_per_session": source_experiment.get("clicks_per_session"),
            "observations": source_experiment.get("observations"),
            "frozen_5m_tape_hashes": dict(dataset.get("frozen_5m_tape_hashes") or {}),
        },
        "criteria": {
            "methodology": "SAME_UNDERLYING_RANDOM_REPLAY_METRICS_AS_FIRST_FOUR_STOCKS",
            "horizons": list(EXPECTED_HORIZONS),
            "no_trade_large_move_thresholds_pct": list(NO_TRADE_MOVE_THRESHOLDS_PCT),
            "barrier_diagnostics": [
                "T1_BEFORE_SL",
                "SL_BEFORE_T1",
                "AMBIGUOUS_SL_T1_SAME_5M_BAR",
                "NEITHER",
                "T2_BEFORE_SL",
                "EOD_MTM_R",
            ],
            "new_pass_threshold_added": False,
            "atr_expansion_gate_added": False,
            "bootstrap_gate_added": False,
        },
        "summary": overall,
        "by_stock": by_stock,
        "by_day": by_day,
        "decision_diagnostics": _source_decision_diagnostics(source_rows),
        "rows": evaluated,
        "safety": architecture_contract(),
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "source_protocol_id": SOURCE_PROTOCOL_ID,
        "source_decisions_recomputed": False,
        "source_decisions_mutated": False,
        "source_tape_hashes_verified": True,
        "same_outcome_resolver_as_first_four_stock_replay": True,
        "outcome_horizons": list(EXPECTED_HORIZONS),
        "same_no_trade_large_move_thresholds": list(NO_TRADE_MOVE_THRESHOLDS_PCT),
        "options_read": False,
        "futures_read": False,
        "live_execution": False,
        "capital_committed": 0,
        "new_strategy_thresholds_added": False,
    }
