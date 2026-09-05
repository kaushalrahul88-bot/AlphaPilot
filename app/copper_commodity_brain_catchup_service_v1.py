from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from .copper_commodity_brain_catchup_runner_v1 import run_catchup

IST = ZoneInfo("Asia/Kolkata")
START = datetime(2026, 9, 1, 0, 0, tzinfo=IST)
END = datetime(2026, 9, 4, 23, 59, 59, tzinfo=IST)


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


def _read_inputs_sync(database_url: str) -> tuple[list[list], list[dict]]:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT candle_at, open, high, low, close, volume, open_interest
                FROM commodity_candles
                WHERE provider='GROWW' AND symbol='COPPER' AND timeframe_minutes=5
                  AND trading_symbol='COPPER30SEP26FUT'
                  AND candle_at >= %s AND candle_at <= %s
                ORDER BY candle_at ASC
                """,
                (START, END),
            )
            candles = [
                [r[0].isoformat(), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                 float(r[5] or 0), float(r[6]) if r[6] is not None else None]
                for r in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT trading_symbol, groww_symbol, expiry_date, strike, option_type,
                       lot_size, sample_bucket_at, observed_at, underlying_price,
                       last_price, volume, open_interest, bid_price, ask_price, collected_at
                FROM commodity_option_snapshots
                WHERE provider='GROWW' AND underlying_symbol='COPPER'
                  AND sample_bucket_at >= %s AND sample_bucket_at <= %s
                ORDER BY sample_bucket_at ASC, trading_symbol ASC
                """,
                (START, END),
            )
            names = [d.name for d in cursor.description]
            rows = []
            for raw in cursor.fetchall():
                row = dict(zip(names, raw))
                for key in ("sample_bucket_at", "observed_at", "collected_at"):
                    if row.get(key) is not None:
                        row[key] = row[key].isoformat()
                if row.get("expiry_date") is not None:
                    row["expiry_date"] = row["expiry_date"].isoformat()
                for key in ("strike", "underlying_price", "last_price", "volume", "open_interest", "bid_price", "ask_price"):
                    if row.get(key) is not None:
                        row[key] = float(row[key])
                rows.append(row)
    return candles, rows


async def run_catchup_from_postgres(database_url: str) -> dict:
    candles, option_rows = await asyncio.to_thread(_read_inputs_sync, database_url)
    result = run_catchup(candles=candles, option_rows=option_rows)
    result["input_audit"] = {
        "underlying_contract": "COPPER30SEP26FUT",
        "underlying_5m_rows": len(candles),
        "live_option_rows": len(option_rows),
        "database_writes": 0,
    }
    return result
