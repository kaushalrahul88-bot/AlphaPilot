"""Candle-only four-stock, 20-session random-click F&O edge replay."""
from __future__ import annotations

import hashlib
import math
import random
import statistics
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Mapping
from zoneinfo import ZoneInfo

import httpx

from . import fno_15m_historical_replay_v1 as core
from .fno_15m_candle_checkpoint_v2 import _is_auth_error, _refresh_after_401
from .fno_underlying_random_replay_v1 import (
    _decision_snapshot,
    _summarize,
    resolve_underlying_path,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

PROTOCOL_ID = "FNO_CANDLE_ONLY_FOUR_STOCK_20D_V1_2026-09-06"
END_DATE = date(2026, 9, 4)
FROZEN_STOCKS = (
    ("ONGC", "ENERGY"),
    ("LTIM", "INFORMATION_TECHNOLOGY"),
    ("SBIN", "BANKING"),
    ("SUNPHARMA", "PHARMACEUTICALS"),
)
STOCKS = tuple(symbol for symbol, _ in FROZEN_STOCKS)
TIMEFRAMES = ("5m", "15m", "1h")
INTERVAL = {"5m": "5minute", "15m": "15minute", "1h": "1hour"}
TF_MIN = {"5m": 5, "15m": 15, "1h": 60}
CHUNK_DAYS = {"5m": 6, "15m": 13, "1h": 55}

DISCOVERY_LOOKBACK_DAYS = 55
CLICKS_PER_DAY = 20
CLICK_START = time(9, 30)
CLICK_END = time(14, 0)
CLICK_STEP = 5

ENGINE_KEYS = (
    "alpha_score", "signal", "price", "latest_candle_at", "family_scores",
    "ema9", "ema20", "ema50", "ema200", "vwap", "rsi14", "macd",
    "macd_signal", "macd_hist", "atr14", "bollinger_upper",
    "bollinger_mid", "bollinger_lower", "volume_ratio_raw",
    "volume_ratio_capped", "market_structure", "recent_support",
    "recent_resistance", "distance_to_resistance_atr",
    "distance_to_support_atr", "candle_pattern", "confirmations", "reasons",
    "warnings", "clean_candles",
)


def _stamp(value):
    return core._stamp(value)


def _merge(rows):
    return core._merge_candles(rows)


def _num(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


async def _chunk(provider, symbol, timeframe, start, end):
    exchange, segment, _, groww_symbol = provider._instrument(symbol)
    if (exchange, segment) != ("NSE", "CASH"):
        raise RuntimeError(f"{symbol} not NSE/CASH")

    async def call(a, b):
        throttle = getattr(provider, "_throttle", None)
        if callable(throttle):
            await throttle()
        async with httpx.AsyncClient(timeout=45) as client:
            return await client.get(
                f"{provider.BASE_URL}/v1/historical/candles",
                headers=await provider._headers(),
                params={
                    "exchange": exchange,
                    "segment": segment,
                    "groww_symbol": groww_symbol,
                    "start_time": a.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                    "end_time": b.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                    "candle_interval": INTERVAL[timeframe],
                },
            )

    rows = []
    cursor = start.astimezone(IST)
    end = end.astimezone(IST)
    step = timedelta(minutes=TF_MIN[timeframe])

    while cursor <= end:
        batch_end = min(
            end,
            cursor + timedelta(days=CHUNK_DAYS[timeframe]) - step,
        )
        response = await call(cursor, batch_end)

        if response.status_code == 429:
            register = getattr(provider, "_register_rate_limit", None)
            if callable(register):
                await register()

        if response.status_code == 401:
            try:
                await _refresh_after_401(provider)
            except Exception:
                response.raise_for_status()
            response = await call(cursor, batch_end)

        try:
            response.raise_for_status()
        except Exception as exc:
            if _is_auth_error(exc):
                raise RuntimeError("GROWW_CANDLE_ONLY_BACKTEST_AUTH_FAILED") from exc
            raise

        body = response.json()
        payload = body.get("payload", body) if isinstance(body, Mapping) else {}
        if isinstance(payload, Mapping):
            rows.extend(payload.get("candles", []))

        if batch_end >= end:
            break
        cursor = batch_end + step

    return _merge(rows)


def _dates(candles):
    dates = set()
    for row in candles:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        stamp = _stamp(row[0])
        if not stamp:
            continue
        local = stamp.astimezone(IST)
        if time(9, 15) <= local.time() <= time(15, 30):
            dates.add(local.date())
    return dates


def last_20_sessions_by_stock(five):
    """Select each stock's own last 20 sessions; no cross-stock intersection gate."""
    return {
        symbol: sorted(
            session
            for session in _dates(five.get(symbol, []))
            if session <= END_DATE
        )[-20:]
        for symbol in STOCKS
    }


def common_last_20_sessions(five):
    """Diagnostic only: the latest 20 sessions common to every frozen stock."""
    sets = [_dates(five.get(symbol, [])) for symbol in STOCKS]
    if any(not sessions for sessions in sets):
        return []
    return sorted(
        session
        for session in set.intersection(*sets)
        if session <= END_DATE
    )[-20:]


def deterministic_clicks(day):
    pool = []
    cursor = datetime.combine(day, CLICK_START, tzinfo=IST)
    end = datetime.combine(day, CLICK_END, tzinfo=IST)
    while cursor <= end:
        pool.append(cursor.astimezone(UTC))
        cursor += timedelta(minutes=CLICK_STEP)

    seed = int.from_bytes(
        hashlib.sha256(f"{PROTOCOL_ID}:{day.isoformat()}".encode()).digest()[:8],
        "big",
    )
    return sorted(random.Random(seed).sample(pool, CLICKS_PER_DAY))


def _audit(technical):
    timeframes = technical.get("timeframes") or {}
    detail = {}
    all_core = True
    numeric_keys = {
        "alpha_score", "price", "ema9", "ema20", "ema50", "ema200", "vwap",
        "rsi14", "macd", "macd_signal", "macd_hist", "atr14",
        "bollinger_upper", "bollinger_mid", "bollinger_lower",
        "volume_ratio_raw", "volume_ratio_capped", "recent_support",
        "recent_resistance", "distance_to_resistance_atr",
        "distance_to_support_atr", "clean_candles",
    }

    for timeframe in TIMEFRAMES:
        payload = timeframes.get(timeframe) or {}
        flags = {}
        for key in ENGINE_KEYS:
            value = payload.get(key)
            flags[key] = _num(value) if key in numeric_keys else value is not None

        core_complete = (
            str(payload.get("status") or "") != "ERROR"
            and int(payload.get("clean_candles") or 0) >= 60
            and all(
                flags.get(key)
                for key in (
                    "alpha_score", "ema20", "ema50", "vwap", "rsi14",
                    "macd_hist", "atr14", "market_structure",
                    "recent_support", "recent_resistance",
                )
            )
        )
        detail[timeframe] = {
            "core_complete": core_complete,
            "clean_candles": payload.get("clean_candles"),
            "feature_flags": flags,
        }
        all_core &= core_complete

    return {
        "all_three_timeframes_core_complete": bool(all_core),
        "timeframes": detail,
    }


def _future_bars(candles, click):
    eod = datetime.combine(
        click.astimezone(IST).date(), time(15, 30), tzinfo=IST
    ).astimezone(UTC)
    output = []
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        stamp = _stamp(row[0])
        if stamp and click <= stamp and stamp + timedelta(minutes=5) <= eod:
            output.append((stamp, list(row)))
    return [row for _, row in sorted(output, key=lambda item: item[0])]


def _barrier(candles, click, decision):
    if decision.get("action") not in {"LONG", "SHORT"}:
        return {"status": "NOT_ACTIONABLE"}

    values = [
        decision.get("model_entry"), decision.get("model_stop_loss"),
        decision.get("model_target1"), decision.get("model_target2"),
    ]
    if not all(_num(value) for value in values):
        return {"status": "MODEL_LEVELS_UNAVAILABLE"}

    entry, stop, target1, target2 = map(float, values)
    direction = decision["action"]
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0:
        return {"status": "INVALID_MODEL_RISK"}

    first_stop = first_target1 = first_target2 = None
    bars = _future_bars(candles, click)
    for row in bars:
        stamp = _stamp(row[0])
        high = float(row[2])
        low = float(row[3])
        stop_hit = low <= stop if direction == "LONG" else high >= stop
        target1_hit = high >= target1 if direction == "LONG" else low <= target1
        target2_hit = high >= target2 if direction == "LONG" else low <= target2
        if stop_hit and first_stop is None:
            first_stop = stamp
        if target1_hit and first_target1 is None:
            first_target1 = stamp
        if target2_hit and first_target2 is None:
            first_target2 = stamp

    if first_stop and first_target1 and first_stop == first_target1:
        first = "AMBIGUOUS_SL_T1_SAME_5M_BAR"
    elif first_target1 and (not first_stop or first_target1 < first_stop):
        first = "T1_BEFORE_SL"
    elif first_stop and (not first_target1 or first_stop < first_target1):
        first = "SL_BEFORE_T1"
    else:
        first = "NEITHER"

    mtm = None
    if bars:
        close = float(bars[-1][4])
        mtm = ((close - entry) if direction == "LONG" else (entry - close)) / risk

    return {
        "status": "RESOLVED" if bars else "NO_FUTURE_BARS",
        "first_barrier": first,
        "t2_before_sl": bool(
            first_target2 and (not first_stop or first_target2 < first_stop)
        ),
        "eod_mtm_r": round(mtm, 6) if mtm is not None else None,
    }


def _barrier_summary(rows):
    actionable = [
        row for row in rows if row["decision"]["action"] in {"LONG", "SHORT"}
    ]
    counts = Counter(
        row["barrier"]["first_barrier"]
        for row in actionable
        if row["barrier"].get("status") == "RESOLVED"
    )
    clean = counts["T1_BEFORE_SL"] + counts["SL_BEFORE_T1"]
    mtm = [
        float(row["barrier"]["eod_mtm_r"])
        for row in actionable
        if row["barrier"].get("eod_mtm_r") is not None
    ]
    return {
        **dict(counts),
        "clean_t1_before_sl_rate_pct": (
            round(100 * counts["T1_BEFORE_SL"] / clean, 4) if clean else None
        ),
        "t2_before_sl_count": sum(
            bool(row["barrier"].get("t2_before_sl")) for row in actionable
        ),
        "mean_eod_mtm_r": round(statistics.fmean(mtm), 6) if mtm else None,
        "median_eod_mtm_r": round(statistics.median(mtm), 6) if mtm else None,
    }


def _input_summary(rows):
    by_stock = {}
    for symbol in STOCKS:
        stock_rows = [row for row in rows if row["symbol"] == symbol]
        core_complete = sum(
            row["brain_input_audit"]["all_three_timeframes_core_complete"]
            for row in stock_rows
        )
        present = Counter()
        expected = Counter()
        for row in stock_rows:
            for timeframe in TIMEFRAMES:
                feature_flags = row["brain_input_audit"]["timeframes"][timeframe]["feature_flags"]
                for key, value in feature_flags.items():
                    expected[key] += 1
                    present[key] += bool(value)

        by_stock[symbol] = {
            "observations": len(stock_rows),
            "core_complete_rate_pct": (
                round(100 * core_complete / len(stock_rows), 4)
                if stock_rows else None
            ),
            "feature_presence_pct": {
                key: (
                    round(100 * present[key] / expected[key], 4)
                    if expected[key] else None
                )
                for key in ENGINE_KEYS
            },
        }

    total_complete = sum(
        row["brain_input_audit"]["all_three_timeframes_core_complete"]
        for row in rows
    )
    return {
        "candle_only": True,
        "expected_timeframes": list(TIMEFRAMES),
        "option_inputs_read": False,
        "futures_inputs_read": False,
        "news_inputs_read": False,
        "core_complete_rate_pct": (
            round(100 * total_complete / len(rows), 4) if rows else None
        ),
        "by_stock": by_stock,
    }


def _result_summary(rows):
    return {**_summarize(rows), "barrier_outcomes": _barrier_summary(rows)}


async def run_candle_only_four_stock_backtest(provider):
    end = datetime.combine(END_DATE, time(15, 30), tzinfo=IST)
    five = {}
    errors = []
    discovery_start = datetime.combine(
        END_DATE - timedelta(days=DISCOVERY_LOOKBACK_DAYS),
        time(9, 15),
        tzinfo=IST,
    )

    for symbol in STOCKS:
        try:
            five[symbol] = await _chunk(
                provider, symbol, "5m", discovery_start, end
            )
        except Exception as exc:
            errors.append({
                "symbol": symbol,
                "timeframe": "5m",
                "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
            })

    if errors:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "SOURCE_CANDLE_DATA_INCOMPLETE",
            "history_errors": errors,
            "safety": architecture_contract(),
        }

    sessions_by_stock = last_20_sessions_by_stock(five)
    insufficient = {
        symbol: [session.isoformat() for session in sessions]
        for symbol, sessions in sessions_by_stock.items()
        if len(sessions) < 20
    }
    if insufficient:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "INSUFFICIENT_PER_STOCK_TRADING_SESSIONS",
            "available_session_count_per_stock": {
                symbol: len(sessions)
                for symbol, sessions in sessions_by_stock.items()
            },
            "insufficient_sessions_by_stock": insufficient,
            "safety": architecture_contract(),
        }

    earliest = min(
        session
        for sessions in sessions_by_stock.values()
        for session in sessions
    )
    histories = {symbol: {"5m": five[symbol]} for symbol in STOCKS}
    starts = {
        "15m": datetime.combine(
            earliest - timedelta(days=18), time(9, 15), tzinfo=IST
        ),
        "1h": datetime.combine(
            earliest - timedelta(days=65), time(9, 15), tzinfo=IST
        ),
    }

    for symbol in STOCKS:
        for timeframe in ("15m", "1h"):
            try:
                histories[symbol][timeframe] = await _chunk(
                    provider, symbol, timeframe, starts[timeframe], end
                )
            except Exception as exc:
                errors.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
                })

    if errors:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "SOURCE_CANDLE_DATA_INCOMPLETE",
            "history_errors": errors,
            "safety": architecture_contract(),
        }

    rows = []
    categories = dict(FROZEN_STOCKS)
    for symbol in STOCKS:
        for day in sessions_by_stock[symbol]:
            for click in deterministic_clicks(day):
                technical = core.technical_at(symbol, histories[symbol], click)
                decision = _decision_snapshot(technical)
                direction = (
                    decision["action"]
                    if decision["action"] in {"LONG", "SHORT"}
                    else None
                )
                rows.append({
                    "trade_date": day.isoformat(),
                    "click_at": click.isoformat(),
                    "symbol": symbol,
                    "category": categories[symbol],
                    "decision": decision,
                    "technical": technical,
                    "brain_input_audit": _audit(technical),
                    "outcome": resolve_underlying_path(
                        histories[symbol]["5m"], click, direction
                    ),
                    "barrier": _barrier(
                        histories[symbol]["5m"], click, decision
                    ),
                })

    expected = 20 * CLICKS_PER_DAY * len(STOCKS)
    selected_sets = {
        symbol: set(sessions)
        for symbol, sessions in sessions_by_stock.items()
    }
    common_selected = sorted(set.intersection(*selected_sets.values()))
    trade_dates = sorted({row["trade_date"] for row in rows})

    return {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED" if len(rows) == expected else "OBSERVATION_COUNT_MISMATCH",
        "experiment": {
            "stocks": [
                {"symbol": symbol, "category": category}
                for symbol, category in FROZEN_STOCKS
            ],
            "session_count": 20,
            "session_count_per_stock": {
                symbol: len(sessions)
                for symbol, sessions in sessions_by_stock.items()
            },
            "tested_sessions_by_stock": {
                symbol: [session.isoformat() for session in sessions]
                for symbol, sessions in sessions_by_stock.items()
            },
            "common_selected_session_count": len(common_selected),
            "common_selected_sessions": [
                session.isoformat() for session in common_selected
            ],
            "clicks_per_day": CLICKS_PER_DAY,
            "observations_per_stock": 20 * CLICKS_PER_DAY,
            "expected_observations": expected,
            "observations": len(rows),
            "latest_completed_session_frozen": END_DATE.isoformat(),
        },
        "data_coverage": {
            symbol: {
                "selected_session_count": len(sessions_by_stock[symbol]),
                "selected_sessions": [
                    session.isoformat() for session in sessions_by_stock[symbol]
                ],
                **{
                    timeframe: {
                        "candles": len(histories[symbol][timeframe]),
                        "first": (
                            str(histories[symbol][timeframe][0][0])
                            if histories[symbol][timeframe] else None
                        ),
                        "last": (
                            str(histories[symbol][timeframe][-1][0])
                            if histories[symbol][timeframe] else None
                        ),
                    }
                    for timeframe in TIMEFRAMES
                },
            }
            for symbol in STOCKS
        },
        "brain_input_audit": _input_summary(rows),
        "summary": _result_summary(rows),
        "by_stock": {
            symbol: _result_summary([
                row for row in rows if row["symbol"] == symbol
            ])
            for symbol in STOCKS
        },
        "by_trade_date": {
            trade_date: _result_summary([
                row for row in rows if row["trade_date"] == trade_date
            ])
            for trade_date in trade_dates
        },
        "rows": rows,
        "methodology": {
            "completed_candles_only": True,
            "random_clicks_fixed": True,
            "session_selection": "PER_STOCK_LAST_20_FROM_5M_TAPE",
            "cross_stock_common_session_required": False,
            "five_minute_discovery_lookback_calendar_days": DISCOVERY_LOOKBACK_DAYS,
            "current_technical_engine_unchanged": True,
            "current_mtf_aggregation_unchanged": True,
            "entry_sl_t1_t2_evaluated": True,
            "same_bar_sl_t1_ambiguous": True,
            "no_trade_misses_retained": True,
        },
        "safety": architecture_contract(),
    }


def architecture_contract():
    return {
        "version": PROTOCOL_ID,
        "candle_only": True,
        "frozen_stocks": [
            {"symbol": symbol, "category": category}
            for symbol, category in FROZEN_STOCKS
        ],
        "trading_sessions_per_stock": 20,
        "random_clicks_per_session": CLICKS_PER_DAY,
        "session_selection": "PER_STOCK_LAST_20_FROM_5M_TAPE",
        "common_session_requirement": False,
        "option_data_required": False,
        "option_chain_read": False,
        "option_premium_read": False,
        "option_oi_read": False,
        "iv_read": False,
        "greeks_read": False,
        "futures_read": False,
        "news_read": False,
        "live_execution": False,
        "capital_committed": 0,
        "strategy_policy_changed": False,
    }
