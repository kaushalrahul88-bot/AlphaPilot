from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

from .commodities import commodity_candles, commodity_quote, mcx_session_status, resolve_nearest_mcx_future, analyze_commodity_candles


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


def _live_price_guard(action, reference, live_price):
    if not reference or action not in {"BUY", "SELL"} or live_price is None:
        return {"ok": False, "reason": "Validated live LTP is required before execution.", "drift_r": None}
    entry = _f(reference.get("entry"))
    stop = _f(reference.get("stop_loss"))
    target1 = _f(reference.get("target1"))
    live = _f(live_price)
    risk = (entry - stop) if action == "BUY" else (stop - entry)
    if min(entry, stop, target1, live) <= 0 or risk <= 0:
        return {"ok": False, "reason": "Live-price execution guard could not validate the trade geometry.", "drift_r": None}
    drift_r = abs(live - entry) / risk
    if action == "BUY" and live <= stop:
        return {"ok": False, "reason": f"Live LTP {live:.2f} is already at/below the planned stop {stop:.2f}.", "drift_r": round(drift_r, 3)}
    if action == "SELL" and live >= stop:
        return {"ok": False, "reason": f"Live LTP {live:.2f} is already at/above the planned stop {stop:.2f}.", "drift_r": round(drift_r, 3)}
    if action == "BUY" and live >= target1:
        return {"ok": False, "reason": f"Live LTP {live:.2f} has already reached/passed T1 {target1:.2f}; do not chase the completed setup.", "drift_r": round(drift_r, 3)}
    if action == "SELL" and live <= target1:
        return {"ok": False, "reason": f"Live LTP {live:.2f} has already reached/passed T1 {target1:.2f}; do not chase the completed setup.", "drift_r": round(drift_r, 3)}
    if drift_r > 0.50:
        return {"ok": False, "reason": f"Live LTP has moved {drift_r:.2f}R from the candle-derived entry; maximum allowed execution drift is 0.50R.", "drift_r": round(drift_r, 3)}
    return {"ok": True, "reason": None, "drift_r": round(drift_r, 3)}


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

    reference = next(
        (frames[tf] for tf in ("15m", "5m", "1h") if frames[tf].get("signal") == action and frames[tf].get("status") == "SETUP"),
        None,
    )

    live_price = None
    live_quote_error = None
    if session["is_open"] and action != "NO TRADE" and reference:
        try:
            quote = await commodity_quote(provider, symbol, contract)
            live_price = _f(quote.get("last_price"), None)
        except Exception as exc:
            live_quote_error = str(exc)
    price_guard = _live_price_guard(action, reference, live_price) if action != "NO TRADE" and reference else {"ok": False, "reason": None, "drift_r": None}

    executable = (
        session["is_open"]
        and fresh_all
        and action != "NO TRADE"
        and strength >= 65
        and not exhaustion
        and bool(reference)
        and price_guard["ok"]
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
    if action != "NO TRADE" and not reference:
        blockers.append("No executable candle-derived reference setup is available for the agreed direction.")
    if session["is_open"] and action != "NO TRADE" and reference and live_quote_error:
        blockers.append(f"Validated live LTP is unavailable: {live_quote_error[:180]}")
    elif session["is_open"] and action != "NO TRADE" and reference and not price_guard["ok"] and price_guard.get("reason"):
        blockers.append(price_guard["reason"])

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
        "live_price": live_price,
        "entry_drift_r": price_guard.get("drift_r"),
        "max_entry_drift_r": 0.50,
        "live_price_guard_passed": bool(price_guard["ok"]) if session["is_open"] and action != "NO TRADE" and reference else None,
        "execution_ready": bool(executable),
        "status": "READY" if executable else "SNAPSHOT" if not session["is_open"] else "WATCH",
        "entry": reference.get("entry") if reference else None,
        "stop_loss": reference.get("stop_loss") if reference else None,
        "target1": reference.get("target1") if reference else None,
        "target2": reference.get("target2") if reference else None,
        "risk_reward": reference.get("risk_reward") if reference else None,
        "blockers": blockers,
    }
