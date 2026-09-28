from __future__ import annotations
import csv
import hashlib
import io
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

BASE_URL = "https://data.binance.vision/data/futures/um"

@dataclass(frozen=True)
class Trade:
    timestamp_ms: int
    price: float
    quantity: float = 0.0

def _months(start: date, end: date) -> Iterator[tuple[int, int]]:
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1

def _days(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

def _download(url: str, target: Path, retries: int = 4) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AlphaPilot-BTC-TrailingFlip/1.4"})
            with urllib.request.urlopen(req, timeout=60) as src, part.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            part.replace(target)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if part.exists():
                part.unlink()
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed: {url}") from last

def _verify_checksum(path: Path, checksum_path: Path) -> None:
    expected = checksum_path.read_text().split()[0].lower()
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    actual = h.hexdigest().lower()
    if actual != expected:
        raise ValueError(f"checksum mismatch for {path.name}: expected {expected}, got {actual}")

def download_aggtrades(symbol: str, start: date, end: date, data_dir: Path, verify_checksum: bool = True) -> list[Path]:
    paths: list[Path] = []
    monthly_dir = data_dir / "monthly"
    daily_dir = data_dir / "daily"
    for y, m in _months(start, end):
        stem = f"{symbol}-aggTrades-{y:04d}-{m:02d}"
        path = monthly_dir / f"{stem}.zip"
        url = f"{BASE_URL}/monthly/aggTrades/{symbol}/{path.name}"
        try:
            if not path.exists():
                _download(url, path)
            if verify_checksum:
                chk = path.with_name(path.name + ".CHECKSUM")
                if not chk.exists():
                    _download(url + ".CHECKSUM", chk)
                _verify_checksum(path, chk)
            paths.append(path)
        except Exception:
            if path.exists():
                path.unlink()
            first = max(start, date(y, m, 1))
            last_day = date(y + (m == 12), 1 if m == 12 else m + 1, 1) - timedelta(days=1)
            for d in _days(max(first, start), min(last_day, end)):
                stem = f"{symbol}-aggTrades-{d:%Y-%m-%d}"
                dp = daily_dir / f"{stem}.zip"
                du = f"{BASE_URL}/daily/aggTrades/{symbol}/{dp.name}"
                if not dp.exists():
                    _download(du, dp)
                if verify_checksum:
                    dc = dp.with_name(dp.name + ".CHECKSUM")
                    if not dc.exists():
                        _download(du + ".CHECKSUM", dc)
                    _verify_checksum(dp, dc)
                paths.append(dp)
    return paths

def _parse_row(row: list[str]) -> Trade | None:
    if not row or row[0].lower() in {"agg_trade_id", "agg_trade_id"}:
        return None
    try:
        if len(row) < 7:
            return None
        return Trade(int(row[5]), float(row[1]), float(row[2]))
    except (ValueError, TypeError):
        return None

def _iter_zip(path: Path, start_ms: int, end_ms: int) -> Iterator[Trade]:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            return
        with z.open(names[0], "r") as raw:
            wrapper = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.reader(wrapper)
            for row in reader:
                trade = _parse_row(row)
                if trade and start_ms <= trade.timestamp_ms <= end_ms:
                    yield trade

def iter_aggtrade_chunks(paths: list[Path], start_ms: int, end_ms: int, chunksize: int = 250_000) -> Iterator[list[Trade]]:
    batch: list[Trade] = []
    for path in sorted(paths):
        for trade in _iter_zip(path, start_ms, end_ms):
            batch.append(trade)
            if len(batch) >= chunksize:
                yield batch
                batch = []
    if batch:
        yield batch

def count_aggtrades(paths: list[Path], start_ms: int, end_ms: int) -> int:
    return sum(len(chunk) for chunk in iter_aggtrade_chunks(paths, start_ms, end_ms, 250_000))

def load_aggtrades(paths: list[Path], start_ms: int, end_ms: int):
    return iter_aggtrade_chunks(paths, start_ms, end_ms, 250_000)
