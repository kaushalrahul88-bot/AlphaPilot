from __future__ import annotations

import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from .backtest import run_backtest
from .config import Config
from .data_feed import download_aggtrades, iter_aggtrade_chunks

app = FastAPI(title="AlphaPilot BTC Trailing Flip Research Runner")
RESULTS = Path(os.getenv("BTC_RESULTS_DIR", "results/btc_trailing_flip"))
DATA = Path(os.getenv("BTC_DATA_DIR", "data/btc_trailing_flip"))
STATUS: dict[str, object] = {"state": "starting"}

def _ms(value: str, end: bool = False) -> int:
    from datetime import datetime, time
    dt = datetime.combine(date.fromisoformat(value), time.max if end else time.min, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def _run() -> None:
    try:
        start = os.getenv("BTC_BACKTEST_START", "2026-09-28")
        end = os.getenv("BTC_BACKTEST_END", "2026-09-29")
        STATUS.update(state="downloading", start=start, end=end)
        paths = download_aggtrades("BTCUSDT", date.fromisoformat(start), date.fromisoformat(end), DATA)
        STATUS.update(state="backtesting", archives=len(paths))
        cfg = Config(
            notional_usdt=float(os.getenv("BTC_NOTIONAL", "1000")),
            entry_fee_rate=float(os.getenv("BTC_ENTRY_FEE", "0.0004")),
            exit_fee_rate=float(os.getenv("BTC_EXIT_FEE", "0.0004")),
            slippage_rate=float(os.getenv("BTC_SLIPPAGE", "0.0001")),
        )
        trades = (trade for chunk in iter_aggtrade_chunks(paths, _ms(start), _ms(end, True), int(os.getenv("BTC_CHUNKSIZE", "250000"))) for trade in chunk)
        results = run_backtest(trades, cfg, _ms(start), _ms(end, True), RESULTS)
        STATUS.update(state="complete", trails=len(results), finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        STATUS.update(state="failed", error=f"{type(exc).__name__}: {exc}")

@app.on_event("startup")
def startup() -> None:
    threading.Thread(target=_run, name="btc-backtest", daemon=True).start()

@app.get("/")
def root():
    return JSONResponse(STATUS)

@app.get("/status")
def status():
    return JSONResponse(STATUS)

@app.get("/summary.csv")
def summary():
    path = RESULTS / "summary.csv"
    if not path.exists():
        return JSONResponse(STATUS, status_code=202)
    return FileResponse(path, media_type="text/csv", filename="summary.csv")
