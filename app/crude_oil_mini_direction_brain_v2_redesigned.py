from __future__ import annotations

from .crude_oil_mini_direction_memory import query_direction_memory
from .crude_oil_mini_evidence_dependency import audit_directional_independence
from .crude_oil_mini_event_lifecycle import event_lifecycle_view
from .crude_oil_mini_global_crude_perception_v2 import build_global_crude_perception
from .crude_oil_mini_participation_v2 import build_participation_observation
from .crude_oil_mini_point_in_time_context import latest_known_as_of

MODE = "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_REDESIGNED_SHADOW"
DIRECTIONAL = {"BULLISH", "BEARISH"}
INDEPENDENT_FAMILIES = (
    "LOCAL_STRUCTURE",
    "PARTICIPATION",
    "GLOBAL_CRUDE",
    "EVENT_REACTION",
    "DIRECTION_MEMORY",
)


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _number_direction(value) -> str:
    value = _f(value)
    if value is None or value == 0:
        return "UNKNOWN"
    return "BULLISH" if value > 0 else "BEARISH"


def _record_stance(record: dict | None) -> str:
    if not isinstance(record, dict):
        return "UNKNOWN"
    candidates = [record.get("stance"), record.get("direction")]
    value = record.get("value")
    if isinstance(value, dict):
        candidates.extend((value.get("stance"), value.get("direction")))
    for raw in candidates:
        stance = str(raw or "UNKNOWN").upper()
        if stance in DIRECTIONAL:
            return stance
    return "UNKNOWN"


def _local_structure(snapshot: dict) -> dict:
    structure = str(snapshot.get("structure") or "UNKNOWN").upper()
    structure_stance = (
        "BULLISH" if structure == "UPTREND"
        else "BEARISH" if structure == "DOWNTREND"
        else "UNKNOWN"
    )
    momentum_15 = _number_direction(snapshot.get("return_15m_pct"))
    momentum_60 = _number_direction(snapshot.get("return_60m_pct"))
    momentum = [item for item in (momentum_15, momentum_60) if item in DIRECTIONAL]

    if structure_stance in DIRECTIONAL:
        if any(item != structure_stance for item in momentum):
            stance = "UNKNOWN"
            state = "INTERNAL_CONTRADICTION"
        elif any(item == structure_stance for item in momentum):
            stance = structure_stance
            state = "STRUCTURE_CONFIRMED_BY_MOMENTUM"
        else:
            stance = structure_stance
            state = "STRUCTURE_ONLY"
    elif len(momentum) == 2 and len(set(momentum)) == 1:
        stance = momentum[0]
        state = "MOMENTUM_COHERENT_WITHOUT_STRUCTURE"
    else:
        stance = "UNKNOWN"
        state = "NO_COHERENT_LOCAL_DIRECTION"

    directional = stance in DIRECTIONAL
    return {
        "family": "LOCAL_STRUCTURE",
        "causal_origin": "LOCAL_PRICE_STRUCTURE",
        "independence_status": "INDEPENDENT" if directional else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": directional,
        "stance": stance,
        "state": state,
        "detail": {
            "structure": structure,
            "return_15m_pct": _f(snapshot.get("return_15m_pct")),
            "return_60m_pct": _f(snapshot.get("return_60m_pct")),
            "momentum_15m": momentum_15,
            "momentum_60m": momentum_60,
        },
    }


def _event_family(event_records: list[dict], click_timestamp: str) -> dict:
    lifecycle = event_lifecycle_view(event_records or [], click_timestamp)
    eligible = lifecycle.get("direction_eligible_events") or []
    stances = {
        str(row.get("mechanism_stance") or "UNKNOWN").upper()
        for row in eligible
        if str(row.get("mechanism_stance") or "UNKNOWN").upper() in DIRECTIONAL
    }

    if len(stances) > 1:
        stance = "UNKNOWN"
        state = "ACTIVE_EVENT_DIRECTION_CONFLICT"
        counts = False
    elif len(stances) == 1:
        stance = next(iter(stances))
        state = "PIT_REACTION_CONFIRMED_EVENT"
        counts = True
    else:
        stance = "UNKNOWN"
        state = "CONTEXT_ONLY_OR_NO_DIRECTION_ELIGIBLE_EVENT"
        counts = False

    return {
        "family": "EVENT_REACTION",
        "causal_origin": "EXOGENOUS_INFORMATION",
        "independence_status": "INDEPENDENT" if counts else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": counts,
        "stance": stance,
        "state": state,
        "detail": {
            "visible_event_count": lifecycle.get("visible_event_count", 0),
            "active_context_count": lifecycle.get("active_context_count", 0),
            "direction_eligible_count": lifecycle.get("direction_eligible_count", 0),
            "direction_eligible_event_ids": [row.get("event_id") for row in eligible],
            "lifecycle": lifecycle,
        },
    }


def _memory_family(memory_cases: list[dict], snapshot: dict, click_timestamp: str) -> dict:
    result = query_direction_memory(memory_cases or [], snapshot, click_timestamp)
    stance = str(result.get("stance") or "UNKNOWN").upper()
    directional = stance in DIRECTIONAL
    return {
        "family": "DIRECTION_MEMORY",
        "causal_origin": "HISTORICAL_ANALOGUE",
        "independence_status": "INDEPENDENT" if directional else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": directional,
        "stance": stance if directional else "UNKNOWN",
        "state": result.get("status"),
        "detail": result,
    }


def _fx_translation(context_records: list[dict], click_timestamp: str, global_crude: dict) -> dict:
    latest = latest_known_as_of(context_records or [], click_timestamp)
    usd_inr = _record_stance(latest.get("USDINR"))
    global_stance = str(global_crude.get("stance") or "UNKNOWN").upper()

    if usd_inr not in DIRECTIONAL or global_stance not in DIRECTIONAL:
        state = "UNRESOLVED"
    elif usd_inr == global_stance:
        state = "REINFORCES_GLOBAL_CRUDE"
    else:
        state = "OPPOSES_GLOBAL_CRUDE"

    return {
        "family": "FX_TRANSLATION",
        "causal_origin": "CURRENCY_TRANSLATION",
        "independence_status": "MODIFIER_ONLY",
        "depends_on": ["CROSS_MARKET_CRUDE"],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": state,
        "detail": {
            "global_crude_stance": global_stance,
            "usd_inr_stance": usd_inr,
            "rule": "USDINR can reinforce or offset MCX translation context but cannot create or reverse the underlying direction thesis by itself.",
        },
    }


def _build_thesis(families: list[dict]) -> dict:
    audit = audit_directional_independence(families)
    counted = audit["counted"]
    bullish = [row["family"] for row in counted if row.get("stance") == "BULLISH"]
    bearish = [row["family"] for row in counted if row.get("stance") == "BEARISH"]

    if bullish and bearish:
        return {
            "direction": "UNKNOWN",
            "confidence": "CONFLICTED",
            "state": "INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION",
            "supporting_families": [],
            "opposing_families": sorted(bullish + bearish),
            "dependency_audit": audit,
        }

    supporting = bullish or bearish
    if len(supporting) < 2:
        return {
            "direction": "UNKNOWN",
            "confidence": "WEAK",
            "state": "INSUFFICIENT_INDEPENDENT_CONFIRMATION",
            "supporting_families": sorted(supporting),
            "opposing_families": [],
            "dependency_audit": audit,
        }

    return {
        "direction": "BULLISH" if bullish else "BEARISH",
        "confidence": "STRONG" if len(supporting) >= 3 else "MODERATE",
        "state": "COHERENT_DIRECTION_THESIS",
        "supporting_families": sorted(supporting),
        "opposing_families": [],
        "dependency_audit": audit,
    }


def evaluate_redesigned_direction_brain_v2_shadow(
    *,
    click_timestamp: str,
    snapshot: dict,
    profile: dict,
    mini_candles,
    context_probe: dict,
    context_records: list[dict] | None = None,
    event_records: list[dict] | None = None,
    direction_memory_cases: list[dict] | None = None,
) -> dict:
    """Build the redesigned direction thesis without changing any acting trade path."""
    local = _local_structure(snapshot or {})
    participation = build_participation_observation(
        mini_candles,
        click_timestamp=click_timestamp,
        snapshot=snapshot or {},
        profile=profile or {},
    )
    global_crude = build_global_crude_perception(context_probe or {}, click_timestamp)
    event = _event_family(event_records or [], click_timestamp)
    memory = _memory_family(direction_memory_cases or [], snapshot or {}, click_timestamp)
    families = [local, participation, global_crude, event, memory]
    thesis = _build_thesis(families)
    fx = _fx_translation(context_records or [], click_timestamp, global_crude)

    return {
        "mode": MODE,
        "research_only": True,
        "shadow_only": True,
        "decision_path_changed": False,
        "legacy_direction_v2_changed": False,
        "current_mind_action": None,
        "option_brain_action": None,
        "geometry_generated": False,
        "promotion_allowed": False,
        "click_timestamp": click_timestamp,
        "direction": thesis["direction"],
        "direction_confidence": thesis["confidence"],
        "thesis_state": thesis["state"],
        "supporting_families": thesis["supporting_families"],
        "opposing_families": thesis["opposing_families"],
        "dependency_audit": thesis["dependency_audit"],
        "families": {row["family"]: row for row in families},
        "modifiers": {"FX_TRANSLATION": fx},
        "persistence": (memory.get("detail") or {}).get("persistence", "UNRESOLVED"),
        "entry_readiness": "NOT_EVALUATED_DIRECTION_ONLY",
        "rules": [
            "No weighted indicator score is used.",
            "At least two independent causal origins must align with no opposing independent origin.",
            "Price plus volume alone cannot make Participation an independent direction vote.",
            "Participation direction requires independent positioning evidence plus acceptance.",
            "WTI and Brent remain one correlated GLOBAL_CRUDE family and both must qualify and agree.",
            "Event archive visibility is not the same as active directional relevance.",
            "Event direction requires explicit mechanism, materiality, novelty, and confirmed PIT reaction.",
            "USDINR is a translation modifier only.",
            "Direction memory is geometry-independent and may vote only from historically available resolved analogue outcomes.",
            "Direction does not imply setup confirmation, entry readiness, risk geometry, or an option action.",
        ],
        "integration_contract": integration_contract(),
    }


def integration_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_REDESIGNED_INTEGRATION_V1",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "legacy_direction_v2_effect": "NONE",
        "geometry_effect": "NONE",
        "option_effect": "NONE",
        "participation_requires_positioning_and_acceptance": True,
        "causal_origin_deduplication": True,
        "wti_and_brent_are_one_family": True,
        "global_crude_single_hour_sign_allowed": False,
        "event_lifecycle_required": True,
        "headline_sentiment_vote_allowed": False,
        "future_reaction_backfill_allowed": False,
        "fx_is_modifier_only": True,
        "inspected_august_threshold_search_allowed": False,
        "inspected_august_promotion_allowed": False,
        "evaluation_protocol_defined_here": False,
        "fixed_primary_horizon_defined_here": False,
        "prospective_untouched_validation_required_for_promotion": True,
    }
