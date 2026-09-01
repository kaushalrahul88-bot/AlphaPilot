from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median


REGIME_DIMENSIONS = (
    "trend_structure",
    "volatility_regime",
    "location",
    "participation",
    "opening_behavior",
)


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(numerator: int, denominator: int) -> float:
    return round(float(numerator) / float(denominator) * 100.0, 2) if denominator else 0.0


def _audit_row(decision: dict) -> dict | None:
    if str(decision.get("action") or "") != "WAIT":
        return None
    outcome = decision.get("outcome") or {}
    if outcome.get("result") != "WAIT":
        return None
    up = _f(outcome.get("max_up_pct"))
    down = _f(outcome.get("max_down_pct"))
    threshold = _f(outcome.get("large_move_threshold_pct"))
    if up is None or down is None:
        return None
    max_move = max(up, down)
    if up > down:
        direction = "UP"
    elif down > up:
        direction = "DOWN"
    else:
        direction = "BALANCED"
    large = bool(outcome.get("future_move_without_setup")) if threshold is not None else None
    regime = decision.get("regime") or {}
    evidence = decision.get("evidence") or {}
    return {
        "session": decision.get("session"),
        "click_timestamp": decision.get("click_timestamp"),
        "max_up_pct": round(up, 4),
        "max_down_pct": round(down, 4),
        "max_move_pct": round(max_move, 4),
        "large_move_threshold_pct": round(threshold, 4) if threshold is not None else None,
        "move_to_threshold_multiple": round(max_move / threshold, 3) if threshold and threshold > 0 else None,
        "future_move_without_setup": large,
        "dominant_path_direction": direction,
        "regime": {key: regime.get(key, "UNKNOWN") for key in REGIME_DIMENSIONS},
        "evidence_quality": evidence.get("quality") or evidence.get("evidence_quality") or "UNKNOWN",
        "decision_reason": (decision.get("decision") or {}).get("reason"),
    }


def _summary(rows: list[dict]) -> dict:
    if not rows:
        return {
            "waits": 0,
            "large_move_candidates": 0,
            "large_move_rate_pct": 0.0,
            "avg_max_move_pct": None,
            "median_max_move_pct": None,
            "avg_move_to_threshold_multiple": None,
            "dominant_path_direction_counts": {},
        }
    large = [row for row in rows if row.get("future_move_without_setup") is True]
    multiples = [
        float(row["move_to_threshold_multiple"])
        for row in rows
        if row.get("move_to_threshold_multiple") is not None
    ]
    max_moves = [float(row["max_move_pct"]) for row in rows]
    return {
        "waits": len(rows),
        "large_move_candidates": len(large),
        "large_move_rate_pct": _pct(len(large), len(rows)),
        "avg_max_move_pct": round(mean(max_moves), 4),
        "median_max_move_pct": round(median(max_moves), 4),
        "avg_move_to_threshold_multiple": round(mean(multiples), 3) if multiples else None,
        "dominant_path_direction_counts": dict(Counter(row["dominant_path_direction"] for row in rows)),
    }


def evaluate_abstention_audit(replay_report: dict) -> dict:
    """Forensically measure what happened after frozen Crude Current Mind WAITs.

    A large move after WAIT is a learning candidate, not proof that the original
    abstention was wrong. The replay's causal decision is never recomputed here.
    """
    observations = [
        row for row in (_audit_row(decision) for decision in replay_report.get("decisions", []))
        if row is not None
    ]
    by_regime = {}
    for dimension in REGIME_DIMENSIONS:
        groups = defaultdict(list)
        for row in observations:
            groups[str((row.get("regime") or {}).get(dimension) or "UNKNOWN")].append(row)
        by_regime[dimension] = {
            value: _summary(group)
            for value, group in sorted(groups.items())
        }

    large_moves = [row for row in observations if row.get("future_move_without_setup") is True]
    return {
        "mode": "CRUDE_OIL_MINI_CURRENT_MIND_ABSTENTION_AUDIT_V1",
        "product": "CRUDE_OIL_MINI",
        "research_only": True,
        "descriptive_only": True,
        "production_rules_changed": False,
        "strategy_rules_changed": False,
        "news_used": False,
        "option_market_data_used": False,
        "futures_pnl_calculated": False,
        "synthetic_option_premium_used": False,
        "large_move_definition": "Per-click frozen replay threshold = 2.0 x contemporaneous CRUDEOILM ATR percent.",
        "overall": _summary(observations),
        "by_regime": by_regime,
        "large_move_candidates": large_moves,
        "observations": observations,
        "interpretation": {
            "large_move_candidate": "WAIT followed by a move exceeding the pre-existing ATR-scaled replay threshold is a forensic learning case, not automatically a strategy error.",
            "direction_unknown_at_click": "UP/DOWN labels are attached only after the frozen WAIT and are never fed back into that decision.",
            "threshold_role": "The threshold scales opportunity cost by contemporaneous Crude volatility; it is not a fixed Copper move threshold or option-P&L target.",
        },
        "guardrails": [
            "This audit consumes already-frozen Current Mind decisions and never recomputes them with future information.",
            "Post-WAIT path direction and excursion are outcome annotations only.",
            "No outcome-derived filter or playbook is promoted from this audit.",
            "No Copper market data, news, option premium or synthetic option P&L is used.",
        ],
    }
