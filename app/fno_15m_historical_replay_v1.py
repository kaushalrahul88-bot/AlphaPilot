"""Read-only 15-minute historical replay for the F&O Market Brain V2.

This module is intentionally diagnostic. It replays every 15-minute click from
09:30 through 15:00 IST across the currently connected F&O universe for every
trading date represented in the saved point-in-time option-chain archive.

Two modes are produced from the exact same click schedule:
- STRICT_V2: option-chain snapshot age must be <= 120 seconds.
- COVERAGE_30M: latest strictly-prior/same-time snapshot may be <= 30 minutes old.

The second mode is coverage diagnostics only and must never be interpreted as
production-equivalent performance. Historical technical candles are
reconstructible and fetched from Groww, then truncated to fully completed bars
at each simulated click. Saved option-chain rows are never backfilled or
interpolated. Outcomes use only later saved observations of the exact selected
option contract and are premium-return diagnostics, not executable P&L because
the historical archive does not contain bid/ask.
"""
from __future__ import annotations

import asyncio
import bisect
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import httpx

from .engine import analyze_candles
from .fno_market_brain_v2 import build_experience_memory, build_perception, decide_shadow
from .fno_prospective_protocol_v1 import PRIMARY_HORIZON_MINUTES, session_outcome_eligible
from .providers.groww import GrowwProvider

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
MODE = "FNO_15M_FULL_WINDOW_HISTORICAL_REPLAY_V1"
STRICT_MAX_SNAPSHOT_AGE_SECONDS = 120
DIAGNOSTIC_MAX_SNAPSHOT_AGE_SECONDS = 30 * 60
CLICK_START = time(9, 30)
CLICK_END = time(15, 0)
CLICK_STEP_MINUTES = 15
TIMEFRAMES = ("5m", "15m", "1h")
TIMEFRAME_MINUTES = {"5m": 5, "15m": 15, "1h": 60}
LOOKBACK_DAYS = {"5m": 7, "15m": 14, "1h": 60}
GROWW_INTERVAL = {"5m": "5minute", "15m": "15minute", "1h": "1hour"}
GROWW_CHUNK_DAYS = {"5m": 6, "15m": 13, "1h": 55}
MIN_RISK_REWARD = 1.5
MAX_MEMORY_CASES = 1000

CONNECTED_UNIVERSE = tuple(
    sorted(set(GrowwProvider.NSE_CASH_SYMBOLS) | {"NIFTY", "BANKNIFTY"})
)

SNAPSHOT_SQL = """
SELECT provider, underlying_symbol, expiry_date, observed_at, payload
FROM fno_option_chain_snapshots
WHERE underlying_symbol = ANY(%s)
ORDER BY underlying_symbol, observed_at;
"""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _stamp(value: Any) -> datetime | None:
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, (int, float)) or str(value).strip().replace(".", "", 1).isdigit():
            number = float(value)
            if number > 1e12:
                number /= 1000.0
            result = datetime.fromtimestamp(number, UTC)
        else:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            result = result.replace(tzinfo=IST)
        return result.astimezone(UTC)
    except Exception:
        return None


def click_schedule(trade_date: date) -> list[datetime]:
    start = datetime.combine(trade_date, CLICK_START, tzinfo=IST)
    end = datetime.combine(trade_date, CLICK_END, tzinfo=IST)
    result = []
    current = start
    while current <= end:
        result.append(current.astimezone(UTC))
        current += timedelta(minutes=CLICK_STEP_MINUTES)
    return result


def _canonical_candle(row: Any) -> tuple[datetime, list] | None:
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        return None
    stamp = _stamp(row[0])
    if stamp is None:
        return None
    try:
        o, h, l, c = [float(value) for value in row[1:5]]
        volume = float(row[5]) if len(row) > 5 and row[5] is not None else 0.0
    except (TypeError, ValueError):
        return None
    if min(o, h, l, c) <= 0 or h < l:
        return None
    extra = list(row[6:]) if len(row) > 6 else []
    normalized = [
        stamp.astimezone(IST).isoformat(),
        o,
        h,
        l,
        c,
        max(0.0, volume),
        *extra,
    ]
    return stamp, normalized


def _merge_candles(rows: Iterable[Any]) -> list[list]:
    by_time: dict[datetime, list] = {}
    for row in rows:
        normalized = _canonical_candle(row)
        if normalized is None:
            continue
        stamp, candle = normalized
        by_time[stamp] = candle
    return [by_time[key] for key in sorted(by_time)]


def completed_candles_at(candles: list[list], click_at: datetime, timeframe: str) -> list[list]:
    """Return only candles that are certainly complete at the simulated click.

    Groww candle timestamps are treated conservatively as candle-start times, so
    the current interval is excluded. This may drop one otherwise-usable bar,
    but it cannot introduce look-ahead.
    """
    click_at = _utc(click_at)
    minutes = TIMEFRAME_MINUTES[timeframe]
    lower = click_at - timedelta(days=LOOKBACK_DAYS[timeframe])
    cutoff = click_at - timedelta(minutes=minutes)
    result = []
    for row in candles:
        stamp = _stamp(row[0])
        if stamp is None:
            continue
        if lower <= stamp <= cutoff:
            result.append(row)
    return result


def _combine_timeframes(symbol: str, tf: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Pure parity copy of GrowwProvider.multi_timeframe_scan aggregation."""
    weights = {"5m": 0.20, "15m": 0.35, "1h": 0.30, "1d": 0.15}
    valid = [value for value in tf.values() if value.get("status") != "ERROR"]
    if not valid:
        return {"symbol": symbol, "status": "ERROR", "timeframes": dict(tf)}

    weighted_sum = sum(
        tf[name].get("alpha_score", 50) * weights.get(name, 0.25)
        for name in tf
        if tf[name].get("status") != "ERROR"
    )
    weight_used = sum(
        weights.get(name, 0.25)
        for name in tf
        if tf[name].get("status") != "ERROR"
    )
    score = weighted_sum / weight_used if weight_used else 50.0

    long_votes = sum(
        1
        for value in valid
        if value.get("signal") in ("LONG", "STRONG_LONG", "WATCH_LONG")
        and value.get("alpha_score", 0) >= 58
    )
    short_votes = sum(
        1
        for value in valid
        if value.get("signal") in ("SHORT", "STRONG_SHORT", "WATCH_SHORT")
        and value.get("alpha_score", 100) <= 42
    )
    setup_long = sum(
        1 for value in valid
        if value.get("status") == "SETUP" and value.get("direction") == "LONG"
    )
    setup_short = sum(
        1 for value in valid
        if value.get("status") == "SETUP" and value.get("direction") == "SHORT"
    )
    higher_bull = any(
        tf.get(name, {}).get("alpha_score", 50) >= 55
        for name in ("1h", "1d")
        if name in tf
    )
    higher_bear = any(
        tf.get(name, {}).get("alpha_score", 50) <= 45
        for name in ("1h", "1d")
        if name in tf
    )
    n = len(valid)
    if score >= 68 and long_votes >= max(2, n - 1) and setup_long >= 1 and not higher_bear:
        status, direction = "SETUP", "LONG"
    elif score <= 32 and short_votes >= max(2, n - 1) and setup_short >= 1 and not higher_bull:
        status, direction = "SETUP", "SHORT"
    else:
        status, direction = "NO_TRADE", None

    if status == "SETUP" and direction == "LONG":
        signal = "STRONG_LONG" if score >= 80 and long_votes == n else "LONG"
    elif status == "SETUP" and direction == "SHORT":
        signal = "STRONG_SHORT" if score <= 20 and short_votes == n else "SHORT"
    elif score >= 58:
        signal = "WATCH_LONG"
    elif score <= 42:
        signal = "WATCH_SHORT"
    else:
        signal = "NO_TRADE"

    item: dict[str, Any] = {
        "symbol": symbol,
        "status": status,
        "signal": signal,
        "multi_timeframe_score": round(score, 1),
        "timeframe_votes": {"long": long_votes, "short": short_votes, "valid": n},
        "higher_timeframe": {"bullish": higher_bull, "bearish": higher_bear},
        "timeframes": dict(tf),
    }
    if status == "SETUP":
        execution = tf.get("15m", valid[0])
        item.update({
            "direction": direction,
            "execution_timeframe": "15m" if "15m" in tf else next(iter(tf)),
            "entry": execution.get("entry"),
            "stop_loss": execution.get("stop_loss"),
            "target1": execution.get("target1"),
            "target2": execution.get("target2"),
            "risk_reward": execution.get("risk_reward"),
        })
    else:
        item["reason"] = "Multi-timeframe alignment threshold not met"
    return item


def technical_at(
    symbol: str,
    histories: Mapping[str, list[list]],
    click_at: datetime,
) -> dict[str, Any]:
    tf = {}
    for timeframe in TIMEFRAMES:
        candles = completed_candles_at(histories.get(timeframe, []), click_at, timeframe)
        try:
            tf[timeframe] = analyze_candles(symbol, candles, MIN_RISK_REWARD)
        except Exception as exc:
            tf[timeframe] = {
                "symbol": symbol,
                "status": "ERROR",
                "error": f"{exc.__class__.__name__}: {str(exc)[:160]}",
            }
    return _combine_timeframes(symbol, tf)


def _chain_payload(raw: Any) -> dict:
    current = dict(raw) if isinstance(raw, Mapping) else {}
    for key in ("data", "payload", "payload"):
        child = current.get(key)
        if isinstance(child, Mapping):
            current = dict(child)
    return current


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _exact_leg(snapshot_payload: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict | None:
    chain = _chain_payload(snapshot_payload)
    strikes = chain.get("strikes") or {}
    if not isinstance(strikes, Mapping):
        return None
    wanted_strike = _number(candidate.get("strike"))
    wanted_type = str(candidate.get("option_type") or "").upper()
    wanted_symbol = str(candidate.get("trading_symbol") or "")
    if wanted_strike is None or wanted_type not in {"CE", "PE"} or not wanted_symbol:
        return None

    selected = None
    for strike_key, value in strikes.items():
        strike = _number(strike_key)
        if strike is not None and abs(strike - wanted_strike) < 1e-6:
            selected = value
            break
    if not isinstance(selected, Mapping):
        return None
    leg = selected.get(wanted_type) or {}
    if not isinstance(leg, Mapping):
        return None
    leg_symbol = str(leg.get("trading_symbol") or "")
    if leg_symbol != wanted_symbol:
        return None
    ltp = _number(leg.get("ltp"))
    if ltp is None or ltp <= 0:
        return None
    return dict(leg)


def _underlying_outcome(
    perception: Mapping[str, Any],
    five_minute_history: list[list],
    click_at: datetime,
    due_at: datetime,
) -> dict[str, Any]:
    start = _number((perception.get("underlying") or {}).get("ltp"))
    bars = []
    for row in five_minute_history:
        stamp = _stamp(row[0])
        if stamp is None:
            continue
        if click_at <= stamp and stamp + timedelta(minutes=5) <= due_at:
            bars.append((stamp, row))
    bars.sort(key=lambda item: item[0])
    if start is None or start <= 0 or not bars:
        return {
            "underlying_end_price": None,
            "underlying_return_pct": None,
            "max_up_pct": None,
            "max_down_pct": None,
        }
    end = float(bars[-1][1][4])
    max_high = max(float(item[1][2]) for item in bars)
    min_low = min(float(item[1][3]) for item in bars)

    def pct(value: float) -> float:
        return round((value / start - 1.0) * 100.0, 6)

    return {
        "underlying_end_price": end,
        "underlying_return_pct": pct(end),
        "max_up_pct": pct(max_high),
        "max_down_pct": pct(min_low),
    }


def resolve_historical_candidate(
    perception: Mapping[str, Any],
    decision: Mapping[str, Any],
    snapshots: list[dict[str, Any]],
    five_minute_history: list[list],
    *,
    click_at: datetime,
) -> dict[str, Any]:
    click_at = _utc(click_at)
    due_at = click_at + timedelta(minutes=PRIMARY_HORIZON_MINUTES)
    action = str(decision.get("research_action") or "NO_TRADE").upper()
    eligible = session_outcome_eligible(click_at)
    underlying = _underlying_outcome(perception, five_minute_history, click_at, due_at)

    base = {
        "horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "outcome_due_at": due_at.isoformat(),
        "outcome_eligible": eligible,
        **underlying,
        "option_observations": 0,
        "option_end_ltp": None,
        "option_return_pct": None,
        "classification": "NO_TRADE_OBSERVED" if action == "NO_TRADE" else "OPTION_FLAT",
        "resolution_status": "RESOLVED",
        "historical_option_chain_backfill_used": False,
        "bid_ask_execution_replay_available": False,
        "live_execution": False,
        "capital_committed": 0,
    }
    if not eligible:
        base.update({
            "resolution_status": "INELIGIBLE_LATE_SESSION_HORIZON",
            "classification": "NOT_ADMITTED_TO_MEMORY",
        })
        return base
    if underlying["underlying_end_price"] is None:
        base.update({
            "resolution_status": "UNDERLYING_DATA_INCOMPLETE",
            "classification": "NOT_ADMITTED_TO_MEMORY",
        })
        return base
    if action == "NO_TRADE":
        return base

    candidate = decision.get("research_candidate") or {}
    entry_ltp = _number(candidate.get("ltp"))
    if entry_ltp is None or entry_ltp <= 0:
        base.update({
            "resolution_status": "SELECTED_OPTION_TAPE_INCOMPLETE",
            "classification": "NOT_ADMITTED_TO_MEMORY",
        })
        return base

    expiry = str((perception.get("source") or {}).get("expiry_date") or "")
    later = []
    for snapshot in snapshots:
        observed = snapshot["observed_at"]
        if observed <= click_at or observed > due_at:
            continue
        if str(snapshot.get("expiry_date") or "") != expiry:
            continue
        leg = _exact_leg(snapshot["payload"], candidate)
        if leg is None:
            continue
        later.append((observed, float(leg["ltp"])))
    later.sort(key=lambda item: item[0])
    base["option_observations"] = 1 + len(later)
    if not later:
        base.update({
            "resolution_status": "SELECTED_OPTION_TAPE_INCOMPLETE",
            "classification": "NOT_ADMITTED_TO_MEMORY",
        })
        return base

    option_end = later[-1][1]
    option_return = round((option_end / entry_ltp - 1.0) * 100.0, 6)
    base["option_end_ltp"] = option_end
    base["option_return_pct"] = option_return
    if option_return > 0.25:
        base["classification"] = "OPTION_GAIN"
    elif option_return < -0.25:
        base["classification"] = "OPTION_LOSS"
    else:
        base["classification"] = "OPTION_FLAT"
    return base


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


def _load_snapshots_sync(database_url: str) -> tuple[dict[str, list[dict[str, Any]]], list[date]]:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SNAPSHOT_SQL, (list(CONNECTED_UNIVERSE),))
            rows = cur.fetchall()

    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trade_dates: set[date] = set()
    for provider, symbol, expiry, observed_at, payload in rows:
        observed = _utc(observed_at)
        local_date = observed.astimezone(IST).date()
        trade_dates.add(local_date)
        by_symbol[str(symbol).upper()].append({
            "provider": str(provider or "GROWW"),
            "underlying_symbol": str(symbol).upper(),
            "expiry_date": expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry),
            "observed_at": observed,
            "payload": payload if isinstance(payload, Mapping) else json.loads(payload),
        })
    for symbol in by_symbol:
        by_symbol[symbol].sort(key=lambda item: item["observed_at"])
    return dict(by_symbol), sorted(trade_dates)


async def _load_snapshots(database_url: str):
    return await asyncio.to_thread(_load_snapshots_sync, database_url)


async def _raw_historical_request(provider, params: dict[str, Any]) -> list:
    last_error = None
    for attempt in range(1, 4):
        try:
            throttle = getattr(provider, "_throttle", None)
            if callable(throttle):
                await throttle()
            async with httpx.AsyncClient(timeout=40) as client:
                response = await client.get(
                    f"{provider.BASE_URL}/v1/historical/candles",
                    headers=await provider._headers(),
                    params=params,
                )
            if response.status_code == 429:
                register = getattr(provider, "_register_rate_limit", None)
                if callable(register):
                    await register()
            if response.status_code >= 500 or response.status_code == 429:
                last_error = RuntimeError(f"Groww historical HTTP {response.status_code}")
                if attempt < 3:
                    await asyncio.sleep(2 * attempt)
                    continue
            response.raise_for_status()
            body = response.json()
            payload = body.get("payload", body) if isinstance(body, Mapping) else {}
            return payload.get("candles", []) if isinstance(payload, Mapping) else []
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                await asyncio.sleep(2 * attempt)
                continue
    raise RuntimeError(f"historical candle fetch failed: {last_error}")


async def fetch_historical_candles(
    provider,
    symbol: str,
    timeframe: str,
    start_at: datetime,
    end_at: datetime,
) -> list[list]:
    exchange, segment, _, groww_symbol = provider._instrument(symbol)
    current = start_at.astimezone(IST)
    final = end_at.astimezone(IST)
    rows: list[Any] = []
    chunk_days = GROWW_CHUNK_DAYS[timeframe]
    while current <= final:
        chunk_end = min(current + timedelta(days=chunk_days), final)
        params = {
            "exchange": exchange,
            "segment": segment,
            "groww_symbol": groww_symbol,
            "start_time": current.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
            "candle_interval": GROWW_INTERVAL[timeframe],
        }
        rows.extend(await _raw_historical_request(provider, params))
        current = chunk_end + timedelta(seconds=1)
    return _merge_candles(rows)


async def fetch_all_histories(
    provider,
    symbols: Iterable[str],
    trade_dates: list[date],
) -> tuple[dict[str, dict[str, list[list]]], list[dict[str, Any]]]:
    if not trade_dates:
        return {}, []
    earliest = datetime.combine(trade_dates[0], time(9, 15), tzinfo=IST)
    latest = datetime.combine(trade_dates[-1], time(15, 30), tzinfo=IST)
    histories: dict[str, dict[str, list[list]]] = {}
    failures: list[dict[str, Any]] = []

    for symbol in symbols:
        histories[symbol] = {}
        for timeframe in TIMEFRAMES:
            start = earliest - timedelta(days=LOOKBACK_DAYS[timeframe])
            try:
                histories[symbol][timeframe] = await fetch_historical_candles(
                    provider, symbol, timeframe, start, latest
                )
            except Exception as exc:
                histories[symbol][timeframe] = []
                failures.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "error": f"{exc.__class__.__name__}: {str(exc)[:240]}",
                })
    return histories, failures


def _snapshot_before(
    snapshots: list[dict[str, Any]],
    click_at: datetime,
) -> dict[str, Any] | None:
    if not snapshots:
        return None
    times = [item["observed_at"] for item in snapshots]
    index = bisect.bisect_right(times, click_at) - 1
    if index < 0:
        return None
    selected = snapshots[index]
    if selected["observed_at"].astimezone(IST).date() != click_at.astimezone(IST).date():
        return None
    return selected


def _candidate_row(
    *,
    mode: str,
    click_at: datetime,
    symbol: str,
    perception: Mapping[str, Any],
    technical: Mapping[str, Any],
    decision: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = decision.get("research_candidate") or {}
    return {
        "mode": mode,
        "click_at": click_at.isoformat(),
        "trade_date": click_at.astimezone(IST).date().isoformat(),
        "symbol": symbol,
        "research_action": decision.get("research_action"),
        "option_type": candidate.get("option_type"),
        "strike": candidate.get("strike"),
        "trading_symbol": candidate.get("trading_symbol"),
        "entry_ltp": candidate.get("ltp"),
        "candidate_open_interest": candidate.get("open_interest"),
        "candidate_volume": candidate.get("volume"),
        "snapshot_observed_at": perception.get("observed_at"),
        "snapshot_age_seconds": perception.get("age_seconds"),
        "technical_score": technical.get("multi_timeframe_score"),
        "technical_direction": technical.get("direction"),
        "pcr_oi": (perception.get("derivatives") or {}).get("pcr_oi"),
        "atm_iv": (perception.get("derivatives") or {}).get("atm_iv"),
        "outcome": dict(outcome),
    }


def _reporting_rank(row: Mapping[str, Any]) -> tuple:
    score = _number(row.get("technical_score"))
    conviction = abs((score if score is not None else 50.0) - 50.0)
    oi = _number(row.get("candidate_open_interest")) or 0.0
    volume = _number(row.get("candidate_volume")) or 0.0
    return (conviction, oi, volume, str(row.get("symbol") or ""))


def _aggregate_mode(
    mode: str,
    clicks: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    symbol_slots: int,
) -> dict[str, Any]:
    resolved = [row for row in candidates if row["outcome"].get("resolution_status") == "RESOLVED"]
    returns = [
        float(row["outcome"]["option_return_pct"])
        for row in resolved
        if row["outcome"].get("option_return_pct") is not None
    ]
    classifications = Counter(row["outcome"].get("classification") for row in candidates)

    top_rows = [
        click.get("display_top_candidate")
        for click in clicks
        if click.get("display_top_candidate")
    ]
    top_resolved = [
        row for row in top_rows
        if (row.get("outcome") or {}).get("resolution_status") == "RESOLVED"
    ]
    top_returns = [
        float(row["outcome"]["option_return_pct"])
        for row in top_resolved
        if (row.get("outcome") or {}).get("option_return_pct") is not None
    ]
    top_classes = Counter((row.get("outcome") or {}).get("classification") for row in top_rows)

    action_counts = Counter()
    for click in clicks:
        action_counts.update({key: int(value) for key, value in click["research_action_counts"].items()})

    return {
        "mode": mode,
        "scheduled_clicks": len(clicks),
        "symbol_slots": symbol_slots,
        "clicks_with_any_snapshot": sum(click["snapshot_available_symbols"] > 0 for click in clicks),
        "clicks_with_actionable_candidate": sum(click["actionable_candidates"] > 0 for click in clicks),
        "snapshot_available_symbol_slots": sum(click["snapshot_available_symbols"] for click in clicks),
        "research_action_counts": dict(action_counts),
        "actionable_candidates": len(candidates),
        "resolved_actionable_candidates": len(resolved),
        "classification_counts": dict(classifications),
        "resolved_option_gain_rate": round(
            classifications.get("OPTION_GAIN", 0) / len(resolved), 6
        ) if resolved else None,
        "average_option_return_pct_resolved": round(statistics.mean(returns), 6) if returns else None,
        "median_option_return_pct_resolved": round(statistics.median(returns), 6) if returns else None,
        "display_top_candidate_diagnostic": {
            "policy_selector": False,
            "purpose": "reporting_only; not a strategy or capital-allocation rule",
            "clicks_with_top_candidate": len(top_rows),
            "resolved": len(top_resolved),
            "classification_counts": dict(top_classes),
            "average_option_return_pct_resolved": round(statistics.mean(top_returns), 6)
            if top_returns else None,
            "median_option_return_pct_resolved": round(statistics.median(top_returns), 6)
            if top_returns else None,
        },
    }


def _daily_summary(clicks: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day_clicks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_day_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for click in clicks:
        by_day_clicks[click["trade_date"]].append(click)
    for row in candidates:
        by_day_candidates[row["trade_date"]].append(row)

    result = []
    for trade_date in sorted(by_day_clicks):
        day_clicks = by_day_clicks[trade_date]
        day_candidates = by_day_candidates.get(trade_date, [])
        classifications = Counter(row["outcome"].get("classification") for row in day_candidates)
        resolved_returns = [
            float(row["outcome"]["option_return_pct"])
            for row in day_candidates
            if row["outcome"].get("resolution_status") == "RESOLVED"
            and row["outcome"].get("option_return_pct") is not None
        ]
        result.append({
            "trade_date": trade_date,
            "scheduled_clicks": len(day_clicks),
            "clicks_with_any_snapshot": sum(item["snapshot_available_symbols"] > 0 for item in day_clicks),
            "clicks_with_actionable_candidate": sum(item["actionable_candidates"] > 0 for item in day_clicks),
            "actionable_candidates": len(day_candidates),
            "classification_counts": dict(classifications),
            "average_option_return_pct_resolved": round(statistics.mean(resolved_returns), 6)
            if resolved_returns else None,
        })
    return result


async def run_fno_15m_historical_replay(provider, database_url: str) -> dict[str, Any]:
    if not str(database_url or "").strip():
        raise ValueError("database_url is required for F&O historical replay")

    snapshots_by_symbol, trade_dates = await _load_snapshots(database_url)
    replayable_symbols = sorted(
        symbol for symbol in CONNECTED_UNIVERSE if snapshots_by_symbol.get(symbol)
    )
    missing_connected_symbols = sorted(set(CONNECTED_UNIVERSE) - set(replayable_symbols))
    if not trade_dates or not replayable_symbols:
        return {
            "mode": MODE,
            "status": "NO_REPLAYABLE_DATA",
            "trade_dates": [item.isoformat() for item in trade_dates],
            "replayable_symbols": replayable_symbols,
            "missing_connected_symbols": missing_connected_symbols,
            "live_execution": False,
            "capital_committed": 0,
        }

    histories, history_failures = await fetch_all_histories(
        provider, replayable_symbols, trade_dates
    )

    mode_configs = {
        "STRICT_V2": STRICT_MAX_SNAPSHOT_AGE_SECONDS,
        "COVERAGE_30M": DIAGNOSTIC_MAX_SNAPSHOT_AGE_SECONDS,
    }
    prior_cases: dict[str, list[dict[str, Any]]] = {mode: [] for mode in mode_configs}
    mode_clicks: dict[str, list[dict[str, Any]]] = {mode: [] for mode in mode_configs}
    mode_candidates: dict[str, list[dict[str, Any]]] = {mode: [] for mode in mode_configs}
    technical_cache: dict[tuple[str, str], dict[str, Any]] = {}

    for trade_date in trade_dates:
        for click_at in click_schedule(trade_date):
            states: list[dict[str, Any]] = []
            for symbol in replayable_symbols:
                snapshot = _snapshot_before(snapshots_by_symbol[symbol], click_at)
                if snapshot is None:
                    states.append({"symbol": symbol, "snapshot": None})
                    continue
                cache_key = (symbol, click_at.isoformat())
                technical = technical_cache.get(cache_key)
                if technical is None:
                    technical = technical_at(symbol, histories.get(symbol, {}), click_at)
                    technical_cache[cache_key] = technical
                perception = build_perception(
                    snapshot,
                    decision_at=click_at,
                    technical=technical,
                    external_context={
                        "historical_replay": True,
                        "news_replayed": False,
                        "macro_context_replayed": False,
                        "reason": "current V2 decision function does not consume external context",
                    },
                )
                states.append({
                    "symbol": symbol,
                    "snapshot": snapshot,
                    "technical": technical,
                    "perception": perception,
                })

            for mode, max_age in mode_configs.items():
                counts = Counter()
                blocker_counts = Counter()
                click_candidate_rows: list[dict[str, Any]] = []
                snapshot_available = 0

                for state in states:
                    symbol = state["symbol"]
                    if state.get("snapshot") is None:
                        counts["NO_SNAPSHOT"] += 1
                        continue
                    snapshot_available += 1
                    perception = state["perception"]
                    technical = state["technical"]
                    memory = build_experience_memory(
                        perception,
                        prior_cases[mode][-MAX_MEMORY_CASES:],
                        limit=5,
                    )
                    decision = decide_shadow(
                        perception,
                        memory,
                        max_snapshot_age_seconds=max_age,
                    )
                    action = str(decision.get("research_action") or "NO_TRADE")
                    counts[action] += 1
                    for blocker in decision.get("research_blockers") or []:
                        blocker_counts[str(blocker)] += 1

                    due = click_at + timedelta(minutes=PRIMARY_HORIZON_MINUTES)
                    outcome = resolve_historical_candidate(
                        perception,
                        decision,
                        snapshots_by_symbol[symbol],
                        histories.get(symbol, {}).get("5m", []),
                        click_at=click_at,
                    )
                    case = {
                        "perception": perception,
                        "research_action": action,
                        "outcome": outcome,
                        "outcome_available_at": due.isoformat(),
                    }

                    if action in {"BUY_CE", "BUY_PE"}:
                        row = _candidate_row(
                            mode=mode,
                            click_at=click_at,
                            symbol=symbol,
                            perception=perception,
                            technical=technical,
                            decision=decision,
                            outcome=outcome,
                        )
                        click_candidate_rows.append(row)
                        mode_candidates[mode].append(row)

                    state.setdefault("cases_to_append", {})[mode] = case

                click_candidate_rows.sort(key=_reporting_rank, reverse=True)
                click_record = {
                    "trade_date": trade_date.isoformat(),
                    "click_at": click_at.isoformat(),
                    "click_ist": click_at.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                    "scheduled": True,
                    "symbols_in_replay_universe": len(replayable_symbols),
                    "snapshot_available_symbols": snapshot_available,
                    "research_action_counts": dict(counts),
                    "research_blocker_counts": dict(blocker_counts),
                    "actionable_candidates": len(click_candidate_rows),
                    "display_top_candidate": click_candidate_rows[0] if click_candidate_rows else None,
                    "display_top_candidate_is_policy_selection": False,
                }
                mode_clicks[mode].append(click_record)

            for state in states:
                for mode, case in (state.get("cases_to_append") or {}).items():
                    prior_cases[mode].append(case)

    symbol_slots = len(trade_dates) * len(click_schedule(trade_dates[0])) * len(replayable_symbols)
    results = {}
    for mode in mode_configs:
        results[mode] = {
            "max_snapshot_age_seconds": mode_configs[mode],
            "summary": _aggregate_mode(
                mode, mode_clicks[mode], mode_candidates[mode], symbol_slots
            ),
            "daily": _daily_summary(mode_clicks[mode], mode_candidates[mode]),
            "clicks": mode_clicks[mode],
            "candidate_results": mode_candidates[mode],
        }

    return {
        "mode": MODE,
        "status": "COMPLETED",
        "generated_at": datetime.now(UTC).isoformat(),
        "methodology": {
            "click_schedule_ist": "09:30-15:00 inclusive every 15 minutes",
            "clicks_per_trading_day": len(click_schedule(trade_dates[0])),
            "trade_dates_source": "distinct dates present in fno_option_chain_snapshots",
            "option_chain_source": "saved point-in-time Groww snapshots only",
            "technical_source": "Groww reconstructible historical candles fetched after the fact",
            "technical_no_lookahead": "only fully completed bars; current candle excluded conservatively",
            "timeframes": list(TIMEFRAMES),
            "memory": "strictly prior and descriptive only; does not create/reverse decisions",
            "strict_snapshot_freshness_seconds": STRICT_MAX_SNAPSHOT_AGE_SECONDS,
            "diagnostic_snapshot_freshness_seconds": DIAGNOSTIC_MAX_SNAPSHOT_AGE_SECONDS,
            "primary_outcome_horizon_minutes": PRIMARY_HORIZON_MINUTES,
            "actionable_option_outcome": "same exact trading_symbol from later saved snapshots at/before horizon",
            "bid_ask_execution_pnl": "NOT_AVAILABLE_IN_HISTORICAL_ARCHIVE",
            "historical_option_chain_backfill": False,
            "external_news_macro_replay": False,
            "production_policy_changed": False,
        },
        "coverage": {
            "trade_dates": [item.isoformat() for item in trade_dates],
            "trading_days": len(trade_dates),
            "scheduled_clicks": len(trade_dates) * len(click_schedule(trade_dates[0])),
            "connected_universe_size": len(CONNECTED_UNIVERSE),
            "replayable_symbols": replayable_symbols,
            "replayable_symbol_count": len(replayable_symbols),
            "missing_connected_symbols": missing_connected_symbols,
            "history_fetch_failures": history_failures,
        },
        "results": results,
        "safety": {
            "diagnostic_only": True,
            "ready_for_live_money": False,
            "live_execution": False,
            "capital_committed": 0,
            "futures_trade_generated": False,
            "database_writes": False,
        },
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "FNO_15M_FULL_WINDOW_HISTORICAL_REPLAY_V1_CONTRACT",
        "scheduled_clicks_per_day": 23,
        "click_start_ist": "09:30",
        "click_end_ist": "15:00",
        "click_step_minutes": 15,
        "strict_point_in_time_option_chain": True,
        "future_option_snapshot_in_decision": False,
        "completed_technical_candles_only": True,
        "historical_option_chain_backfill": False,
        "diagnostic_stale_mode_separate": True,
        "display_top_candidate_is_policy_selector": False,
        "database_writes": False,
        "strategy_policy_changed": False,
        "live_execution": False,
        "capital_committed": 0,
        "futures_trade_generation": False,
    }
