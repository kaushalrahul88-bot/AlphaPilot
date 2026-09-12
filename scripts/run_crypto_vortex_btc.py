"""Reproducible BTC VORTEX v1 replay runner.

Fetches public Coinbase Exchange BTC-USD candles and runs the frozen Crypto
VORTEX v1 logic. Research/backtest only; no order placement.

The original Binance COIN-M source is intentionally not used here because
GitHub-hosted runners can receive HTTP 451 from that endpoint. Strategy rules
and parameters are unchanged; only the public replay data source changed.
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

BASE = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
INTERVAL_SECONDS = {"1m": 60, "5m": 300}
MAX_CANDLES_PER_REQUEST = 300


def _dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_from_seconds(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_klines(start_ms: int, end_ms: int, interval: str) -> list[Candle]:
    if interval not in INTERVAL_SECONDS:
        raise ValueError(f"unsupported interval: {interval}")
    granularity = INTERVAL_SECONDS[interval]
    start_s = start_ms // 1000
    end_s = (end_ms + 999) // 1000
    out: list[Candle] = []
    cursor = start_s
    while cursor < end_s:
        chunk_end = min(end_s, cursor + granularity * MAX_CANDLES_PER_REQUEST)
        params = urllib.parse.urlencode({
            "granularity": granularity,
            "start": _iso_from_seconds(cursor),
            "end": _iso_from_seconds(chunk_end),
        })
        req = urllib.request.Request(
            f"{BASE}?{params}",
            headers={"User-Agent": "AlphaPilot/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            rows = json.load(response)
        if not isinstance(rows, list):
            raise RuntimeError(f"unexpected Coinbase response: {rows!r}")
        for row in rows:
            # Coinbase candles: [time, low, high, open, close, volume].
            ts_ms = int(row[0]) * 1000
            if start_ms <= ts_ms < end_ms:
                out.append(Candle(
                    ts=ts_ms,
                    open=float(row[3]),
                    high=float(row[2]),
                    low=float(row[1]),
                    close=float(row[4]),
                    volume=float(row[5]),
                ))
        if chunk_end <= cursor:
            raise RuntimeError("Coinbase pagination did not advance")
        cursor = chunk_end
        time.sleep(0.10)
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
        "pair": "BTC-USD",
        "market": "COINBASE_EXCHANGE_SPOT",
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
    p.add_argument("--interval", choices=sorted(INTERVAL_SECONDS), required=True)
    p.add_argument("--output", default="")
    args = p.parse_args()
    candles = fetch_klines(int(_dt(args.start).timestamp() * 1000), int(_dt(args.end).timestamp() * 1000), args.interval)
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
