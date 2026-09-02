from __future__ import annotations

from math import sqrt
from statistics import mean, median

from .commodity_time import parse_ist_timestamp

HORIZONS = (15, 30, 60, 120)
PRIMARY_HORIZON_MINUTES = 60
MIN_DIRECTION_ANALOGUES = 20
WILSON_Z = 1.96

FEATURES = (
    "return_15m_pct",
    "return_60m_pct",
    "ema20_gap_pct",
    "ema50_gap_pct",
    "atr_pct",
    "relative_volume",
    "time_adjusted_relative_volume",
    "session_return_pct",
    "session_range_position",
    "session_vwap_gap_pct",
    "opening_range_position",
)
CATEGORICAL = ("structure", "opening_range_break", "price_oi_state")


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _vector(snapshot: dict) -> dict:
    return {key: _f(snapshot.get(key)) for key in FEATURES}


def make_direction_case(
    *,
    snapshot: dict,
    click_timestamp: str,
    available_at: str,
    future_returns_pct: dict,
) -> dict:
    """Create one geometry-independent underlying-direction memory case.

    The case stores only market state and future underlying returns. It deliberately
    contains no BUY_CE/BUY_PE action, entry, stop, target, R multiple or target-first /
    stop-first label. `available_at` must represent the first timestamp at which every
    stored forward return was actually knowable.
    """
    click = parse_ist_timestamp(click_timestamp)
    available = parse_ist_timestamp(available_at)
    if available <= click:
        raise ValueError("Direction-memory outcome must become available after the source click")

    returns = {}
    for minutes in HORIZONS:
        value = _f((future_returns_pct or {}).get(str(minutes)))
        if value is not None:
            returns[str(minutes)] = value
    if not returns:
        raise ValueError("At least one forward underlying return is required")

    return {
        "mode": "CRUDE_OIL_MINI_DIRECTION_MEMORY_CASE_V1",
        "click_timestamp": click.isoformat(),
        "available_at": available.isoformat(),
        "vector": _vector(snapshot),
        "structure": snapshot.get("structure"),
        "opening_range_break": snapshot.get("opening_range_break"),
        "price_oi_state": snapshot.get("price_oi_state"),
        "future_returns_pct": returns,
        "geometry_independent": True,
    }


def _scales(cases: list[dict]) -> dict:
    out = {}
    for key in FEATURES:
        values = [
            _f((case.get("vector") or {}).get(key))
            for case in cases
            if _f((case.get("vector") or {}).get(key)) is not None
        ]
        if len(values) < 2:
            out[key] = 1.0
            continue
        avg = mean(values)
        variance = mean((value - avg) ** 2 for value in values)
        out[key] = max(sqrt(variance), 1e-9)
    return out


def _distance(query: dict, case: dict, scales: dict) -> float:
    parts = []
    case_vector = case.get("vector") or {}
    for key in FEATURES:
        a = _f(query["vector"].get(key))
        b = _f(case_vector.get(key))
        if a is None or b is None:
            continue
        parts.append(((a - b) / scales[key]) ** 2)
    for key in CATEGORICAL:
        a, b = query.get(key), case.get(key)
        if a and b and "UNKNOWN" not in {str(a).upper(), str(b).upper()}:
            parts.append(0.0 if a == b else 1.0)
    return sqrt(sum(parts) / len(parts)) if parts else 999.0


def _adaptive_k(prior_count: int) -> int:
    if prior_count <= 0:
        return 0
    return min(prior_count, max(MIN_DIRECTION_ANALOGUES, int(round(2.0 * sqrt(prior_count)))))


def _wilson_positive_interval(positive: int, total: int) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = positive / total
    z2 = WILSON_Z**2
    denom = 1.0 + z2 / total
    centre = (p + z2 / (2.0 * total)) / denom
    margin = WILSON_Z * sqrt((p * (1.0 - p) / total) + z2 / (4.0 * total**2)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _horizon_summary(cases: list[dict], minutes: int) -> dict:
    values = [
        value
        for case in cases
        if (value := _f((case.get("future_returns_pct") or {}).get(str(minutes)))) is not None
    ]
    positive = sum(value > 0 for value in values)
    negative = sum(value < 0 for value in values)
    flat = len(values) - positive - negative
    directional_n = positive + negative
    low, high = _wilson_positive_interval(positive, directional_n)
    stance = "UNKNOWN"
    if directional_n >= MIN_DIRECTION_ANALOGUES and low is not None and high is not None:
        if low > 0.5:
            stance = "BULLISH"
        elif high < 0.5:
            stance = "BEARISH"
    return {
        "observations": len(values),
        "directional_observations": directional_n,
        "positive": positive,
        "negative": negative,
        "flat": flat,
        "positive_pct_directional": round(positive / directional_n * 100.0, 2) if directional_n else None,
        "avg_return_pct": round(mean(values), 4) if values else None,
        "median_return_pct": round(median(values), 4) if values else None,
        "positive_wilson_95": [round(low, 4), round(high, 4)] if low is not None else None,
        "stance": stance,
    }


def query_direction_memory(cases: list[dict], snapshot: dict, click_timestamp: str) -> dict:
    """Retrieve only resolved prior market states and summarize future direction.

    Similarity is selected from market-state descriptors only. Forward-return signs are
    inspected only after analogue selection, so outcomes cannot influence neighbour
    choice. Cases becoming available at the current timestamp are withheld.
    """
    click = parse_ist_timestamp(click_timestamp)
    safe = []
    withheld = 0
    for case in cases or []:
        available_raw = case.get("available_at")
        if not available_raw:
            withheld += 1
            continue
        try:
            available = parse_ist_timestamp(available_raw)
        except Exception:
            withheld += 1
            continue
        if available < click:
            safe.append(case)
        else:
            withheld += 1

    if len(safe) < MIN_DIRECTION_ANALOGUES:
        return {
            "mode": "CRUDE_OIL_MINI_DIRECTION_MEMORY_V1",
            "status": "INSUFFICIENT_MEMORY",
            "prior_resolved_cases": len(safe),
            "withheld_cases": withheld,
            "minimum_cases": MIN_DIRECTION_ANALOGUES,
            "stance": "UNKNOWN",
            "geometry_independent": True,
        }

    query = {
        "vector": _vector(snapshot),
        "structure": snapshot.get("structure"),
        "opening_range_break": snapshot.get("opening_range_break"),
        "price_oi_state": snapshot.get("price_oi_state"),
    }
    scales = _scales(safe)
    k = _adaptive_k(len(safe))
    nearest = sorted(safe, key=lambda case: _distance(query, case, scales))[:k]
    horizons = {str(minutes): _horizon_summary(nearest, minutes) for minutes in HORIZONS}
    primary = horizons[str(PRIMARY_HORIZON_MINUTES)]
    primary_stance = primary["stance"]
    long_stance = horizons["120"]["stance"]
    if primary_stance in {"BULLISH", "BEARISH"} and long_stance == primary_stance:
        persistence = "PERSISTS_TO_120M"
    elif primary_stance in {"BULLISH", "BEARISH"} and long_stance in {"BULLISH", "BEARISH"}:
        persistence = "REVERSES_BY_120M"
    else:
        persistence = "UNRESOLVED"

    return {
        "mode": "CRUDE_OIL_MINI_DIRECTION_MEMORY_V1",
        "status": "READY",
        "prior_resolved_cases": len(safe),
        "withheld_cases": withheld,
        "analogues_used": len(nearest),
        "nearest_distance": round(_distance(query, nearest[0], scales), 4) if nearest else None,
        "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "horizons": horizons,
        "stance": primary_stance,
        "persistence": persistence,
        "geometry_independent": True,
        "selection_rule": (
            "Analogue selection uses only point-in-time market state. Direction is declared only when the "
            "95% Wilson interval for positive-return frequency excludes 50% with at least 20 directional analogues."
        ),
    }


def architecture_contract() -> dict:
    return {
        "mode": "CRUDE_OIL_MINI_DIRECTION_MEMORY_V1",
        "research_only": True,
        "shadow_only": True,
        "point_in_time": True,
        "same_timestamp_allowed": False,
        "geometry_independent": True,
        "trade_outcome_labels_used": False,
        "entry_stop_target_used": False,
        "option_pnl_used": False,
        "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "secondary_horizons_minutes": [15, 30, 120],
    }
