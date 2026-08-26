from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_backtest import _fetch_chunked, _plan_at, _resolve_trade, _ts
from .commodity_benchmarks import benchmark_confirmation, fetch_benchmark_candles
from .commodity_click_brain import evaluate_commodity_click
from .commodity_next_session import build_next_session_plan
from .commodities import analyze_commodity_candles, resolve_nearest_mcx_future


IST = ZoneInfo("Asia/Kolkata")
SYMBOLS = ("CRUDEOIL", "NATURALGAS")
CLICK_TIMES = ("09:35", "10:55", "11:05", "13:20", "13:35", "15:15", "15:25", "16:15", "16:40", "18:35")


def _click(day, text):
    hour, minute = [int(value) for value in text.split(":")]
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


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


def _summary(decisions):
    ready = [row for row in decisions if row.get("status") == "READY"]
    resolved = [row for row in ready if isinstance((row.get("outcome") or {}).get("r_multiple"), (int, float))]
    positive = sum(1 for row in resolved if row["outcome"]["r_multiple"] > 0)
    negative = sum(1 for row in resolved if row["outcome"]["r_multiple"] < 0)
    flat = sum(1 for row in resolved if row["outcome"]["r_multiple"] == 0)
    return {
        "independent_click_decisions": len(decisions),
        "ready_setups": len(ready),
        "wait": sum(1 for row in decisions if row.get("status") == "WAIT"),
        "no_trade": sum(1 for row in decisions if row.get("status") == "NO_TRADE"),
        "resolved_underlying_proxies": len(resolved),
        "positive": positive,
        "negative": negative,
        "flat_or_ambiguous": flat,
        "average_resolved_r_proxy": round(mean(row["outcome"]["r_multiple"] for row in resolved), 3) if resolved else 0.0,
        "win_rate_pct": round(positive / len(resolved) * 100.0, 1) if resolved else 0.0,
        "non_additive": True,
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
        target_rows = [row for row in rows_by_timeframe["5m"] if _ts(row[0]).date() == target_date]
        comparison_rows = [row for row in rows_by_timeframe["5m"] if _ts(row[0]).date() < target_date]
        data_quality.append({
            "symbol": symbol,
            "contract": contract.get("trading_symbol"),
            "candles": {key: len(value) for key, value in rows_by_timeframe.items()},
            "benchmark": benchmark_payload.get("benchmark_symbol"),
            "benchmark_candles": len(benchmark_rows),
            "previous_status": previous.get("status"),
            "previous_direction": previous.get("underlying_direction"),
        })

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
                "benchmark": benchmark,
                "underlying_setup": {
                    "entry": plan.get("entry"), "stop_loss": plan.get("stop"), "target1": plan.get("target1"), "risk_reward": 1.5
                } if plan else None,
                "outcome": outcome,
                "blockers": brain["blockers"],
                "gates": brain["gates"],
                "timeframe_signals": {key: value.get("signal") for key, value in frames.items()},
            })

    return {
        "mode": "COMMODITY_CLICK_PHASE_A_FROZEN_TUESDAY_V1",
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
