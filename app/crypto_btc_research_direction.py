"""Research-only BTC directional lean for dense historical replay.

The live/prospective Market Brain deliberately requires two independent causal
origins before it exposes a trade-quality BULLISH/BEARISH thesis.  A directional
research experiment asks a different question: given only evidence genuinely
visible at a historical click, did the information available then lean UP or
DOWN over the next interval?

This module answers that narrower question without changing the production
market-state gate.  Missing lanes are neutral, context-only rows cannot create
direction, and causal origins are de-duplicated before scoring.  The resulting
lean is fingerprinted, research-only, non-tradeable, and contains no future
prices or option/futures execution data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from app.crypto_market_intelligence import Evidence, evidence_is_fresh

_STRENGTH_WEIGHT = {"LOW": 0.60, "MEDIUM": 1.00, "HIGH": 1.30}
_MIN_NORMALIZED_EDGE = 0.20


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _weight(row: Evidence) -> float:
    strength = _STRENGTH_WEIGHT.get(str(row.strength).upper(), 0.60)
    confidence = max(0.0, min(1.0, float(row.confidence)))
    return strength * confidence


def derive_btc_research_directional_lean(
    evidence: list[Evidence],
    *,
    decision_at: datetime,
    trade_horizon: str = "intraday",
) -> dict[str, Any]:
    """Return an outcome-blind UP/DOWN/WAIT research lean.

    Unlike ``assemble_market_state`` this does not require two aligned origins.
    That relaxation exists only inside this explicitly non-tradeable research
    signal.  The production gate is not called or modified here.
    """
    decision = _utc(decision_at)
    fresh = [
        row
        for row in evidence
        if evidence_is_fresh(row, decision_at=decision, trade_horizon=trade_horizon)
    ]

    context_rows = [
        row
        for row in fresh
        if row.context_only or row.stance not in {"BULLISH", "BEARISH"}
    ]
    directional = [
        row
        for row in fresh
        if not row.context_only and row.stance in {"BULLISH", "BEARISH"}
    ]

    # One causal phenomenon gets at most one vote.  If two providers somehow
    # disagree inside the same origin, fail closed for that origin rather than
    # selecting whichever row happens to have the larger confidence.
    by_origin: dict[str, list[Evidence]] = {}
    for row in directional:
        by_origin.setdefault(str(row.causal_origin or "UNKNOWN"), []).append(row)

    contributors: list[Evidence] = []
    conflicted_origins: list[str] = []
    duplicate_rows_ignored = 0
    for origin, rows in sorted(by_origin.items()):
        stances = {str(row.stance) for row in rows}
        if len(stances) > 1:
            conflicted_origins.append(origin)
            duplicate_rows_ignored += len(rows)
            continue
        chosen = max(
            rows,
            key=lambda row: (
                _weight(row),
                _utc(row.observed_at),
                str(row.source),
            ),
        )
        contributors.append(chosen)
        duplicate_rows_ignored += max(0, len(rows) - 1)

    signed: list[tuple[Evidence, float]] = []
    for row in contributors:
        magnitude = _weight(row)
        signed.append((row, magnitude if row.stance == "BULLISH" else -magnitude))

    raw_score = sum(score for _, score in signed)
    gross_score = sum(abs(score) for _, score in signed)
    normalized_edge = 0.0 if gross_score <= 0 else abs(raw_score) / gross_score

    if gross_score <= 0 or normalized_edge < _MIN_NORMALIZED_EDGE or raw_score == 0:
        direction = "WAIT"
    else:
        direction = "UP" if raw_score > 0 else "DOWN"

    aligned = 0
    if direction == "UP":
        aligned = sum(row.stance == "BULLISH" for row, _ in signed)
    elif direction == "DOWN":
        aligned = sum(row.stance == "BEARISH" for row, _ in signed)

    if direction == "WAIT":
        confidence_band = "NONE"
    elif aligned >= 3 and normalized_edge >= 0.75:
        confidence_band = "HIGH"
    elif aligned >= 2 and normalized_edge >= 0.50:
        confidence_band = "MEDIUM"
    else:
        confidence_band = "LOW"

    contributor_rows = [
        {
            "family": row.family,
            "causal_origin": row.causal_origin,
            "stance": row.stance,
            "strength": row.strength,
            "confidence": round(float(row.confidence), 4),
            "score": round(score, 6),
            "source": row.source,
            "observed_at": _utc(row.observed_at).isoformat(),
            "reason": row.reason,
        }
        for row, score in signed
    ]
    payload: dict[str, Any] = {
        "version": "BTC_RESEARCH_DIRECTIONAL_LEAN_V1",
        "decision_at": decision.isoformat(),
        "direction": direction,
        "confidence_band": confidence_band,
        "raw_score": round(raw_score, 6),
        "gross_score": round(gross_score, 6),
        "normalized_edge": round(normalized_edge, 6),
        "minimum_normalized_edge": _MIN_NORMALIZED_EDGE,
        "independent_directional_origins": len(contributors),
        "context_rows_available": len(context_rows),
        "fresh_evidence_rows": len(fresh),
        "conflicted_origins": conflicted_origins,
        "duplicate_rows_ignored": duplicate_rows_ignored,
        "contributors": contributor_rows,
        "missing_evidence_penalty": 0.0,
        "context_only_may_create_direction": False,
        "single_origin_may_create_research_lean": True,
        "production_two_origin_gate_changed": False,
        "future_prices_used": False,
        "research_only": True,
        "tradeable": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed_inr": 0,
    }
    return {**payload, "decision_fingerprint": _fingerprint(payload)}


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "BTC_RESEARCH_DIRECTIONAL_LEAN_CONTRACT_V1",
        "production_market_state_gate_changed": False,
        "production_two_origin_requirement_preserved": True,
        "missing_lanes_are_negative_votes": False,
        "context_only_rows_may_create_direction": False,
        "same_causal_origin_double_counted": False,
        "same_origin_conflict_fails_closed": True,
        "single_valid_origin_may_create_research_lean": True,
        "outcome_data_used_for_lean": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "research_only": True,
    }
