from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean

from .backtest import _historical, _ts
from .fno_historical_backtest import _available_contracts, _instrument_rows, _select_expiry
from .fno_premium_replay import _historical_option_day
from .market_regime_research import classify_market_brain
from .option_native_phase2 import _clean_rows, _ema, _num, _premium_features, _volume_ratio

TARGETS = (0.5, 1.0, 1.5)


def _atr(rows: list[list], end_index: int, period: int = 14) -> float | None:
    start = max(1, end_index - period + 1)
    trs = []
    for i in range(start, end_index + 1):
        try:
            high = float(rows[i][2]); low = float(rows[i][3]); prev = float(rows[i - 1][4])
        except (TypeError, ValueError, IndexError):
            continue
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    return mean(trs) if trs else None


def _risk_fraction(entry: float) -> float:
    if entry < 5: return 0.30
    if entry < 10: return 0.25
    if entry < 30: return 0.25
    return 0.20


def _forward_labels(rows: list[list], entry_index: int, entry: float, cost_bps: float) -> dict:
    risk = entry * _risk_fraction(entry)
    stop = max(0.05, entry - risk)
    targets = {r: entry + risk * r for r in TARGETS}
    hit = {r: False for r in TARGETS}
    stopped = False
    max_price = entry; min_price = entry
    for row in rows[entry_index:]:
        when = _ts(row[0])
        if not when or when.time() > time(15, 25):
            break
        high = _num(row[2]); low = _num(row[3])
        if high is None or low is None:
            continue
        max_price = max(max_price, high); min_price = min(min_price, low)
        # Conservative same-candle ordering: stop wins when both stop and target are touched.
        if low <= stop:
            stopped = True
            break
        for r, target in targets.items():
            if high >= target:
                hit[r] = True
    cost_r = (entry * max(cost_bps, 0.0) / 10000.0) / risk if risk > 0 else 0.0
    return {
        "hit_0_5r_before_stop": hit[0.5],
        "hit_1_0r_before_stop": hit[1.0],
        "hit_1_5r_before_stop": hit[1.5],
        "stopped_before_1_5r": stopped,
        "mfe_r": round(max(0.0, (max_price - entry) / risk), 3) if risk > 0 else 0.0,
        "mae_r": round(max(0.0, (entry - min_price) / risk), 3) if risk > 0 else 0.0,
        "cost_r": round(cost_r, 4),
    }


def _snapshot(rows: list[list], i: int) -> dict:
    f = _premium_features(rows[: i + 1])
    close = float(rows[i][4])
    ema5 = f["ema5"][-1]; ema12 = f["ema12"][-1]; vwap = f["vwap"][-1]
    ret3 = close / float(rows[i - 3][4]) - 1.0 if i >= 3 and float(rows[i - 3][4]) > 0 else 0.0
    ret6 = close / float(rows[i - 6][4]) - 1.0 if i >= 6 and float(rows[i - 6][4]) > 0 else 0.0
    atr = _atr(rows, i)
    vr = _volume_ratio(rows, i)
    recent = [float(r[4]) for r in rows[max(0, i - 5): i + 1]]
    compression = ((max(recent) - min(recent)) / close * 100.0) if recent and close > 0 else None
    return {
        "premium": round(close, 2),
        "premium_return_3bar_pct": round(ret3 * 100.0, 3),
        "premium_return_6bar_pct": round(ret6 * 100.0, 3),
        "premium_ema5_gap_pct": round((close / ema5 - 1.0) * 100.0, 3) if ema5 else None,
        "premium_ema12_gap_pct": round((close / ema12 - 1.0) * 100.0, 3) if ema12 else None,
        "premium_vwap_gap_pct": round((close / vwap - 1.0) * 100.0, 3) if vwap else None,
        "premium_atr_pct": round(atr / close * 100.0, 3) if atr is not None and close > 0 else None,
        "premium_volume_ratio": round(vr, 3) if vr is not None else None,
        "premium_6bar_range_pct": round(compression, 3) if compression is not None else None,
    }


def _bucket(value, cuts: tuple[float, float], labels: tuple[str, str, str]) -> str:
    if value is None: return "UNKNOWN"
    if value < cuts[0]: return labels[0]
    if value < cuts[1]: return labels[1]
    return labels[2]


def _feature_report(rows: list[dict], key: str, bucket_fn) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[bucket_fn(row.get(key))].append(row)
    out = []
    for bucket, sample in groups.items():
        n = len(sample)
        out.append({
            "bucket": bucket,
            "observations": n,
            "hit_0_5r_pct": round(sum(bool(x["labels"]["hit_0_5r_before_stop"]) for x in sample) / n * 100.0, 1),
            "hit_1_0r_pct": round(sum(bool(x["labels"]["hit_1_0r_before_stop"]) for x in sample) / n * 100.0, 1),
            "hit_1_5r_pct": round(sum(bool(x["labels"]["hit_1_5r_before_stop"]) for x in sample) / n * 100.0, 1),
            "avg_mfe_r": round(mean(float(x["labels"]["mfe_r"]) for x in sample), 3),
            "avg_mae_r": round(mean(float(x["labels"]["mae_r"]) for x in sample), 3),
        })
    return sorted(out, key=lambda x: (x["hit_1_0r_pct"], x["observations"]), reverse=True)


async def run_edge_discovery(provider, symbols: list[str], start_date: str, end_date: str, max_observations: int = 600, round_trip_cost_bps: float = 10.0, sample_every_bars: int = 3):
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date) + timedelta(hours=23, minutes=59)
    if end < start: raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 14: raise ValueError("Edge Discovery Lab v1 is limited to 14 calendar days per run")
    cap = max(30, min(int(max_observations), 1500)); step = max(1, min(int(sample_every_bars), 12))
    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
    master = await _instrument_rows(symbols)
    observations = []; errors = []
    try: nifty = _clean_rows(await _historical(provider, "NIFTY", "5m", start, end))
    except Exception as exc:
        nifty = []; errors.append({"symbol":"NIFTY","stage":"MARKET_CONTEXT","error":f"{exc.__class__.__name__}: {exc}"})

    for symbol in symbols:
        if len(observations) >= cap: break
        try: underlying = _clean_rows(await _historical(provider, symbol, "5m", start, end))
        except Exception as exc:
            errors.append({"symbol":symbol,"stage":"UNDERLYING","error":f"{exc.__class__.__name__}: {exc}"}); continue
        days = sorted({_ts(r[0]).date().isoformat() for r in underlying if _ts(r[0]) and start.date() <= _ts(r[0]).date() <= end.date()})
        for day in days:
            if len(observations) >= cap: break
            day_underlying = [r for r in underlying if (w := _ts(r[0])) and w.date().isoformat() == day]
            ref = next((r for r in day_underlying if (w := _ts(r[0])) and w.time() >= time(9,45)), None)
            spot = _num(ref[4]) if ref else None
            if not spot: continue
            candle_sets = {}; contracts = {}
            for option_type in ("CE","PE"):
                try:
                    expiry, dte = _select_expiry(master, symbol, option_type, datetime.fromisoformat(day).date(), None)
                    if expiry is None: raise ValueError("No listed expiry")
                    expiry_s = expiry.isoformat()
                    available = [x for x in _available_contracts(master, symbol, expiry_s, option_type) if _num(x.get("strike")) is not None]
                    selected = min(available, key=lambda x: abs(float(x["strike"]) - spot))
                    candles = _clean_rows(await _historical_option_day(provider, selected, day, "5minute"))
                    if len(candles) < 20: raise ValueError(f"Insufficient premium candles ({len(candles)})")
                    candle_sets[option_type] = candles; contracts[option_type] = (selected, expiry_s, dte)
                except Exception as exc:
                    errors.append({"symbol":symbol,"date":day,"option_type":option_type,"stage":"CONTRACT_OR_CANDLES","error":f"{exc.__class__.__name__}: {exc}"})
            for option_type, rows in candle_sets.items():
                selected, expiry_s, dte = contracts[option_type]
                for i in range(12, len(rows) - 1, step):
                    if len(observations) >= cap: break
                    when = _ts(rows[i][0])
                    if not when or not (time(9,45) <= when.time() <= time(14,30)): continue
                    entry_index = i + 1; entry = _num(rows[entry_index][1])
                    if not entry or entry <= 0: continue
                    brain = classify_market_brain(underlying, nifty, when)
                    snap = _snapshot(rows, i)
                    opposite = candle_sets.get("PE" if option_type == "CE" else "CE")
                    relative_edge = None
                    if opposite:
                        opp_map = {_ts(r[0]): j for j,r in enumerate(opposite) if _ts(r[0])}
                        oi = opp_map.get(when)
                        if oi is not None and oi >= 3:
                            own_prev = float(rows[i-3][4]); opp_prev = float(opposite[oi-3][4])
                            if own_prev > 0 and opp_prev > 0:
                                own_ret = float(rows[i][4]) / own_prev - 1.0; opp_ret = float(opposite[oi][4]) / opp_prev - 1.0
                                relative_edge = round((own_ret - opp_ret) * 100.0, 3)
                    observations.append({
                        "symbol":symbol,"date":day,"observed_at":when.isoformat(),"option_type":option_type,
                        "contract":selected.get("trading_symbol") or selected.get("groww_symbol"),"expiry":expiry_s,"expiry_dte":dte,
                        "strike":_num(selected.get("strike")),"underlying_reference":round(spot,2),
                        **snap,"ce_pe_relative_edge_pct":relative_edge,"market_brain":brain,
                        "labels":_forward_labels(rows, entry_index, entry, round_trip_cost_bps),
                    })

    reports = {
        "option_type": _feature_report(observations, "option_type", lambda v: str(v)),
        "market_regime": _feature_report(observations, "market_brain", lambda v: (v or {}).get("final_regime","UNKNOWN")),
        "premium_return_3bar": _feature_report(observations, "premium_return_3bar_pct", lambda v: _bucket(v, (-1.0, 3.0), ("WEAK","NEUTRAL","STRONG"))),
        "premium_vwap_gap": _feature_report(observations, "premium_vwap_gap_pct", lambda v: _bucket(v, (-1.0, 1.0), ("BELOW","NEAR","ABOVE"))),
        "premium_volume_ratio": _feature_report(observations, "premium_volume_ratio", lambda v: _bucket(v, (0.8, 1.3), ("WEAK","NORMAL","EXPANDING"))),
        "premium_atr_pct": _feature_report(observations, "premium_atr_pct", lambda v: _bucket(v, (2.0, 5.0), ("LOW","NORMAL","HIGH"))),
        "ce_pe_relative_edge": _feature_report(observations, "ce_pe_relative_edge_pct", lambda v: _bucket(v, (-2.0, 2.0), ("LAGGING","NEUTRAL","LEADING"))),
    }
    n = len(observations)
    baseline = {"observations":n}
    for label in ("hit_0_5r_before_stop","hit_1_0r_before_stop","hit_1_5r_before_stop"):
        baseline[label + "_pct"] = round(sum(bool(x["labels"][label]) for x in observations) / n * 100.0, 1) if n else 0.0
    return {
        "mode":"ALPHAPILOT_EDGE_DISCOVERY_LAB_V1","research_only":True,"production_rules_changed":False,
        "start_date":start_date,"end_date":end_date,"symbols":symbols,"round_trip_cost_bps":round_trip_cost_bps,
        "sample_every_bars":step,"baseline":baseline,"feature_reports":reports,"observations":observations,"errors":errors,
        "limitations":[
            "V1 is descriptive discovery, not a strategy optimizer or live signal generator.",
            "ATM contracts are frozen from the 09:45 underlying reference for each symbol-day.",
            "Each observation uses only information available at that timestamp; outcome labels look forward only after the observation.",
            "Same-candle stop/target ambiguity is resolved conservatively in favor of the stop.",
            "Feature buckets are fixed diagnostics, not fitted thresholds.",
            "Discovery findings must be frozen and tested on untouched dates/symbols before strategy construction.",
        ],
    }
