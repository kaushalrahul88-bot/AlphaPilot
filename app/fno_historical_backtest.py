from __future__ import annotations

import csv
import io
from datetime import datetime

import httpx

from .backtest import run_backtest
from .fno_history_probe import INSTRUMENT_CSV_URL, _as_float, _norm_expiry
from .fno_premium_replay import replay_option_trade


async def _instrument_rows():
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(INSTRUMENT_CSV_URL)
        response.raise_for_status()
    return list(csv.DictReader(io.StringIO(response.text)))


def _available_contracts(rows, symbol: str, expiry: str, option_type: str):
    target_symbol = symbol.upper().strip()
    target_expiry = _norm_expiry(expiry)
    target_type = option_type.upper().strip()
    out = []
    for row in rows:
        if str(row.get("exchange", "")).upper() != "NSE":
            continue
        if str(row.get("segment", "")).upper() != "FNO":
            continue
        if str(row.get("underlying_symbol", "")).upper().strip() != target_symbol:
            continue
        if _norm_expiry(row.get("expiry_date", "")) != target_expiry:
            continue
        if str(row.get("instrument_type", "")).upper() != target_type:
            continue
        strike = _as_float(row.get("strike_price"))
        if strike is None or strike <= 0:
            continue
        groww_symbol = row.get("groww_symbol") or row.get("groww_ticker") or row.get("symbol") or row.get("trading_symbol")
        if not groww_symbol:
            continue
        out.append({
            "underlying": target_symbol,
            "expiry": target_expiry,
            "strike": float(strike),
            "option_type": target_type,
            "groww_symbol": groww_symbol,
            "trading_symbol": row.get("trading_symbol") or row.get("tradingsymbol") or row.get("symbol"),
            "lot_size": _as_float(row.get("lot_size")),
            "instrument_type": row.get("instrument_type"),
            "segment": row.get("segment"),
            "exchange": row.get("exchange"),
        })
    return out


async def run_true_premium_backtest(provider, symbols: list[str], start_date: str, end_date: str, expiry: str, min_rr: float = 1.5, entry_before: str | None = None, max_trades: int = 20):
    expiry_date = datetime.fromisoformat(_norm_expiry(expiry)).date()
    if datetime.fromisoformat(end_date[:10]).date() > expiry_date:
        raise ValueError("end_date cannot be after the selected option expiry")
    max_trades = max(1, min(int(max_trades), 50))

    directional = await run_backtest(provider, symbols, start_date, end_date, min_rr, entry_before)
    candidates = list(directional.get("trades", []))[:max_trades]
    master_rows = await _instrument_rows()
    contract_cache: dict[tuple[str, str], list[dict]] = {}
    trades = []
    errors = []

    for candidate in candidates:
        symbol = str(candidate.get("symbol", "")).upper()
        option_type = "CE" if candidate.get("direction") == "LONG" else "PE"
        key = (symbol, option_type)
        try:
            contracts = contract_cache.get(key)
            if contracts is None:
                contracts = _available_contracts(master_rows, symbol, expiry, option_type)
                contract_cache[key] = contracts
            if not contracts:
                errors.append({"symbol": symbol, "timestamp": candidate.get("timestamp"), "stage": "CONTRACT_SELECTION", "error": f"No {option_type} contracts found for {expiry}"})
                continue
            underlying_entry = float(candidate.get("entry"))
            selected = min(contracts, key=lambda x: abs(float(x["strike"]) - underlying_entry))
            when = datetime.fromisoformat(str(candidate["timestamp"]))
            replay = await replay_option_trade(
                provider=provider,
                symbol=symbol,
                expiry=expiry,
                strike=float(selected["strike"]),
                option_type=option_type,
                trade_date=when.date().isoformat(),
                entry_time=when.strftime("%H:%M"),
                min_rr=min_rr,
                resolved_contract=selected,
            )
            replay_contract = replay.get("contract") if isinstance(replay, dict) else None
            option_contract = None
            if isinstance(replay_contract, dict):
                option_contract = replay_contract.get("trading_symbol") or replay_contract.get("groww_symbol")
            option_contract = option_contract or selected.get("trading_symbol") or selected.get("groww_symbol")
            row = {
                "symbol": symbol,
                "timestamp": candidate["timestamp"],
                "signal_at": candidate["timestamp"],
                "direction": candidate.get("direction"),
                "action": f"BUY {option_type}",
                "mtf_alpha": candidate.get("mtf_alpha"),
                "underlying_entry": underlying_entry,
                "expiry": expiry,
                "strike": float(selected["strike"]),
                "option_type": option_type,
                "option_contract": option_contract,
                "strike_selection": "NEAREST_LISTED_STRIKE_TO_UNDERLYING_ENTRY",
                **replay,
            }
            row["timestamp"] = candidate["timestamp"]
            trades.append(row)
        except Exception as exc:
            errors.append({"symbol": symbol, "timestamp": candidate.get("timestamp"), "stage": "PREMIUM_REPLAY", "error": str(exc)})

    resolved = [t for t in trades if isinstance(t.get("r_multiple"), (int, float))]
    wins = sum(1 for t in resolved if float(t["r_multiple"]) > 0)
    losses = sum(1 for t in resolved if float(t["r_multiple"]) < 0)
    total_r = sum(float(t["r_multiple"]) for t in resolved)
    equity = peak = max_dd = 0.0
    for t in sorted(resolved, key=lambda x: str(x.get("timestamp", ""))):
        equity += float(t["r_multiple"])
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "mode": "TRUE_OPTION_PREMIUM_HISTORICAL_BACKTEST_PHASE1",
        "start_date": start_date,
        "end_date": end_date,
        "expiry": _norm_expiry(expiry),
        "min_risk_reward": min_rr,
        "entry_before": entry_before,
        "candidate_signals": len(candidates),
        "summary": {
            "trades": len(resolved),
            "replayed": len(trades),
            "resolved": len(resolved),
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
        "limitations": [
            "P&L uses actual Groww historical 5-minute option-premium OHLC for exact contracts.",
            "Historical direction/timestamp comes from the existing technical MTF scanner replay.",
            "Strike selection is nearest listed strike to the historical underlying entry for the supplied expiry; historical OI/IV-based strike ranking is not reconstructed.",
            "Historical F&O confirmation score, option-chain OI/IV/Greeks, external news and GIFT context are not reconstructed and are never fabricated.",
            "Same-candle stop/target collisions remain AMBIGUOUS and are excluded from resolved R statistics.",
            "Brokerage, taxes, bid/ask spread and slippage are not yet deducted.",
        ],
    }
