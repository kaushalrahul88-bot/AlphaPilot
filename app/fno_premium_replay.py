from __future__ import annotations

from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import httpx

from .fno_history_probe import _resolve_option_contract

IST = ZoneInfo("Asia/Kolkata")


def _ts(value):
    try:
        if isinstance(value, (int, float)):
            x = float(value)
            if x > 1e12:
                x /= 1000.0
            return datetime.fromtimestamp(x, IST)
        text = str(value)
        if text.isdigit():
            x = float(text)
            if x > 1e12:
                x /= 1000.0
            return datetime.fromtimestamp(x, IST)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)
    except Exception:
        return None


def _risk_fraction(entry: float) -> float:
    if entry < 10:
        return 0.30
    if entry < 30:
        return 0.25
    return 0.20


def _parse_entry_at(trade_date: str, entry_time: str) -> datetime:
    d = datetime.fromisoformat(str(trade_date)[:10]).date()
    hour, minute = [int(x) for x in entry_time.split(":", 1)]
    return datetime.combine(d, time(hour, minute), tzinfo=IST)


async def _historical_option_day(provider, contract, trade_date: str, interval: str = "5minute"):
    day = datetime.fromisoformat(str(trade_date)[:10]).date()
    start = datetime.combine(day, time(9, 15), tzinfo=IST)
    end = datetime.combine(day, time(15, 30), tzinfo=IST)
    throttle = getattr(provider, "_throttle", None)
    if callable(throttle):
        await throttle()
    params = {
        "exchange": "NSE",
        "segment": "FNO",
        "groww_symbol": contract["groww_symbol"],
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
        "candle_interval": interval,
    }
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.get(
            f"{provider.BASE_URL}/v1/historical/candles",
            headers=await provider._headers(),
            params=params,
        )
    response.raise_for_status()
    body = response.json()
    payload = body.get("payload", body) if isinstance(body, dict) else {}
    candles = payload.get("candles", []) if isinstance(payload, dict) else []
    rows = [x for x in candles if isinstance(x, (list, tuple)) and len(x) >= 5]
    rows.sort(key=lambda x: (_ts(x[0]) or start))
    return rows


def _simulate(candles, entry_index: int, entry: float, stop: float, t1: float, t2: float):
    max_price = entry
    min_price = entry
    entry_day = (_ts(candles[entry_index][0]) or datetime.now(IST)).date()
    for i in range(entry_index, len(candles)):
        c = candles[i]
        when = _ts(c[0])
        if not when or when.date() != entry_day:
            break
        high, low, close = float(c[2]), float(c[3]), float(c[4])
        max_price = max(max_price, high)
        min_price = min(min_price, low)
        hit_stop = low <= stop
        hit_t1 = high >= t1
        hit_t2 = high >= t2
        if hit_stop and (hit_t1 or hit_t2):
            return {
                "outcome": "AMBIGUOUS",
                "exit_price": None,
                "exit_at": when.isoformat(),
                "reason": "The same 5-minute candle touched both the premium stop and a target; intrabar order is unknown.",
                "max_price": max_price,
                "min_price": min_price,
            }
        if hit_t2:
            return {"outcome": "T2", "exit_price": t2, "exit_at": when.isoformat(), "max_price": max_price, "min_price": min_price}
        if hit_t1:
            return {"outcome": "T1", "exit_price": t1, "exit_at": when.isoformat(), "max_price": max_price, "min_price": min_price}
        if hit_stop:
            return {"outcome": "SL", "exit_price": stop, "exit_at": when.isoformat(), "max_price": max_price, "min_price": min_price}
    last = next((x for x in reversed(candles) if (_ts(x[0]) and _ts(x[0]).date() == entry_day)), None)
    if last:
        return {"outcome": "EOD", "exit_price": float(last[4]), "exit_at": _ts(last[0]).isoformat(), "max_price": max_price, "min_price": min_price}
    return {"outcome": "NO_EXIT", "exit_price": entry, "exit_at": None, "max_price": max_price, "min_price": min_price}


async def replay_option_trade(
    provider,
    symbol: str,
    expiry: str,
    strike: float,
    option_type: str,
    trade_date: str,
    entry_time: str,
    min_rr: float = 1.5,
):
    option_type = option_type.upper().strip()
    if option_type not in {"CE", "PE"}:
        raise ValueError("option_type must be CE or PE")
    signal_at = _parse_entry_at(trade_date, entry_time)
    contract = await _resolve_option_contract(symbol, expiry, strike, option_type)
    if not contract or not contract.get("groww_symbol"):
        return {
            "status": "CONTRACT_NOT_FOUND",
            "symbol": symbol.upper(),
            "expiry": expiry,
            "strike": strike,
            "option_type": option_type,
            "message": "Exact option contract could not be resolved from Groww's current instrument master.",
        }
    candles = await _historical_option_day(provider, contract, trade_date, "5minute")
    if not candles:
        return {"status": "NO_CANDLES", "contract": contract, "trade_date": trade_date, "message": "No historical 5-minute premium candles were returned for the selected trade date."}

    # Enter on the first completed market interval strictly after the signal timestamp.
    entry_index = next((i for i, c in enumerate(candles) if (_ts(c[0]) and _ts(c[0]) > signal_at)), None)
    if entry_index is None:
        return {"status": "NO_ENTRY_CANDLE", "contract": contract, "trade_date": trade_date, "signal_at": signal_at.isoformat()}
    entry_candle = candles[entry_index]
    entry_at = _ts(entry_candle[0])
    entry = float(entry_candle[1])
    if entry <= 0:
        return {"status": "INVALID_ENTRY", "contract": contract, "entry_candle": entry_candle}

    rr = max(1.0, float(min_rr))
    risk_fraction = _risk_fraction(entry)
    risk = entry * risk_fraction
    stop = max(0.05, entry - risk)
    t1 = entry + risk * rr
    t2 = entry + risk * max(2.0, rr + 0.5)
    sim = _simulate(candles, entry_index, entry, stop, t1, t2)
    exit_price = sim.get("exit_price")
    r_multiple = None
    if isinstance(exit_price, (int, float)) and risk > 0:
        r_multiple = (float(exit_price) - entry) / risk
    mfe_r = max(0.0, (float(sim["max_price"]) - entry) / risk) if risk > 0 else None
    mae_r = max(0.0, (entry - float(sim["min_price"])) / risk) if risk > 0 else None
    return {
        "status": "REPLAY_COMPLETE" if sim["outcome"] != "AMBIGUOUS" else "AMBIGUOUS",
        "mode": "TRUE_OPTION_PREMIUM_REPLAY",
        "contract": contract,
        "signal_at": signal_at.isoformat(),
        "entry_at": entry_at.isoformat() if entry_at else None,
        "entry_basis": "NEXT_5M_CANDLE_OPEN_AFTER_SIGNAL",
        "option_entry": round(entry, 2),
        "option_stop": round(stop, 2),
        "option_target1": round(t1, 2),
        "option_target2": round(t2, 2),
        "premium_risk_percent": round(risk_fraction * 100, 1),
        "min_rr": rr,
        "outcome": sim["outcome"],
        "exit_price": round(float(exit_price), 2) if isinstance(exit_price, (int, float)) else None,
        "exit_at": sim.get("exit_at"),
        "r_multiple": round(r_multiple, 3) if r_multiple is not None else None,
        "mfe_r": round(mfe_r, 3) if mfe_r is not None else None,
        "mae_r": round(mae_r, 3) if mae_r is not None else None,
        "candles_available": len(candles),
        "ambiguity_reason": sim.get("reason"),
        "limitations": [
            "This uses actual Groww historical option-premium OHLC candles for the exact contract.",
            "Entry is the next 5-minute candle open after the supplied signal time to avoid look-ahead.",
            "If stop and target occur in the same 5-minute candle, the trade is marked AMBIGUOUS rather than assuming an intrabar order.",
            "Historical bid/ask spread, brokerage, taxes and slippage are not yet deducted.",
        ],
    }
