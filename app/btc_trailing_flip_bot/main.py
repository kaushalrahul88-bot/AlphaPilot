from __future__ import annotations
import argparse
from datetime import date, datetime, time, timezone
from pathlib import Path

from .backtest import run_backtest
from .config import Config
from .data_feed import download_aggtrades, iter_aggtrade_chunks

def _ms(value: str, end: bool = False) -> int:
    d = date.fromisoformat(value)
    dt = datetime.combine(d, time.max if end else time.min, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="BTC/USDT trailing-flip research backtester")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("download", "run", "backtest"):
        s = sub.add_parser(name)
        s.add_argument("--start", required=True, help="UTC date YYYY-MM-DD")
        s.add_argument("--end", required=True, help="UTC date YYYY-MM-DD")
        s.add_argument("--data-dir", default="data")
        s.add_argument("--results-dir", default="results")
        s.add_argument("--notional", type=float, default=1000.0)
        s.add_argument("--entry-fee", type=float, default=0.0004)
        s.add_argument("--exit-fee", type=float, default=0.0004)
        s.add_argument("--slippage", type=float, default=0.0001)
        s.add_argument("--chunksize", type=int, default=250000)
    return p

def main() -> None:
    args = _parser().parse_args()
    start_ms, end_ms = _ms(args.start), _ms(args.end, True)
    data_dir, results_dir = Path(args.data_dir), Path(args.results_dir)
    if args.command in {"download", "run"}:
        paths = download_aggtrades("BTCUSDT", date.fromisoformat(args.start), date.fromisoformat(args.end), data_dir)
    else:
        paths = sorted((data_dir / "monthly").glob("BTCUSDT-aggTrades-*.zip")) + sorted((data_dir / "daily").glob("BTCUSDT-aggTrades-*.zip"))
    if args.command == "download":
        print(f"Downloaded/verified {len(paths)} archive(s)")
        return
    config = Config(notional_usdt=args.notional, entry_fee_rate=args.entry_fee, exit_fee_rate=args.exit_fee, slippage_rate=args.slippage)
    trades = (trade for chunk in iter_aggtrade_chunks(paths, start_ms, end_ms, args.chunksize) for trade in chunk)
    results = run_backtest(trades, config, start_ms, end_ms, results_dir)
    for trail, summary in results.items():
        print(f"{trail:.4%}: trades={summary['total_trades']} net={summary['net_pnl']:.4f} PF={summary['profit_factor']}")
if __name__ == "__main__":
    main()
