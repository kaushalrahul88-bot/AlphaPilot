"""Prospective underlying-only F&O edge-discovery protocol V2.

V2 is a clean forward-only protocol created after the four-stock candle replay.
It freezes the current technical LONG/SHORT/NO_TRADE decision unchanged and adds
outcome-blind *descriptive* move-potential features. Those features are recorded
for later validation only; they cannot create, suppress, flip, or size a trade.

This lane is intentionally separate from option forward validation and never
reads option-chain, option-premium, IV/Greeks, or Futures state.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from . import fno_15m_historical_replay_v1 as technical_core
from .fno_prospective_capture_v1 import assess_market_liveness
from .providers.groww import GrowwProvider

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

PROTOCOL_ID = "FNO_UNDERLYING_PROSPECTIVE_V2_2026-09-07"
CLICKS_PER_DAY = 20
CLICK_START = time(9, 30)
CLICK_END = time(14, 0)
CLICK_STEP_MINUTES = 5
CAPTURE_GRACE_SECONDS = 240
BATCH_SIZE = 4
TIMEFRAMES = ("5m", "15m", "1h")
MIN_RISK_REWARD = 1.5
HORIZONS_MINUTES = (15, 30, 60, 90)
NO_TRADE_LARGE_MOVE_PCT = 0.5

# Keep forward sampling comparable with the previously frozen 44-symbol candle
# universe. LTIM's current NSE ticker is LTM and TATAMOTORS was also outside that
# historical comparability set; neither is silently substituted here.
HISTORICAL_COVERAGE_EXCLUSIONS = frozenset({"LTIM", "TATAMOTORS"})
PRIMARY_UNIVERSE = tuple(sorted(
    (set(GrowwProvider.NSE_CASH_SYMBOLS) | {"NIFTY", "BANKNIFTY"})
    - HISTORICAL_COVERAGE_EXCLUSIONS
))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


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
    pool = click_pool(trade_date)
    digest = hashlib.sha256(
        f"{PROTOCOL_ID}:{trade_date.isoformat()}".encode("utf-8")
    ).digest()
    seed = int.from_bytes(digest[:8], "big", signed=False)
    return sorted(random.Random(seed).sample(pool, CLICKS_PER_DAY))


def schedule_manifest(trade_date: date) -> dict[str, Any]:
    slots = deterministic_clicks(trade_date)
    return {
        "protocol_id": PROTOCOL_ID,
        "trade_date": trade_date.isoformat(),
        "slots_utc": [slot.isoformat() for slot in slots],
        "slots_ist": [slot.astimezone(IST).isoformat() for slot in slots],
        "precommitted_count": len(slots),
        "backfill_allowed": False,
    }


def due_click_slot(now: datetime) -> datetime | None:
    """Return today's due frozen slot only inside the fail-closed capture grace."""
    now = _utc(now)
    local_date = now.astimezone(IST).date()
    if local_date.weekday() >= 5:
        return None
    matches: list[datetime] = []
    for slot in deterministic_clicks(local_date):
        lag = (now - slot).total_seconds()
        if 0 <= lag <= CAPTURE_GRACE_SECONDS:
            matches.append(slot)
    return max(matches) if matches else None


def deterministic_batch(slot_at: datetime) -> list[str]:
    """Rotate four symbols deterministically over the frozen 44-symbol set."""
    slot_at = _utc(slot_at)
    items = list(PRIMARY_UNIVERSE)
    slot_number = int(slot_at.timestamp() // (CLICK_STEP_MINUTES * 60))
    start = (slot_number * BATCH_SIZE) % len(items)
    return [items[(start + index) % len(items)] for index in range(BATCH_SIZE)]


def episode_id(symbol: str, slot_at: datetime) -> str:
    stable = f"{PROTOCOL_ID}|{symbol.upper()}|{_utc(slot_at).isoformat()}"
    return "fnu2-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:27]


def _decision_snapshot(technical: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze the existing technical decision; V2 diagnostics cannot modify it."""
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


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def move_potential_snapshot(technical: Mapping[str, Any]) -> dict[str, Any]:
    """Record outcome-blind magnitude context without producing a trade signal."""
    timeframes = technical.get("timeframes") or {}
    result: dict[str, Any] = {
        "diagnostic_only": True,
        "influences_action": False,
        "derived_before_outcome": True,
        "timeframes": {},
    }
    for timeframe in TIMEFRAMES:
        payload = timeframes.get(timeframe) or {}
        price = _finite_float(payload.get("price"))
        atr = _finite_float(payload.get("atr14"))
        upper = _finite_float(payload.get("bollinger_upper"))
        lower = _finite_float(payload.get("bollinger_lower"))
        atr_pct = (100.0 * atr / price) if price and atr is not None else None
        bbw_pct = (
            100.0 * (upper - lower) / price
            if price and upper is not None and lower is not None
            else None
        )
        result["timeframes"][timeframe] = {
            "atr_pct": round(atr_pct, 8) if atr_pct is not None else None,
            "bollinger_width_pct": round(bbw_pct, 8) if bbw_pct is not None else None,
            "volume_ratio_raw": _finite_float(payload.get("volume_ratio_raw")),
            "rsi14": _finite_float(payload.get("rsi14")),
            "market_structure": payload.get("market_structure"),
            "signal": payload.get("signal"),
            "alpha_score": _finite_float(payload.get("alpha_score")),
        }
    return result


def reference_price(five_minute_history: list[list], slot_at: datetime) -> float | None:
    completed = technical_core.completed_candles_at(
        five_minute_history, slot_at, "5m"
    )
    if not completed:
        return None
    try:
        value = float(completed[-1][4])
    except (TypeError, ValueError, IndexError):
        return None
    return value if value > 0 else None


async def technical_at_live(
    provider,
    symbol: str,
    slot_at: datetime,
) -> tuple[dict, dict[str, list[list]]]:
    """Fetch available history but analyze only bars completed at the frozen slot."""
    histories: dict[str, list[list]] = {}
    tf: dict[str, dict] = {}
    for timeframe in TIMEFRAMES:
        try:
            rows = await provider.candles(symbol, timeframe)
            histories[timeframe] = list(rows or [])
            completed = technical_core.completed_candles_at(
                histories[timeframe], slot_at, timeframe
            )
            from .engine import analyze_candles
            tf[timeframe] = analyze_candles(symbol, completed, MIN_RISK_REWARD)
        except Exception as exc:
            histories.setdefault(timeframe, [])
            tf[timeframe] = {
                "symbol": symbol,
                "status": "ERROR",
                "error": f"{exc.__class__.__name__}: {str(exc)[:180]}",
            }
    return technical_core._combine_timeframes(symbol, tf), histories


async def capture_due_underlying_batch(
    provider,
    store,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    capture_started_at = _utc(now or datetime.now(UTC))
    slot_at = due_click_slot(capture_started_at)
    if slot_at is None:
        return {
            "status": "SKIPPED_NOT_A_PRECOMMITTED_CLICK",
            "captured_at": capture_started_at.isoformat(),
            "live_execution": False,
            "capital_committed": 0,
        }

    try:
        quote = await provider.quote("NIFTY")
        market_liveness = assess_market_liveness(quote, now=capture_started_at)
    except Exception as exc:
        market_liveness = {
            "status": "UNPROVEN",
            "symbol": "NIFTY",
            "reason": f"QUOTE_ERROR:{exc.__class__.__name__}",
            "live": False,
        }
    if not market_liveness.get("live"):
        return {
            "status": "SKIPPED_MARKET_LIVENESS_UNPROVEN",
            "capture_slot_at": slot_at.isoformat(),
            "captured_at": capture_started_at.isoformat(),
            "market_liveness": market_liveness,
            "live_execution": False,
            "capital_committed": 0,
        }

    symbols = deterministic_batch(slot_at)
    captured: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for batch_index, symbol in enumerate(symbols):
        try:
            technical, histories = await technical_at_live(provider, symbol, slot_at)
            decision = _decision_snapshot(technical)
            move_potential = move_potential_snapshot(technical)
            start = reference_price(histories.get("5m", []), slot_at)
            if start is None:
                raise RuntimeError("REFERENCE_PRICE_UNAVAILABLE")
            frozen_at = datetime.now(UTC)
            record = {
                "protocol_id": PROTOCOL_ID,
                "episode_id": episode_id(symbol, slot_at),
                "capture_slot_at": slot_at.isoformat(),
                "capture_started_at": capture_started_at.isoformat(),
                "captured_at": frozen_at.isoformat(),
                "capture_latency_seconds": round(
                    (capture_started_at - slot_at).total_seconds(), 3
                ),
                "underlying_symbol": symbol,
                "batch_index": batch_index,
                "reference_price": start,
                "decision": decision,
                "technical": technical,
                "move_potential": move_potential,
                "market_liveness": market_liveness,
                "future_outcome_present_in_decision": False,
                "outcome_used_for_decision": False,
                "move_potential_influenced_decision": False,
                "option_chain_used": False,
                "option_premium_used": False,
                "option_oi_iv_greeks_used": False,
                "futures_used": False,
                "broker_orders_created": False,
                "live_execution": False,
                "capital_committed": 0,
            }
            stored = await store.insert_episode(record)
            captured.append({
                "symbol": symbol,
                "batch_index": batch_index,
                "episode_id": record["episode_id"],
                "store_status": stored.get("status"),
                "action": decision["action"],
                "reference_price": start,
            })
        except Exception as exc:
            failures.append({
                "symbol": symbol,
                "batch_index": batch_index,
                "error": f"{exc.__class__.__name__}: {str(exc)[:300]}",
            })

    return {
        "status": "CAPTURED" if not failures else "PARTIAL" if captured else "FAILED",
        "protocol_id": PROTOCOL_ID,
        "capture_slot_at": slot_at.isoformat(),
        "capture_started_at": capture_started_at.isoformat(),
        "capture_latency_seconds": round(
            (capture_started_at - slot_at).total_seconds(), 3
        ),
        "selected_symbols": symbols,
        "market_liveness": market_liveness,
        "captured": captured,
        "failed": failures,
        "live_execution": False,
        "capital_committed": 0,
        "strategy_policy_changed": False,
        "move_potential_diagnostic_only": True,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "version": PROTOCOL_ID,
        "prospective": True,
        "underlying_only": True,
        "precommitted_random_clicks_per_day": CLICKS_PER_DAY,
        "click_pool_ist": "09:30-14:00 inclusive, 5-minute aligned",
        "capture_grace_seconds": CAPTURE_GRACE_SECONDS,
        "primary_universe_size": len(PRIMARY_UNIVERSE),
        "batch_size": BATCH_SIZE,
        "deterministic_rotation": True,
        "completed_candles_only": True,
        "timeframes": list(TIMEFRAMES),
        "backfill_allowed": False,
        "move_potential_recorded": True,
        "move_potential_influences_action": False,
        "options_used_for_decision": False,
        "futures_used_for_decision": False,
        "news_macro_used_for_primary_decision": False,
        "market_liveness_required": True,
        "future_outcomes_used": False,
        "live_execution": False,
        "broker_orders": False,
        "capital_committed": 0,
    }
