from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .crude_research_brain import (
    _f,
    brain_a_signal,
    build_crude_experiences,
    chronological_split,
    clean_ohlcv,
)


def _bucket(value, cuts, labels):
    x = _f(value)
    if x is None:
        return "UNKNOWN"
    for cut, label in zip(cuts, labels):
        if x < cut:
            return label
    return labels[-1]


def _hour(value):
    try:
        from .commodity_time import parse_ist_timestamp
        return parse_ist_timestamp(value).hour
    except Exception:
        return None


def _stats(rows):
    values = [float(x["net_pct"]) for x in rows]
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    return {
        "signals": len(rows),
        "win_rate_pct": round(100.0 * len(wins) / len(rows), 2) if rows else 0.0,
        "avg_net_return_pct": round(mean(values), 4) if values else 0.0,
        "net_return_sum_pct": round(sum(values), 4),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
    }


def _observations(experiences, round_trip_cost_bps=4.0):
    observations = []
    cost = max(0.0, float(round_trip_cost_bps)) / 100.0
    for item in experiences:
        f = item.get("features") or {}
        forward = _f((item.get("labels") or {}).get("forward_60m_pct"))
        signal = brain_a_signal(f)
        if signal == "NO_TRADE" or forward is None:
            continue
        gross = forward if signal == "BUY" else -forward
        hour = _hour(f.get("available_at"))
        session_pos = _f(f.get("session_range_position"))
        vwap_gap = _f(f.get("session_vwap_gap_pct"))
        observations.append({
            "available_at": f.get("available_at"),
            "signal": signal,
            "net_pct": gross - cost,
            "structure": f.get("structure") or "UNKNOWN",
            "session": (
                "MORNING" if hour is not None and hour < 12 else
                "MIDDAY" if hour is not None and hour < 16 else
                "EVENING" if hour is not None else "UNKNOWN"
            ),
            "atr_bucket": _bucket(f.get("atr_pct"), [0.10, 0.20, 0.35], ["LOW", "NORMAL", "HIGH", "EXTREME"]),
            "volume_bucket": _bucket(f.get("relative_volume"), [0.75, 1.0, 1.5], ["QUIET", "NORMAL", "ACTIVE", "SURGE"]),
            "momentum_bucket": _bucket(abs(_f(f.get("return_15m_pct"), 0.0)), [0.03, 0.08, 0.15], ["WEAK", "NORMAL", "STRONG", "EXTREME"]),
            "session_location_bucket": (
                "UNKNOWN" if session_pos is None else
                "LOWER_QUARTER" if session_pos < 0.25 else
                "LOWER_MIDDLE" if session_pos < 0.50 else
                "UPPER_MIDDLE" if session_pos < 0.75 else
                "UPPER_QUARTER"
            ),
            "vwap_location_bucket": (
                "UNKNOWN" if vwap_gap is None else
                "BELOW_FAR" if vwap_gap < -0.15 else
                "BELOW_NEAR" if vwap_gap < 0 else
                "ABOVE_NEAR" if vwap_gap < 0.15 else
                "ABOVE_FAR"
            ),
            "price_oi_state": f.get("price_oi_state") or "UNKNOWN",
        })
    return observations


def _group(observations, key):
    grouped = defaultdict(list)
    for row in observations:
        grouped[str(row.get(key) or "UNKNOWN")].append(row)
    return {
        label: _stats(rows)
        for label, rows in sorted(grouped.items())
    }


def run_crude_edge_attribution(candles, *, trading_symbol, sample_every_bars=3, round_trip_cost_bps=4.0):
    """Descriptive attribution on development data only; never a promotion gate."""
    rows = clean_ohlcv(candles)
    if len(rows) < 80:
        raise RuntimeError(f"Insufficient exact-contract MCX Crude 5m history ({len(rows)} candles)")
    experiences = build_crude_experiences(rows, sample_every_bars=sample_every_bars)
    development, reserved = chronological_split(experiences, 0.70)
    observations = _observations(development, round_trip_cost_bps)
    dimensions = [
        "signal",
        "structure",
        "session",
        "atr_bucket",
        "volume_bucket",
        "momentum_bucket",
        "session_location_bucket",
        "vwap_location_bucket",
        "price_oi_state",
    ]
    return {
        "mode": "ALPHAPILOT_CRUDE_EDGE_ATTRIBUTION_V1",
        "commodity": "CRUDEOIL",
        "trading_symbol": str(trading_symbol),
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "news_enabled": False,
        "source_brain": "FROZEN_BRAIN_A",
        "brain_b_promoted": False,
        "development_only": True,
        "current_reserved_partition_used_for_attribution": False,
        "coverage": {
            "mcx_5m_candles": len(rows),
            "all_experiences": len(experiences),
            "development_experiences": len(development),
            "reserved_experiences": len(reserved),
            "attribution_signals": len(observations),
        },
        "overall_development": _stats(observations),
        "dimensions": {key: _group(observations, key) for key in dimensions},
        "interpretation_policy": [
            "This artifact is descriptive only and cannot promote or retune a strategy.",
            "Small segments must not be treated as evidence of edge.",
            "Any candidate inspired by this audit must be frozen before evaluation on a different untouched period.",
            "News remains forbidden; the historical news modules are not imported here.",
        ],
        "next_gate": "Use attribution only to define a preregistered no-news candidate or avoidance hypothesis, then test it on a different untouched historical period before any promotion.",
    }
