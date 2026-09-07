"""Compare the frozen V1 technical decision logic on the exact V3 current-expiry tape.

The comparator deliberately reuses the already-frozen current-expiry source rows,
click timestamps, technical snapshots, and 5-minute outcome tapes.  It derives a
V1 action only from each row's frozen technical snapshot via the original
``_decision_snapshot`` function.  V3 context/news and V3 decisions are ignored
for the V1 decision itself and are used only for descriptive action-overlap
statistics.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Mapping

from . import fno_candle_only_four_stock_backtest_v1 as old_four_stock
from . import fno_market_brain_v3_current_expiry_backtest as v3_backtest
from . import fno_market_brain_v3_current_expiry_dataset as source_dataset
from . import fno_underlying_random_replay_v1 as old_replay

PROTOCOL_ID = "FNO_V1_CURRENT_EXPIRY_COMPARATOR_2026-09-07"
SOURCE_PROTOCOL_ID = source_dataset.PROTOCOL_ID
OLD_DECISION_PROTOCOL_ID = old_replay.PROTOCOL_ID
STOCKS = source_dataset.STOCKS
EXPECTED_HORIZONS = ("15m", "30m", "60m", "90m", "EOD")


def _action(value: Any) -> str:
    value = str(value or "NO_TRADE").upper()
    return value if value in {"LONG", "SHORT"} else "NO_TRADE"


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = old_replay._summarize(rows)
    result["barrier_outcomes"] = old_four_stock._barrier_summary(rows)
    return result


def _decision_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    transitions = Counter()
    same = 0
    both_actionable_same = 0
    both_actionable_opposite = 0
    v1_only = 0
    v3_only = 0
    neither = 0

    for row in rows:
        v1 = _action((row.get("decision") or {}).get("action"))
        v3 = _action(row.get("v3_action"))
        transitions[f"{v3}->{v1}"] += 1
        same += v1 == v3
        v1_active = v1 in {"LONG", "SHORT"}
        v3_active = v3 in {"LONG", "SHORT"}
        if v1_active and v3_active:
            if v1 == v3:
                both_actionable_same += 1
            else:
                both_actionable_opposite += 1
        elif v1_active:
            v1_only += 1
        elif v3_active:
            v3_only += 1
        else:
            neither += 1

    total = len(rows)
    return {
        "observations": total,
        "same_action": same,
        "same_action_rate_pct": round(100.0 * same / total, 4) if total else None,
        "both_actionable_same_direction": both_actionable_same,
        "both_actionable_opposite_direction": both_actionable_opposite,
        "v1_only_actionable": v1_only,
        "v3_only_actionable": v3_only,
        "neither_actionable": neither,
        "transition_counts_v3_to_v1": dict(sorted(transitions.items())),
    }


def evaluate_frozen_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate original V1 decision logic on the exact frozen V3 source dataset."""
    v3_backtest._source_contract(dataset)
    source_rows = list(dataset.get("rows") or [])
    tapes = dataset.get("frozen_5m_tape_by_stock") or {}
    evaluated: list[dict[str, Any]] = []

    for source_row in source_rows:
        symbol = str(source_row.get("symbol") or "").upper()
        if symbol not in STOCKS:
            raise ValueError(f"UNEXPECTED_SOURCE_SYMBOL:{symbol}")
        technical = source_row.get("technical") or {}
        decision = old_replay._decision_snapshot(technical)
        v1_action = _action(decision.get("action"))
        click = datetime.fromisoformat(str(source_row["click_at"]))
        tape = list(tapes[symbol] or [])
        direction = v1_action if v1_action in {"LONG", "SHORT"} else None
        outcome = old_replay.resolve_underlying_path(tape, click, direction)
        barrier = old_four_stock._barrier(tape, click, decision)
        evaluated.append({
            "trade_date": source_row.get("trade_date"),
            "click_at": source_row.get("click_at"),
            "symbol": symbol,
            "category": source_row.get("category"),
            "decision": decision,
            "v3_action": _action((source_row.get("decision") or {}).get("action")),
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
    experiment = dataset.get("experiment") or {}

    return {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED",
        "source": {
            "protocol_id": SOURCE_PROTOCOL_ID,
            "source_brain_protocol_id": (dataset.get("safety") or {}).get("brain_protocol_id"),
            "old_decision_protocol_id": OLD_DECISION_PROTOCOL_ID,
            "stocks": list(STOCKS),
            "expiry_cycle_start": experiment.get("expiry_cycle_start"),
            "expiry_date": experiment.get("expiry_date"),
            "data_frozen_through": experiment.get("data_frozen_through"),
            "completed_sessions": experiment.get("completed_sessions") or [],
            "session_count": experiment.get("session_count"),
            "clicks_per_session": experiment.get("clicks_per_session"),
            "observations": experiment.get("observations"),
            "frozen_5m_tape_hashes": dict(dataset.get("frozen_5m_tape_hashes") or {}),
        },
        "methodology": {
            "comparison": "ORIGINAL_V1_DECISION_LOGIC_ON_EXACT_V3_CURRENT_EXPIRY_CLICKS_AND_TAPES",
            "v1_decision_function": "fno_underlying_random_replay_v1._decision_snapshot",
            "technical_snapshot_source": "FROZEN_SOURCE_ROW_TECHNICAL",
            "v3_context_used_for_v1_decision": False,
            "v3_news_used_for_v1_decision": False,
            "v3_action_used_for_v1_decision": False,
            "horizons": list(EXPECTED_HORIZONS),
            "no_trade_large_move_thresholds_pct": list(old_replay.NO_TRADE_MOVE_THRESHOLDS_PCT),
            "same_outcome_resolver_as_original_v1": True,
            "new_strategy_thresholds_added": False,
            "retuning_from_v3_results": False,
        },
        "summary": overall,
        "by_stock": by_stock,
        "by_day": by_day,
        "decision_comparison": _decision_comparison(evaluated),
        "safety": architecture_contract(),
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "source_protocol_id": SOURCE_PROTOCOL_ID,
        "old_decision_protocol_id": OLD_DECISION_PROTOCOL_ID,
        "same_frozen_clicks_as_v3": True,
        "same_frozen_future_tapes_as_v3": True,
        "source_tape_hashes_verified": True,
        "frozen_technical_snapshots_reused": True,
        "old_v1_decision_logic_reused": True,
        "source_context_ignored_for_v1_decision": True,
        "source_news_ignored_for_v1_decision": True,
        "source_v3_action_ignored_for_v1_decision": True,
        "same_outcome_resolver_as_original_v1": True,
        "options_read": False,
        "futures_read": False,
        "live_execution": False,
        "capital_committed": 0,
        "strategy_policy_changed": False,
        "thresholds_retuned": False,
        "outcomes_can_change_decision": False,
        "result_rows_persisted": False,
    }
