from __future__ import annotations

import asyncio

from app.frozen_copper_reaction_candles import export_frozen_copper_reaction_candles


class Store:
    def __init__(self, segments):
        self.segments = segments
        self.initialized = False
        self.read_args = None

    async def initialize(self):
        self.initialized = True

    async def read_symbol_contract_segments(self, *args):
        self.read_args = args
        return self.segments


def test_exports_exact_reference_segment_without_outcomes():
    store = Store([
        {"trading_symbol": "OTHER", "candles": [["2026-08-03T09:00:00+05:30", 1, 2, 0.5, 1.5, 10]]},
        {"trading_symbol": "COPPER31AUG26FUT", "expiry_date": "2026-08-31",
         "candles": [["2026-08-03T09:00:00+05:30", 100, 102, 99, 101, 12]]},
    ])
    result = asyncio.run(export_frozen_copper_reaction_candles(store))
    assert store.initialized is True
    assert store.read_args[0:2] == ("COPPER", 5)
    assert result["trading_symbol"] == "COPPER31AUG26FUT"
    assert result["timeframe_minutes"] == 5
    assert result["candle_count"] == 1
    assert len(result["candles_sha256"]) == 64
    assert "outcome" not in result and "pnl" not in result and "decision" not in result
    assert result["network_refetch"] is False


def test_checksum_is_deterministic():
    segment = {"trading_symbol": "COPPER31AUG26FUT",
               "candles": [["2026-08-03T09:00:00+05:30", 100, 102, 99, 101, 12]]}
    first = asyncio.run(export_frozen_copper_reaction_candles(Store([segment])))
    second = asyncio.run(export_frozen_copper_reaction_candles(Store([segment])))
    assert first["candles_sha256"] == second["candles_sha256"]


def test_missing_reference_contract_fails_closed():
    store = Store([{"trading_symbol": "OTHER", "candles": []}])
    try:
        asyncio.run(export_frozen_copper_reaction_candles(store))
    except RuntimeError as exc:
        assert "COPPER31AUG26FUT" in str(exc)
    else:
        raise AssertionError("missing reference contract must fail closed")
