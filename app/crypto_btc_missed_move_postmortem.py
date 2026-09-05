"""Postmortem framework for BTC NO-TRADE decisions followed by large moves.

This module is retrospective learning only. It preserves the frozen click-time
decision and classifies later research findings by whether they were genuinely
available at the click, missing because of a data-coverage gap, or only emerged
after the click. Recommendations are hypotheses for later out-of-sample testing;
they never rewrite historical decisions or auto-retune production logic.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Literal

FindingFamily = Literal[
    "SPOT_STRUCTURE",
    "DERIVATIVES_POSITIONING",
    "ONCHAIN",
    "STABLECOIN",
    "NEWS",
    "SOCIAL",
    "MACRO",
    "OPTIONS_CONTEXT",
    "DATA_QUALITY",
    "OTHER",
]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bounded(name: str, value: float) -> float:
    value = float(value)
    if not isfinite(value) or value < 0 or value > 1:
        raise ValueError(f"{name} must be finite within 0..1")
    return value


@dataclass(frozen=True)
class BtcMissedMoveFinding:
    family: FindingFamily
    first_seen_at: datetime
    summary: str
    confidence: float
    independent_source_count: int
    source_tier: str
    verified: bool = True
    material_to_move: bool = True

    def validated(self) -> "BtcMissedMoveFinding":
        if str(self.family).upper() not in {
            "SPOT_STRUCTURE", "DERIVATIVES_POSITIONING", "ONCHAIN", "STABLECOIN",
            "NEWS", "SOCIAL", "MACRO", "OPTIONS_CONTEXT", "DATA_QUALITY", "OTHER",
        }:
            raise ValueError("unsupported finding family")
        if not str(self.summary or "").strip():
            raise ValueError("finding summary is required")
        _bounded("confidence", self.confidence)
        if int(self.independent_source_count) < 1:
            raise ValueError("independent_source_count must be >= 1")
        if not str(self.source_tier or "").strip():
            raise ValueError("source_tier is required")
        return self


_FAMILY_TO_LANE = {
    "SPOT_STRUCTURE": "BTC_SPOT_STRUCTURE",
    "DERIVATIVES_POSITIONING": "DERIVATIVES",
    "ONCHAIN": "ONCHAIN",
    "STABLECOIN": "STABLECOINS",
    "NEWS": "NEWS",
    "SOCIAL": "SOCIAL",
    "MACRO": "MACRO_CROSS_ASSET",
    "OPTIONS_CONTEXT": "BTC_OPTIONS_MARKET",
    "DATA_QUALITY": "DATA_QUALITY",
    "OTHER": "OTHER",
}


def _classify_finding(*, finding: BtcMissedMoveFinding, experience_entry: dict) -> dict:
    finding.validated()
    decision_at = datetime.fromisoformat(str(experience_entry["decision_at"]).replace("Z", "+00:00"))
    decision_at = _utc(decision_at)
    first_seen = _utc(finding.first_seen_at)
    lane = _FAMILY_TO_LANE[str(finding.family).upper()]
    missing_lanes = {str(x).upper() for x in experience_entry.get("missing_lanes", [])}
    available_lanes = {str(x).upper() for x in experience_entry.get("available_lanes", [])}

    existed_at_click = first_seen <= decision_at
    lane_missing = lane.upper() in missing_lanes
    lane_available = lane.upper() in available_lanes

    if not existed_at_click:
        classification = "EMERGED_AFTER_CLICK"
    elif lane_missing:
        classification = "DATA_MISSING_AT_CLICK"
    elif lane_available:
        classification = "AVAILABLE_AT_CLICK"
    else:
        classification = "AVAILABILITY_UNCERTAIN_AT_CLICK"

    return {
        **asdict(finding),
        "first_seen_at": first_seen.isoformat(),
        "mapped_lane": lane,
        "existed_at_click": existed_at_click,
        "lane_missing_at_click": lane_missing,
        "lane_available_at_click": lane_available,
        "availability_classification": classification,
        "historical_decision_rewritten": False,
    }


def build_missed_move_postmortem(
    *,
    experience_entry: dict,
    findings: list[BtcMissedMoveFinding],
    min_actionable_confidence: float = 0.70,
) -> dict:
    """Build a hypothesis-only postmortem for a missed large BTC move."""
    _bounded("min_actionable_confidence", min_actionable_confidence)
    if str(experience_entry.get("instrument_type", "")).upper() != "OPTIONS":
        raise ValueError("BTC missed-move postmortem is scoped to the Options experience ledger")
    if experience_entry.get("futures_route_invoked") is True or experience_entry.get("futures_trade_generated") is True:
        raise ValueError("postmortem rejects Futures-route experience state")
    if experience_entry.get("final_decision") != "NO_TRADE":
        raise ValueError("missed-move postmortem requires a frozen NO_TRADE decision")
    if experience_entry.get("outcome_type") != "NO_TRADE_LEARNING":
        raise ValueError("missed-move postmortem requires NO_TRADE_LEARNING experience entry")

    follow = experience_entry.get("no_trade_follow_through")
    if not isinstance(follow, dict):
        raise ValueError("NO_TRADE follow-through payload is required")
    if follow.get("large_move_missed") is not True:
        return {
            "version": "BTC_MISSED_MOVE_POSTMORTEM_V1",
            "click_id": experience_entry.get("click_id"),
            "status": "POSTMORTEM_NOT_REQUIRED",
            "reason": "NO_TRADE was not followed by a move above the configured learning threshold.",
            "historical_decision_rewritten": False,
            "automatic_strategy_change_allowed": False,
            "futures_route_invoked": False,
        }

    classified = [_classify_finding(finding=row, experience_entry=experience_entry) for row in findings]
    material_verified = [
        row for row in classified
        if row["verified"] is True
        and row["material_to_move"] is True
        and float(row["confidence"]) >= float(min_actionable_confidence)
    ]
    available = [row for row in material_verified if row["availability_classification"] == "AVAILABLE_AT_CLICK"]
    missing = [row for row in material_verified if row["availability_classification"] == "DATA_MISSING_AT_CLICK"]
    post_click = [row for row in material_verified if row["availability_classification"] == "EMERGED_AFTER_CLICK"]
    uncertain = [row for row in material_verified if row["availability_classification"] == "AVAILABILITY_UNCERTAIN_AT_CLICK"]

    if available:
        primary = "POTENTIAL_UNDERWEIGHTED_OR_GATED_SIGNAL"
        hypothesis = (
            "At least one high-confidence material signal appears to have been available at the click; "
            "investigate whether weighting, confirmation, horizon matching, or a gate suppressed it."
        )
    elif missing:
        primary = "POTENTIAL_DATA_COVERAGE_GAP"
        hypothesis = (
            "At least one high-confidence material signal existed by the click but its mapped lane was marked missing; "
            "investigate collection, normalization, latency, or availability coverage."
        )
    elif post_click:
        primary = "LIKELY_POST_CLICK_CATALYST"
        hypothesis = (
            "Material verified evidence in this postmortem first appeared after the click; do not treat it as a miss "
            "unless earlier precursors are independently demonstrated."
        )
    elif uncertain:
        primary = "CLICK_TIME_AVAILABILITY_UNCERTAIN"
        hypothesis = "Material evidence may have existed, but the ledger cannot prove whether its lane was available at the click."
    else:
        primary = "CAUSE_NOT_ESTABLISHED"
        hypothesis = "Current findings do not establish a sufficiently verified, material explanation for the missed move."

    investigation = []
    if available:
        investigation.extend(["WEIGHTING", "CONFIRMATION_GATES", "HORIZON_MATCHING", "REGIME_CLASSIFICATION"])
    if missing:
        investigation.extend(["DATA_COVERAGE", "INGESTION_LATENCY", "ENTITY_OR_SOURCE_NORMALIZATION"])
    if post_click:
        investigation.append("PRECURSOR_SEARCH_WITH_STRICT_POINT_IN_TIME_CUTOFF")
    if uncertain:
        investigation.append("RECONSTRUCT_CLICK_TIME_INFORMATION_BOARD")
    if not investigation:
        investigation.append("EXPAND_CAUSAL_RESEARCH_WITHOUT_RETUNING")

    return {
        "version": "BTC_MISSED_MOVE_POSTMORTEM_V1",
        "click_id": experience_entry.get("click_id"),
        "asset": "BTC",
        "instrument_type": "OPTIONS",
        "status": "POSTMORTEM_READY",
        "missed_move_classification": follow.get("classification"),
        "missed_direction": follow.get("missed_direction"),
        "max_abs_move_pct": follow.get("max_abs_move_pct"),
        "decision_at": experience_entry.get("decision_at"),
        "frozen_decision": "NO_TRADE",
        "decision_reason_codes": list(experience_entry.get("reason_codes", [])),
        "available_lanes_at_click": list(experience_entry.get("available_lanes", [])),
        "missing_lanes_at_click": list(experience_entry.get("missing_lanes", [])),
        "primary_learning_classification": primary,
        "learning_hypothesis": hypothesis,
        "investigation_dimensions": sorted(set(investigation)),
        "findings": classified,
        "high_confidence_material_finding_count": len(material_verified),
        "available_at_click_material_count": len(available),
        "missing_at_click_material_count": len(missing),
        "post_click_material_count": len(post_click),
        "availability_uncertain_material_count": len(uncertain),
        "recommendation_status": "HYPOTHESIS_ONLY_REQUIRES_OUT_OF_SAMPLE_TEST",
        "historical_decision_rewritten": False,
        "automatic_strategy_change_allowed": False,
        "automatic_weight_change_allowed": False,
        "automatic_gate_change_allowed": False,
        "outcome_may_be_used_to_select_new_rule_without_retest": False,
        "futures_data_may_be_investigated_as_context": True,
        "futures_trade_may_be_generated_from_postmortem": False,
        "futures_route_invoked": False,
        "broker_execution_enabled": False,
        "research_only": True,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_MISSED_MOVE_POSTMORTEM_CONTRACT_V1",
        "requires_no_trade_followed_by_large_move": True,
        "preserves_frozen_historical_decision": True,
        "distinguishes_preclick_missing_and_postclick_evidence": True,
        "postclick_catalyst_cannot_be_counted_as_available_at_click": True,
        "recommendations_are_hypotheses_only": True,
        "automatic_retuning_allowed": False,
        "out_of_sample_retest_required_before_strategy_change": True,
        "futures_context_may_be_investigated": True,
        "futures_trade_generation_allowed": False,
        "broker_execution_enabled": False,
        "research_only": True,
    }
