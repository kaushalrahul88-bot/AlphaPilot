from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta

import httpx
from zoneinfo import ZoneInfo

from .commodities import MCX_TICK_SIZE_RUPEES, _download_instrument_master_to_tempfile, _parse_expiry, SUPPORTED_COMMODITIES, analyze_commodity_candles
from .commodity_backtest import _fetch_chunked, _slice_until, _plan_at, _resolve_trade, _summary, _ts
from .mcx_calendar import mcx_metal_day_schedule

IST = ZoneInfo("Asia/Kolkata")


def _matches(row, symbol):
    if str(row.get("exchange") or "").upper() != "MCX": return False
    if str(row.get("segment") or "").upper() != "COMMODITY": return False
    trading_symbol = str(row.get("trading_symbol") or "").upper()
    underlying = str(row.get("underlying_symbol") or row.get("name") or "").upper().replace(" ", "")
    instrument_type = str(row.get("instrument_type") or "").upper()
    if underlying != symbol and not trading_symbol.startswith(symbol): return False
    if instrument_type not in {"FUT", "FUTURE", "FUTURES"} and not trading_symbol.endswith("FUT"): return False
    return bool(_parse_expiry(row.get("expiry_date")))


def _weekday_count(start, end):
    """Count MCX metal trading days, excluding weekends and full exchange holidays."""
    current = start.date()
    finish = end.date()
    count = 0
    while current <= finish:
        if mcx_metal_day_schedule(current)["expected_open"]:
            count += 1
        current += timedelta(days=1)
    return count


def _month_keys(start, end):
    current = start.replace(day=1)
    finish = end.replace(day=1)
    out = []
    while current <= finish:
        out.append((current.year, current.month))
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def _historical_future_contract(symbol, groww_symbol):
    """Parse Groww's canonical historical FUT identifier into an MCX contract."""
    raw = str(groww_symbol or "").strip()
    pattern = rf"^MCX-{re.escape(symbol)}-(\d{{2}}[A-Za-z]{{3}}\d{{2}})-FUT$"
    match = re.match(pattern, raw, re.IGNORECASE)
    if not match:
        return None
    try:
        expiry = datetime.strptime(match.group(1), "%d%b%y").date()
    except ValueError:
        return None
    compact = expiry.strftime("%d%b%y").upper()
    return {
        "underlying": symbol,
        "exchange": "MCX",
        "segment": "COMMODITY",
        "trading_symbol": f"{symbol}{compact}FUT",
        "groww_symbol": raw,
        "expiry_date": expiry.isoformat(),
        "lot_size": None,
        "tick_size": MCX_TICK_SIZE_RUPEES[symbol],
        "provider_tick_size_raw": None,
        "tick_size_source": "MCX_CONTRACT_SPECIFICATION_2026",
        "instrument_type": "FUT",
        "discovery_source": "GROWW_HISTORICAL_CONTRACTS_API",
    }


async def discover_groww_historical_mcx_contracts(provider, symbol, start, end):
    """
    Ask Groww's historical expiry/contract APIs for genuine archived MCX futures.

    Groww documents these APIs for NSE/BSE F&O. MCX support is therefore probed
    at runtime and never assumed. If MCX is rejected or returns no futures,
    supported=False and callers must fail closed rather than reuse today's
    instrument master for historical dates.
    """
    symbol = str(symbol or "").strip().upper()
    if symbol not in SUPPORTED_COMMODITIES:
        raise ValueError(f"Unsupported commodity {symbol}")
    start = _ts(start).astimezone(IST)
    end = _ts(end).astimezone(IST)
    query_start = start - timedelta(days=45)
    query_end = end + timedelta(days=75)
    expiries = set()
    diagnostics = []
    headers = await provider._headers()
    async with httpx.AsyncClient(timeout=30) as client:
        for year, month in _month_keys(query_start, query_end):
            response = await client.get(
                f"{provider.BASE_URL}/v1/historical/expiries",
                headers=headers,
                params={
                    "exchange": "MCX",
                    "underlying_symbol": symbol,
                    "year": year,
                    "month": month,
                },
            )
            if response.status_code != 200:
                diagnostics.append({
                    "endpoint": "expiries",
                    "year": year,
                    "month": month,
                    "status_code": response.status_code,
                })
                continue
            try:
                payload = response.json().get("payload", {})
            except Exception:
                payload = {}
            for value in payload.get("expiries", []) if isinstance(payload, dict) else []:
                try:
                    expiry = datetime.fromisoformat(str(value)[:10]).date()
                except ValueError:
                    continue
                if query_start.date() <= expiry <= query_end.date():
                    expiries.add(expiry)

        contracts = []
        for expiry in sorted(expiries):
            response = await client.get(
                f"{provider.BASE_URL}/v1/historical/contracts",
                headers=headers,
                params={
                    "exchange": "MCX",
                    "underlying_symbol": symbol,
                    "expiry_date": expiry.isoformat(),
                },
            )
            if response.status_code != 200:
                diagnostics.append({
                    "endpoint": "contracts",
                    "expiry_date": expiry.isoformat(),
                    "status_code": response.status_code,
                })
                continue
            try:
                payload = response.json().get("payload", {})
            except Exception:
                payload = {}
            raw_contracts = payload.get("contracts", []) if isinstance(payload, dict) else []
            for value in raw_contracts:
                parsed = _historical_future_contract(symbol, value)
                if parsed:
                    contracts.append(parsed)

    unique = {row["groww_symbol"]: row for row in contracts}
    ordered = sorted(unique.values(), key=lambda row: row["expiry_date"])
    return {
        "supported": bool(ordered),
        "source": "GROWW_HISTORICAL_CONTRACTS_API",
        "contracts": ordered,
        "expiries_returned": len(expiries),
        "diagnostics": diagnostics,
    }


async def discover_current_mcx_contracts(symbol):
    """Current instrument-master contracts only; never proof of expired history."""
    symbol = str(symbol or "").strip().upper()
    if symbol not in SUPPORTED_COMMODITIES: raise ValueError(f"Unsupported commodity {symbol}")
    path = await _download_instrument_master_to_tempfile(); contracts = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if not _matches(row, symbol): continue
                expiry = _parse_expiry(row.get("expiry_date"))
                raw_tick=float(row.get("tick_size") or 0) if str(row.get("tick_size") or "").strip() else None
                contracts.append({"underlying":symbol,"exchange":"MCX","segment":"COMMODITY","trading_symbol":str(row.get("trading_symbol") or ""),"groww_symbol":str(row.get("groww_symbol") or ""),"expiry_date":expiry.isoformat(),"lot_size":int(float(row.get("lot_size") or 0)) if str(row.get("lot_size") or "").strip() else None,"tick_size":MCX_TICK_SIZE_RUPEES[symbol],"provider_tick_size_raw":raw_tick,"tick_size_source":"MCX_CONTRACT_SPECIFICATION_2026","instrument_type":str(row.get("instrument_type") or "FUT"),"discovery_source":"GROWW_CURRENT_INSTRUMENT_MASTER"})
    finally:
        import os
        try: os.remove(path)
        except OSError: pass
    unique = {c["trading_symbol"]: c for c in contracts if c["trading_symbol"]}
    return sorted(unique.values(), key=lambda c: c["expiry_date"])


async def discover_mcx_contracts(symbol, provider=None, start=None, end=None):
    """
    Historical-safe MCX discovery.

    For historical work, a provider and explicit range are required and only the
    provider's historical contract archive is accepted. Without those arguments
    this returns current instrument-master contracts for live/current use.
    """
    if provider is not None and start is not None and end is not None:
        result = await discover_groww_historical_mcx_contracts(provider, symbol, start, end)
        return result["contracts"]
    return await discover_current_mcx_contracts(symbol)


async def _run_contract_window(provider, symbol, contract, window_start, window_end, min_rr, strength_threshold, slippage_bps, cost_bps):
    fetch_start = window_start - timedelta(days=14)
    data = {"5m":await _fetch_chunked(provider,contract,5,fetch_start,window_end),"15m":await _fetch_chunked(provider,contract,15,fetch_start,window_end),"1h":await _fetch_chunked(provider,contract,60,fetch_start,window_end)}
    checkpoints = [_ts(row[0]) for row in data["15m"] if window_start <= _ts(row[0]) <= window_end]
    trades=[]; busy_until=None
    for when in checkpoints:
        if busy_until and when <= busy_until: continue
        frames={}; valid=True
        for tf in ("5m","15m","1h"):
            history=_slice_until(data[tf],when)
            if len(history)<60: valid=False; break
            frames[tf]=analyze_commodity_candles(symbol,history,min_rr)
        if not valid: continue
        plan=_plan_at(symbol,frames,min_rr,strength_threshold)
        if not plan: continue
        future_rows=[row for row in data["5m"] if _ts(row[0])<=window_end]
        outcome=_resolve_trade(plan,future_rows,when,slippage_bps,cost_bps)
        trades.append({**plan,**outcome,"entry_time":when.isoformat(),"contract":contract["trading_symbol"],"contract_expiry":contract["expiry_date"],"timeframe_signals":{tf:frames[tf].get("signal") for tf in frames},"timeframe_alpha":{tf:float(frames[tf].get("alpha_score") or 50) for tf in frames}})
        if outcome.get("exit_time"): busy_until=datetime.fromisoformat(outcome["exit_time"])
        else: busy_until=window_end
    five=data["5m"]
    in_window_five=[r for r in five if window_start <= _ts(r[0]) <= window_end]
    raw_start=min((_ts(r[0]) for r in five),default=None); raw_end=max((_ts(r[0]) for r in five),default=None)
    effective_start=min((_ts(r[0]) for r in in_window_five),default=None)
    effective_end=max((_ts(r[0]) for r in in_window_five),default=None)
    effective_days=max(0.0,(effective_end-effective_start).total_seconds()/86400) if effective_start and effective_end and effective_end>effective_start else 0.0
    observed_dates=sorted({_ts(r[0]).date().isoformat() for r in in_window_five})
    return {"contract":contract,"window_start":window_start.isoformat(),"window_end":window_end.isoformat(),"coverage_start":raw_start.isoformat() if raw_start else None,"coverage_end":raw_end.isoformat() if raw_end else None,"effective_start":effective_start.isoformat() if effective_start else None,"effective_end":effective_end.isoformat() if effective_end else None,"effective_days":round(effective_days,2),"observed_session_days":len(observed_dates),"observed_dates":observed_dates,"candles":{tf:len(rows) for tf,rows in data.items()},"trades":trades}


async def run_continuous_commodity_backtest(provider, symbol, days=180, min_rr=1.5, strength_threshold=65.0, slippage_bps=2.0, cost_bps=2.0):
    symbol=str(symbol or "").strip().upper(); days=max(30,min(int(days),365)); requested_end=datetime.now(IST); requested_start=requested_end-timedelta(days=days)
    historical_discovery=await discover_groww_historical_mcx_contracts(provider,symbol,requested_start,requested_end)
    contracts=historical_discovery["contracts"]
    selected=[]
    for c in contracts:
        expiry=datetime.fromisoformat(c["expiry_date"]).replace(tzinfo=IST)
        if expiry>=requested_start-timedelta(days=7) and expiry<=requested_end+timedelta(days=120): selected.append(c)

    used=[]; skipped=[]; all_trades=[]; previous_expiry=None; observed_dates=set()
    for contract in selected:
        expiry=datetime.fromisoformat(contract["expiry_date"]).replace(tzinfo=IST).replace(hour=23,minute=30)
        natural_start=requested_start if previous_expiry is None else previous_expiry+timedelta(seconds=1)
        window_start=max(requested_start,natural_start); window_end=min(requested_end,expiry); previous_expiry=expiry
        if window_end<=window_start: continue
        try:
            result=await _run_contract_window(provider,symbol,contract,window_start,window_end,min_rr,strength_threshold,slippage_bps,cost_bps)
            if result["candles"].get("5m",0)==0 or result.get("observed_session_days",0)<=0:
                skipped.append({"contract":contract["trading_symbol"],"expiry":contract["expiry_date"],"reason":"No usable 5m candles in assigned front-month window"}); continue
            observed_dates.update(result.get("observed_dates",[]))
            used.append({"contract":contract["trading_symbol"],"expiry":contract["expiry_date"],"window_start":result["window_start"],"window_end":result["window_end"],"effective_start":result["effective_start"],"effective_end":result["effective_end"],"effective_days":result["effective_days"],"observed_session_days":result["observed_session_days"],"candles":result["candles"],"trades":len(result["trades"])})
            all_trades.extend(result["trades"])
        except Exception as exc:
            skipped.append({"contract":contract["trading_symbol"],"expiry":contract["expiry_date"],"reason":str(exc)[:300]})

    all_trades.sort(key=lambda t:t["entry_time"])
    span_days=round(sum(float(x.get("effective_days") or 0) for x in used),1)
    observed_session_days=len(observed_dates)
    requested_weekdays=_weekday_count(requested_start,requested_end)
    historical_coverage_pct=round(min(100.0,(observed_session_days/requested_weekdays*100.0) if requested_weekdays else 0.0),1)
    distinct_contracts=len({x["contract"] for x in used})
    rollover_valid=distinct_contracts>=2
    classification="VALID_CONTINUOUS_ROLLOVER" if rollover_valid else "SINGLE_CONTRACT_EXTENDED_HISTORY" if distinct_contracts==1 else "INSUFFICIENT_HISTORY"
    confidence="HIGH" if rollover_valid and historical_coverage_pct>=80 else "MEDIUM" if rollover_valid and historical_coverage_pct>=50 else "LOW"
    actual_start=min((datetime.fromisoformat(x["effective_start"]) for x in used if x.get("effective_start")),default=None)
    actual_end=max((datetime.fromisoformat(x["effective_end"]) for x in used if x.get("effective_end")),default=None)
    rollover_method="EXPIRY_BOUNDARY_FRONT_MONTH"

    limitations=[
        "Historical contract discovery uses Groww's historical expiry/contracts APIs and never treats the current instrument master as an archive of expired MCX contracts.",
        "Contract handoff is expiry-boundary based: AlphaPilot keeps each discovered contract through its expiry window and starts the next contract immediately after the prior expiry. This is a deterministic synthetic front-month series, not a volume/open-interest based institutional rollover model.",
        "Historical span, observed session-day coverage and rollover validity are reported separately; elapsed time between first and last candle is not treated as proof that every trading day is present.",
        "Coverage percentage uses official MCX metal trading-calendar days; weekends and fully closed exchange holidays are excluded from the denominator, while partial-session holidays remain expected trading days.",
        "Coverage depends on expired MCX contracts still being discoverable through Groww's current instrument master and historical candle API.",
        "AlphaPilot requires at least two distinct contracts before labeling a result as a valid continuous rollover backtest.",
        "The frozen baseline exits 100% at T1; brokerage, taxes and slippage remain basis-point approximations.",
    ]
    if not rollover_valid and distinct_contracts==1:
        limitations.insert(0,"Only one contract contributed. Treat performance as single-contract extended history, NOT as continuous-contract evidence.")

    return {"mode":"MCX_CONTINUOUS_AVAILABLE_CONTRACTS","historical_contract_discovery":historical_discovery,"rollover_method":rollover_method,"classification":classification,"rollover_valid":rollover_valid,"coverage_confidence":confidence,"symbol":symbol,"requested_days":days,"requested_start":requested_start.isoformat(),"requested_end":requested_end.isoformat(),"actual_start":actual_start.isoformat() if actual_start else None,"actual_end":actual_end.isoformat() if actual_end else None,"coverage_days":span_days,"coverage_ratio_pct":historical_coverage_pct,"historical_coverage_days":observed_session_days,"historical_coverage_pct":historical_coverage_pct,"requested_weekdays":requested_weekdays,"observed_session_days":observed_session_days,"contracts_discovered":len(contracts),"contracts_selected":len(selected),"contracts_used":used,"contracts_skipped":skipped,"summary":_summary(all_trades),"by_action":{"BUY":_summary([t for t in all_trades if t["action"]=="BUY"]),"SELL":_summary([t for t in all_trades if t["action"]=="SELL"])},"trades":all_trades[-300:],"limitations":limitations}
