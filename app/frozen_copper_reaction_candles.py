from __future__ import annotations

import hashlib
import json

from .copper_market_brain_direction_audit import PRIMARY_END, PRIMARY_START, REFERENCE_CONTRACT
from .copper_research_brain import clean_ohlcv


def _canonical_sha256(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def export_frozen_copper_reaction_candles(store) -> dict:
    """Export the exact stored Current Mind Copper segment without replaying decisions.

    This is an offline/research data export. It performs no provider fetch and exposes
    no decision, trade outcome or P&L fields.
    """
    await store.initialize()
    segments = await store.read_symbol_contract_segments("COPPER", 5, PRIMARY_START, PRIMARY_END)
    target = next(
        (s for s in segments if str(s.get("trading_symbol") or "").upper() == REFERENCE_CONTRACT),
        None,
    )
    if not target:
        raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")

    candles = clean_ohlcv(target.get("candles") or [])
    payload = {
        "mode": "FROZEN_COPPER_REACTION_CANDLES_V1",
        "outcome_blind": True,
        "network_refetch": False,
        "symbol": "COPPER",
        "trading_symbol": target.get("trading_symbol"),
        "expiry_date": target.get("expiry_date"),
        "timeframe_minutes": 5,
        "start_at": PRIMARY_START.isoformat(),
        "end_at": PRIMARY_END.isoformat(),
        "candles": candles,
        "candle_count": len(candles),
        "guardrails": [
            "Candles come from the same persistent-store segment used by Current Mind replay.",
            "No provider/network historical refetch occurs in this exporter.",
            "No strategy decision, trade outcome or P&L is evaluated or exported.",
            "The checksum covers the normalized candle array exactly as exported.",
        ],
    }
    payload["candles_sha256"] = _canonical_sha256(candles)
    return payload
