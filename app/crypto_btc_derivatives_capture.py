"""Point-in-time archival adapters for verified BTC derivatives captures."""
from __future__ import annotations

from datetime import datetime, timezone

from app.coinglass_btc_derivatives_provider import (
    CoinGlassLiquidationCapture,
    CoinGlassOpenInterestCapture,
)
from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture

BTC_OPEN_INTEREST_DATASET = "BTC_OPEN_INTEREST"
BTC_LIQUIDATIONS_DATASET = "BTC_LIQUIDATIONS"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _source_key(kind: str, provider_time: datetime, interval: str) -> str:
    return f"BTC:{kind}:{interval}:{int(_utc(provider_time).timestamp() * 1000)}"


def coinglass_open_interest_archive_record(capture: CoinGlassOpenInterestCapture) -> BtcPitArchiveRecord:
    capture.validated()
    return archive_record_from_capture(
        dataset=BTC_OPEN_INTEREST_DATASET,
        provider=capture.provider,
        source_key=_source_key("OI", capture.provider_time, capture.interval),
        first_seen_at=_utc(capture.first_seen_at),
        event_at=_utc(capture.provider_time),
        source_version="COINGLASS_V4_AGGREGATED_OI_OHLC_V1",
        payload={
            "symbol": capture.symbol,
            "interval": capture.interval,
            "unit": capture.unit,
            "provider_time": _utc(capture.provider_time).isoformat(),
            "open_interest_open_usd": capture.open_interest_open_usd,
            "open_interest_high_usd": capture.open_interest_high_usd,
            "open_interest_low_usd": capture.open_interest_low_usd,
            "open_interest_close_usd": capture.open_interest_close_usd,
            "historical_endpoint_polled_prospectively": True,
            "historical_values_assumed_immutable": False,
            "open_interest_inferred_from_volume": False,
        },
    )


def coinglass_liquidation_archive_record(capture: CoinGlassLiquidationCapture) -> BtcPitArchiveRecord:
    capture.validated()
    return archive_record_from_capture(
        dataset=BTC_LIQUIDATIONS_DATASET,
        provider=capture.provider,
        source_key=_source_key("LIQ", capture.provider_time, capture.interval),
        first_seen_at=_utc(capture.first_seen_at),
        event_at=_utc(capture.provider_time),
        source_version="COINGLASS_V4_AGGREGATED_LIQUIDATIONS_V1",
        payload={
            "symbol": capture.symbol,
            "interval": capture.interval,
            "provider_time": _utc(capture.provider_time).isoformat(),
            "exchanges": list(capture.exchanges),
            "long_liquidation_usd": capture.long_liquidation_usd,
            "short_liquidation_usd": capture.short_liquidation_usd,
            "historical_endpoint_polled_prospectively": True,
            "historical_values_assumed_immutable": False,
            "liquidations_inferred_from_price": False,
        },
    )


def architecture_contract() -> dict:
    return {
        "version": "BTC_DERIVATIVES_CAPTURE_ADAPTER_V1",
        "open_interest_dataset": BTC_OPEN_INTEREST_DATASET,
        "liquidation_dataset": BTC_LIQUIDATIONS_DATASET,
        "provider_history_polled_prospectively": True,
        "historical_provider_values_assumed_immutable": False,
        "first_seen_timestamp_required": True,
        "open_interest_inferred_from_volume": False,
        "liquidations_inferred_from_price": False,
        "options_trade_generation_allowed": False,
        "futures_trade_generation_allowed": False,
        "research_only": True,
    }
