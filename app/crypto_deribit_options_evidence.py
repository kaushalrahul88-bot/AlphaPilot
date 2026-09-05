"""Build BTC options-market context from Deribit PIT snapshots.

The latest visible Deribit snapshot supplies current IV/OI/term-structure context.
ATM IV percentile is computed only against earlier AlphaPilot first-seen Deribit
snapshots inside a bounded lookback. Insufficient prior history leaves IV
percentile unknown. The resulting evidence delegates to BTC options perception,
which is always underlying-direction-neutral and cannot create a trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Iterable

from app.crypto_btc_perception import BtcOptionsMarketSnapshot, options_market_context
from app.crypto_deribit_options_pit import DATASET
from app.crypto_market_intelligence import Evidence


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stamp(value) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _finite_or_none(value):
    if value is None:
        return None
    number = float(value)
    return number if isfinite(number) else None


@dataclass(frozen=True)
class DeribitOptionsEvidencePolicy:
    min_prior_iv_samples: int = 20
    iv_lookback_days: int = 30
    max_snapshot_age_seconds: int = 15 * 60

    def validated(self) -> "DeribitOptionsEvidencePolicy":
        if int(self.min_prior_iv_samples) < 2:
            raise ValueError("min_prior_iv_samples must be >= 2")
        if int(self.iv_lookback_days) <= 0:
            raise ValueError("iv_lookback_days must be > 0")
        if int(self.max_snapshot_age_seconds) <= 0:
            raise ValueError("max_snapshot_age_seconds must be > 0")
        return self


def _visible_rows(records: Iterable[dict], *, decision_at: datetime) -> list[dict]:
    cutoff = _utc(decision_at)
    rows = []
    for row in records:
        if row.get("dataset") != DATASET or row.get("first_seen_at") is None:
            continue
        try:
            seen = _stamp(row["first_seen_at"])
        except (TypeError, ValueError):
            continue
        if seen <= cutoff:
            rows.append(row)
    return sorted(rows, key=lambda row: _stamp(row["first_seen_at"]))


def _iv_percentile(*, current_iv: float, current_seen: datetime, visible: list[dict], policy: DeribitOptionsEvidencePolicy) -> tuple[float | None, int]:
    earliest = current_seen - timedelta(days=int(policy.iv_lookback_days))
    prior = []
    for row in visible:
        seen = _stamp(row["first_seen_at"])
        if not (earliest <= seen < current_seen):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        iv = _finite_or_none(payload.get("atm_mark_iv_pct"))
        if iv is not None and iv > 0:
            prior.append(iv)
    if len(prior) < int(policy.min_prior_iv_samples):
        return None, len(prior)
    percentile = sum(1 for value in prior if value <= current_iv) / len(prior)
    return percentile, len(prior)


def deribit_options_evidence_from_pit_records(
    records: Iterable[dict],
    *,
    decision_at: datetime,
    policy: DeribitOptionsEvidencePolicy | None = None,
) -> Evidence:
    policy = (policy or DeribitOptionsEvidencePolicy()).validated()
    decision = _utc(decision_at)
    visible = _visible_rows(records, decision_at=decision)
    if not visible:
        return Evidence(
            family="BTC_OPTIONS_MARKET",
            causal_origin="OPTIONS_POSITIONING",
            stance="UNKNOWN",
            strength="LOW",
            confidence=0.4,
            observed_at=decision,
            reason="No Deribit global BTC options PIT snapshot was visible by the decision time.",
            context_only=True,
            source="DERIBIT_PIT_GLOBAL_OPTIONS_CONTEXT",
            metadata={
                "status": "NO_VISIBLE_DERIBIT_OPTIONS_CONTEXT",
                "standalone_direction_allowed": False,
                "coindcx_contract_selection_allowed": False,
                "coindcx_quote_fill_allowed": False,
                "trade_generated": False,
            },
        )

    latest = visible[-1]
    latest_seen = _stamp(latest["first_seen_at"])
    age = (decision - latest_seen).total_seconds()
    if age < 0:
        raise AssertionError("future Deribit snapshot escaped PIT visibility filter")
    if age > int(policy.max_snapshot_age_seconds):
        return Evidence(
            family="BTC_OPTIONS_MARKET",
            causal_origin="OPTIONS_POSITIONING",
            stance="UNKNOWN",
            strength="LOW",
            confidence=0.4,
            observed_at=latest_seen,
            reason="Latest Deribit global BTC options snapshot is stale for current Options context.",
            context_only=True,
            source="DERIBIT_PIT_GLOBAL_OPTIONS_CONTEXT",
            metadata={
                "status": "STALE_DERIBIT_OPTIONS_CONTEXT",
                "snapshot_age_seconds": age,
                "standalone_direction_allowed": False,
                "coindcx_contract_selection_allowed": False,
                "coindcx_quote_fill_allowed": False,
                "trade_generated": False,
            },
        )

    payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
    atm_iv = _finite_or_none(payload.get("atm_mark_iv_pct"))
    oi_ratio = _finite_or_none(payload.get("put_call_open_interest_ratio"))
    term_slope = _finite_or_none(payload.get("term_structure_slope_iv_points"))
    if atm_iv is None or atm_iv <= 0:
        raise ValueError("visible Deribit options snapshot lacks valid ATM mark IV")
    if oi_ratio is not None and oi_ratio < 0:
        raise ValueError("visible Deribit put/call OI ratio cannot be negative")

    iv_percentile, prior_count = _iv_percentile(
        current_iv=atm_iv,
        current_seen=latest_seen,
        visible=visible,
        policy=policy,
    )
    evidence = options_market_context(BtcOptionsMarketSnapshot(
        observed_at=latest_seen,
        atm_iv_percentile=iv_percentile,
        put_call_skew_25d=None,
        put_call_oi_ratio=oi_ratio,
        term_structure_slope=term_slope,
        source="DERIBIT_PIT_GLOBAL_OPTIONS_CONTEXT",
    ))
    metadata = dict(evidence.metadata)
    metadata.update({
        "status": "DERIBIT_OPTIONS_CONTEXT_READY",
        "raw_atm_mark_iv_pct": atm_iv,
        "prior_iv_sample_count": prior_count,
        "iv_percentile_ready": iv_percentile is not None,
        "iv_percentile_point_in_time": iv_percentile,
        "iv_lookback_days": int(policy.iv_lookback_days),
        "put_call_open_interest_ratio": oi_ratio,
        "term_structure_slope_iv_points": term_slope,
        "skew_25d": None,
        "skew_25d_inferred": False,
        "global_options_context_only": True,
        "coindcx_contract_data": False,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "future_rows_used": False,
        "trade_generated": False,
    })
    return Evidence(
        family=evidence.family,
        causal_origin=evidence.causal_origin,
        stance=evidence.stance,
        strength=evidence.strength,
        confidence=evidence.confidence,
        observed_at=evidence.observed_at,
        reason=evidence.reason,
        context_only=True,
        source=evidence.source,
        metadata=metadata,
    )


def architecture_contract() -> dict:
    return {
        "version": "DERIBIT_BTC_OPTIONS_EVIDENCE_V1",
        "uses_only_visible_first_seen_snapshots": True,
        "iv_percentile_uses_only_prior_visible_history": True,
        "insufficient_iv_history_invents_percentile": False,
        "skew_25d_inferred": False,
        "underlying_direction_vote_allowed": False,
        "global_options_context_only": True,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
