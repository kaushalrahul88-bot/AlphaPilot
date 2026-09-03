from __future__ import annotations

from statistics import median

from .commodity_time import parse_ist_timestamp

DIRECTIONAL = {"BULLISH", "BEARISH"}
BENCHMARKS = ("WTI_CRUDE", "BRENT_CRUDE")
SHORT_MOMENTUM_BARS = 3
MEDIUM_MOMENTUM_BARS = 6
CONTEXT_RANGE_BARS = 12
STRUCTURE_LEG_BARS = 3


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _direction(value) -> str:
    value = _f(value)
    if value is None or value == 0:
        return "UNKNOWN"
    return "BULLISH" if value > 0 else "BEARISH"


def _visible_rows(feed: dict | None, click_timestamp: str) -> list[dict]:
    click = parse_ist_timestamp(click_timestamp)
    rows = []
    for raw in (feed or {}).get("data") or []:
        try:
            available = parse_ist_timestamp(raw["available_at"])
            close = float(raw["close"])
            high = float(raw["high"])
            low = float(raw["low"])
            open_ = float(raw["open"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if available > click or min(open_, high, low, close) <= 0:
            continue
        row = dict(raw)
        row["_available"] = available
        row["_open"] = open_
        row["_high"] = high
        row["_low"] = low
        row["_close"] = close
        row["_volume"] = _f(raw.get("volume"))
        rows.append(row)
    rows.sort(key=lambda row: row["_available"])
    return rows


def _return_pct(rows: list[dict], bars_back: int):
    if len(rows) <= bars_back:
        return None
    latest = rows[-1]["_close"]
    earlier = rows[-1 - bars_back]["_close"]
    if earlier <= 0:
        return None
    return (latest / earlier - 1.0) * 100.0


def _swing_structure(rows: list[dict]) -> str:
    required = STRUCTURE_LEG_BARS * 2
    if len(rows) < required:
        return "UNKNOWN"
    prior = rows[-required:-STRUCTURE_LEG_BARS]
    recent = rows[-STRUCTURE_LEG_BARS:]
    prior_high = max(row["_high"] for row in prior)
    prior_low = min(row["_low"] for row in prior)
    recent_high = max(row["_high"] for row in recent)
    recent_low = min(row["_low"] for row in recent)
    if recent_high > prior_high and recent_low > prior_low:
        return "UPTREND"
    if recent_high < prior_high and recent_low < prior_low:
        return "DOWNTREND"
    return "MIXED"


def _true_ranges(rows: list[dict]) -> list[float]:
    out = []
    for index, row in enumerate(rows):
        previous_close = rows[index - 1]["_close"] if index else row["_open"]
        out.append(max(
            row["_high"] - row["_low"],
            abs(row["_high"] - previous_close),
            abs(row["_low"] - previous_close),
        ))
    return out


def _relative_state(latest, history: list, *, high_label: str, low_label: str) -> str:
    values = [value for value in history if value is not None]
    if latest is None or not values:
        return "UNAVAILABLE"
    benchmark = median(values)
    if latest > benchmark:
        return high_label
    if latest < benchmark:
        return low_label
    return "AT_RECENT_MEDIAN"


def _range_location(rows: list[dict]):
    if len(rows) < 2:
        return None
    sample = rows[-CONTEXT_RANGE_BARS:]
    high = max(row["_high"] for row in sample)
    low = min(row["_low"] for row in sample)
    if high <= low:
        return None
    return (rows[-1]["_close"] - low) / (high - low)


def _benchmark_state(series: str, feed: dict | None, click_timestamp: str) -> dict:
    rows = _visible_rows(feed, click_timestamp)
    if len(rows) < MEDIUM_MOMENTUM_BARS + 1:
        return {
            "series": series,
            "status": "INSUFFICIENT_COMPLETED_HISTORY",
            "stance": "UNKNOWN",
            "counts_for_global_direction": False,
            "completed_bars_visible": len(rows),
        }

    return_1h = _return_pct(rows, 1)
    return_short = _return_pct(rows, SHORT_MOMENTUM_BARS)
    return_medium = _return_pct(rows, MEDIUM_MOMENTUM_BARS)
    structure = _swing_structure(rows)
    structure_stance = (
        "BULLISH" if structure == "UPTREND"
        else "BEARISH" if structure == "DOWNTREND"
        else "UNKNOWN"
    )
    short_stance = _direction(return_short)
    medium_stance = _direction(return_medium)

    if (
        structure_stance in DIRECTIONAL
        and short_stance == structure_stance
        and medium_stance == structure_stance
    ):
        stance = structure_stance
        state = "STRUCTURE_AND_MULTI_HOUR_MOMENTUM_COHERENT"
    else:
        stance = "UNKNOWN"
        state = "MIXED_OR_UNCONFIRMED_GLOBAL_STRUCTURE"

    recent = rows[-(CONTEXT_RANGE_BARS + 1):]
    true_ranges = _true_ranges(recent)
    latest_tr = true_ranges[-1] if true_ranges else None
    prior_tr = true_ranges[:-1]
    latest_volume = rows[-1]["_volume"]
    prior_volume = [row["_volume"] for row in rows[-(MEDIUM_MOMENTUM_BARS + 1):-1]]

    return {
        "series": series,
        "status": "AVAILABLE",
        "source": (feed or {}).get("source"),
        "bar_minutes": (feed or {}).get("bar_minutes"),
        "latest_available_at": rows[-1]["available_at"],
        "latest_close": rows[-1]["_close"],
        "completed_bars_visible": len(rows),
        "stance": stance,
        "counts_for_global_direction": stance in DIRECTIONAL,
        "state": state,
        "structure": structure,
        "momentum": {
            "return_1h_pct": return_1h,
            f"return_{SHORT_MOMENTUM_BARS}h_pct": return_short,
            f"return_{MEDIUM_MOMENTUM_BARS}h_pct": return_medium,
            "one_hour_direction": _direction(return_1h),
            "short_direction": short_stance,
            "medium_direction": medium_stance,
        },
        "location": {
            f"trailing_{CONTEXT_RANGE_BARS}h_range_fraction": _range_location(rows),
        },
        "volatility": {
            "latest_true_range": latest_tr,
            "state_vs_recent_median": _relative_state(
                latest_tr,
                prior_tr,
                high_label="EXPANDING",
                low_label="CONTRACTING",
            ),
        },
        "participation": {
            "latest_volume": latest_volume,
            "state_vs_recent_median": _relative_state(
                latest_volume,
                prior_volume,
                high_label="ABOVE_RECENT_MEDIAN",
                low_label="BELOW_RECENT_MEDIAN",
            ),
        },
    }


def build_global_crude_perception(context_probe: dict, click_timestamp: str) -> dict:
    """Build a PIT-safe WTI/Brent state without reducing global crude to one 1h sign.

    This component is research-only and intentionally conservative: WTI and Brent must
    each show coherent structure plus multi-hour momentum, and both benchmarks must
    agree before GLOBAL_CRUDE is allowed to express a directional stance.
    """
    feeds = (context_probe or {}).get("feeds") or {}
    benchmarks = {
        series: _benchmark_state(series, feeds.get(series), click_timestamp)
        for series in BENCHMARKS
    }
    directional = {
        series: row["stance"]
        for series, row in benchmarks.items()
        if row.get("counts_for_global_direction") and row.get("stance") in DIRECTIONAL
    }

    if len(directional) == len(BENCHMARKS) and len(set(directional.values())) == 1:
        stance = next(iter(directional.values()))
        state = "WTI_BRENT_STRUCTURE_MOMENTUM_CONFIRMED"
        counts = True
    elif len(directional) == len(BENCHMARKS):
        stance = "UNKNOWN"
        state = "WTI_BRENT_DIRECTION_CONFLICT"
        counts = False
    elif directional:
        stance = "UNKNOWN"
        state = "PARTIAL_BENCHMARK_CONFIRMATION_ONLY"
        counts = False
    else:
        stance = "UNKNOWN"
        state = "NO_COHERENT_GLOBAL_CRUDE_DIRECTION"
        counts = False

    return {
        "family": "GLOBAL_CRUDE",
        "version": "CRUDE_OIL_MINI_GLOBAL_CRUDE_PERCEPTION_V2",
        "research_only": True,
        "shadow_only": True,
        "independent": True,
        "causal_origin": "CROSS_MARKET_CRUDE",
        "independence_status": "INDEPENDENT" if counts else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": counts,
        "stance": stance,
        "state": state,
        "click_timestamp": parse_ist_timestamp(click_timestamp).isoformat(),
        "benchmarks": benchmarks,
        "rules": [
            "Only completed benchmark bars available by the click are visible.",
            "WTI and Brent are one correlated GLOBAL_CRUDE family, never two votes.",
            "A single latest hourly sign cannot create a directional vote.",
            "Each benchmark requires coherent swing structure plus short and medium multi-hour momentum.",
            "Both WTI and Brent must independently satisfy the benchmark rule and agree before GLOBAL_CRUDE votes.",
            "Volume and volatility are descriptive context here; they do not independently create direction.",
            "No August outcome is used to choose or optimize thresholds.",
        ],
    }


def architecture_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_GLOBAL_CRUDE_PERCEPTION_V2_CONTRACT",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "direction_v2_effect_until_explicit_wiring": "NONE",
        "option_brain_effect": "NONE",
        "short_momentum_bars": SHORT_MOMENTUM_BARS,
        "medium_momentum_bars": MEDIUM_MOMENTUM_BARS,
        "context_range_bars": CONTEXT_RANGE_BARS,
        "structure_leg_bars": STRUCTURE_LEG_BARS,
        "wti_and_brent_are_one_family": True,
        "single_hour_sign_vote_allowed": False,
        "single_benchmark_vote_allowed": False,
        "threshold_search_on_inspected_august_allowed": False,
        "promotion_allowed": False,
    }
