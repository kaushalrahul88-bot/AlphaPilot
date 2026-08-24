from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from .backtest import _historical, _ts

IST = ZoneInfo("Asia/Kolkata")

STRATEGIES = ("ORB_30", "VWAP_TREND", "BREAKOUT_20")


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def _atr(rows: list[list], period: int = 14) -> list[float]:
    if not rows:
        return []
    trs: list[float] = []
    prev_close = None
    for row in rows:
        high, low, close = float(row[2]), float(row[3]), float(row[4])
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(max(tr, 0.0))
        prev_close = close
    out: list[float] = []
    for i in range(len(trs)):
        start = max(0, i - period + 1)
        window = trs[start : i + 1]
        out.append(sum(window) / len(window))
    return out


def _vwap(rows: list[list]) -> list[float]:
    out: list[float] = []
    cum_pv = 0.0
    cum_vol = 0.0
    current_day = None
    for row in rows:
        when = _ts(row[0])
        day = when.date() if when else None
        if day != current_day:
            current_day = day
            cum_pv = 0.0
            cum_vol = 0.0
        high, low, close = float(row[2]), float(row[3]), float(row[4])
        volume = float(row[5]) if len(row) > 5 else 0.0
        typical = (high + low + close) / 3.0
        cum_pv += typical * max(volume, 0.0)
        cum_vol += max(volume, 0.0)
        out.append(cum_pv / cum_vol if cum_vol > 0 else close)
    return out


def _simulate_underlying(rows: list[list], signal_index: int, direction: str, stop: float, target_r: float = 1.0):
    entry_index = signal_index + 1
    if entry_index >= len(rows):
        return None
    signal_time = _ts(rows[signal_index][0])
    entry_time = _ts(rows[entry_index][0])
    if not signal_time or not entry_time or entry_time.date() != signal_time.date():
        return None
    entry = float(rows[entry_index][1])
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0:
        return None
    target = entry + risk * target_r if direction == "LONG" else entry - risk * target_r
    max_fav = 0.0
    max_adv = 0.0
    for row in rows[entry_index:]:
        when = _ts(row[0])
        if not when or when.date() != entry_time.date():
            break
        high, low = float(row[2]), float(row[3])
        if direction == "LONG":
            max_fav = max(max_fav, (high - entry) / risk)
            max_adv = max(max_adv, (entry - low) / risk)
            hit_stop = low <= stop
            hit_target = high >= target
        else:
            max_fav = max(max_fav, (entry - low) / risk)
            max_adv = max(max_adv, (high - entry) / risk)
            hit_stop = high >= stop
            hit_target = low <= target
        if hit_stop and hit_target:
            return {"outcome": "AMBIGUOUS", "r_multiple": None, "entry": entry, "stop": stop, "target": target, "entry_at": entry_time.isoformat(), "mfe_r": round(max_fav, 3), "mae_r": round(max_adv, 3)}
        if hit_target:
            return {"outcome": "TARGET", "r_multiple": round(target_r, 3), "entry": entry, "stop": stop, "target": target, "entry_at": entry_time.isoformat(), "mfe_r": round(max_fav, 3), "mae_r": round(max_adv, 3)}
        if hit_stop:
            return {"outcome": "SL", "r_multiple": -1.0, "entry": entry, "stop": stop, "target": target, "entry_at": entry_time.isoformat(), "mfe_r": round(max_fav, 3), "mae_r": round(max_adv, 3)}
    last = next((x for x in reversed(rows[entry_index:]) if (_ts(x[0]) and _ts(x[0]).date() == entry_time.date())), None)
    if not last:
        return None
    close = float(last[4])
    r = (close - entry) / risk if direction == "LONG" else (entry - close) / risk
    return {"outcome": "EOD", "r_multiple": round(r, 3), "entry": entry, "stop": stop, "target": target, "entry_at": entry_time.isoformat(), "mfe_r": round(max_fav, 3), "mae_r": round(max_adv, 3)}


def _day_indices(rows: list[list]):
    out: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        when = _ts(row[0])
        if when:
            out[when.date().isoformat()].append(i)
    return out


def _orb_signal(rows: list[list], indices: list[int], atrs: list[float]):
    opening = [i for i in indices if (w := _ts(rows[i][0])) and time(9, 15) <= w.time() < time(9, 45)]
    if len(opening) < 3:
        return None
    or_high = max(float(rows[i][2]) for i in opening)
    or_low = min(float(rows[i][3]) for i in opening)
    or_range = or_high - or_low
    if or_range <= 0:
        return None
    search = [i for i in indices if (w := _ts(rows[i][0])) and time(9, 45) <= w.time() <= time(12, 30)]
    for i in search:
        close = float(rows[i][4])
        volumes = [float(rows[j][5]) for j in range(max(indices[0], i - 20), i) if len(rows[j]) > 5]
        vol = float(rows[i][5]) if len(rows[i]) > 5 else 0.0
        avg_vol = mean(volumes) if volumes else 0.0
        volume_ok = avg_vol <= 0 or vol >= 1.15 * avg_vol
        atr = atrs[i] if i < len(atrs) else 0.0
        if close > or_high and volume_ok:
            stop = max(or_low, close - max(atr * 1.2, or_range * 0.6))
            return i, "LONG", stop, {"opening_high": or_high, "opening_low": or_low, "volume_ratio": round(vol / avg_vol, 2) if avg_vol > 0 else None}
        if close < or_low and volume_ok:
            stop = min(or_high, close + max(atr * 1.2, or_range * 0.6))
            return i, "SHORT", stop, {"opening_high": or_high, "opening_low": or_low, "volume_ratio": round(vol / avg_vol, 2) if avg_vol > 0 else None}
    return None


def _vwap_trend_signal(rows: list[list], indices: list[int], vwaps: list[float], ema20: list[float], ema50: list[float], atrs: list[float]):
    search = [i for i in indices if (w := _ts(rows[i][0])) and time(9, 45) <= w.time() <= time(14, 0)]
    for i in search:
        if i < 2:
            continue
        close = float(rows[i][4])
        prev_close = float(rows[i - 1][4])
        high = float(rows[i][2])
        low = float(rows[i][3])
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        distance = abs(close - vwaps[i]) / atr
        if ema20[i] > ema50[i] and close > vwaps[i] and prev_close <= max(vwaps[i - 1], ema20[i - 1]) and distance <= 0.9:
            stop = min(low, vwaps[i] - 0.25 * atr)
            return i, "LONG", stop, {"vwap": round(vwaps[i], 2), "ema20": round(ema20[i], 2), "ema50": round(ema50[i], 2), "vwap_distance_atr": round(distance, 2)}
        if ema20[i] < ema50[i] and close < vwaps[i] and prev_close >= min(vwaps[i - 1], ema20[i - 1]) and distance <= 0.9:
            stop = max(high, vwaps[i] + 0.25 * atr)
            return i, "SHORT", stop, {"vwap": round(vwaps[i], 2), "ema20": round(ema20[i], 2), "ema50": round(ema50[i], 2), "vwap_distance_atr": round(distance, 2)}
    return None


def _breakout_signal(rows: list[list], indices: list[int], atrs: list[float]):
    search = [i for i in indices if (w := _ts(rows[i][0])) and time(9, 45) <= w.time() <= time(14, 0)]
    first = indices[0] if indices else 0
    for i in search:
        if i - first < 20:
            continue
        prior = [j for j in range(max(first, i - 20), i)]
        prior_high = max(float(rows[j][2]) for j in prior)
        prior_low = min(float(rows[j][3]) for j in prior)
        close = float(rows[i][4])
        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue
        buffer = 0.10 * atr
        if close > prior_high + buffer:
            stop = max(prior_high - 0.35 * atr, close - 1.25 * atr)
            return i, "LONG", stop, {"lookback_high": prior_high, "lookback_low": prior_low, "breakout_buffer_atr": 0.10}
        if close < prior_low - buffer:
            stop = min(prior_low + 0.35 * atr, close + 1.25 * atr)
            return i, "SHORT", stop, {"lookback_high": prior_high, "lookback_low": prior_low, "breakout_buffer_atr": 0.10}
    return None


def _summary(trades: list[dict]):
    resolved = [t for t in trades if isinstance(t.get("r_multiple"), (int, float))]
    wins = sum(1 for t in resolved if float(t["r_multiple"]) > 0)
    total_r = sum(float(t["r_multiple"]) for t in resolved)
    equity = peak = max_dd = 0.0
    for trade in sorted(resolved, key=lambda x: x.get("signal_at", "")):
        equity += float(trade["r_multiple"])
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(resolved),
        "wins": wins,
        "losses": sum(1 for t in resolved if float(t["r_multiple"]) < 0),
        "win_rate": round(wins / len(resolved) * 100, 1) if resolved else 0.0,
        "total_r": round(total_r, 3),
        "average_r": round(total_r / len(resolved), 3) if resolved else 0.0,
        "max_drawdown_r": round(max_dd, 3),
        "ambiguous": sum(1 for t in trades if t.get("outcome") == "AMBIGUOUS"),
    }


async def run_strategy_research(provider, symbols: list[str], start_date: str, end_date: str, target_r: float = 1.0):
    start = datetime.fromisoformat(start_date).replace(tzinfo=IST)
    end = datetime.fromisoformat(end_date).replace(tzinfo=IST) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 31:
        raise ValueError("Strategy research range is limited to 31 days per run")
    target_r = max(0.25, min(float(target_r), 3.0))
    strategies: dict[str, list[dict]] = {name: [] for name in STRATEGIES}
    errors = []

    for raw_symbol in symbols:
        symbol = raw_symbol.upper().strip()
        if not symbol:
            continue
        try:
            warmup = start - timedelta(days=5)
            rows = await _historical(provider, symbol, "5m", warmup, end)
            rows = [r for r in rows if _ts(r[0]) and _ts(r[0]) <= end]
            closes = [float(r[4]) for r in rows]
            atrs = _atr(rows, 14)
            vwaps = _vwap(rows)
            ema20 = _ema(closes, 20)
            ema50 = _ema(closes, 50)
            days = _day_indices(rows)
            for day, indices in sorted(days.items()):
                d = datetime.fromisoformat(day).date()
                if d < start.date() or d > end.date():
                    continue
                definitions = {
                    "ORB_30": _orb_signal(rows, indices, atrs),
                    "VWAP_TREND": _vwap_trend_signal(rows, indices, vwaps, ema20, ema50, atrs),
                    "BREAKOUT_20": _breakout_signal(rows, indices, atrs),
                }
                for strategy, signal in definitions.items():
                    if not signal:
                        continue
                    signal_index, direction, stop, features = signal
                    sim = _simulate_underlying(rows, signal_index, direction, float(stop), target_r)
                    if not sim:
                        continue
                    signal_at = _ts(rows[signal_index][0])
                    strategies[strategy].append({
                        "strategy": strategy,
                        "symbol": symbol,
                        "signal_at": signal_at.isoformat() if signal_at else str(rows[signal_index][0]),
                        "direction": direction,
                        "action": "BUY CE" if direction == "LONG" else "BUY PE",
                        "signal_close": round(float(rows[signal_index][4]), 2),
                        "research_target_r": target_r,
                        "features": features,
                        **sim,
                    })
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    leaderboard = []
    for strategy in STRATEGIES:
        summary = _summary(strategies[strategy])
        leaderboard.append({"strategy": strategy, **summary})
    leaderboard.sort(key=lambda x: (x["average_r"], x["trades"]), reverse=True)
    return {
        "mode": "ALPHAPILOT_STRATEGY_RESEARCH_V2_UNDERLYING_DISCOVERY",
        "start_date": start_date,
        "end_date": end_date,
        "target_r": target_r,
        "strategy_definitions": {
            "ORB_30": "30-minute opening-range breakout after 09:45, with a modest volume-expansion requirement and ATR/range-based stop.",
            "VWAP_TREND": "VWAP continuation/pullback hypothesis using EMA20/EMA50 trend alignment and a return through VWAP/EMA area.",
            "BREAKOUT_20": "20-bar intraday price-channel breakout with a 0.10 ATR confirmation buffer and ATR-based stop.",
        },
        "leaderboard": leaderboard,
        "trades_by_strategy": strategies,
        "errors": errors,
        "limitations": [
            "This is a strategy-discovery layer on underlying 5-minute NSE candles, not option-premium P&L.",
            "The three rule sets are explicit AlphaPilot research definitions of established strategy families; they are hypotheses, not claims that a public strategy is profitable.",
            "Entry is the next 5-minute candle open after the signal to avoid look-ahead.",
            "Only one signal per strategy per symbol per day is generated in this first research version.",
            "Same-candle target/stop collisions are marked AMBIGUOUS rather than guessed.",
            "Any strategy that survives this discovery stage must next be passed through the existing true option-premium replay and untouched out-of-sample validation before live use.",
        ],
    }
