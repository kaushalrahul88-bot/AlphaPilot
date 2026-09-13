"""Crypto VORTEX v1: auditable screenshot-derived proxy.

This is NOT claimed to reproduce ENIGMA/MMF proprietary rules. It formalizes
bias -> liquidity sweep -> displacement/structure shift -> FVG/inversion -> retrace entry.
All decisions use closed candles only. Backtests are continuity-safe: setups,
entries and exits may not span missing bars.
"""
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

Side = Literal["LONG", "SHORT"]

@dataclass(frozen=True)
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

@dataclass(frozen=True)
class VortexConfig:
    bias_lookback: int = 48
    liquidity_lookback: int = 24
    atr_period: int = 14
    displacement_atr: float = 0.80
    sweep_atr: float = 0.05
    fvg_min_atr: float = 0.05
    entry_retrace: float = 0.50
    stop_buffer_atr: float = 0.05
    target_r: float = 1.50
    max_entry_wait: int = 12
    max_hold_bars: int = 48

@dataclass(frozen=True)
class Setup:
    signal_index: int
    side: Side
    sweep_level: float
    fvg_low: float
    fvg_high: float
    entry: float
    stop: float
    target: float
    risk: float

@dataclass(frozen=True)
class Trade:
    setup: Setup
    entry_index: int
    exit_index: int
    exit_price: float
    outcome: str
    r: float


def _step_ms(c: list[Candle]) -> Optional[int]:
    diffs = [c[i].ts - c[i - 1].ts for i in range(1, min(len(c), 64)) if c[i].ts > c[i - 1].ts]
    return min(diffs) if diffs else None


def _continuous(c: list[Candle], start: int, end: int, step_ms: int) -> bool:
    return start >= 0 and end < len(c) and all(c[j].ts - c[j - 1].ts == step_ms for j in range(start + 1, end + 1))


def _atr(c: list[Candle], i: int, period: int) -> Optional[float]:
    if i < period:
        return None
    tr = []
    for j in range(i - period + 1, i + 1):
        prev = c[j - 1].close
        tr.append(max(c[j].high - c[j].low, abs(c[j].high - prev), abs(c[j].low - prev)))
    return sum(tr) / len(tr)


def _bias(c: list[Candle], i: int, lookback: int) -> Optional[Side]:
    if i < lookback:
        return None
    first = c[i - lookback].close
    last = c[i].close
    mid = sum(x.close for x in c[i - lookback:i + 1]) / (lookback + 1)
    if last > first and last > mid:
        return "LONG"
    if last < first and last < mid:
        return "SHORT"
    return None


def detect_setup(c: list[Candle], i: int, cfg: VortexConfig = VortexConfig(), step_ms: Optional[int] = None) -> Optional[Setup]:
    if i < max(cfg.bias_lookback, cfg.liquidity_lookback) + 3:
        return None
    step_ms = step_ms or _step_ms(c)
    history = max(cfg.bias_lookback + 1, cfg.liquidity_lookback + 3, cfg.atr_period + 1)
    if not step_ms or not _continuous(c, i - history, i, step_ms):
        return None
    atr = _atr(c, i, cfg.atr_period)
    if not atr or atr <= 0:
        return None
    side = _bias(c, i - 1, cfg.bias_lookback)
    if side is None:
        return None
    start = i - cfg.liquidity_lookback - 2
    pool = c[start:i - 2]
    if not pool:
        return None
    prior_high = max(x.high for x in pool)
    prior_low = min(x.low for x in pool)
    sweep, reaction, disp = c[i - 2], c[i - 1], c[i]
    body = abs(disp.close - disp.open)
    if body < cfg.displacement_atr * atr:
        return None
    if side == "LONG":
        swept = sweep.low < prior_low - cfg.sweep_atr * atr and reaction.close > prior_low
        shifted = disp.close > reaction.high and disp.close > disp.open
        gap_low, gap_high = sweep.high, disp.low
        if not (swept and shifted and gap_high - gap_low >= cfg.fvg_min_atr * atr):
            return None
        entry = gap_low + cfg.entry_retrace * (gap_high - gap_low)
        stop = min(sweep.low, prior_low) - cfg.stop_buffer_atr * atr
        risk = entry - stop
        return Setup(i, side, prior_low, gap_low, gap_high, entry, stop, entry + cfg.target_r * risk, risk) if risk > 0 else None
    swept = sweep.high > prior_high + cfg.sweep_atr * atr and reaction.close < prior_high
    shifted = disp.close < reaction.low and disp.close < disp.open
    gap_low, gap_high = disp.high, sweep.low
    if not (swept and shifted and gap_high - gap_low >= cfg.fvg_min_atr * atr):
        return None
    entry = gap_low + cfg.entry_retrace * (gap_high - gap_low)
    stop = max(sweep.high, prior_high) + cfg.stop_buffer_atr * atr
    risk = stop - entry
    return Setup(i, side, prior_high, gap_low, gap_high, entry, stop, entry - cfg.target_r * risk, risk) if risk > 0 else None


def backtest(candles: Iterable[Candle], cfg: VortexConfig = VortexConfig()) -> list[Trade]:
    c = list(candles)
    trades: list[Trade] = []
    step_ms = _step_ms(c)
    if not step_ms:
        return trades
    i = max(cfg.bias_lookback, cfg.liquidity_lookback) + 3
    while i < len(c) - 1:
        s = detect_setup(c, i, cfg, step_ms)
        if s is None:
            i += 1
            continue
        entry_i = None
        for j in range(i + 1, min(len(c), i + 1 + cfg.max_entry_wait)):
            if not _continuous(c, i, j, step_ms):
                break
            if c[j].low <= s.entry <= c[j].high:
                entry_i = j
                break
        if entry_i is None:
            i += 1
            continue
        exit_i = min(len(c) - 1, entry_i + cfg.max_hold_bars)
        exit_price, outcome = c[exit_i].close, "TIME"
        r = ((exit_price - s.entry) if s.side == "LONG" else (s.entry - exit_price)) / s.risk
        for j in range(entry_i, exit_i + 1):
            if j > entry_i and c[j].ts - c[j - 1].ts != step_ms:
                exit_i, exit_price, outcome = j - 1, c[j - 1].close, "GAP"
                r = ((exit_price - s.entry) if s.side == "LONG" else (s.entry - exit_price)) / s.risk
                break
            bar = c[j]
            # Conservative OHLC ambiguity: if stop and target are both reachable in
            # the same bar, stop is deliberately evaluated first.
            if s.side == "LONG":
                if bar.low <= s.stop:
                    exit_i, exit_price, outcome, r = j, s.stop, "SL", -1.0; break
                if bar.high >= s.target:
                    exit_i, exit_price, outcome, r = j, s.target, "TP", cfg.target_r; break
            else:
                if bar.high >= s.stop:
                    exit_i, exit_price, outcome, r = j, s.stop, "SL", -1.0; break
                if bar.low <= s.target:
                    exit_i, exit_price, outcome, r = j, s.target, "TP", cfg.target_r; break
        trades.append(Trade(s, entry_i, exit_i, exit_price, outcome, r))
        i = exit_i + 1
    return trades


def summarize(trades: Iterable[Trade]) -> dict:
    t = list(trades)
    if not t:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_r": 0.0, "avg_r": 0.0}
    wins, losses = sum(x.r > 0 for x in t), sum(x.r < 0 for x in t)
    total = sum(x.r for x in t)
    return {"trades": len(t), "wins": wins, "losses": losses, "win_rate": wins / len(t), "total_r": total, "avg_r": total / len(t)}
