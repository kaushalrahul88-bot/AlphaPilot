from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_backtest import _fetch_chunked, _ts
from .commodity_candle_collector import _records
from .commodity_contract_continuity import (
    ContractArchiveStore,
    archive_active_contract_if_needed,
    retention_policy,
)
from .commodity_mtf import completed_rows
from .commodities import resolve_nearest_mcx_future
from .copper_candle_observation_store import (
    PROVENANCE_ID,
    SYMBOL,
    TIMEFRAME_MINUTES,
    CopperCandleObservationStore,
)
from .mcx_calendar import mcx_metal_session_status


IST = ZoneInfo("Asia/Kolkata")
INITIAL_WARMUP_HOURS = 6
OVERLAP_MINUTES = 10


async def collect_copper_pit_candles(
    provider,
    store: CopperCandleObservationStore,
    now: datetime | None = None,
) -> dict:
    """Capture bounded first-seen COPPER 5m tape plus a separate expiry archive.

    Prospective observations remain immutable. The contract-continuity archive is a
    distinct reconstructable dataset and never retroactively becomes PIT evidence.
    """
    collected_at = _ts(now or datetime.now(IST))
    session = mcx_metal_session_status(collected_at)
    if not session.get("is_open"):
        return {
            "status": "MARKET_CLOSED",
            "symbol": SYMBOL,
            "timeframe_minutes": TIMEFRAME_MINUTES,
            "collected_at": collected_at.isoformat(),
            "market_session": session,
            "contract_continuity_policy": retention_policy(),
            "research_only": True,
            "production_rules_changed": False,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "historical_backfill_used": False,
            "provenance_id": PROVENANCE_ID,
        }

    await store.initialize()
    contract = await resolve_nearest_mcx_future(SYMBOL)
    trading_symbol = str(contract.get("trading_symbol") or "").strip().upper()
    if not trading_symbol.startswith("COPPER"):
        raise RuntimeError("Resolved MCX contract is not exact COPPER")

    latest = await store.latest_candle_at(trading_symbol)
    first_run = latest is None
    fetch_start = (
        collected_at - timedelta(hours=INITIAL_WARMUP_HOURS)
        if first_run
        else _ts(latest) - timedelta(minutes=OVERLAP_MINUTES)
    )

    fetched = await _fetch_chunked(
        provider,
        contract,
        TIMEFRAME_MINUTES,
        fetch_start,
        collected_at,
    )
    completed = completed_rows(fetched, collected_at, TIMEFRAME_MINUTES)
    records = _records(
        SYMBOL,
        contract,
        TIMEFRAME_MINUTES,
        completed,
        collected_at,
    )
    inserted = await store.insert_first_seen(records)
    status = await store.status()

    # Operational resilience only: preserve an exact-contract replay archive before
    # expiry while Groww still serves it. This archive is deliberately separate from
    # the immutable prospective tape and cannot enter prospective memory.
    try:
        continuity = await archive_active_contract_if_needed(
            provider,
            ContractArchiveStore(store.database_url),
            symbol=SYMBOL,
            contract=contract,
            observed_at=collected_at,
        )
    except Exception as exc:
        continuity = {
            "status": "ARCHIVE_GUARD_ERROR",
            "error": f"{exc.__class__.__name__}: {str(exc)[:240]}",
            "archive_written": False,
            "prospective_tape_changed": False,
            "direction_effect": "NONE",
            "live_execution_enabled": False,
        }

    return {
        "status": "COLLECTED",
        "symbol": SYMBOL,
        "contract": trading_symbol,
        "timeframe_minutes": TIMEFRAME_MINUTES,
        "fetch_start": fetch_start.isoformat(),
        "collected_at": collected_at.isoformat(),
        "first_run_bounded_warmup": first_run,
        "warmup_hours": INITIAL_WARMUP_HOURS if first_run else 0,
        "overlap_minutes": OVERLAP_MINUTES,
        "fetched": len(fetched),
        "completed": len(completed),
        "candidate_records": len(records),
        "inserted_first_seen": inserted,
        "latest_completed_at": records[-1]["candle_at"].isoformat() if records else None,
        "store": status,
        "point_in_time_policy": "BAR_COMPLETED_AND_COLLECTED_AT_OR_BEFORE_CLICK",
        "first_seen_immutable": True,
        "provenance_id": PROVENANCE_ID,
        "historical_backfill_used": False,
        "warmup_market_timestamps_retroactively_visible": False,
        "generic_all_commodity_collector_used": False,
        "contract_continuity": continuity,
        "contract_continuity_policy": retention_policy(),
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
    }


async def read_copper_pit_candles(
    store: CopperCandleObservationStore,
    as_of: datetime,
    lookback_days: int = 7,
    trading_symbol: str | None = None,
) -> list[list]:
    observed = _ts(as_of)
    return await store.read_pit(
        observed - timedelta(days=max(1, int(lookback_days))),
        observed,
        observed,
        trading_symbol=trading_symbol,
    )
