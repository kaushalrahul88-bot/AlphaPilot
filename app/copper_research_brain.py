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


def build_copper_snapshot(mcx_candles, index, *, lme_candles=None, comex_candles=None, usdinr_candles=None):
    rows = clean_ohlcv(mcx_candles)
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


def label_forward_path(mcx_candles, index):
    rows = clean_ohlcv(mcx_candles)
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


def build_copper_experiences(mcx_candles, *, lme_candles=None, comex_candles=None, usdinr_candles=None, sample_every_bars=1):
    rows = clean_ohlcv(mcx_candles)
    step = max(1, int(sample_every_bars))
    experiences = []
    for i in range(50, max(50, len(rows) - max(HORIZONS)), step):
        snapshot = build_copper_snapshot(rows, i, lme_candles=lme_candles, comex_candles=comex_candles, usdinr_candles=usdinr_candles)
        experiences.append({"features": snapshot, "labels": label_forward_path(rows, i)})
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
    """Experiment 002: compare frozen Brain A vs Brain B on chronological holdout."""
    baseline = await run_copper_research_baseline(provider, days, sample_every_bars, round_trip_cost_bps)
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from .commodity_backtest import _fetch_chunked
    from .commodities import resolve_nearest_mcx_future
    ist = ZoneInfo("Asia/Kolkata")
    end = datetime.now(ist)
    start = end - timedelta(days=max(7, min(int(days), 60)))
    contract = await resolve_nearest_mcx_future("COPPER")
    mcx = await _fetch_chunked(provider, contract, 5, start, end)
    experiences = build_copper_experiences(mcx, sample_every_bars=max(1, min(int(sample_every_bars), 12)))
    comparison = compare_brains_a_b(experiences, 60, round_trip_cost_bps, 0.70)
    return {
        "mode": "ALPHAPILOT_COPPER_EXPERIMENT_002",
        "research_only": True,
        "production_rules_changed": False,
        "contract": contract,
        "coverage": baseline["coverage"],
        "comparison": comparison,
        "next_gate": "Proceed to Brain C only if Brain B passes the untouched chronological holdout gate.",
    }
