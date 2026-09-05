"""Immutable first-seen archival boundary for aggregate stablecoin liquidity."""
from __future__ import annotations

from datetime import datetime, timezone

from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture
from app.defillama_stablecoin_provider import DefiLlamaStablecoinSupplyCapture

STABLECOIN_SUPPLY_DATASET = "STABLECOIN_SUPPLY_LIQUIDITY"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def defillama_stablecoin_archive_record(capture: DefiLlamaStablecoinSupplyCapture) -> BtcPitArchiveRecord:
    capture.validated()
    first_seen = _utc(capture.first_seen_at)
    return archive_record_from_capture(
        dataset=STABLECOIN_SUPPLY_DATASET,
        provider=capture.provider,
        source_key=f"USD_STABLECOIN_SUPPLY:{int(first_seen.timestamp())}",
        first_seen_at=first_seen,
        event_at=None,
        source_version="DEFILLAMA_STABLECOINS_CURRENT_SUPPLY_V1",
        payload={
            "peg_type": capture.peg_type,
            "total_circulating": capture.total_circulating,
            "by_symbol": capture.by_symbol,
            "prices": capture.prices,
            "asset_count": capture.asset_count,
            "provider_event_time_available": False,
            "historical_values_backdated_to_click": False,
            "aggregate_supply_equals_exchange_inflow": False,
            "aggregate_supply_equals_deployable_spot_buying_power": False,
            "standalone_direction_assigned": False,
        },
    )


def architecture_contract() -> dict:
    return {
        "version": "STABLECOIN_SUPPLY_PIT_CAPTURE_V1",
        "dataset": STABLECOIN_SUPPLY_DATASET,
        "provider_event_time_required_when_unavailable": False,
        "first_seen_controls_click_visibility": True,
        "historical_values_backdated_to_click": False,
        "aggregate_supply_equals_exchange_inflow": False,
        "aggregate_supply_equals_deployable_spot_buying_power": False,
        "standalone_direction_assigned": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
