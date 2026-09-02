from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median

from .commodity_time import parse_ist_timestamp

HORIZONS = (15, 30, 60)
PRIMARY_HORIZON_MINUTES = 60


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _signed_forward(row: dict, minutes: int) -> float | None:
    raw = _f((row.get("future_returns_pct") or {}).get(str(minutes)))
    if raw is None:
        return None
    action = str(row.get("action") or "").upper()
    if action == "BUY_CE":
        return raw
    if action == "BUY_PE":
        return -raw
    return None


def _direction_class(row: dict, minutes: int = PRIMARY_HORIZON_MINUTES) -> str:
    signed = _signed_forward(row, minutes)
    if signed is None:
        return "UNKNOWN"
    if signed > 0:
        return "CORRECT"
    if signed < 0:
        return "WRONG"
    return "FLAT"


def _memory_stance(row: dict) -> str:
    items = (((row.get("evidence") or {}).get("lanes") or {}).get("EXPERIENCE") or [])
    if not items:
        return "UNKNOWN"
    return str(items[-1].get("stance") or "UNKNOWN").upper()


def _dimension(row: dict, name: str) -> str:
    features = row.get("features") or {}
    regime = ((row.get("regime") or {}).get("observations") or {})
    decision = row.get("decision") or {}
    click = parse_ist_timestamp(row["click_timestamp"])
    if name == "action":
        return str(row.get("action") or "UNKNOWN")
    if name == "playbook":
        return str(decision.get("playbook") or "UNKNOWN")
    if name == "structure":
        return str(features.get("structure") or "UNKNOWN")
    if name == "volatility_regime":
        return str(regime.get("volatility_regime") or "UNKNOWN")
    if name == "location":
        return str(regime.get("location") or "UNKNOWN")
    if name == "participation":
        return str(regime.get("participation") or "UNKNOWN")
    if name == "opening_behavior":
        return str(regime.get("opening_behavior") or "UNKNOWN")
    if name == "memory_stance":
        return _memory_stance(row)
    if name == "session":
        if click.hour < 12:
            return "MORNING"
        if click.hour < 16:
            return "MIDDAY"
        return "EVENING"
    return "UNKNOWN"


def _path_stats(rows: list[dict]) -> dict:
    mfe = [value for row in rows if (value := _f((row.get("outcome") or {}).get("mfe_r"))) is not None]
    mae = [value for row in rows if (value := _f((row.get("outcome") or {}).get("mae_r"))) is not None]
    return {
        "observations_with_mfe": len(mfe),
        "avg_mfe_r": round(mean(mfe), 4) if mfe else None,
        "median_mfe_r": round(median(mfe), 4) if mfe else None,
        "observations_with_mae": len(mae),
        "avg_mae_r": round(mean(mae), 4) if mae else None,
        "median_mae_r": round(median(mae), 4) if mae else None,
    }


def _stats(rows: list[dict]) -> dict:
    outcomes = Counter(str((row.get("outcome") or {}).get("result") or "UNKNOWN") for row in rows)
    direction = Counter(_direction_class(row) for row in rows)
    known = direction["CORRECT"] + direction["WRONG"] + direction["FLAT"]
    resolved = [row for row in rows if str((row.get("outcome") or {}).get("result")) in {"TARGET", "STOP"}]
    realized = [
        value for row in resolved
        if (value := _f((row.get("outcome") or {}).get("realized_r"))) is not None
    ]
    return {
        "trades": len(rows),
        "direction_known": known,
        "direction_correct": direction["CORRECT"],
        "direction_wrong": direction["WRONG"],
        "direction_flat": direction["FLAT"],
        "direction_unknown": direction["UNKNOWN"],
        "direction_accuracy_pct": round(direction["CORRECT"] / known * 100.0, 2) if known else None,
        "outcomes": dict(sorted(outcomes.items())),
        "resolved_trades": len(resolved),
        "expectancy_r_resolved": round(mean(realized), 4) if realized else None,
        "path": _path_stats(rows),
    }


def _contingency(rows: list[dict]) -> dict:
    table: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        outcome = str((row.get("outcome") or {}).get("result") or "UNKNOWN")
        table[outcome][_direction_class(row)] += 1
    return {
        outcome: dict(sorted(counts.items()))
        for outcome, counts in sorted(table.items())
    }


def _trajectory(row: dict) -> str:
    labels = [_direction_class(row, minutes) for minutes in HORIZONS]
    if any(label == "UNKNOWN" for label in labels):
        return "INCOMPLETE"
    if all(label == "CORRECT" for label in labels):
        return "CORRECT_ALL_15_30_60"
    if all(label == "WRONG" for label in labels):
        return "WRONG_ALL_15_30_60"
    if labels[0] == "WRONG" and labels[-1] == "CORRECT":
        return "EARLY_WRONG_LATE_CORRECT"
    if labels[0] == "CORRECT" and labels[-1] == "WRONG":
        return "EARLY_CORRECT_LATE_WRONG"
    return "MIXED"


def _window_map(rows: list[dict], windows: int = 3) -> dict[str, int]:
    ordered = sorted(rows, key=lambda row: parse_ist_timestamp(row["click_timestamp"]))
    n = len(ordered)
    return {
        row["click_timestamp"]: min(windows - 1, int(index * windows / max(1, n)))
        for index, row in enumerate(ordered)
    }


def evaluate_direction_geometry_audit(baseline_report: dict) -> dict:
    """Diagnose Crude directional quality separately from trade geometry.

    The audit consumes already-frozen Current Mind decisions and their already-scored
    outcomes. It does not reroute a decision, search a threshold, or propose a
    production setting. A positive 60-minute signed return is only a directional
    diagnostic; it is not interchangeable with target-first execution or option P&L.
    """
    trades = [
        row for row in (baseline_report.get("decisions") or [])
        if str(row.get("action") or "").upper() in {"BUY_CE", "BUY_PE"}
    ]
    windows = _window_map(trades)

    horizon_summary = {}
    for minutes in HORIZONS:
        classes = Counter(_direction_class(row, minutes) for row in trades)
        known = classes["CORRECT"] + classes["WRONG"] + classes["FLAT"]
        horizon_summary[str(minutes)] = {
            "known": known,
            "correct": classes["CORRECT"],
            "wrong": classes["WRONG"],
            "flat": classes["FLAT"],
            "unknown": classes["UNKNOWN"],
            "accuracy_pct": round(classes["CORRECT"] / known * 100.0, 2) if known else None,
        }

    correct_60 = [row for row in trades if _direction_class(row) == "CORRECT"]
    wrong_60 = [row for row in trades if _direction_class(row) == "WRONG"]
    correct_stops = [row for row in correct_60 if (row.get("outcome") or {}).get("result") == "STOP"]
    wrong_targets = [row for row in wrong_60 if (row.get("outcome") or {}).get("result") == "TARGET"]
    all_stops = [row for row in trades if (row.get("outcome") or {}).get("result") == "STOP"]
    all_targets = [row for row in trades if (row.get("outcome") or {}).get("result") == "TARGET"]

    trajectory_counts = Counter(_trajectory(row) for row in trades)
    trajectory_report = {}
    for label in sorted(trajectory_counts):
        subset = [row for row in trades if _trajectory(row) == label]
        trajectory_report[label] = _stats(subset)

    dimensions = {}
    for name in (
        "action", "playbook", "structure", "volatility_regime", "location",
        "participation", "opening_behavior", "memory_stance", "session",
    ):
        groups = defaultdict(list)
        for row in trades:
            groups[_dimension(row, name)].append(row)
        dimensions[name] = {state: _stats(group) for state, group in sorted(groups.items())}

    chronological = []
    for window in range(3):
        subset = [row for row in trades if windows.get(row["click_timestamp"]) == window]
        chronological.append({
            "window": window + 1,
            "first_click": min((row["click_timestamp"] for row in subset), default=None),
            "last_click": max((row["click_timestamp"] for row in subset), default=None),
            **_stats(subset),
            "contingency": _contingency(subset),
        })

    correct_stop_by_playbook = Counter(_dimension(row, "playbook") for row in correct_stops)
    correct_stop_by_action = Counter(_dimension(row, "action") for row in correct_stops)
    wrong_target_by_playbook = Counter(_dimension(row, "playbook") for row in wrong_targets)

    return {
        "mode": "CRUDE_OIL_MINI_DIRECTION_VS_GEOMETRY_AUDIT_V1",
        "research_only": True,
        "descriptive_only": True,
        "strategy_rules_changed": False,
        "decision_path_changed": False,
        "geometry_changed": False,
        "threshold_search_performed": False,
        "news_used": False,
        "option_market_data_used": False,
        "reference_contract": baseline_report.get("reference_contract"),
        "clicks": baseline_report.get("evaluated_clicks"),
        "trade_observations": len(trades),
        "primary_direction_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "horizon_direction": horizon_summary,
        "overall": _stats(trades),
        "outcome_by_60m_direction": _contingency(trades),
        "geometry_realization": {
            "direction_correct_trades": len(correct_60),
            "direction_wrong_trades": len(wrong_60),
            "direction_correct_stops": len(correct_stops),
            "direction_correct_stops_pct_of_all_stops": round(len(correct_stops) / len(all_stops) * 100.0, 2) if all_stops else None,
            "direction_correct_stop_path": _path_stats(correct_stops),
            "direction_correct_stops_by_action": dict(sorted(correct_stop_by_action.items())),
            "direction_correct_stops_by_playbook": dict(sorted(correct_stop_by_playbook.items())),
            "direction_wrong_targets": len(wrong_targets),
            "direction_wrong_targets_pct_of_all_targets": round(len(wrong_targets) / len(all_targets) * 100.0, 2) if all_targets else None,
            "direction_wrong_target_path": _path_stats(wrong_targets),
            "direction_wrong_targets_by_playbook": dict(sorted(wrong_target_by_playbook.items())),
            "interpretation": (
                "A correct 60-minute direction with a STOP means the later horizon agreed with the thesis but the frozen "
                "entry/invalidation path failed first. It is a geometry/timing research case, not proof the stop was wrong. "
                "A TARGET with wrong 60-minute direction means target-first execution succeeded before later reversal."
            ),
        },
        "direction_trajectory": trajectory_report,
        "chronological_windows": chronological,
        "dimensions": dimensions,
        "research_hypothesis": (
            "If correct-direction STOPs are substantial and recurrent, test playbook-appropriate entry/invalidation geometry "
            "as a separately preregistered candidate on future/held-out data. Do not fit geometry to this inspected sample."
        ),
        "promotion_allowed": False,
        "guardrails": [
            "The audit consumes frozen decisions and outcomes; it cannot change BUY_CE, BUY_PE or WAIT.",
            "No entry, stop, target, R multiple or directional threshold is searched or retuned.",
            "60-minute directional correctness is an underlying diagnostic, not target-first execution and not option P&L.",
            "Outcome-aware findings may generate a hypothesis only; they cannot modify Current Mind.",
            "Any geometry candidate must be specified before evaluation on chronological holdout or prospective data.",
        ],
    }
