from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, time as dt_time
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from .commodity_option_history import fetch_mcx_option_master, ranked_mcx_option_contracts
from .commodities import commodity_quote, mcx_session_status
from .options_only_policy import assert_option_contract, mark_underlying_reference, options_only_policy


IST = ZoneInfo("Asia/Kolkata")
PROVIDER = "GROWW"
TIMEFRAME_MINUTES = 5
DEFAULT_STRIKES_PER_TYPE = 10
MIN_VALID_SNAPSHOTS_PER_TYPE = 3
MAX_UNDERLYING_AGE_MINUTES = 45
MASTER_CACHE_SECONDS = 30 * 60

_master_cache: dict = {"loaded_at": 0.0, "rows": None}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS commodity_option_snapshots (
    provider TEXT NOT NULL,
    underlying_symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    segment TEXT NOT NULL,
    trading_symbol TEXT NOT NULL,
    groww_symbol TEXT NOT NULL,
    expiry_date DATE NOT NULL,
    strike NUMERIC NOT NULL,
    option_type TEXT NOT NULL,
    lot_size INTEGER,
    sample_bucket_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    underlying_price NUMERIC,
    last_price NUMERIC NOT NULL,
    volume NUMERIC,
    open_interest NUMERIC,
    bid_price NUMERIC,
    ask_price NUMERIC,
    raw_payload TEXT,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, trading_symbol, sample_bucket_at)
);
ALTER TABLE commodity_option_snapshots
    ADD COLUMN IF NOT EXISTS underlying_price NUMERIC;
CREATE INDEX IF NOT EXISTS commodity_option_snapshots_underlying_time_idx
    ON commodity_option_snapshots (underlying_symbol, sample_bucket_at DESC);
CREATE INDEX IF NOT EXISTS commodity_option_snapshots_contract_idx
    ON commodity_option_snapshots (
        underlying_symbol, expiry_date, option_type, strike, sample_bucket_at DESC
    );
"""

UPSERT_SQL = """
INSERT INTO commodity_option_snapshots (
    provider, underlying_symbol, exchange, segment, trading_symbol, groww_symbol,
    expiry_date, strike, option_type, lot_size, sample_bucket_at, observed_at,
    underlying_price, last_price, volume, open_interest, bid_price, ask_price, raw_payload, collected_at
) VALUES (
    %(provider)s, %(underlying_symbol)s, %(exchange)s, %(segment)s,
    %(trading_symbol)s, %(groww_symbol)s, %(expiry_date)s, %(strike)s,
    %(option_type)s, %(lot_size)s, %(sample_bucket_at)s, %(observed_at)s,
    %(underlying_price)s, %(last_price)s, %(volume)s, %(open_interest)s, %(bid_price)s,
    %(ask_price)s, %(raw_payload)s, %(collected_at)s
)
ON CONFLICT (provider, trading_symbol, sample_bucket_at)
DO UPDATE SET
    observed_at = EXCLUDED.observed_at,
    underlying_price = EXCLUDED.underlying_price,
    last_price = EXCLUDED.last_price,
    volume = EXCLUDED.volume,
    open_interest = EXCLUDED.open_interest,
    bid_price = EXCLUDED.bid_price,
    ask_price = EXCLUDED.ask_price,
    raw_payload = EXCLUDED.raw_payload,
    lot_size = EXCLUDED.lot_size,
    collected_at = EXCLUDED.collected_at;
"""


class OptionSnapshotStore(Protocol):
    async def initialize(self) -> None: ...
    async def upsert(self, records: list[dict]) -> int: ...
    async def status(self, underlying_symbol: str = "COPPER") -> dict: ...


class PostgresOptionSnapshotStore:
    """Durable forward-only live option-premium snapshot memory."""

    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for option snapshot collection")

    def _connect(self):
        import psycopg
        return psycopg.connect(self.database_url, connect_timeout=10)

    def _initialize_sync(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA_SQL)

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    def _upsert_sync(self, records):
        if not records:
            return 0
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(UPSERT_SQL, records)
        return len(records)

    async def upsert(self, records):
        return await asyncio.to_thread(self._upsert_sync, records)

    def _status_sync(self, underlying_symbol):
        sql = """
            SELECT
                option_type,
                COUNT(DISTINCT trading_symbol),
                COUNT(*),
                MIN(sample_bucket_at),
                MAX(sample_bucket_at),
                COUNT(DISTINCT sample_bucket_at::date)
            FROM commodity_option_snapshots
            WHERE provider = %s AND underlying_symbol = %s
            GROUP BY option_type
            ORDER BY option_type
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (PROVIDER, str(underlying_symbol).upper()))
                rows = cursor.fetchall()
        return {
            "enabled": True,
            "underlying_symbol": str(underlying_symbol).upper(),
            "data_type": "LIVE_5M_LTP_SNAPSHOTS_NOT_OHLC",
            "series": [
                {
                    "option_type": option_type,
                    "contracts": contracts,
                    "snapshots": snapshots,
                    "first_at": first_at.isoformat() if first_at else None,
                    "last_at": last_at.isoformat() if last_at else None,
                    "trading_days": trading_days,
                }
                for option_type, contracts, snapshots, first_at, last_at, trading_days in rows
            ],
        }

    async def status(self, underlying_symbol="COPPER"):
        return await asyncio.to_thread(self._status_sync, underlying_symbol)


def _timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _bucket_5m(value):
    stamp = _timestamp(value)
    return stamp.replace(minute=(stamp.minute // 5) * 5, second=0, microsecond=0)


def _number(value):
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _decimal(value):
    number = _number(value)
    return Decimal(str(number)) if number is not None else None


def _nested_first(mapping, paths):
    for path in paths:
        current = mapping
        for key in path:
            if isinstance(current, dict):
                if key not in current:
                    current = None
                    break
                current = current.get(key)
            elif isinstance(current, list):
                try:
                    current = current[int(key)]
                except (ValueError, IndexError, TypeError):
                    current = None
                    break
            else:
                current = None
                break
        number = _number(current)
        if number is not None:
            return number
    return None


def _normalize_quote_body(body):
    if not isinstance(body, dict):
        raise RuntimeError("Groww option quote response is not a JSON object")
    status = str(body.get("status") or "").upper()
    if status and status != "SUCCESS":
        raise RuntimeError(f"Groww option quote status is {status}")
    payload = body.get("payload", body)
    if not isinstance(payload, dict):
        raise RuntimeError("Groww option quote payload is missing")

    last_price = _nested_first(payload, [
        ("last_price",), ("ltp",), ("last_traded_price",),
    ])
    if last_price is None or last_price <= 0:
        raise RuntimeError("Groww option quote has no positive last price")

    bid_price = _nested_first(payload, [
        ("bid_price",), ("best_bid_price",),
        ("market_depth", "buy", "0", "price"),
    ])
    ask_price = _nested_first(payload, [
        ("ask_price",), ("best_ask_price",),
        ("market_depth", "sell", "0", "price"),
    ])
    volume = _nested_first(payload, [
        ("volume",), ("day_volume",), ("total_traded_volume",),
    ])
    open_interest = _nested_first(payload, [
        ("open_interest",), ("oi",),
    ])
    return {
        "last_price": last_price,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "volume": volume,
        "open_interest": open_interest,
        "payload": payload,
    }


async def fetch_mcx_option_live_quote(provider, contract):
    throttle = getattr(provider, "_throttle", None)
    if callable(throttle):
        await throttle()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{provider.BASE_URL}/v1/live-data/quote",
            headers=await provider._headers(),
            params={
                "exchange": contract.get("exchange") or "MCX",
                "segment": contract.get("segment") or "COMMODITY",
                "trading_symbol": contract["trading_symbol"],
            },
        )
    response.raise_for_status()
    return _normalize_quote_body(response.json())


async def _current_master():
    now = time.monotonic()
    rows = _master_cache.get("rows")
    if rows is not None and now - float(_master_cache.get("loaded_at") or 0) < MASTER_CACHE_SECONDS:
        return rows
    rows = await fetch_mcx_option_master(["COPPER"])
    _master_cache["loaded_at"] = now
    _master_cache["rows"] = rows
    return rows


def _session_window(day):
    return (
        datetime.combine(day, dt_time(9, 0), tzinfo=IST),
        datetime.combine(day, dt_time(23, 30), tzinfo=IST),
    )


def _snapshot_record(contract, quote, observed_at, underlying_price):
    return {
        "provider": PROVIDER,
        "underlying_symbol": "COPPER",
        "exchange": contract.get("exchange") or "MCX",
        "segment": contract.get("segment") or "COMMODITY",
        "trading_symbol": str(contract.get("trading_symbol") or ""),
        "groww_symbol": str(contract.get("groww_symbol") or ""),
        "expiry_date": contract.get("expiry"),
        "strike": _decimal(contract.get("strike")),
        "option_type": str(contract.get("option_type") or "").upper(),
        "lot_size": contract.get("lot_size"),
        "sample_bucket_at": _bucket_5m(observed_at),
        "observed_at": observed_at,
        "underlying_price": _decimal(underlying_price),
        "last_price": _decimal(quote.get("last_price")),
        "volume": _decimal(quote.get("volume")),
        "open_interest": _decimal(quote.get("open_interest")),
        "bid_price": _decimal(quote.get("bid_price")),
        "ask_price": _decimal(quote.get("ask_price")),
        "raw_payload": json.dumps(quote.get("payload") or {}, separators=(",", ":"), default=str),
        "collected_at": observed_at,
    }


async def collect_copper_option_snapshots(
    provider,
    underlying_store,
    snapshot_store: OptionSnapshotStore,
    now: datetime | None = None,
    strikes_per_type: int = DEFAULT_STRIKES_PER_TYPE,
):
    observed_at = _timestamp(now or datetime.now(IST))
    session_state = mcx_session_status(observed_at)
    if not session_state.get("is_open"):
        return {
            "status": "MARKET_CLOSED",
            "research_only": True,
            "production_rules_changed": False,
            "live_execution_enabled": False,
            "observed_at": observed_at.isoformat(),
            "snapshots": 0,
        }

    strike_count = max(1, min(int(strikes_per_type), 20))
    await underlying_store.initialize()
    await snapshot_store.initialize()

    start_at, end_at = _session_window(observed_at.date())
    rows = await underlying_store.read_symbol(
        "COPPER", TIMEFRAME_MINUTES, start_at, min(end_at, observed_at),
    )
    usable = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = _timestamp(row[0])
            close = float(row[4])
        except (TypeError, ValueError, OverflowError):
            continue
        if stamp.date() == observed_at.date() and stamp <= observed_at and close > 0:
            usable.append((stamp, row))

    latest_stamp = None
    underlying_candle_close = None
    underlying_age_minutes = None
    if usable:
        latest_stamp, latest_row = max(usable, key=lambda item: item[0])
        underlying_candle_close = float(latest_row[4])
        underlying_age_minutes = (observed_at - latest_stamp).total_seconds() / 60.0
        candle_health = {
            "status": (
                "FRESH"
                if underlying_age_minutes <= MAX_UNDERLYING_AGE_MINUTES
                else "STALE"
            ),
            "pass": underlying_age_minutes <= MAX_UNDERLYING_AGE_MINUTES,
            "latest_at": latest_stamp.isoformat(),
            "age_minutes": round(underlying_age_minutes, 2),
            "close": underlying_candle_close,
            "max_age_minutes": MAX_UNDERLYING_AGE_MINUTES,
        }
    else:
        candle_health = {
            "status": "MISSING",
            "pass": False,
            "latest_at": None,
            "age_minutes": None,
            "close": None,
            "max_age_minutes": MAX_UNDERLYING_AGE_MINUTES,
        }

    try:
        live_underlying = await commodity_quote(provider, "COPPER")
        underlying_price = _number(
            (live_underlying.get("validation") or {}).get("last_price")
        )
        if underlying_price is None or underlying_price <= 0:
            raise RuntimeError("live Copper future quote has no positive last_price")
        underlying_contract = (
            (live_underlying.get("contract") or {}).get("trading_symbol")
            or None
        )
    except Exception as exc:
        return {
            "status": "UNDERLYING_LIVE_QUOTE_ERROR",
            "research_only": True,
            "production_rules_changed": False,
            "live_execution_enabled": False,
            "observed_at": observed_at.isoformat(),
            "underlying_candle_health": candle_health,
            "snapshots": 0,
            "error": f"{exc.__class__.__name__}: {str(exc)[:160]}",
        }

    master = await _current_master()
    selected = []
    for option_type in ("CE", "PE"):
        selected.extend(
            ranked_mcx_option_contracts(
                master,
                "COPPER",
                observed_at.date(),
                underlying_price,
                option_type,
                max_strikes=strike_count,
            )
        )

    contracts = {}
    for contract in selected:
        try:
            assert_option_contract(contract)
        except ValueError:
            continue
        trading_symbol = str(contract.get("trading_symbol") or "")
        if trading_symbol:
            contracts[trading_symbol] = contract

    if not contracts:
        return {
            "status": "NO_ELIGIBLE_OPTION_CONTRACTS",
            "research_only": True,
            "production_rules_changed": False,
            "live_execution_enabled": False,
            "observed_at": observed_at.isoformat(),
            "underlying_price": underlying_price,
            "underlying_price_source": "LIVE_MCX_FUTURE_QUOTE",
            "underlying_contract": underlying_contract,
            "underlying_candle_health": candle_health,
            "snapshots": 0,
        }

    details = []
    records = []
    errors = 0
    counts = {"CE": 0, "PE": 0}
    for contract in contracts.values():
        try:
            quote = await fetch_mcx_option_live_quote(provider, contract)
            record = _snapshot_record(contract, quote, observed_at, underlying_price)
            if (
                not record["trading_symbol"]
                or not record["groww_symbol"]
                or record["option_type"] not in {"CE", "PE"}
                or record["strike"] is None
                or record["last_price"] is None
                or record["last_price"] <= 0
            ):
                raise RuntimeError("normalized option snapshot is incomplete")
            records.append(record)
            counts[record["option_type"]] += 1
            details.append({
                "trading_symbol": record["trading_symbol"],
                "option_type": record["option_type"],
                "strike": float(record["strike"]),
                "last_price": float(record["last_price"]),
                "status": "COLLECTED",
            })
        except Exception as exc:
            errors += 1
            details.append({
                "trading_symbol": contract.get("trading_symbol"),
                "option_type": contract.get("option_type"),
                "strike": contract.get("strike"),
                "status": "DATA_ERROR",
                "error": f"{exc.__class__.__name__}: {str(exc)[:160]}",
            })

    upserted = await snapshot_store.upsert(records)
    quality_pass = (
        counts["CE"] >= MIN_VALID_SNAPSHOTS_PER_TYPE
        and counts["PE"] >= MIN_VALID_SNAPSHOTS_PER_TYPE
    )
    status = (
        "COLLECTED"
        if quality_pass and errors == 0
        else "PARTIAL"
        if quality_pass
        else "INSUFFICIENT_OPTION_QUOTES"
    )

    strikes = [float(contract.get("strike")) for contract in contracts.values() if _number(contract.get("strike")) is not None]
    return {
        "status": status,
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "data_type": "LIVE_5M_LTP_SNAPSHOTS_NOT_OHLC",
        "observed_at": observed_at.isoformat(),
        "sample_bucket_at": _bucket_5m(observed_at).isoformat(),
        "trade_instrument": "OPTIONS",
        "options_only_policy": options_only_policy(),
        "underlying_reference": mark_underlying_reference(
            {"last_price": underlying_price, "trading_symbol": underlying_contract}
        ),
        "underlying_price": underlying_price,
        "underlying_price_source": "LIVE_MCX_FUTURE_QUOTE",
        "underlying_contract": underlying_contract,
        "underlying_candle_health": candle_health,
        "underlying_candle_close": underlying_candle_close,
        "latest_underlying_at": latest_stamp.isoformat() if latest_stamp else None,
        "underlying_age_minutes": round(underlying_age_minutes, 2) if underlying_age_minutes is not None else None,
        "strikes_per_type": strike_count,
        "selected_strike_range": {
            "low": min(strikes) if strikes else None,
            "high": max(strikes) if strikes else None,
        },
        "contracts_requested": len(contracts),
        "snapshots": len(records),
        "ce_snapshots": counts["CE"],
        "pe_snapshots": counts["PE"],
        "data_errors": errors,
        "upserted": upserted,
        "quality": {
            "minimum_valid_snapshots_per_type": MIN_VALID_SNAPSHOTS_PER_TYPE,
            "pass": quality_pass,
        },
        "contracts": details,
        "idempotency_key": "provider+trading_symbol+sample_bucket_at",
        "guardrail": "These are sampled live LTP observations, not reconstructed 5-minute OHLC candles. Strike selection is anchored to the live Copper futures quote. Stored 5-minute Copper candles are an independent health diagnostic and do not suppress otherwise valid live option observations.",
    }
