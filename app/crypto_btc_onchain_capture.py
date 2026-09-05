"""Immutable PIT archival adapters for verified BTC on-chain metric captures."""
from __future__ import annotations

from datetime import datetime, timezone

from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture
from app.crypto_onchain_intelligence import OnchainMetric, generic_metric_context, metric_semantics
from app.glassnode_btc_onchain_provider import GlassnodeMetricCapture

BTC_ONCHAIN_DATASET = "BTC_ONCHAIN_ENTITY_AND_FLOW_METRICS"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def glassnode_onchain_archive_record(capture: GlassnodeMetricCapture) -> BtcPitArchiveRecord:
    capture.validated()
    source_key = f"BTC:{capture.metric}:{capture.interval}:{int(_utc(capture.provider_time).timestamp())}"
    return archive_record_from_capture(
        dataset=BTC_ONCHAIN_DATASET,
        provider=capture.provider,
        source_key=source_key,
        first_seen_at=_utc(capture.first_seen_at),
        event_at=_utc(capture.provider_time),
        source_version="GLASSNODE_ONCHAIN_CAPTURE_V1",
        payload={
            "asset": capture.asset,
            "metric": capture.metric,
            "provider_time": _utc(capture.provider_time).isoformat(),
            "interval": capture.interval,
            "value": capture.value,
            "unit": capture.unit,
            "endpoint": capture.endpoint,
            "historical_content_immutable": capture.historical_content_immutable,
            "provider_delivery_time_proven": False,
            "first_seen_required_for_click_replay": True,
            "standalone_trade_signal": False,
        },
    )


def onchain_metric_from_pit_record(row: dict) -> OnchainMetric:
    if row.get("dataset") != BTC_ONCHAIN_DATASET:
        raise ValueError("PIT row is not a BTC on-chain metric record")
    if row.get("first_seen_at") is None:
        raise ValueError("on-chain PIT row requires first_seen_at")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    metric = str(payload.get("metric") or "").upper()
    if not metric:
        raise ValueError("on-chain PIT row requires metric")
    # Trigger semantic registration/fallback now so callers can inspect unknowns.
    semantics = metric_semantics(metric)
    return OnchainMetric(
        asset=str(payload.get("asset") or "BTC"),
        metric=metric,
        observed_at=datetime.fromisoformat(str(row["first_seen_at"])),
        value=float(payload["value"]),
        source=str(row.get("provider") or "UNKNOWN_PROVIDER"),
        role=semantics["role"],
        unit=payload.get("unit"),
        historical_percentile=None,
        metadata={
            "provider_time": payload.get("provider_time"),
            "interval": payload.get("interval"),
            "historical_content_immutable": bool(payload.get("historical_content_immutable", False)),
            "provider_delivery_time_proven": bool(payload.get("provider_delivery_time_proven", False)),
            "first_seen_required_for_click_replay": True,
            "source_key": row.get("source_key"),
        },
    )


def onchain_context_from_pit_record(row: dict):
    return generic_metric_context(onchain_metric_from_pit_record(row))


def architecture_contract() -> dict:
    return {
        "version": "BTC_ONCHAIN_PIT_CAPTURE_V1",
        "dataset": BTC_ONCHAIN_DATASET,
        "provider_time_separate_from_first_seen": True,
        "pit_content_immutability_equals_exact_delivery_time": False,
        "mutable_entity_metric_may_rewrite_first_seen": False,
        "raw_onchain_metric_standalone_direction_allowed": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
