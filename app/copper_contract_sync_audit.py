from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from .commodity_backtest import _fetch_chunked, _ts
from .commodity_candle_collector import _records
from .commodity_option_history import (
    fetch_mcx_option_day,
    fetch_mcx_option_master,
    select_mcx_option_contract,
)
from .commodities import resolve_nearest_mcx_future
from .mcx_calendar import mcx_metal_day_schedule

IST=ZoneInfo("Asia/Kolkata")


def _day_rows(rows):
    out=defaultdict(list)
    for row in rows or []:
        if not isinstance(row,(list,tuple)) or len(row)<5:
            continue
        try:
            stamp=_ts(row[0])
        except Exception:
            continue
        out[stamp.date()].append(list(row))
    for day in out:
        out[day].sort(key=lambda r:_ts(r[0]))
    return out


def _timestamps(rows):
    return {_ts(row[0]).replace(second=0,microsecond=0).isoformat() for row in rows or []}


def _day_quality(day, provider_rows, stored_rows):
    schedule=mcx_metal_day_schedule(day)
    p=list(provider_rows or [])
    s=list(stored_rows or [])
    expected=int(schedule["expected_5m_bars"])
    p_ts=_timestamps(p)
    s_ts=_timestamps(s)
    shared=len(p_ts & s_ts)
    provider_count=len(p_ts)
    stored_count=len(s_ts)
    first=_ts(p[0][0]) if p else None
    last=_ts(p[-1][0]) if p else None
    if not schedule["expected_open"]:
        state="CLOSED_AS_EXPECTED" if not p and not s else "UNEXPECTED_DATA_ON_CLOSED_DAY"
    elif not p:
        state="MISSING_PROVIDER_DATA"
    elif not s:
        state="MISSING_STORED_DATA"
    elif p_ts != s_ts:
        state="STORE_PROVIDER_MISMATCH"
    else:
        coverage=(provider_count/expected*100.0) if expected else 100.0
        state="SYNCED_COMPLETE" if coverage>=95.0 else "SYNCED_PARTIAL_SESSION"
    return {
        **schedule,
        "provider_5m_bars":provider_count,
        "stored_5m_bars":stored_count,
        "provider_expected_coverage_pct":round(provider_count/expected*100.0,2) if expected else 100.0,
        "store_provider_timestamp_match_pct":round(shared/provider_count*100.0,2) if provider_count else (100.0 if stored_count==0 else 0.0),
        "provider_first_at":first.isoformat() if first else None,
        "provider_last_at":last.isoformat() if last else None,
        "sync_state":state,
    }


async def audit_copper_aug26_contract_window(provider, store):
    """Sync and audit current 31-Aug Copper FUT reference + basic CE/PE history availability.

    Scope is the August expiry month through the last completed Friday (28-Aug-2026).
    Futures are reference data only; option history is checked solely for options-only replay readiness.
    """
    contract=await resolve_nearest_mcx_future("COPPER",force=True)
    expiry=str(contract.get("expiry_date") or "")
    if expiry!="2026-08-31":
        return {
            "mode":"COPPER_AUG26_CONTRACT_SYNC_AUDIT_V1",
            "status":"EXPECTED_CONTRACT_NOT_ACTIVE",
            "expected_expiry":"2026-08-31",
            "resolved_contract":contract,
            "research_only":True,
            "trade_instrument":"OPTIONS",
        }

    fetch_start=datetime(2026,8,1,9,0,tzinfo=IST)
    fetch_end=datetime(2026,8,28,23,30,tzinfo=IST)
    calendar_end=date(2026,8,30)

    provider_rows=await _fetch_chunked(provider,contract,5,fetch_start,fetch_end)
    provider_rows=[
        row for row in provider_rows
        if fetch_start<=_ts(row[0])<=fetch_end
    ]

    # Make the durable store a byte-for-time equivalent of all valid provider rows
    # available for this exact contract/window. Upsert is idempotent.
    await store.initialize()
    sync_records=_records("COPPER",contract,5,provider_rows,datetime.now(IST))
    upserted=await store.upsert(sync_records)

    segments=await store.read_symbol_contract_segments("COPPER",5,fetch_start,fetch_end)
    stored_rows=[]
    for segment in segments:
        if str(segment.get("trading_symbol") or "")==str(contract.get("trading_symbol") or ""):
            stored_rows.extend(segment.get("candles") or [])

    provider_by_day=_day_rows(provider_rows)
    stored_by_day=_day_rows(stored_rows)

    days=[]
    cursor=fetch_start.date()
    while cursor<=calendar_end:
        days.append(_day_quality(cursor,provider_by_day.get(cursor,[]),stored_by_day.get(cursor,[])))
        cursor+=timedelta(days=1)

    expected_days=[d for d in days if d["expected_open"] and d["date"]<=fetch_end.date().isoformat()]
    closed_days=[d for d in days if not d["expected_open"]]
    sync_failures=[d for d in expected_days if d["sync_state"] not in {"SYNCED_COMPLETE","SYNCED_PARTIAL_SESSION"}]
    partial_days=[d for d in expected_days if d["sync_state"]=="SYNCED_PARTIAL_SESSION"]

    # Basic historical-option feasibility: for each expected trading day, use that
    # day's last point-in-time underlying close and test nearest CE + PE from the
    # currently authoritative listed option universe. This does NOT prove a full
    # historical option universe; it is a minimum-data gate only.
    option_master=await fetch_mcx_option_master(["COPPER"])
    option_days=[]
    async with httpx.AsyncClient(timeout=40) as client:
        for item in expected_days:
            day=date.fromisoformat(item["date"])
            rows=provider_by_day.get(day,[])
            if not rows:
                option_days.append({
                    "date":item["date"],
                    "status":"NO_UNDERLYING_REFERENCE",
                    "ce":None,
                    "pe":None,
                })
                continue
            underlying_close=float(rows[-1][4])
            result={
                "date":item["date"],
                "underlying_reference_close":underlying_close,
                "underlying_reference_only":True,
                "ce":None,
                "pe":None,
            }
            for option_type,key in (("CE","ce"),("PE","pe")):
                selected=select_mcx_option_contract(
                    option_master,"COPPER",day,underlying_close,option_type,
                )
                if selected is None:
                    result[key]={
                        "status":"NO_POINT_IN_TIME_ELIGIBLE_CONTRACT_FROM_CURRENT_MASTER",
                        "candles":0,
                    }
                    continue
                try:
                    history=await fetch_mcx_option_day(provider,selected,day,client=client)
                    candles=history.get("candles") or []
                    result[key]={
                        "status":history.get("status"),
                        "expiry":selected.get("expiry"),
                        "strike":selected.get("strike"),
                        "trading_symbol":selected.get("trading_symbol"),
                        "candles":len(candles),
                        "first_at":candles[0][0] if candles else None,
                        "last_at":candles[-1][0] if candles else None,
                    }
                except Exception as exc:
                    result[key]={
                        "status":"DATA_ERROR",
                        "expiry":selected.get("expiry"),
                        "strike":selected.get("strike"),
                        "trading_symbol":selected.get("trading_symbol"),
                        "candles":0,
                        "error":f"{exc.__class__.__name__}: {str(exc)[:160]}",
                    }
            ce_ok=(result["ce"] or {}).get("candles",0)>0
            pe_ok=(result["pe"] or {}).get("candles",0)>0
            result["status"]="BOTH_OPTION_SIDES_AVAILABLE" if ce_ok and pe_ok else "OPTION_HISTORY_INCOMPLETE"
            option_days.append(result)

    option_complete_days=sum(1 for x in option_days if x.get("status")=="BOTH_OPTION_SIDES_AVAILABLE")
    option_data_ready=bool(option_days) and option_complete_days==len(option_days)
    underlying_sync_ready=not sync_failures and bool(expected_days)
    full_backtest_ready=underlying_sync_ready and option_data_ready

    return {
        "mode":"COPPER_AUG26_CONTRACT_SYNC_AUDIT_V1",
        "status":"READY_FOR_OPTIONS_BACKTEST" if full_backtest_ready else "DATA_GAPS_BLOCK_OPTIONS_BACKTEST",
        "scope":"2026-08 expiry month through Friday 2026-08-28; weekend 29-30 included for calendar verification",
        "trade_instrument":"OPTIONS",
        "futures_role":"REFERENCE_ONLY",
        "resolved_reference_contract":contract,
        "provider":"GROWW",
        "sync":{
            "provider_5m_rows":len(provider_rows),
            "valid_rows_upserted":upserted,
            "stored_5m_rows":len(stored_rows),
            "expected_trading_days":len(expected_days),
            "sync_failures":len(sync_failures),
            "partial_sessions":len(partial_days),
            "underlying_sync_ready":underlying_sync_ready,
        },
        "calendar":{
            "source":"MCX_OFFICIAL_2026_TRADING_HOLIDAY_SCHEDULE",
            "august_declared_trading_holidays":[],
            "weekend_days":[d["date"] for d in closed_days if d["calendar_class"]=="WEEKEND"],
            "days":days,
        },
        "historical_option_minimum_gate":{
            "method":"Nearest point-in-time CE and PE using each trading day's last underlying close; current listed master only, then exact Groww 5m historical option candles.",
            "warning":"This is a minimum availability audit, not proof that the complete historical option universe is reconstructible without survivorship bias.",
            "days_tested":len(option_days),
            "days_with_both_ce_pe_history":option_complete_days,
            "option_history_ready_for_full_window":option_data_ready,
            "days":option_days,
        },
        "ready_for_options_backtest":full_backtest_ready,
        "research_only":True,
        "production_rules_changed":False,
        "live_execution_enabled":False,
    }
