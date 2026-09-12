"""Reproducible BTC VORTEX v1 replay runner.

Fetches public Binance BTCUSD perpetual continuous klines and runs the frozen
Crypto VORTEX v1 logic. Research/backtest only; no order placement.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.crypto_vortex_v1 import Candle, VortexConfig, backtest, summarize

BASE = "https://dapi.binance.com/dapi/v1/continuousKlines"
INTERVAL_MS = {"1m": 60_000, "5m": 300_000}


def _ms(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines(start_ms: int, end_ms: int, interval: str) -> list[Candle]:
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    step = INTERVAL_MS[interval]
    out: list[Candle] = []
    cursor = start_ms
    while cursor < end_ms:
        params = urllib.parse.urlencode({
            "pair": "BTCUSD",
            "contractType": "PERPETUAL",
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms - 1,
            "limit": 1500,
        })
        req = urllib.request.Request(f"{BASE}?{params}", headers={"User-Agent": "AlphaPilot/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            rows = json.load(response)
        if not rows:
            break
        for row in rows:
            ts = int(row[0])
            if start_ms <= ts < end_ms:
                out.append(Candle(ts=ts, open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]), volume=float(row[5])))
        nxt = int(rows[-1][0]) + step
        if nxt <= cursor:
            raise RuntimeError("Binance pagination did not advance")
        cursor = nxt
        if len(rows) < 1500:
            break
        time.sleep(0.08)
    unique = {c.ts: c for c in out}
    return [unique[k] for k in sorted(unique)]


def max_drawdown_r(trades) -> float:
    equity = peak = 0.0
    worst = 0.0
    for trade in trades:
        equity += trade.r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def report(candles: list[Candle], interval: str, start: str, end: str) -> dict:
    cfg = VortexConfig()  # frozen v1 parameters
    trades = backtest(candles, cfg)
    summary = summarize(trades)
    summary.update({
        "strategy": "CRYPTO_VORTEX_V1",
        "pair": "BTCUSD",
        "market": "BINANCE_COIN_M_PERPETUAL_CONTINUOUS",
        "interval": interval,
        "start": start,
        "end_exclusive": end,
        "candles": len(candles),
        "target_r": cfg.target_r,
        "max_drawdown_r": max_drawdown_r(trades),
        "long_trades": sum(t.setup.side == "LONG" for t in trades),
        "short_trades": sum(t.setup.side == "SHORT" for t in trades),
        "tp": sum(t.outcome == "TP" for t in trades),
        "sl": sum(t.outcome == "SL" for t in trades),
        "time_exit": sum(t.outcome == "TIME" for t in trades),
    })
    return {"summary": summary, "trades": [{
        "signal_ts": candles[t.setup.signal_index].ts,
        "entry_ts": candles[t.entry_index].ts,
        "exit_ts": candles[t.exit_index].ts,
        "side": t.setup.side,
        "entry": t.setup.entry,
        "stop": t.setup.stop,
        "target": t.setup.target,
        "outcome": t.outcome,
        "r": t.r,
    } for t in trades]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="UTC ISO timestamp, inclusive")
    p.add_argument("--end", required=True, help="UTC ISO timestamp, exclusive")
    p.add_argument("--interval", choices=sorted(INTERVAL_MS), required=True)
    p.add_argument("--output", default="")
    args = p.parse_args()
    candles = fetch_klines(_ms(args.start), _ms(args.end), args.interval)
    if len(candles) < 100:
        raise RuntimeError(f"insufficient candles: {len(candles)}")
    result = report(candles, args.interval, args.start, args.end)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
