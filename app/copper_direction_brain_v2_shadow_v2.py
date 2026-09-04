from __future__ import annotations

from .copper_direction_brain_v2_shadow import (
    DIRECTIONAL,
    INDEPENDENT_FAMILIES,
    MODE,
    _family,
    _thesis,
    china_demand_family,
    context_modifiers,
    event_reaction_family,
    experience_memory_family,
    global_copper_family,
    local_structure_family,
)
from .copper_option_participation_v1 import RULE_VERSION as OPTION_PARTICIPATION_RULE_VERSION


CONTRACT_VERSION = "COPPER_DIRECTION_BRAIN_V2_SHADOW_V2"


def option_participation_family(board: dict) -> dict:
    option = (
        (((board.get("groups") or {}).get("option_market") or {}).get("MCX_COPPER_OPTION"))
        or {}
    )
    snapshot = option.get("participation_snapshot") or {}
    if option.get("status") != "AVAILABLE":
        return _family(
            "OPTION_PARTICIPATION",
            origin="OPTION_MARKET_POSITIONING",
            state="NO_FIRST_SEEN_OPTION_SNAPSHOT",
            detail={
                "status": option.get("status"),
                "participation_status": snapshot.get("status"),
                "rule_version": OPTION_PARTICIPATION_RULE_VERSION,
            },
        )
    if snapshot.get("status") != "READY":
        return _family(
            "OPTION_PARTICIPATION",
            origin="OPTION_MARKET_POSITIONING",
            state="OPTION_PARTICIPATION_NOT_READY",
            detail={
                "status": option.get("status"),
                "participation_status": snapshot.get("status"),
                "reason": snapshot.get("reason") or "NO_REGISTERED_CHANGE_SNAPSHOT",
                "rule_version": snapshot.get("rule_version") or OPTION_PARTICIPATION_RULE_VERSION,
                "latest_bucket_at": snapshot.get("latest_bucket_at"),
                "previous_bucket_at": snapshot.get("previous_bucket_at"),
                "bucket_gap_seconds": snapshot.get("bucket_gap_seconds"),
                "matched_ce_contracts": snapshot.get("matched_ce_contracts"),
                "matched_pe_contracts": snapshot.get("matched_pe_contracts"),
                "raw_oi_level_directional_vote_allowed": False,
                "underlying_price_direction_used": False,
            },
        )

    evidence = [
        row for row in (snapshot.get("contract_evidence") or [])
        if row.get("eligible_new_oi_evidence")
        and str(row.get("stance") or "").upper() in DIRECTIONAL
        and str(row.get("option_type") or "").upper() in {"CE", "PE"}
    ]
    bullish = [row for row in evidence if str(row.get("stance")).upper() == "BULLISH"]
    bearish = [row for row in evidence if str(row.get("stance")).upper() == "BEARISH"]

    bullish_ce = [row for row in bullish if str(row.get("option_type")).upper() == "CE"]
    bullish_pe = [row for row in bullish if str(row.get("option_type")).upper() == "PE"]
    bearish_ce = [row for row in bearish if str(row.get("option_type")).upper() == "CE"]
    bearish_pe = [row for row in bearish if str(row.get("option_type")).upper() == "PE"]

    if bullish and bearish:
        stance = "UNKNOWN"
        state = "OPPOSING_NEW_OI_OPTION_EVIDENCE"
    elif bullish_ce and bullish_pe:
        stance = "BULLISH"
        state = "CROSS_SIDE_NEW_OI_BULLISH"
    elif bearish_ce and bearish_pe:
        stance = "BEARISH"
        state = "CROSS_SIDE_NEW_OI_BEARISH"
    else:
        stance = "UNKNOWN"
        state = "INSUFFICIENT_CROSS_SIDE_NEW_OI_CONFIRMATION"

    return _family(
        "OPTION_PARTICIPATION",
        origin="OPTION_MARKET_POSITIONING",
        stance=stance,
        counts=stance in DIRECTIONAL,
        state=state,
        detail={
            "status": option.get("status"),
            "rule_version": snapshot.get("rule_version") or OPTION_PARTICIPATION_RULE_VERSION,
            "latest_bucket_at": snapshot.get("latest_bucket_at"),
            "previous_bucket_at": snapshot.get("previous_bucket_at"),
            "bucket_gap_seconds": snapshot.get("bucket_gap_seconds"),
            "nearest_expiry": snapshot.get("nearest_expiry"),
            "matched_contracts": snapshot.get("matched_contracts"),
            "matched_ce_contracts": snapshot.get("matched_ce_contracts"),
            "matched_pe_contracts": snapshot.get("matched_pe_contracts"),
            "eligible_new_oi_evidence": len(evidence),
            "bullish_ce_evidence": len(bullish_ce),
            "bullish_pe_evidence": len(bullish_pe),
            "bearish_ce_evidence": len(bearish_ce),
            "bearish_pe_evidence": len(bearish_pe),
            "evidence": evidence,
            "raw_oi_level_directional_vote_allowed": False,
            "oi_flat_or_decreasing_directional_vote_allowed": False,
            "underlying_price_direction_used": False,
            "rule": (
                "Only matched latest-vs-previous first-seen contracts with OI increase "
                "may create evidence. A family vote requires aligned CE and PE evidence "
                "with no opposing eligible new-OI evidence."
            ),
        },
    )


def evaluate_copper_direction_v2_shadow(board: dict) -> dict:
    families = [
        local_structure_family(board),
        option_participation_family(board),
        global_copper_family(board),
        china_demand_family(board),
        event_reaction_family(board),
        experience_memory_family(board),
    ]
    thesis = _thesis(families)
    return {
        "mode": MODE,
        "product": "COPPER",
        "trade_instrument": "OPTIONS_ONLY",
        "as_of": board.get("as_of"),
        "research_only": True,
        "shadow_only": True,
        "direction": thesis["direction"],
        "direction_confidence": thesis["confidence"],
        "thesis_state": thesis["state"],
        "supporting_families": thesis["supporting_families"],
        "opposing_families": thesis["opposing_families"],
        "counted_families": thesis["counted_families"],
        "duplicate_causal_origins_suppressed": thesis["duplicate_causal_origins_suppressed"],
        "families": {row["family"]: row for row in families},
        "modifiers": context_modifiers(board),
        "current_mind_action": None,
        "setup_geometry_generated": False,
        "option_expression_generated": False,
        "sealed_current_mind_effect": "NONE",
        "decision_effect": "NONE",
        "option_expression_effect": "NONE",
        "production_rules_changed": False,
        "historical_backfill_used": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
        "promotion_eligible": False,
        "rules": [
            "No weighted indicator score is used.",
            "At least two independent causal origins must align and no counted origin may oppose them.",
            "Local structure and local momentum belong to one LOCAL_STRUCTURE causal family.",
            "Raw option OI levels and one option snapshot cannot vote direction.",
            "Option Participation V1 may vote only from matched latest-vs-previous CE+PE new-OI premium-change evidence.",
            "OI-flat or OI-decreasing option contracts cannot create Option Participation direction.",
            "Underlying price direction is excluded from the Option Participation vote to preserve causal-family independence.",
            "Multiple option strikes are corroboration inside one OPTION_PARTICIPATION family, never separate Direction V2 families.",
            "Price-plus-volume alone is not an independent Participation direction family.",
            "China macro levels are slow context until an event-surprise plus PIT price-reaction rule is separately registered.",
            "COMEX/LME require timestamp-safe first-seen global data before Global Copper may vote.",
            "Headline sentiment cannot vote; event direction requires mechanism, materiality, novelty and confirmed PIT reaction.",
            "Daily FX and weekly CFTC positioning are modifiers/context only.",
            "Experience Memory may vote only under a separately registered, outcome-availability-safe prospective memory contract.",
            "Direction does not imply entry readiness or an option trade.",
        ],
        "integration_contract": integration_contract(),
    }


def integration_contract() -> dict:
    return {
        "version": CONTRACT_VERSION,
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_expression_effect": "NONE",
        "production_rules_changed": False,
        "independent_direction_families": list(INDEPENDENT_FAMILIES),
        "causal_origin_deduplication": True,
        "minimum_independent_confirmations": 2,
        "raw_option_oi_directional_vote_allowed": False,
        "legacy_price_volume_participation_vote_allowed": False,
        "option_participation_rule_version": OPTION_PARTICIPATION_RULE_VERSION,
        "option_participation_directional_vote_allowed": True,
        "option_participation_requires_new_oi": True,
        "option_participation_requires_cross_side_ce_pe": True,
        "option_participation_oi_flat_or_decrease_vote_allowed": False,
        "option_participation_underlying_direction_vote_allowed": False,
        "absolute_macro_level_directional_vote_allowed": False,
        "headline_sentiment_direction_allowed": False,
        "slow_fx_directional_vote_allowed": False,
        "weekly_cftc_directional_vote_allowed": False,
        "public_delayed_comex_substitution_allowed": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "promotion_allowed": False,
    }
