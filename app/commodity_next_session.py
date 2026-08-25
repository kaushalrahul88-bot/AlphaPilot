from __future__ import annotations

from datetime import date, datetime, time, timedelta
import math
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_backtest import _fetch_chunked, _ts
from .commodities import SUPPORTED_COMMODITIES, resolve_nearest_mcx_future
from .news import latest_commodity_news


IST = ZoneInfo("Asia/Kolkata")
PROTOCOL_REVISION = "commodity-next-session-v1-2026-08-26-r3"
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(23, 30)
SYMBOLS = ("CRUDEOIL", "NATURALGAS")


def _f(value, default=0.0):
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _ema(values, period):
    if not values:
        return 0.0
    multiplier = 2.0 / (period + 1)
    current = values[0]
    for value in values[1:]:
        current = value * multiplier + current * (1.0 - multiplier)
    return current


def _floor_to_tick(value, tick):
    return round(math.floor((value + 1e-10) / tick) * tick, 10) if tick > 0 else value


def _ceil_to_tick(value, tick):
    return round(math.ceil((value - 1e-10) / tick) * tick, 10) if tick > 0 else value


def _session_rows(rows, session_date):
    return [
        row
        for row in rows
        if isinstance(row, (list, tuple))
        and len(row) >= 5
        and _ts(row[0]).date() == session_date
        and SESSION_OPEN <= _ts(row[0]).time() <= SESSION_CLOSE
        and min(_f(row[1]), _f(row[2]), _f(row[3]), _f(row[4])) > 0
    ]


def _group_sessions(rows, through_date):
    grouped = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        stamp = _ts(row[0])
        if stamp.date() > through_date or not (SESSION_OPEN <= stamp.time() <= SESSION_CLOSE):
            continue
        if min(_f(row[1]), _f(row[2]), _f(row[3]), _f(row[4])) <= 0:
            continue
        grouped.setdefault(stamp.date(), []).append(row)
    return {key: sorted(value, key=lambda row: _ts(row[0])) for key, value in grouped.items()}


def _daily_range_metrics(sessions):
    output = []
    previous_close = None
    for session_date in sorted(sessions):
        rows = sessions[session_date]
        high = max(_f(row[2]) for row in rows)
        low = min(_f(row[3]) for row in rows)
        close = _f(rows[-1][4])
        true_range = high - low
        if previous_close is not None:
            true_range = max(true_range, abs(high - previous_close), abs(low - previous_close))
        output.append({"date": session_date, "high": high, "low": low, "close": close, "true_range": true_range})
        previous_close = close
    return output


def build_next_session_plan(symbol, rows, observation_date, target_date, tick_size=None):
    sessions = _group_sessions(rows, observation_date)
    observed = sessions.get(observation_date, [])
    if len(observed) < 60:
        return {
            "symbol": symbol,
            "observation_date": observation_date.isoformat(),
            "target_date": target_date.isoformat(),
            "status": "DATA_ERROR",
            "action": "NO TRADE",
            "reason": "At least 60 completed 5-minute candles are required for the observed MCX session.",
            "observed_candles": len(observed),
        }

    prior_dates = sorted(value for value in sessions if value < observation_date)
    prior = sessions[prior_dates[-1]] if prior_dates else None
    closes = [_f(row[4]) for key in sorted(sessions) for row in sessions[key] if key <= observation_date]
    session_open = _f(observed[0][1])
    session_close = _f(observed[-1][4])
    session_high = max(_f(row[2]) for row in observed)
    session_low = min(_f(row[3]) for row in observed)
    session_range = session_high - session_low
    if session_open <= 0 or session_range <= 0:
        return {
            "symbol": symbol,
            "observation_date": observation_date.isoformat(),
            "target_date": target_date.isoformat(),
            "status": "DATA_ERROR",
            "action": "NO TRADE",
            "reason": "Observed session has invalid open/range geometry.",
            "observed_candles": len(observed),
        }

    late_rows = [row for row in observed if _ts(row[0]).time() >= time(22, 30)]
    late_open = _f(late_rows[0][1]) if late_rows else session_close
    session_return_pct = (session_close / session_open - 1.0) * 100.0
    close_location = (session_close - session_low) / session_range
    late_return_pct = (session_close / late_open - 1.0) * 100.0 if late_open else 0.0
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)

    votes = []
    votes.append(("session_return", 1 if session_return_pct >= 0.15 else -1 if session_return_pct <= -0.15 else 0))
    votes.append(("close_location", 1 if close_location >= 0.70 else -1 if close_location <= 0.30 else 0))
    votes.append(("last_hour_momentum", 1 if late_return_pct >= 0.10 else -1 if late_return_pct <= -0.10 else 0))
    votes.append(("ema_regime", 1 if session_close > ema20 > ema50 else -1 if session_close < ema20 < ema50 else 0))

    structure_vote = 0
    prior_high = prior_low = None
    if prior:
        prior_high = max(_f(row[2]) for row in prior)
        prior_low = min(_f(row[3]) for row in prior)
        structure_vote = 1 if session_high > prior_high and session_low > prior_low else -1 if session_high < prior_high and session_low < prior_low else 0
    votes.append(("session_structure", structure_vote))
    directional_score = sum(value for _, value in votes)
    action = "BUY" if directional_score >= 3 else "SELL" if directional_score <= -3 else "NO TRADE"

    daily = _daily_range_metrics(sessions)
    ranges = [item["true_range"] for item in daily[-10:] if item["true_range"] > 0]
    daily_atr = mean(ranges) if ranges else session_range
    observed_volume = sum(max(0.0, _f(row[5])) for row in observed if len(row) > 5)
    prior_volumes = [sum(max(0.0, _f(row[5])) for row in sessions[key] if len(row) > 5) for key in prior_dates[-5:]]
    valid_prior_volumes = [value for value in prior_volumes if value > 0]
    volume_ratio = observed_volume / mean(valid_prior_volumes) if observed_volume > 0 and valid_prior_volumes else None

    features = {
        "session_open": round(session_open, 4),
        "session_high": round(session_high, 4),
        "session_low": round(session_low, 4),
        "session_close": round(session_close, 4),
        "session_return_pct": round(session_return_pct, 3),
        "close_location_pct": round(close_location * 100.0, 1),
        "last_hour_return_pct": round(late_return_pct, 3),
        "ema20": round(ema20, 4),
        "ema50": round(ema50, 4),
        "prior_session_high": round(prior_high, 4) if prior_high is not None else None,
        "prior_session_low": round(prior_low, 4) if prior_low is not None else None,
        "daily_atr": round(daily_atr, 4),
        "session_volume": round(observed_volume, 2) if observed_volume > 0 else None,
        "volume_vs_prior_5": round(volume_ratio, 3) if volume_ratio is not None else None,
        "votes": {name: value for name, value in votes},
        "directional_score": directional_score,
    }
    base = {
        "symbol": symbol,
        "observation_date": observation_date.isoformat(),
        "target_date": target_date.isoformat(),
        "observed_candles": len(observed),
        "last_observed_candle_at": _ts(observed[-1][0]).isoformat(),
        "features": features,
        "action": action,
        "confidence_pct": round(abs(directional_score) / 5.0 * 100.0, 1),
    }
    if action == "NO TRADE":
        return {**base, "status": "NO_TRADE", "reason": "Fewer than 3 of 5 frozen directional votes agree."}

    tick = max(_f(tick_size), 0.0)
    buffer_distance = max(0.05 * daily_atr, tick)
    risk_distance = max(0.75 * daily_atr, 0.40 * session_range)
    raw_entry = session_high + buffer_distance if action == "BUY" else session_low - buffer_distance
    entry = _ceil_to_tick(raw_entry, tick) if action == "BUY" else _floor_to_tick(raw_entry, tick)
    raw_stop = entry - risk_distance if action == "BUY" else entry + risk_distance
    stop = _floor_to_tick(raw_stop, tick) if action == "BUY" else _ceil_to_tick(raw_stop, tick)
    aligned_risk = abs(entry - stop)
    raw_target = entry + 1.5 * aligned_risk if action == "BUY" else entry - 1.5 * aligned_risk
    target = _ceil_to_tick(raw_target, tick) if action == "BUY" else _floor_to_tick(raw_target, tick)
    invalidation = session_low if action == "BUY" else session_high
    return {
        **base,
        "status": "SETUP",
        "entry_type": "NEXT_SESSION_STOP_TRIGGER",
        "entry": round(entry, 4),
        "stop_loss": round(stop, 4),
        "target1": round(target, 4),
        "risk_points": round(aligned_risk, 4),
        "risk_reward": 1.5,
        "invalidation": round(invalidation, 4),
        "reason": f"{abs(directional_score)} of 5 frozen directional votes agree on {action}.",
    }


def score_next_session(plan, target_rows, slippage_bps=2.0, cost_bps=2.0):
    if plan.get("status") != "SETUP":
        return {"outcome": "NO_TRADE", "r_multiple": 0.0, "entry_time": None, "exit_time": None}
    rows = sorted(target_rows, key=lambda row: _ts(row[0]))
    if not rows:
        return {"outcome": "TARGET_SESSION_UNAVAILABLE", "r_multiple": 0.0, "entry_time": None, "exit_time": None}

    action = plan["action"]
    entry = _f(plan["entry"])
    stop = _f(plan["stop_loss"])
    target = _f(plan["target1"])
    risk = abs(entry - stop)
    entered_at = None
    for row in rows:
        stamp = _ts(row[0])
        high, low = _f(row[2]), _f(row[3])
        entry_hit = high >= entry if action == "BUY" else low <= entry
        if entered_at is None:
            if not entry_hit:
                continue
            entered_at = stamp
            stop_hit = low <= stop if action == "BUY" else high >= stop
            target_hit = high >= target if action == "BUY" else low <= target
            if stop_hit or target_hit:
                return {"outcome": "AMBIGUOUS_ENTRY_BAR", "r_multiple": 0.0, "entry_time": stamp.isoformat(), "exit_time": stamp.isoformat()}
            continue

        stop_hit = low <= stop if action == "BUY" else high >= stop
        target_hit = high >= target if action == "BUY" else low <= target
        if stop_hit and target_hit:
            return {"outcome": "AMBIGUOUS_EXIT_BAR", "r_multiple": 0.0, "entry_time": entered_at.isoformat(), "exit_time": stamp.isoformat()}
        if stop_hit:
            gross_r = -1.0
            return {"outcome": "SL_HIT", "r_multiple": round(gross_r - _cost_r(entry, risk, slippage_bps, cost_bps), 3), "entry_time": entered_at.isoformat(), "exit_time": stamp.isoformat()}
        if target_hit:
            gross_r = 1.5
            return {"outcome": "TARGET_HIT", "r_multiple": round(gross_r - _cost_r(entry, risk, slippage_bps, cost_bps), 3), "entry_time": entered_at.isoformat(), "exit_time": stamp.isoformat()}

    if entered_at is None:
        return {"outcome": "NO_ENTRY", "r_multiple": 0.0, "entry_time": None, "exit_time": _ts(rows[-1][0]).isoformat()}
    exit_price = _f(rows[-1][4])
    gross_r = ((exit_price - entry) / risk) if action == "BUY" else ((entry - exit_price) / risk)
    return {
        "outcome": "SESSION_CLOSE_EXIT",
        "r_multiple": round(gross_r - _cost_r(entry, risk, slippage_bps, cost_bps), 3),
        "entry_time": entered_at.isoformat(),
        "exit_time": _ts(rows[-1][0]).isoformat(),
        "exit_price": round(exit_price, 4),
    }


def _cost_r(entry, risk, slippage_bps, cost_bps):
    round_trip_fraction = 2.0 * (max(0.0, slippage_bps) + max(0.0, cost_bps)) / 10000.0
    return entry * round_trip_fraction / risk if risk > 0 else 0.0


def _news_context(payload, historical):
    if historical:
        return {
            "status": "UNAVAILABLE_NOT_RECONSTRUCTED",
            "decision_role": "CONTEXT_ONLY_NOT_BACKTEST_GATE",
            "reason": "AlphaPilot did not capture a timestamped news ledger for this historical observation session.",
            "items": [],
        }
    items = payload.get("items", []) if isinstance(payload, dict) else []
    counts = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}
    for item in items:
        sentiment = str(item.get("sentiment") or "NEUTRAL").upper()
        counts[sentiment if sentiment in counts else "NEUTRAL"] += 1
    return {
        "status": "CURRENT_CONTEXT_AVAILABLE",
        "decision_role": "CONTEXT_ONLY_NOT_BACKTEST_GATE",
        "generated_at": payload.get("generated_at"),
        "provider": payload.get("provider"),
        "event_tags": payload.get("event_tags", []),
        "sentiment_counts": counts,
        "items": items,
    }


async def run_commodity_next_session(provider, observation_date_text, target_date_text, include_outcome, include_news=True):
    observation_date = date.fromisoformat(observation_date_text)
    target_date = date.fromisoformat(target_date_text)
    if target_date <= observation_date or (target_date - observation_date).days > 4:
        raise ValueError("Target date must be the next MCX weekday session, no more than four calendar days after observation.")
    now = datetime.now(IST)
    if observation_date >= now.date():
        raise ValueError("Observation date must be a fully completed prior IST session.")
    if include_outcome and target_date >= now.date():
        raise ValueError("Outcome scoring is allowed only for a completed target session.")

    results = []
    for symbol in SYMBOLS:
        if symbol not in SUPPORTED_COMMODITIES:
            continue
        contract = await resolve_nearest_mcx_future(symbol)
        fetch_start = datetime.combine(observation_date - timedelta(days=14), SESSION_OPEN, tzinfo=IST)
        fetch_end_date = target_date if include_outcome else observation_date
        fetch_end = datetime.combine(fetch_end_date, SESSION_CLOSE, tzinfo=IST)
        rows = await _fetch_chunked(provider, contract, 5, fetch_start, fetch_end)
        plan = build_next_session_plan(symbol, rows, observation_date, target_date, contract.get("tick_size"))
        outcome = score_next_session(plan, _session_rows(rows, target_date)) if include_outcome else None
        historical = include_outcome or target_date < now.date()
        news_payload = {}
        news_error = None
        if include_news and not historical:
            try:
                news_payload = await latest_commodity_news(symbol, 6)
            except Exception as exc:
                news_error = f"{exc.__class__.__name__}: {str(exc)[:180]}"
        news = _news_context(news_payload, historical)
        if news_error:
            news = {"status": "CURRENT_CONTEXT_ERROR", "decision_role": "CONTEXT_ONLY_NOT_BACKTEST_GATE", "reason": news_error, "items": []}
        results.append({**plan, "contract": contract, "outcome": outcome, "news_context": news})

    return {
        "mode": "ALPHAPILOT_COMMODITY_NEXT_SESSION_V1",
        "protocol_revision": PROTOCOL_REVISION,
        "generated_at": now.isoformat(),
        "observation_date": observation_date.isoformat(),
        "target_date": target_date.isoformat(),
        "include_outcome": bool(include_outcome),
        "symbols": list(SYMBOLS),
        "fixed_protocol": {
            "observation_window_ist": "09:00-23:30 completed prior MCX session",
            "features": ["session_return", "close_location", "last_hour_momentum", "ema20_ema50_regime", "session_structure"],
            "minimum_agreeing_votes": 3,
            "entry": "prior-session high/low breakout plus 0.05 daily ATR buffer",
            "stop": "max(0.75 daily ATR, 40% observed session range)",
            "target_r": 1.5,
            "session_exit": "23:30 IST final available 5m close",
            "slippage_bps_each_side": 2.0,
            "cost_bps_each_side": 2.0,
            "max_risk_per_trade_pct": 1.0,
            "news_role": "current context only; never reconstructed and not a backtest gate",
            "provider_unit_normalization": "MCX authoritative rupee tick sizes: CRUDEOIL 1.00, NATURALGAS 0.10; all plan levels aligned conservatively to legal ticks",
        },
        "research_only": True,
        "production_rules_changed": False,
        "paper_trading_permission_changed": False,
        "live_execution_enabled": False,
        "results": results,
    }
