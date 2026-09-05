"""Build BTC derivatives evidence from immutable point-in-time archive rows.

This adapter never generates an instrument-specific trade. It combines only
point-in-time-visible OI and liquidation records, requires temporal/interval
alignment, and delegates interpretation to the shared derivatives context layer.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from math import isfinite
from typing import Iterable

from app.crypto_btc_derivatives_capture import BTC_LIQUIDATIONS_DATASET, BTC_OPEN_INTEREST_DATASET
from app.crypto_btc_funding_percentile import FundingPercentilePolicy, funding_percentile_from_pit_records
from app.crypto_market_intelligence import Evidence, derivatives_context


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _stamp(value) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value)))


def _unknown(decision_at: datetime, reason: str, metadata: dict | None = None) -> Evidence:
    return Evidence(
        family="DERIVATIVES_POSITIONING",
        causal_origin="LEVERAGED_POSITIONING",
        stance="UNKNOWN",
        strength="LOW",
        confidence=0.45,
        observed_at=_utc(decision_at),
        reason=reason,
        context_only=True,
        source="PIT_DERIVATIVES_ADAPTER",
        metadata={
            **(metadata or {}),
            "may_inform_options": True,
            "may_generate_futures_trade": False,
        },
    )


def _latest_visible(records: Iterable[dict], *, dataset: str, decision_at: datetime) -> dict | None:
    decision = _utc(decision_at)
    eligible = []
    for row in records:
        if row.get("dataset") != dataset or row.get("first_seen_at") is None:
            continue
        first_seen = _stamp(row["first_seen_at"])
        if first_seen <= decision:
            eligible.append((first_seen, row))
    if not eligible:
        return None
    eligible.sort(key=lambda item: item[0])
    return eligible[-1][1]


def derivatives_evidence_from_pit_records(
    records: Iterable[dict],
    *,
    decision_at: datetime,
    price_change_pct: float,
    funding_percentile: float | None = None,
    perp_premium_bps: float | None = None,
    max_event_misalignment_seconds: int = 60,
) -> Evidence:
    decision = _utc(decision_at)
    if not isfinite(float(price_change_pct)):
        raise ValueError("price_change_pct must be finite")
    if max_event_misalignment_seconds < 0:
        raise ValueError("max_event_misalignment_seconds must be >= 0")

    rows = list(records)
    oi = _latest_visible(rows, dataset=BTC_OPEN_INTEREST_DATASET, decision_at=decision)
    liq = _latest_visible(rows, dataset=BTC_LIQUIDATIONS_DATASET, decision_at=decision)
    if oi is None or liq is None:
        return _unknown(
            decision,
            "Point-in-time OI and liquidation records are both required before derivatives positioning may become directional.",
            {"open_interest_available": oi is not None, "liquidations_available": liq is not None},
        )

    oi_payload = oi.get("payload") if isinstance(oi.get("payload"), dict) else {}
    liq_payload = liq.get("payload") if isinstance(liq.get("payload"), dict) else {}
    oi_interval = str(oi_payload.get("interval") or "")
    liq_interval = str(liq_payload.get("interval") or "")
    if not oi_interval or oi_interval != liq_interval:
        return _unknown(
            decision,
            "OI and liquidation records use different or missing aggregation intervals.",
            {"oi_interval": oi_interval, "liquidation_interval": liq_interval},
        )

    oi_event = _stamp(oi["event_at"]) if oi.get("event_at") is not None else None
    liq_event = _stamp(liq["event_at"]) if liq.get("event_at") is not None else None
    if oi_event is None or liq_event is None:
        return _unknown(decision, "OI/liquidation provider event times are required for temporal alignment.")
    misalignment = abs((oi_event - liq_event).total_seconds())
    if misalignment > max_event_misalignment_seconds:
        return _unknown(
            decision,
            "OI and liquidation records are not time-aligned closely enough to form one derivatives state.",
            {"event_misalignment_seconds": misalignment},
        )

    try:
        oi_open = float(oi_payload["open_interest_open_usd"])
        oi_close = float(oi_payload["open_interest_close_usd"])
        long_liq = float(liq_payload["long_liquidation_usd"])
        short_liq = float(liq_payload["short_liquidation_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        return _unknown(decision, f"Required OI/liquidation fields are missing or invalid: {exc.__class__.__name__}.")
    if not all(isfinite(value) for value in (oi_open, oi_close, long_liq, short_liq)):
        return _unknown(decision, "OI/liquidation values must be finite.")
    if oi_open <= 0:
        return _unknown(decision, "OI percentage change cannot be computed from a zero/non-positive OI baseline.")
    if long_liq < 0 or short_liq < 0:
        return _unknown(decision, "Liquidation amounts cannot be negative.")

    oi_change_pct = ((oi_close - oi_open) / oi_open) * 100.0
    observed_at = max(_stamp(oi["first_seen_at"]), _stamp(liq["first_seen_at"]))
    evidence = derivatives_context(
        observed_at=observed_at,
        price_change_pct=float(price_change_pct),
        oi_change_pct=oi_change_pct,
        funding_percentile=funding_percentile,
        short_liquidations_usd=short_liq,
        long_liquidations_usd=long_liq,
        perp_premium_bps=perp_premium_bps,
        source="COINGLASS_V4_PIT",
    )
    metadata = dict(evidence.metadata)
    metadata.update({
        "oi_dataset": BTC_OPEN_INTEREST_DATASET,
        "liquidation_dataset": BTC_LIQUIDATIONS_DATASET,
        "aggregation_interval": oi_interval,
        "oi_first_seen_at": _stamp(oi["first_seen_at"]).isoformat(),
        "liquidation_first_seen_at": _stamp(liq["first_seen_at"]).isoformat(),
        "oi_event_at": oi_event.isoformat(),
        "liquidation_event_at": liq_event.isoformat(),
        "event_misalignment_seconds": misalignment,
        "historical_provider_values_assumed_immutable": False,
        "future_rows_used": False,
        "may_generate_futures_trade": False,
    })
    return replace(evidence, metadata=metadata)


def derivatives_evidence_from_full_pit_context(
    records: Iterable[dict],
    *,
    decision_at: datetime,
    price_change_pct: float,
    perp_premium_bps: float | None = None,
    funding_policy: FundingPercentilePolicy | None = None,
    max_event_misalignment_seconds: int = 60,
) -> Evidence:
    rows = list(records)
    funding = funding_percentile_from_pit_records(rows, decision_at=decision_at, policy=funding_policy)
    evidence = derivatives_evidence_from_pit_records(
        rows,
        decision_at=decision_at,
        price_change_pct=price_change_pct,
        funding_percentile=funding.get("percentile"),
        perp_premium_bps=perp_premium_bps,
        max_event_misalignment_seconds=max_event_misalignment_seconds,
    )
    metadata = dict(evidence.metadata)
    metadata.update({
        "funding_context_status": funding["status"],
        "funding_prior_sample_count": funding.get("prior_sample_count", 0),
        "funding_percentile_point_in_time": funding.get("percentile"),
    })
    return replace(evidence, metadata=metadata)


def architecture_contract() -> dict:
    return {
        "version": "BTC_DERIVATIVES_PIT_EVIDENCE_V2",
        "requires_open_interest": True,
        "requires_liquidations": True,
        "requires_point_in_time_visibility": True,
        "requires_temporal_alignment": True,
        "funding_percentile_may_be_derived_from_prior_pit_history": True,
        "insufficient_funding_history_blocks_oi_liquidation_evidence": False,
        "missing_leg_may_be_directional": False,
        "mismatched_intervals_may_be_directional": False,
        "provider_history_assumed_immutable": False,
        "futures_data_may_inform_options": True,
        "futures_trade_generation_allowed": False,
        "options_trade_generation_allowed": False,
        "research_only": True,
    }
