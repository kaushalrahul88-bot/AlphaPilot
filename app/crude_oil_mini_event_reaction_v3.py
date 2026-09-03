from __future__ import annotations

from .crude_oil_mini_event_lifecycle import event_lifecycle_view

DIRECTIONAL = {"BULLISH", "BEARISH"}

CONFIRMATION_ORIGIN_MAP = {
    "WTI": "CROSS_MARKET_CRUDE",
    "WTI_CRUDE": "CROSS_MARKET_CRUDE",
    "BRENT": "CROSS_MARKET_CRUDE",
    "BRENT_CRUDE": "CROSS_MARKET_CRUDE",
    "GLOBAL_CRUDE": "CROSS_MARKET_CRUDE",
    "CRUDEOILM": "LOCAL_PRICE_STRUCTURE",
    "MCX_CRUDE": "LOCAL_PRICE_STRUCTURE",
    "MCX_CRUDEOILM": "LOCAL_PRICE_STRUCTURE",
    "LOCAL_PRICE": "LOCAL_PRICE_STRUCTURE",
    "USDINR": "CURRENCY_TRANSLATION",
}


def _nested(record: dict, key: str, default=None):
    value = record.get("value")
    if isinstance(value, dict) and key in value:
        return value.get(key)
    payload = value.get("event_payload") if isinstance(value, dict) else None
    if isinstance(payload, dict) and key in payload:
        return payload.get(key)
    payload = record.get("event_payload")
    if isinstance(payload, dict) and key in payload:
        return payload.get(key)
    return record.get(key, default)


def _confirmation_sources(lifecycle_row: dict) -> list[str]:
    source_record = lifecycle_row.get("source_record") or {}
    reaction = _nested(source_record, "reaction", {})
    sources = reaction.get("confirmation_sources") if isinstance(reaction, dict) else None
    if not sources:
        sources = _nested(source_record, "confirmation_sources", [])
    if isinstance(sources, str):
        sources = [sources]
    return sorted({str(item).strip().upper() for item in (sources or []) if str(item).strip()})


def _confirmation_origins(sources: list[str]) -> tuple[list[str], list[str]]:
    origins = []
    unmapped = []
    for source in sources:
        origin = CONFIRMATION_ORIGIN_MAP.get(source)
        if origin:
            if origin not in origins:
                origins.append(origin)
        else:
            unmapped.append(source)
    return sorted(origins), sorted(unmapped)


def build_event_reaction_family(event_records: list[dict], click_timestamp: str) -> dict:
    """Convert the PIT event lifecycle into one auditable EVENT_REACTION family.

    A lifecycle-qualified event still cannot vote unless its reaction-confirmation
    source is declared and mappable to a causal origin. This lets the dependency
    auditor suppress double counting against Local Structure or Global Crude.
    """
    lifecycle = event_lifecycle_view(event_records or [], click_timestamp)
    eligible = []
    dependency_unknown = []

    for row in lifecycle.get("direction_eligible_events") or []:
        sources = _confirmation_sources(row)
        origins, unmapped = _confirmation_origins(sources)
        enriched = dict(row)
        enriched["confirmation_sources"] = sources
        enriched["confirmation_origins"] = origins
        enriched["unmapped_confirmation_sources"] = unmapped
        if not sources or unmapped:
            dependency_unknown.append(enriched)
            continue
        eligible.append(enriched)

    stances = {str(row.get("mechanism_stance") or "UNKNOWN").upper() for row in eligible}
    stances &= DIRECTIONAL
    dependencies = sorted({
        origin
        for row in eligible
        for origin in row.get("confirmation_origins") or []
    })

    if len(stances) > 1:
        stance = "UNKNOWN"
        state = "CONFLICTED_ACTIVE_EVENTS"
        counts = False
    elif len(stances) == 1:
        stance = next(iter(stances))
        state = "CONFIRMED_BULLISH" if stance == "BULLISH" else "CONFIRMED_BEARISH"
        counts = True
    elif dependency_unknown:
        stance = "UNKNOWN"
        state = "REACTION_DEPENDENCY_UNAUDITABLE"
        counts = False
    elif lifecycle.get("direction_eligible_count", 0):
        stance = "UNKNOWN"
        state = "NO_AUDITABLE_DIRECTION_ELIGIBLE_EVENT"
        counts = False
    elif any(row.get("state") == "REACTION_REJECTED" for row in lifecycle.get("events") or []):
        stance = "UNKNOWN"
        state = "EVENT_REACTION_REJECTED"
        counts = False
    elif lifecycle.get("active_context_count", 0):
        stance = "UNKNOWN"
        state = "ACTIVE_CONTEXT_NOT_DIRECTION_ELIGIBLE"
        counts = False
    elif lifecycle.get("visible_event_count", 0):
        stance = "UNKNOWN"
        state = "HISTORICAL_CONTEXT_ONLY"
        counts = False
    else:
        stance = "UNKNOWN"
        state = "NO_VISIBLE_EVENT"
        counts = False

    return {
        "family": "EVENT_REACTION",
        "causal_origin": "EXOGENOUS_INFORMATION",
        "independence_status": "INDEPENDENT" if counts else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on_origins": dependencies,
        "counts_for_direction": counts,
        "stance": stance,
        "state": state,
        "detail": {
            "lifecycle": lifecycle,
            "auditable_direction_events": eligible,
            "dependency_unknown_events": dependency_unknown,
            "reaction_dependencies": dependencies,
            "headline_sentiment_inferred": False,
            "rejection_creates_reverse_vote": False,
        },
    }


def event_reaction_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_EVENT_REACTION_V3",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "uses_event_lifecycle": True,
        "requires_mechanism_materiality_novelty_and_confirmed_reaction": True,
        "requires_declared_reaction_confirmation_source": True,
        "unmapped_confirmation_source_can_vote": False,
        "headline_keyword_direction_allowed": False,
        "event_rejection_reverse_vote_allowed": False,
        "dependency_origins_declared": True,
        "promotion_allowed": False,
    }
