from __future__ import annotations

import csv
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx

from .fno_history_probe import INSTRUMENT_CSV_URL


IST = ZoneInfo("Asia/Kolkata")
SUPPORTED = {"COPPER", "CRUDEOIL", "NATURALGAS"}
AUTO_EXPIRY_MAX_DTE_DAYS = 35


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or str(value).isdigit():
        parsed = datetime.fromtimestamp(float(value) / 1000.0 if float(value) > 1e12 else float(value), IST)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _expiry(value):
    try:
        return datetime.fromisoformat(str(value or "")[:10]).date()
    except Exception:
        return None


def _normalized_tick(raw):
    value = _number(raw)
    return round(value / 100.0, 4) if value is not None and value > 0 else None


def _option_row(row, wanted):
    symbol = str(row.get("underlying_symbol") or "").upper().strip()
    option_type = str(row.get("instrument_type") or "").upper().strip()
    if str(row.get("exchange") or "").upper() != "MCX":
        return None
    if str(row.get("segment") or "").upper() != "COMMODITY":
        return None
    if symbol not in SUPPORTED or (wanted and symbol not in wanted):
        return None
    if option_type not in {"CE", "PE"}:
        return None
    strike = _number(row.get("strike_price"))
    expiry_date = _expiry(row.get("expiry_date"))
    groww_symbol = row.get("groww_symbol")
    if strike is None or strike <= 0 or expiry_date is None or not groww_symbol:
        return None
    return {
        "underlying": symbol,
        "exchange": "MCX",
        "segment": "COMMODITY",
        "option_type": option_type,
        "expiry": expiry_date.isoformat(),
        "strike": float(strike),
        "groww_symbol": str(groww_symbol),
        "trading_symbol": str(row.get("trading_symbol") or row.get("internal_trading_symbol") or ""),
        "lot_size": int(float(row["lot_size"])) if str(row.get("lot_size") or "").strip() else None,
        "tick_size": _normalized_tick(row.get("tick_size")),
        "buy_allowed": str(row.get("buy_allowed") or "") == "1",
    }


async def fetch_mcx_option_master(symbols=None):
    wanted = {str(symbol).upper().strip() for symbol in (symbols or []) if str(symbol).strip()}
    rows = []
    async with httpx.AsyncClient(timeout=40) as client:
        async with client.stream("GET", INSTRUMENT_CSV_URL) as response:
            response.raise_for_status()
            fieldnames = None
            async for line in response.aiter_lines():
                if not line:
                    continue
                values = next(csv.reader([line]))
                if fieldnames is None:
                    fieldnames = [str(value).lstrip("\ufeff").strip() for value in values]
                    continue
                if len(values) < len(fieldnames):
                    values += [""] * (len(fieldnames) - len(values))
                elif len(values) > len(fieldnames):
                    values = values[: len(fieldnames)]
                normalized = _option_row(dict(zip(fieldnames, values)), wanted)
                if normalized:
                    rows.append(normalized)
    return rows


def select_mcx_option_contract(master_rows, symbol, trade_date, underlying_price, option_type):
    symbol = str(symbol).upper().strip()
    option_type = str(option_type).upper().strip()
    when = trade_date if isinstance(trade_date, date) else datetime.fromisoformat(str(trade_date)[:10]).date()
    price = _number(underlying_price)
    if symbol not in SUPPORTED:
        raise ValueError("symbol must be COPPER, CRUDEOIL or NATURALGAS")
    if option_type not in {"CE", "PE"}:
        raise ValueError("option_type must be CE or PE")
    if price is None or price <= 0:
        raise ValueError("underlying_price must be positive")
    candidates = []
    for row in master_rows or []:
        if str(row.get("underlying") or "").upper() != symbol:
            continue
        if str(row.get("option_type") or "").upper() != option_type:
            continue
        expiry_date = _expiry(row.get("expiry"))
        strike = _number(row.get("strike"))
        if expiry_date is None or strike is None or expiry_date < when:
            continue
        dte = (expiry_date - when).days
        if dte > AUTO_EXPIRY_MAX_DTE_DAYS:
            continue
        candidates.append((expiry_date, abs(strike - price), strike, row))
    if not candidates:
        return None
    nearest_expiry = min(item[0] for item in candidates)
    same_expiry = [item for item in candidates if item[0] == nearest_expiry]
    _, distance, _, selected = min(same_expiry, key=lambda item: (item[1], item[2]))
    return {
        **selected,
        "expiry_dte": (nearest_expiry - when).days,
        "strike_selection": "NEAREST_LISTED_STRIKE_TO_POINT_IN_TIME_UNDERLYING",
        "distance_from_underlying": round(distance, 4),
        "research_only": True,
    }


def ranked_mcx_option_contracts(master_rows, symbol, trade_date, underlying_price, option_type, max_strikes=12):
    """
    Rank nearest-expiry MCX options from nearest strike outward.

    Affordability is intentionally not decided here because it depends on the
    point-in-time option premium, which must come from historical/live data.
    """
    symbol = str(symbol).upper().strip()
    option_type = str(option_type).upper().strip()
    when = trade_date if isinstance(trade_date, date) else datetime.fromisoformat(str(trade_date)[:10]).date()
    price = _number(underlying_price)
    if symbol not in SUPPORTED:
        raise ValueError("symbol must be COPPER, CRUDEOIL or NATURALGAS")
    if option_type not in {"CE", "PE"}:
        raise ValueError("option_type must be CE or PE")
    if price is None or price <= 0:
        raise ValueError("underlying_price must be positive")

    candidates = []
    for row in master_rows or []:
        if str(row.get("underlying") or "").upper() != symbol:
            continue
        if str(row.get("option_type") or "").upper() != option_type:
            continue
        expiry_date = _expiry(row.get("expiry"))
        strike = _number(row.get("strike"))
        if expiry_date is None or strike is None or expiry_date < when:
            continue
        dte = (expiry_date - when).days
        if dte > AUTO_EXPIRY_MAX_DTE_DAYS:
            continue
        candidates.append((expiry_date, abs(strike - price), strike, row))
    if not candidates:
        return []

    nearest_expiry = min(item[0] for item in candidates)
    same_expiry = [item for item in candidates if item[0] == nearest_expiry]
    ranked = sorted(same_expiry, key=lambda item: (item[1], item[2]))[:max(1, int(max_strikes))]
    return [
        {
            **row,
            "expiry_dte": (nearest_expiry - when).days,
            "strike_selection_rank": rank,
            "distance_from_underlying": round(distance, 4),
            "research_only": True,
        }
        for rank, (_expiry_date, distance, _strike, row) in enumerate(ranked, start=1)
    ]


def option_lot_affordability(entry_premium, lot_size, budget_rupees=15000.0):
    premium = _number(entry_premium)
    try:
        lot = int(lot_size)
    except (TypeError, ValueError):
        lot = 0
    budget = float(budget_rupees)
    if premium is None or premium <= 0 or lot <= 0 or budget <= 0:
        return {
            "affordable": False,
            "entry_premium": premium,
            "lot_size": lot or None,
            "budget_rupees": budget,
            "cost_per_lot_rupees": None,
            "lots": 0,
            "deployed_amount_rupees": 0.0,
        }
    cost_per_lot = premium * lot
    lots = int(budget // cost_per_lot) if cost_per_lot > 0 else 0
    return {
        "affordable": lots >= 1,
        "entry_premium": round(premium, 4),
        "lot_size": lot,
        "budget_rupees": round(budget, 2),
        "cost_per_lot_rupees": round(cost_per_lot, 2),
        "lots": lots,
        "deployed_amount_rupees": round(lots * cost_per_lot, 2),
        "unused_budget_rupees": round(budget - lots * cost_per_lot, 2),
    }


async def select_affordable_historical_mcx_option(
    provider,
    master_rows,
    symbol,
    trade_date,
    underlying_price,
    option_type,
    click_at,
    budget_rupees=15000.0,
    max_strikes=12,
):
    """
    Select the nearest-expiry, closest-to-ATM option that is actually affordable
    at the next 5m option candle open after the signal.

    Each strike is evaluated using its own point-in-time premium. No option is
    selected from future premium information.
    """
    ranked = ranked_mcx_option_contracts(
        master_rows, symbol, trade_date, underlying_price, option_type, max_strikes=max_strikes,
    )
    attempts = []
    for contract in ranked:
        history = await fetch_mcx_option_day(provider, contract, trade_date)
        candles = history.get("candles", [])
        entry = premium_entry_after_click(candles, click_at)
        if entry is None:
            attempts.append({
                "strike": contract.get("strike"),
                "trading_symbol": contract.get("trading_symbol"),
                "status": "NO_ENTRY_CANDLE",
                "candles_available": len(candles),
            })
            continue
        affordability = option_lot_affordability(
            entry["entry_price"], contract.get("lot_size"), budget_rupees,
        )
        attempts.append({
            "strike": contract.get("strike"),
            "trading_symbol": contract.get("trading_symbol"),
            "entry_at": entry.get("entry_at"),
            "entry_premium": entry.get("entry_price"),
            "lot_size": contract.get("lot_size"),
            "cost_per_lot_rupees": affordability.get("cost_per_lot_rupees"),
            "lots": affordability.get("lots"),
            "status": "AFFORDABLE" if affordability["affordable"] else "TOO_EXPENSIVE",
        })
        if affordability["affordable"]:
            return {
                "status": "SELECTED",
                "contract": contract,
                "entry": entry,
                "affordability": affordability,
                "candles": candles,
                "selection_rule": (
                    "Nearest expiry; strikes ranked from ATM outward; first strike "
                    "with at least one whole lot affordable under the daily budget."
                ),
                "attempts": attempts,
                "research_only": True,
            }
    return {
        "status": "NO_AFFORDABLE_OPTION",
        "symbol": str(symbol).upper(),
        "trade_date": str(trade_date)[:10],
        "option_type": str(option_type).upper(),
        "budget_rupees": float(budget_rupees),
        "attempts": attempts,
        "research_only": True,
    }


def _clean_candles(candles):
    rows = []
    for row in candles or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = _timestamp(row[0])
        except Exception:
            continue
        values = [_number(row[index]) for index in range(1, 5)]
        if any(value is None or value <= 0 for value in values) or values[1] < values[2]:
            continue
        volume = max(0.0, _number(row[5]) or 0.0) if len(row) > 5 else 0.0
        rows.append([stamp.isoformat(), *values, volume])
    return sorted(rows, key=lambda row: _timestamp(row[0]))


async def fetch_mcx_option_day(provider, contract, trade_date, client=None):
    day = trade_date if isinstance(trade_date, date) else datetime.fromisoformat(str(trade_date)[:10]).date()
    start = datetime.combine(day, time(9, 0), tzinfo=IST)
    end = datetime.combine(day, time(23, 30), tzinfo=IST)
    throttle = getattr(provider, "_throttle", None)
    if callable(throttle):
        await throttle()
    params = {
        "exchange": "MCX",
        "segment": "COMMODITY",
        "groww_symbol": contract["groww_symbol"],
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
        "candle_interval": "5minute",
    }
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=40)
    try:
        response = await client.get(
            f"{provider.BASE_URL}/v1/historical/candles",
            headers=await provider._headers(),
            params=params,
        )
        response.raise_for_status()
        body = response.json()
    finally:
        if owns_client:
            await client.aclose()
    payload = body.get("payload", body) if isinstance(body, dict) else {}
    candles = _clean_candles(payload.get("candles", []) if isinstance(payload, dict) else [])
    return {
        "status": "AVAILABLE" if candles else "NO_CANDLES",
        "contract": contract,
        "trade_date": day.isoformat(),
        "candles": candles,
        "candles_available": len(candles),
        "source": "GROWW_HISTORICAL_MCX_OPTION_PREMIUM",
        "research_only": True,
    }


def premium_entry_after_click(candles, click_at):
    click = _timestamp(click_at)
    rows = _clean_candles(candles)
    entry = next((row for row in rows if _timestamp(row[0]) > click), None)
    if entry is None:
        return None
    return {
        "entry_at": entry[0],
        "entry_price": round(float(entry[1]), 4),
        "entry_basis": "NEXT_5M_OPTION_CANDLE_OPEN_AFTER_CLICK",
        "no_look_ahead_signal": True,
    }


async def probe_mcx_option_history(provider, symbol, trade_date, underlying_price, option_type):
    symbol = str(symbol).upper().strip()
    option_type = str(option_type).upper().strip()
    master = await fetch_mcx_option_master([symbol])
    selected = select_mcx_option_contract(master, symbol, trade_date, underlying_price, option_type)
    if selected is None:
        return {
            "status": "CONTRACT_NOT_FOUND",
            "symbol": symbol,
            "trade_date": str(trade_date)[:10],
            "underlying_price": float(underlying_price),
            "option_type": option_type,
            "research_only": True,
        }
    history = await fetch_mcx_option_day(provider, selected, trade_date)
    return {
        "mode": "MCX_OPTION_HISTORY_PROBE_V1",
        "symbol": symbol,
        "trade_date": str(trade_date)[:10],
        "underlying_price": float(underlying_price),
        "option_type": option_type,
        "contract": selected,
        "history": history,
        "research_only": True,
        "production_rules_changed": False,
        "paper_trading_permission_changed": False,
        "live_execution_enabled": False,
    }


async def scan_mcx_option_history_band(provider, symbol, trade_date, center_price, radius=5):
    symbol = str(symbol).upper().strip()
    when = datetime.fromisoformat(str(trade_date)[:10]).date()
    center = float(center_price)
    radius = max(0, min(int(radius), 8))
    master = await fetch_mcx_option_master([symbol])
    eligible = []
    for row in master:
        expiry_date = _expiry(row.get("expiry"))
        if str(row.get("underlying") or "").upper() != symbol or expiry_date is None or expiry_date < when:
            continue
        dte = (expiry_date - when).days
        if dte <= AUTO_EXPIRY_MAX_DTE_DAYS:
            eligible.append(row)
    if not eligible:
        return {"status": "NO_ELIGIBLE_CONTRACTS", "symbol": symbol, "trade_date": when.isoformat(), "research_only": True}
    selected_expiry = min(_expiry(row["expiry"]) for row in eligible)
    expiry_rows = [row for row in eligible if _expiry(row["expiry"]) == selected_expiry]
    strikes = sorted({float(row["strike"]) for row in expiry_rows})
    center_index = min(range(len(strikes)), key=lambda index: (abs(strikes[index] - center), strikes[index]))
    selected_strikes = strikes[max(0, center_index - radius): center_index + radius + 1]
    observations = []
    for option_type in ("CE", "PE"):
        by_strike = {float(row["strike"]): row for row in expiry_rows if row.get("option_type") == option_type}
        for strike in selected_strikes:
            contract = by_strike.get(strike)
            if contract is None:
                observations.append({"option_type": option_type, "strike": strike, "status": "CONTRACT_NOT_LISTED", "candles_available": 0})
                continue
            try:
                history = await fetch_mcx_option_day(provider, contract, when)
                candles = history.get("candles", [])
                observations.append({
                    "option_type": option_type,
                    "strike": strike,
                    "trading_symbol": contract.get("trading_symbol"),
                    "groww_symbol": contract.get("groww_symbol"),
                    "status": history.get("status"),
                    "candles_available": len(candles),
                    "first_candle_at": candles[0][0] if candles else None,
                    "last_candle_at": candles[-1][0] if candles else None,
                })
            except Exception as exc:
                observations.append({
                    "option_type": option_type,
                    "strike": strike,
                    "trading_symbol": contract.get("trading_symbol"),
                    "status": "DATA_ERROR",
                    "candles_available": 0,
                    "error": f"{exc.__class__.__name__}: {str(exc)[:160]}",
                })
    available = [row for row in observations if row.get("candles_available", 0) > 0]
    return {
        "mode": "MCX_OPTION_HISTORY_BAND_SCAN_V1",
        "status": "AVAILABLE" if available else "NO_CANDLES_IN_BAND",
        "symbol": symbol,
        "trade_date": when.isoformat(),
        "center_price": center,
        "expiry": selected_expiry.isoformat(),
        "expiry_dte": (selected_expiry - when).days,
        "radius": radius,
        "strikes": selected_strikes,
        "contracts_tested": len(observations),
        "contracts_with_candles": len(available),
        "total_candles": sum(row.get("candles_available", 0) for row in available),
        "observations": observations,
        "research_only": True,
        "production_rules_changed": False,
        "paper_trading_permission_changed": False,
        "live_execution_enabled": False,
    }
