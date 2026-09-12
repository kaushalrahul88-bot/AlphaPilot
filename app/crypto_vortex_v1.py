"""Crypto VORTEX v1: an auditable interpretation of the screenshot supplied by the user.

This is NOT claimed to reproduce ENIGMA/MMF proprietary rules. It formalizes:
bias -> liquidity sweep -> displacement/structure shift -> FVG/inversion -> retrace entry.
All decisions use candles closed at or before the decision bar; no future data is used for signals.
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


def detect_setup(c: list[Candle], i: int, cfg: VortexConfig = VortexConfig()) -> Optional[Setup]:
    # i is the just-closed displacement/FVG confirmation candle.
    if i < max(cfg.bias_lookback, cfg.liquidity_lookback) + 3:
        return None
    atr = _atr(c, i, cfg.atr_period)
    if not atr or atr <= 0:
        return None
    side = _bias(c, i - 1, cfg.bias_lookback)
    if side is None:
        return None

    # Liquidity pool excludes the last three candles so the sweep and reaction are observable.
    start = i - cfg.liquidity_lookback - 2
    pool = c[start:i - 2]
    if not pool:
        return None
    prior_high = max(x.high for x in pool)
    prior_low = min(x.low for x in pool)
    sweep = c[i - 2]
    reaction = c[i - 1]
    disp = c[i]

    body = abs(disp.close - disp.open)
    if body < cfg.displacement_atr * atr:
        return None

    if side == "LONG":
        swept = sweep.low < prior_low - cfg.sweep_atr * atr and reaction.close > prior_low
        shifted = disp.close > reaction.high and disp.close > disp.open
        # Three-candle bullish fair-value gap: current low above candle i-2 high.
        gap_low, gap_high = sweep.high, disp.low
        has_fvg = gap_high - gap_low >= cfg.fvg_min_atr * atr
        if not (swept and shifted and has_fvg):
            return None
        entry = gap_low + cfg.entry_retrace * (gap_high - gap_low)
        stop = min(sweep.low, prior_low) - cfg.stop_buffer_atr * atr
        risk = entry - stop
        if risk <= 0:
            return None
        return Setup(i, side, prior_low, gap_low, gap_high, entry, stop, entry + cfg.target_r * risk, risk)

    swept = sweep.high > prior_high + cfg.sweep_atr * atr and reaction.close < prior_high
    shifted = disp.close < reaction.low and disp.close < disp.open
    # Three-candle bearish FVG: current high below candle i-2 low.
    gap_low, gap_high = disp.high, sweep.low
    has_fvg = gap_high - gap_low >= cfg.fvg_min_atr * atr
    if not (swept and shifted and has_fvg):
        return None
    entry = gap_low + cfg.entry_retrace * (gap_high - gap_low)
    stop = max(sweep.high, prior_high) + cfg.stop_buffer_atr * atr
    risk = stop - entry
    if risk <= 0:
        return None
    return Setup(i, side, prior_high, gap_low, gap_high, entry, stop, entry - cfg.target_r * risk, risk)


def backtest(candles: Iterable[Candle], cfg: VortexConfig = VortexConfig()) -> list[Trade]:
    c = list(candles)
    trades: list[Trade] = []
    i = max(cfg.bias_lookback, cfg.liquidity_lookback) + 3
    while i < len(c) - 1:
        s = detect_setup(c, i, cfg)
        if s is None:
            i += 1
            continue
        entry_i = None
        for j in range(i + 1, min(len(c), i + 1 + cfg.max_entry_wait)):
            if c[j].low <= s.entry <= c[j].high:
                entry_i = j
                break
        if entry_i is None:
            i += 1
            continue

        exit_i = min(len(c) - 1, entry_i + cfg.max_hold_bars)
        exit_price = c[exit_i].close
        outcome = "TIME"
        r = ((exit_price - s.entry) if s.side == "LONG" else (s.entry - exit_price)) / s.risk
        for j in range(entry_i, exit_i + 1):
            bar = c[j]
            # Conservative same-bar ambiguity: stop is assumed first.
            if s.side == "LONG":
                if bar.low <= s.stop:
                    exit_i, exit_price, outcome, r = j, s.stop, "SL", -1.0
                    break
                if bar.high >= s.target:
                    exit_i, exit_price, outcome, r = j, s.target, "TP", cfg.target_r
                    break
            else:
                if bar.high >= s.stop:
                    exit_i, exit_price, outcome, r = j, s.stop, "SL", -1.0
                    break
                if bar.low <= s.target:
                    exit_i, exit_price, outcome, r = j, s.target, "TP", cfg.target_r
                    break
        trades.append(Trade(s, entry_i, exit_i, exit_price, outcome, r))
        i = exit_i + 1
    return trades


def summarize(trades: Iterable[Trade]) -> dict:
    t = list(trades)
    if not t:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_r": 0.0, "avg_r": 0.0}
    wins = sum(x.r > 0 for x in t)
    losses = sum(x.r < 0 for x in t)
    total = sum(x.r for x in t)
    return {"trades": len(t), "wins": wins, "losses": losses, "win_rate": wins / len(t), "total_r": total, "avg_r": total / len(t)}
