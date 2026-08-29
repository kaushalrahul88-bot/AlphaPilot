from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean

HORIZONS = (1, 3, 6, 12, 24)  # 5/15/30/60/120 minutes on 5m bars


def _f(value, default=None):
    try:
        number = float(value)
        return number if isfinite(number) else default
    except (TypeError, ValueError):
        return default


def clean_ohlcv(candles):
    out = []
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        ts, o, h, l, c = row[:5]
        volume = _f(row[5], 0.0) if len(row) > 5 else 0.0
        oi = _f(row[6]) if len(row) > 6 else None
        o, h, l, c = map(_f, (o, h, l, c))
        if None in (o, h, l, c) or min(o, h, l, c) <= 0 or h < l:
            continue
        out.append([ts, o, h, l, c, max(volume or 0.0, 0.0), oi])
    return out


def _ema(values, period):
    if not values:
        return None
    k = 2.0 / (period + 1.0)
    value = values[0]
    for x in values[1:]:
        value = x * k + value * (1.0 - k)
    return value


def _atr(rows, end, period=14):
    start = max(1, end - period + 1)
    tr = []
    for i in range(start, end + 1):
        h, l, prev = rows[i][2], rows[i][3], rows[i - 1][4]
        tr.append(max(h - l, abs(h - prev), abs(l - prev)))
    return mean(tr) if tr else None


def _relative_volume(rows, end, period=20):
    history = [r[5] for r in rows[max(0, end - period):end] if r[5] > 0]
    if not history:
        return None
    base = mean(history)
    return rows[end][5] / base if base > 0 else None


def _structure(rows, end, lookback=20):
    sample = rows[max(0, end - lookback + 1):end + 1]
    if len(sample) < 8:
        return "UNKNOWN"
    half = len(sample) // 2
    a, b = sample[:half], sample[half:]
    ah, al = max(r[2] for r in a), min(r[3] for r in a)
    bh, bl = max(r[2] for r in b), min(r[3] for r in b)
    if bh > ah and bl > al:
        return "UPTREND"
    if bh < ah and bl < al:
        return "DOWNTREND"
    return "RANGE"


def _series_return(rows, end, bars):
    if end < bars or rows[end - bars][4] <= 0:
        return None
    return (rows[end][4] / rows[end - bars][4] - 1.0) * 100.0


def _aligned_return(context_rows, timestamp, bars=3):
    """Return context momentum using the latest context bar available at or before timestamp."""
    if not context_rows:
        return None
    from .commodity_time import parse_ist_timestamp

    clean = clean_ohlcv(context_rows)
    target = parse_ist_timestamp(timestamp)
    eligible = []
    for i, row in enumerate(clean):
        try:
            stamp = parse_ist_timestamp(row[0])
        except (TypeError, ValueError, OverflowError):
            continue
        if stamp <= target:
            eligible.append(i)
        else:
            break
    if not eligible:
        return None
    i = eligible[-1]
    return _series_return(clean, i, bars)


def _build_copper_snapshot_clean(rows, index, *, lme_candles=None, comex_candles=None, usdinr_candles=None):
    if index < 50 or index >= len(rows):
        raise ValueError("Copper snapshot requires at least 50 completed MCX 5m bars")
    close = rows[index][4]
    closes = [r[4] for r in rows[:index + 1]]
    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
    atr = _atr(rows, index)
    oi_now = rows[index][6]
    oi_prev = rows[index - 3][6] if index >= 3 else None
    return {
        "timestamp": str(rows[index][0]),
        "price": close,
        "structure": _structure(rows, index),
        "return_15m_pct": _series_return(rows, index, 3),
        "return_60m_pct": _series_return(rows, index, 12),
        "ema20_gap_pct": (close / ema20 - 1.0) * 100.0 if ema20 else None,
        "ema50_gap_pct": (close / ema50 - 1.0) * 100.0 if ema50 else None,
        "atr_pct": atr / close * 100.0 if atr and close else None,
        "relative_volume": _relative_volume(rows, index),
        "oi_change_15m_pct": ((oi_now / oi_prev - 1.0) * 100.0) if oi_now is not None and oi_prev not in (None, 0) else None,
        "lme_return_15m_pct": _aligned_return(lme_candles, rows[index][0]),
        "comex_return_15m_pct": _aligned_return(comex_candles, rows[index][0]),
        "usdinr_return_15m_pct": _aligned_return(usdinr_candles, rows[index][0]),
    }


def build_copper_snapshot(mcx_candles, index, *, lme_candles=None, comex_candles=None, usdinr_candles=None):
    return _build_copper_snapshot_clean(
        clean_ohlcv(mcx_candles), index,
        lme_candles=lme_candles, comex_candles=comex_candles, usdinr_candles=usdinr_candles,
    )


def _label_forward_path_clean(rows, index):
    if index < 0 or index >= len(rows):
        raise IndexError(index)
    entry = rows[index][4]
    labels = {}
    for bars in HORIZONS:
        end = min(len(rows) - 1, index + bars)
        sample = rows[index + 1:end + 1]
        if not sample:
            labels[f"forward_{bars * 5}m_pct"] = None
            labels[f"mfe_{bars * 5}m_pct"] = None
            labels[f"mae_{bars * 5}m_pct"] = None
            continue
        labels[f"forward_{bars * 5}m_pct"] = (sample[-1][4] / entry - 1.0) * 100.0
        labels[f"mfe_{bars * 5}m_pct"] = (max(r[2] for r in sample) / entry - 1.0) * 100.0
        labels[f"mae_{bars * 5}m_pct"] = (min(r[3] for r in sample) / entry - 1.0) * 100.0
    return labels


def label_forward_path(mcx_candles, index):
    return _label_forward_path_clean(clean_ohlcv(mcx_candles), index)


def build_copper_experiences(mcx_candles, *, lme_candles=None, comex_candles=None, usdinr_candles=None, sample_every_bars=1):
    rows = clean_ohlcv(mcx_candles)
    step = max(1, int(sample_every_bars))
    experiences = []
    for i in range(50, max(50, len(rows) - max(HORIZONS)), step):
        snapshot = _build_copper_snapshot_clean(
            rows, i,
            lme_candles=lme_candles, comex_candles=comex_candles, usdinr_candles=usdinr_candles,
        )
        experiences.append({"features": snapshot, "labels": _label_forward_path_clean(rows, i)})
    return experiences


def experiment_manifest():
    return {
        "mode": "ALPHAPILOT_COPPER_RESEARCH_BRAIN_V1",
        "research_only": True,
        "production_rules_changed": False,
        "bar_interval": "5m",
        "brains": {
            "A": ["mcx_price", "technical_baseline"],
            "B": ["mcx_price", "structure", "volume", "open_interest"],
            "C": ["brain_b", "lme_copper", "comex_copper", "usdinr"],
            "D": ["brain_c", "macro_event_context"],
        },
        "promotion_order": ["discovery", "candidate", "validated", "forward_test", "live_eligible"],
        "guardrails": [
            "No live strategy may self-modify from research output.",
            "Features use information available at the observation timestamp only.",
            "Forward labels are never inputs to the same observation.",
            "Chronological out-of-sample validation is required before promotion.",
            "Complexity must outperform the simpler baseline after realistic costs.",
        ],
    }


def brain_a_signal(features):
    """Frozen technical-only Copper baseline. No OI/global context is allowed."""
    structure = str(features.get("structure") or "UNKNOWN")
    ret15 = _f(features.get("return_15m_pct"), 0.0)
    ema20_gap = _f(features.get("ema20_gap_pct"), 0.0)
    ema50_gap = _f(features.get("ema50_gap_pct"), 0.0)
    if structure == "UPTREND" and ret15 > 0 and ema20_gap > 0 and ema50_gap > 0:
        return "BUY"
    if structure == "DOWNTREND" and ret15 < 0 and ema20_gap < 0 and ema50_gap < 0:
        return "SELL"
    return "NO_TRADE"


def evaluate_brain_a(experiences, horizon_minutes=60, round_trip_cost_bps=4.0):
    """Measure the frozen technical baseline on already-built experiences."""
    key = f"forward_{int(horizon_minutes)}m_pct"
    decisions = []
    for item in experiences:
        features = item.get("features") or {}
        labels = item.get("labels") or {}
        signal = brain_a_signal(features)
        forward = _f(labels.get(key))
        price = _f(features.get("price"))
        if signal == "NO_TRADE" or forward is None or not price:
            continue
        signed_return = forward if signal == "BUY" else -forward
        cost_pct = max(0.0, float(round_trip_cost_bps)) / 100.0
        net_pct = signed_return - cost_pct
        decisions.append({"timestamp": features.get("timestamp"), "signal": signal, "gross_pct": signed_return, "net_pct": net_pct})

    wins = [x for x in decisions if x["net_pct"] > 0]
    losses = [x for x in decisions if x["net_pct"] < 0]
    net = [x["net_pct"] for x in decisions]
    gross_profit = sum(x for x in net if x > 0)
    gross_loss = abs(sum(x for x in net if x < 0))
    return {
        "brain": "A",
        "name": "MCX_TECHNICAL_BASELINE",
        "research_only": True,
        "horizon_minutes": int(horizon_minutes),
        "round_trip_cost_bps": float(round_trip_cost_bps),
        "signals": len(decisions),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(decisions) * 100.0, 2) if decisions else 0.0,
        "avg_net_return_pct": round(mean(net), 4) if net else 0.0,
        "net_return_sum_pct": round(sum(net), 4),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "decisions": decisions[-200:],
        "rules_frozen": {
            "buy": "UPTREND + positive 15m return + price above EMA20 and EMA50",
            "sell": "DOWNTREND + negative 15m return + price below EMA20 and EMA50",
        },
    }


async def run_copper_research_baseline(provider, days=30, sample_every_bars=3, round_trip_cost_bps=4.0):
    """Run Copper Experiment 001 Brain A on the nearest active MCX contract."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from .commodity_backtest import _fetch_chunked
    from .commodity_benchmarks import fetch_benchmark_candles
    from .commodities import resolve_nearest_mcx_future

    days = max(7, min(int(days), 60))
    step = max(1, min(int(sample_every_bars), 12))
    ist = ZoneInfo("Asia/Kolkata")
    end = datetime.now(ist)
    start = end - timedelta(days=days)
    contract = await resolve_nearest_mcx_future("COPPER")
    mcx = await _fetch_chunked(provider, contract, 5, start, end)
    if len(mcx) < 80:
        raise RuntimeError(f"Insufficient MCX Copper 5m history ({len(mcx)} candles)")

    try:
        comex_result = await fetch_benchmark_candles("COPPER", start, end)
        comex = comex_result.get("candles", [])
        comex_status = comex_result.get("status", "UNAVAILABLE")
    except Exception as exc:
        comex = []
        comex_status = f"UNAVAILABLE: {exc.__class__.__name__}"

    experiences = build_copper_experiences(
        mcx,
        comex_candles=comex,
        sample_every_bars=step,
    )
    brain_a = evaluate_brain_a(
        experiences,
        horizon_minutes=60,
        round_trip_cost_bps=round_trip_cost_bps,
    )
    return {
        "mode": "ALPHAPILOT_COPPER_EXPERIMENT_001",
        "research_only": True,
        "production_rules_changed": False,
        "contract": contract,
        "requested_days": days,
        "sample_every_bars": step,
        "coverage": {
            "mcx_5m_candles": len(mcx),
            "experiences": len(experiences),
            "start": str(mcx[0][0]) if mcx else None,
            "end": str(mcx[-1][0]) if mcx else None,
            "comex_status": comex_status,
            "comex_5m_candles": len(comex),
        },
        "brain_a": brain_a,
        "next_gate": {
            "required": "Brain B must beat frozen Brain A on chronological untouched data after costs.",
            "brain_a_frozen": True,
            "brain_b_enabled": False,
            "brain_c_enabled": False,
            "brain_d_enabled": False,
        },
        "limitations": [
            "Experiment 001 uses the nearest active MCX Copper contract, not a multi-year continuous futures series.",
            "Brain A intentionally ignores COMEX, LME, FX, OI and event context.",
            "COMEX is collected only to verify context availability for later experiments; it does not affect Brain A.",
            "The first run is a research baseline, not a live trading recommendation.",
        ],
    }


BRAIN_B_CONFIG = {
    "min_relative_volume": 0.90,
    "max_atr_pct": 0.65,
    "min_abs_return_15m_pct": 0.02,
    "oi_confirmation": True,
}


def brain_b_signal(features, config=None):
    """Research-only Copper Brain B: Brain A direction gated by participation/regime."""
    cfg = dict(BRAIN_B_CONFIG)
    if config:
        cfg.update(config)
    base = brain_a_signal(features)
    if base == "NO_TRADE":
        return "NO_TRADE"
    rel_vol = _f(features.get("relative_volume"))
    atr_pct = _f(features.get("atr_pct"))
    ret15 = _f(features.get("return_15m_pct"), 0.0)
    oi_change = _f(features.get("oi_change_15m_pct"))
    if rel_vol is not None and rel_vol < cfg["min_relative_volume"]:
        return "NO_TRADE"
    if atr_pct is not None and atr_pct > cfg["max_atr_pct"]:
        return "NO_TRADE"
    if abs(ret15) < cfg["min_abs_return_15m_pct"]:
        return "NO_TRADE"
    if cfg.get("oi_confirmation") and oi_change is not None:
        if base == "BUY" and oi_change <= 0:
            return "NO_TRADE"
        if base == "SELL" and oi_change <= 0:
            return "NO_TRADE"
    return base


def _evaluate_signal(experiences, signal_fn, *, brain, name, horizon_minutes=60, round_trip_cost_bps=4.0):
    key = f"forward_{int(horizon_minutes)}m_pct"
    decisions = []
    for item in experiences:
        features = item.get("features") or {}
        labels = item.get("labels") or {}
        signal = signal_fn(features)
        forward = _f(labels.get(key))
        if signal == "NO_TRADE" or forward is None:
            continue
        signed = forward if signal == "BUY" else -forward
        net = signed - max(0.0, float(round_trip_cost_bps)) / 100.0
        decisions.append({"timestamp": features.get("timestamp"), "signal": signal, "gross_pct": signed, "net_pct": net})
    values = [x["net_pct"] for x in decisions]
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    return {
        "brain": brain, "name": name, "research_only": True,
        "horizon_minutes": int(horizon_minutes), "round_trip_cost_bps": float(round_trip_cost_bps),
        "signals": len(decisions), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(decisions) * 100.0, 2) if decisions else 0.0,
        "avg_net_return_pct": round(mean(values), 4) if values else 0.0,
        "net_return_sum_pct": round(sum(values), 4),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "decisions": decisions[-200:],
    }


def evaluate_brain_b(experiences, horizon_minutes=60, round_trip_cost_bps=4.0, config=None):
    cfg = dict(BRAIN_B_CONFIG)
    if config:
        cfg.update(config)
    report = _evaluate_signal(
        experiences, lambda f: brain_b_signal(f, cfg), brain="B",
        name="MCX_STRUCTURE_PARTICIPATION_REGIME", horizon_minutes=horizon_minutes,
        round_trip_cost_bps=round_trip_cost_bps,
    )
    report["config"] = cfg
    return report


def chronological_split(experiences, train_fraction=0.70):
    if len(experiences) < 20:
        return experiences[:], []
    cut = max(1, min(len(experiences) - 1, int(len(experiences) * float(train_fraction))))
    return experiences[:cut], experiences[cut:]


def compare_brains_a_b(experiences, horizon_minutes=60, round_trip_cost_bps=4.0, train_fraction=0.70):
    """Evaluate frozen A and frozen B on the same chronological untouched holdout."""
    train, holdout = chronological_split(experiences, train_fraction)
    a_train = evaluate_brain_a(train, horizon_minutes, round_trip_cost_bps)
    b_train = evaluate_brain_b(train, horizon_minutes, round_trip_cost_bps)
    a_test = evaluate_brain_a(holdout, horizon_minutes, round_trip_cost_bps)
    b_test = evaluate_brain_b(holdout, horizon_minutes, round_trip_cost_bps)
    a_pf = _f(a_test.get("profit_factor"), 0.0) or 0.0
    b_pf = _f(b_test.get("profit_factor"), 0.0) or 0.0
    promoted = (
        b_test["signals"] >= 20
        and b_test["avg_net_return_pct"] > a_test["avg_net_return_pct"]
        and b_pf > a_pf
        and b_test["avg_net_return_pct"] > 0
        and b_pf > 1.0
    )
    return {
        "split": {"train_fraction": float(train_fraction), "train_experiences": len(train), "holdout_experiences": len(holdout)},
        "train": {"brain_a": a_train, "brain_b": b_train},
        "holdout": {"brain_a": a_test, "brain_b": b_test},
        "gate": {
            "brain_b_promoted": promoted,
            "requirements": ["holdout signals >= 20", "holdout expectancy > Brain A", "holdout profit factor > Brain A", "positive holdout expectancy", "holdout profit factor > 1.0"],
        },
    }


async def run_copper_brain_b_experiment(provider, days=30, sample_every_bars=3, round_trip_cost_bps=4.0):
    """Experiment 002: compare frozen Brain A vs Brain B on one shared historical fetch."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from .commodity_backtest import _fetch_chunked
    from .commodities import resolve_nearest_mcx_future

    days = max(7, min(int(days), 60))
    step = max(1, min(int(sample_every_bars), 12))
    ist = ZoneInfo("Asia/Kolkata")
    end = datetime.now(ist)
    start = end - timedelta(days=days)
    contract = await resolve_nearest_mcx_future("COPPER")
    mcx = await _fetch_chunked(provider, contract, 5, start, end)
    if len(mcx) < 80:
        raise RuntimeError(f"Insufficient MCX Copper 5m history ({len(mcx)} candles)")
    experiences = build_copper_experiences(mcx, sample_every_bars=step)
    comparison = compare_brains_a_b(experiences, 60, round_trip_cost_bps, 0.70)
    return {
        "mode": "ALPHAPILOT_COPPER_EXPERIMENT_002",
        "research_only": True,
        "production_rules_changed": False,
        "contract": contract,
        "coverage": {
            "mcx_5m_candles": len(mcx),
            "experiences": len(experiences),
            "start": str(mcx[0][0]) if mcx else None,
            "end": str(mcx[-1][0]) if mcx else None,
            "failed_chunks": failed_chunks,
            "partial_coverage": bool(failed_chunks),
        },
        "comparison": comparison,
        "next_gate": "Proceed to Brain C only if Brain B passes the untouched chronological holdout gate.",
        "limitations": [
            "Experiment 002 intentionally uses MCX-only features; COMEX/LME/FX remain excluded until Brain C.",
            "Brain A and Brain B are evaluated on the same fetched candles and chronological holdout.",
        ],
    }


def _bucket(value, cuts, labels):
    x = _f(value)
    if x is None:
        return "UNKNOWN"
    for cut, label in zip(cuts, labels):
        if x < cut:
            return label
    return labels[-1]


def _hour_from_timestamp(value):
    try:
        from .commodity_time import parse_ist_timestamp
        return parse_ist_timestamp(value).hour
    except Exception:
        return None


def _segment_stats(rows):
    values = [x["net_pct"] for x in rows]
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {
        "signals": len(rows),
        "win_rate_pct": round(len(wins) / len(rows) * 100.0, 2) if rows else 0.0,
        "avg_net_return_pct": round(mean(values), 4) if values else 0.0,
        "net_return_sum_pct": round(sum(values), 4),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
    }


def attribute_brain_a_edges(experiences, horizon_minutes=60, round_trip_cost_bps=4.0):
    """Descriptive attribution only: no threshold search and no strategy mutation."""
    key = f"forward_{int(horizon_minutes)}m_pct"
    observations = []
    for item in experiences:
        f = item.get("features") or {}
        forward = _f((item.get("labels") or {}).get(key))
        signal = brain_a_signal(f)
        if signal == "NO_TRADE" or forward is None:
            continue
        gross = forward if signal == "BUY" else -forward
        net = gross - max(0.0, float(round_trip_cost_bps)) / 100.0
        hour = _hour_from_timestamp(f.get("timestamp"))
        observations.append({
            "timestamp": f.get("timestamp"),
            "signal": signal,
            "net_pct": net,
            "structure": f.get("structure") or "UNKNOWN",
            "session": (
                "MORNING" if hour is not None and hour < 12 else
                "MIDDAY" if hour is not None and hour < 16 else
                "EVENING" if hour is not None else "UNKNOWN"
            ),
            "atr_bucket": _bucket(f.get("atr_pct"), [0.10, 0.20, 0.35], ["LOW", "NORMAL", "HIGH", "EXTREME"]),
            "volume_bucket": _bucket(f.get("relative_volume"), [0.75, 1.0, 1.5], ["QUIET", "NORMAL", "ACTIVE", "SURGE"]),
            "momentum_bucket": _bucket(abs(_f(f.get("return_15m_pct"), 0.0)), [0.03, 0.08, 0.15], ["WEAK", "NORMAL", "STRONG", "EXTREME"]),
            "oi_bucket": (
                "UNKNOWN" if _f(f.get("oi_change_15m_pct")) is None else
                "RISING" if _f(f.get("oi_change_15m_pct")) > 0 else
                "FALLING" if _f(f.get("oi_change_15m_pct")) < 0 else "FLAT"
            ),
        })

    dimensions = ["signal", "structure", "session", "atr_bucket", "volume_bucket", "momentum_bucket", "oi_bucket"]
    attribution = {}
    for dim in dimensions:
        groups = {}
        for row in observations:
            groups.setdefault(row[dim], []).append(row)
        attribution[dim] = {
            name: _segment_stats(group)
            for name, group in sorted(groups.items())
        }
    return {
        "mode": "DESCRIPTIVE_EDGE_ATTRIBUTION",
        "research_only": True,
        "threshold_optimization": False,
        "observations": len(observations),
        "overall": _segment_stats(observations),
        "dimensions": attribution,
        "guardrail": "Segments describe where frozen Brain A historically won/lost; they are not trading rules.",
    }


async def run_copper_edge_attribution(provider, days=14, sample_every_bars=3, round_trip_cost_bps=4.0):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from .commodity_backtest import _fetch_chunked
    from .commodities import resolve_nearest_mcx_future

    days = max(7, min(int(days), 60))
    step = max(1, min(int(sample_every_bars), 12))
    ist = ZoneInfo("Asia/Kolkata")
    end = datetime.now(ist)
    start = end - timedelta(days=days)
    contract = await resolve_nearest_mcx_future("COPPER")
    mcx = await _fetch_chunked(provider, contract, 5, start, end)
    if len(mcx) < 80:
        raise RuntimeError(f"Insufficient MCX Copper 5m history ({len(mcx)} candles)")
    experiences = build_copper_experiences(mcx, sample_every_bars=step)
    train, holdout = chronological_split(experiences, 0.70)
    return {
        "mode": "ALPHAPILOT_COPPER_EDGE_ATTRIBUTION_V1",
        "research_only": True,
        "production_rules_changed": False,
        "contract": contract,
        "coverage": {"mcx_5m_candles": len(mcx), "experiences": len(experiences)},
        "development_attribution": attribute_brain_a_edges(train, 60, round_trip_cost_bps),
        "holdout_attribution": attribute_brain_a_edges(holdout, 60, round_trip_cost_bps),
        "interpretation_rule": "Use development segments to form hypotheses; use holdout only to check whether directionality persists. Do not tune thresholds on holdout.",
    }


def _split_windows(experiences, windows=4):
    n = len(experiences)
    windows = max(2, min(int(windows), max(2, n // 20)))
    base = n // windows
    out = []
    start = 0
    for i in range(windows):
        end = n if i == windows - 1 else start + base
        out.append(experiences[start:end])
        start = end
    return out


def _dimension_candidate_summary(window_reports, dimension):
    keys = sorted({k for report in window_reports for k in report["dimensions"].get(dimension, {}).keys()})
    summary = {}
    for key in keys:
        rows = []
        for idx, report in enumerate(window_reports):
            stats = report["dimensions"].get(dimension, {}).get(key)
            if stats:
                rows.append({
                    "window": idx + 1,
                    "signals": stats["signals"],
                    "avg_net_return_pct": stats["avg_net_return_pct"],
                    "profit_factor": stats["profit_factor"],
                })
        positive_expectancy = sum(1 for r in rows if r["avg_net_return_pct"] > 0)
        pf_over_one = sum(1 for r in rows if (r["profit_factor"] or 0) > 1)
        summary[key] = {
            "windows_present": len(rows),
            "positive_expectancy_windows": positive_expectancy,
            "pf_over_one_windows": pf_over_one,
            "consistent_positive": bool(rows) and positive_expectancy == len(rows) and pf_over_one == len(rows),
            "window_stats": rows,
        }
    return summary


def regime_stability_study(experiences, windows=4, horizon_minutes=60, round_trip_cost_bps=4.0):
    chunks = _split_windows(experiences, windows)
    reports = [attribute_brain_a_edges(chunk, horizon_minutes, round_trip_cost_bps) for chunk in chunks]
    dimensions = ["signal", "session", "atr_bucket", "volume_bucket", "momentum_bucket"]
    stability = {dim: _dimension_candidate_summary(reports, dim) for dim in dimensions}
    recurring = []
    for dim, entries in stability.items():
        for name, stats in entries.items():
            if stats["consistent_positive"] and stats["windows_present"] >= 3:
                recurring.append({"dimension": dim, "value": name, **stats})
    return {
        "mode": "COPPER_REGIME_STABILITY_STUDY",
        "research_only": True,
        "threshold_optimization": False,
        "windows": len(chunks),
        "window_sizes": [len(x) for x in chunks],
        "window_reports": reports,
        "stability": stability,
        "recurring_positive_candidates": recurring,
        "guardrail": "Recurring candidates are hypotheses only; no strategy promotion or threshold tuning occurs here.",
    }


async def run_copper_regime_stability(provider, days=45, sample_every_bars=3, round_trip_cost_bps=4.0, windows=4):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from .commodity_backtest import _fetch_chunked
    from .commodities import resolve_nearest_mcx_future

    days = max(21, min(int(days), 60))
    step = max(1, min(int(sample_every_bars), 12))
    ist = ZoneInfo("Asia/Kolkata")
    end = datetime.now(ist)
    start = end - timedelta(days=days)
    contract = await resolve_nearest_mcx_future("COPPER")
    # Long 5m requests can hit intermittent Groww upstream failures. Build the
    # study from smaller independent chunks and keep successful history rather
    # than failing the entire research request on one bad range.
    mcx = []
    failed_chunks = []
    cursor = start
    chunk_days = 2
    while cursor < end:
        chunk_end = min(end, cursor + timedelta(days=chunk_days))
        try:
            chunk = await _fetch_chunked(provider, contract, 5, cursor, chunk_end)
            mcx.extend(chunk)
        except Exception as exc:
            failed_chunks.append({
                "start": cursor.isoformat(),
                "end": chunk_end.isoformat(),
                "error": type(exc).__name__,
            })
        cursor = chunk_end + timedelta(seconds=1)
    # Do not silently accept a mostly-missing long-history study.
    successful_chunks = max(0, ((days + chunk_days - 1) // chunk_days) - len(failed_chunks))
    if successful_chunks < 4:
        raise RuntimeError(f"Too few successful Copper history chunks ({successful_chunks}; failed_chunks={len(failed_chunks)})")
    dedup = {}
    for row in mcx:
        if isinstance(row, (list, tuple)) and len(row) >= 5:
            dedup[str(row[0])] = list(row)
    mcx = sorted(dedup.values(), key=lambda row: str(row[0]))
    if len(mcx) < 300:
        raise RuntimeError(f"Insufficient MCX Copper 5m history for stability study ({len(mcx)} candles; failed_chunks={len(failed_chunks)})")
    experiences = build_copper_experiences(mcx, sample_every_bars=step)
    study = regime_stability_study(experiences, windows, 60, round_trip_cost_bps)
    return {
        "mode": "ALPHAPILOT_COPPER_REGIME_STABILITY_V1",
        "research_only": True,
        "production_rules_changed": False,
        "contract": contract,
        "coverage": {
            "requested_days": days,
            "mcx_5m_candles": len(mcx),
            "experiences": len(experiences),
            "start": str(mcx[0][0]) if mcx else None,
            "end": str(mcx[-1][0]) if mcx else None,
            "chunk_days": chunk_days,
            "failed_chunks": failed_chunks,
            "partial_coverage": bool(failed_chunks),
        },
        "study": study,
        "next_gate": "Only recurring candidates may be proposed for Brain B v2, and they still require a fresh untouched validation period.",
    }


async def run_copper_regime_stability_from_store(store, days=45, sample_every_bars=3, round_trip_cost_bps=4.0, windows=4):
    """Run Copper stability entirely from durable stored candles; no Groww fetch occurs here."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    days = max(21, min(int(days), 3650))
    step = max(1, min(int(sample_every_bars), 12))
    ist = ZoneInfo("Asia/Kolkata")
    end = datetime.now(ist)
    start = end - timedelta(days=days)
    await store.initialize()
    mcx = await store.read_symbol("COPPER", 5, start, end)
    if len(mcx) < 300:
        raise RuntimeError(f"Insufficient stored MCX Copper 5m history ({len(mcx)} candles)")
    experiences = build_copper_experiences(mcx, sample_every_bars=step)
    study = regime_stability_study(experiences, windows, 60, round_trip_cost_bps)
    return {
        "mode": "ALPHAPILOT_COPPER_REGIME_STABILITY_STORED_V1",
        "research_only": True,
        "production_rules_changed": False,
        "data_source": "POSTGRES_COMMODITY_CANDLES",
        "coverage": {
            "requested_days": days,
            "mcx_5m_candles": len(mcx),
            "experiences": len(experiences),
            "start": str(mcx[0][0]) if mcx else None,
            "end": str(mcx[-1][0]) if mcx else None,
        },
        "study": study,
        "next_gate": "Only recurring candidates may be proposed for Brain B v2, and they still require a fresh untouched validation period.",
    }
