from __future__ import annotations

from .crude_oil_mini_direction_memory import query_direction_memory
from .crude_oil_mini_evidence_dependency import audit_directional_independence
from .crude_oil_mini_event_reaction_v2 import (
    build_event_reaction_family,
    build_event_reaction_family_from_records,
)
from .crude_oil_mini_point_in_time_context import latest_known_as_of

MODE = "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_SHADOW"
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
    except (TypeError, ValueError):
        return default


def _direction_from_number(value) -> str:
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
    momentum_15 = _direction_from_number(snapshot.get("return_15m_pct"))
    momentum_60 = _direction_from_number(snapshot.get("return_60m_pct"))
    directional_momentum = [stance for stance in (momentum_15, momentum_60) if stance in DIRECTIONAL]

    if structure_stance in DIRECTIONAL:
        opposing = [stance for stance in directional_momentum if stance != structure_stance]
        supporting = [stance for stance in directional_momentum if stance == structure_stance]
        if opposing:
            stance = "UNKNOWN"
            state = "INTERNAL_CONTRADICTION"
        elif supporting:
            stance = structure_stance
            state = "STRUCTURE_CONFIRMED_BY_MOMENTUM"
        else:
            stance = structure_stance
            state = "STRUCTURE_ONLY"
    elif len(directional_momentum) == 2 and len(set(directional_momentum)) == 1:
        stance = directional_momentum[0]
        state = "MOMENTUM_COHERENT_WITHOUT_STRUCTURE"
    else:
        stance = "UNKNOWN"
        state = "NO_COHERENT_LOCAL_DIRECTION"

    return {
        "family": "LOCAL_STRUCTURE",
        "independent": True,
        "causal_origin": "LOCAL_PRICE_STRUCTURE",
        "independence_status": "INDEPENDENT" if stance in DIRECTIONAL else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": stance in DIRECTIONAL,
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


def _participation(participation_observation: dict | None, snapshot: dict, profile: dict) -> dict:
    """Accept only the new commitment/acceptance observation as an independent vote.

    The legacy price+relative-volume proxy is retained as visible diagnostic detail but
    is deliberately suppressed because its direction came from the same 15-minute price
    move already used by LOCAL_STRUCTURE.
    """
    if isinstance(participation_observation, dict):
        row = dict(participation_observation)
        row.setdefault("family", "PARTICIPATION")
        row.setdefault("independent", row.get("independence_status") == "INDEPENDENT")
        row.setdefault("causal_origin", "POSITIONING_FLOW")
        row.setdefault("depends_on", [])
        row.setdefault("counts_for_direction", False)
        row.setdefault("stance", "UNKNOWN")
        row.setdefault("state", "UNKNOWN")
        return row

    relative = _f(snapshot.get("time_adjusted_relative_volume"))
    confirming = _f((profile or {}).get("participation_confirming"))
    momentum = _direction_from_number(snapshot.get("return_15m_pct"))
    legacy_active = (
        relative is not None
        and confirming is not None
        and relative >= confirming
        and momentum in DIRECTIONAL
    )
    return {
        "family": "PARTICIPATION",
        "independent": False,
        "causal_origin": "LOCAL_PRICE_VOLUME",
        "independence_status": "DEPENDENT_ON_LOCAL_PRICE",
        "depends_on": ["LOCAL_PRICE_STRUCTURE"],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": "LEGACY_PRICE_VOLUME_PROXY_SUPPRESSED",
        "detail": {
            "time_adjusted_relative_volume": relative,
            "prior_confirming_level": confirming,
            "legacy_momentum_15m": momentum,
            "legacy_proxy_would_have_voted": legacy_active,
            "reason": "15-minute price direction already belongs to LOCAL_STRUCTURE; price+volume alone is not an independent second vote.",
        },
    }


def _global_crude(latest: dict[str, dict]) -> dict:
    observations = []
    for series in ("WTI_CRUDE", "BRENT_CRUDE"):
        record = latest.get(series)
        stance = _record_stance(record)
        if stance in DIRECTIONAL:
            observations.append((series, stance))

    stances = {stance for _, stance in observations}
    if len(stances) > 1:
        stance = "UNKNOWN"
        state = "WTI_BRENT_CONTRADICTION"
    elif len(stances) == 1:
        stance = next(iter(stances))
        state = "WTI_BRENT_CONFIRMED" if len(observations) == 2 else "PARTIAL_GLOBAL_CRUDE"
    else:
        stance = "UNKNOWN"
        state = "GLOBAL_CRUDE_UNAVAILABLE_OR_NON_DIRECTIONAL"

    return {
        "family": "GLOBAL_CRUDE",
        "independent": True,
        "causal_origin": "CROSS_MARKET_CRUDE",
        "independence_status": "INDEPENDENT" if stance in DIRECTIONAL else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": stance in DIRECTIONAL,
        "stance": stance,
        "state": state,
        "detail": {
            "directional_observations": [
                {"series": series, "stance": item_stance}
                for series, item_stance in observations
            ],
            "breadth": len(observations),
            "wti_brent_collapsed_into_one_vote": True,
        },
    }


def _fx_translation(latest: dict[str, dict], global_crude: dict) -> dict:
    usd_inr = _record_stance(latest.get("USDINR"))
    global_stance = global_crude.get("stance")
    if usd_inr not in DIRECTIONAL or global_stance not in DIRECTIONAL:
        state = "UNRESOLVED"
    elif usd_inr == global_stance:
        state = "REINFORCES_GLOBAL_CRUDE"
    else:
        state = "OPPOSES_GLOBAL_CRUDE"
    return {
        "family": "FX_TRANSLATION",
        "independent": False,
        "causal_origin": "CURRENCY_TRANSLATION",
        "independence_status": "MODIFIER_ONLY",
        "depends_on": ["CROSS_MARKET_CRUDE"],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": state,
        "detail": {
            "global_crude_stance": global_stance,
            "usd_inr_stance": usd_inr,
            "rule": "USDINR modifies MCX translation context; it cannot independently create or reverse a Crude direction thesis.",
        },
    }


def _memory_family(memory_cases: list[dict], snapshot: dict, click_timestamp: str) -> dict:
    result = query_direction_memory(memory_cases, snapshot, click_timestamp)
    stance = str(result.get("stance") or "UNKNOWN").upper()
    directional = stance in DIRECTIONAL
    return {
        "family": "DIRECTION_MEMORY",
        "independent": True,
        "causal_origin": "HISTORICAL_ANALOGUE",
        "independence_status": "INDEPENDENT" if directional else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": directional,
        "stance": stance if directional else "UNKNOWN",
        "state": result.get("status"),
        "detail": result,
    }


def _thesis_from_families(families: list[dict]) -> dict:
    dependency_audit = audit_directional_independence(families)
    counted = dependency_audit["counted"]
    bullish = [row["family"] for row in counted if row["stance"] == "BULLISH"]
    bearish = [row["family"] for row in counted if row["stance"] == "BEARISH"]

    if bullish and bearish:
        return {
            "direction": "UNKNOWN",
            "confidence": "CONFLICTED",
            "state": "INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION",
            "supporting_families": [],
            "opposing_families": sorted(bullish + bearish),
            "dependency_audit": dependency_audit,
        }

    supporting = bullish or bearish
    if len(supporting) < 2:
        return {
            "direction": "UNKNOWN",
            "confidence": "WEAK",
            "state": "INSUFFICIENT_INDEPENDENT_CONFIRMATION",
            "supporting_families": sorted(supporting),
            "opposing_families": [],
            "dependency_audit": dependency_audit,
        }

    return {
        "direction": "BULLISH" if bullish else "BEARISH",
        "confidence": "STRONG" if len(supporting) >= 3 else "MODERATE",
        "state": "COHERENT_DIRECTION_THESIS",
        "supporting_families": sorted(supporting),
        "opposing_families": [],
        "dependency_audit": dependency_audit,
    }


def evaluate_direction_brain_v2_shadow(
    *,
    click_timestamp: str,
    snapshot: dict,
    profile: dict,
    context_records: list[dict],
    direction_memory_cases: list[dict] | None = None,
    participation_observation: dict | None = None,
    event_records: list[dict] | None = None,
) -> dict:
    """Build a non-voting Crude direction thesis without touching Current Mind."""
    latest = latest_known_as_of(context_records or [], click_timestamp)
    local = _local_structure(snapshot)
    participation = _participation(participation_observation, snapshot, profile or {})
    global_crude = _global_crude(latest)
    event = (
        build_event_reaction_family_from_records(event_records, click_timestamp)
        if event_records is not None
        else build_event_reaction_family(latest)
    )
    event.setdefault("independent", event.get("independence_status") == "INDEPENDENT")
    memory = _memory_family(direction_memory_cases or [], snapshot, click_timestamp)
    families = [local, participation, global_crude, event, memory]
    fx = _fx_translation(latest, global_crude)
    thesis = _thesis_from_families(families)
    available_context = set(latest)
    if event_records is not None and event.get("detail", {}).get("visible_event_count", 0):
        available_context.add("PIT_EVENT_ARCHIVE")

    return {
        "mode": MODE,
        "research_only": True,
        "shadow_only": True,
        "decision_path_changed": False,
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
        "available_context_series": sorted(available_context),
        "rules": [
            "No weighted indicator score is used.",
            "Directional confidence counts independent causal origins, not merely family names.",
            "The legacy price-plus-volume participation proxy cannot vote because its direction duplicates LOCAL_STRUCTURE price momentum.",
            "Participation can vote only from a separately constructed commitment/acceptance observation with independent positioning evidence.",
            "WTI and Brent are one correlated GLOBAL_CRUDE family, never two votes.",
            "USDINR is a translation modifier and cannot independently create or reverse direction.",
            "Event/news cannot infer direction from headline keywords; material mechanism plus confirmed reaction is required.",
            "All visible PIT archive events may be preserved; same-series headlines are not required to collapse to only the latest one.",
            "A rejected event thesis removes that event vote; rejection does not automatically create the opposite vote.",
            "Direction memory uses future underlying returns only after those horizons became historically available; it never uses trade geometry.",
            "Direction does not imply entry readiness or an option trade.",
        ],
        "validation": preregistration_contract(),
    }


def preregistration_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_SHADOW_PREREG_V1",
        "development_sample_end": "2026-08-31",
        "prospective_validation_not_before": "2026-09-03",
        "primary_horizon_minutes": 60,
        "secondary_horizons_minutes": [15, 30, 120],
        "primary_question": "Does the frozen shadow direction thesis improve 60-minute underlying directional discrimination out of sample?",
        "geometry_tuning_allowed": False,
        "option_pnl_tuning_allowed": False,
        "threshold_search_on_june_august_allowed": False,
        "current_mind_mutation_allowed": False,
        "promotion_allowed_from_june_august": False,
    }


def architecture_contract() -> dict:
    return {
        "mode": MODE,
        "research_only": True,
        "shadow_only": True,
        "current_mind_imported": False,
        "decision_effect": "NONE",
        "geometry_effect": "NONE",
        "option_effect": "NONE",
        "independent_direction_families": list(INDEPENDENT_FAMILIES),
        "causal_origin_deduplication": True,
        "legacy_participation_price_vote_suppressed": True,
        "participation_requires_positioning_and_acceptance": True,
        "correlated_global_crude_collapsed": True,
        "fx_is_modifier_only": True,
        "event_requires_explicit_mechanism_materiality_and_reaction": True,
        "event_headline_sentiment_allowed": False,
        "multiple_visible_events_supported": True,
        "direction_memory_geometry_independent": True,
    }
