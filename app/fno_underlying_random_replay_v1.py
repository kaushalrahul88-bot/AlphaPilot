"""Frozen underlying-only random-click replay for F&O edge discovery.

This protocol deliberately excludes option-chain state, option prices, IV, Greeks,
option OI and futures from both decisions and outcomes.  It reuses only the
already-validated no-lookahead technical candle decision function and evaluates
that decision against later underlying candles.

The historical experiment is intentionally fixed before outcomes are inspected:
- source window: 2026-08-31 through 2026-09-04;
- 20 unique deterministic random clicks per trading day;
- clicks are 5-minute aligned between 09:30 and 14:00 IST;
- outcomes are recorded at 15m, 30m, 60m, 90m and EOD;
- MFE/MAE and NO_TRADE large-move misses are retained;
- no strategy threshold is tuned by this replay.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from . import fno_15m_historical_replay_v1 as technical_core

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

PROTOCOL_ID = "FNO_UNDERLYING_RANDOM_EDGE_REPLAY_V1_2026-09-06"
SOURCE_DATASET_KEY = (
    "FNO_15M_CANDLE_CACHE_V1:2026-08-31:2026-09-04:"
    "5d0efd9f2e3dfdf2f9cc"
)
SOURCE_START_DATE = date(2026, 8, 31)
SOURCE_END_DATE = date(2026, 9, 4)
CLICKS_PER_DAY = 20
CLICK_START = time(9, 30)
CLICK_END = time(14, 0)
CLICK_STEP_MINUTES = 5
HORIZONS_MINUTES = (15, 30, 60, 90)
SESSION_CLOSE = time(15, 30)
NO_TRADE_MOVE_THRESHOLDS_PCT = (0.5, 1.0)
EXPECTED_TIMEFRAMES = ("5m", "15m", "1h")

CACHE_LOAD_SQL = """
SELECT symbol, timeframe, candles, fetch_error
FROM fno_15m_backtest_candle_cache_v1
WHERE dataset_key=%s
ORDER BY symbol, timeframe;
"""


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _stamp(value: Any) -> datetime | None:
    return technical_core._stamp(value)


def trading_dates() -> list[date]:
    current = SOURCE_START_DATE
    result: list[date] = []
    while current <= SOURCE_END_DATE:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def click_pool(trade_date: date) -> list[datetime]:
    start = datetime.combine(trade_date, CLICK_START, tzinfo=IST)
    end = datetime.combine(trade_date, CLICK_END, tzinfo=IST)
    result: list[datetime] = []
    current = start
    while current <= end:
        result.append(current.astimezone(UTC))
        current += timedelta(minutes=CLICK_STEP_MINUTES)
    return result


def deterministic_clicks(trade_date: date) -> list[datetime]:
    """Return the permanently reproducible 20-click sample for one date."""
    pool = click_pool(trade_date)
    if len(pool) < CLICKS_PER_DAY:
        raise ValueError("click pool is smaller than the frozen sample size")
    digest = hashlib.sha256(
        f"{PROTOCOL_ID}:{trade_date.isoformat()}".encode("utf-8")
    ).digest()
    seed = int.from_bytes(digest[:8], "big", signed=False)
    return sorted(random.Random(seed).sample(pool, CLICKS_PER_DAY))


def _load_histories_sync(database_url: str) -> tuple[dict[str, dict[str, list[list]]], list[dict[str, Any]]]:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(CACHE_LOAD_SQL, (SOURCE_DATASET_KEY,))
            rows = cur.fetchall()

    histories: dict[str, dict[str, list[list]]] = defaultdict(dict)
    errors: list[dict[str, Any]] = []
    for symbol, timeframe, candles, fetch_error in rows:
        symbol = str(symbol).upper()
        timeframe = str(timeframe)
        if fetch_error:
            errors.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "error": str(fetch_error),
            })
            continue
        if isinstance(candles, str):
            candles = json.loads(candles)
        histories[symbol][timeframe] = list(candles or [])

    complete: dict[str, dict[str, list[list]]] = {}
    for symbol, by_timeframe in histories.items():
        missing = [tf for tf in EXPECTED_TIMEFRAMES if not by_timeframe.get(tf)]
        if missing:
            errors.append({
                "symbol": symbol,
                "timeframe": ",".join(missing),
                "error": "MISSING_OR_EMPTY_CANDLE_HISTORY",
            })
            continue
        complete[symbol] = by_timeframe
    return dict(sorted(complete.items())), errors


async def load_histories(database_url: str) -> tuple[dict[str, dict[str, list[list]]], list[dict[str, Any]]]:
    return await asyncio.to_thread(_load_histories_sync, database_url)


def _reference_price(five_minute_history: list[list], click_at: datetime) -> float | None:
    completed = technical_core.completed_candles_at(
        five_minute_history,
        click_at,
        "5m",
    )
    if not completed:
        return None
    try:
        price = float(completed[-1][4])
    except (TypeError, ValueError, IndexError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _future_bars(
    five_minute_history: list[list],
    click_at: datetime,
    end_at: datetime,
) -> list[list]:
    click_at = _utc(click_at)
    end_at = _utc(end_at)
    rows: list[tuple[datetime, list]] = []
    for row in five_minute_history:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        stamp = _stamp(row[0])
        if stamp is None:
            continue
        # Groww timestamps are treated as candle-start times.  Only bars whose
        # entire 5-minute interval is after the click and complete by end_at are
        # outcome data.
        if click_at <= stamp and stamp + timedelta(minutes=5) <= end_at:
            rows.append((stamp, list(row)))
    rows.sort(key=lambda item: item[0])
    return [row for _, row in rows]


def _pct(value: float, reference: float) -> float:
    return round((value / reference - 1.0) * 100.0, 6)


def _path_metrics(
    five_minute_history: list[list],
    click_at: datetime,
    end_at: datetime,
    reference_price: float,
    direction: str | None,
) -> dict[str, Any]:
    bars = _future_bars(five_minute_history, click_at, end_at)
    if not bars:
        return {
            "resolved": False,
            "end_price": None,
            "raw_return_pct": None,
            "directional_return_pct": None,
            "max_up_pct": None,
            "max_down_pct": None,
            "mfe_pct": None,
            "mae_pct": None,
            "max_abs_excursion_pct": None,
        }
    try:
        end_price = float(bars[-1][4])
        max_high = max(float(row[2]) for row in bars)
        min_low = min(float(row[3]) for row in bars)
    except (TypeError, ValueError, IndexError):
        return {
            "resolved": False,
            "end_price": None,
            "raw_return_pct": None,
            "directional_return_pct": None,
            "max_up_pct": None,
            "max_down_pct": None,
            "mfe_pct": None,
            "mae_pct": None,
            "max_abs_excursion_pct": None,
        }

    raw = _pct(end_price, reference_price)
    max_up = _pct(max_high, reference_price)
    max_down = _pct(min_low, reference_price)
    directional = None
    mfe = None
    mae = None
    if direction == "LONG":
        directional = raw
        mfe = max_up
        mae = max_down
    elif direction == "SHORT":
        directional = round(-raw, 6)
        mfe = round(-max_down, 6)
        mae = round(-max_up, 6)

    return {
        "resolved": True,
        "end_price": end_price,
        "raw_return_pct": raw,
        "directional_return_pct": directional,
        "max_up_pct": max_up,
        "max_down_pct": max_down,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "max_abs_excursion_pct": round(max(abs(max_up), abs(max_down)), 6),
    }


def resolve_underlying_path(
    five_minute_history: list[list],
    click_at: datetime,
    direction: str | None,
) -> dict[str, Any]:
    reference = _reference_price(five_minute_history, click_at)
    if reference is None:
        return {
            "reference_price": None,
            "status": "REFERENCE_PRICE_UNAVAILABLE",
            "checkpoints": {},
            "eod": {},
        }

    checkpoints: dict[str, dict[str, Any]] = {}
    for horizon in HORIZONS_MINUTES:
        checkpoints[f"{horizon}m"] = _path_metrics(
            five_minute_history,
            click_at,
            click_at + timedelta(minutes=horizon),
            reference,
            direction,
        )
    eod_at = datetime.combine(
        click_at.astimezone(IST).date(),
        SESSION_CLOSE,
        tzinfo=IST,
    ).astimezone(UTC)
    eod = _path_metrics(
        five_minute_history,
        click_at,
        eod_at,
        reference,
        direction,
    )
    status = "RESOLVED" if all(
        item.get("resolved") for item in [*checkpoints.values(), eod]
    ) else "PARTIAL_OUTCOME_DATA"
    return {
        "reference_price": reference,
        "status": status,
        "checkpoints": checkpoints,
        "eod": eod,
    }


def _decision_snapshot(technical: Mapping[str, Any]) -> dict[str, Any]:
    status = str(technical.get("status") or "NO_TRADE")
    direction = str(technical.get("direction") or "").upper() or None
    if status != "SETUP" or direction not in {"LONG", "SHORT"}:
        direction = None
    return {
        "action": direction or "NO_TRADE",
        "status": status,
        "signal": technical.get("signal"),
        "multi_timeframe_score": technical.get("multi_timeframe_score"),
        "timeframe_votes": technical.get("timeframe_votes") or {},
        "higher_timeframe": technical.get("higher_timeframe") or {},
        "execution_timeframe": technical.get("execution_timeframe"),
        "model_entry": technical.get("entry"),
        "model_stop_loss": technical.get("stop_loss"),
        "model_target1": technical.get("target1"),
        "model_target2": technical.get("target2"),
        "risk_reward": technical.get("risk_reward"),
        "reason": technical.get("reason"),
    }


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return round(statistics.fmean(values), 6) if values else None


def _median(values: Iterable[float]) -> float | None:
    values = list(values)
    return round(statistics.median(values), 6) if values else None


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts = Counter(row["decision"]["action"] for row in rows)
    actionable = [row for row in rows if row["decision"]["action"] in {"LONG", "SHORT"}]
    no_trade = [row for row in rows if row["decision"]["action"] == "NO_TRADE"]

    horizons: dict[str, dict[str, Any]] = {}
    for key in [*(f"{value}m" for value in HORIZONS_MINUTES), "EOD"]:
        resolved_returns: list[float] = []
        for row in actionable:
            block = row["outcome"]["eod"] if key == "EOD" else row["outcome"]["checkpoints"].get(key, {})
            value = block.get("directional_return_pct")
            if value is not None:
                resolved_returns.append(float(value))
        wins = sum(value > 0 for value in resolved_returns)
        losses = sum(value < 0 for value in resolved_returns)
        flats = len(resolved_returns) - wins - losses
        horizons[key] = {
            "resolved_actionable": len(resolved_returns),
            "direction_correct": wins,
            "direction_incorrect": losses,
            "flat": flats,
            "direction_correct_rate_pct": round(100.0 * wins / len(resolved_returns), 4) if resolved_returns else None,
            "mean_directional_return_pct": _mean(resolved_returns),
            "median_directional_return_pct": _median(resolved_returns),
        }

    eod_mfe = [
        float(row["outcome"]["eod"]["mfe_pct"])
        for row in actionable
        if row["outcome"]["eod"].get("mfe_pct") is not None
    ]
    eod_mae = [
        float(row["outcome"]["eod"]["mae_pct"])
        for row in actionable
        if row["outcome"]["eod"].get("mae_pct") is not None
    ]
    missed_moves: dict[str, Any] = {}
    for threshold in NO_TRADE_MOVE_THRESHOLDS_PCT:
        misses = [
            row for row in no_trade
            if row["outcome"]["eod"].get("max_abs_excursion_pct") is not None
            and float(row["outcome"]["eod"]["max_abs_excursion_pct"]) >= threshold
        ]
        missed_moves[f"ge_{threshold:g}pct"] = {
            "count": len(misses),
            "rate_of_no_trade_pct": round(100.0 * len(misses) / len(no_trade), 4) if no_trade else None,
        }

    return {
        "observations": len(rows),
        "action_counts": dict(sorted(action_counts.items())),
        "actionable": len(actionable),
        "no_trade": len(no_trade),
        "actionable_rate_pct": round(100.0 * len(actionable) / len(rows), 4) if rows else None,
        "horizons": horizons,
        "eod_mean_mfe_pct": _mean(eod_mfe),
        "eod_median_mfe_pct": _median(eod_mfe),
        "eod_mean_mae_pct": _mean(eod_mae),
        "eod_median_mae_pct": _median(eod_mae),
        "no_trade_large_move_misses": missed_moves,
    }


async def run_underlying_random_replay_v1(database_url: str) -> dict[str, Any]:
    if not str(database_url or "").strip():
        raise ValueError("database_url is required")

    histories, history_errors = await load_histories(database_url)
    if history_errors:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "SOURCE_CANDLE_DATA_INCOMPLETE",
            "source_dataset_key": SOURCE_DATASET_KEY,
            "history_errors": history_errors,
            "safety": architecture_contract(),
        }
    if not histories:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "NO_SOURCE_CANDLE_DATA",
            "source_dataset_key": SOURCE_DATASET_KEY,
            "history_errors": [],
            "safety": architecture_contract(),
        }

    dates = trading_dates()
    schedule = {item.isoformat(): deterministic_clicks(item) for item in dates}
    rows: list[dict[str, Any]] = []
    daily_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for trade_date in dates:
        for click_at in schedule[trade_date.isoformat()]:
            for symbol, by_timeframe in histories.items():
                technical = technical_core.technical_at(symbol, by_timeframe, click_at)
                decision = _decision_snapshot(technical)
                outcome = resolve_underlying_path(
                    by_timeframe["5m"],
                    click_at,
                    decision["action"] if decision["action"] in {"LONG", "SHORT"} else None,
                )
                row = {
                    "trade_date": trade_date.isoformat(),
                    "click_at": click_at.isoformat(),
                    "symbol": symbol,
                    "decision": decision,
                    "outcome": outcome,
                }
                rows.append(row)
                daily_rows[trade_date.isoformat()].append(row)

    return {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED",
        "source_dataset_key": SOURCE_DATASET_KEY,
        "source_window": {
            "first_trade_date": SOURCE_START_DATE.isoformat(),
            "last_trade_date": SOURCE_END_DATE.isoformat(),
        },
        "universe": sorted(histories),
        "universe_size": len(histories),
        "scheduled_clicks": sum(len(value) for value in schedule.values()),
        "symbol_slots": sum(len(value) for value in schedule.values()) * len(histories),
        "click_schedule_ist": {
            key: [value.astimezone(IST).strftime("%H:%M") for value in values]
            for key, values in schedule.items()
        },
        "methodology": {
            "random_sample_frozen": True,
            "random_seed_derivation": "SHA256(protocol_id + trade_date), first 64 bits",
            "clicks_per_day": CLICKS_PER_DAY,
            "click_pool_ist": "09:30-14:00 inclusive, 5-minute aligned",
            "future_horizons_minutes": list(HORIZONS_MINUTES),
            "eod_ist": "15:30",
            "fully_completed_candles_only": True,
            "technical_brain": "existing F&O underlying multi-timeframe technical_at parity",
            "timeframes": list(EXPECTED_TIMEFRAMES),
            "options_used_for_decision": False,
            "options_used_for_outcome": False,
            "futures_used": False,
            "news_macro_replayed": False,
            "strategy_thresholds_retuned": False,
            "selector_optimization": False,
            "no_trade_retained": True,
            "no_trade_large_move_thresholds_pct": list(NO_TRADE_MOVE_THRESHOLDS_PCT),
        },
        "summary": _summarize(rows),
        "daily": {
            key: _summarize(value)
            for key, value in sorted(daily_rows.items())
        },
        "observations": rows,
        "history_errors": history_errors,
        "safety": architecture_contract(),
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": PROTOCOL_ID,
        "research_only": True,
        "historical_replay": True,
        "underlying_only": True,
        "option_chain_read": False,
        "option_premium_read": False,
        "option_oi_iv_greeks_read": False,
        "futures_generation": False,
        "broker_orders": False,
        "live_execution": False,
        "capital_committed": 0,
        "strategy_policy_changed": False,
        "outcomes_can_change_decision": False,
    }
