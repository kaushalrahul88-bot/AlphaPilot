from __future__ import annotations

from datetime import datetime, time
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp


IST = ZoneInfo("Asia/Kolkata")
OPENING_RANGE_START = time(9, 0)
OPENING_RANGE_END = time(10, 0)
RVOL_MINIMUM = 1.20
PREMIUM_RISK_REWARD = 1.50
BENCHMARKS = {"CRUDEOIL": "WTI", "NATURALGAS": "HENRY_HUB"}


def _number(value, default=0.0):
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _timestamp(value):
    return parse_ist_timestamp(value)


def _valid_rows(rows, through=None):
    output = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = _timestamp(row[0])
        except Exception:
            continue
        if through is not None and stamp > through:
            continue
        opened, high, low, close = (_number(row[index]) for index in range(1, 5))
        if min(opened, high, low, close) <= 0 or high < low:
            continue
        volume = max(0.0, _number(row[5])) if len(row) > 5 else 0.0
        output.append([stamp, opened, high, low, close, volume])
    return sorted(output, key=lambda row: row[0])


def _session_vwap_path(rows):
    path = []
    price_volume = 0.0
    volume = 0.0
    fallback_prices = []
    for row in rows:
        typical = (row[2] + row[3] + row[4]) / 3.0
        fallback_prices.append(typical)
        if row[5] > 0:
            price_volume += typical * row[5]
            volume += row[5]
        path.append(price_volume / volume if volume > 0 else mean(fallback_prices))
    return path


def _vwap_gate(rows, direction):
    path = _session_vwap_path(rows)
    if len(path) < 4:
        return {"passed": False, "reason": "At least four current-session candles are required."}
    close = rows[-1][4]
    vwap = path[-1]
    earlier = path[-4]
    aligned = close > vwap and vwap > earlier if direction == "BULLISH" else close < vwap and vwap < earlier
    return {
        "passed": aligned,
        "session_vwap": round(vwap, 4),
        "vwap_three_bars_ago": round(earlier, 4),
        "price_location": "ABOVE" if close > vwap else "BELOW" if close < vwap else "AT",
        "slope": "RISING" if vwap > earlier else "FALLING" if vwap < earlier else "FLAT",
    }


def _opening_range_gate(rows, direction, click_at):
    opening = [row for row in rows if OPENING_RANGE_START <= row[0].time() < OPENING_RANGE_END]
    after = [row for row in rows if row[0].time() >= OPENING_RANGE_END]
    if click_at.time() < OPENING_RANGE_END or len(opening) < 6 or len(after) < 2:
        return {
            "passed": False,
            "status": "WAIT",
            "reason": "The 09:00-10:00 opening range and two confirmation candles must complete first.",
        }
    high = max(row[2] for row in opening)
    low = min(row[3] for row in opening)
    if direction == "BULLISH":
        breakout_seen = any(row[4] > high for row in after[:-1])
        latest_holds = after[-1][4] > high
        retest_or_continuation = after[-1][3] <= high or after[-1][4] > after[-2][4]
    else:
        breakout_seen = any(row[4] < low for row in after[:-1])
        latest_holds = after[-1][4] < low
        retest_or_continuation = after[-1][2] >= low or after[-1][4] < after[-2][4]
    return {
        "passed": breakout_seen and latest_holds and retest_or_continuation,
        "status": "CONFIRMED" if breakout_seen and latest_holds and retest_or_continuation else "UNCONFIRMED",
        "opening_range_high": round(high, 4),
        "opening_range_low": round(low, 4),
        "breakout_seen": breakout_seen,
        "latest_close_holds": latest_holds,
        "retest_or_continuation": retest_or_continuation,
    }


def _relative_volume_gate(current_rows, comparison_rows, click_at):
    current_volume = sum(row[5] for row in current_rows)
    grouped = {}
    for row in _valid_rows(comparison_rows, click_at):
        if row[0].date() >= click_at.date() or row[0].time() > click_at.time():
            continue
        grouped.setdefault(row[0].date(), 0.0)
        grouped[row[0].date()] += row[5]
    samples = [grouped[key] for key in sorted(grouped)[-5:] if grouped[key] > 0]
    baseline = mean(samples) if samples else 0.0
    ratio = current_volume / baseline if baseline > 0 else None
    return {
        "passed": ratio is not None and ratio >= RVOL_MINIMUM,
        "current_cumulative_volume": round(current_volume, 2),
        "same_time_baseline_volume": round(baseline, 2) if baseline > 0 else None,
        "comparison_sessions": len(samples),
        "relative_volume": round(ratio, 3) if ratio is not None else None,
        "minimum": RVOL_MINIMUM,
    }


def _benchmark_gate(symbol, direction, benchmark, click_at):
    expected = BENCHMARKS[symbol]
    supplied = str((benchmark or {}).get("symbol") or "").upper()
    supplied_direction = str((benchmark or {}).get("direction") or "").upper()
    fresh = bool((benchmark or {}).get("fresh"))
    try:
        as_of = _timestamp((benchmark or {}).get("as_of"))
    except Exception:
        as_of = None
    no_future_data = as_of is not None and as_of <= click_at
    return {
        "passed": supplied == expected and supplied_direction == direction and fresh and no_future_data,
        "required_symbol": expected,
        "direction": supplied_direction or None,
        "fresh": fresh,
        "as_of": as_of.isoformat() if as_of else None,
        "no_future_data": no_future_data,
    }


def premium_plan(entry, risk_reward=PREMIUM_RISK_REWARD):
    premium = _number(entry)
    if premium <= 0:
        return None
    risk_fraction = 0.30 if premium < 10 else 0.25 if premium < 30 else 0.20
    risk = premium * risk_fraction
    rr = float(risk_reward)
    return {
        "entry": round(premium, 2),
        "stop_loss": round(max(0.05, premium - risk), 2),
        "target": round(premium + rr * risk, 2),
        "risk_amount": round(risk, 2),
        "risk_percent": round(risk_fraction * 100.0, 1),
        "risk_reward": round(rr, 2),
    }


def evaluate_commodity_click(
    symbol,
    click_at,
    previous_plan,
    mtf_snapshot,
    current_rows,
    comparison_rows,
    benchmark,
    option_premium=None,
    premium_risk_reward=PREMIUM_RISK_REWARD,
    require_option_premium=True,
):
    symbol = str(symbol).upper().strip()
    if symbol not in BENCHMARKS:
        raise ValueError("symbol must be CRUDEOIL or NATURALGAS")
    click = _timestamp(click_at)
    session_rows = [row for row in _valid_rows(current_rows, click) if row[0].date() == click.date()]
    previous_direction = str((previous_plan or {}).get("underlying_direction") or "NEUTRAL").upper()
    mtf_action = str((mtf_snapshot or {}).get("action") or "NO TRADE").upper()
    current_direction = "BULLISH" if mtf_action == "BUY" else "BEARISH" if mtf_action == "SELL" else "NEUTRAL"
    alpha = _number((mtf_snapshot or {}).get("alpha_score"), 50.0)
    fresh = bool((mtf_snapshot or {}).get("fresh_market_data"))
    aligned = previous_direction in {"BULLISH", "BEARISH"} and previous_direction == current_direction
    action = "BUY CE" if previous_direction == "BULLISH" else "BUY PE" if previous_direction == "BEARISH" else "NO TRADE"

    gates = {
        "previous_current_alignment": {"passed": aligned, "previous": previous_direction, "current": current_direction},
        "mtf_strength": {"passed": alpha >= 65.0, "alpha_score": round(alpha, 1), "minimum": 65.0},
        "fresh_intraday_data": {"passed": fresh},
    }
    if previous_direction in {"BULLISH", "BEARISH"}:
        gates["session_vwap"] = _vwap_gate(session_rows, previous_direction)
        gates["opening_range"] = _opening_range_gate(session_rows, previous_direction, click)
        gates["time_adjusted_relative_volume"] = _relative_volume_gate(session_rows, comparison_rows, click)
        gates["global_benchmark"] = _benchmark_gate(symbol, previous_direction, benchmark, click)
    else:
        for name in ("session_vwap", "opening_range", "time_adjusted_relative_volume", "global_benchmark"):
            gates[name] = {"passed": False, "reason": "Previous session has no directional setup."}

    plan = premium_plan(option_premium, premium_risk_reward)
    gates["option_premium"] = {
        "passed": plan is not None or not require_option_premium,
        "required": bool(require_option_premium),
        "available": plan is not None,
        "risk_reward": float(premium_risk_reward),
    }
    passed = all(bool(gate.get("passed")) for gate in gates.values())
    waiting = gates["opening_range"].get("status") == "WAIT"
    blockers = [name for name, gate in gates.items() if not gate.get("passed")]
    return {
        "mode": "COMMODITY_CLICK_BRAIN_V1_RESEARCH",
        "symbol": symbol,
        "click_at": click.isoformat(),
        "research_only": True,
        "no_future_candles": True,
        "status": "READY" if passed else "WAIT" if waiting else "NO_TRADE",
        "action": action if passed else "NO TRADE",
        "underlying_direction": previous_direction if passed else "NEUTRAL",
        "premium_setup": plan if passed else None,
        "gates": gates,
        "blockers": blockers,
    }
