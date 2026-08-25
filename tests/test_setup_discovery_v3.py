from datetime import datetime, timedelta

import pytest

from app.setup_discovery_v3 import run_setup_discovery_v3


class EmptyProvider:
    async def historical_candles(self, symbol, timeframe, start, end):
        return []


@pytest.mark.asyncio
async def test_v3_protocol_metadata_is_frozen():
    result = await run_setup_discovery_v3(EmptyProvider(), ["RELIANCE"], "2026-04-13", "2026-04-17")
    assert result["mode"] == "ALPHAPILOT_SETUP_DISCOVERY_V3_FAST_FOLLOW_THROUGH"
    assert result["production_rules_changed"] is False
    assert result["fixed_gates"]["replication_blocks_required"] == 4
    assert len(result["rows"]) == 8


@pytest.mark.asyncio
async def test_v3_rejects_dates_outside_frozen_development_book():
    with pytest.raises(ValueError, match="frozen"):
        await run_setup_discovery_v3(EmptyProvider(), ["RELIANCE"], "2026-08-11", "2026-08-17")


@pytest.mark.asyncio
async def test_v3_rejects_blocks_longer_than_one_week():
    with pytest.raises(ValueError, match="7 calendar days"):
        await run_setup_discovery_v3(EmptyProvider(), ["RELIANCE"], "2026-04-13", "2026-04-21")
