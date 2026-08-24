from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean

from .backtest import _historical, _ts
from .edge_discovery import _atr, _risk_fraction
from .fno_historical_backtest import _available_contracts, _instrument_rows, _select_expiry
from .fno_premium_replay import _historical_option_day
from .market_regime_research import classify_market_brain
from .option_native_phase2 import _clean_rows, _num

CANDIDATE_ID = "PE_RANGE_HIGH_ATR_V1"
ATR_HIGH_PCT = 5.0


def _replay_one_r(rows: list[list], entry_index: int, entry: float, cost_bps: float) -> tuple[dict, int]:
    risk = entry * _risk_fraction(entry)
    stop = max(0.05, entry - risk)
    target = entry + risk
    max_price = entry
    min_price = entry
    last_close = entry
    exit_index = entry_index
    outcome = "EOD"
    gross_r = 0.0
    exit_price = entry
    for j in range(entry_index, len(rows)):
        row = rows[j]
        when = _ts(row[0])
        if not when or when.time() > time(15, 25):
            break
        high = _num(row[2]); low = _num(row[3]); close = _num(row[4])
        if high is None or low is None:
            continue
        if close is not None:
            last_close = close
        max_price = max(max_price, high); min_price = min(min_price, low); exit_index = j
        if low <= stop:
            outcome = "SL"; gross_r = -1.0; exit_price = stop
            break
        if high >= target:
            outcome = "T1"; gross_r = 1.0; exit_price = target
            break
    else:
        exit_index = len(rows) - 1
    if outcome == "EOD":
        exit_price = last_close
        gross_r = (exit_price - entry) / risk if risk > 0 else 0.0
        gross_r = max(-1.0, min(1.0, gross_r))
    cost_r = (entry * max(cost_bps, 0.0) / 10000.0) / risk if risk > 0 else 0.0
    net_r = gross_r - cost_r
    return ({
        "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2), "exit": round(exit_price, 2),
        "outcome": outcome, "gross_r": round(gross_r, 3), "cost_r": round(cost_r, 4), "net_r": round(net_r, 3),
        "mfe_r": round(max(0.0, (max_price-entry)/risk), 3) if risk > 0 else 0.0,
        "mae_r": round(max(0.0, (entry-min_price)/risk), 3) if risk > 0 else 0.0,
    }, exit_index)


def _stats(trades: list[dict]) -> dict:
    rs = [float(t["net_r"]) for t in trades]
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r < 0]
    equity = 0.0; peak = 0.0; max_dd = 0.0; streak = 0; max_streak = 0
    for r in rs:
        equity += r; peak = max(peak, equity); max_dd = max(max_dd, peak-equity)
        if r < 0: streak += 1; max_streak = max(max_streak, streak)
        else: streak = 0
    gross_profit = sum(wins); gross_loss = abs(sum(losses))
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins)/len(trades)*100, 1) if trades else 0.0,
        "avg_r": round(mean(rs), 3) if rs else 0.0, "total_r": round(sum(rs), 3),
        "profit_factor": round(gross_profit/gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        "max_drawdown_r": round(max_dd, 3), "max_consecutive_losses": max_streak,
        "avg_mfe_r": round(mean(float(t["mfe_r"]) for t in trades), 3) if trades else 0.0,
        "avg_mae_r": round(mean(float(t["mae_r"]) for t in trades), 3) if trades else 0.0,
        "t1": sum(t["outcome"]=="T1" for t in trades), "sl": sum(t["outcome"]=="SL" for t in trades), "eod": sum(t["outcome"]=="EOD" for t in trades),
    }


def _breakdown(trades: list[dict], key: str) -> list[dict]:
    groups = defaultdict(list)
    for t in trades: groups[str(t.get(key, "UNKNOWN"))].append(t)
    return [{key:k, **_stats(v)} for k,v in sorted(groups.items())]


async def run_candidate_validator(provider, symbols: list[str], start_date: str, end_date: str, round_trip_cost_bps: float = 10.0, sample_every_bars: int = 3, max_trades: int = 250):
    start = datetime.fromisoformat(start_date); end = datetime.fromisoformat(end_date) + timedelta(hours=23, minutes=59)
    if end < start: raise ValueError("end_date must be on or after start_date")
    if (end-start).days > 14: raise ValueError("Candidate Validator v1 is limited to 14 calendar days per run")
    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    step = max(1, min(int(sample_every_bars), 12)); cap = max(10, min(int(max_trades), 500))
    master = await _instrument_rows(symbols); trades=[]; baseline=[]; errors=[]
    try: nifty = _clean_rows(await _historical(provider, "NIFTY", "5m", start, end))
    except Exception as exc:
        nifty=[]; errors.append({"symbol":"NIFTY","stage":"MARKET_CONTEXT","error":f"{exc.__class__.__name__}: {exc}"})
    for symbol in symbols:
        if len(trades) >= cap: break
        try: underlying = _clean_rows(await _historical(provider, symbol, "5m", start, end))
        except Exception as exc:
            errors.append({"symbol":symbol,"stage":"UNDERLYING","error":f"{exc.__class__.__name__}: {exc}"}); continue
        days=sorted({_ts(r[0]).date().isoformat() for r in underlying if _ts(r[0]) and start.date() <= _ts(r[0]).date() <= end.date()})
        for day in days:
            if len(trades) >= cap: break
            day_under=[r for r in underlying if (w:=_ts(r[0])) and w.date().isoformat()==day]
            ref=next((r for r in day_under if (w:=_ts(r[0])) and w.time()>=time(9,45)),None); spot=_num(ref[4]) if ref else None
            if not spot: continue
            try:
                expiry,dte=_select_expiry(master,symbol,"PE",datetime.fromisoformat(day).date(),None)
                if expiry is None: raise ValueError("No listed PE expiry")
                expiry_s=expiry.isoformat(); available=[x for x in _available_contracts(master,symbol,expiry_s,"PE") if _num(x.get("strike")) is not None]
                selected=min(available,key=lambda x:abs(float(x["strike"])-spot)); rows=_clean_rows(await _historical_option_day(provider,selected,day,"5minute"))
                if len(rows)<20: raise ValueError(f"Insufficient premium candles ({len(rows)})")
            except Exception as exc:
                errors.append({"symbol":symbol,"date":day,"stage":"CONTRACT_OR_CANDLES","error":f"{exc.__class__.__name__}: {exc}"}); continue
            i=12
            while i < len(rows)-1:
                when=_ts(rows[i][0])
                if not when or not(time(9,45)<=when.time()<=time(14,30)): i += step; continue
                entry_index=i+1; entry=_num(rows[entry_index][1])
                if not entry or entry<=0: i += step; continue
                atr=_atr(rows,i); close=_num(rows[i][4]); atr_pct=(atr/close*100.0) if atr is not None and close and close>0 else None
                brain=classify_market_brain(underlying,nifty,when); regime=(brain or {}).get("final_regime","UNKNOWN")
                base_replay,base_exit=_replay_one_r(rows,entry_index,entry,round_trip_cost_bps)
                baseline.append({"symbol":symbol,"date":day,"signal_at":when.isoformat(),"regime":regime,"atr_pct":round(atr_pct,3) if atr_pct is not None else None,**base_replay})
                qualifies = regime=="RANGE" and atr_pct is not None and atr_pct>=ATR_HIGH_PCT
                if qualifies and len(trades)<cap:
                    trades.append({"symbol":symbol,"date":day,"signal_at":when.isoformat(),"entry_at":_ts(rows[entry_index][0]).isoformat() if _ts(rows[entry_index][0]) else None,
                        "option_type":"PE","contract":selected.get("trading_symbol") or selected.get("groww_symbol"),"expiry":expiry_s,"expiry_dte":dte,
                        "strike":_num(selected.get("strike")),"regime":regime,"atr_pct":round(atr_pct,3),**base_replay})
                    i=max(i+step,base_exit+1)
                else: i += step
    summary=_stats(trades); baseline_summary=_stats(baseline)
    status="PASS" if summary["trades"]>=50 and summary["avg_r"]>0 and summary["profit_factor"]>1.0 else "WATCH" if summary["trades"]>=25 and summary["avg_r"]>0 else "FAIL" if summary["trades"]>=25 else "INSUFFICIENT_SAMPLE"
    return {
        "mode":"ALPHAPILOT_CANDIDATE_VALIDATOR_V1","research_only":True,"production_rules_changed":False,
        "candidate":{"id":CANDIDATE_ID,"option_type":"PE","market_regime":"RANGE","premium_atr_bucket":"HIGH","premium_atr_pct_min":ATR_HIGH_PCT,"target_r":1.0,
            "entry":"next 5-minute candle open","stop":"same frozen premium-risk rule as discovery","extra_filters":[]},
        "start_date":start_date,"end_date":end_date,"symbols":symbols,"round_trip_cost_bps":round_trip_cost_bps,"sample_every_bars":step,
        "summary":summary,"baseline":baseline_summary,"status":status,"trades":trades,"by_symbol":_breakdown(trades,"symbol"),"by_date":_breakdown(trades,"date"),"errors":errors,
        "validation_note":"This candidate was discovered on prior development data. Use only untouched symbols/dates here; do not retune candidate rules from this result."
    }
