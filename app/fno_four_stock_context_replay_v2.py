"""Replay the frozen 1,600-click baseline with V2 point-in-time context."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from . import fno_candle_only_four_stock_backtest_v1 as baseline
from . import fno_four_stock_context_replay_v1 as replay_v1
from .fno_four_stock_context_enrichment_v2 import (
    CONTEXT_SYMBOLS,
    PROTOCOL_ID,
    architecture_contract,
    context_at,
    enriched_decision,
    _load_events,
)
from .fno_underlying_random_replay_v1 import _summarize


async def run_four_stock_context_replay_v2(provider, baseline_result, progress_callback=None):
    if not isinstance(baseline_result, dict) or baseline_result.get("status") != "COMPLETED":
        return {"protocol_id": PROTOCOL_ID, "status": "VALIDATED_BASELINE_REQUIRED", "safety": architecture_contract()}
    base_rows = baseline_result.get("rows") or []
    if len(base_rows) != 1600:
        return {"protocol_id": PROTOCOL_ID, "status": "BASELINE_OBSERVATION_COUNT_MISMATCH", "observations": len(base_rows), "safety": architecture_contract()}

    sessions = [
        datetime.fromisoformat(day).date()
        for days in (baseline_result.get("experiment") or {}).get("tested_sessions_by_stock", {}).values()
        for day in days
    ]
    if not sessions:
        return {"protocol_id": PROTOCOL_ID, "status": "BASELINE_SESSION_METADATA_MISSING", "safety": architecture_contract()}

    start = datetime.combine(min(sessions) - timedelta(days=7), time(9, 15), tzinfo=baseline.IST)
    end = datetime.combine(baseline.END_DATE, time(15, 30), tzinfo=baseline.IST)
    symbols = tuple(sorted(set(CONTEXT_SYMBOLS).union(baseline.STOCKS)))
    histories, errors = {}, []
    total_symbols = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        if progress_callback:
            await progress_callback({"stage":"FETCHING_CONTEXT_V2","current_symbol":symbol,"completed_symbols":index-1,"total_symbols":total_symbols})
        try:
            histories[symbol] = await baseline._chunk(provider, symbol, "15m", start, end)
            if not histories[symbol]:
                errors.append({"symbol":symbol,"error":"EMPTY_15M_CONTEXT_TAPE"})
        except Exception as exc:
            errors.append({"symbol":symbol,"error":f"{exc.__class__.__name__}: {str(exc)[:500]}"})
        if progress_callback:
            await progress_callback({"stage":"FETCHING_CONTEXT_V2","current_symbol":symbol,"completed_symbols":index,"total_symbols":total_symbols,"candles_for_symbol":len(histories.get(symbol) or []),"history_errors":len(errors)})

    required = {"NIFTY", *baseline.STOCKS}
    missing_required = sorted(symbol for symbol in required if not histories.get(symbol))
    if missing_required:
        return {"protocol_id":PROTOCOL_ID,"status":"REQUIRED_CONTEXT_DATA_INCOMPLETE","missing_required":missing_required,"history_errors":errors,"safety":architecture_contract()}

    events = _load_events()
    rows = []
    if progress_callback:
        await progress_callback({"stage":"ENRICHING_CLICKS_V2","completed_observations":0,"total_observations":len(base_rows),"event_archive_events":len(events)})
    for index, base_row in enumerate(base_rows, start=1):
        click = datetime.fromisoformat(str(base_row["click_at"]))
        symbol = str(base_row["symbol"])
        context = context_at(symbol, click, histories, histories, events)
        decision = enriched_decision(base_row["decision"], base_row["technical"], context)
        rows.append({
            "trade_date":base_row["trade_date"],
            "click_at":base_row["click_at"],
            "symbol":symbol,
            "category":base_row["category"],
            "baseline_decision":base_row["decision"],
            "decision":decision,
            "technical":base_row["technical"],
            "context":context,
            "outcome":replay_v1.outcome_for_action(base_row["outcome"], decision["action"]),
        })
        if progress_callback and (index % 100 == 0 or index == len(base_rows)):
            await progress_callback({"stage":"ENRICHING_CLICKS_V2","completed_observations":index,"total_observations":len(base_rows)})

    summary = _summarize(rows)
    trade_dates = sorted({row["trade_date"] for row in rows})
    result = {
        "protocol_id":PROTOCOL_ID,
        "status":"COMPLETED",
        "experiment":{
            "baseline_protocol":baseline_result.get("protocol_id"),
            "baseline_observations":len(base_rows),
            "observations":len(rows),
            "same_frozen_clicks":True,
            "same_frozen_future_tape":True,
            "stocks":(baseline_result.get("experiment") or {}).get("stocks"),
            "sessions_by_stock":(baseline_result.get("experiment") or {}).get("tested_sessions_by_stock"),
        },
        "baseline_summary":baseline_result.get("summary") or {},
        "enriched_summary":summary,
        "comparison":replay_v1._comparison(rows),
        "context_audit":replay_v1._context_audit(rows, errors, len(events)),
        "by_stock":{
            symbol:{
                "baseline":(baseline_result.get("by_stock") or {}).get(symbol),
                "enriched":_summarize([row for row in rows if row["symbol"] == symbol]),
                "comparison":replay_v1._comparison([row for row in rows if row["symbol"] == symbol]),
            }
            for symbol in baseline.STOCKS
        },
        "by_trade_date":{day:_summarize([row for row in rows if row["trade_date"] == day]) for day in trade_dates},
        "context_data_coverage":{
            symbol:{"candles":len(histories.get(symbol) or []),"first":str((histories.get(symbol) or [[None]])[0][0]) if histories.get(symbol) else None,"last":str((histories.get(symbol) or [[None]])[-1][0]) if histories.get(symbol) else None}
            for symbol in symbols
        },
        "rows":rows,
        "methodology":{
            "context_timeframe":"15m",
            "context_momentum_lookback_minutes":60,
            "market_context":["NIFTY","BANKNIFTY_FOR_SBIN"],
            "sector_context":"FROZEN_CASH_PEER_BASKETS_PLUS_TIMESTAMPED_SECTOR_EVENTS",
            "stock_relative_strength_vs_nifty":True,
            "point_in_time_event_archive":"V2",
            "event_unknown_time_policy":"NEXT_SESSION_OPEN",
            "future_tape_reused_from_validated_baseline":True,
            "outcome_refetch_forbidden":True,
            "decision_thresholds_identical_to_v1":True,
            "thresholds_retuned_from_v1_results":False,
        },
        "safety":architecture_contract(),
    }
    if progress_callback:
        await progress_callback({"stage":"SUMMARIZING_V2","observations":len(rows)})
    return result
