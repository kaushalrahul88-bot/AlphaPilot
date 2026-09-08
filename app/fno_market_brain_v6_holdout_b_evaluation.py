"""Pre-registered evaluator for frozen V6 Historical Holdout B.

This module is intentionally frozen before Holdout B outcomes are inspected. It
never recomputes or mutates V6 decisions. It verifies the frozen source contract
and 5-minute tape hashes, resolves the established underlying-only replay path,
and applies exactly the gates registered in
``docs/fno_market_brain_v6_holdout_b_protocol.md``.

The 90-minute horizon is primary. 15m/30m/60m/EOD and MFE/MAE are descriptive.
No options, futures, execution, or capital logic is present here.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import fno_market_brain_v6_holdout_b_dataset as source_dataset
from . import fno_underlying_random_replay_v1 as replay

PROTOCOL_ID = "FNO_MARKET_BRAIN_V6_HOLDOUT_B_EVALUATION_V1_2026-09-08"
SOURCE_PROTOCOL_ID = source_dataset.PROTOCOL_ID
BRAIN_FROZEN_COMMIT = source_dataset.BRAIN_FROZEN_COMMIT
BRAIN_PROTOCOL_ID = "FNO_MARKET_BRAIN_V6_EVIDENCE_TRANSITION_2026-09-08"
STOCKS = source_dataset.STOCKS
PRIMARY_HORIZON = "90m"
HORIZONS = ("15m", "30m", "60m", "90m", "EOD")
EVIDENCE_MODES = (
    "DUAL_VOLUME",
    "15M_PRICE_ACTION",
    "DUAL_VOLUME_AND_15M_PRICE_ACTION",
)
EXPECTED_WINDOWS = tuple(
    (window_id, start.isoformat(), end.isoformat())
    for window_id, start, end in source_dataset.WINDOWS
)

# Frozen verbatim from the pre-outcome Holdout B protocol.
GATES = {
    "minimum_actionable_overall": 80,
    "minimum_actionable_each_window": 30,
    "minimum_stocks_meeting_stock_sample": 6,
    "minimum_actionable_per_stock_for_robustness": 8,
    "minimum_nonflat_90m_accuracy_pct": 58.0,
    "maximum_one_sided_binomial_p": 0.05,
    "minimum_windows_accuracy_exclusive_pct": 50.0,
    "minimum_positive_mean_stocks": 5,
    "minimum_stock_accuracy_pct": 45.0,
    "maximum_stock_concentration_pct": 30.0,
    "maximum_session_concentration_pct": 15.0,
}


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return round(statistics.fmean(values), 6) if values else None


def _median(values: Iterable[float]) -> float | None:
    values = list(values)
    return round(statistics.median(values), 6) if values else None


def exact_one_sided_binomial_p(wins: int, nonflat: int) -> float | None:
    """Exact P[X >= wins] for X~Binomial(nonflat, 0.5)."""
    if nonflat <= 0 or wins < 0 or wins > nonflat:
        return None
    numerator = sum(math.comb(nonflat, k) for k in range(wins, nonflat + 1))
    return float(Fraction(numerator, 1 << nonflat))


def wilson_interval_pct(wins: int, nonflat: int, z: float = 1.959963984540054) -> list[float] | None:
    if nonflat <= 0:
        return None
    p = wins / nonflat
    z2 = z * z
    denominator = 1.0 + z2 / nonflat
    centre = (p + z2 / (2.0 * nonflat)) / denominator
    half = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * nonflat)) / nonflat) / denominator
    return [round(100.0 * max(0.0, centre - half), 4), round(100.0 * min(1.0, centre + half), 4)]


def _source_contract(dataset: Mapping[str, Any]) -> None:
    if dataset.get("status") != "COMPLETED":
        raise ValueError("SOURCE_DATASET_NOT_COMPLETED")
    if dataset.get("protocol_id") != SOURCE_PROTOCOL_ID:
        raise ValueError("SOURCE_PROTOCOL_MISMATCH")
    if dataset.get("brain_frozen_commit") != BRAIN_FROZEN_COMMIT:
        raise ValueError("SOURCE_BRAIN_COMMIT_MISMATCH")
    if dataset.get("brain_protocol_id") != BRAIN_PROTOCOL_ID:
        raise ValueError("SOURCE_BRAIN_PROTOCOL_MISMATCH")

    experiment = dataset.get("experiment") or {}
    if experiment.get("kind") != "HISTORICAL_HOLDOUT_NOT_FORWARD_TEST":
        raise ValueError("SOURCE_EXPERIMENT_KIND_MISMATCH")
    symbols = tuple(str(item.get("symbol") or "") for item in experiment.get("stocks") or [])
    if symbols != STOCKS:
        raise ValueError("SOURCE_STOCK_UNIVERSE_MISMATCH")
    windows = tuple(
        (str(item.get("id") or ""), str(item.get("start") or ""), str(item.get("end") or ""))
        for item in experiment.get("windows") or []
    )
    if windows != EXPECTED_WINDOWS:
        raise ValueError("SOURCE_WINDOWS_MISMATCH")
    if int(experiment.get("clicks_per_session") or 0) != source_dataset.CLICKS_PER_DAY:
        raise ValueError("SOURCE_CLICK_COUNT_MISMATCH")
    rows = list(dataset.get("rows") or [])
    if int(experiment.get("observations") or 0) != len(rows):
        raise ValueError("SOURCE_OBSERVATION_COUNT_MISMATCH")
    expected = int(experiment.get("session_count") or 0) * source_dataset.CLICKS_PER_DAY * len(STOCKS)
    if expected <= 0 or expected != len(rows):
        raise ValueError("SOURCE_EXPECTED_OBSERVATION_COUNT_MISMATCH")
    if experiment.get("same_click_times_across_stocks") is not True:
        raise ValueError("SOURCE_CLICK_ALIGNMENT_NOT_FROZEN")
    if experiment.get("thesis_tracker_reset_each_stock_session") is not True:
        raise ValueError("SOURCE_THESIS_TRACKER_CONTRACT_MISMATCH")
    if experiment.get("future_outcomes_resolved") is not False:
        raise ValueError("SOURCE_ALREADY_OUTCOME_RESOLVED")
    if experiment.get("evaluation_metrics_computed") is not False:
        raise ValueError("SOURCE_ALREADY_EVALUATED")

    safety = dataset.get("safety") or {}
    required_true = (
        "historical_backtest_dataset_only",
        "windows_frozen_before_outcomes",
        "completed_candles_only",
        "point_in_time_context_only",
        "clicks_deterministic_and_frozen",
        "same_click_times_across_stocks",
        "thesis_deduplication_enabled",
        "future_tape_frozen",
        "history_transport_cache_only",
    )
    if not all(safety.get(key) is True for key in required_true):
        raise ValueError("SOURCE_SAFETY_TRUE_CONTRACT_MISMATCH")
    required_false = (
        "forward_test",
        "future_outcomes_resolved",
        "evaluation_metrics_computed",
        "options_read_for_decision",
        "futures_read_for_decision",
        "v3_development_rows_used_as_holdout",
        "v5_holdout_a_rows_used_as_holdout",
        "live_execution",
        "history_cache_may_change_decisions",
    )
    if not all(safety.get(key) is False for key in required_false):
        raise ValueError("SOURCE_SAFETY_FALSE_CONTRACT_MISMATCH")
    if int(safety.get("capital_committed") or 0) != 0:
        raise ValueError("SOURCE_CAPITAL_COMMITTED")

    # Re-prove 20 unique common click timestamps per session and identical clicks
    # across every stock before resolving a single outcome.
    clicks_by_day_symbol: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        day = str(row.get("trade_date") or "")
        symbol = str(row.get("symbol") or "").upper()
        click = str(row.get("click_at") or "")
        if symbol not in STOCKS or not day or not click:
            raise ValueError("SOURCE_ROW_IDENTITY_INVALID")
        clicks_by_day_symbol.setdefault((day, symbol), set()).add(click)
    days = sorted({day for day, _ in clicks_by_day_symbol})
    for day in days:
        expected_clicks: set[str] | None = None
        for symbol in STOCKS:
            clicks = clicks_by_day_symbol.get((day, symbol), set())
            if len(clicks) != source_dataset.CLICKS_PER_DAY:
                raise ValueError(f"SOURCE_CLICK_UNIQUENESS_MISMATCH:{day}:{symbol}")
            if expected_clicks is None:
                expected_clicks = clicks
            elif clicks != expected_clicks:
                raise ValueError(f"SOURCE_CLICKS_NOT_COMMON:{day}:{symbol}")

    tapes = dataset.get("frozen_5m_tape_by_stock") or {}
    hashes = dataset.get("frozen_5m_tape_hashes") or {}
    for symbol in STOCKS:
        tape = list(tapes.get(symbol) or [])
        if not tape or symbol not in hashes:
            raise ValueError(f"SOURCE_FROZEN_TAPE_MISSING:{symbol}")
        if source_dataset._tape_hash(tape) != hashes[symbol]:
            raise ValueError(f"SOURCE_FROZEN_TAPE_HASH_MISMATCH:{symbol}")


def _action(row: Mapping[str, Any]) -> str:
    action = str((row.get("decision") or {}).get("action") or "NO_TRADE").upper()
    return action if action in {"LONG", "SHORT"} else "NO_TRADE"


def _evidence_mode(row: Mapping[str, Any]) -> str:
    decision = row.get("decision") or {}
    thesis = decision.get("thesis") or {}
    evidence = decision.get("evidence") or {}
    return str(thesis.get("evidence_mode") or evidence.get("mode") or "NONE")


def _block(row: Mapping[str, Any], horizon: str) -> Mapping[str, Any]:
    outcome = row.get("outcome") or {}
    if horizon == "EOD":
        return outcome.get("eod") or {}
    return (outcome.get("checkpoints") or {}).get(horizon) or {}


def _horizon_summary(rows: Sequence[Mapping[str, Any]], horizon: str) -> dict[str, Any]:
    actionable = [row for row in rows if _action(row) in {"LONG", "SHORT"}]
    values: list[float] = []
    mfe: list[float] = []
    mae: list[float] = []
    for row in actionable:
        block = _block(row, horizon)
        value = block.get("directional_return_pct")
        if value is None:
            continue
        values.append(float(value))
        if block.get("mfe_pct") is not None:
            mfe.append(float(block["mfe_pct"]))
        if block.get("mae_pct") is not None:
            mae.append(float(block["mae_pct"]))

    wins = sum(value > 0 for value in values)
    losses = sum(value < 0 for value in values)
    flats = len(values) - wins - losses
    nonflat = wins + losses
    accuracy = round(100.0 * wins / nonflat, 4) if nonflat else None
    return {
        "actionable": len(actionable),
        "resolved_actionable": len(values),
        "unresolved_actionable": len(actionable) - len(values),
        "direction_correct": wins,
        "direction_incorrect": losses,
        "flat": flats,
        "nonflat": nonflat,
        "direction_correct_rate_nonflat_pct": accuracy,
        "mean_directional_return_pct": _mean(values),
        "median_directional_return_pct": _median(values),
        "wilson_95pct_accuracy_interval_pct": wilson_interval_pct(wins, nonflat),
        "one_sided_exact_binomial_p_vs_50pct": (
            round(exact_one_sided_binomial_p(wins, nonflat), 12)
            if nonflat and exact_one_sided_binomial_p(wins, nonflat) is not None
            else None
        ),
        "mean_mfe_pct": _mean(mfe),
        "median_mfe_pct": _median(mfe),
        "mean_mae_pct": _mean(mae),
        "median_mae_pct": _median(mae),
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    action_counts = Counter(_action(row) for row in rows)
    actionable = sum(action_counts[action] for action in ("LONG", "SHORT"))
    return {
        "observations": len(rows),
        "action_counts": dict(sorted(action_counts.items())),
        "actionable": actionable,
        "no_trade": action_counts.get("NO_TRADE", 0),
        "actionable_rate_pct": round(100.0 * actionable / len(rows), 4) if rows else None,
        "horizons": {horizon: _horizon_summary(rows, horizon) for horizon in HORIZONS},
    }


def _split(rows: Sequence[Mapping[str, Any]], key_fn, values: Sequence[str] | None = None) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = str(key_fn(row))
        groups.setdefault(key, []).append(row)
    keys = list(values) if values is not None else sorted(groups)
    return {key: summarize(groups.get(key, [])) for key in keys}


def apply_pre_registered_gates(
    overall: Mapping[str, Any],
    by_window: Mapping[str, Mapping[str, Any]],
    by_stock: Mapping[str, Mapping[str, Any]],
    by_session: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    primary = (overall.get("horizons") or {}).get(PRIMARY_HORIZON) or {}
    total_actionable = int(overall.get("actionable") or 0)

    window_counts = {key: int(value.get("actionable") or 0) for key, value in by_window.items()}
    stock_counts = {key: int(value.get("actionable") or 0) for key, value in by_stock.items()}
    eligible_stocks = [
        symbol for symbol, count in stock_counts.items()
        if count >= GATES["minimum_actionable_per_stock_for_robustness"]
    ]

    sufficient_overall = total_actionable >= GATES["minimum_actionable_overall"]
    sufficient_windows = all(
        window_counts.get(window_id, 0) >= GATES["minimum_actionable_each_window"]
        for window_id, _, _ in EXPECTED_WINDOWS
    )
    sufficient_stocks = len(eligible_stocks) >= GATES["minimum_stocks_meeting_stock_sample"]

    accuracy = primary.get("direction_correct_rate_nonflat_pct")
    p_value = primary.get("one_sided_exact_binomial_p_vs_50pct")
    accuracy_pass = accuracy is not None and float(accuracy) >= GATES["minimum_nonflat_90m_accuracy_pct"]
    significance_pass = p_value is not None and float(p_value) < GATES["maximum_one_sided_binomial_p"]

    mean_return = primary.get("mean_directional_return_pct")
    median_return = primary.get("median_directional_return_pct")
    mean_pass = mean_return is not None and float(mean_return) > 0.0
    median_pass = median_return is not None and float(median_return) >= 0.0

    temporal_detail: dict[str, Any] = {}
    temporal_pass = True
    for window_id, _, _ in EXPECTED_WINDOWS:
        summary = by_window.get(window_id) or {}
        block = (summary.get("horizons") or {}).get(PRIMARY_HORIZON) or {}
        win_accuracy = block.get("direction_correct_rate_nonflat_pct")
        win_mean = block.get("mean_directional_return_pct")
        passed = (
            win_accuracy is not None
            and float(win_accuracy) > GATES["minimum_windows_accuracy_exclusive_pct"]
            and win_mean is not None
            and float(win_mean) > 0.0
        )
        temporal_detail[window_id] = {
            "accuracy_pct": win_accuracy,
            "mean_directional_return_pct": win_mean,
            "passed": passed,
        }
        temporal_pass = temporal_pass and passed

    stock_detail: dict[str, Any] = {}
    positive_mean_stocks = 0
    stock_floor_pass = True
    for symbol in eligible_stocks:
        block = ((by_stock.get(symbol) or {}).get("horizons") or {}).get(PRIMARY_HORIZON) or {}
        stock_mean = block.get("mean_directional_return_pct")
        stock_accuracy = block.get("direction_correct_rate_nonflat_pct")
        positive = stock_mean is not None and float(stock_mean) > 0.0
        floor_ok = stock_accuracy is not None and float(stock_accuracy) >= GATES["minimum_stock_accuracy_pct"]
        positive_mean_stocks += int(positive)
        stock_floor_pass = stock_floor_pass and floor_ok
        stock_detail[symbol] = {
            "actionable": stock_counts[symbol],
            "accuracy_pct": stock_accuracy,
            "mean_directional_return_pct": stock_mean,
            "positive_mean": positive,
            "accuracy_floor_pass": floor_ok,
        }
    cross_stock_pass = (
        len(eligible_stocks) >= GATES["minimum_stocks_meeting_stock_sample"]
        and positive_mean_stocks >= GATES["minimum_positive_mean_stocks"]
        and stock_floor_pass
    )

    stock_concentration = {
        symbol: round(100.0 * count / total_actionable, 6) if total_actionable else None
        for symbol, count in stock_counts.items()
    }
    session_counts = {key: int(value.get("actionable") or 0) for key, value in by_session.items()}
    session_concentration = {
        day: round(100.0 * count / total_actionable, 6) if total_actionable else None
        for day, count in session_counts.items()
    }
    max_stock = max((value for value in stock_concentration.values() if value is not None), default=None)
    max_session = max((value for value in session_concentration.values() if value is not None), default=None)
    concentration_pass = (
        max_stock is not None
        and max_stock <= GATES["maximum_stock_concentration_pct"]
        and max_session is not None
        and max_session <= GATES["maximum_session_concentration_pct"]
    )

    gate_flags = {
        "sample_sufficiency": sufficient_overall and sufficient_windows and sufficient_stocks,
        "directional_accuracy_and_significance": accuracy_pass and significance_pass,
        "directional_payoff": mean_pass and median_pass,
        "temporal_robustness": temporal_pass,
        "cross_stock_robustness": cross_stock_pass,
        "concentration_control": concentration_pass,
    }
    return {
        "passed": all(gate_flags.values()),
        "gate_flags": gate_flags,
        "details": {
            "sample_sufficiency": {
                "actionable_overall": total_actionable,
                "minimum_overall": GATES["minimum_actionable_overall"],
                "actionable_by_window": window_counts,
                "minimum_each_window": GATES["minimum_actionable_each_window"],
                "eligible_stocks": eligible_stocks,
                "eligible_stock_count": len(eligible_stocks),
                "minimum_eligible_stocks": GATES["minimum_stocks_meeting_stock_sample"],
                "stock_sample_threshold": GATES["minimum_actionable_per_stock_for_robustness"],
            },
            "directional_accuracy_and_significance": {
                "accuracy_pct": accuracy,
                "minimum_accuracy_pct": GATES["minimum_nonflat_90m_accuracy_pct"],
                "wilson_95pct_accuracy_interval_pct": primary.get("wilson_95pct_accuracy_interval_pct"),
                "one_sided_exact_binomial_p_vs_50pct": p_value,
                "maximum_p_exclusive": GATES["maximum_one_sided_binomial_p"],
            },
            "directional_payoff": {
                "mean_directional_return_pct": mean_return,
                "median_directional_return_pct": median_return,
                "mean_must_be_positive": True,
                "median_must_be_nonnegative": True,
            },
            "temporal_robustness": temporal_detail,
            "cross_stock_robustness": {
                "eligible_stock_count": len(eligible_stocks),
                "positive_mean_stock_count": positive_mean_stocks,
                "minimum_positive_mean_stocks": GATES["minimum_positive_mean_stocks"],
                "stock_accuracy_floor_pct": GATES["minimum_stock_accuracy_pct"],
                "stocks": stock_detail,
            },
            "concentration_control": {
                "stock_concentration_pct": stock_concentration,
                "session_concentration_pct": session_concentration,
                "maximum_stock_concentration_observed_pct": max_stock,
                "maximum_stock_concentration_allowed_pct": GATES["maximum_stock_concentration_pct"],
                "maximum_session_concentration_observed_pct": max_session,
                "maximum_session_concentration_allowed_pct": GATES["maximum_session_concentration_pct"],
            },
        },
    }


def evaluate_frozen_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the already-frozen V6 decisions and apply only registered gates."""
    _source_contract(dataset)
    tapes = dataset.get("frozen_5m_tape_by_stock") or {}
    evaluated: list[dict[str, Any]] = []

    for source_row in dataset.get("rows") or []:
        symbol = str(source_row.get("symbol") or "").upper()
        click = datetime.fromisoformat(str(source_row["click_at"]))
        action = _action(source_row)
        direction = action if action in {"LONG", "SHORT"} else None
        outcome = replay.resolve_underlying_path(list(tapes[symbol] or []), click, direction)
        decision = source_row.get("decision") or {}
        evaluated.append({
            "window": source_row.get("window"),
            "trade_date": source_row.get("trade_date"),
            "click_at": source_row.get("click_at"),
            "symbol": symbol,
            "category": source_row.get("category"),
            "decision": {
                "action": action,
                "protocol_id": decision.get("protocol_id"),
                "setup_type": decision.get("setup_type"),
                "thesis_key": (decision.get("thesis") or {}).get("key"),
                "state_signature": (decision.get("thesis") or {}).get("state_signature"),
                "evidence_mode": _evidence_mode(source_row),
                "reasons": decision.get("reasons") or [],
            },
            "outcome": outcome,
        })

    # A missing 90m path is a source-data integrity failure, not permission to
    # silently shrink the pre-registered denominator.
    unresolved_primary = [
        row for row in evaluated
        if _action(row) in {"LONG", "SHORT"}
        and _block(row, PRIMARY_HORIZON).get("directional_return_pct") is None
    ]
    if unresolved_primary:
        raise ValueError(f"PRIMARY_OUTCOME_DATA_INCOMPLETE:{len(unresolved_primary)}")

    overall = summarize(evaluated)
    by_window = _split(evaluated, lambda row: row.get("window") or "UNKNOWN", [item[0] for item in EXPECTED_WINDOWS])
    by_stock = _split(evaluated, lambda row: row.get("symbol") or "UNKNOWN", list(STOCKS))
    by_session = _split(evaluated, lambda row: row.get("trade_date") or "UNKNOWN")
    by_side = {
        side: summarize([row for row in evaluated if _action(row) == side])
        for side in ("LONG", "SHORT")
    }
    actionable_rows = [row for row in evaluated if _action(row) in {"LONG", "SHORT"}]
    by_evidence_mode = {
        mode: summarize([
            row for row in actionable_rows
            if str((row.get("decision") or {}).get("evidence_mode") or "NONE") == mode
        ])
        for mode in EVIDENCE_MODES
    }

    gates = apply_pre_registered_gates(overall, by_window, by_stock, by_session)
    experiment = dataset.get("experiment") or {}
    return {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED",
        "source": {
            "protocol_id": SOURCE_PROTOCOL_ID,
            "brain_protocol_id": BRAIN_PROTOCOL_ID,
            "brain_frozen_commit": BRAIN_FROZEN_COMMIT,
            "dataset_deployment_commit": dataset.get("deployment_commit"),
            "stocks": list(STOCKS),
            "windows": [
                {"id": item.get("id"), "start": item.get("start"), "end": item.get("end"), "completed_sessions": item.get("completed_sessions") or []}
                for item in experiment.get("windows") or []
            ],
            "session_count": experiment.get("session_count"),
            "clicks_per_session": experiment.get("clicks_per_session"),
            "observations": experiment.get("observations"),
            "frozen_5m_tape_hashes": dict(dataset.get("frozen_5m_tape_hashes") or {}),
        },
        "primary_horizon": PRIMARY_HORIZON,
        "secondary_horizons_descriptive_only": [h for h in HORIZONS if h != PRIMARY_HORIZON],
        "pre_registered_gates": dict(GATES),
        "gate_result": gates,
        "summary": overall,
        "by_window": by_window,
        "by_stock": by_stock,
        "by_session": by_session,
        "by_side": by_side,
        "by_evidence_mode": by_evidence_mode,
        "rows": evaluated,
        "safety": architecture_contract(),
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "source_protocol_id": SOURCE_PROTOCOL_ID,
        "brain_frozen_commit": BRAIN_FROZEN_COMMIT,
        "evaluator_frozen_before_outcomes": True,
        "source_decisions_recomputed": False,
        "source_decisions_mutated": False,
        "source_tape_hashes_verified": True,
        "effective_deduplicated_decision_used": True,
        "primary_horizon": PRIMARY_HORIZON,
        "flat_excluded_from_accuracy_denominator_only": True,
        "registered_gates_changed": False,
        "diagnostics_can_override_failed_gate": False,
        "options_read": False,
        "futures_read": False,
        "forward_test": False,
        "live_execution": False,
        "capital_committed": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate frozen V6 Historical Holdout B dataset")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, default=Path("v6-holdout-b-evaluation.json"))
    args = parser.parse_args(argv)
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    result = evaluate_frozen_dataset(dataset)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "protocol_id": result["protocol_id"],
        "status": result["status"],
        "passed": result["gate_result"]["passed"],
        "primary": result["summary"]["horizons"][PRIMARY_HORIZON],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
