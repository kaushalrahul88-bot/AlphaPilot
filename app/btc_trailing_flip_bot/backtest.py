from __future__ import annotations
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import Config
from .data_feed import Trade
from .execution import entry_fill, exit_fill, fee
from .indicators import EMA
from .position import Position
from .strategy import TrailState, initial_side

@dataclass
class TradeRecord:
    trail_percent: float
    entry_timestamp: str
    exit_timestamp: str
    side: str
    entry_price: float
    exit_price: float
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    slippage: float
    funding: float
    net_pnl: float
    holding_seconds: float
    exit_reason: str

def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

def _gross(side: str, qty: float, entry: float, exit_: float) -> float:
    return qty * (exit_ - entry) if side == "LONG" else qty * (entry - exit_)

def _write_ledger(path: Path, rows: Iterable[TradeRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(TradeRecord.__annotations__.keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: getattr(row, k) for k in fields})

def _summary(rows: list[TradeRecord], open_position: bool) -> dict:
    wins = [r for r in rows if r.net_pnl > 0]
    losses = [r for r in rows if r.net_pnl < 0]
    gross = sum(r.gross_pnl for r in rows)
    entry_fees = sum(r.entry_fee for r in rows)
    exit_fees = sum(r.exit_fee for r in rows)
    slippage = sum(r.slippage for r in rows)
    funding = sum(r.funding for r in rows)
    net = sum(r.net_pnl for r in rows)
    curve = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_streak = 0
    for r in rows:
        curve += r.net_pnl
        peak = max(peak, curve)
        max_dd = max(max_dd, peak - curve)
        streak = streak + 1 if r.net_pnl < 0 else 0
        max_streak = max(max_streak, streak)
    profit_factor = sum(r.net_pnl for r in wins) / abs(sum(r.net_pnl for r in losses)) if losses else float("inf")
    long_rows = [r for r in rows if r.side == "LONG"]
    short_rows = [r for r in rows if r.side == "SHORT"]
    return {
        "total_trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(rows) if rows else 0.0,
        "gross_pnl": gross,
        "entry_fees": entry_fees,
        "exit_fees": exit_fees,
        "total_fees": entry_fees + exit_fees,
        "slippage": slippage,
        "funding": funding,
        "net_pnl": net,
        "average_trade": net / len(rows) if rows else 0.0,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd,
        "long_trades": len(long_rows),
        "short_trades": len(short_rows),
        "reversals": len(rows),
        "max_consecutive_losses": max_streak,
        "average_holding_seconds": sum(r.holding_seconds for r in rows) / len(rows) if rows else 0.0,
        "long_net_pnl": sum(r.net_pnl for r in long_rows),
        "short_net_pnl": sum(r.net_pnl for r in short_rows),
        "open_position_at_end": open_position,
    }

def run_backtest(trades: Iterable[Trade], config: Config, start_ms: int, end_ms: int, results_dir: Path) -> dict[float, dict]:
    config.validate()
    trades = iter(trades)
    emas9, emas21 = EMA(9), EMA(21)
    states: dict[float, TrailState | None] = {t: None for t in config.trail_percentages}
    positions: dict[float, Position | None] = {t: None for t in config.trail_percentages}
    rows: dict[float, list[TradeRecord]] = {t: [] for t in config.trail_percentages}
    prev_bucket: int | None = None
    completed_ema: tuple[float, float] | None = None
    bucket_close = 0.0
    for trade in trades:
        bucket = trade.timestamp_ms // 300_000
        if prev_bucket is None:
            prev_bucket = bucket
        elif bucket != prev_bucket:
            e9 = emas9.update(bucket_close)
            e21 = emas21.update(bucket_close)
            completed_ema = (e9, e21) if emas21.ready else None
            prev_bucket = bucket
        bucket_close = trade.price
        if completed_ema and all(states[t] is None for t in states):
            side = initial_side(*completed_ema)
            if side:
                for trail in states:
                    ep = entry_fill(side, trade.price, config.slippage_rate)
                    qty = config.notional_usdt / ep
                    ef = fee(ep, qty, config.entry_fee_rate)
                    positions[trail] = Position(side, datetime.fromtimestamp(trade.timestamp_ms / 1000, tz=timezone.utc), ep, trade.price, trade.price, config.notional_usdt, ef)
                    states[trail] = TrailState(side, trade.price, trail)
        for trail, state in states.items():
            pos = positions[trail]
            if state is None or pos is None:
                continue
            _, hit = state.update(trade.price)
            if not hit:
                continue
            xp = exit_fill(pos.side, trade.price, config.slippage_rate)
            gross = _gross(pos.side, pos.quantity, pos.market_entry_price, trade.price)
            xf = fee(xp, pos.quantity, config.exit_fee_rate)
            slip = abs(pos.entry_price - pos.market_entry_price) * pos.quantity + abs(xp - trade.price) * pos.quantity
            net = gross - pos.entry_fee - xf - slip - pos.funding
            entry_ms = int(pos.entry_time.timestamp() * 1000)
            rows[trail].append(TradeRecord(trail, _iso(entry_ms), _iso(trade.timestamp_ms), pos.side, pos.entry_price, xp, gross, pos.entry_fee, xf, slip, pos.funding, net, (trade.timestamp_ms - entry_ms) / 1000.0, "TRAILING_STOP"))
            new_side = "SHORT" if pos.side == "LONG" else "LONG"
            ep = entry_fill(new_side, trade.price, config.slippage_rate)
            qty = config.notional_usdt / ep
            ef = fee(ep, qty, config.entry_fee_rate)
            positions[trail] = Position(new_side, datetime.fromtimestamp(trade.timestamp_ms / 1000, tz=timezone.utc), ep, trade.price, trade.price, config.notional_usdt, ef)
            states[trail] = TrailState(new_side, trade.price, trail)
    results: dict[float, dict] = {}
    results_dir.mkdir(parents=True, exist_ok=True)
    for trail, trail_rows in rows.items():
        pct = f"{trail:.4%}".replace("%", "pct")
        _write_ledger(results_dir / f"trades_{pct}.csv", trail_rows)
        results[trail] = _summary(trail_rows, positions[trail] is not None)
    with (results_dir / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["trail_percent"] + list(next(iter(results.values())).keys()) if results else ["trail_percent"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for trail, summary in results.items():
            w.writerow({"trail_percent": trail, **summary})
    return results
