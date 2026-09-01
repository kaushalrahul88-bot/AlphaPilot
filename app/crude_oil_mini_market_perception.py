from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp

IST = ZoneInfo("Asia/Kolkata")
BAR_MINUTES = 5


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ts(value) -> datetime:
    return parse_ist_timestamp(value).astimezone(IST)


def clean_ohlcv(candles) -> list[list]:
    """Normalize Mini candles without inventing missing price, volume or OI data."""
    dedup: dict[datetime, list] = {}
    for row in candles or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = _ts(row[0])
            o, h, l, c = [float(row[i]) for i in range(1, 5)]
            volume = max(0.0, float(row[5] or 0.0)) if len(row) > 5 else 0.0
            oi = _f(row[6]) if len(row) > 6 else None
        except Exception:
            continue
        if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h):
            continue
        dedup[stamp] = [stamp.isoformat(), o, h, l, c, volume, oi]
    return [dedup[key] for key in sorted(dedup)]


def bar_visible_at(row) -> datetime:
    """Groww MCX candle timestamps are bar starts; OHLC is knowable after completion."""
    return _ts(row[0]) + timedelta(minutes=BAR_MINUTES)


def latest_visible_index(rows: list[list], click_at) -> int | None:
    click = _ts(click_at)
    lo, hi = 0, len(rows) - 1
    found = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if bar_visible_at(rows[mid]) <= click:
            found = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return found


def _ema(values: list[float], period: int) -> float | None:
    if not values:
        return None
    alpha = 2.0 / (period + 1.0)
    out = float(values[0])
    for value in values[1:]:
        out = alpha * float(value) + (1.0 - alpha) * out
    return out


def _atr_points(rows: list[list], period: int = 14) -> float | None:
    if len(rows) < 2:
        return None
    trs = []
    for i in range(1, len(rows)):
        high, low, previous_close = rows[i][2], rows[i][3], rows[i - 1][4]
        trs.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return mean(trs[-period:]) if trs else None


def _rolling_return(closes: list[float], bars: int) -> float | None:
    if len(closes) <= bars:
        return None
    base = closes[-bars - 1]
    return (closes[-1] / base - 1.0) * 100.0 if base > 0 else None


def _structure(session_rows: list[list], window: int = 24) -> str:
    sample = session_rows[-window:]
    if len(sample) < 12:
        return "UNKNOWN"
    half = len(sample) // 2
    older, recent = sample[:half], sample[half:]
    older_high, older_low = max(r[2] for r in older), min(r[3] for r in older)
    recent_high, recent_low = max(r[2] for r in recent), min(r[3] for r in recent)
    if recent_high > older_high and recent_low > older_low:
        return "UPTREND"
    if recent_high < older_high and recent_low < older_low:
        return "DOWNTREND"
    return "RANGE"


def _quantile(values, q: float):
    data = sorted(float(v) for v in values if v is not None)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * min(1.0, max(0.0, q))
    lower = int(pos)
    upper = min(lower + 1, len(data) - 1)
    weight = pos - lower
    return data[lower] * (1.0 - weight) + data[upper] * weight


def precompute_perception(candles) -> tuple[list[list], list[dict]]:
    """Build one causal feature row per candle from Mini data only.

    Every feature at index i uses rows <= i. Time-adjusted volume uses only earlier
    sessions at the same clock time. No Copper data, future bar or news is accepted.
    """
    rows = clean_ohlcv(candles)
    features: list[dict] = []
    closes: list[float] = []
    volumes: list[float] = []
    session_rows: list[list] = []
    current_day = None
    same_clock_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))

    for index, row in enumerate(rows):
        stamp = _ts(row[0])
        if current_day != stamp.date():
            session_rows = []
            current_day = stamp.date()
        session_rows.append(row)
        closes.append(row[4])
        volumes.append(row[5])

        ema20 = _ema(closes[-80:], 20)
        ema50 = _ema(closes[-160:], 50)
        atr_points = _atr_points(rows[max(0, index - 60): index + 1], 14)
        atr_pct = (atr_points / row[4] * 100.0) if atr_points and row[4] > 0 else None

        session_high = max(r[2] for r in session_rows)
        session_low = min(r[3] for r in session_rows)
        session_range = session_high - session_low
        range_position = (row[4] - session_low) / session_range if session_range > 0 else 0.5

        total_volume = sum(max(0.0, r[5]) for r in session_rows)
        if total_volume > 0:
            vwap = sum(((r[2] + r[3] + r[4]) / 3.0) * max(0.0, r[5]) for r in session_rows) / total_volume
        else:
            vwap = mean(r[4] for r in session_rows)
        vwap_gap = (row[4] / vwap - 1.0) * 100.0 if vwap > 0 else None

        recent_volume = mean(volumes[-3:]) if volumes else 0.0
        baseline = volumes[-23:-3]
        relative_volume = recent_volume / mean(baseline) if baseline and mean(baseline) > 0 else None
        clock_key = stamp.strftime("%H:%M")
        prior_clock = list(same_clock_history[clock_key])
        time_adjusted = row[5] / mean(prior_clock) if prior_clock and mean(prior_clock) > 0 else None
        same_clock_history[clock_key].append(row[5])

        opening = session_rows[:12]
        opening_high = max(r[2] for r in opening) if opening else row[2]
        opening_low = min(r[3] for r in opening) if opening else row[3]
        opening_range = opening_high - opening_low
        opening_position = (row[4] - opening_low) / opening_range if opening_range > 0 else 0.5
        opening_break = "ABOVE" if len(session_rows) > 12 and row[4] > opening_high else "BELOW" if len(session_rows) > 12 and row[4] < opening_low else "INSIDE"

        oi = row[6]
        previous_oi = rows[index - 1][6] if index > 0 else None
        price_change = row[4] - rows[index - 1][4] if index > 0 else 0.0
        oi_change = (oi - previous_oi) if oi is not None and previous_oi is not None else None
        if oi_change is None or oi_change == 0 or price_change == 0:
            price_oi_state = "UNKNOWN"
        elif price_change > 0 and oi_change > 0:
            price_oi_state = "LONG_BUILDUP"
        elif price_change < 0 and oi_change > 0:
            price_oi_state = "SHORT_BUILDUP"
        elif price_change > 0 and oi_change < 0:
            price_oi_state = "SHORT_COVERING"
        else:
            price_oi_state = "LONG_UNWINDING"

        features.append({
            "timestamp": stamp.isoformat(),
            "price": row[4],
            "return_5m_pct": _rolling_return(closes, 1),
            "return_15m_pct": _rolling_return(closes, 3),
            "return_30m_pct": _rolling_return(closes, 6),
            "return_60m_pct": _rolling_return(closes, 12),
            "return_120m_pct": _rolling_return(closes, 24),
            "ema20": ema20,
            "ema50": ema50,
            "ema20_gap_pct": (row[4] / ema20 - 1.0) * 100.0 if ema20 else None,
            "ema50_gap_pct": (row[4] / ema50 - 1.0) * 100.0 if ema50 else None,
            "atr_points": atr_points,
            "atr_pct": atr_pct,
            "structure": _structure(session_rows),
            "relative_volume": relative_volume,
            "time_adjusted_relative_volume": time_adjusted,
            "session_open": session_rows[0][1],
            "session_return_pct": (row[4] / session_rows[0][1] - 1.0) * 100.0,
            "session_high": session_high,
            "session_low": session_low,
            "session_range_position": range_position,
            "session_vwap": vwap,
            "session_vwap_gap_pct": vwap_gap,
            "opening_range_high": opening_high,
            "opening_range_low": opening_low,
            "opening_range_position": opening_position,
            "opening_range_break": opening_break,
            "price_oi_state": price_oi_state,
            "oi_available": oi is not None,
            "visible_session_bars": len(session_rows),
            "data_quality": {
                "volume_observed": row[5] > 0,
                "oi_observed": oi is not None,
            },
        })
    return rows, features


def causal_profiles(rows: list[list], features: list[dict], lookback_sessions: int = 15) -> dict[str, dict]:
    """Estimate Crude-specific regime reference levels from prior sessions only."""
    by_day: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        by_day[_ts(row[0]).date().isoformat()].append(i)
    days = sorted(by_day)
    profiles: dict[str, dict] = {}
    for pos, day in enumerate(days):
        prior_days = days[max(0, pos - lookback_sessions):pos]
        prior = [features[i] for d in prior_days for i in by_day[d] if features[i].get("visible_session_bars", 0) >= 12]
        atr = [f.get("atr_pct") for f in prior]
        gap = [abs(f.get("session_vwap_gap_pct")) for f in prior if f.get("session_vwap_gap_pct") is not None]
        position = [f.get("session_range_position") for f in prior]
        participation = [f.get("time_adjusted_relative_volume") for f in prior if f.get("time_adjusted_relative_volume") is not None]
        profiles[day] = {
            "mode": "CRUDEOILM_EXPANDING_PRIOR_SESSION_PROFILE_V1",
            "prior_sessions": len(prior_days),
            "prior_observations": len(prior),
            "atr_low_pct": _quantile(atr, 0.25),
            "atr_high_pct": _quantile(atr, 0.75),
            "extended_abs_vwap_gap_pct": _quantile(gap, 0.80),
            "range_position_low": _quantile(position, 0.15),
            "range_position_mid": _quantile(position, 0.50),
            "range_position_high": _quantile(position, 0.85),
            "participation_fading": _quantile(participation, 0.25),
            "participation_confirming": _quantile(participation, 0.60),
            "point_in_time": True,
        }
    return profiles


def market_regime_features(snapshot: dict, profile: dict) -> dict:
    atr = snapshot.get("atr_pct")
    low_atr, high_atr = profile.get("atr_low_pct"), profile.get("atr_high_pct")
    if atr is None or low_atr is None or high_atr is None:
        volatility = "UNKNOWN"
    elif atr >= high_atr:
        volatility = "HIGH"
    elif atr <= low_atr:
        volatility = "LOW"
    else:
        volatility = "NORMAL"

    pos = snapshot.get("session_range_position")
    gap = snapshot.get("session_vwap_gap_pct")
    ext = profile.get("extended_abs_vwap_gap_pct")
    low_pos, high_pos = profile.get("range_position_low"), profile.get("range_position_high")
    if None in {pos, gap, ext, low_pos, high_pos}:
        location = "UNKNOWN"
    elif pos >= high_pos and gap >= ext:
        location = "EXTENDED_ABOVE_VALUE"
    elif pos <= low_pos and gap <= -ext:
        location = "EXTENDED_BELOW_VALUE"
    else:
        location = "IN_VALUE"

    rel = snapshot.get("time_adjusted_relative_volume")
    fade = profile.get("participation_fading")
    participation = "WEAKENING" if rel is not None and fade is not None and rel <= fade else "NORMAL" if rel is not None else "UNKNOWN"
    opening = snapshot.get("opening_range_break")
    opening_behavior = "BREAKOUT" if opening == "ABOVE" else "BREAKDOWN" if opening == "BELOW" else "BALANCED"
    return {
        "trend_structure": snapshot.get("structure") or "UNKNOWN",
        "volatility_regime": volatility,
        "location": location,
        "participation": participation,
        "opening_behavior": opening_behavior,
        "profile": profile,
    }


def price_evidence(snapshot: dict, profile: dict) -> list[dict]:
    """Translate Mini perception into independent evidence lanes, not a weighted score."""
    structure = snapshot.get("structure")
    ret60 = _f(snapshot.get("return_60m_pct"), 0.0)
    if structure == "UPTREND" and ret60 > 0:
        structure_stance = "BULLISH"
    elif structure == "DOWNTREND" and ret60 < 0:
        structure_stance = "BEARISH"
    else:
        structure_stance = "UNKNOWN"

    r15 = _f(snapshot.get("return_15m_pct"), 0.0)
    r30 = _f(snapshot.get("return_30m_pct"), 0.0)
    momentum = "BULLISH" if r15 > 0 and r30 > 0 else "BEARISH" if r15 < 0 and r30 < 0 else "UNKNOWN"

    gap = snapshot.get("session_vwap_gap_pct")
    pos = snapshot.get("session_range_position")
    mid = profile.get("range_position_mid")
    if None in {gap, pos, mid}:
        value = "UNKNOWN"
    elif gap > 0 and pos > mid:
        value = "BULLISH"
    elif gap < 0 and pos < mid:
        value = "BEARISH"
    else:
        value = "UNKNOWN"

    rel = snapshot.get("time_adjusted_relative_volume")
    confirming = profile.get("participation_confirming")
    if rel is not None and confirming is not None and rel >= confirming:
        participation = "BULLISH" if r15 > 0 else "BEARISH" if r15 < 0 else "UNKNOWN"
    else:
        participation = "UNKNOWN"

    opening = snapshot.get("opening_range_break")
    breakout = "BULLISH" if opening == "ABOVE" else "BEARISH" if opening == "BELOW" else "UNKNOWN"

    return [
        {"lane": "STRUCTURE", "stance": structure_stance, "source": "crude_oil_mini_structure", "detail": {"structure": structure, "return_60m_pct": ret60}},
        {"lane": "MOMENTUM", "stance": momentum, "source": "crude_oil_mini_momentum", "detail": {"return_15m_pct": r15, "return_30m_pct": r30}},
        {"lane": "VALUE_LOCATION", "stance": value, "source": "crude_oil_mini_vwap_location", "detail": {"vwap_gap_pct": gap, "session_range_position": pos, "prior_mid": mid}},
        {"lane": "PARTICIPATION", "stance": participation, "source": "crude_oil_mini_time_adjusted_volume", "detail": {"relative_volume": rel, "prior_confirming_level": confirming}},
        {"lane": "OPENING_STRUCTURE", "stance": breakout, "source": "crude_oil_mini_opening_range", "detail": {"opening_range_break": opening}},
    ]


def architecture_contract() -> dict:
    return {
        "mode": "CRUDE_OIL_MINI_MARKET_PERCEPTION_V1",
        "product": "CRUDE_OIL_MINI",
        "underlying_symbol": "CRUDEOILM",
        "news_used": False,
        "option_market_data_used": False,
        "copper_data_used": False,
        "bar_visibility": "BAR_START_PLUS_5_MINUTES",
        "profile_rule": "Regime reference levels are empirical quantiles of prior CRUDEOILM sessions only.",
        "features": [
            "multi_horizon_returns", "ema20_ema50_location", "atr", "market_structure",
            "session_range_location", "session_vwap", "opening_range", "raw_and_time_adjusted_volume",
            "price_oi_state_when_observed",
        ],
    }
