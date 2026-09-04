from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_backtest import _fetch_chunked, _ts
from .commodity_candle_collector import PROVIDER, _records
from .commodity_mtf import completed_rows

IST = ZoneInfo("Asia/Kolkata")
MODE = "COMMODITY_CONTRACT_DATA_CONTINUITY_V1"
ARCHIVE_TABLE = "commodity_contract_candle_archive"
TIMEFRAME_MINUTES = 5
DEFAULT_GUARD_DAYS = 7
DEFAULT_ARCHIVE_LOOKBACK_DAYS = 45
OVERLAP_MINUTES = 10

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {ARCHIVE_TABLE} (
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    segment TEXT NOT NULL,
    trading_symbol TEXT NOT NULL,
    groww_symbol TEXT,
    expiry_date DATE,
    timeframe_minutes SMALLINT NOT NULL,
    candle_at TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC NOT NULL DEFAULT 0,
    open_interest NUMERIC,
    archived_at TIMESTAMPTZ NOT NULL,
    source_class TEXT NOT NULL DEFAULT 'PRE_EXPIRY_PROVIDER_RETENTION_ARCHIVE',
    PRIMARY KEY (provider, trading_symbol, timeframe_minutes, candle_at)
);
CREATE INDEX IF NOT EXISTS commodity_contract_archive_symbol_time_idx
    ON {ARCHIVE_TABLE} (symbol, trading_symbol, timeframe_minutes, candle_at DESC);
"""

INSERT_SQL = f"""
INSERT INTO {ARCHIVE_TABLE} (
    provider, symbol, exchange, segment, trading_symbol, groww_symbol,
    expiry_date, timeframe_minutes, candle_at, open, high, low, close,
    volume, open_interest, archived_at, source_class
) VALUES (
    %(provider)s, %(symbol)s, %(exchange)s, %(segment)s, %(trading_symbol)s,
    %(groww_symbol)s, %(expiry_date)s, %(timeframe_minutes)s, %(candle_at)s,
    %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s,
    %(open_interest)s, %(archived_at)s, %(source_class)s
)
ON CONFLICT (provider, trading_symbol, timeframe_minutes, candle_at)
DO NOTHING;
"""


def _expiry_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def retention_policy() -> dict:
    """Provider-retention knowledge shared by every commodity brain.

    This is data provenance knowledge, never market-direction evidence.
    """
    return {
        "mode": MODE,
        "role": "DATA_PROVENANCE_AND_REPLAY_READINESS",
        "affects_direction": False,
        "affects_direction_confidence": False,
        "counts_as_causal_confirmation": False,
        "live_execution_effect": "NONE",
        "provider_history_after_expiry_assumed": False,
        "exact_contract_identity_must_be_preserved": True,
        "silent_next_contract_substitution_allowed": False,
        "prospective_and_archive_tapes_must_remain_distinct": True,
        "pre_expiry_guard_days": DEFAULT_GUARD_DAYS,
        "pre_expiry_archive_lookback_days": DEFAULT_ARCHIVE_LOOKBACK_DAYS,
        "empirical_provider_observation": {
            "provider": "GROWW",
            "product": "MCX_COPPER",
            "contract": "COPPER31AUG26FUT",
            "groww_symbol": "MCX-COPPER-31Aug26-FUT",
            "expiry": "2026-08-31",
            "while_active": {
                "observed_on": "2026-08-29",
                "historical_5m_retrieval": "AVAILABLE",
                "stored_rows_for_2026_08_03_through_2026_08_28": 3318,
            },
            "after_expiry": {
                "retested_on": "2026-09-05",
                "exact_contract_historical_5m_rows_returned": 0,
            },
            "control_check": {
                "contract": "COPPER30SEP26FUT",
                "dates": ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"],
                "historical_5m_rows_returned": 696,
                "classification": "ACTIVE_CONTRACT_HISTORY_STILL_AVAILABLE",
            },
            "lesson": "Provider historical access for an active MCX contract is not evidence that the same contract will remain retrievable after expiry.",
        },
        "rules": [
            "Preserve the exact trading_symbol, groww_symbol and expiry while the contract is visible.",
            "Inside the pre-expiry guard window, retain a contract-keyed replay archive while the provider still serves the contract.",
            "Prospective first-seen observations remain immutable and separate from reconstructed/archive data.",
            "An archived candle is reconstructable replay input; archived_at is not the candle's historical availability time.",
            "Never substitute the next futures contract for a missing expired contract without an explicit cross-contract methodology.",
            "If exact-contract data is missing, mark replay coverage partial or abstain rather than fabricate continuity.",
        ],
    }


def assess_contract_continuity(
    contract: dict,
    observed_at: datetime | str,
    *,
    guard_days: int = DEFAULT_GUARD_DAYS,
) -> dict:
    observed = _ts(observed_at).astimezone(IST)
    expiry = _expiry_date(contract.get("expiry_date") or contract.get("expiry"))
    exact_identity = bool(
        str(contract.get("trading_symbol") or "").strip()
        and str(contract.get("groww_symbol") or "").strip()
        and expiry
    )
    if expiry is None:
        stage = "UNKNOWN_EXPIRY"
        days_to_expiry = None
        archive_required = True
    else:
        days_to_expiry = (expiry - observed.date()).days
        if days_to_expiry < 0:
            stage = "EXPIRED"
            archive_required = False
        elif days_to_expiry == 0:
            stage = "EXPIRY_SESSION"
            archive_required = True
        elif days_to_expiry <= max(1, int(guard_days)):
            stage = "PRE_EXPIRY_ARCHIVE_WINDOW"
            archive_required = True
        else:
            stage = "NORMAL"
            archive_required = False

    return {
        "mode": MODE,
        "observed_at": observed.isoformat(),
        "provider": PROVIDER,
        "trading_symbol": contract.get("trading_symbol"),
        "groww_symbol": contract.get("groww_symbol"),
        "expiry_date": expiry.isoformat() if expiry else None,
        "days_to_expiry": days_to_expiry,
        "stage": stage,
        "exact_contract_identity_complete": exact_identity,
        "archive_required_now": archive_required,
        "provider_history_after_expiry_assumed": False,
        "silent_next_contract_substitution_allowed": False,
        "affects_direction": False,
        "counts_as_causal_confirmation": False,
        "required_actions": (
            [
                "PRESERVE_EXACT_CONTRACT_IDENTITY",
                "VERIFY_LOCAL_5M_ARCHIVE",
                "REPAIR_ARCHIVE_GAPS_BEFORE_EXPIRY",
                "KEEP_ARCHIVE_SEPARATE_FROM_PROSPECTIVE_TAPE",
            ]
            if archive_required
            else (
                ["DO_NOT_ASSUME_PROVIDER_RETRIEVABILITY", "DO_NOT_SUBSTITUTE_NEXT_CONTRACT"]
                if stage == "EXPIRED"
                else ["PRESERVE_EXACT_CONTRACT_IDENTITY", "CONTINUE_PROSPECTIVE_COLLECTION"]
            )
        ),
    }


class ContractArchiveStore:
    """First archival copy of completed bars, separate from prospective PIT memory."""

    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for contract continuity archive")

    def _connect(self):
        import psycopg
        return psycopg.connect(self.database_url, connect_timeout=10)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA_SQL)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _coverage_sync(self, trading_symbol: str, timeframe_minutes: int) -> dict:
        sql = f"""
            SELECT COUNT(*), MIN(candle_at), MAX(candle_at), MIN(archived_at), MAX(archived_at)
            FROM {ARCHIVE_TABLE}
            WHERE provider=%s AND trading_symbol=%s AND timeframe_minutes=%s
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (PROVIDER, str(trading_symbol), int(timeframe_minutes)))
                row = cursor.fetchone() or (0, None, None, None, None)
        return {
            "rows": int(row[0] or 0),
            "first_candle_at": row[1],
            "last_candle_at": row[2],
            "first_archived_at": row[3],
            "last_archived_at": row[4],
        }

    async def coverage(self, trading_symbol: str, timeframe_minutes: int = TIMEFRAME_MINUTES) -> dict:
        return await asyncio.to_thread(self._coverage_sync, trading_symbol, timeframe_minutes)

    def _insert_sync(self, records: list[dict]) -> int:
        if not records:
            return 0
        inserted = 0
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for record in records:
                    cursor.execute(INSERT_SQL, record)
                    inserted += int(cursor.rowcount or 0)
        return inserted

    async def insert_first_archive(self, records: list[dict]) -> int:
        return await asyncio.to_thread(self._insert_sync, records)


async def archive_active_contract_if_needed(
    provider,
    store: ContractArchiveStore,
    *,
    symbol: str,
    contract: dict,
    observed_at: datetime | None = None,
    guard_days: int = DEFAULT_GUARD_DAYS,
    lookback_days: int = DEFAULT_ARCHIVE_LOOKBACK_DAYS,
) -> dict:
    """Archive exact active-contract completed bars only when retention risk is near.

    The first call in the guard window fetches a bounded historical window while the
    exact contract is still provider-retrievable. Later calls overlap only the newest
    archived bars. The archive never claims prospective first-seen provenance.
    """
    observed = _ts(observed_at or datetime.now(IST)).astimezone(IST)
    assessment = assess_contract_continuity(contract, observed, guard_days=guard_days)
    if not assessment["archive_required_now"]:
        return {
            "status": "NOT_IN_ARCHIVE_WINDOW",
            "assessment": assessment,
            "archive_written": False,
            "prospective_tape_changed": False,
            "live_execution_enabled": False,
        }
    if not assessment["exact_contract_identity_complete"]:
        return {
            "status": "BLOCKED_INCOMPLETE_CONTRACT_IDENTITY",
            "assessment": assessment,
            "archive_written": False,
            "prospective_tape_changed": False,
            "live_execution_enabled": False,
        }

    await store.initialize()
    before = await store.coverage(contract["trading_symbol"], TIMEFRAME_MINUTES)
    archive_floor = observed - timedelta(days=max(1, int(lookback_days)))
    latest = before.get("last_candle_at")
    fetch_start = (
        max(archive_floor, _ts(latest) - timedelta(minutes=OVERLAP_MINUTES))
        if latest
        else archive_floor
    )
    fetched = await _fetch_chunked(
        provider,
        contract,
        TIMEFRAME_MINUTES,
        fetch_start,
        observed,
    )
    completed = completed_rows(fetched, observed, TIMEFRAME_MINUTES)
    base_records = _records(symbol.upper(), contract, TIMEFRAME_MINUTES, completed, observed)
    records = [
        {
            **record,
            "archived_at": observed,
            "source_class": "PRE_EXPIRY_PROVIDER_RETENTION_ARCHIVE",
        }
        for record in base_records
    ]
    inserted = await store.insert_first_archive(records)
    after = await store.coverage(contract["trading_symbol"], TIMEFRAME_MINUTES)
    return {
        "status": "ARCHIVED",
        "assessment": assessment,
        "archive_written": bool(inserted),
        "source_class": "PRE_EXPIRY_PROVIDER_RETENTION_ARCHIVE",
        "replay_class": "RECONSTRUCTABLE_NOT_PROSPECTIVE",
        "prospective_first_seen_claimed": False,
        "prospective_tape_changed": False,
        "fetch_start": fetch_start.isoformat(),
        "fetched": len(fetched),
        "completed": len(completed),
        "inserted_first_archive": inserted,
        "coverage_before": {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in before.items()
        },
        "coverage_after": {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in after.items()
        },
        "silent_next_contract_substitution_allowed": False,
        "direction_effect": "NONE",
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
    }
