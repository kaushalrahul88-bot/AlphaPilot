from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from .commodity_option_history import (
    fetch_mcx_option_day,
    fetch_mcx_option_master,
    ranked_mcx_option_contracts,
)


IST = ZoneInfo("Asia/Kolkata")
PROVIDER = "GROWW"
TIMEFRAME_MINUTES = 5
DEFAULT_STRIKES_PER_TYPE = 12
SESSION_LOOKBACK_DAYS = 7
MAX_SESSION_AGE_DAYS = 5

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS commodity_option_candles (
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
    timeframe_minutes SMALLINT NOT NULL,
    candle_at TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC NOT NULL DEFAULT 0,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, trading_symbol, timeframe_minutes, candle_at)
);
CREATE INDEX IF NOT EXISTS commodity_option_candles_underlying_time_idx
    ON commodity_option_candles (underlying_symbol, timeframe_minutes, candle_at DESC);
CREATE INDEX IF NOT EXISTS commodity_option_candles_contract_idx
    ON commodity_option_candles (
        underlying_symbol, expiry_date, option_type, strike, candle_at DESC
    );
"""

UPSERT_SQL = """
INSERT INTO commodity_option_candles (
    provider, underlying_symbol, exchange, segment, trading_symbol, groww_symbol,
    expiry_date, strike, option_type, lot_size, timeframe_minutes, candle_at,
    open, high, low, close, volume, collected_at
) VALUES (
    %(provider)s, %(underlying_symbol)s, %(exchange)s, %(segment)s,
    %(trading_symbol)s, %(groww_symbol)s, %(expiry_date)s, %(strike)s,
    %(option_type)s, %(lot_size)s, %(timeframe_minutes)s, %(candle_at)s,
    %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s, %(collected_at)s
)
ON CONFLICT (provider, trading_symbol, timeframe_minutes, candle_at)
DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    lot_size = EXCLUDED.lot_size,
    collected_at = EXCLUDED.collected_at;
"""


class OptionCandleStore(Protocol):
    async def initialize(self) -> None: ...
    async def upsert(self, records: list[dict]) -> int: ...
    async def status(self, underlying_symbol: str = "COPPER") -> dict: ...


class PostgresOptionCandleStore:
    """Durable forward-only MCX option-premium memory."""

    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for option candle collection")

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
                MIN(candle_at),
                MAX(candle_at),
                COUNT(DISTINCT candle_at::date)
            FROM commodity_option_candles
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
            "series": [
                {
                    "option_type": option_type,
                    "contracts": contracts,
                    "candles": candles,
                    "first_at": first_at.isoformat() if first_at else None,
                    "last_at": last_at.isoformat() if last_at else None,
                    "trading_days": trading_days,
                }
                for option_type, contracts, candles, first_at, last_at, trading_days in rows
            ],
        }

    async def status(self, underlying_symbol="COPPER"):
        return await asyncio.to_thread(self._status_sync, underlying_symbol)


def _decimal(value, default=0):
    try:
        return Decimal(str(default if value is None else value))
    except Exception:
        return Decimal(str(default))


def _timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _records(contract, candles, collected_at):
    deduplicated = {}
    for row in candles or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            candle_at = _timestamp(row[0])
            open_price, high, low, close = (_decimal(value) for value in row[1:5])
        except (TypeError, ValueError, OverflowError):
            continue
        if min(open_price, high, low, close) <= 0 or high < low:
            continue
        record = {
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
            "timeframe_minutes": TIMEFRAME_MINUTES,
            "candle_at": candle_at,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": max(Decimal(0), _decimal(row[5] if len(row) > 5 else 0)),
            "collected_at": collected_at,
        }
        if (
            not record["trading_symbol"]
            or not record["groww_symbol"]
            or record["option_type"] not in {"CE", "PE"}
            or record["strike"] <= 0
        ):
            continue
        deduplicated[candle_at.isoformat()] = record
    return [deduplicated[key] for key in sorted(deduplicated)]



async def collect_copper_option_candles(
    provider,
    underlying_store,
    option_store: OptionCandleStore,
    now: datetime | None = None,
    strikes_per_type: int = DEFAULT_STRIKES_PER_TYPE,
):
    """Collect the completed session's active Copper CE/PE 5m premiums.

    Contract/strike choice is based only on the stored underlying close for the
    same session and the current MCX instrument master. No trade outcomes are
    used. This is data collection only, not strategy selection.
    """
    collected_at = _timestamp(now or datetime.now(IST))
    strike_count = max(1, min(int(strikes_per_type), 20))

    await underlying_store.initialize()
    await option_store.initialize()

    lookup_start = collected_at - timedelta(days=SESSION_LOOKBACK_DAYS)
    underlying = await underlying_store.read_symbol(
        "COPPER", TIMEFRAME_MINUTES, lookup_start, collected_at,
    )
    usable = []
    for row in underlying or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            stamp = _timestamp(row[0])
            float(row[2]); float(row[3]); float(row[4])
        except (TypeError, ValueError, OverflowError):
            continue
        if stamp <= collected_at:
            usable.append((stamp, row))

    if not usable:
        return {
            "status": "NO_UNDERLYING_CANDLES",
            "research_only": True,
            "production_rules_changed": False,
            "requested_through": collected_at.isoformat(),
            "underlying_symbol": "COPPER",
            "upserted": 0,
            "contracts_requested": 0,
            "contracts_with_candles": 0,
        }

    trade_day = max(stamp.date() for stamp, _row in usable)
    session_age_days = (collected_at.date() - trade_day).days
    if session_age_days > MAX_SESSION_AGE_DAYS:
        return {
            "status": "STALE_UNDERLYING_SESSION",
            "research_only": True,
            "production_rules_changed": False,
            "requested_through": collected_at.isoformat(),
            "trade_date": trade_day.isoformat(),
            "session_age_days": session_age_days,
            "max_session_age_days": MAX_SESSION_AGE_DAYS,
            "underlying_symbol": "COPPER",
            "upserted": 0,
            "contracts_requested": 0,
            "contracts_with_candles": 0,
        }

    session = [row for stamp, row in usable if stamp.date() == trade_day]
    latest_price = float(session[-1][4])
    session_low = min(float(row[3]) for row in session)
    session_high = max(float(row[2]) for row in session)

    master = await fetch_mcx_option_master(["COPPER"])
    selected = []
    for option_type in ("CE", "PE"):
        selected.extend(
            ranked_mcx_option_contracts(
                master,
                "COPPER",
                trade_day,
                latest_price,
                option_type,
                max_strikes=strike_count,
            )
        )

    # Deduplicate defensively by exact listed contract.
    contracts = {}
    for contract in selected:
        symbol = str(contract.get("trading_symbol") or "")
        if symbol:
            contracts[symbol] = contract

    if not contracts:
        return {
            "status": "NO_ELIGIBLE_OPTION_CONTRACTS",
            "research_only": True,
            "production_rules_changed": False,
            "trade_date": trade_day.isoformat(),
            "underlying_symbol": "COPPER",
            "underlying_close": latest_price,
            "session_low": session_low,
            "session_high": session_high,
            "upserted": 0,
            "contracts_requested": 0,
            "contracts_with_candles": 0,
        }

    details = []
    total_upserted = 0
    contracts_with_candles = 0
    data_errors = 0

    for contract in contracts.values():
        try:
            history = await fetch_mcx_option_day(provider, contract, trade_day)
            candles = history.get("candles") or []
            records = _records(contract, candles, collected_at)
            upserted = await option_store.upsert(records)
            if records:
                contracts_with_candles += 1
            total_upserted += upserted
            details.append({
                "trading_symbol": contract.get("trading_symbol"),
                "option_type": contract.get("option_type"),
                "strike": contract.get("strike"),
                "expiry": contract.get("expiry"),
                "candles": len(records),
                "upserted": upserted,
                "status": "COLLECTED" if records else "NO_CANDLES",
            })
        except Exception as exc:
            data_errors += 1
            details.append({
                "trading_symbol": contract.get("trading_symbol"),
                "option_type": contract.get("option_type"),
                "strike": contract.get("strike"),
                "expiry": contract.get("expiry"),
                "candles": 0,
                "upserted": 0,
                "status": "DATA_ERROR",
                "error": f"{exc.__class__.__name__}: {str(exc)[:160]}",
            })

    strikes = [float(contract["strike"]) for contract in contracts.values()]
    coverage_low = min(strikes) if strikes else None
    coverage_high = max(strikes) if strikes else None

    return {
        "status": (
            "COLLECTED"
            if contracts_with_candles > 0 and data_errors == 0
            else "PARTIAL"
            if contracts_with_candles > 0
            else "NO_OPTION_CANDLES"
        ),
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "trade_date": trade_day.isoformat(),
        "session_age_days": session_age_days,
        "underlying_symbol": "COPPER",
        "underlying_close": latest_price,
        "session_low": session_low,
        "session_high": session_high,
        "strikes_per_type": strike_count,
        "strike_coverage": {
            "lowest_selected": coverage_low,
            "highest_selected": coverage_high,
            "covers_session_range": (
                coverage_low is not None
                and coverage_high is not None
                and coverage_low <= session_low
                and coverage_high >= session_high
            ),
        },
        "contracts_requested": len(contracts),
        "contracts_with_candles": contracts_with_candles,
        "data_errors": data_errors,
        "upserted": total_upserted,
        "contracts": details,
        "idempotency_key": "provider+trading_symbol+timeframe_minutes+candle_at",
    }
