from __future__ import annotations

from datetime import date, datetime, time, timedelta
from hashlib import sha256
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_backtest import _fetch_chunked, _plan_at, _resolve_trade, _ts
from .commodity_benchmarks import benchmark_confirmation, fetch_benchmark_candles
from .commodity_click_brain import _valid_rows, evaluate_commodity_click, market_brain_audit
from .commodity_next_session import build_next_session_plan
from .commodities import analyze_commodity_candles, resolve_nearest_mcx_future


IST = ZoneInfo("Asia/Kolkata")
SYMBOLS = ("CRUDEOIL", "NATURALGAS")
CLICK_TIMES = ("09:35", "10:55", "11:05", "13:20", "13:35", "15:15", "15:25", "16:15", "16:40", "18:35")
WEEKLY_CLICK_TIMES = tuple(f"{hour:02d}:00" for hour in range(10, 20))
WEEKLY_SESSION_PAIRS = (
    (date(2026, 8, 17), date(2026, 8, 18)),
    (date(2026, 8, 18), date(2026, 8, 19)),
    (date(2026, 8, 19), date(2026, 8, 20)),
    (date(2026, 8, 20), date(2026, 8, 21)),
    (date(2026, 8, 21), date(2026, 8, 24)),
)
EXTENDED_TARGET_DATES = tuple(
    day
    for day in (date(2026, 7, 29) + timedelta(days=offset) for offset in range(28))
    if day.weekday() < 5
)
EXTENDED_SESSION_PAIRS = tuple(
    (target - timedelta(days=3 if target.weekday() == 0 else 1), target)
    for target in EXTENDED_TARGET_DATES
)
VALIDATION_TARGET_DATES = tuple(
    day
    for day in (date(2026, 7, 1) + timedelta(days=offset) for offset in range(28))
    if day.weekday() < 5
)
VALIDATION_SESSION_PAIRS = tuple(
    (target - timedelta(days=3 if target.weekday() == 0 else 1), target)
    for target in VALIDATION_TARGET_DATES
)
IDENTIFIED_SETUP_AUDIT_POINTS = (
    {"symbol": "NATURALGAS", "observation_date": date(2026, 8, 7), "target_date": date(2026, 8, 10), "click_time_ist": "19:05"},
    {"symbol": "CRUDEOIL", "observation_date": date(2026, 8, 17), "target_date": date(2026, 8, 18), "click_time_ist": "12:10"},
    {"symbol": "CRUDEOIL", "observation_date": date(2026, 8, 24), "target_date": date(2026, 8, 25), "click_time_ist": "21:10"},
)
MIN_TARGET_CANDLES = {"5m": 100, "15m": 30, "1h": 8}
EXTENDED_CLICK_SALT = "alphapilot-frozen-20-session-click-v1"
VALIDATION_CLICK_SALT = "alphapilot-frozen-july-validation-v1"


def _click(day, text):
    hour, minute = [int(value) for value in text.split(":")]
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


def _deterministic_click_times(target_date, salt):
    """Select ten reproducible, result-independent 5-minute slots from 10:00-22:00 IST."""
    slots = range(10 * 60, 22 * 60 + 1, 5)
    ranked = sorted(
        slots,
        key=lambda minute: sha256(
            f"{salt}|{target_date.isoformat()}|{minute}".encode()
        ).digest(),
    )
    selected = sorted(ranked[:10])
    return tuple(f"{minute // 60:02d}:{minute % 60:02d}" for minute in selected)


def _extended_click_times(target_date):
    return _deterministic_click_times(target_date, EXTENDED_CLICK_SALT)


def _validation_click_times(target_date):
    return _deterministic_click_times(target_date, VALIDATION_CLICK_SALT)


def _click_schedule(session_pairs, selector):
    return {target.isoformat(): tuple(selector(target)) for _, target in session_pairs}


def _strict_slice(rows, click):
    return [row for row in rows if _ts(row[0]) < click][-260:]


def _historical_mtf(rows_by_timeframe, click):
    frames = {
        timeframe: analyze_commodity_candles("", _strict_slice(rows_by_timeframe[timeframe], click), 1.5)
        for timeframe in ("5m", "15m", "1h")
    }
    plan = _plan_at("", frames, 1.5, 65.0)
    return frames, plan, {
        "action": plan.get("action") if plan else "NO TRADE",
        "alpha_score": plan.get("strength") if plan else 50.0,
        "fresh_market_data": True,
    }


def _data_quality(symbol, contract, rows_by_timeframe, benchmark_payload, previous, target_date):
    normalized_by_timeframe = {
        timeframe: _valid_rows(rows)
        for timeframe, rows in rows_by_timeframe.items()
    }
    target_by_timeframe = {
        timeframe: [row for row in rows if _ts(row[0]).date() == target_date]
        for timeframe, rows in normalized_by_timeframe.items()
    }
    target_5m = target_by_timeframe["5m"]
    comparison_dates = {
        _ts(row[0]).date()
        for row in normalized_by_timeframe["5m"]
        if _ts(row[0]).date() < target_date and len(row) > 5 and float(row[5] or 0) > 0
    }
    first = _ts(target_5m[0][0]) if target_5m else None
    last = _ts(target_5m[-1][0]) if target_5m else None
    checks = {
        "target_candle_counts": all(
            len(target_by_timeframe[timeframe]) >= minimum
            for timeframe, minimum in MIN_TARGET_CANDLES.items()
        ),
        "target_session_start": first is not None and first.time() <= time(9, 15),
        "target_session_through_last_click": last is not None and last.time() >= time(18, 35),
        "target_volume": sum(float(row[5] or 0) for row in target_5m if len(row) > 5) > 0,
        "comparison_sessions": len(comparison_dates) >= 5,
        "first_click_gate_handoff": sum(
            1 for row in target_5m if _ts(row[0]) < _click(target_date, CLICK_TIMES[0])
        ) >= 4,
    }
    return {
        "symbol": symbol,
        "contract": contract.get("trading_symbol"),
        "status": "VALID" if all(checks.values()) else "INVALID_TARGET_SESSION_SLICE",
        "checks": checks,
        "candles": {key: len(value) for key, value in rows_by_timeframe.items()},
        "target_candles": {key: len(value) for key, value in target_by_timeframe.items()},
        "target_first_at": first.isoformat() if first else None,
        "target_last_at": last.isoformat() if last else None,
        "target_5m_volume": round(sum(float(row[5] or 0) for row in target_5m if len(row) > 5), 2),
        "comparison_sessions": len(comparison_dates),
        "benchmark": benchmark_payload.get("benchmark_symbol"),
        "benchmark_candles": len(benchmark_payload.get("candles", [])),
        "previous_status": previous.get("status"),
        "previous_direction": previous.get("underlying_direction"),
    }
def _summary(decisions):
    ready = [row for row in decisions if row.get("status") == "READY"]
    completed_outcomes = {"T1_HIT", "SL_HIT"}
    resolved = [row for row in ready if (row.get("outcome") or {}).get("outcome") in completed_outcomes]
    positive = sum(1 for row in resolved if row["outcome"]["r_multiple"] > 0)
    negative = sum(1 for row in resolved if row["outcome"]["r_multiple"] < 0)
    flat = sum(1 for row in resolved if row["outcome"]["r_multiple"] == 0)
    return {
        "independent_click_decisions": len(decisions),
        "ready_setups": len(ready),
        "wait": sum(1 for row in decisions if row.get("status") == "WAIT"),
        "no_trade": sum(1 for row in decisions if row.get("status") == "NO_TRADE"),
        "resolved_underlying_proxies": len(resolved),
        "open_underlying_proxies": sum((row.get("outcome") or {}).get("outcome") == "OPEN" for row in ready),
        "ambiguous_underlying_proxies": sum((row.get("outcome") or {}).get("outcome") == "AMBIGUOUS" for row in ready),
        "positive": positive,
        "negative": negative,
        "flat_or_ambiguous": flat,
        "average_resolved_r_proxy": round(mean(row["outcome"]["r_multiple"] for row in resolved), 3) if resolved else 0.0,
        "win_rate_pct": round(positive / len(resolved) * 100.0, 1) if resolved else 0.0,
        "non_additive": True,
    }


def _deduplicate_ready_setups(decisions):
    """Mark consecutive same-direction READY snapshots without deleting audit rows."""
    next_trade = 1
    active = {}
    for row in decisions:
        symbol = row["symbol"]
        key = (row.get("status"), row.get("action"))
        previous = active.get(symbol)
        if previous and previous["target_date"] != row.get("target_date"):
            active.pop(symbol, None)
        if row.get("status") != "READY":
            active.pop(symbol, None)
            row["independent_setup"] = False
            row["trade_id"] = None
            continue
        previous = active.get(symbol)
        if previous and previous["key"] == key:
            row["independent_setup"] = False
            row["trade_id"] = previous["trade_id"]
            row["outcome"] = None
            row["outcome_note"] = "Duplicate snapshot of the preceding open setup; not scored again."
            continue
        trade_id = f"W{next_trade:03d}"
        next_trade += 1
        active[symbol] = {"key": key, "trade_id": trade_id, "target_date": row.get("target_date")}
        row["independent_setup"] = True
        row["trade_id"] = trade_id
    return decisions


def _weekly_summary(decisions):
    setups = [row for row in decisions if row.get("independent_setup")]
    completed_outcomes = {"T1_HIT", "SL_HIT"}
    resolved = [row for row in setups if (row.get("outcome") or {}).get("outcome") in completed_outcomes]
    positive = sum(row["outcome"]["r_multiple"] > 0 for row in resolved)
    negative = sum(row["outcome"]["r_multiple"] < 0 for row in resolved)
    return {
        "decision_snapshots": len(decisions),
        "ready_snapshots": sum(row.get("status") == "READY" for row in decisions),
        "wait_snapshots": sum(row.get("status") == "WAIT" for row in decisions),
        "no_trade_snapshots": sum(row.get("status") == "NO_TRADE" for row in decisions),
        "independent_setups": len(setups),
        "duplicate_ready_snapshots": sum(row.get("status") == "READY" and not row.get("independent_setup") for row in decisions),
        "resolved_underlying_proxies": len(resolved),
        "open_underlying_proxies": sum((row.get("outcome") or {}).get("outcome") == "OPEN" for row in setups),
        "ambiguous_underlying_proxies": sum((row.get("outcome") or {}).get("outcome") == "AMBIGUOUS" for row in setups),
        "positive": positive,
        "negative": negative,
        "flat_or_ambiguous": len(resolved) - positive - negative,
        "average_resolved_r_proxy": round(mean(row["outcome"]["r_multiple"] for row in resolved), 3) if resolved else 0.0,
        "win_rate_pct": round(positive / len(resolved) * 100.0, 1) if resolved else 0.0,
        "additive_pnl_available": False,
    }


def _click_timeline(decisions, session_pairs=WEEKLY_SESSION_PAIRS, click_schedule=None):
    schedule = click_schedule or _click_schedule(session_pairs, lambda _: WEEKLY_CLICK_TIMES)
    timeline = []
    for _, target_date in session_pairs:
        for click_text in schedule[target_date.isoformat()]:
            cards = []
            for symbol in SYMBOLS:
                row = next((item for item in decisions if item["target_date"] == target_date.isoformat()
                            and item["click_time_ist"] == click_text and item["symbol"] == symbol), None)
                if row:
                    cards.append({
                        "symbol": symbol,
                        "screen_result": row["status"],
                        "action": row["action"],
                        "strength": row["current_mtf_strength"],
                        "main_reason": row["blockers"][0] if row["blockers"] else "All frozen gates passed.",
                        "trade_id": row.get("trade_id"),
                        "independent_setup": row.get("independent_setup", False),
                        "outcome": row.get("outcome"),
                    })
            timeline.append({
                "target_date": target_date.isoformat(),
                "clicked_at_ist": click_text,
                "display_label": f"Clicked at {click_text} IST",
                "cards": cards,
            })
    return timeline


async def validate_frozen_tuesday_phase_a_data(provider):
    observation_date = date(2026, 8, 24)
    target_date = date(2026, 8, 25)
    fetch_start = datetime.combine(observation_date - timedelta(days=14), time(9, 0), tzinfo=IST)
    fetch_end = datetime.combine(target_date, time(23, 30), tzinfo=IST)
    quality_rows = []
    benchmarks = {"CRUDEOIL": "WTI", "NATURALGAS": "HENRY_HUB"}

    for symbol in SYMBOLS:
        contract = await resolve_nearest_mcx_future(symbol)
        rows_by_timeframe = {
            "5m": await _fetch_chunked(provider, contract, 5, fetch_start, fetch_end),
            "15m": await _fetch_chunked(provider, contract, 15, fetch_start, fetch_end),
            "1h": await _fetch_chunked(provider, contract, 60, fetch_start, fetch_end),
        }
        previous = build_next_session_plan(
            symbol,
            rows_by_timeframe["5m"],
            observation_date,
            target_date,
            contract.get("tick_size"),
        )
        quality_rows.append(_data_quality(
            symbol,
            contract,
            rows_by_timeframe,
            {"benchmark_symbol": benchmarks[symbol], "candles": []},
            previous,
            target_date,
        ))

    valid = len(quality_rows) == len(SYMBOLS) and all(row["status"] == "VALID" for row in quality_rows)
    return {
        "mode": "COMMODITY_CLICK_PHASE_A_DATA_VALIDATION_V1",
        "status": "VALID" if valid else "INVALID_TARGET_SESSION_SLICE",
        "observation_date": observation_date.isoformat(),
        "target_date": target_date.isoformat(),
        "symbols": list(SYMBOLS),
        "data_quality": quality_rows,
        "generates_trade_decisions": False,
        "benchmark_check_performed": False,
        "option_premium_check_performed": False,
        "research_only": True,
        "production_rules_changed": False,
        "paper_trading_permission_changed": False,
        "live_execution_enabled": False,
    }


async def run_frozen_tuesday_phase_a(provider):
    observation_date = date(2026, 8, 24)
    target_date = date(2026, 8, 25)
    fetch_start = datetime.combine(observation_date - timedelta(days=14), time(9, 0), tzinfo=IST)
    fetch_end = datetime.combine(target_date, time(23, 30), tzinfo=IST)
    benchmark_start = datetime.combine(target_date, time(0, 0), tzinfo=IST)
    benchmark_end = datetime.combine(target_date + timedelta(days=1), time(0, 0), tzinfo=IST)
    decisions = []
    data_quality = []

    for symbol in SYMBOLS:
        contract = await resolve_nearest_mcx_future(symbol)
        rows_by_timeframe = {
            "5m": await _fetch_chunked(provider, contract, 5, fetch_start, fetch_end),
            "15m": await _fetch_chunked(provider, contract, 15, fetch_start, fetch_end),
            "1h": await _fetch_chunked(provider, contract, 60, fetch_start, fetch_end),
        }
        benchmark_payload = await fetch_benchmark_candles(symbol, benchmark_start, benchmark_end)
        benchmark_rows = benchmark_payload.get("candles", [])
        previous = build_next_session_plan(symbol, rows_by_timeframe["5m"], observation_date, target_date, contract.get("tick_size"))
        quality = _data_quality(symbol, contract, rows_by_timeframe, benchmark_payload, previous, target_date)
        data_quality.append(quality)
        if quality["status"] != "VALID":
            continue
        normalized_5m = _valid_rows(rows_by_timeframe["5m"])
        target_rows = [row for row in normalized_5m if _ts(row[0]).date() == target_date]
        comparison_rows = [row for row in normalized_5m if _ts(row[0]).date() < target_date]

        for click_text in CLICK_TIMES:
            click = _click(target_date, click_text)
            frames, plan, mtf = _historical_mtf(rows_by_timeframe, click)
            current_rows = [row for row in target_rows if _ts(row[0]) < click]
            benchmark = benchmark_confirmation(symbol, benchmark_rows, click)
            brain = evaluate_commodity_click(
                symbol=symbol,
                click_at=click,
                previous_plan=previous,
                mtf_snapshot=mtf,
                current_rows=current_rows,
                comparison_rows=comparison_rows,
                benchmark=benchmark,
                option_premium=None,
                premium_risk_reward=1.5,
                require_option_premium=False,
            )
            outcome = None
            if brain["status"] == "READY" and plan:
                outcome = _resolve_trade(plan, target_rows, click, 2.0, 2.0)
            decisions.append({
                "symbol": symbol,
                "click_at": click.isoformat(),
                "status": brain["status"],
                "action": brain["action"],
                "underlying_direction": brain["underlying_direction"],
                "previous_direction": previous.get("underlying_direction", "NEUTRAL"),
                "current_mtf_action": mtf.get("action"),
                "current_mtf_strength": mtf.get("alpha_score"),
                "session_input_candles": len(current_rows),
                "benchmark": benchmark,
                "underlying_setup": {
                    "entry": plan.get("entry"), "stop_loss": plan.get("stop"), "target1": plan.get("target1"), "risk_reward": 1.5
                } if plan else None,
                "outcome": outcome,
                "blockers": brain["blockers"],
                "gates": brain["gates"],
                "timeframe_signals": {key: value.get("signal") for key, value in frames.items()},
            })

    valid = all(row["status"] == "VALID" for row in data_quality) and len(data_quality) == len(SYMBOLS)
    return {
        "mode": "COMMODITY_CLICK_PHASE_A_FROZEN_TUESDAY_V1",
        "status": "VALID" if valid else "INVALID_TARGET_SESSION_SLICE",
        "observation_date": observation_date.isoformat(),
        "target_date": target_date.isoformat(),
        "click_times_ist": list(CLICK_TIMES),
        "symbols": list(SYMBOLS),
        "summary": _summary(decisions),
        "data_quality": data_quality,
        "decisions": decisions,
        "research_only": True,
        "outcome_basis": "UNDERLYING_DIRECTION_PROXY_NOT_OPTION_PREMIUM_PNL",
        "option_premium_backtest": False,
        "independent_overlapping_snapshots": True,
        "production_rules_changed": False,
        "paper_trading_permission_changed": False,
        "live_execution_enabled": False,
    }


async def _run_frozen_click_backtest(provider, session_pairs, mode, click_schedule):
    """Replay preregistered next sessions at frozen user-click samples."""
    first_observation = session_pairs[0][0]
    last_target = session_pairs[-1][1]
    fetch_start = datetime.combine(first_observation - timedelta(days=14), time(9, 0), tzinfo=IST)
    fetch_end = datetime.combine(last_target, time(23, 30), tzinfo=IST)
    decisions = []
    data_quality = []

    for symbol in SYMBOLS:
        contract = await resolve_nearest_mcx_future(symbol)
        rows_by_timeframe = {
            "5m": await _fetch_chunked(provider, contract, 5, fetch_start, fetch_end),
            "15m": await _fetch_chunked(provider, contract, 15, fetch_start, fetch_end),
            "1h": await _fetch_chunked(provider, contract, 60, fetch_start, fetch_end),
        }
        normalized_5m = _valid_rows(rows_by_timeframe["5m"])

        for observation_date, target_date in session_pairs:
            benchmark_start = datetime.combine(target_date, time(0, 0), tzinfo=IST)
            benchmark_end = datetime.combine(target_date + timedelta(days=1), time(0, 0), tzinfo=IST)
            benchmark_payload = await fetch_benchmark_candles(symbol, benchmark_start, benchmark_end)
            benchmark_rows = benchmark_payload.get("candles", [])
            previous = build_next_session_plan(
                symbol, normalized_5m, observation_date, target_date, contract.get("tick_size"),
            )
            quality = _data_quality(
                symbol, contract, rows_by_timeframe, benchmark_payload, previous, target_date,
            )
            quality["observation_date"] = observation_date.isoformat()
            quality["target_date"] = target_date.isoformat()
            data_quality.append(quality)
            if quality["status"] != "VALID":
                continue

            target_rows = [row for row in normalized_5m if _ts(row[0]).date() == target_date]
            comparison_rows = [row for row in normalized_5m if _ts(row[0]).date() < target_date]
            for click_text in click_schedule[target_date.isoformat()]:
                click = _click(target_date, click_text)
                frames, plan, mtf = _historical_mtf(rows_by_timeframe, click)
                current_rows = [row for row in target_rows if _ts(row[0]) < click]
                benchmark = benchmark_confirmation(symbol, benchmark_rows, click)
                brain = evaluate_commodity_click(
                    symbol=symbol,
                    click_at=click,
                    previous_plan=previous,
                    mtf_snapshot=mtf,
                    current_rows=current_rows,
                    comparison_rows=comparison_rows,
                    benchmark=benchmark,
                    option_premium=None,
                    premium_risk_reward=1.5,
                    require_option_premium=False,
                )
                outcome = _resolve_trade(plan, target_rows, click, 2.0, 2.0) if brain["status"] == "READY" and plan else None
                decisions.append({
                    "observation_date": observation_date.isoformat(),
                    "target_date": target_date.isoformat(),
                    "click_time_ist": click_text,
                    "click_at": click.isoformat(),
                    "symbol": symbol,
                    "status": brain["status"],
                    "action": brain["action"],
                    "underlying_direction": brain["underlying_direction"],
                    "previous_direction": previous.get("underlying_direction", "NEUTRAL"),
                    "current_mtf_action": mtf.get("action"),
                    "current_mtf_strength": mtf.get("alpha_score"),
                    "session_input_candles": len(current_rows),
                    "benchmark": benchmark,
                    "underlying_setup": {
                        "entry": plan.get("entry"), "stop_loss": plan.get("stop"),
                        "target1": plan.get("target1"), "risk_reward": 1.5,
                    } if plan else None,
                    "outcome": outcome,
                    "blockers": brain["blockers"],
                    "gates": brain["gates"],
                    "timeframe_signals": {key: value.get("signal") for key, value in frames.items()},
                    "market_brain_audit": market_brain_audit(previous, frames, click, brain["gates"]),
                })

    expected_snapshots = sum(len(times) for times in click_schedule.values()) * len(SYMBOLS)
    valid = len(data_quality) == len(SYMBOLS) * len(session_pairs) and all(
        row["status"] == "VALID" for row in data_quality
    )
    if valid:
        _deduplicate_ready_setups(decisions)
    return {
        "mode": mode,
        "status": "VALID" if valid else "INVALID_TARGET_SESSION_SLICE",
        "observation_target_pairs": [
            {"observation_date": observation.isoformat(), "target_date": target.isoformat()}
            for observation, target in session_pairs
        ],
        "click_sampling": "TEN_FROZEN_BACKTEST_SAMPLES_PER_SESSION_NOT_A_LIVE_CLICK_LIMIT",
        "click_window_ist": {"start": "10:00", "end_inclusive": "22:00"},
        "click_schedule": [
            {"target_date": target.isoformat(), "click_times_ist": list(click_schedule[target.isoformat()])}
            for _, target in session_pairs
        ],
        "symbols": list(SYMBOLS),
        "expected_decision_snapshots": expected_snapshots,
        "summary": _weekly_summary(decisions),
        "data_quality": data_quality,
        "click_timeline": _click_timeline(decisions, session_pairs, click_schedule),
        "decisions": decisions,
        "deduplication_rule": "Consecutive same-symbol READY snapshots with the same action are one trade.",
        "research_only": True,
        "outcome_basis": "UNDERLYING_DIRECTION_PROXY_NOT_OPTION_PREMIUM_PNL",
        "historical_news_reconstructed": False,
        "strategy_rules_changed": False,
        "live_execution_enabled": False,
    }


async def run_frozen_weekly_click_backtest(provider):
    """Replay the original five-session preregistered protocol."""
    schedule = _click_schedule(WEEKLY_SESSION_PAIRS, lambda _: WEEKLY_CLICK_TIMES)
    return await _run_frozen_click_backtest(
        provider, WEEKLY_SESSION_PAIRS, "COMMODITY_FROZEN_WEEKLY_CLICK_BACKTEST_V1", schedule,
    )


async def run_frozen_extended_click_backtest(provider):
    """Replay the fixed 20-target-session extension without optional stopping."""
    schedule = _click_schedule(EXTENDED_SESSION_PAIRS, _extended_click_times)
    return await _run_frozen_click_backtest(
        provider, EXTENDED_SESSION_PAIRS, "COMMODITY_FROZEN_20_SESSION_CLICK_BACKTEST_V1", schedule,
    )


async def run_frozen_july_validation_backtest(provider):
    """Replay the independently frozen July sample without changing the baseline brain."""
    schedule = _click_schedule(VALIDATION_SESSION_PAIRS, _validation_click_times)
    return await _run_frozen_click_backtest(
        provider,
        VALIDATION_SESSION_PAIRS,
        "COMMODITY_FROZEN_JULY_VALIDATION_BACKTEST_V1",
        schedule,
    )


async def audit_identified_setups(provider):
    """Recompute only the three frozen setup inputs; never score their outcomes."""
    records = []
    quality_records = []
    for symbol in SYMBOLS:
        points = [point for point in IDENTIFIED_SETUP_AUDIT_POINTS if point["symbol"] == symbol]
        if not points:
            continue
        contract = await resolve_nearest_mcx_future(symbol)
        fetch_start = datetime.combine(min(point["observation_date"] for point in points) - timedelta(days=14), time(9, 0), tzinfo=IST)
        fetch_end = datetime.combine(max(point["target_date"] for point in points), time(23, 30), tzinfo=IST)
        rows_by_timeframe = {
            "5m": await _fetch_chunked(provider, contract, 5, fetch_start, fetch_end),
            "15m": await _fetch_chunked(provider, contract, 15, fetch_start, fetch_end),
            "1h": await _fetch_chunked(provider, contract, 60, fetch_start, fetch_end),
        }
        normalized_5m = _valid_rows(rows_by_timeframe["5m"])
        for point in points:
            observation_date = point["observation_date"]
            target_date = point["target_date"]
            click = _click(target_date, point["click_time_ist"])
            benchmark_payload = await fetch_benchmark_candles(
                symbol,
                datetime.combine(target_date, time(0, 0), tzinfo=IST),
                datetime.combine(target_date + timedelta(days=1), time(0, 0), tzinfo=IST),
            )
            previous = build_next_session_plan(
                symbol, normalized_5m, observation_date, target_date, contract.get("tick_size"),
            )
            quality = _data_quality(symbol, contract, rows_by_timeframe, benchmark_payload, previous, target_date)
            quality.update({
                "observation_date": observation_date.isoformat(),
                "target_date": target_date.isoformat(),
                "click_time_ist": point["click_time_ist"],
            })
            quality_records.append(quality)
            if quality["status"] != "VALID":
                continue
            frames, _, mtf = _historical_mtf(rows_by_timeframe, click)
            target_rows = [row for row in normalized_5m if _ts(row[0]).date() == target_date]
            current_rows = [row for row in target_rows if _ts(row[0]) < click]
            comparison_rows = [row for row in normalized_5m if _ts(row[0]).date() < target_date]
            benchmark = benchmark_confirmation(symbol, benchmark_payload.get("candles", []), click)
            brain = evaluate_commodity_click(
                symbol=symbol,
                click_at=click,
                previous_plan=previous,
                mtf_snapshot=mtf,
                current_rows=current_rows,
                comparison_rows=comparison_rows,
                benchmark=benchmark,
                option_premium=None,
                premium_risk_reward=1.5,
                require_option_premium=False,
            )
            records.append({
                "symbol": symbol,
                "observation_date": observation_date.isoformat(),
                "target_date": target_date.isoformat(),
                "click_time_ist": point["click_time_ist"],
                "status": brain["status"],
                "action": brain["action"],
                "gates": brain["gates"],
                "blockers": brain["blockers"],
                "benchmark": benchmark,
                "market_brain_audit": market_brain_audit(previous, frames, click, brain["gates"]),
            })
    valid = len(quality_records) == len(IDENTIFIED_SETUP_AUDIT_POINTS) and all(
        quality["status"] == "VALID" for quality in quality_records
    )
    return {
        "mode": "MARKET_BRAIN_IDENTIFIED_SETUP_AUDIT_V1",
        "status": "VALID" if valid else "INVALID_TARGET_SESSION_SLICE",
        "frozen_points": [
            {key: value.isoformat() if isinstance(value, date) else value for key, value in point.items()}
            for point in IDENTIFIED_SETUP_AUDIT_POINTS
        ],
        "quality": quality_records,
        "records": records,
        "random_schedule_regenerated": False,
        "full_backtest_rerun": False,
        "outcomes_scored": False,
        "performance_statistics_generated": False,
        "strategy_rules_changed": False,
        "research_only": True,
    }
