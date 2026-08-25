from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import math

from .backtest import IST, _historical, _ts
from .fno_historical_backtest import (
    AUTO_EXPIRY_MAX_DTE_DAYS,
    _available_contracts,
    _instrument_rows,
    _select_expiry,
)
from .fno_premium_replay import replay_option_trade
from .option_native_research import _cost_adjusted_r
from .price_action_knowledge import BOOK_KNOWLEDGE_REVISION, price_action_snapshot
from .setup_discovery_v2 import _pullback_continuation
from .strategy_regime_routing import (
    CONTEXT_LAG_MINUTES,
    _attach_context,
    _context_for_split,
    _market_regime,
)
from .strategy_research import _atr, _day_indices, _ema, _simulate_underlying


PROTOCOL_REVISION = "PULLBACK_CONTINUATION_SHORT_OPTION_H1_FROZEN_2026_08_25"
HOLDOUT_START = "2026-08-11"
HOLDOUT_END = "2026-08-21"
SYMBOLS = (
    "RELIANCE",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "TCS",
    "INFY",
    "TATASTEEL",
    "MARUTI",
)
ROUND_TRIP_COST_BPS = 10.0
MAX_SIGNALS = 80
TARGET_R = 1.0

MIN_SIGNALS = 20
MIN_RESOLVED_TRADES = 20
MIN_REPLAY_COVERAGE_PCT = 70.0
MIN_SYMBOLS = 3
MIN_DATES = 6
MIN_PROFIT_FACTOR = 1.20
MAX_DRAWDOWN_R = 6.0


def _metric_rows(trades: list[dict], round_trip_cost_bps: float) -> tuple[list[dict], dict]:
    prepared = []
    for trade in trades:
        adjusted = _cost_adjusted_r(trade, round_trip_cost_bps)
        if adjusted is None or not math.isfinite(float(adjusted)):
            continue
        prepared.append({
            **trade,
            "timestamp": str(trade.get("signal_at") or trade.get("entry_at") or ""),
            "cost_adjusted_r": round(float(adjusted), 6),
        })
    prepared.sort(key=lambda row: (row["timestamp"], str(row.get("symbol") or "")))

    values = [float(row["cost_adjusted_r"]) for row in prepared]
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    equity = peak = max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    symbols = sorted({str(row.get("symbol")) for row in prepared if row.get("symbol")})
    dates = sorted({row["timestamp"][:10] for row in prepared if row["timestamp"]})
    monthly: dict[str, list[float]] = defaultdict(list)
    for row in prepared:
        monthly[row["timestamp"][:7]].append(float(row["cost_adjusted_r"]))
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
    metrics = {
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
        "dates": dates,
        "unique_months": len(by_month),
        "by_month": by_month,
    }
    return prepared, metrics


def _profit_factor_pass(metrics: dict) -> bool:
    if metrics.get("profit_factor_unbounded"):
        return float(metrics.get("gross_profit_r") or 0.0) > 0
    value = metrics.get("profit_factor")
    return isinstance(value, (int, float)) and float(value) >= MIN_PROFIT_FACTOR


def _diagnostic_groups(prepared: list[dict], field) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for trade in prepared:
        grouped[str(field(trade) or "UNKNOWN")].append(trade)
    rows = []
    for label, sample in grouped.items():
        _, metrics = _metric_rows(sample, ROUND_TRIP_COST_BPS)
        rows.append({"label": label, "metrics": metrics})
    rows.sort(key=lambda row: (-row["metrics"]["trades"], row["label"]))
    return rows


def evaluate_pullback_short_option_h1(
    trades: list[dict],
    candidate_signal_count: int,
    attempted_signal_count: int,
    round_trip_cost_bps: float = ROUND_TRIP_COST_BPS,
    book_diagnostics: list[dict] | None = None,
    market_brain_diagnostics: dict | None = None,
) -> dict:
    """Score only frozen option outcomes; book/Market Brain fields are descriptive."""
    prepared, metrics = _metric_rows(trades, round_trip_cost_bps)
    replay_coverage = (
        len(prepared) / attempted_signal_count * 100.0 if attempted_signal_count else 0.0
    )
    data_quality_gates = {
        "underlying_signals_at_least_20": candidate_signal_count >= MIN_SIGNALS,
        "resolved_option_trades_at_least_20": metrics["trades"] >= MIN_RESOLVED_TRADES,
        "option_replay_coverage_at_least_70pct": replay_coverage >= MIN_REPLAY_COVERAGE_PCT,
        "holdout_symbols_at_least_3": metrics["unique_symbols"] >= MIN_SYMBOLS,
        "holdout_dates_at_least_6": metrics["unique_dates"] >= MIN_DATES,
    }
    economic_gates = {
        "holdout_average_r_positive": metrics["average_r"] > 0,
        "holdout_profit_factor_at_least_1_20": _profit_factor_pass(metrics),
        "holdout_drawdown_at_most_6r": metrics["max_drawdown_r"] <= MAX_DRAWDOWN_R,
    }
    acceptance_gates = {**data_quality_gates, **economic_gates}
    data_complete = all(data_quality_gates.values())
    if not data_complete:
        decision = "INSUFFICIENT_DATA_FOR_PULLBACK_SHORT_OPTION_H1"
        failed_gates = [name for name, passed in data_quality_gates.items() if not passed]
        economic_status = "NOT_EVALUABLE"
    elif all(economic_gates.values()):
        decision = "VALIDATED_PULLBACK_SHORT_OPTION_CANDIDATE"
        failed_gates = []
        economic_status = "VALID_SAMPLE"
    else:
        decision = "NO_VALIDATED_PULLBACK_SHORT_OPTION_EDGE"
        failed_gates = [name for name, passed in economic_gates.items() if not passed]
        economic_status = "VALID_SAMPLE"

    return {
        "mode": "ALPHAPILOT_PULLBACK_CONTINUATION_SHORT_OPTION_H1",
        "protocol_revision": PROTOCOL_REVISION,
        "research_only": True,
        "production_rules_changed": False,
        "market_brain_permission_changed": False,
        "paper_trading_permission_changed": False,
        "live_execution_enabled": False,
        "holdout_scored_once": True,
        "decision": decision,
        "failed_gates": failed_gates,
        "data_quality_status": "COMPLETE" if data_complete else "INCOMPLETE",
        "economic_evaluation_status": economic_status,
        "data_quality_gates": data_quality_gates,
        "economic_gates": economic_gates,
        "acceptance_gates": acceptance_gates,
        "holdout_metrics": metrics,
        "source_diagnostics": {
            "candidate_signals": int(candidate_signal_count),
            "attempted_option_replays": int(attempted_signal_count),
            "resolved_option_trades": metrics["trades"],
            "option_replay_coverage_pct": round(replay_coverage, 1),
        },
        "book_diagnostics": book_diagnostics or [],
        "market_brain_diagnostics": market_brain_diagnostics or {
            "role": "DIAGNOSTIC_ONLY",
            "context_match": {"input_trades": metrics["trades"], "matched_trades": 0, "match_rate_pct": 0.0},
            "by_regime": [],
        },
        "trades": prepared,
    }


async def _collect_frozen_signals(provider) -> tuple[list[dict], list[dict]]:
    start = datetime.fromisoformat(HOLDOUT_START).replace(tzinfo=IST)
    end = datetime.fromisoformat(HOLDOUT_END).replace(tzinfo=IST) + timedelta(hours=23, minutes=59)
    signals = []
    errors = []
    for symbol in SYMBOLS:
        try:
            rows = await _historical(provider, symbol, "5m", start - timedelta(days=5), end)
            rows = [row for row in rows if (when := _ts(row[0])) and when <= end]
            closes = [float(row[4]) for row in rows]
            atrs = _atr(rows, 14)
            ema20 = _ema(closes, 20)
            ema50 = _ema(closes, 50)
            for day, indices in sorted(_day_indices(rows).items()):
                if day < HOLDOUT_START or day > HOLDOUT_END:
                    continue
                signal = _pullback_continuation(rows, indices, ema20, ema50, atrs)
                if not signal:
                    continue
                index, direction, stop, features = signal
                if direction != "SHORT":
                    continue
                simulation = _simulate_underlying(rows, index, direction, float(stop), TARGET_R)
                signal_at = _ts(rows[index][0])
                if not simulation or not signal_at:
                    continue
                atr = atrs[index] if index < len(atrs) else 0.0
                try:
                    book = price_action_snapshot(
                        rows,
                        index,
                        "SHORT",
                        atr,
                        float(rows[index - 1][3]),
                    )
                except Exception as exc:
                    book = {
                        "knowledge_revision": BOOK_KNOWLEDGE_REVISION,
                        "price_action_grade": "UNAVAILABLE",
                        "diagnostic_error": f"{exc.__class__.__name__}: {exc}",
                    }
                    errors.append({
                        "symbol": symbol,
                        "signal_at": signal_at.isoformat(),
                        "stage": "BOOK_DIAGNOSTIC",
                        "error": book["diagnostic_error"],
                    })
                signals.append({
                    "strategy": "PULLBACK_CONTINUATION_SHORT",
                    "setup_type": "PULLBACK_CONTINUATION",
                    "symbol": symbol,
                    "direction": "SHORT",
                    "action": "BUY PE",
                    "signal_at": signal_at.isoformat(),
                    "underlying_entry_at": simulation.get("entry_at"),
                    "underlying_entry": simulation.get("entry"),
                    "underlying_stop": simulation.get("stop"),
                    "underlying_target": simulation.get("target"),
                    "underlying_outcome": simulation.get("outcome"),
                    "setup_features": features,
                    "research_features": {"book_price_action": book},
                })
        except Exception as exc:
            errors.append({"symbol": symbol, "stage": "UNDERLYING_SIGNAL_DISCOVERY", "error": f"{exc.__class__.__name__}: {exc}"})
    signals.sort(key=lambda row: (row["signal_at"], row["symbol"]))
    return signals, errors


def _frozen_one_r_view(replay: dict) -> dict:
    scenario = ((replay.get("target_scenarios") or {}).get("1.0R") or {})
    if not scenario:
        return replay
    ambiguous = bool(scenario.get("ambiguous"))
    return {
        **replay,
        "status": "AMBIGUOUS" if ambiguous else "REPLAY_COMPLETE",
        "option_target1": scenario.get("target_price"),
        "option_target2": None,
        "outcome": scenario.get("outcome"),
        "exit_price": scenario.get("exit_price"),
        "exit_at": scenario.get("exit_at"),
        "r_multiple": scenario.get("r_multiple"),
        "frozen_exit_model": "1.0R_TARGET_OR_PREMIUM_STOP_OR_EOD",
        "ambiguity_reason": (
            "The frozen 1R target and premium stop were touched in the same 5-minute candle."
            if ambiguous else None
        ),
    }


async def _replay_frozen_signals(provider, signals: list[dict]) -> tuple[list[dict], list[dict]]:
    try:
        master_rows = await _instrument_rows(list(SYMBOLS))
    except Exception as exc:
        return [], [{
            "stage": "INSTRUMENT_MASTER",
            "error": f"{exc.__class__.__name__}: {exc}",
        }]
    contract_cache: dict[tuple[str, str, str], list[dict]] = {}
    trades = []
    errors = []
    for candidate in signals[:MAX_SIGNALS]:
        symbol = candidate["symbol"]
        signal_at = candidate["signal_at"]
        try:
            when = datetime.fromisoformat(signal_at)
            expiry_date, expiry_dte = _select_expiry(master_rows, symbol, "PE", when.date(), None)
            if expiry_date is None:
                if isinstance(expiry_dte, int) and expiry_dte > AUTO_EXPIRY_MAX_DTE_DAYS:
                    message = f"Nearest PE expiry is {expiry_dte} DTE; exceeds {AUTO_EXPIRY_MAX_DTE_DAYS}-day integrity limit"
                else:
                    message = f"No listed PE expiry on or after {when.date().isoformat()}"
                errors.append({"symbol": symbol, "signal_at": signal_at, "stage": "EXPIRY_SELECTION", "error": message})
                continue
            expiry = expiry_date.isoformat()
            key = (symbol, "PE", expiry)
            contracts = contract_cache.get(key)
            if contracts is None:
                contracts = _available_contracts(master_rows, symbol, expiry, "PE")
                contract_cache[key] = contracts
            if not contracts:
                errors.append({"symbol": symbol, "signal_at": signal_at, "stage": "CONTRACT_SELECTION", "error": f"No PE contracts found for {expiry}"})
                continue
            selected = min(contracts, key=lambda row: abs(float(row["strike"]) - float(candidate["underlying_entry"])))
            replay = await replay_option_trade(
                provider=provider,
                symbol=symbol,
                expiry=expiry,
                strike=float(selected["strike"]),
                option_type="PE",
                trade_date=when.date().isoformat(),
                entry_time=when.strftime("%H:%M"),
                min_rr=TARGET_R,
                resolved_contract=selected,
            )
            replay = _frozen_one_r_view(replay)
            contract = replay.get("contract") if isinstance(replay, dict) else None
            option_contract = None
            if isinstance(contract, dict):
                option_contract = contract.get("trading_symbol") or contract.get("groww_symbol")
            option_contract = option_contract or selected.get("trading_symbol") or selected.get("groww_symbol")
            row = {
                **candidate,
                "expiry": expiry,
                "expiry_dte": expiry_dte,
                "expiry_selection": "NEAREST_LISTED_EXPIRY_ON_OR_AFTER_TRADE_DATE",
                "strike": float(selected["strike"]),
                "strike_selection": "NEAREST_LISTED_STRIKE_TO_UNDERLYING_NEXT_OPEN",
                "option_type": "PE",
                "option_contract": option_contract,
                **replay,
            }
            row["signal_at"] = signal_at
            row["strategy"] = "PULLBACK_CONTINUATION_SHORT"
            row["direction"] = "SHORT"
            row["action"] = "BUY PE"
            if isinstance(row.get("r_multiple"), (int, float)):
                trades.append(row)
            else:
                errors.append({
                    "symbol": symbol,
                    "signal_at": signal_at,
                    "stage": str(row.get("status") or "OPTION_REPLAY"),
                    "error": str(row.get("ambiguity_reason") or row.get("message") or "Option replay did not resolve"),
                })
        except Exception as exc:
            errors.append({"symbol": symbol, "signal_at": signal_at, "stage": "OPTION_PREMIUM_REPLAY", "error": f"{exc.__class__.__name__}: {exc}"})
    return trades, errors


async def run_pullback_short_option_h1(provider) -> dict:
    signals, signal_errors = await _collect_frozen_signals(provider)
    attempted = signals[:MAX_SIGNALS]
    trades, replay_errors = await _replay_frozen_signals(provider, attempted)

    prepared, _ = _metric_rows(trades, ROUND_TRIP_COST_BPS)
    book_rows = _diagnostic_groups(
        prepared,
        lambda row: ((row.get("research_features") or {}).get("book_price_action") or {}).get("price_action_grade"),
    )

    try:
        contexts, context_errors = await _context_for_split(provider, HOLDOUT_START, HOLDOUT_END)
        matched, context_match = _attach_context(prepared, contexts)
    except Exception as exc:
        contexts, matched = [], []
        context_errors = [{"symbol": "MARKET_BRAIN", "error": f"{exc.__class__.__name__}: {exc}"}]
        context_match = {
            "input_trades": len(prepared),
            "matched_trades": 0,
            "match_rate_pct": 0.0,
            "context_lag_minutes": CONTEXT_LAG_MINUTES,
            "unmatched_count": len(prepared),
        }
    market_rows = []
    for row in matched:
        regime, alignment = _market_regime(row.get("market_brain_features") or {})
        row["market_regime"] = regime
        row["market_alignment_score"] = round(alignment, 6)
    market_rows = _diagnostic_groups(matched, lambda row: row.get("market_regime"))

    result = evaluate_pullback_short_option_h1(
        trades,
        candidate_signal_count=len(signals),
        attempted_signal_count=len(attempted),
        round_trip_cost_bps=ROUND_TRIP_COST_BPS,
        book_diagnostics=book_rows,
        market_brain_diagnostics={
            "role": "DIAGNOSTIC_ONLY_NOT_AN_ACCEPTANCE_FILTER",
            "context_lag_minutes": CONTEXT_LAG_MINUTES,
            "market_context_observations": len(contexts),
            "context_match": context_match,
            "by_regime": market_rows,
            "errors": context_errors,
        },
    )
    result.update({
        "request": {
            "symbols": list(SYMBOLS),
            "holdout_start": HOLDOUT_START,
            "holdout_end": HOLDOUT_END,
            "max_signals": MAX_SIGNALS,
        },
        "frozen_candidate": {
            "setup_type": "PULLBACK_CONTINUATION",
            "direction": "SHORT",
            "option_action": "BUY PE",
            "promotion_basis": {
                "development_end": "2026-08-10",
                "all_block_trades": 177,
                "all_block_total_r": 22.24,
                "all_block_average_r": 0.126,
                "all_block_win_rate_approx": 55.9,
                "positive_blocks": 4,
                "independent_blocks": 6,
            },
            "signal_rule": "EMA20 below EMA50; prior candle spans EMA20 and closes at/below EMA20; current candle closes below the prior low; first qualifying signal per symbol/day between 09:45 and 14:15.",
            "underlying_entry": "Next 5-minute open after the signal.",
            "option_contract": "BUY PE at the nearest listed strike to the underlying next-open price and nearest listed expiry on/after the trade date, capped at 35 DTE.",
            "option_exit": "Premium stop from the frozen AlphaPilot risk-fraction model, fixed 1R target, otherwise EOD; ambiguous same-candle stop/target paths are excluded.",
        },
        "cost_model": {"round_trip_cost_bps": ROUND_TRIP_COST_BPS},
        "book_knowledge": {
            "revision": BOOK_KNOWLEDGE_REVISION,
            "role": "DIAGNOSTIC_ONLY_NOT_AN_ACCEPTANCE_FILTER",
            "threshold_ownership": "All numeric thresholds are AlphaPilot hypotheses; the source contributes concepts, not validated parameters.",
        },
        "fixed_acceptance_rules": {
            "min_underlying_signals": MIN_SIGNALS,
            "min_resolved_option_trades": MIN_RESOLVED_TRADES,
            "min_option_replay_coverage_pct": MIN_REPLAY_COVERAGE_PCT,
            "min_symbols": MIN_SYMBOLS,
            "min_dates": MIN_DATES,
            "average_r": "GREATER_THAN_ZERO",
            "min_profit_factor": MIN_PROFIT_FACTOR,
            "max_drawdown_r": MAX_DRAWDOWN_R,
        },
        "errors": [
            *signal_errors,
            *replay_errors,
            *[
                {"symbol": row.get("symbol"), "stage": "MARKET_BRAIN_DIAGNOSTIC", "error": row.get("error")}
                for row in context_errors
            ],
        ],
        "limitations": [
            "This is one frozen research holdout; it cannot change scanner, paper or live permissions.",
            "Market Brain and book-informed price-action features are diagnostics only and cannot include, exclude or rescore a trade.",
            "Historical option OHLC plus a 10 bps cost stress is not a broker contract-note reconstruction of bid/ask spread, taxes and slippage.",
            "A positive H-1 result promotes only a research candidate; it still requires forward paper execution evidence before any controlled-live consideration.",
        ],
    })
    return result
