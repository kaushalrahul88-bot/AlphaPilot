from __future__ import annotations

from collections import Counter
from functools import lru_cache
from heapq import nsmallest
from math import sqrt
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_market_perception import bar_visible_at

FEATURES = (
    "return_15m_pct", "return_60m_pct", "ema20_gap_pct", "ema50_gap_pct", "atr_pct",
    "relative_volume", "time_adjusted_relative_volume", "session_return_pct",
    "session_range_position", "session_vwap_gap_pct", "opening_range_position",
)
CATEGORICAL = ("structure", "opening_range_break", "price_oi_state")


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _vector(snapshot: dict) -> dict:
    return {key: _f(snapshot.get(key)) for key in FEATURES}


def _scales(experiences: list[dict]) -> dict:
    scales = {}
    for key in FEATURES:
        values = [e["vector"].get(key) for e in experiences if e["vector"].get(key) is not None]
        if len(values) < 2:
            scales[key] = 1.0
            continue
        avg = mean(values)
        variance = mean((value - avg) ** 2 for value in values)
        scales[key] = max(sqrt(variance), 1e-9)
    return scales


def _distance(query: dict, candidate: dict, scales: dict) -> float:
    parts = []
    for key in FEATURES:
        a, b = query["vector"].get(key), candidate["vector"].get(key)
        if a is None or b is None:
            continue
        parts.append(((a - b) / scales[key]) ** 2)
    for key in CATEGORICAL:
        a, b = query.get(key), candidate.get(key)
        if a and b and "UNKNOWN" not in {str(a), str(b)}:
            parts.append(0.0 if a == b else 1.0)
    return sqrt(sum(parts) / len(parts)) if parts else 999.0


@lru_cache(maxsize=32768)
def _parsed_resolved_at(value: str):
    """Cache immutable historical outcome timestamps across repeated PIT queries."""
    return parse_ist_timestamp(value)


def _stable_nearest(experiences: list[dict], query: dict, scales: dict, k: int) -> list[dict]:
    """Return the same stable top-k as sorted(..., key=distance)[:k], with less work.

    The original full sort is stable: equal-distance candidates retain their source
    order. Including the source index as the second key preserves that exact tie
    behavior while heap-based selection avoids sorting the full causal memory pool.
    This is a runtime optimization only; distances, k, candidates and ordering are
    unchanged.
    """
    if k <= 0:
        return []
    indexed = enumerate(experiences)
    selected = nsmallest(
        k,
        indexed,
        key=lambda item: (_distance(query, item[1], scales), item[0]),
    )
    return [item[1] for item in selected]


def _event_path(day_rows: list[list], start_index: int, direction: str, atr_points: float) -> dict | None:
    """Resolve a historical counterfactual with Crude's contemporaneous ATR as scale.

    The stop is one contemporaneous ATR and the target is 1.5R. This is the shared
    AlphaPilot minimum reward/risk discipline, not a Copper price threshold. A same-bar
    target/stop collision resolves conservatively to stop. Outcome availability is the
    completion time of the bar that proved the event.
    """
    if atr_points is None or atr_points <= 0 or start_index >= len(day_rows) - 1:
        return None
    entry = float(day_rows[start_index][4])
    risk = float(atr_points)
    bullish = direction == "BULLISH"
    target = entry + 1.5 * risk if bullish else entry - 1.5 * risk
    stop = entry - risk if bullish else entry + risk
    mfe = mae = 0.0
    for row in day_rows[start_index + 1:]:
        high, low = float(row[2]), float(row[3])
        if bullish:
            mfe = max(mfe, (high - entry) / entry * 100.0)
            mae = max(mae, (entry - low) / entry * 100.0)
            hit_target, hit_stop = high >= target, low <= stop
        else:
            mfe = max(mfe, (entry - low) / entry * 100.0)
            mae = max(mae, (high - entry) / entry * 100.0)
            hit_target, hit_stop = low <= target, high >= stop
        if hit_target and hit_stop:
            return {"outcome": "STOP_FIRST", "resolved_at": bar_visible_at(row).isoformat(), "mfe_pct": mfe, "mae_pct": mae, "same_bar_ambiguous": True}
        if hit_stop:
            return {"outcome": "STOP_FIRST", "resolved_at": bar_visible_at(row).isoformat(), "mfe_pct": mfe, "mae_pct": mae}
        if hit_target:
            return {"outcome": "TARGET_FIRST", "resolved_at": bar_visible_at(row).isoformat(), "mfe_pct": mfe, "mae_pct": mae}
    return {
        "outcome": "SESSION_END_NO_EVENT",
        "resolved_at": bar_visible_at(day_rows[-1]).isoformat(),
        "mfe_pct": mfe,
        "mae_pct": mae,
    }


def build_experiences(rows: list[list], features: list[dict], complete_days: set[str], sample_every_bars: int = 3) -> list[dict]:
    by_day: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        day = parse_ist_timestamp(row[0]).date().isoformat()
        by_day.setdefault(day, []).append(i)

    experiences = []
    for day in sorted(complete_days):
        indices = by_day.get(day, [])
        if len(indices) < 40:
            continue
        day_rows = [rows[i] for i in indices]
        local_by_global = {global_index: local_index for local_index, global_index in enumerate(indices)}
        for global_index in indices[24:-12:max(1, int(sample_every_bars))]:
            snapshot = features[global_index]
            atr = _f(snapshot.get("atr_points"))
            if atr is None or atr <= 0:
                continue
            local_index = local_by_global[global_index]
            for direction in ("BULLISH", "BEARISH"):
                path = _event_path(day_rows, local_index, direction, atr)
                if not path:
                    continue
                experiences.append({
                    "timestamp": snapshot["timestamp"],
                    "resolved_at": path["resolved_at"],
                    "direction": direction,
                    "vector": _vector(snapshot),
                    "structure": snapshot.get("structure"),
                    "opening_range_break": snapshot.get("opening_range_break"),
                    "price_oi_state": snapshot.get("price_oi_state"),
                    "outcome": path["outcome"],
                    "mfe_pct": round(path["mfe_pct"], 4),
                    "mae_pct": round(path["mae_pct"], 4),
                })
    return experiences


def _adaptive_k(prior_count: int) -> int:
    """Choose analogue breadth from available Crude memory size, never from outcomes."""
    if prior_count <= 0:
        return 0
    return min(prior_count, max(20, int(round(2.0 * sqrt(prior_count)))))


def query_memory(experiences: list[dict], snapshot: dict, click_at) -> dict:
    click = parse_ist_timestamp(click_at)
    safe = [e for e in experiences if _parsed_resolved_at(str(e["resolved_at"])) < click]
    if len(safe) < 40:
        return {"status": "INSUFFICIENT_MEMORY", "prior_resolved_experiences": len(safe)}

    query = {
        "vector": _vector(snapshot),
        "structure": snapshot.get("structure"),
        "opening_range_break": snapshot.get("opening_range_break"),
        "price_oi_state": snapshot.get("price_oi_state"),
    }
    scales = _scales(safe)
    k = _adaptive_k(len(safe))
    nearest = _stable_nearest(safe, query, scales, k)
    by_direction = {}
    for direction in ("BULLISH", "BEARISH"):
        sample = [e for e in nearest if e["direction"] == direction]
        resolved = [e for e in sample if e["outcome"] != "SESSION_END_NO_EVENT"]
        wins = [e for e in resolved if e["outcome"] == "TARGET_FIRST"]
        p = len(wins) / len(resolved) if resolved else None
        by_direction[direction] = {
            "analogues": len(sample),
            "resolved": len(resolved),
            "target_first_pct_resolved": round(p * 100.0, 2) if p is not None else None,
            "avg_mfe_pct": round(mean(e["mfe_pct"] for e in sample), 4) if sample else None,
            "avg_mae_pct": round(mean(e["mae_pct"] for e in sample), 4) if sample else None,
            "outcomes": dict(Counter(e["outcome"] for e in sample)),
        }

    bull, bear = by_direction["BULLISH"], by_direction["BEARISH"]
    stance = "UNKNOWN"
    significance = None
    if bull["resolved"] >= 10 and bear["resolved"] >= 10:
        p1 = bull["target_first_pct_resolved"] / 100.0
        p2 = bear["target_first_pct_resolved"] / 100.0
        se = sqrt(max(1e-12, p1 * (1 - p1) / bull["resolved"] + p2 * (1 - p2) / bear["resolved"]))
        z = (p1 - p2) / se if se > 0 else 0.0
        significance = round(z, 3)
        if abs(z) >= 1.96:
            stance = "BULLISH" if z > 0 else "BEARISH"

    return {
        "status": "READY",
        "prior_resolved_experiences": len(safe),
        "analogues_used": len(nearest),
        "nearest_distance": round(_distance(query, nearest[0], scales), 4) if nearest else None,
        "by_direction": by_direction,
        "stance": stance,
        "direction_difference_z": significance,
        "selection_rule": "Adaptive analogue count depends only on prior memory size; directional stance requires two-sided z>=1.96.",
    }


def memory_evidence(experiences: list[dict], snapshot: dict, click_at) -> dict:
    result = query_memory(experiences, snapshot, click_at)
    return {
        "lane": "EXPERIENCE",
        "stance": result.get("stance", "UNKNOWN") if result.get("status") == "READY" else "UNKNOWN",
        "source": "crude_oil_mini_walk_forward_memory",
        "detail": result,
    }


def architecture_contract() -> dict:
    return {
        "mode": "CRUDE_OIL_MINI_EXPERIENCE_MEMORY_V1",
        "point_in_time": True,
        "outcome_availability_required": True,
        "future_current_outcome_visible": False,
        "copper_experiences_used": False,
        "news_used": False,
        "event_scale": "CONTEMPORANEOUS_CRUDEOILM_ATR",
        "analogue_count": "ADAPTIVE_FROM_PRIOR_MEMORY_SIZE_NOT_OUTCOMES",
    }
