from __future__ import annotations

from datetime import datetime, timezone

from .copper_market_brain_direction_audit import PRIMARY_END, PRIMARY_START, REFERENCE_CONTRACT
from .frozen_market_candle_export import build_frozen_candle_artifact


async def export_copper_reaction_candles_from_store(store, *, exported_at=None):
    """Export the exact stored reference-contract bars used by Copper research.

    This is a read-only, no-network-refetch path. It intentionally does not run
    strategy decisions, trade outcomes, or P&L calculations.
    """
    await store.initialize()
    segments = await store.read_symbol_contract_segments("COPPER", 5, PRIMARY_START, PRIMARY_END)
    target = next(
        (segment for segment in segments
         if str(segment.get("trading_symbol") or "").upper() == REFERENCE_CONTRACT),
        None,
    )
    if target is None:
        raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    candles = target.get("candles") or []
    if not candles:
        raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} has no candles")
    return build_frozen_candle_artifact(
        candles,
        symbol="COPPER",
        trading_symbol=REFERENCE_CONTRACT,
        interval_minutes=5,
        start=PRIMARY_START.isoformat(),
        end=PRIMARY_END.isoformat(),
        source="persistent_store.read_symbol_contract_segments",
        exported_at=exported_at or datetime.now(timezone.utc).isoformat(),
    )
