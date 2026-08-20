from __future__ import annotations

from datetime import datetime, time as dt_time
from statistics import mean, pstdev
from typing import Any


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clean_candles(candles: list[list[Any]]) -> list[list[Any]]:
    """
    Groww candle format:
    [timestamp, open, high, low, close, volume, open_interest?]

    Filters:
    - rows with missing OHLC
    - malformed/non-positive OHLC
    - NSE pre-open / synthetic rows before 09:15
    - post-market single-price rows after 15:30
    """
    cleaned = []
    for candle in candles:
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            continue

        ts, op, hi, lo, cl, vol = candle[:6]
        if any(v is None for v in (op, hi, lo, cl)):
            continue

        op = _safe_float(op)
        hi = _safe_float(hi)
        lo = _safe_float(lo)
        cl = _safe_float(cl)
        vol = max(0.0, _safe_float(vol))

        if min(op, hi, lo, cl) <= 0:
            continue
        if hi < lo:
            continue

        try:
            t = datetime.fromisoformat(str(ts)).time()
            if t < dt_time(9, 15) or t > dt_time(15, 30):
                continue
        except Exception:
            pass

        cleaned.append([ts, op, hi, lo, cl, vol, candle[6] if len(candle) > 6 else None])

    return cleaned


def sma(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    window = values[-period:] if len(values) >= period else values
    return mean(window)


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * k + result[-1] * (1 - k))
    return result


def ema(values: list[float], period: int) -> float:
    series = ema_series(values, period)
    return series[-1] if series else 0.0


def rsi_series(values: list[float], period: int = 14) -> list[float]:
    if len(values) < 2:
        return [50.0] * len(values)

    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(values)):
        ch = values[i] - values[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))

    out = []
    for i in range(len(values)):
        if i < period:
            out.append(50.0)
            continue
        ag = mean(gains[i - period + 1:i + 1])
        al = mean(losses[i - period + 1:i + 1])
        if al == 0:
            out.append(100.0)
        else:
            rs = ag / al
            out.append(100 - (100 / (1 + rs)))
    return out


def rsi(values: list[float], period: int = 14) -> float:
    s = rsi_series(values, period)
    return s[-1] if s else 50.0


def atr_series(candles: list[list[Any]], period: int = 14) -> list[float]:
    if not candles:
        return []

    trs = [max(_safe_float(candles[0][2]) - _safe_float(candles[0][3]), 0.0)]
    for i in range(1, len(candles)):
        high = _safe_float(candles[i][2])
        low = _safe_float(candles[i][3])
        prev_close = _safe_float(candles[i - 1][4])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    out = []
    for i in range(len(trs)):
        window = trs[max(0, i - period + 1):i + 1]
        out.append(mean(window))
    return out


def atr(candles: list[list[Any]], period: int = 14) -> float:
    s = atr_series(candles, period)
    return s[-1] if s else 0.0


def macd(values: list[float]) -> tuple[float, float, float]:
    fast = ema_series(values, 12)
    slow = ema_series(values, 26)
    n = min(len(fast), len(slow))
    line = [fast[-n + i] - slow[-n + i] for i in range(n)] if n else []
    signal = ema_series(line, 9)
    macd_line = line[-1] if line else 0.0
    signal_line = signal[-1] if signal else 0.0
    return macd_line, signal_line, macd_line - signal_line


def bollinger(values: list[float], period: int = 20, mult: float = 2.0) -> tuple[float, float, float]:
    window = values[-period:] if len(values) >= period else values
    if not window:
        return 0.0, 0.0, 0.0
    mid = mean(window)
    sd = pstdev(window) if len(window) > 1 else 0.0
    return mid + mult * sd, mid, mid - mult * sd


def roc(values: list[float], period: int = 10) -> float:
    if len(values) <= period:
        return 0.0
    prev = values[-period - 1]
    return ((values[-1] / prev) - 1) * 100 if prev else 0.0


def stoch_rsi(values: list[float], rsi_period: int = 14, stoch_period: int = 14) -> float:
    rs = rsi_series(values, rsi_period)
    if len(rs) < stoch_period:
        return 50.0
    window = rs[-stoch_period:]
    lo = min(window)
    hi = max(window)
    if hi == lo:
        return 50.0
    return 100 * (rs[-1] - lo) / (hi - lo)


def vwap(candles: list[list[Any]]) -> float:
    total_pv = 0.0
    total_vol = 0.0
    for c in candles:
        typical = (_safe_float(c[2]) + _safe_float(c[3]) + _safe_float(c[4])) / 3
        vol = _safe_float(c[5])
        total_pv += typical * vol
        total_vol += vol
    return total_pv / total_vol if total_vol else _safe_float(candles[-1][4]) if candles else 0.0


def adx(candles: list[list[Any]], period: int = 14) -> float:
    if len(candles) <= period + 1:
        return 0.0

    tr_values, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        hi = _safe_float(candles[i][2])
        lo = _safe_float(candles[i][3])
        phi = _safe_float(candles[i - 1][2])
        plo = _safe_float(candles[i - 1][3])
        pcl = _safe_float(candles[i - 1][4])

        tr_values.append(max(hi - lo, abs(hi - pcl), abs(lo - pcl)))
        up = hi - phi
        down = plo - lo
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    dxs = []
    for i in range(period - 1, len(tr_values)):
        tr = sum(tr_values[i - period + 1:i + 1])
        if tr <= 0:
            continue
        pdi = 100 * sum(plus_dm[i - period + 1:i + 1]) / tr
        mdi = 100 * sum(minus_dm[i - period + 1:i + 1]) / tr
        denom = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / denom if denom else 0.0)

    return mean(dxs[-period:]) if dxs else 0.0


def supertrend(candles: list[list[Any]], period: int = 10, multiplier: float = 3.0) -> tuple[float, str]:
    if len(candles) < period + 2:
        return 0.0, "NEUTRAL"

    atrs = atr_series(candles, period)
    final_upper = final_lower = 0.0
    trend = 1
    st = 0.0

    for i, c in enumerate(candles):
        high, low, close = _safe_float(c[2]), _safe_float(c[3]), _safe_float(c[4])
        hl2 = (high + low) / 2
        basic_upper = hl2 + multiplier * atrs[i]
        basic_lower = hl2 - multiplier * atrs[i]

        if i == 0:
            final_upper = basic_upper
            final_lower = basic_lower
            st = basic_lower
            continue

        prev_close = _safe_float(candles[i - 1][4])
        final_upper = basic_upper if basic_upper < final_upper or prev_close > final_upper else final_upper
        final_lower = basic_lower if basic_lower > final_lower or prev_close < final_lower else final_lower

        if trend == -1 and close > final_upper:
            trend = 1
        elif trend == 1 and close < final_lower:
            trend = -1

        st = final_lower if trend == 1 else final_upper

    return st, "BULLISH" if trend == 1 else "BEARISH"


def candle_pattern(candles: list[list[Any]]) -> str:
    if len(candles) < 2:
        return "NONE"
    p = candles[-2]
    c = candles[-1]

    po, pc = _safe_float(p[1]), _safe_float(p[4])
    o, h, l, cl = _safe_float(c[1]), _safe_float(c[2]), _safe_float(c[3]), _safe_float(c[4])

    body = abs(cl - o)
    rng = max(h - l, 1e-9)
    upper = h - max(o, cl)
    lower = min(o, cl) - l

    if cl > o and pc < po and o <= pc and cl >= po:
        return "BULLISH_ENGULFING"
    if cl < o and pc > po and o >= pc and cl <= po:
        return "BEARISH_ENGULFING"
    if body / rng <= 0.1:
        return "DOJI"
    if lower > body * 2 and upper <= body:
        return "HAMMER"
    if upper > body * 2 and lower <= body:
        return "SHOOTING_STAR"
    return "NONE"


def market_structure(candles: list[list[Any]], lookback: int = 20) -> tuple[str, float, float]:
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    highs = [_safe_float(c[2]) for c in recent]
    lows = [_safe_float(c[3]) for c in recent]
    closes = [_safe_float(c[4]) for c in recent]

    support = min(lows)
    resistance = max(highs)

    if len(recent) < 6:
        return "RANGE", support, resistance

    first_half = recent[: len(recent)//2]
    second_half = recent[len(recent)//2:]

    first_high = max(_safe_float(c[2]) for c in first_half)
    first_low = min(_safe_float(c[3]) for c in first_half)
    second_high = max(_safe_float(c[2]) for c in second_half)
    second_low = min(_safe_float(c[3]) for c in second_half)

    if second_high > first_high and second_low > first_low:
        return "UPTREND", support, resistance
    if second_high < first_high and second_low < first_low:
        return "DOWNTREND", support, resistance
    return "RANGE", support, resistance


def analyze_candles(symbol: str, candles: list[list[Any]], min_rr: float = 1.5) -> dict[str, Any]:
    candles = clean_candles(candles)
    if len(candles) < 60:
        return {
            "symbol": symbol,
            "status": "NO_TRADE",
            "reason": "Not enough clean candle history",
            "clean_candles": len(candles),
        }

    closes = [_safe_float(c[4]) for c in candles]
    volumes = [_safe_float(c[5]) for c in candles]

    last = closes[-1]
    e9 = ema(closes, 9)
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200) if len(closes) >= 200 else ema(closes, min(100, len(closes)))
    r14 = rsi(closes, 14)
    a14 = atr(candles, 14)
    macd_line, macd_signal, macd_hist = macd(closes)
    bb_upper, bb_mid, bb_lower = bollinger(closes, 20, 2.0)
    stoch = stoch_rsi(closes)
    roc10 = roc(closes, 10)
    vw = vwap(candles[-26:])  # approximately one NSE session on 15m candles
    adx14 = adx(candles[-100:], 14)
    st_value, st_trend = supertrend(candles[-100:], 10, 3.0)
    pattern = candle_pattern(candles)
    structure, support, resistance = market_structure(candles, 20)

    avg_vol = mean(volumes[-20:]) if any(volumes[-20:]) else 0.0
    volume_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

    # Independent family scores. Avoid double-counting correlated indicators.
    trend_score = 0
    momentum_score = 0
    structure_score = 0
    volume_score = 0
    volatility_score = 0
    price_action_score = 0

    reasons = []
    warnings = []

    # Trend family: max 25
    if last > e20 > e50:
        trend_score += 10
        reasons.append("Price above EMA20 and EMA50")
    elif last < e20 < e50:
        trend_score -= 10
        reasons.append("Price below EMA20 and EMA50")

    if e9 > e20:
        trend_score += 5
        reasons.append("EMA9 above EMA20")
    elif e9 < e20:
        trend_score -= 5

    if last > vw:
        trend_score += 4
        reasons.append("Above session VWAP")
    elif last < vw:
        trend_score -= 4

    if st_trend == "BULLISH":
        trend_score += 6
        reasons.append("Supertrend bullish")
    else:
        trend_score -= 6

    # Momentum family: max 20
    if 55 <= r14 <= 70:
        momentum_score += 6
        reasons.append("RSI bullish without being overbought")
    elif 30 <= r14 <= 45:
        momentum_score -= 6
        reasons.append("RSI bearish")
    elif r14 > 75:
        warnings.append("RSI overbought")
    elif r14 < 25:
        warnings.append("RSI oversold")

    if macd_hist > 0 and macd_line > macd_signal:
        momentum_score += 7
        reasons.append("MACD bullish")
    elif macd_hist < 0 and macd_line < macd_signal:
        momentum_score -= 7

    if roc10 > 0.3:
        momentum_score += 4
    elif roc10 < -0.3:
        momentum_score -= 4

    if 55 <= stoch <= 85:
        momentum_score += 3
    elif 15 <= stoch <= 45:
        momentum_score -= 3

    # Structure family: max 20
    if structure == "UPTREND":
        structure_score += 10
        reasons.append("Higher-high / higher-low structure")
    elif structure == "DOWNTREND":
        structure_score -= 10
        reasons.append("Lower-high / lower-low structure")

    breakout_buffer = max(a14 * 0.2, last * 0.001)
    if last >= resistance - breakout_buffer:
        structure_score += 7
        reasons.append("Testing recent resistance")
    if last <= support + breakout_buffer:
        structure_score -= 7
        reasons.append("Testing recent support")

    if adx14 >= 25:
        if structure == "UPTREND":
            structure_score += 3
        elif structure == "DOWNTREND":
            structure_score -= 3
        reasons.append("ADX confirms trend strength")
    elif adx14 < 18:
        warnings.append("Weak trend / ranging ADX")

    # Volume family: max 15
    if volume_ratio >= 1.5:
        volume_score += 15
        reasons.append("Strong volume expansion")
    elif volume_ratio >= 1.2:
        volume_score += 10
        reasons.append("Volume confirmation")
    elif volume_ratio >= 0.8:
        volume_score += 4
    elif volume_ratio < 0.5:
        volume_score -= 8
        warnings.append("Very weak volume confirmation")

    # Volatility / location family: max 10
    bb_width = ((bb_upper - bb_lower) / bb_mid * 100) if bb_mid else 0.0
    atr_pct = (a14 / last * 100) if last else 0.0
    if 0.15 <= atr_pct <= 1.5:
        volatility_score += 5
    if bb_lower <= last <= bb_upper:
        volatility_score += 3
    if bb_width < 0.5:
        warnings.append("Bollinger compression")
    elif bb_width > 6:
        warnings.append("Elevated volatility")
    else:
        volatility_score += 2

    # Price action family: max 10
    if pattern in ("BULLISH_ENGULFING", "HAMMER"):
        price_action_score += 7
        reasons.append(pattern.replace("_", " ").title())
    elif pattern in ("BEARISH_ENGULFING", "SHOOTING_STAR"):
        price_action_score -= 7
        reasons.append(pattern.replace("_", " ").title())
    elif pattern == "DOJI":
        warnings.append("Doji / indecision")

    # Normalize each family to a long bias contribution.
    raw_bias = (
        trend_score
        + momentum_score
        + structure_score
        + volume_score
        + volatility_score
        + price_action_score
    )

    # Map to 0-100. 50 = neutral.
    alpha_score = max(0.0, min(100.0, 50 + raw_bias))

    long_confirmations = sum([
        trend_score >= 10,
        momentum_score >= 6,
        structure_score >= 7,
        volume_score >= 4,
        price_action_score > 0,
    ])
    short_confirmations = sum([
        trend_score <= -10,
        momentum_score <= -6,
        structure_score <= -7,
        price_action_score < 0,
    ])

    if alpha_score >= 70 and long_confirmations >= 3:
        direction = "LONG"
    elif alpha_score <= 30 and short_confirmations >= 3:
        direction = "SHORT"
    else:
        direction = "NO_TRADE"

    family_scores = {
        "trend": trend_score,
        "momentum": momentum_score,
        "structure": structure_score,
        "volume": volume_score,
        "volatility": volatility_score,
        "price_action": price_action_score,
    }

    base = {
        "symbol": symbol,
        "alpha_score": round(alpha_score, 1),
        "price": round(last, 2),
        "family_scores": family_scores,
        "ema9": round(e9, 2),
        "ema20": round(e20, 2),
        "ema50": round(e50, 2),
        "ema200_proxy": round(e200, 2),
        "vwap": round(vw, 2),
        "rsi14": round(r14, 2),
        "stoch_rsi": round(stoch, 2),
        "macd": round(macd_line, 4),
        "macd_signal": round(macd_signal, 4),
        "macd_hist": round(macd_hist, 4),
        "roc10_pct": round(roc10, 2),
        "atr14": round(a14, 2),
        "adx14": round(adx14, 2),
        "supertrend": round(st_value, 2),
        "supertrend_trend": st_trend,
        "bollinger_upper": round(bb_upper, 2),
        "bollinger_mid": round(bb_mid, 2),
        "bollinger_lower": round(bb_lower, 2),
        "volume_ratio": round(volume_ratio, 2),
        "market_structure": structure,
        "recent_support": round(support, 2),
        "recent_resistance": round(resistance, 2),
        "candle_pattern": pattern,
        "confirmations": {
            "long": long_confirmations,
            "short": short_confirmations,
        },
        "reasons": reasons,
        "warnings": warnings,
        "clean_candles": len(candles),
    }

    if direction == "NO_TRADE" or a14 <= 0:
        return {
            **base,
            "status": "NO_TRADE",
            "reason": "Insufficient independent confluence",
        }

    # Stop uses both ATR and nearby structure.
    atr_risk = max(a14 * 1.25, last * 0.003)
    if direction == "LONG":
        structure_stop = support - 0.15 * a14
        stop = min(last - atr_risk, structure_stop) if structure_stop < last else last - atr_risk
        risk = last - stop
        target1 = last + risk * min_rr
        target2 = last + risk * max(2.0, min_rr + 0.5)
    else:
        structure_stop = resistance + 0.15 * a14
        stop = max(last + atr_risk, structure_stop) if structure_stop > last else last + atr_risk
        risk = stop - last
        target1 = last - risk * min_rr
        target2 = last - risk * max(2.0, min_rr + 0.5)

    return {
        **base,
        "status": "SETUP",
        "direction": direction,
        "entry": round(last, 2),
        "stop_loss": round(stop, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "risk_reward": round(min_rr, 2),
        "note": "Alpha Score is an internal confluence score, not a probability of profit. Paper-test before real-money use.",
    }
