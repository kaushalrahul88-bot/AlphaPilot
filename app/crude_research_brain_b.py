from __future__ import annotations

from statistics import mean

from .crude_research_brain import (
    _f,
    brain_a_signal,
    build_crude_experiences,
    chronological_split,
    clean_ohlcv,
    evaluate_brain_a,
)

# Intentionally inherited from the already-frozen Copper Brain-B starting candidate.
# These thresholds were not chosen from Crude Experiment-001 outcomes.
BRAIN_B_CONFIG = {
    "min_relative_volume": 0.90,
    "max_atr_pct": 0.65,
    "min_abs_return_15m_pct": 0.02,
    "oi_confirmation": True,
}


def brain_b_signal(features, config=None):
    """No-news Crude Brain B: Brain A direction gated by participation/regime."""
    cfg = dict(BRAIN_B_CONFIG)
    if config:
        cfg.update(config)
    base = brain_a_signal(features)
    if base == "NO_TRADE":
        return "NO_TRADE"

    relative_volume = _f(features.get("relative_volume"))
    atr_pct = _f(features.get("atr_pct"))
    ret15 = _f(features.get("return_15m_pct"), 0.0)
    oi_change = _f(features.get("oi_change_15m_pct"))

    if relative_volume is not None and relative_volume < cfg["min_relative_volume"]:
        return "NO_TRADE"
    if atr_pct is not None and atr_pct > cfg["max_atr_pct"]:
        return "NO_TRADE"
    if abs(ret15) < cfg["min_abs_return_15m_pct"]:
        return "NO_TRADE"
    if cfg.get("oi_confirmation") and oi_change is not None and oi_change <= 0:
        return "NO_TRADE"
    return base


def _evaluate(experiences, signal_fn, *, brain, name, horizon_minutes=60, round_trip_cost_bps=4.0):
    key = f"forward_{int(horizon_minutes)}m_pct"
    decisions = []
    for item in experiences:
        features = item.get("features") or {}
        forward = _f((item.get("labels") or {}).get(key))
        signal = signal_fn(features)
        if signal == "NO_TRADE" or forward is None:
            continue
        gross = forward if signal == "BUY" else -forward
        net = gross - max(0.0, float(round_trip_cost_bps)) / 100.0
        decisions.append({
            "available_at": features.get("available_at"),
            "signal": signal,
            "gross_pct": gross,
            "net_pct": net,
        })
    values = [x["net_pct"] for x in decisions]
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    return {
        "brain": brain,
        "name": name,
        "research_only": True,
        "news_enabled": False,
        "horizon_minutes": int(horizon_minutes),
        "round_trip_cost_bps": float(round_trip_cost_bps),
        "signals": len(decisions),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(decisions) * 100.0, 2) if decisions else 0.0,
        "avg_net_return_pct": round(mean(values), 4) if values else 0.0,
        "net_return_sum_pct": round(sum(values), 4),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "decisions": decisions[-250:],
    }


def evaluate_brain_b(experiences, horizon_minutes=60, round_trip_cost_bps=4.0, config=None):
    cfg = dict(BRAIN_B_CONFIG)
    if config:
        cfg.update(config)
    result = _evaluate(
        experiences,
        lambda features: brain_b_signal(features, cfg),
        brain="B",
        name="CRUDE_MCX_STRUCTURE_PARTICIPATION_REGIME",
        horizon_minutes=horizon_minutes,
        round_trip_cost_bps=round_trip_cost_bps,
    )
    result["config"] = cfg
    result["config_provenance"] = "COPPER_BRAIN_B_STARTING_CONFIG; NOT_TUNED_ON_CRUDE_OUTCOMES"
    return result


def compare_crude_brains_a_b(candles, *, trading_symbol, sample_every_bars=3, round_trip_cost_bps=4.0):
    rows = clean_ohlcv(candles)
    if len(rows) < 80:
        raise RuntimeError(f"Insufficient exact-contract MCX Crude 5m history ({len(rows)} candles)")
    experiences = build_crude_experiences(rows, sample_every_bars=sample_every_bars)
    train, holdout = chronological_split(experiences, 0.70)

    a_train = evaluate_brain_a(train, 60, round_trip_cost_bps)
    b_train = evaluate_brain_b(train, 60, round_trip_cost_bps)
    a_holdout = evaluate_brain_a(holdout, 60, round_trip_cost_bps)
    b_holdout = evaluate_brain_b(holdout, 60, round_trip_cost_bps)

    a_pf = _f(a_holdout.get("profit_factor"), 0.0) or 0.0
    b_pf = _f(b_holdout.get("profit_factor"), 0.0) or 0.0
    promoted = (
        b_holdout["signals"] >= 20
        and b_holdout["avg_net_return_pct"] > a_holdout["avg_net_return_pct"]
        and b_pf > a_pf
        and b_holdout["avg_net_return_pct"] > 0
        and b_pf > 1.0
    )
    return {
        "mode": "ALPHAPILOT_CRUDE_EXPERIMENT_002",
        "commodity": "CRUDEOIL",
        "trading_symbol": str(trading_symbol),
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "news_enabled": False,
        "baseline_frozen_before_brain_b": True,
        "brain_b_config_selected_from_crude_outcomes": False,
        "coverage": {
            "mcx_5m_candles": len(rows),
            "experiences": len(experiences),
            "start_bar": rows[0][0].isoformat(),
            "end_bar": rows[-1][0].isoformat(),
        },
        "split": {
            "train_fraction": 0.70,
            "train_experiences": len(train),
            "holdout_experiences": len(holdout),
        },
        "train": {"brain_a": a_train, "brain_b": b_train},
        "holdout": {"brain_a": a_holdout, "brain_b": b_holdout},
        "gate": {
            "brain_b_promoted": promoted,
            "requirements": [
                "holdout signals >= 20",
                "holdout expectancy > frozen Brain A",
                "holdout profit factor > frozen Brain A",
                "positive holdout expectancy",
                "holdout profit factor > 1.0",
            ],
        },
        "next_gate": (
            "Proceed to no-news edge attribution/stability with Brain B frozen."
            if promoted else
            "Do not promote Brain B; proceed with attribution diagnostics without retuning on this holdout."
        ),
    }
