from __future__ import annotations

from collections import defaultdict
from statistics import mean, median

from .commodity_time import parse_ist_timestamp
from .crude_directional_asymmetry_candidate import long_only_shadow_signal
from .crude_research_brain import _f, brain_a_signal, build_crude_experiences, clean_ohlcv


def _score(experiences, signal_fn, round_trip_cost_bps=4.0):
    cost = max(0.0, float(round_trip_cost_bps)) / 100.0
    rows = []
    for item in experiences:
        features = item.get("features") or {}
        forward = _f((item.get("labels") or {}).get("forward_60m_pct"))
        signal = signal_fn(features)
        if signal == "NO_TRADE" or forward is None:
            continue
        gross = forward if signal == "BUY" else -forward
        rows.append({
            "available_at": features.get("available_at"),
            "signal": signal,
            "net_pct": gross - cost,
        })
    values = [x["net_pct"] for x in rows]
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {
        "signals": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(rows), 2) if rows else 0.0,
        "avg_net_return_pct": round(mean(values), 4) if values else 0.0,
        "net_return_sum_pct": round(sum(values), 4),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
    }


def run_long_only_day_stability(candles, *, trading_symbol, sample_every_bars=3, round_trip_cost_bps=4.0):
    rows = clean_ohlcv(candles)
    if len(rows) < 80:
        raise RuntimeError(f"Insufficient exact-contract MCX Crude 5m history ({len(rows)} candles)")
    experiences = build_crude_experiences(rows, sample_every_bars=sample_every_bars)
    by_day = defaultdict(list)
    for item in experiences:
        available_at = (item.get("features") or {}).get("available_at")
        if not available_at:
            continue
        day = parse_ist_timestamp(available_at).date().isoformat()
        by_day[day].append(item)

    sessions = []
    for day in sorted(by_day):
        sample = by_day[day]
        baseline = _score(sample, brain_a_signal, round_trip_cost_bps)
        candidate = _score(sample, long_only_shadow_signal, round_trip_cost_bps)
        sessions.append({
            "date": day,
            "experiences": len(sample),
            "baseline_brain_a": baseline,
            "long_only_shadow": candidate,
            "candidate_positive": candidate["net_return_sum_pct"] > 0,
            "candidate_outperformed_baseline": candidate["net_return_sum_pct"] > baseline["net_return_sum_pct"],
        })

    candidate_daily = [s["long_only_shadow"]["net_return_sum_pct"] for s in sessions]
    baseline_daily = [s["baseline_brain_a"]["net_return_sum_pct"] for s in sessions]
    positive = [x for x in candidate_daily if x > 0]
    total_positive = sum(positive)
    largest_positive_share = max(positive) / total_positive if positive and total_positive > 0 else None
    return {
        "mode": "ALPHAPILOT_CRUDE_LONG_ONLY_DAY_STABILITY_V1",
        "commodity": "CRUDEOIL",
        "trading_symbol": str(trading_symbol),
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "news_enabled": False,
        "candidate_frozen_before_this_audit": True,
        "candidate_rule_changed": False,
        "coverage": {
            "mcx_5m_candles": len(rows),
            "experiences": len(experiences),
            "sessions": len(sessions),
            "start_bar": rows[0][0].isoformat(),
            "end_bar": rows[-1][0].isoformat(),
        },
        "aggregate": {
            "baseline_brain_a": _score(experiences, brain_a_signal, round_trip_cost_bps),
            "long_only_shadow": _score(experiences, long_only_shadow_signal, round_trip_cost_bps),
        },
        "stability": {
            "candidate_positive_sessions": len(positive),
            "candidate_positive_session_pct": round(100.0 * len(positive) / len(sessions), 1) if sessions else 0.0,
            "candidate_outperformed_baseline_sessions": sum(s["candidate_outperformed_baseline"] for s in sessions),
            "candidate_outperformed_baseline_session_pct": round(100.0 * sum(s["candidate_outperformed_baseline"] for s in sessions) / len(sessions), 1) if sessions else 0.0,
            "candidate_mean_daily_net_sum_pct": round(mean(candidate_daily), 4) if candidate_daily else 0.0,
            "candidate_median_daily_net_sum_pct": round(median(candidate_daily), 4) if candidate_daily else 0.0,
            "baseline_mean_daily_net_sum_pct": round(mean(baseline_daily), 4) if baseline_daily else 0.0,
            "largest_positive_day_share": round(largest_positive_share, 4) if largest_positive_share is not None else None,
        },
        "sessions": sessions,
        "interpretation_policy": [
            "This is a stability audit of an already-frozen candidate, not a tuning surface.",
            "No session result may be used to alter the long-only rule inside this audit.",
            "News remains forbidden.",
            "The exact currently listed contract history is not described as a continuous front-month series.",
        ],
        "next_gate": "If stability is not concentrated in only a few sessions, proceed to deterministic random-click replay using the same frozen no-news candidate.",
    }
