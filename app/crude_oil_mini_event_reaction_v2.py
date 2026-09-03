from __future__ import annotations

from copy import deepcopy

DIRECTIONAL = {"BULLISH", "BEARISH"}
EVENT_SERIES = {"EIA_CRUDE_INVENTORY", "OPEC_SUPPLY", "CRUDE_MACRO_RELEASE", "CRUDE_NEWS"}


def _upper(value, default="UNKNOWN") -> str:
    text = str(value or default).upper()
    return text or default


def _nested(record: dict, key: str, default=None):
    value = record.get("value")
    if isinstance(value, dict) and key in value:
        return value.get(key)
    return record.get(key, default)


def normalize_event_record(record: dict | None) -> dict | None:
    """Normalize explicit event metadata without inferring sentiment from headline text."""
    if not isinstance(record, dict):
        return None
    series = _upper(record.get("series"))
    if series not in EVENT_SERIES:
        return None

    mechanism_stance = _upper(_nested(record, "mechanism_stance", _nested(record, "stance")))
    if mechanism_stance not in DIRECTIONAL:
        mechanism_stance = "UNKNOWN"

    materiality = _upper(_nested(record, "materiality_status", _nested(record, "materiality")))
    novelty = _upper(_nested(record, "novelty_status", _nested(record, "novelty")))
    surprise = _upper(_nested(record, "surprise_status"))

    reaction = _nested(record, "reaction")
    if not isinstance(reaction, dict):
        reaction = {}
    reaction_direction = _upper(
        reaction.get("direction")
        or _nested(record, "reaction_direction")
        or _nested(record, "price_reaction_direction")
    )
    reaction_confirmed = bool(
        reaction.get("confirmed")
        or _nested(record, "reaction_confirmed", False)
        or _nested(record, "price_confirmed", False)
    )
    confirmation_sources = reaction.get("confirmation_sources") or _nested(record, "confirmation_sources", []) or []
    if isinstance(confirmation_sources, str):
        confirmation_sources = [confirmation_sources]

    return {
        "series": series,
        "event_id": record.get("event_id") or _nested(record, "event_id"),
        "event_type": record.get("event_type") or _nested(record, "event_type"),
        "available_at": record.get("available_at") or record.get("observed_at"),
        "mechanism_stance": mechanism_stance,
        "materiality_status": materiality,
        "novelty_status": novelty,
        "surprise_status": surprise,
        "reaction_direction": reaction_direction if reaction_direction in DIRECTIONAL else "UNKNOWN",
        "reaction_confirmed": reaction_confirmed,
        "confirmation_sources": sorted(str(item) for item in confirmation_sources),
        "source": record.get("source"),
        "raw": deepcopy(record),
    }


def build_event_reaction_family(latest: dict[str, dict]) -> dict:
    """Create an event-family vote only from explicit PIT mechanism + confirmed reaction.

    Headline keywords never create direction. A mechanism may be context-only until
    materiality/novelty are known and the market reaction is explicitly confirmed.
    Rejection of a theoretical event direction removes its vote; it does not
    automatically create the opposite direction.
    """
    events = []
    for series in EVENT_SERIES:
        normalized = normalize_event_record((latest or {}).get(series))
        if normalized:
            events.append(normalized)

    directional = []
    contextual = []
    rejected = []
    for event in events:
        mechanism = event["mechanism_stance"]
        reaction_direction = event["reaction_direction"]
        material = event["materiality_status"] in {"MATERIAL", "HIGH", "CONFIRMED_MATERIAL"}
        novel = event["novelty_status"] not in {"REPEATED", "STALE", "ALREADY_KNOWN"}
        confirmed = event["reaction_confirmed"] and reaction_direction in DIRECTIONAL

        if mechanism in DIRECTIONAL and material and novel and confirmed:
            if reaction_direction == mechanism:
                directional.append(event)
            else:
                rejected.append(event)
        else:
            contextual.append(event)

    stances = {event["mechanism_stance"] for event in directional}
    if len(stances) > 1:
        stance = "UNKNOWN"
        state = "CONFLICTED_EVENTS"
        counts = False
    elif len(stances) == 1:
        stance = next(iter(stances))
        state = "CONFIRMED_BULLISH" if stance == "BULLISH" else "CONFIRMED_BEARISH"
        counts = True
    elif rejected:
        stance = "UNKNOWN"
        rejected_stances = {event["mechanism_stance"] for event in rejected}
        if rejected_stances == {"BULLISH"}:
            state = "BULLISH_EVENT_REJECTED"
        elif rejected_stances == {"BEARISH"}:
            state = "BEARISH_EVENT_REJECTED"
        else:
            state = "EVENT_REACTION_REJECTED_OR_MIXED"
        counts = False
    elif events:
        stance = "UNKNOWN"
        state = "CONTEXT_ONLY"
        counts = False
    else:
        stance = "UNKNOWN"
        state = "UNKNOWN"
        counts = False

    reaction_dependencies = sorted({
        source
        for event in directional + rejected
        for source in event.get("confirmation_sources", [])
    })
    return {
        "family": "EVENT_REACTION",
        "causal_origin": "EXOGENOUS_INFORMATION",
        "independence_status": "INDEPENDENT" if counts else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": reaction_dependencies,
        "counts_for_direction": counts,
        "stance": stance,
        "state": state,
        "detail": {
            "directional_events": directional,
            "rejected_events": rejected,
            "context_only_events": contextual,
            "reaction_dependencies": reaction_dependencies,
            "headline_sentiment_inferred": False,
            "rejection_creates_reverse_vote": False,
        },
    }


def event_input_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_EVENT_REACTION_INPUT_V2",
        "research_only": True,
        "required_for_direction": [
            "point_in_time availability",
            "explicit mechanism stance",
            "materiality",
            "novelty/not stale",
            "confirmed market reaction",
        ],
        "headline_keyword_direction_allowed": False,
        "event_rejection_reverse_vote_allowed": False,
        "reaction_dependency_declared": True,
        "current_mind_effect": "NONE",
        "promotion_allowed": False,
    }
