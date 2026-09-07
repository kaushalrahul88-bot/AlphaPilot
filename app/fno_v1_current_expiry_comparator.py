"""Compare frozen V1 technical logic on the exact V3 current-expiry experiment.

V1 actions are derived only from each source row's frozen technical snapshot via
the original ``_decision_snapshot`` function.  The already-completed V3 replay is
used only as a cache of objective future-path facts (raw return, max-up and
max-down) that were produced by the same original underlying resolver.  All
direction-specific fields are recomputed for the V1 action, so no V3 direction,
context, news, or performance result can influence the V1 decision.
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
V3_EVALUATION_PROTOCOL_ID = v3_backtest.PROTOCOL_ID
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


def _objective_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("symbol") or "").upper(),
        str(row.get("click_at") or ""),
    )


def _objective_index(
    dataset: Mapping[str, Any],
    resolved_v3_result: Mapping[str, Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    if resolved_v3_result.get("status") != "COMPLETED":
        raise ValueError("V3_OBJECTIVE_RESULT_NOT_COMPLETED")
    if resolved_v3_result.get("protocol_id") != V3_EVALUATION_PROTOCOL_ID:
        raise ValueError("V3_OBJECTIVE_PROTOCOL_MISMATCH")
    source = resolved_v3_result.get("source") or {}
    if source.get("protocol_id") != SOURCE_PROTOCOL_ID:
        raise ValueError("V3_OBJECTIVE_SOURCE_PROTOCOL_MISMATCH")
    if dict(source.get("frozen_5m_tape_hashes") or {}) != dict(dataset.get("frozen_5m_tape_hashes") or {}):
        raise ValueError("V3_OBJECTIVE_TAPE_HASH_MISMATCH")

    source_rows = list(dataset.get("rows") or [])
    objective_rows = list(resolved_v3_result.get("rows") or [])
    if len(objective_rows) != len(source_rows):
        raise ValueError("V3_OBJECTIVE_ROW_COUNT_MISMATCH")

    index: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in objective_rows:
        key = _objective_key(row)
        if not all(key) or key in index:
            raise ValueError("V3_OBJECTIVE_KEY_INVALID_OR_DUPLICATE")
        index[key] = row

    if {_objective_key(row) for row in source_rows} != set(index):
        raise ValueError("V3_OBJECTIVE_KEYS_DO_NOT_MATCH_SOURCE")
    return index


def _retarget_block(block: Mapping[str, Any], direction: str | None) -> dict[str, Any]:
    payload = dict(block or {})
    if not payload.get("resolved"):
        payload["directional_return_pct"] = None
        payload["mfe_pct"] = None
        payload["mae_pct"] = None
        return payload

    raw = payload.get("raw_return_pct")
    max_up = payload.get("max_up_pct")
    max_down = payload.get("max_down_pct")
    if direction == "LONG":
        payload["directional_return_pct"] = float(raw) if raw is not None else None
        payload["mfe_pct"] = float(max_up) if max_up is not None else None
        payload["mae_pct"] = float(max_down) if max_down is not None else None
    elif direction == "SHORT":
        payload["directional_return_pct"] = round(-float(raw), 6) if raw is not None else None
        payload["mfe_pct"] = round(-float(max_down), 6) if max_down is not None else None
        payload["mae_pct"] = round(-float(max_up), 6) if max_up is not None else None
    else:
        payload["directional_return_pct"] = None
        payload["mfe_pct"] = None
        payload["mae_pct"] = None
    return payload


def _retarget_outcome(objective: Mapping[str, Any], direction: str | None) -> dict[str, Any]:
    checkpoints = {
        key: _retarget_block(value or {}, direction)
        for key, value in dict(objective.get("checkpoints") or {}).items()
    }
    return {
        "reference_price": objective.get("reference_price"),
        "status": objective.get("status"),
        "checkpoints": checkpoints,
        "eod": _retarget_block(objective.get("eod") or {}, direction),
    }


def evaluate_frozen_dataset(
    dataset: Mapping[str, Any],
    resolved_v3_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate original V1 logic using frozen technicals and objective paths."""
    v3_backtest._source_contract(dataset)
    objective = _objective_index(dataset, resolved_v3_result)
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
        direction = v1_action if v1_action in {"LONG", "SHORT"} else None
        objective_row = objective[_objective_key(source_row)]
        outcome = _retarget_outcome(objective_row.get("outcome") or {}, direction)

        # Model-level path diagnostics remain resolved directly against the same
        # hash-verified frozen tape because V1 levels can differ from V3 levels.
        click = datetime.fromisoformat(str(source_row["click_at"]))
        tape = list(tapes[symbol] or [])
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
            "objective_path_protocol_id": V3_EVALUATION_PROTOCOL_ID,
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
            "objective_path_source": "COMPLETED_V3_EVALUATION_RAW_PATH_FACTS_FROM_ORIGINAL_V1_RESOLVER",
            "v3_directional_fields_reused": False,
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
        "objective_path_protocol_id": V3_EVALUATION_PROTOCOL_ID,
        "same_frozen_clicks_as_v3": True,
        "same_frozen_future_tapes_as_v3": True,
        "source_tape_hashes_verified": True,
        "frozen_technical_snapshots_reused": True,
        "old_v1_decision_logic_reused": True,
        "objective_paths_reused_from_same_original_resolver": True,
        "v3_directional_outputs_ignored": True,
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
