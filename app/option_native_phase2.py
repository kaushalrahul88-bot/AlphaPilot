from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean

from .backtest import _historical, _ts
from .fno_historical_backtest import _available_contracts, _instrument_rows, _select_expiry
from .fno_premium_replay import _historical_option_day, _risk_fraction, _simulate

MODELS = ("CE_PREMIUM_MOMENTUM", "PE_PREMIUM_MOMENTUM", "CE_PE_RELATIVE_STRENGTH")


def _num(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_rows(rows: list[list]) -> list[list]:
    """Keep only candles with usable timestamp/OHLC; coerce missing volume to zero."""
    cleaned = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5 or not _ts(row[0]):
            continue
        o, h, l, c = (_num(row[i]) for i in range(1, 5))
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            continue
        volume = _num(row[5], 0.0) if len(row) > 5 else 0.0
        cleaned.append([row[0], o, h, l, c, max(volume or 0.0, 0.0)])
    return cleaned


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    a = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(a * value + (1.0 - a) * out[-1])
    return out


def _vwap(rows: list[list]) -> list[float]:
    total_pv = 0.0
    total_v = 0.0
    out = []
    for row in rows:
        high, low, close = float(row[2]), float(row[3]), float(row[4])
        volume = _num(row[5], 0.0) if len(row) > 5 else 0.0
        typical = (high + low + close) / 3.0
        total_pv += typical * max(volume or 0.0, 0.0)
        total_v += max(volume or 0.0, 0.0)
        out.append(total_pv / total_v if total_v > 0 else close)
    return out


def _volume_ratio(rows: list[list], i: int, lookback: int = 8) -> float | None:
    if i >= len(rows) or len(rows[i]) <= 5:
        return None
    volume = _num(rows[i][5], 0.0) or 0.0
    prior = []
    for j in range(max(0, i - lookback), i):
        if len(rows[j]) > 5:
            v = _num(rows[j][5])
            if v is not None:
                prior.append(v)
    avg = mean(prior) if prior else 0.0
    return volume / avg if avg > 0 else None


def _premium_features(rows: list[list]) -> dict:
    closes = [float(r[4]) for r in rows]
    return {"ema5": _ema(closes, 5), "ema12": _ema(closes, 12), "vwap": _vwap(rows), "closes": closes}


def _momentum_signal(rows: list[list], option_type: str):
    if len(rows) < 14:
        return None
    f = _premium_features(rows)
    for i in range(12, len(rows) - 1):
        when = _ts(rows[i][0])
        if not when or not (time(9, 45) <= when.time() <= time(13, 30)):
            continue
        close = f["closes"][i]
        prev3 = f["closes"][i - 3]
        if prev3 <= 0:
            continue
        ret3 = close / prev3 - 1.0
        vr = _volume_ratio(rows, i)
        volume_ok = vr is None or vr >= 1.10
        trend_ok = close > f["ema5"][i] > f["ema12"][i] and close > f["vwap"][i]
        if trend_ok and ret3 >= 0.03 and volume_ok:
            return {
                "index": i,
                "option_type": option_type,
                "signal_at": when,
                "premium_return_3bar_pct": round(ret3 * 100.0, 2),
                "volume_ratio": round(vr, 2) if vr is not None else None,
                "premium_vwap": round(f["vwap"][i], 2),
                "ema5": round(f["ema5"][i], 2),
                "ema12": round(f["ema12"][i], 2),
            }
    return None


def _relative_signal(ce_rows: list[list], pe_rows: list[list]):
    if len(ce_rows) < 5 or len(pe_rows) < 5:
        return None
    ce_map = {_ts(r[0]): i for i, r in enumerate(ce_rows) if _ts(r[0])}
    pe_map = {_ts(r[0]): i for i, r in enumerate(pe_rows) if _ts(r[0])}
    common = sorted(set(ce_map).intersection(pe_map))
    ce_f = _premium_features(ce_rows)
    pe_f = _premium_features(pe_rows)
    for when in common:
        if not (time(9, 45) <= when.time() <= time(13, 30)):
            continue
        ci, pi = ce_map[when], pe_map[when]
        if ci < 3 or pi < 3 or ci >= len(ce_rows) - 1 or pi >= len(pe_rows) - 1:
            continue
        ce_prev = ce_f["closes"][ci - 3]
        pe_prev = pe_f["closes"][pi - 3]
        if ce_prev <= 0 or pe_prev <= 0:
            continue
        ce_ret = ce_f["closes"][ci] / ce_prev - 1.0
        pe_ret = pe_f["closes"][pi] / pe_prev - 1.0
        ce_ok = ce_f["closes"][ci] > ce_f["ema5"][ci] > ce_f["ema12"][ci]
        pe_ok = pe_f["closes"][pi] > pe_f["ema5"][pi] > pe_f["ema12"][pi]
        if ce_ok and ce_ret >= 0.03 and ce_ret - pe_ret >= 0.04:
            return {"index": ci, "option_type": "CE", "signal_at": when, "relative_edge_pct": round((ce_ret - pe_ret) * 100.0, 2), "side_return_3bar_pct": round(ce_ret * 100.0, 2), "opposite_return_3bar_pct": round(pe_ret * 100.0, 2)}
        if pe_ok and pe_ret >= 0.03 and pe_ret - ce_ret >= 0.04:
            return {"index": pi, "option_type": "PE", "signal_at": when, "relative_edge_pct": round((pe_ret - ce_ret) * 100.0, 2), "side_return_3bar_pct": round(pe_ret * 100.0, 2), "opposite_return_3bar_pct": round(ce_ret * 100.0, 2)}
    return None


def _regime_at(rows: list[list], when: datetime) -> str:
    day = [r for r in rows if (w := _ts(r[0])) and w.date() == when.date() and w <= when]
    if not day:
        return "UNKNOWN"
    open_price = _num(day[0][1])
    close = _num(day[-1][4])
    if not open_price or close is None:
        return "UNKNOWN"
    pct = (close / open_price - 1.0) * 100.0
    if pct >= 0.35:
        return "BULLISH"
    if pct <= -0.35:
        return "BEARISH"
    return "SIDEWAYS"


def _simulate_native(rows: list[list], signal: dict, rr: float, cost_bps: float):
    i = int(signal["index"])
    entry_index = i + 1
    if entry_index >= len(rows):
        return None
    entry = _num(rows[entry_index][1])
    when = _ts(rows[entry_index][0])
    if entry is None or entry <= 0 or not when:
        return None
    risk_fraction = _risk_fraction(entry)
    risk = entry * risk_fraction
    if risk <= 0:
        return None
    stop = max(0.05, entry - risk)
    t1 = entry + risk * rr
    t2 = entry + risk * max(2.0, rr + 0.5)
    sim = _simulate(rows, entry_index, entry, stop, t1, t2) or {}
    exit_price = _num(sim.get("exit_price"))
    r = (exit_price - entry) / risk if exit_price is not None else None
    cost_r = (entry * max(cost_bps, 0.0) / 10000.0) / risk
    adj = r - cost_r if r is not None else None
    max_price = _num(sim.get("max_price"), entry)
    min_price = _num(sim.get("min_price"), entry)
    mfe = max(0.0, ((max_price if max_price is not None else entry) - entry) / risk)
    mae = max(0.0, (entry - (min_price if min_price is not None else entry)) / risk)
    return {
        "entry_at": when.isoformat(),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target1": round(t1, 2),
        "target2": round(t2, 2),
        "exit_at": sim.get("exit_at"),
        "exit_price": round(exit_price, 2) if exit_price is not None else None,
        "outcome": sim.get("outcome"),
        "r_multiple": round(r, 3) if r is not None else None,
        "cost_adjusted_r": round(adj, 3) if adj is not None else None,
        "mfe_r": round(mfe, 3),
        "mae_r": round(mae, 3),
        "premium_risk_percent": round(risk_fraction * 100.0, 1),
    }


def _summary(trades: list[dict]):
    vals = [float(t["cost_adjusted_r"]) for t in trades if isinstance(t.get("cost_adjusted_r"), (int, float))]
    wins = sum(1 for x in vals if x > 0)
    gains = sum(x for x in vals if x > 0)
    losses_abs = abs(sum(x for x in vals if x < 0))
    equity = peak = max_dd = 0.0
    for x in vals:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    avg = sum(vals) / len(vals) if vals else 0.0
    classification = "PASS" if len(vals) >= 30 and avg >= 0.10 else "WATCH" if len(vals) >= 20 and avg > 0 else "FAIL"
    mfe = [float(t["mfe_r"]) for t in trades if isinstance(t.get("mfe_r"), (int, float))]
    mae = [float(t["mae_r"]) for t in trades if isinstance(t.get("mae_r"), (int, float))]
    return {
        "trades": len(vals), "wins": wins, "losses": sum(1 for x in vals if x < 0),
        "win_rate": round(wins / len(vals) * 100.0, 1) if vals else 0.0,
        "average_r": round(avg, 3), "total_r": round(sum(vals), 3),
        "profit_factor": round(gains / losses_abs, 3) if losses_abs > 0 else None,
        "max_drawdown_r": round(max_dd, 3),
        "avg_mfe_r": round(mean(mfe), 3) if mfe else None,
        "avg_mae_r": round(mean(mae), 3) if mae else None,
        "classification": classification,
    }


async def run_option_native_phase2(provider, symbols: list[str], start_date: str, end_date: str, premium_min_rr: float = 1.5, max_trades_per_model: int = 30, round_trip_cost_bps: float = 10.0):
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 14:
        raise ValueError("Option-native Phase 2 is limited to 14 calendar days per run to protect Groww/Render limits")
    rr = max(1.0, float(premium_min_rr))
    cap = max(1, min(int(max_trades_per_model), 50))
    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    master = await _instrument_rows(symbols)
    model_trades = {m: [] for m in MODELS}
    errors = []

    try:
        nifty_rows = _clean_rows(await _historical(provider, "NIFTY", "5m", start, end))
    except Exception as exc:
        nifty_rows = []
        errors.append({"symbol": "NIFTY", "stage": "REGIME_DATA", "error": f"{exc.__class__.__name__}: {exc}"})

    for symbol in symbols:
        if all(len(model_trades[m]) >= cap for m in MODELS):
            break
        try:
            underlying = _clean_rows(await _historical(provider, symbol, "5m", start, end))
        except Exception as exc:
            errors.append({"symbol": symbol, "stage": "UNDERLYING", "error": f"{exc.__class__.__name__}: {exc}"})
            continue
        if not underlying:
            errors.append({"symbol": symbol, "stage": "UNDERLYING", "error": "No valid historical candles after cleaning"})
            continue

        days = sorted({_ts(r[0]).date().isoformat() for r in underlying if _ts(r[0]) and start.date() <= _ts(r[0]).date() <= end.date()})
        for day in days:
            if all(len(model_trades[m]) >= cap for m in MODELS):
                break
            try:
                day_rows = [r for r in underlying if (w := _ts(r[0])) and w.date().isoformat() == day]
                ref = next((r for r in day_rows if (w := _ts(r[0])) and w.time() >= time(9, 45)), None)
                if not ref:
                    continue
                spot = _num(ref[4])
                if spot is None or spot <= 0:
                    errors.append({"symbol": symbol, "date": day, "stage": "ATM_REFERENCE", "error": "Invalid 09:45 underlying reference"})
                    continue

                contracts = {}
                candle_sets = {}
                for option_type in ("CE", "PE"):
                    try:
                        expiry, dte = _select_expiry(master, symbol, option_type, datetime.fromisoformat(day).date(), None)
                        if expiry is None:
                            raise ValueError("No listed expiry")
                        expiry_s = expiry.isoformat()
                        available = [x for x in _available_contracts(master, symbol, expiry_s, option_type) if _num(x.get("strike")) is not None]
                        if not available:
                            raise ValueError("No contracts with valid strikes for selected expiry")
                        selected = min(available, key=lambda x: abs(float(x["strike"]) - spot))
                        candles = _clean_rows(await _historical_option_day(provider, selected, day, "5minute"))
                        if len(candles) < 14:
                            raise ValueError(f"Insufficient valid 5-minute premium candles ({len(candles)})")
                        contracts[option_type] = (selected, expiry_s, dte)
                        candle_sets[option_type] = candles
                    except Exception as exc:
                        errors.append({"symbol": symbol, "date": day, "option_type": option_type, "stage": "CONTRACT_OR_CANDLES", "error": f"{exc.__class__.__name__}: {exc}"})

                if "CE" not in candle_sets or "PE" not in candle_sets:
                    continue

                try:
                    signals = {
                        "CE_PREMIUM_MOMENTUM": _momentum_signal(candle_sets["CE"], "CE"),
                        "PE_PREMIUM_MOMENTUM": _momentum_signal(candle_sets["PE"], "PE"),
                        "CE_PE_RELATIVE_STRENGTH": _relative_signal(candle_sets["CE"], candle_sets["PE"]),
                    }
                except Exception as exc:
                    errors.append({"symbol": symbol, "date": day, "stage": "SIGNAL_GENERATION", "error": f"{exc.__class__.__name__}: {exc}"})
                    continue

                for model, signal in signals.items():
                    if not signal or len(model_trades[model]) >= cap:
                        continue
                    try:
                        option_type = signal["option_type"]
                        selected, expiry_s, dte = contracts[option_type]
                        sim = _simulate_native(candle_sets[option_type], signal, rr, round_trip_cost_bps)
                        if not sim:
                            continue
                        regime = _regime_at(nifty_rows, signal["signal_at"]) if nifty_rows else "UNKNOWN"
                        strike = _num(selected.get("strike"))
                        model_trades[model].append({
                            "model": model, "symbol": symbol, "date": day,
                            "signal_at": signal["signal_at"].isoformat(), "action": f"BUY {option_type}",
                            "option_type": option_type,
                            "contract": selected.get("trading_symbol") or selected.get("groww_symbol"),
                            "expiry": expiry_s, "expiry_dte": dte, "strike": strike,
                            "underlying_reference": round(spot, 2), "market_regime": regime,
                            "signal_features": {k: v for k, v in signal.items() if k not in {"index", "option_type", "signal_at"}},
                            **sim,
                        })
                    except Exception as exc:
                        errors.append({"symbol": symbol, "date": day, "model": model, "stage": "MODEL_SIMULATION", "error": f"{exc.__class__.__name__}: {exc}"})
            except Exception as exc:
                errors.append({"symbol": symbol, "date": day, "stage": "DAY_PROCESSING", "error": f"{exc.__class__.__name__}: {exc}"})
                continue

    leaderboard = []
    for model in MODELS:
        s = _summary(model_trades[model])
        by_regime = {
            regime: _summary([t for t in model_trades[model] if t.get("market_regime") == regime])
            for regime in ("BULLISH", "BEARISH", "SIDEWAYS", "UNKNOWN")
            if any(t.get("market_regime") == regime for t in model_trades[model])
        }
        leaderboard.append({"model": model, **s, "by_regime": by_regime})
    leaderboard.sort(key=lambda x: (x["average_r"], x["trades"]), reverse=True)
    for i, row in enumerate(leaderboard, 1):
        row["rank"] = i

    return {
        "mode": "ALPHAPILOT_OPTION_NATIVE_PHASE2_PREMIUM_SIGNAL_DISCOVERY",
        "research_only": True, "production_rules_changed": False,
        "start_date": start_date, "end_date": end_date,
        "premium_min_risk_reward": rr, "round_trip_cost_bps": round_trip_cost_bps,
        "leaderboard": leaderboard, "trades_by_model": model_trades, "errors": errors,
        "limitations": [
            "Signals in this phase are generated from the historical ATM option premium itself after 09:45; underlying strategy v2 signals are not used for entry timing.",
            "The underlying is used only to choose an ATM strike reference at 09:45 and to resolve a real listed contract.",
            "CE and PE momentum are researched separately instead of assuming symmetric rules.",
            "Relative Strength compares contemporaneous CE and PE premium momentum and buys only the stronger side.",
            "Malformed or incomplete historical candles are excluded and reported instead of crashing the whole research run.",
            "Market regime uses NIFTY 5-minute data when available; UNKNOWN is reported rather than fabricated.",
            "All entries use the next 5-minute option candle after the premium signal to avoid look-ahead.",
            "PASS/WATCH/FAIL is research-only and cannot change live AlphaPilot rules.",
        ],
    }
