"""Convert point-in-time social enrichment into BTC social context evidence.

Only enrichment records visible by the decision time and backed by a raw social
record that was also already visible may enter the Social Narrative lane. The
result still passes through ``crypto_social_intelligence``, whose contract keeps
all social evidence UNKNOWN/context-only and routes factual verification through
Crypto News Intelligence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.crypto_market_intelligence import Evidence
from app.crypto_social_intelligence import SocialNarrativeSignal, social_narrative_context
from app.crypto_social_pit_ingest import SOCIAL_ENRICHMENT_DATASET, SOCIAL_RAW_DATASET


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stamp(value) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value)))


def _visible(rows: Iterable[dict], *, decision_at: datetime) -> list[dict]:
    cutoff = _utc(decision_at)
    visible = []
    for row in rows:
        first_seen = row.get("first_seen_at")
        if first_seen is None:
            continue
        if _stamp(first_seen) <= cutoff:
            visible.append(row)
    return visible


def social_evidence_from_pit_records(
    records: Iterable[dict],
    *,
    decision_at: datetime,
    asset: str = "BTC",
) -> list[Evidence]:
    """Return deduplicated social context visible at ``decision_at``.

    Multiple enrichment revisions for the same event are permitted, but only the
    latest visible revision is used. A raw record that is missing/not yet visible
    makes its enrichment unusable rather than implicitly trusted.
    """
    cutoff = _utc(decision_at)
    wanted_asset = str(asset or "BTC").upper()
    visible = _visible(records, decision_at=cutoff)
    raw_by_natural_key = {
        str(row.get("natural_key")): row
        for row in visible
        if row.get("dataset") == SOCIAL_RAW_DATASET and row.get("natural_key")
    }

    latest_by_event: dict[str, tuple[datetime, dict]] = {}
    for row in visible:
        if row.get("dataset") != SOCIAL_ENRICHMENT_DATASET:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        raw_key = str(payload.get("raw_natural_key") or "")
        if not raw_key or raw_key not in raw_by_natural_key:
            continue
        assets = {str(value).upper() for value in payload.get("assets", [])}
        if wanted_asset not in assets:
            continue
        event_key = str(payload.get("event_key") or "").strip()
        if not event_key:
            continue
        seen = _stamp(row["first_seen_at"])
        current = latest_by_event.get(event_key)
        if current is None or seen > current[0]:
            latest_by_event[event_key] = (seen, row)

    output: list[Evidence] = []
    for event_key in sorted(latest_by_event):
        seen, row = latest_by_event[event_key]
        payload = row["payload"]
        signal = SocialNarrativeSignal(
            signal_id=str(row.get("record_fingerprint") or row.get("source_key") or event_key),
            event_key=event_key,
            assets=tuple(str(value).upper() for value in payload.get("assets", [])),
            platform="PIT_SOCIAL_ENRICHMENT",
            first_seen_at=seen,
            source_tier="D_COMMUNITY",
            claim=str(payload.get("claim_summary") or ""),
            mention_velocity_percentile=float(payload.get("mention_velocity_percentile", 0.0)),
            source_historical_reliability=float(payload.get("source_historical_reliability", 0.0)),
            truth_confidence=float(payload.get("truth_confidence", 0.0)),
            market_impact_confidence=float(payload.get("market_impact_confidence", 0.0)),
            direction_hint=str(payload.get("direction_hint") or "UNKNOWN").upper(),
            independent_source_count=int(payload.get("independent_source_count", 0)),
            primary_source_confirmed=bool(payload.get("primary_source_confirmed", False)),
        )
        evidence = social_narrative_context(signal, decision_at=cutoff)
        metadata = dict(evidence.metadata)
        metadata.update({
            "raw_social_natural_key": payload["raw_natural_key"],
            "raw_first_seen_at": payload.get("raw_first_seen_at"),
            "analysis_first_seen_at": payload.get("analysis_first_seen_at"),
            "point_in_time_social_ingestion": True,
            "standalone_direction_allowed": False,
            "generates_instrument_trade": False,
        })
        output.append(Evidence(
            family=evidence.family,
            causal_origin=evidence.causal_origin,
            stance=evidence.stance,
            strength=evidence.strength,
            confidence=evidence.confidence,
            observed_at=evidence.observed_at,
            reason=evidence.reason,
            context_only=evidence.context_only,
            source=evidence.source,
            metadata=metadata,
        ))
    return output


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_SOCIAL_PIT_EVIDENCE_V1",
        "raw_record_must_be_visible": True,
        "enrichment_must_be_visible": True,
        "latest_visible_revision_only": True,
        "future_enrichment_visible": False,
        "social_direction_vote_allowed": False,
        "verified_fact_must_pass_news_intelligence": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
