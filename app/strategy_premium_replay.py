from __future__ import annotations

from datetime import datetime

from .fno_historical_backtest import (
    AUTO_EXPIRY_MAX_DTE_DAYS,
    _available_contracts,
    _instrument_rows,
    _select_expiry,
)
from .fno_premium_replay import replay_option_trade
from .strategy_research import run_strategy_research


async def run_strategy_premium_replay(
    provider,
    symbols: list[str],
    start_date: str,
    end_date: str,
    strategy: str = "VWAP_TREND",
    research_target_r: float = 1.0,
    premium_min_rr: float = 1.5,
    max_trades: int = 30,
):
    strategy = str(strategy).upper().strip()
    if strategy not in {"VWAP_TREND", "ORB_30", "BREAKOUT_20"}:
        raise ValueError("strategy must be VWAP_TREND, ORB_30 or BREAKOUT_20")

    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    max_trades = max(1, min(int(max_trades), 50))

    research = await run_strategy_research(
        provider,
        symbols,
        start_date,
        end_date,
        research_target_r,
    )
    candidates = list((research.get("trades_by_strategy") or {}).get(strategy, []))
    candidates.sort(key=lambda x: str(x.get("entry_at") or x.get("signal_at") or ""))
    candidates = candidates[:max_trades]

    master_rows = await _instrument_rows(symbols)
    contract_cache: dict[tuple[str, str, str], list[dict]] = {}
    trades = []
    errors = []

    for candidate in candidates:
        symbol = str(candidate.get("symbol", "")).upper().strip()
        direction = str(candidate.get("direction", "")).upper()
        option_type = "CE" if direction == "LONG" else "PE"
        entry_at = str(candidate.get("entry_at") or candidate.get("signal_at") or "")
        try:
            when = datetime.fromisoformat(entry_at)
            selected_expiry_date, expiry_dte = _select_expiry(
                master_rows,
                symbol,
                option_type,
                when.date(),
                None,
            )
            if selected_expiry_date is None:
                if isinstance(expiry_dte, int) and expiry_dte > AUTO_EXPIRY_MAX_DTE_DAYS:
                    error = f"Nearest listed {option_type} expiry is {expiry_dte} DTE; exceeds {AUTO_EXPIRY_MAX_DTE_DAYS}-day integrity limit"
                else:
                    error = f"No listed {option_type} expiry on or after {when.date().isoformat()}"
                errors.append({"symbol": symbol, "entry_at": entry_at, "stage": "EXPIRY_SELECTION", "error": error})
                continue

            selected_expiry = selected_expiry_date.isoformat()
            cache_key = (symbol, option_type, selected_expiry)
            contracts = contract_cache.get(cache_key)
            if contracts is None:
                contracts = _available_contracts(master_rows, symbol, selected_expiry, option_type)
                contract_cache[cache_key] = contracts
            if not contracts:
                errors.append({"symbol": symbol, "entry_at": entry_at, "stage": "CONTRACT_SELECTION", "error": f"No {option_type} contracts found for {selected_expiry}"})
                continue

            underlying_entry = float(candidate.get("entry"))
            selected = min(contracts, key=lambda x: abs(float(x["strike"]) - underlying_entry))
            replay = await replay_option_trade(
                provider=provider,
                symbol=symbol,
                expiry=selected_expiry,
                strike=float(selected["strike"]),
                option_type=option_type,
                trade_date=when.date().isoformat(),
                entry_time=when.strftime("%H:%M"),
                min_rr=premium_min_rr,
                resolved_contract=selected,
            )

            replay_contract = replay.get("contract") if isinstance(replay, dict) else None
            option_contract = None
            if isinstance(replay_contract, dict):
                option_contract = replay_contract.get("trading_symbol") or replay_contract.get("groww_symbol")
            option_contract = option_contract or selected.get("trading_symbol") or selected.get("groww_symbol")

            row = {
                "strategy": strategy,
                "symbol": symbol,
                "signal_at": candidate.get("signal_at"),
                "entry_at": entry_at,
                "direction": direction,
                "action": f"BUY {option_type}",
                "underlying_entry": underlying_entry,
                "underlying_outcome": candidate.get("outcome"),
                "underlying_r_multiple": candidate.get("r_multiple"),
                "research_features": candidate.get("features"),
                "expiry": selected_expiry,
                "expiry_dte": expiry_dte,
                "strike": float(selected["strike"]),
                "option_type": option_type,
                "option_contract": option_contract,
                **replay,
            }
            row["strategy"] = strategy
            row["symbol"] = symbol
            row["signal_at"] = candidate.get("signal_at")
            row["entry_at"] = entry_at
            row["expiry"] = selected_expiry
            row["expiry_dte"] = expiry_dte
            trades.append(row)
        except Exception as exc:
            errors.append({"symbol": symbol, "entry_at": entry_at, "stage": "PREMIUM_REPLAY", "error": str(exc)})

    resolved = [t for t in trades if isinstance(t.get("r_multiple"), (int, float))]
    wins = sum(1 for t in resolved if float(t["r_multiple"]) > 0)
    losses = sum(1 for t in resolved if float(t["r_multiple"]) < 0)
    total_r = sum(float(t["r_multiple"]) for t in resolved)
    equity = peak = max_dd = 0.0
    for trade in sorted(resolved, key=lambda x: str(x.get("entry_at", ""))):
        equity += float(trade["r_multiple"])
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "mode": "ALPHAPILOT_STRATEGY_TO_TRUE_OPTION_PREMIUM_REPLAY",
        "strategy": strategy,
        "start_date": start_date,
        "end_date": end_date,
        "research_target_r": research_target_r,
        "premium_min_risk_reward": premium_min_rr,
        "candidate_signals_total": len((research.get("trades_by_strategy") or {}).get(strategy, [])),
        "candidate_signals_selected": len(candidates),
        "summary": {
            "trades": len(resolved),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(resolved) * 100, 1) if resolved else 0.0,
            "total_r": round(total_r, 3),
            "average_r": round(total_r / len(resolved), 3) if resolved else 0.0,
            "max_drawdown_r": round(max_dd, 3),
            "ambiguous": sum(1 for t in trades if t.get("status") == "AMBIGUOUS"),
        },
        "trades": trades,
        "errors": errors,
        "research_errors": research.get("errors", []),
        "limitations": [
            "The underlying signal is generated by the frozen Strategy Research v2 rule set; no legacy AlphaPilot signal is used.",
            "Option entry time is the underlying strategy's next-candle entry time, avoiding signal-candle look-ahead.",
            "Each signal is translated to CE for LONG and PE for SHORT, using nearest listed strike to the underlying entry and nearest listed expiry on or after the trade date.",
            "Historical option P&L uses Groww 5-minute option-premium OHLC and the existing premium risk model.",
            "Brokerage, taxes, bid/ask spread and slippage are not yet deducted.",
        ],
    }
