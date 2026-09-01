from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .commodity_time import parse_ist_timestamp

HORIZON_MINUTES = 60
MIN_STATE_OBSERVATIONS = 20
MIN_WINDOW_OBSERVATIONS = 8


def _signed_forward(row: dict) -> float | None:
    raw = ((row.get("future_returns_pct") or {}).get(str(HORIZON_MINUTES)))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    action = row.get("action")
    if action == "BUY_CE":
        return value
    if action == "BUY_PE":
        return -value
    return None


def _memory_stance(row: dict) -> str:
    lanes = ((row.get("evidence") or {}).get("lanes") or {}).get("EXPERIENCE") or []
    if not lanes:
        return "UNKNOWN"
    return str(lanes[-1].get("stance") or "UNKNOWN").upper()


def _dimension(row: dict, name: str) -> str:
    features = row.get("features") or {}
    regime = ((row.get("regime") or {}).get("observations") or {})
    decision = row.get("decision") or {}
    click = parse_ist_timestamp(row["click_timestamp"])
    if name == "action":
        return str(row.get("action") or "UNKNOWN")
    if name == "session":
        return "MORNING" if click.hour < 12 else "MIDDAY" if click.hour < 16 else "EVENING"
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
    if name == "playbook":
        return str(decision.get("playbook") or "UNKNOWN")
    if name == "memory_stance":
        return _memory_stance(row)
    return "UNKNOWN"


def _stats(rows: list[dict]) -> dict:
    signed = [value for row in rows if (value := _signed_forward(row)) is not None]
    resolved = [
        row for row in rows
        if (row.get("outcome") or {}).get("result") in {"TARGET", "STOP"}
    ]
    realized = [float((row.get("outcome") or {}).get("realized_r") or 0.0) for row in resolved]
    if not signed:
        return {
            "observations": 0,
            "direction_accuracy_pct": 0.0,
            "avg_signed_forward_pct": 0.0,
            "resolved_setups": len(resolved),
            "avg_realized_r_resolved": round(mean(realized), 4) if realized else None,
        }
    return {
        "observations": len(signed),
        "direction_accuracy_pct": round(sum(value > 0 for value in signed) / len(signed) * 100.0, 2),
        "avg_signed_forward_pct": round(mean(signed), 4),
        "resolved_setups": len(resolved),
        "avg_realized_r_resolved": round(mean(realized), 4) if realized else None,
    }


def _window_ids(rows: list[dict], windows: int = 3) -> dict[str, int]:
    ordered = sorted(rows, key=lambda row: parse_ist_timestamp(row["click_timestamp"]))
    n = len(ordered)
    return {
        row["click_timestamp"]: min(windows - 1, int(index * windows / max(1, n)))
        for index, row in enumerate(ordered)
    }


def evaluate_error_attribution(baseline_report: dict) -> dict:
    trades = [
        row for row in (baseline_report.get("decisions") or [])
        if row.get("action") in {"BUY_CE", "BUY_PE"} and _signed_forward(row) is not None
    ]
    dimensions = [
        "action", "session", "structure", "volatility_regime", "location",
        "participation", "opening_behavior", "playbook", "memory_stance",
    ]
    window_map = _window_ids(trades, 3)
    reports = {}
    stable_good = []
    stable_bad = []
    for dimension in dimensions:
        groups = defaultdict(list)
        for row in trades:
            groups[_dimension(row, dimension)].append(row)
        dimension_report = {}
        for state, group in sorted(groups.items()):
            overall = _stats(group)
            if overall["observations"] < MIN_STATE_OBSERVATIONS:
                continue
            windows = []
            for window in range(3):
                subset = [row for row in group if window_map.get(row["click_timestamp"]) == window]
                windows.append({"window": window + 1, **_stats(subset)})
            enough = all(item["observations"] >= MIN_WINDOW_OBSERVATIONS for item in windows)
            all_good = enough and all(item["direction_accuracy_pct"] > 50.0 for item in windows)
            all_bad = enough and all(item["direction_accuracy_pct"] < 50.0 for item in windows)
            payload = {
                **overall,
                "windows": windows,
                "stable_above_50_pct": all_good,
                "stable_below_50_pct": all_bad,
            }
            dimension_report[state] = payload
            candidate = {"dimension": dimension, "state": state, **payload}
            if all_good:
                stable_good.append(candidate)
            if all_bad:
                stable_bad.append(candidate)
        reports[dimension] = dimension_report

    return {
        "mode": "CRUDE_OIL_MINI_CURRENT_MIND_ERROR_ATTRIBUTION_V1",
        "research_only": True,
        "descriptive_only": True,
        "strategy_rules_changed": False,
        "news_used": False,
        "option_market_data_used": False,
        "horizon_minutes": HORIZON_MINUTES,
        "reference_contract": baseline_report.get("reference_contract"),
        "clicks": baseline_report.get("evaluated_clicks"),
        "trade_observations": len(trades),
        "overall": _stats(trades),
        "dimensions": reports,
        "stable_above_50_pct_states": stable_good,
        "stable_below_50_pct_states": stable_bad,
        "guardrails": [
            "The frozen Crude Current Mind decisions are audited; the audit does not reroute or suppress a trade.",
            "Only pre-existing categorical states are grouped; no threshold is searched against outcomes.",
            "A stability label requires at least 20 observations overall and at least 8 in each of three chronological windows.",
            "Stable states are hypotheses for the next research step, never automatic strategy gates.",
            "Direction is evaluated on the underlying Mini tape only; option premium and synthetic P&L are absent.",
        ],
    }
