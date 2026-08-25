from __future__ import annotations

import asyncio
import math
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta

from .backtest import _historical
from .market_brain_context_research import SYMBOLS
from .market_brain_setup_expectancy import _minute_key
from .market_brain_v7_regime_quality import (
    FEATURE_NAMES,
    _build_continuous_context,
    _feature_vector,
)
from .option_native_research import _cost_adjusted_r, run_option_native_research


PROTOCOL_REVISION = "STRATEGY_REGIME_ROUTING_V1_FROZEN_2026_08_25"
STRATEGIES = ("VWAP_TREND", "ORB_30", "BREAKOUT_20", "PRICE_ACTION_BREAKOUT")
BOOK_ELIGIBLE_GRADES = {"CONFIRMED", "ACCEPTABLE"}
CONTEXT_LAG_MINUTES = 15
CONTEXT_MAX_AGE_MINUTES = 35

MIN_DEVELOPMENT_TRADES = 30
MIN_DEVELOPMENT_ROUTE_TRADES = 8
MIN_DEVELOPMENT_ROUTE_SYMBOLS = 2
MIN_DEVELOPMENT_ROUTE_DATES = 4
MIN_DEVELOPMENT_ROUTE_AVG_R = 0.10
MIN_DEVELOPMENT_ROUTE_PROFIT_FACTOR = 1.20
MAX_DEVELOPMENT_ROUTE_DRAWDOWN_R = 4.0

MIN_HOLDOUT_TRADES = 20
MIN_HOLDOUT_ROUTE_TRADES = 5
MIN_HOLDOUT_SYMBOLS = 3
MIN_HOLDOUT_DATES = 6
MIN_HOLDOUT_MONTHS = 2
MIN_HOLDOUT_PROFIT_FACTOR = 1.20
MAX_HOLDOUT_DRAWDOWN_R = 6.0


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _date_range(start_date: str, end_date: str, label: str):
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except Exception as exc:
        raise ValueError(f"{label} dates must use YYYY-MM-DD") from exc
    if end < start:
        raise ValueError(f"{label} end must be on or after start")
    if (end - start).days > 31:
        raise ValueError(f"{label} range is limited to 31 days")
    return start, end


def _trade_timestamp(trade: dict) -> str:
    return str(trade.get("signal_at") or trade.get("entry_at") or "")


def _trade_id(trade: dict) -> str:
    return "|".join((
        str(trade.get("strategy") or ""),
        str(trade.get("symbol") or ""),
        _trade_timestamp(trade),
        str(trade.get("option_contract") or ""),
    ))


def _market_regime(features: dict) -> tuple[str, float]:
    directional = [
        _number(features.get(name))
        for name in FEATURE_NAMES
        if name != "volatility_expansion"
    ]
    alignment = sum(directional) / len(directional) if directional else 0.0
    volatility = _number(features.get("volatility_expansion"), 1.0)
    if alignment >= 0.35 and volatility >= 1.10:
        regime = "ALIGNED_EXPANSION"
    elif alignment >= 0.35:
        regime = "ALIGNED_NORMAL"
    elif alignment <= -0.35:
        regime = "CONFLICTED"
    else:
        regime = "MIXED"
    return regime, alignment


def _profit_factor_gate(metrics: dict, minimum: float) -> bool:
    if metrics.get("profit_factor_unbounded"):
        return metrics.get("gross_profit_r", 0.0) > 0
    value = metrics.get("profit_factor")
    return isinstance(value, (int, float)) and value >= minimum


def _metrics(trades: list[dict]) -> dict:
    ordered = sorted(trades, key=lambda row: row.get("timestamp", ""))
    values = [_number(row.get("cost_adjusted_r")) for row in ordered]
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    equity = peak = max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    symbols = sorted({str(row.get("symbol")) for row in ordered if row.get("symbol")})
    dates = sorted({str(row.get("timestamp", ""))[:10] for row in ordered if row.get("timestamp")})
    monthly = defaultdict(list)
    for row in ordered:
        monthly[str(row.get("timestamp", ""))[:7]].append(_number(row.get("cost_adjusted_r")))
    by_month = [
        {
            "month": month,
            "trades": len(monthly[month]),
            "total_r": round(sum(monthly[month]), 3),
            "average_r": round(sum(monthly[month]) / len(monthly[month]), 3),
        }
        for month in sorted(key for key in monthly if key)
    ]
    total = sum(values)
    wins = sum(value > 0 for value in values)
    return {
        "trades": len(values),
        "wins": wins,
        "losses": sum(value < 0 for value in values),
        "win_rate": round(wins / len(values) * 100.0, 1) if values else 0.0,
        "total_r": round(total, 3),
        "average_r": round(total / len(values), 3) if values else 0.0,
        "gross_profit_r": round(gross_profit, 3),
        "gross_loss_r": round(gross_loss, 3),
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "profit_factor_unbounded": bool(values and gross_loss == 0 and gross_profit > 0),
        "max_drawdown_r": round(max_drawdown, 3),
        "unique_symbols": len(symbols),
        "symbols": symbols,
        "unique_dates": len(dates),
        "unique_months": len(by_month),
        "by_month": by_month,
    }


def _prepare_trades(trades: list[dict], cost_bps: float):
    prepared = []
    rejected = defaultdict(int)
    for raw in trades:
        strategy = str(raw.get("strategy") or "").upper()
        if strategy not in STRATEGIES:
            rejected["UNKNOWN_STRATEGY"] += 1
            continue
        timestamp = _trade_timestamp(raw)
        key = _minute_key(timestamp)
        if not key:
            rejected["INVALID_TIMESTAMP"] += 1
            continue
        adjusted_r = _cost_adjusted_r(raw, cost_bps)
        if adjusted_r is None or not math.isfinite(float(adjusted_r)):
            rejected["UNRESOLVED_OPTION_R"] += 1
            continue
        features = raw.get("market_brain_features") or {}
        if any(not math.isfinite(_number(features.get(name), float("nan"))) for name in FEATURE_NAMES):
            rejected["MISSING_MARKET_BRAIN_FEATURES"] += 1
            continue
        price_action = ((raw.get("research_features") or {}).get("book_price_action") or {})
        book_grade = str(price_action.get("price_action_grade") or "WEAK").upper()
        regime, alignment = _market_regime(features)
        prepared.append({
            **raw,
            "trade_id": _trade_id(raw),
            "timestamp": key,
            "strategy": strategy,
            "symbol": str(raw.get("symbol") or "").upper(),
            "cost_adjusted_r": round(float(adjusted_r), 6),
            "market_regime": regime,
            "market_alignment_score": round(alignment, 6),
            "book_price_action_grade": book_grade,
            "book_price_action_eligible": book_grade in BOOK_ELIGIBLE_GRADES,
            "route_id": f"{strategy}|{regime}",
        })
    return prepared, dict(sorted(rejected.items()))


def evaluate_strategy_regime_router(
    development_trades: list[dict],
    holdout_trades: list[dict],
    round_trip_cost_bps: float = 10.0,
) -> dict:
    """Select routes on development once, then score an untouched holdout once."""
    cost_bps = max(0.0, min(float(round_trip_cost_bps), 100.0))
    development, development_rejected = _prepare_trades(development_trades, cost_bps)
    holdout, holdout_rejected = _prepare_trades(holdout_trades, cost_bps)

    development_ids = {row["trade_id"] for row in development}
    overlap = development_ids.intersection(row["trade_id"] for row in holdout)
    if overlap:
        raise ValueError("development and holdout contain overlapping trade identities")
    if development and holdout:
        last_development = max(row["timestamp"] for row in development)
        first_holdout = min(row["timestamp"] for row in holdout)
        if last_development >= first_holdout:
            raise ValueError("holdout must begin strictly after all development trades")

    development_eligible = [row for row in development if row["book_price_action_eligible"]]
    holdout_eligible = [row for row in holdout if row["book_price_action_eligible"]]
    grouped = defaultdict(list)
    for row in development_eligible:
        grouped[row["route_id"]].append(row)

    route_candidates = []
    selected_route_ids = []
    for route_id in sorted(grouped):
        rows = grouped[route_id]
        metrics = _metrics(rows)
        gates = {
            "trades_at_least_8": metrics["trades"] >= MIN_DEVELOPMENT_ROUTE_TRADES,
            "symbols_at_least_2": metrics["unique_symbols"] >= MIN_DEVELOPMENT_ROUTE_SYMBOLS,
            "dates_at_least_4": metrics["unique_dates"] >= MIN_DEVELOPMENT_ROUTE_DATES,
            "average_r_at_least_0_10": metrics["average_r"] >= MIN_DEVELOPMENT_ROUTE_AVG_R,
            "profit_factor_at_least_1_20": _profit_factor_gate(metrics, MIN_DEVELOPMENT_ROUTE_PROFIT_FACTOR),
            "drawdown_at_most_4r": metrics["max_drawdown_r"] <= MAX_DEVELOPMENT_ROUTE_DRAWDOWN_R,
        }
        selected = all(gates.values())
        if selected:
            selected_route_ids.append(route_id)
        strategy, regime = route_id.split("|", 1)
        route_candidates.append({
            "route_id": route_id,
            "strategy": strategy,
            "market_regime": regime,
            "selected_on_development": selected,
            "development_metrics": metrics,
            "selection_gates": gates,
        })

    routed_holdout = [row for row in holdout_eligible if row["route_id"] in selected_route_ids]
    holdout_metrics = _metrics(routed_holdout)
    holdout_by_route = []
    for route_id in selected_route_ids:
        rows = [row for row in routed_holdout if row["route_id"] == route_id]
        holdout_by_route.append({"route_id": route_id, "metrics": _metrics(rows)})
    monthly_non_negative = (
        holdout_metrics["unique_months"] >= MIN_HOLDOUT_MONTHS
        and all(row["total_r"] >= 0 for row in holdout_metrics["by_month"])
    )
    gates = {
        "development_book_eligible_trades_at_least_30": len(development_eligible) >= MIN_DEVELOPMENT_TRADES,
        "selected_route_exists": bool(selected_route_ids),
        "routed_holdout_trades_at_least_20": holdout_metrics["trades"] >= MIN_HOLDOUT_TRADES,
        "holdout_average_r_positive": holdout_metrics["average_r"] > 0,
        "holdout_profit_factor_at_least_1_20": _profit_factor_gate(holdout_metrics, MIN_HOLDOUT_PROFIT_FACTOR),
        "holdout_drawdown_at_most_6r": holdout_metrics["max_drawdown_r"] <= MAX_HOLDOUT_DRAWDOWN_R,
        "holdout_symbols_at_least_3": holdout_metrics["unique_symbols"] >= MIN_HOLDOUT_SYMBOLS,
        "holdout_dates_at_least_6": holdout_metrics["unique_dates"] >= MIN_HOLDOUT_DATES,
        "holdout_months_at_least_2": holdout_metrics["unique_months"] >= MIN_HOLDOUT_MONTHS,
        "each_holdout_month_non_negative": monthly_non_negative,
        "each_selected_route_has_5_holdout_trades": bool(selected_route_ids) and all(
            row["metrics"]["trades"] >= MIN_HOLDOUT_ROUTE_TRADES for row in holdout_by_route
        ),
    }
    failed_gates = [name for name, passed in gates.items() if not passed]
    decision = (
        "VALIDATED_STRATEGY_REGIME_ROUTER"
        if gates and all(gates.values())
        else "NO_VALIDATED_STRATEGY_REGIME_ROUTER"
    )

    return {
        "mode": "ALPHAPILOT_STRATEGY_REGIME_ROUTING_V1",
        "protocol_revision": PROTOCOL_REVISION,
        "research_only": True,
        "production_rules_changed": False,
        "market_brain_permission_changed": False,
        "paper_trading_permission_changed": False,
        "live_execution_enabled": False,
        "decision": decision,
        "failed_gates": failed_gates,
        "cost_model": {"round_trip_cost_bps": cost_bps},
        "book_knowledge": {
            "role": "SETUP_TIME_EVIDENCE_GATE",
            "eligible_grades": sorted(BOOK_ELIGIBLE_GRADES),
            "weak_grade_action": "EXCLUDED_BEFORE_ROUTE_SELECTION_AND_HOLDOUT_SCORING",
            "concepts": [
                "market_structure",
                "support_resistance",
                "candlestick_context",
                "breakout_close_quality",
                "volume_confirmation",
                "compression",
                "false_breakout_risk",
            ],
            "threshold_ownership": "ALPHAPILOT_RESEARCH_HYPOTHESIS",
        },
        "route_definition": {
            "key": "strategy|direction_aligned_market_regime",
            "market_regimes": ["ALIGNED_EXPANSION", "ALIGNED_NORMAL", "MIXED", "CONFLICTED"],
            "alignment_thresholds": {"aligned_at_or_above": 0.35, "conflicted_at_or_below": -0.35},
            "expansion_threshold": 1.10,
        },
        "development": {
            "all_matched_metrics": _metrics(development),
            "book_eligible_metrics": _metrics(development_eligible),
            "rejected_counts": development_rejected,
            "route_candidates": route_candidates,
            "selected_route_ids": selected_route_ids,
        },
        "holdout": {
            "all_matched_metrics": _metrics(holdout),
            "book_eligible_metrics": _metrics(holdout_eligible),
            "routed_metrics": holdout_metrics,
            "rejected_counts": holdout_rejected,
            "by_selected_route": holdout_by_route,
            "routed_trades": routed_holdout,
        },
        "acceptance_gates": gates,
        "fixed_acceptance_rules": {
            "min_development_trades": MIN_DEVELOPMENT_TRADES,
            "min_development_route_trades": MIN_DEVELOPMENT_ROUTE_TRADES,
            "min_development_route_average_r": MIN_DEVELOPMENT_ROUTE_AVG_R,
            "min_development_route_profit_factor": MIN_DEVELOPMENT_ROUTE_PROFIT_FACTOR,
            "max_development_route_drawdown_r": MAX_DEVELOPMENT_ROUTE_DRAWDOWN_R,
            "min_holdout_trades": MIN_HOLDOUT_TRADES,
            "min_holdout_route_trades": MIN_HOLDOUT_ROUTE_TRADES,
            "min_holdout_profit_factor": MIN_HOLDOUT_PROFIT_FACTOR,
            "max_holdout_drawdown_r": MAX_HOLDOUT_DRAWDOWN_R,
            "min_holdout_symbols": MIN_HOLDOUT_SYMBOLS,
            "min_holdout_dates": MIN_HOLDOUT_DATES,
            "min_holdout_months": MIN_HOLDOUT_MONTHS,
        },
        "limitations": [
            "Routes are selected once on development data and applied unchanged to holdout.",
            "The book supplies concepts, while AlphaPilot owns and must validate every numeric threshold.",
            "Historical option OHLC plus a configurable cost stress is not a broker contract-note reconstruction.",
            "A validated result remains research-only and cannot authorize paper or live trading.",
        ],
    }


async def _context_for_split(provider, start_date: str, end_date: str):
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(hours=23, minutes=59)
    context_start = start - timedelta(days=5)
    all_rows = {}
    errors = []

    async def fetch_one(symbol):
        try:
            return symbol, await _historical(provider, symbol, "15m", context_start, end), None
        except Exception as exc:
            return symbol, [], f"{exc.__class__.__name__}: {exc}"

    for offset in range(0, len(SYMBOLS), 4):
        batch = await asyncio.gather(*(fetch_one(symbol) for symbol in SYMBOLS[offset:offset + 4]))
        for symbol, rows, error in batch:
            all_rows[symbol] = rows
            if error:
                errors.append({"symbol": symbol, "error": error})
        await asyncio.sleep(0.15)
    contexts = [
        row for row in _build_continuous_context(all_rows)
        if start_date <= str(row.get("ts", ""))[:10] <= end_date
    ]
    return contexts, errors


def _attach_context(trades: list[dict], contexts: list[dict]):
    context_by_key = {
        key: row for row in contexts if (key := _minute_key(row.get("ts")))
    }
    keys = sorted(context_by_key)
    matched, unmatched = [], []
    for trade in trades:
        signal_key = _minute_key(trade.get("signal_at"))
        if not signal_key:
            unmatched.append({"trade_id": _trade_id(trade), "reason": "INVALID_SIGNAL_TIME"})
            continue
        signal_time = datetime.strptime(signal_key, "%Y-%m-%d %H:%M")
        cutoff = signal_time - timedelta(minutes=CONTEXT_LAG_MINUTES)
        index = bisect_right(keys, cutoff.strftime("%Y-%m-%d %H:%M")) - 1
        if index < 0:
            unmatched.append({"trade_id": _trade_id(trade), "reason": "NO_CLOSED_CONTEXT"})
            continue
        context_key = keys[index]
        context_time = datetime.strptime(context_key, "%Y-%m-%d %H:%M")
        age_minutes = (signal_time - context_time).total_seconds() / 60.0
        if context_time.date() != signal_time.date() or age_minutes > CONTEXT_MAX_AGE_MINUTES:
            unmatched.append({"trade_id": _trade_id(trade), "reason": "STALE_CONTEXT"})
            continue
        row = dict(trade)
        row["market_brain_context_at"] = context_key
        row["market_brain_context_age_minutes"] = round(age_minutes, 1)
        row["market_brain_features"] = _feature_vector(context_by_key[context_key], trade.get("direction"))
        matched.append(row)
    return matched, {
        "input_trades": len(trades),
        "matched_trades": len(matched),
        "match_rate_pct": round(len(matched) / len(trades) * 100.0, 1) if trades else 0.0,
        "context_lag_minutes": CONTEXT_LAG_MINUTES,
        "max_context_age_minutes": CONTEXT_MAX_AGE_MINUTES,
        "unmatched_count": len(unmatched),
        "unmatched_samples": unmatched[:10],
    }


def _flatten_option_research(payload: dict) -> list[dict]:
    trades = []
    for result in payload.get("leaderboard") or []:
        trades.extend(result.get("trades") or [])
    return trades


async def _run_split(
    provider,
    symbols: list[str],
    start_date: str,
    end_date: str,
    research_target_r: float,
    premium_min_rr: float,
    max_trades_per_strategy: int,
    round_trip_cost_bps: float,
):
    option_research = await run_option_native_research(
        provider,
        symbols,
        start_date,
        end_date,
        research_target_r,
        premium_min_rr,
        max_trades_per_strategy,
        round_trip_cost_bps,
    )
    contexts, context_errors = await _context_for_split(provider, start_date, end_date)
    trades = _flatten_option_research(option_research)
    matched, match_diagnostics = _attach_context(trades, contexts)
    return matched, {
        "period": {"start_date": start_date, "end_date": end_date},
        "option_trade_count": len(trades),
        "market_context_observations": len(contexts),
        "context_match": match_diagnostics,
        "option_errors": option_research.get("errors") or [],
        "context_errors": context_errors,
    }


async def run_strategy_regime_routing(
    provider,
    symbols: list[str],
    development_start: str,
    development_end: str,
    holdout_start: str,
    holdout_end: str,
    research_target_r: float = 1.0,
    premium_min_rr: float = 1.5,
    max_trades_per_strategy: int = 50,
    round_trip_cost_bps: float = 10.0,
):
    _, development_end_dt = _date_range(development_start, development_end, "development")
    holdout_start_dt, _ = _date_range(holdout_start, holdout_end, "holdout")
    if development_end_dt >= holdout_start_dt:
        raise ValueError("holdout must start after the development period ends")
    symbols = [str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()]
    if not symbols:
        raise ValueError("at least one symbol is required")

    development, development_source = await _run_split(
        provider, symbols, development_start, development_end, research_target_r,
        premium_min_rr, max_trades_per_strategy, round_trip_cost_bps,
    )
    holdout, holdout_source = await _run_split(
        provider, symbols, holdout_start, holdout_end, research_target_r,
        premium_min_rr, max_trades_per_strategy, round_trip_cost_bps,
    )
    result = evaluate_strategy_regime_router(development, holdout, round_trip_cost_bps)
    result["request"] = {
        "symbols": symbols,
        "development_start": development_start,
        "development_end": development_end,
        "holdout_start": holdout_start,
        "holdout_end": holdout_end,
        "research_target_r": research_target_r,
        "premium_min_risk_reward": premium_min_rr,
        "max_trades_per_strategy": max(1, min(int(max_trades_per_strategy), 50)),
    }
    result["source_diagnostics"] = {
        "development": development_source,
        "holdout": holdout_source,
    }
    return result
