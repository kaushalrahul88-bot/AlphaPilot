from __future__ import annotations

from statistics import mean

from .crude_research_brain import (
    _f,
    brain_a_signal,
    build_crude_experiences,
    clean_ohlcv,
    evaluate_brain_a,
)

CANDIDATE_ID = "CRUDE_LONG_ONLY_SHADOW_V1"
CANDIDATE_FROZEN_FROM = "DEVELOPMENT_ONLY_EDGE_ATTRIBUTION"


def long_only_shadow_signal(features):
    """Retain only existing Brain-A BUY signals; never create or reverse a trade."""
    base = brain_a_signal(features)
    return "BUY" if base == "BUY" else "NO_TRADE"


def _evaluate_candidate(experiences, horizon_minutes=60, round_trip_cost_bps=4.0):
    key = f"forward_{int(horizon_minutes)}m_pct"
    cost = max(0.0, float(round_trip_cost_bps)) / 100.0
    decisions = []
    for item in experiences:
        features = item.get("features") or {}
        forward = _f((item.get("labels") or {}).get(key))
        signal = long_only_shadow_signal(features)
        if signal == "NO_TRADE" or forward is None:
            continue
        net = forward - cost
        decisions.append({
            "available_at": features.get("available_at"),
            "signal": "BUY",
            "gross_pct": forward,
            "net_pct": net,
        })
    values = [x["net_pct"] for x in decisions]
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    return {
        "candidate_id": CANDIDATE_ID,
        "research_only": True,
        "news_enabled": False,
        "horizon_minutes": int(horizon_minutes),
        "round_trip_cost_bps": float(round_trip_cost_bps),
        "signals": len(decisions),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(decisions), 2) if decisions else 0.0,
        "avg_net_return_pct": round(mean(values), 4) if values else 0.0,
        "net_return_sum_pct": round(sum(values), 4),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "decisions": decisions[-250:],
    }


def validate_long_only_shadow(
    candles,
    *,
    trading_symbol,
    sample_every_bars=3,
    round_trip_cost_bps=4.0,
    validation_window=None,
):
    """Evaluate the preregistered long-only shadow on a separate historical window."""
    rows = clean_ohlcv(candles)
    if len(rows) < 80:
        raise RuntimeError(f"Insufficient exact-contract MCX Crude 5m history ({len(rows)} candles)")
    experiences = build_crude_experiences(rows, sample_every_bars=sample_every_bars)
    baseline = evaluate_brain_a(experiences, 60, round_trip_cost_bps)
    candidate = _evaluate_candidate(experiences, 60, round_trip_cost_bps)

    base_pf = _f(baseline.get("profit_factor"), 0.0) or 0.0
    candidate_pf = _f(candidate.get("profit_factor"), 0.0) or 0.0
    passes = (
        candidate["signals"] >= 20
        and candidate["avg_net_return_pct"] > baseline["avg_net_return_pct"]
        and candidate_pf > base_pf
        and candidate["avg_net_return_pct"] > 0
        and candidate_pf > 1.0
    )
    return {
        "mode": "ALPHAPILOT_CRUDE_EXPERIMENT_003",
        "commodity": "CRUDEOIL",
        "trading_symbol": str(trading_symbol),
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "news_enabled": False,
        "candidate": {
            "id": CANDIDATE_ID,
            "definition": "Retain frozen Brain-A BUY signals; convert every SELL/NO_TRADE to NO_TRADE.",
            "can_create_trades": False,
            "can_reverse_direction": False,
            "derived_from": CANDIDATE_FROZEN_FROM,
            "validation_outcomes_used_to_choose_rule": False,
        },
        "validation_window": validation_window,
        "validation_type": "INDEPENDENT_EARLIER_HISTORICAL_WINDOW_NOT_FORWARD",
        "coverage": {
            "mcx_5m_candles": len(rows),
            "experiences": len(experiences),
            "start_bar": rows[0][0].isoformat(),
            "end_bar": rows[-1][0].isoformat(),
        },
        "baseline_brain_a": baseline,
        "long_only_shadow": candidate,
        "gate": {
            "candidate_passed_independent_historical_validation": passes,
            "requirements": [
                "candidate signals >= 20",
                "candidate expectancy > frozen Brain A on same validation window",
                "candidate profit factor > frozen Brain A on same validation window",
                "candidate expectancy > 0",
                "candidate profit factor > 1.0",
            ],
        },
        "limitations": [
            "This is an independent earlier historical validation, not a forward test.",
            "The currently listed exact contract may expose extended history; this is not evidence of a continuous front-month series.",
            "The candidate was motivated by development-only attribution and is not eligible for promotion from the attribution window itself.",
            "News is explicitly excluded from both baseline and candidate decisions.",
        ],
        "next_gate": (
            "Candidate may proceed to broader no-news stability/day-by-day validation; do not add news yet."
            if passes else
            "Candidate fails; keep frozen Brain A and continue no-news diagnostics without retuning this validation window."
        ),
    }
