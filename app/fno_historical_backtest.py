from __future__ import annotations

import csv
import io
from datetime import datetime

import httpx

from .backtest import run_backtest
from .fno_history_probe import INSTRUMENT_CSV_URL, _as_float, _norm_expiry
from .fno_premium_replay import replay_option_trade

AUTO_EXPIRY_MAX_DTE_DAYS = 35


async def _instrument_rows(symbols: list[str] | None = None):
    """Download Groww instrument master once, but retain only relevant NSE F&O rows.

    The full master can be large enough to push Render's 512 MB free instance over its
    memory limit when combined with historical candle arrays. Filtering while parsing
    avoids keeping thousands of unrelated equity/commodity rows in memory.
    """
    wanted = {str(s).upper().strip() for s in (symbols or []) if str(s).strip()}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(INSTRUMENT_CSV_URL)
        response.raise_for_status()
        text = response.text
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if str(row.get("exchange", "")).upper() != "NSE":
            continue
        if str(row.get("segment", "")).upper() != "FNO":
            continue
        underlying = str(row.get("underlying_symbol", "")).upper().strip()
        if wanted and underlying not in wanted:
            continue
        if str(row.get("instrument_type", "")).upper() not in {"CE", "PE"}:
            continue
        rows.append(row)
    del text
    return rows


def _available_expiries(rows, symbol: str, option_type: str, on_date):
    target_symbol = symbol.upper().strip()
    target_type = option_type.upper().strip()
    found = set()
    for row in rows:
        if str(row.get("underlying_symbol", "")).upper().strip() != target_symbol:
            continue
        if str(row.get("instrument_type", "")).upper() != target_type:
            continue
        text = _norm_expiry(row.get("expiry_date", ""))
        try:
            d = datetime.fromisoformat(text).date()
        except Exception:
            continue
        if d >= on_date:
            found.add(d)
    return sorted(found)


def _available_contracts(rows, symbol: str, expiry: str, option_type: str):
    target_symbol = symbol.upper().strip()
    target_expiry = _norm_expiry(expiry)
    target_type = option_type.upper().strip()
    out = []
    for row in rows:
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


def _select_expiry(rows, symbol: str, option_type: str, trade_date, fixed_expiry: str | None):
    if fixed_expiry:
        d = datetime.fromisoformat(_norm_expiry(fixed_expiry)).date()
        return (d, (d - trade_date).days) if d >= trade_date else (None, None)
    expiries = _available_expiries(rows, symbol, option_type, trade_date)
    if not expiries:
        return None, None
    chosen = expiries[0]
    dte = (chosen - trade_date).days
    if dte > AUTO_EXPIRY_MAX_DTE_DAYS:
        return None, dte
    return chosen, dte


async def run_true_premium_backtest(provider, symbols: list[str], start_date: str, end_date: str, expiry: str | None = None, min_rr: float = 1.5, entry_before: str | None = None, max_trades: int = 20):
    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    fixed_expiry = _norm_expiry(expiry) if expiry else None
    if fixed_expiry:
        expiry_date = datetime.fromisoformat(fixed_expiry).date()
        if datetime.fromisoformat(end_date[:10]).date() > expiry_date:
            raise ValueError("end_date cannot be after the selected option expiry")
    max_trades = max(1, min(int(max_trades), 50))

    directional = await run_backtest(provider, symbols, start_date, end_date, min_rr, entry_before)
    all_candidates = list(directional.get("trades", []))
    all_candidates.sort(key=lambda x: str(x.get("timestamp", "")))
    candidates = all_candidates[:max_trades]
    # Keep only instrument rows needed for this test universe. This materially
    # lowers peak memory on Render's 512 MB free worker.
    master_rows = await _instrument_rows(symbols)
    contract_cache: dict[tuple[str, str, str], list[dict]] = {}
    trades = []
    errors = []

    for candidate in candidates:
        symbol = str(candidate.get("symbol", "")).upper()
        option_type = "CE" if candidate.get("direction") == "LONG" else "PE"
        try:
            when = datetime.fromisoformat(str(candidate["timestamp"]))
            selected_expiry_date, expiry_dte = _select_expiry(master_rows, symbol, option_type, when.date(), fixed_expiry)
            if selected_expiry_date is None:
                if not fixed_expiry and isinstance(expiry_dte, int) and expiry_dte > AUTO_EXPIRY_MAX_DTE_DAYS:
                    error = f"Nearest listed {option_type} expiry is {expiry_dte} DTE; exceeds {AUTO_EXPIRY_MAX_DTE_DAYS}-day historical integrity limit"
                else:
                    error = f"No listed {option_type} expiry on or after {when.date().isoformat()}"
                errors.append({"symbol": symbol, "timestamp": candidate.get("timestamp"), "stage": "EXPIRY_SELECTION", "error": error})
                continue
            selected_expiry = selected_expiry_date.isoformat()
            key = (symbol, option_type, selected_expiry)
            contracts = contract_cache.get(key)
            if contracts is None:
                contracts = _available_contracts(master_rows, symbol, selected_expiry, option_type)
                contract_cache[key] = contracts
            if not contracts:
                errors.append({"symbol": symbol, "timestamp": candidate.get("timestamp"), "stage": "CONTRACT_SELECTION", "error": f"No {option_type} contracts found for {selected_expiry}"})
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
                "expiry": selected_expiry,
                "expiry_dte": expiry_dte,
                "expiry_selection": "FIXED_REQUESTED_EXPIRY" if fixed_expiry else "NEAREST_LISTED_EXPIRY_ON_OR_AFTER_TRADE_DATE",
                "strike": float(selected["strike"]),
                "option_type": option_type,
                "option_contract": option_contract,
                "strike_selection": "NEAREST_LISTED_STRIKE_TO_UNDERLYING_ENTRY",
                **replay,
            }
            row["timestamp"] = candidate["timestamp"]
            row["expiry"] = selected_expiry
            row["expiry_dte"] = expiry_dte
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

    selected_symbols = sorted({str(x.get("symbol", "")) for x in candidates if x.get("symbol")})
    expiries_used = sorted({str(x.get("expiry")) for x in trades if x.get("expiry")})
    return {
        "mode": "TRUE_OPTION_PREMIUM_HISTORICAL_BACKTEST_PHASE2_AUTO_EXPIRY",
        "start_date": start_date,
        "end_date": end_date,
        "expiry": fixed_expiry,
        "expiry_mode": "FIXED" if fixed_expiry else "AUTO_NEAREST_LISTED",
        "auto_expiry_max_dte_days": AUTO_EXPIRY_MAX_DTE_DAYS,
        "expiries_used": expiries_used,
        "min_risk_reward": min_rr,
        "entry_before": entry_before,
        "candidate_selection": "CHRONOLOGICAL_FIRST_N_ACROSS_UNIVERSE",
        "candidate_signals_total": len(all_candidates),
        "candidate_signals_selected": len(candidates),
        "candidate_symbols_selected": selected_symbols,
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
            f"With Auto Expiry, each candidate uses the nearest listed option expiry on or after the trade date, but candidates are rejected if that expiry is more than {AUTO_EXPIRY_MAX_DTE_DAYS} calendar days away.",
            "This prevents stale current-master contracts from being silently assigned to much older historical signals.",
            "Expired contracts absent from the current instrument master cannot be reconstructed by automatic selection and are reported as errors rather than fabricated.",
            "When max_trades truncates a multi-symbol sample, candidates are selected chronologically across the requested universe instead of being biased toward symbols listed first.",
            "Strike selection is nearest listed strike to the historical underlying entry; historical OI/IV-based strike ranking is not reconstructed.",
            "Historical F&O confirmation score, option-chain OI/IV/Greeks, external news and GIFT context are not reconstructed and are never fabricated.",
            "Same-candle stop/target collisions remain AMBIGUOUS and are excluded from resolved R statistics.",
            "Brokerage, taxes, bid/ask spread and slippage are not yet deducted.",
        ],
    }
