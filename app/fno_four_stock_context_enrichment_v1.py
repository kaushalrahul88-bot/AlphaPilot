"""Point-in-time context enrichment for the frozen four-stock F&O direction study.

This module deliberately sits beside, rather than modifies, the validated
candle-only baseline.  It reuses the baseline's exact 1,600 observations and
adds only information that could have been known at or before each click.

Research/shadow only: no option-chain, option-premium, IV, Greeks, futures,
orders, or capital are read or produced here.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import fmean, median
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from . import fno_candle_only_four_stock_backtest_v1 as baseline

IST = ZoneInfo("Asia/Kolkata")
PROTOCOL_ID = "FNO_FOUR_STOCK_CONTEXT_ENRICHMENT_V1_2026-09-07"

# Frozen before observing enriched outcomes.  These are cash-equity peers only;
# they are context inputs, never alternate trade candidates.
PEER_BASKETS = {
    "ONGC": ("RELIANCE", "COALINDIA", "NTPC", "POWERGRID"),
    "LTIM": ("TCS", "INFY", "HCLTECH", "WIPRO", "TECHM"),
    "SBIN": ("HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK"),
    "SUNPHARMA": ("DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP"),
}
MARKET_SYMBOLS = ("NIFTY", "BANKNIFTY")
CONTEXT_SYMBOLS = tuple(sorted(set(MARKET_SYMBOLS).union(*map(set, PEER_BASKETS.values()))))

# Context thresholds are protocol constants, not fitted to the 1,600 outcomes.
MOMENTUM_LOOKBACK_MINUTES = 60
MOMENTUM_DEADBAND_PCT = 0.15
RELATIVE_STRENGTH_DEADBAND_PCT = 0.10
PROMOTE_SCORE = 4
VETO_SCORE = 3
EVENT_MAX_AGE_DAYS = 5

EVENT_ARCHIVE = Path(__file__).resolve().parent.parent / "data" / "fno_four_stock_context_events_v1.json"


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _stamp(row) -> datetime | None:
    value = baseline._stamp(row[0]) if isinstance(row, (list, tuple)) and row else None
    return value.astimezone(IST) if value is not None else None


def _close(row) -> float | None:
    try:
        value = float(row[4])
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _completed_rows(rows: Iterable, click: datetime, timeframe_minutes: int = 15):
    cutoff = _dt(click)
    output = []
    for row in rows or []:
        stamp = _stamp(row)
        if stamp is None:
            continue
        # A candle stamped at its start is usable only after it has completed.
        if stamp + timedelta(minutes=timeframe_minutes) <= cutoff:
            output.append((stamp, row))
    return [row for _, row in sorted(output, key=lambda item: item[0])]


def _momentum(rows, click: datetime, lookback_minutes: int = MOMENTUM_LOOKBACK_MINUTES):
    completed = _completed_rows(rows, click, 15)
    if len(completed) < 2:
        return None
    latest_stamp = _stamp(completed[-1])
    if latest_stamp is None:
        return None
    target = latest_stamp - timedelta(minutes=lookback_minutes)
    prior = None
    for row in reversed(completed[:-1]):
        stamp = _stamp(row)
        if stamp is not None and stamp <= target:
            prior = row
            break
    if prior is None:
        return None
    start, end = _close(prior), _close(completed[-1])
    if not start or not end:
        return None
    return round(100.0 * (end / start - 1.0), 6)


def _sign(value: float | None, deadband: float = MOMENTUM_DEADBAND_PCT) -> int:
    if value is None or not math.isfinite(value):
        return 0
    if value > deadband:
        return 1
    if value < -deadband:
        return -1
    return 0


def _load_events(path: Path = EVENT_ARCHIVE):
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events", payload) if isinstance(payload, Mapping) else payload
    return list(events or [])


def events_at(symbol: str, click: datetime, events=None):
    """Return only events whose conservative effective timestamp is <= click."""
    observed = _dt(click)
    output = []
    for event in events if events is not None else _load_events():
        if str(event.get("symbol") or "").upper() not in {symbol.upper(), "MARKET"}:
            continue
        effective = _dt(event["effective_at"])
        if effective > observed:
            continue
        age = observed - effective
        if age < timedelta(0) or age > timedelta(days=EVENT_MAX_AGE_DAYS):
            continue
        output.append({**event, "age_hours": round(age.total_seconds() / 3600.0, 3)})
    return sorted(output, key=lambda item: item["effective_at"])


def _event_score(events) -> int:
    # Direction must be assigned in the frozen archive from the event itself,
    # never from subsequent price action.  Unknown/ambiguous events score zero.
    score = 0
    for event in events:
        direction = str(event.get("direction") or "NEUTRAL").upper()
        weight = int(event.get("weight") or 0)
        weight = max(0, min(weight, 2))
        if direction == "POSITIVE":
            score += weight
        elif direction == "NEGATIVE":
            score -= weight
    return max(-2, min(2, score))


def _technical_vote(technical) -> int:
    frames = (technical or {}).get("timeframes") or {}
    weights = {"5m": 1, "15m": 2, "1h": 2}
    score = 0
    for timeframe, weight in weights.items():
        signal = str((frames.get(timeframe) or {}).get("signal") or "").upper()
        if signal in {"BUY", "LONG"}:
            score += weight
        elif signal in {"SELL", "SHORT"}:
            score -= weight
    return score


def context_at(symbol: str, click: datetime, context_histories, stock_histories, events=None):
    market = _momentum(context_histories.get("NIFTY", []), click)
    bank = _momentum(context_histories.get("BANKNIFTY", []), click) if symbol == "SBIN" else None
    peers = {
        peer: _momentum(context_histories.get(peer, []), click)
        for peer in PEER_BASKETS[symbol]
    }
    peer_values = [value for value in peers.values() if value is not None]
    peer_mean = round(fmean(peer_values), 6) if peer_values else None
    peer_breadth = (
        round(sum(1 if value > 0 else -1 if value < 0 else 0 for value in peer_values) / len(peer_values), 6)
        if peer_values else None
    )
    stock = _momentum(stock_histories.get(symbol, []), click)
    relative = round(stock - market, 6) if stock is not None and market is not None else None
    eligible_events = events_at(symbol, click, events)

    components = {
        "market": _sign(market),
        "sector": _sign(peer_mean),
        "relative_strength": _sign(relative, RELATIVE_STRENGTH_DEADBAND_PCT),
        "bank_index": _sign(bank) if symbol == "SBIN" else 0,
        "events": _event_score(eligible_events),
    }
    return {
        "market_60m_pct": market,
        "banknifty_60m_pct": bank,
        "stock_60m_pct": stock,
        "relative_strength_vs_nifty_pct": relative,
        "peer_60m_pct": peers,
        "peer_mean_60m_pct": peer_mean,
        "peer_breadth": peer_breadth,
        "events": eligible_events,
        "components": components,
        "context_score": sum(components.values()),
        "complete": market is not None and stock is not None and len(peer_values) >= max(2, len(PEER_BASKETS[symbol]) - 1),
    }


def enriched_decision(base_decision, technical, context):
    """Frozen, outcome-agnostic overlay used only for shadow comparison.

    - Existing actionable calls remain unless context is strongly opposite.
    - Existing NO_TRADE is promoted only when technical votes and independent
      context are both strongly aligned.
    - Missing context can never create a trade.
    """
    base = str((base_decision or {}).get("action") or "NO_TRADE").upper()
    technical_score = _technical_vote(technical)
    context_score = int((context or {}).get("context_score") or 0)
    complete = (context or {}).get("complete") is True

    action = base if base in {"LONG", "SHORT"} else "NO_TRADE"
    reason = "BASELINE_PRESERVED"
    if not complete:
        return {"action": action, "reason": "CONTEXT_INCOMPLETE_FAIL_CLOSED", "technical_vote": technical_score, "context_score": context_score}

    if action == "LONG" and context_score <= -VETO_SCORE:
        action, reason = "NO_TRADE", "STRONG_CONTEXT_VETO_LONG"
    elif action == "SHORT" and context_score >= VETO_SCORE:
        action, reason = "NO_TRADE", "STRONG_CONTEXT_VETO_SHORT"
    elif action == "NO_TRADE" and technical_score >= 3 and context_score >= PROMOTE_SCORE:
        action, reason = "LONG", "CONTEXT_PROMOTED_LONG"
    elif action == "NO_TRADE" and technical_score <= -3 and context_score <= -PROMOTE_SCORE:
        action, reason = "SHORT", "CONTEXT_PROMOTED_SHORT"

    return {"action": action, "reason": reason, "technical_vote": technical_score, "context_score": context_score}


def architecture_contract():
    return {
        "version": PROTOCOL_ID,
        "frozen_baseline_protocol": baseline.PROTOCOL_ID,
        "frozen_stocks": [symbol for symbol, _ in baseline.FROZEN_STOCKS],
        "same_click_schedule_as_baseline": True,
        "completed_candles_only": True,
        "point_in_time_events_only": True,
        "event_after_click_forbidden": True,
        "market_cash_context": list(MARKET_SYMBOLS),
        "peer_baskets": {key: list(value) for key, value in PEER_BASKETS.items()},
        "options_read": False,
        "option_chain_read": False,
        "option_premium_read": False,
        "iv_read": False,
        "greeks_read": False,
        "futures_read": False,
        "live_execution": False,
        "capital_committed": 0,
        "outcome_used_to_build_context": False,
        "thresholds_frozen_before_enriched_outcomes": True,
    }
