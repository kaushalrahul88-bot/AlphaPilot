from __future__ import annotations

from collections import Counter
from statistics import mean, median

from .commodity_time import parse_ist_timestamp
from .copper_market_brain_direction_audit import (
    DEFAULT_SAMPLE_EVERY_BARS,
    HORIZON_BARS,
    _same_session_future,
    _session_quality,
)
from .copper_research_brain import (
    _build_copper_snapshot_clean,
    _precompute_information_quality,
    brain_a_signal,
    brain_b_signal,
    clean_ohlcv,
)

MOVE_THRESHOLDS_PCT = (0.10, 0.20, 0.30)
PRIMARY_HORIZON_MINUTES = 60
PRIMARY_LARGE_MOVE_THRESHOLD_PCT = 0.30


def _pct(n: int, d: int) -> float:
    return round(float(n) / float(d) * 100.0, 2) if d else 0.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, float(q))) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def normalize_candle_rows(candles) -> list[list]:
    """Accept stored row candles or the deterministic frozen dict artifact format."""
    rows = []
    for candle in candles or []:
        if isinstance(candle, dict):
            timestamp = candle.get("timestamp") or candle.get("time") or candle.get("datetime")
            if timestamp is None:
                continue
            rows.append([
                timestamp,
                candle.get("open"),
                candle.get("high"),
                candle.get("low"),
                candle.get("close"),
                candle.get("volume", 0.0),
                candle.get("open_interest"),
            ])
        elif isinstance(candle, (list, tuple)):
            rows.append(list(candle))
    return rows


def _abstention_origin(brain: str, brain_a: str, signal: str) -> str:
    if signal != "NO_TRADE":
        return "NOT_AN_ABSTENTION"
    if brain == "A":
        return "BASELINE_ABSTENTION"
    if brain_a == "BUY":
        return "B_FILTERED_BUY"
    if brain_a == "SELL":
        return "B_FILTERED_SELL"
    return "BASELINE_ABSTENTION"


def _abstention_outcome(rows: list[list], index: int, horizon_minutes: int) -> dict | None:
    future = _same_session_future(rows, index, HORIZON_BARS[int(horizon_minutes)])
    if not future:
        return None
    entry = float(rows[index][4])
    if entry <= 0:
        return None
    final = float(future[-1][4])
    forward = (final / entry - 1.0) * 100.0
    up_excursion = max(0.0, (max(float(row[2]) for row in future) / entry - 1.0) * 100.0)
    down_excursion = max(0.0, (entry - min(float(row[3]) for row in future)) / entry * 100.0)
    max_excursion = max(up_excursion, down_excursion)
    if up_excursion > down_excursion:
        dominant_direction = "UP"
    elif down_excursion > up_excursion:
        dominant_direction = "DOWN"
    else:
        dominant_direction = "BALANCED"
    return {
        "forward_pct": forward,
        "absolute_forward_pct": abs(forward),
        "up_excursion_pct": up_excursion,
        "down_excursion_pct": down_excursion,
        "max_excursion_pct": max_excursion,
        "dominant_path_direction": dominant_direction,
    }


def _summary(observations: list[dict]) -> dict:
    if not observations:
        return {
            "no_trade_observations": 0,
            "avg_absolute_forward_pct": 0.0,
            "median_absolute_forward_pct": 0.0,
            "avg_max_excursion_pct": 0.0,
            "median_max_excursion_pct": 0.0,
            "p90_max_excursion_pct": 0.0,
            "low_path_motion_lt_0_10_pct": 0,
            "low_path_motion_lt_0_10_rate_pct": 0.0,
            "path_excursion_thresholds": {},
            "close_move_thresholds": {},
            "dominant_path_direction_counts": {},
        }

    absolute_forward = [float(row["absolute_forward_pct"]) for row in observations]
    max_excursion = [float(row["max_excursion_pct"]) for row in observations]
    path_thresholds = {}
    close_thresholds = {}
    for threshold in MOVE_THRESHOLDS_PCT:
        key = f"ge_{threshold:.2f}_pct".replace(".", "_")
        path_count = sum(1 for value in max_excursion if value >= threshold)
        close_count = sum(1 for value in absolute_forward if value >= threshold)
        path_thresholds[key] = {"count": path_count, "rate_pct": _pct(path_count, len(observations))}
        close_thresholds[key] = {"count": close_count, "rate_pct": _pct(close_count, len(observations))}
    low_motion = sum(1 for value in max_excursion if value < MOVE_THRESHOLDS_PCT[0])
    directions = Counter(str(row.get("dominant_path_direction") or "UNKNOWN") for row in observations)
    return {
        "no_trade_observations": len(observations),
        "avg_absolute_forward_pct": round(mean(absolute_forward), 4),
        "median_absolute_forward_pct": round(median(absolute_forward), 4),
        "avg_max_excursion_pct": round(mean(max_excursion), 4),
        "median_max_excursion_pct": round(median(max_excursion), 4),
        "p90_max_excursion_pct": round(_percentile(max_excursion, 0.90), 4),
        "low_path_motion_lt_0_10_pct": low_motion,
        "low_path_motion_lt_0_10_rate_pct": _pct(low_motion, len(observations)),
        "path_excursion_thresholds": path_thresholds,
        "close_move_thresholds": close_thresholds,
        "dominant_path_direction_counts": dict(sorted(directions.items())),
    }


def evaluate_market_brain_abstention(candles, sample_every_bars=DEFAULT_SAMPLE_EVERY_BARS) -> dict:
    """Measure what the underlying did after frozen Brain A/B NO_TRADE decisions.

    This is intentionally not an option-P&L backtest. A post-abstention move is a learning case,
    not automatically a trading mistake: AlphaPilot did not possess the ex-post direction when it
    abstained. The audit first measures candidate misses; causal failure attribution is separate.
    """
    rows = clean_ohlcv(normalize_candle_rows(candles))
    step = max(1, int(sample_every_bars))
    quality = _session_quality(rows)
    information_quality = _precompute_information_quality(rows)
    reports = {
        "A": {minutes: [] for minutes in HORIZON_BARS},
        "B": {minutes: [] for minutes in HORIZON_BARS},
    }

    for index in range(50, len(rows), step):
        stamp = parse_ist_timestamp(rows[index][0])
        day_quality = quality.get(stamp.date()) or {}
        if not day_quality.get("primary_score_eligible"):
            continue
        features = _build_copper_snapshot_clean(rows, index, information_quality=information_quality)
        signal_a = brain_a_signal(features)
        signals = {"A": signal_a, "B": brain_b_signal(features)}
        for brain, signal in signals.items():
            if signal != "NO_TRADE":
                continue
            origin = _abstention_origin(brain, signal_a, signal)
            for horizon in HORIZON_BARS:
                outcome = _abstention_outcome(rows, index, horizon)
                if outcome is None:
                    continue
                reports[brain][horizon].append({
                    "timestamp": stamp.isoformat(),
                    "brain": brain,
                    "signal": signal,
                    "abstention_origin": origin,
                    "brain_a_signal": signal_a,
                    "structure": features.get("structure"),
                    "entry_reference_price": float(features.get("price")),
                    "horizon_minutes": horizon,
                    "return_15m_pct": features.get("return_15m_pct"),
                    "atr_pct": features.get("atr_pct"),
                    "relative_volume": features.get("relative_volume"),
                    "oi_change_15m_pct": features.get("oi_change_15m_pct"),
                    **outcome,
                })

    brain_reports = {}
    for brain, by_horizon in reports.items():
        brain_reports[brain] = {}
        for horizon, observations in by_horizon.items():
            by_origin = {}
            for origin in sorted({row["abstention_origin"] for row in observations}):
                by_origin[origin] = _summary([row for row in observations if row["abstention_origin"] == origin])
            incremental = [row for row in observations if row["abstention_origin"].startswith("B_FILTERED_")]
            brain_reports[brain][str(horizon)] = {
                **_summary(observations),
                "by_abstention_origin": by_origin,
                "incremental_brain_b_filter": _summary(incremental) if brain == "B" else None,
                "latest_observations": observations[-100:],
            }

    complete_days = sorted(row["date"] for row in quality.values() if row["primary_score_eligible"])
    excluded_partial_days = sorted(row["date"] for row in quality.values() if not row["primary_score_eligible"])
    primary = brain_reports["B"][str(PRIMARY_HORIZON_MINUTES)]
    severe_key = f"ge_{PRIMARY_LARGE_MOVE_THRESHOLD_PCT:.2f}_pct".replace(".", "_")
    severe = int((primary.get("path_excursion_thresholds") or {}).get(severe_key, {}).get("count") or 0)

    return {
        "mode": "COPPER_MARKET_BRAIN_ABSTENTION_AUDIT_V1",
        "research_only": True,
        "descriptive_only": True,
        "production_rules_changed": False,
        "strategy_rules_changed": False,
        "trade_instrument": "OPTIONS",
        "underlying_reference_role": "REFERENCE_ONLY",
        "futures_pnl_calculated": False,
        "synthetic_option_premium_used": False,
        "same_session_only": True,
        "candidate_brain": "B",
        "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "primary_large_move_threshold_pct": PRIMARY_LARGE_MOVE_THRESHOLD_PCT,
        "move_thresholds_pct": list(MOVE_THRESHOLDS_PCT),
        "sample_every_bars": step,
        "sample_interval_minutes": step * 5,
        "primary_score_days": complete_days,
        "excluded_partial_days": excluded_partial_days,
        "no_trade_observations": int(primary.get("no_trade_observations") or 0),
        "no_trade_followed_by_large_move": severe,
        "no_trade_opportunity_cost": {
            "type": "UNDERLYING_MAX_EXCURSION_PROXY_NOT_PNL",
            "avg_pct": primary.get("avg_max_excursion_pct"),
            "median_pct": primary.get("median_max_excursion_pct"),
            "p90_pct": primary.get("p90_max_excursion_pct"),
        },
        "gap_attribution_status": "NOT_RUN",
        "gap_counts": {},
        "brains": brain_reports,
        "interpretation": {
            "large_move_candidate": (
                "A NO_TRADE followed by >=0.30% same-session underlying excursion at the 60-minute horizon is a "
                "missed-move candidate for forensic review, not automatically a strategy error."
            ),
            "brain_b_incremental_filter": (
                "B_FILTERED_BUY/SELL isolates observations where frozen Brain A had direction but Brain B's "
                "participation/regime gate abstained."
            ),
            "opportunity_cost": (
                "Max underlying excursion is an opportunity-cost proxy only. It is not option premium return, "
                "realizable P&L, or evidence that the ex-post direction was knowable."
            ),
        },
        "guardrails": [
            "Features are constructed only from bars available at the abstention timestamp.",
            "Forward measurement never crosses into a later trading date.",
            "Sparse sessions are excluded exactly as in the frozen direction audit.",
            "0.10%, 0.20%, and 0.30% move bands are all reported; the audit does not tune a threshold from outcomes.",
            "NO_TRADE followed by a large move is a learning candidate, not automatically a mistake.",
            "Underlying futures motion is reference evidence only; futures P&L and synthetic option P&L are forbidden.",
            "Overlapping 15-minute observations are descriptive checkpoints, not independent trades.",
            "Failure-mode attribution and research-hypothesis generation are separate, outcome-aware forensic stages.",
        ],
    }
