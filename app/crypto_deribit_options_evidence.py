"""Build BTC options-market context from Deribit PIT snapshots.

Two independently timestamped Deribit datasets may contribute:
- chain context: ATM IV, open interest and term structure;
- ticker Greeks: observed option deltas/Greeks and 25-delta put/call skew.

Each component is filtered by its own AlphaPilot first_seen_at and freshness rule.
ATM IV percentile is computed only from *prior* visible chain snapshots. Missing
or stale Greeks simply leave skew unknown; missing or stale chain context leaves
IV/OI unknown. Neither dataset may create underlying BTC direction or substitute
for CoinDCX execution data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Iterable

from app.crypto_btc_perception import BtcOptionsMarketSnapshot, options_market_context
from app.crypto_deribit_options_greeks_pit import DATASET as GREEKS_DATASET
from app.crypto_deribit_options_pit import DATASET as CONTEXT_DATASET
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
    max_greeks_age_seconds: int = 30

    def validated(self) -> "DeribitOptionsEvidencePolicy":
        if int(self.min_prior_iv_samples) < 2:
            raise ValueError("min_prior_iv_samples must be >= 2")
        if int(self.iv_lookback_days) <= 0:
            raise ValueError("iv_lookback_days must be > 0")
        if int(self.max_snapshot_age_seconds) <= 0:
            raise ValueError("max_snapshot_age_seconds must be > 0")
        if int(self.max_greeks_age_seconds) <= 0:
            raise ValueError("max_greeks_age_seconds must be > 0")
        return self


def _visible_rows(records: Iterable[dict], *, dataset: str, decision_at: datetime) -> list[dict]:
    cutoff = _utc(decision_at)
    rows = []
    for row in records:
        if row.get("dataset") != dataset or row.get("first_seen_at") is None:
            continue
        try:
            seen = _stamp(row["first_seen_at"])
        except (TypeError, ValueError):
            continue
        if seen <= cutoff:
            rows.append(row)
    return sorted(rows, key=lambda row: _stamp(row["first_seen_at"]))


def _latest_fresh(visible: list[dict], *, decision_at: datetime, max_age_seconds: int) -> tuple[dict | None, str, float | None]:
    if not visible:
        return None, "MISSING", None
    latest = visible[-1]
    seen = _stamp(latest["first_seen_at"])
    age = (_utc(decision_at) - seen).total_seconds()
    if age < 0:
        raise AssertionError("future Deribit row escaped PIT visibility filter")
    if age > int(max_age_seconds):
        return None, "STALE", age
    return latest, "READY", age


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


def _empty_evidence(*, decision: datetime, context_status: str, greeks_status: str) -> Evidence:
    if context_status == "STALE":
        status = "STALE_DERIBIT_OPTIONS_CONTEXT"
        reason = "Latest Deribit global BTC options chain snapshot is stale and no fresh Greeks state can replace the missing chain context."
    elif context_status == "MISSING" and greeks_status == "MISSING":
        status = "NO_VISIBLE_DERIBIT_OPTIONS_CONTEXT"
        reason = "No Deribit global BTC options PIT snapshot was visible by the decision time."
    else:
        status = "NO_FRESH_DERIBIT_OPTIONS_CONTEXT"
        reason = "No fresh Deribit BTC options chain or Greeks PIT state was usable by the decision time."
    return Evidence(
        family="BTC_OPTIONS_MARKET",
        causal_origin="OPTIONS_POSITIONING",
        stance="UNKNOWN",
        strength="LOW",
        confidence=0.4,
        observed_at=decision,
        reason=reason,
        context_only=True,
        source="DERIBIT_PIT_GLOBAL_OPTIONS_CONTEXT",
        metadata={
            "status": status,
            "chain_context_status": context_status,
            "greeks_status": greeks_status,
            "skew_25d": None,
            "skew_25d_inferred": False,
            "skew_25d_inferred_from_strike": False,
            "standalone_direction_allowed": False,
            "coindcx_contract_selection_allowed": False,
            "coindcx_quote_fill_allowed": False,
            "coindcx_pnl_replay_allowed": False,
            "trade_generated": False,
        },
    )


def deribit_options_evidence_from_pit_records(
    records: Iterable[dict],
    *,
    decision_at: datetime,
    policy: DeribitOptionsEvidencePolicy | None = None,
) -> Evidence:
    policy = (policy or DeribitOptionsEvidencePolicy()).validated()
    decision = _utc(decision_at)
    rows = list(records)

    visible_context = _visible_rows(rows, dataset=CONTEXT_DATASET, decision_at=decision)
    visible_greeks = _visible_rows(rows, dataset=GREEKS_DATASET, decision_at=decision)
    context_row, context_status, context_age = _latest_fresh(
        visible_context,
        decision_at=decision,
        max_age_seconds=int(policy.max_snapshot_age_seconds),
    )
    greeks_row, greeks_status, greeks_age = _latest_fresh(
        visible_greeks,
        decision_at=decision,
        max_age_seconds=int(policy.max_greeks_age_seconds),
    )
    if context_row is None and greeks_row is None:
        return _empty_evidence(decision=decision, context_status=context_status, greeks_status=greeks_status)

    atm_iv = None
    oi_ratio = None
    term_slope = None
    iv_percentile = None
    prior_count = 0
    context_seen = None
    if context_row is not None:
        context_seen = _stamp(context_row["first_seen_at"])
        payload = context_row.get("payload") if isinstance(context_row.get("payload"), dict) else {}
        atm_iv = _finite_or_none(payload.get("atm_mark_iv_pct"))
        oi_ratio = _finite_or_none(payload.get("put_call_open_interest_ratio"))
        term_slope = _finite_or_none(payload.get("term_structure_slope_iv_points"))
        if atm_iv is None or atm_iv <= 0:
            raise ValueError("visible Deribit options chain snapshot lacks valid ATM mark IV")
        if oi_ratio is not None and oi_ratio < 0:
            raise ValueError("visible Deribit put/call OI ratio cannot be negative")
        iv_percentile, prior_count = _iv_percentile(
            current_iv=atm_iv,
            current_seen=context_seen,
            visible=visible_context,
            policy=policy,
        )

    skew_25d = None
    greeks_seen = None
    greeks_payload = {}
    if greeks_row is not None:
        greeks_seen = _stamp(greeks_row["first_seen_at"])
        greeks_payload = greeks_row.get("payload") if isinstance(greeks_row.get("payload"), dict) else {}
        skew_25d = _finite_or_none(greeks_payload.get("put_call_skew_25d_iv_points"))
        if skew_25d is None:
            raise ValueError("visible Deribit Greeks snapshot lacks valid 25d skew")
        if greeks_payload.get("skew_25d_observed_from_ticker_delta") is not True:
            raise ValueError("Deribit 25d skew must be backed by observed ticker delta")
        if greeks_payload.get("skew_25d_inferred_from_strike") is True:
            raise ValueError("strike-inferred delta/skew is forbidden")

    observed_at = max(value for value in (context_seen, greeks_seen) if value is not None)
    evidence = options_market_context(BtcOptionsMarketSnapshot(
        observed_at=observed_at,
        atm_iv_percentile=iv_percentile,
        put_call_skew_25d=skew_25d,
        put_call_oi_ratio=oi_ratio,
        term_structure_slope=term_slope,
        source="DERIBIT_PIT_GLOBAL_OPTIONS_CONTEXT",
    ))
    metadata = dict(evidence.metadata)
    metadata.update({
        "status": "DERIBIT_OPTIONS_CONTEXT_READY",
        "chain_context_status": context_status,
        "greeks_status": greeks_status,
        "chain_snapshot_age_seconds": context_age,
        "greeks_snapshot_age_seconds": greeks_age,
        "raw_atm_mark_iv_pct": atm_iv,
        "prior_iv_sample_count": prior_count,
        "iv_percentile_ready": iv_percentile is not None,
        "iv_percentile_point_in_time": iv_percentile,
        "iv_lookback_days": int(policy.iv_lookback_days),
        "put_call_open_interest_ratio": oi_ratio,
        "term_structure_slope_iv_points": term_slope,
        "skew_25d": skew_25d,
        "skew_25d_inferred": False,
        "skew_25d_observed_from_ticker_delta": skew_25d is not None,
        "skew_25d_inferred_from_strike": False,
        "greeks_call_instrument": (greeks_payload.get("call") or {}).get("instrument_name") if isinstance(greeks_payload.get("call"), dict) else None,
        "greeks_put_instrument": (greeks_payload.get("put") or {}).get("instrument_name") if isinstance(greeks_payload.get("put"), dict) else None,
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
        "version": "DERIBIT_BTC_OPTIONS_EVIDENCE_V2",
        "chain_and_greeks_have_independent_first_seen_state": True,
        "uses_only_visible_first_seen_snapshots": True,
        "iv_percentile_uses_only_prior_visible_history": True,
        "insufficient_iv_history_invents_percentile": False,
        "skew_25d_uses_observed_ticker_delta": True,
        "skew_25d_inferred": False,
        "skew_25d_inferred_from_strike": False,
        "stale_chain_may_be_replaced_by_fresh_greeks": False,
        "stale_greeks_may_be_carried_forward": False,
        "underlying_direction_vote_allowed": False,
        "global_options_context_only": True,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
