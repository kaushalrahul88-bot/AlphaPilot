from __future__ import annotations

from datetime import datetime, time, timedelta

from .backtest import _historical, _ts
from .candidate_validator import _breakdown, _replay_one_r, _stats
from .edge_discovery import _atr
from .fno_historical_backtest import _available_contracts, _instrument_rows, _select_expiry
from .fno_premium_replay import _historical_option_day
from .market_regime_research import classify_market_brain
from .option_native_phase2 import _clean_rows, _num

CANDIDATE_ID = "PE_ATR_NORMAL_PERSIST_TREND_LONG_V1"
ATR_NORMAL_MIN_PCT = 2.0
ATR_NORMAL_MAX_PCT = 5.0


def _is_normal_atr(value: float | None) -> bool:
    return value is not None and ATR_NORMAL_MIN_PCT <= value < ATR_NORMAL_MAX_PCT


async def run_candidate_b_validator(provider, symbols: list[str], start_date: str, end_date: str, round_trip_cost_bps: float = 10.0, sample_every_bars: int = 3, max_trades: int = 250):
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date) + timedelta(hours=23, minutes=59)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 14:
        raise ValueError("Candidate B Validator is limited to 14 calendar days per run")

    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    step = max(1, min(int(sample_every_bars), 12))
    cap = max(10, min(int(max_trades), 500))
    master = await _instrument_rows(symbols)
    trades: list[dict] = []
    baseline: list[dict] = []
    errors: list[dict] = []

    try:
        nifty = _clean_rows(await _historical(provider, "NIFTY", "5m", start, end))
    except Exception as exc:
        nifty = []
        errors.append({"symbol": "NIFTY", "stage": "MARKET_CONTEXT", "error": f"{exc.__class__.__name__}: {exc}"})

    for symbol in symbols:
        if len(trades) >= cap:
            break
        try:
            underlying = _clean_rows(await _historical(provider, symbol, "5m", start, end))
        except Exception as exc:
            errors.append({"symbol": symbol, "stage": "UNDERLYING", "error": f"{exc.__class__.__name__}: {exc}"})
            continue

        days = sorted({_ts(r[0]).date().isoformat() for r in underlying if _ts(r[0]) and start.date() <= _ts(r[0]).date() <= end.date()})
        for day in days:
            if len(trades) >= cap:
                break
            day_under = [r for r in underlying if (w := _ts(r[0])) and w.date().isoformat() == day]
            ref = next((r for r in day_under if (w := _ts(r[0])) and w.time() >= time(9, 45)), None)
            spot = _num(ref[4]) if ref else None
            if not spot:
                continue

            try:
                expiry, dte = _select_expiry(master, symbol, "PE", datetime.fromisoformat(day).date(), None)
                if expiry is None:
                    raise ValueError("No listed PE expiry")
                expiry_s = expiry.isoformat()
                available = [x for x in _available_contracts(master, symbol, expiry_s, "PE") if _num(x.get("strike")) is not None]
                selected = min(available, key=lambda x: abs(float(x["strike"]) - spot))
                rows = _clean_rows(await _historical_option_day(provider, selected, day, "5minute"))
                if len(rows) < 20:
                    raise ValueError(f"Insufficient premium candles ({len(rows)})")
            except Exception as exc:
                errors.append({"symbol": symbol, "date": day, "stage": "CONTRACT_OR_CANDLES", "error": f"{exc.__class__.__name__}: {exc}"})
                continue

            previous_sample_atr_pct: float | None = None
            i = 12
            while i < len(rows) - 1:
                when = _ts(rows[i][0])
                if not when or not (time(9, 45) <= when.time() <= time(14, 30)):
                    i += step
                    continue

                entry_index = i + 1
                entry = _num(rows[entry_index][1])
                if not entry or entry <= 0:
                    i += step
                    continue

                atr = _atr(rows, i)
                close = _num(rows[i][4])
                atr_pct = (atr / close * 100.0) if atr is not None and close and close > 0 else None
                brain = classify_market_brain(underlying, nifty, when)
                regime = (brain or {}).get("final_regime", "UNKNOWN")

                base_replay, _ = _replay_one_r(rows, entry_index, entry, round_trip_cost_bps)
                baseline.append({
                    "symbol": symbol,
                    "date": day,
                    "signal_at": when.isoformat(),
                    "option_type": "PE",
                    "regime": regime,
                    "previous_sample_atr_pct": round(previous_sample_atr_pct, 3) if previous_sample_atr_pct is not None else None,
                    "atr_pct": round(atr_pct, 3) if atr_pct is not None else None,
                    **base_replay,
                })

                qualifies = _is_normal_atr(previous_sample_atr_pct) and _is_normal_atr(atr_pct) and regime == "TREND_LONG"
                current_atr_pct = atr_pct
                if qualifies and len(trades) < cap:
                    replay, exit_index = _replay_one_r(rows, entry_index, entry, round_trip_cost_bps)
                    trades.append({
                        "symbol": symbol,
                        "date": day,
                        "signal_at": when.isoformat(),
                        "entry_at": _ts(rows[entry_index][0]).isoformat() if _ts(rows[entry_index][0]) else None,
                        "option_type": "PE",
                        "contract": selected.get("trading_symbol") or selected.get("groww_symbol"),
                        "expiry": expiry_s,
                        "expiry_dte": dte,
                        "strike": _num(selected.get("strike")),
                        "regime": regime,
                        "previous_sample_atr_pct": round(previous_sample_atr_pct, 3) if previous_sample_atr_pct is not None else None,
                        "atr_pct": round(atr_pct, 3) if atr_pct is not None else None,
                        **replay,
                    })
                    previous_sample_atr_pct = current_atr_pct
                    i = max(i + step, exit_index + 1)
                else:
                    previous_sample_atr_pct = current_atr_pct
                    i += step

    summary = _stats(trades)
    baseline_summary = _stats(baseline)
    status = (
        "PASS" if summary["trades"] >= 50 and summary["avg_r"] > 0 and summary["profit_factor"] > 1.0
        else "WATCH" if summary["trades"] >= 25 and summary["avg_r"] > 0
        else "FAIL" if summary["trades"] >= 25
        else "INSUFFICIENT_SAMPLE"
    )
    return {
        "mode": "ALPHAPILOT_CANDIDATE_B_VALIDATOR_V1",
        "research_only": True,
        "production_rules_changed": False,
        "candidate": {
            "id": CANDIDATE_ID,
            "option_type": "PE",
            "previous_sample_premium_atr_bucket": "NORMAL",
            "current_sample_premium_atr_bucket": "NORMAL",
            "premium_atr_pct_min_inclusive": ATR_NORMAL_MIN_PCT,
            "premium_atr_pct_max_exclusive": ATR_NORMAL_MAX_PCT,
            "current_market_regime": "TREND_LONG",
            "sample_every_bars": step,
            "target_r": 1.0,
            "entry": "next 5-minute candle open",
            "stop": "same frozen premium-risk rule as discovery",
            "extra_filters": [],
        },
        "start_date": start_date,
        "end_date": end_date,
        "symbols": symbols,
        "round_trip_cost_bps": round_trip_cost_bps,
        "sample_every_bars": step,
        "summary": summary,
        "baseline": baseline_summary,
        "status": status,
        "trades": trades,
        "by_symbol": _breakdown(trades, "symbol"),
        "by_date": _breakdown(trades, "date"),
        "errors": errors,
        "validation_note": "Candidate B was frozen from Edge Discovery Lab v3 development data. Do not alter its ATR bucket, option type, regime, entry, stop, target, costs or sampling from OOS results.",
    }
