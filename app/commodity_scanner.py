from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

from .commodities import commodity_candles, mcx_session_status, resolve_nearest_mcx_future, analyze_commodity_candles


def _f(value, default=0.0):
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _to_ist_iso(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            raw = float(value)
            if raw > 1_000_000_000_000:
                raw /= 1000.0
            return datetime.fromtimestamp(raw, ZoneInfo("Asia/Kolkata")).isoformat()
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        return parsed.astimezone(ZoneInfo("Asia/Kolkata")).isoformat()
    except Exception:
        return str(value)


def _fresh_enough(timestamp, timeframe, now):
    try:
        text = _to_ist_iso(timestamp)
        ts = datetime.fromisoformat(str(text))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        age = (now - ts.astimezone(ZoneInfo("Asia/Kolkata"))).total_seconds() / 60
        limit = {"5m": 15, "15m": 35, "1h": 90}.get(timeframe, 35)
        return age <= limit, round(age, 1), text
    except Exception:
        return False, None, _to_ist_iso(timestamp)


def _direction_strength(frame, action):
    signal = str(frame.get("signal") or "NO TRADE").upper()
    alpha = _f(frame.get("alpha_score"), 50)
    if signal != action:
        return 50.0
    return alpha if action == "BUY" else 100.0 - alpha


async def commodity_mtf_scan(provider, symbol, min_rr=1.5):
    symbol = str(symbol or "").upper()
    contract = await resolve_nearest_mcx_future(symbol)
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    session = mcx_session_status(now)
    frames = {}
    directions = []

    for tf in ("5m", "15m", "1h"):
        result = await commodity_candles(provider, symbol, tf, contract)
        analysis = analyze_commodity_candles(symbol, result.get("candles", []), min_rr)
        fresh, age, normalized_ts = _fresh_enough(analysis.get("latest_candle_at"), tf, now)
        analysis["latest_candle_at"] = normalized_ts
        analysis["fresh"] = fresh
        analysis["age_minutes"] = age
        analysis["historical_source"] = result.get("historical_source")
        frames[tf] = analysis
        if analysis.get("signal") in {"BUY", "SELL"}:
            directions.append(analysis["signal"])

    buy_count = directions.count("BUY")
    sell_count = directions.count("SELL")
    action = "BUY" if buy_count >= 2 else "SELL" if sell_count >= 2 else "NO TRADE"

    if action == "NO TRADE":
        strength = 50.0
    else:
        strength = round(mean([_direction_strength(frames[tf], action) for tf in ("5m", "15m", "1h")]), 1)

    fresh_all = all(bool(frames[tf].get("fresh")) for tf in frames)
    five_rsi = _f(frames.get("5m", {}).get("rsi14"), 50)
    exhaustion = (action == "BUY" and five_rsi >= 78) or (action == "SELL" and five_rsi <= 22)
    executable = session["is_open"] and fresh_all and action != "NO TRADE" and strength >= 65 and not exhaustion

    reference = next(
        (frames[tf] for tf in ("15m", "5m", "1h") if frames[tf].get("signal") == action and frames[tf].get("status") == "SETUP"),
        None,
    )

    blockers = []
    if not session["is_open"]:
        blockers.append("MCX market is closed; snapshot only.")
    if session["is_open"] and not fresh_all:
        blockers.append("Fresh 5m/15m/1h commodity candles are required.")
    if action == "NO TRADE":
        blockers.append("At least 2 of 3 timeframes must agree on BUY or SELL.")
    if action != "NO TRADE" and strength < 65:
        blockers.append("Direction-normalized commodity strength must be at least 65.")
    if exhaustion:
        blockers.append(f"5m RSI exhaustion blocks {action}: RSI {five_rsi:.1f}.")

    return {
        "provider": "GROWW",
        "mode": "MCX_COMMODITY_MTF",
        "symbol": symbol,
        "contract": contract,
        "market_session": session,
        "timeframes": frames,
        "action": action,
        "alpha_score": strength,
        "raw_timeframe_alpha": {tf: _f(frames[tf].get("alpha_score"), 50) for tf in ("5m", "15m", "1h")},
        "fresh_market_data": fresh_all if session["is_open"] else None,
        "execution_ready": bool(executable),
        "status": "READY" if executable else "SNAPSHOT" if not session["is_open"] else "WATCH",
        "entry": reference.get("entry") if reference else None,
        "stop_loss": reference.get("stop_loss") if reference else None,
        "target1": reference.get("target1") if reference else None,
        "target2": reference.get("target2") if reference else None,
        "risk_reward": reference.get("risk_reward") if reference else None,
        "blockers": blockers,
    }
