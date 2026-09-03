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
    payload = value.get("event_payload") if isinstance(value, dict) else None
    if isinstance(payload, dict) and key in payload:
        return payload.get(key)
    payload = record.get("event_payload")
    if isinstance(payload, dict) and key in payload:
        return payload.get(key)
    return record.get(key, default)


def _dependency_origin(source: str) -> str:
    token = _upper(source)
    if token in {"WTI", "WTI_CRUDE", "BRENT", "BRENT_CRUDE", "WTI_BRENT"}:
        return "CROSS_MARKET_CRUDE"
    if token in {"MCX", "MCX_CRUDEOILM", "LOCAL_PRICE", "LOCAL_STRUCTURE"}:
        return "LOCAL_PRICE_STRUCTURE"
    if token in {"USDINR", "USD_INR", "FX_TRANSLATION"}:
        return "CURRENCY_TRANSLATION"
    return token


def normalize_event_record(record: dict | None, *, lifecycle_state: str = "UNASSESSED") -> dict | None:
    """Normalize explicit event metadata without inferring direction from text."""
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
    if reaction_direction not in DIRECTIONAL:
        reaction_direction = "UNKNOWN"
    reaction_confirmed = bool(
        reaction.get("confirmed")
        or _nested(record, "reaction_confirmed", False)
        or _nested(record, "price_confirmed", False)
    )
    confirmation_sources = reaction.get("confirmation_sources") or _nested(record, "confirmation_sources", []) or []
    if isinstance(confirmation_sources, str):
        confirmation_sources = [confirmation_sources]
    confirmation_origins = sorted({_dependency_origin(str(item)) for item in confirmation_sources if item})

    return {
        "series": series,
        "event_id": record.get("event_id") or _nested(record, "event_id"),
        "event_type": record.get("event_type") or _nested(record, "event_type"),
        "available_at": record.get("available_at") or record.get("observed_at"),
        "lifecycle_state": _upper(lifecycle_state),
        "mechanism_stance": mechanism_stance,
        "materiality_status": materiality,
        "novelty_status": novelty,
        "surprise_status": surprise,
        "reaction_direction": reaction_direction,
        "reaction_confirmed": reaction_confirmed,
        "confirmation_sources": sorted(str(item) for item in confirmation_sources),
        "confirmation_origins": confirmation_origins,
        "source": record.get("source"),
        "raw": deepcopy(record),
    }


def _qualified(event: dict) -> bool:
    material = event["materiality_status"] in {"MATERIAL", "HIGH", "CONFIRMED_MATERIAL"}
    novel = event["novelty_status"] not in {"REPEATED", "STALE", "ALREADY_KNOWN", "UNASSESSED", "UNKNOWN"}
    return (
        event["mechanism_stance"] in DIRECTIONAL
        and material
        and novel
        and event["reaction_confirmed"]
        and event["reaction_direction"] in DIRECTIONAL
    )


def build_event_reaction_family_from_lifecycle(lifecycle_view: dict | None) -> dict:
    """Build EVENT_REACTION from a point-in-time lifecycle view.

    Only lifecycle-active, fully qualified, reaction-confirmed events can vote. Visible
    historical events remain auditable context but do not stay directionally alive just
    because they were once published.
    """
    view = lifecycle_view or {}
    active_ids = {row.get("event_id") for row in (view.get("active_events") or [])}
    directional = []
    contextual = []
    rejected = []
    terminal = []

    for row in view.get("events") or []:
        source = row.get("source_record") if isinstance(row, dict) else None
        normalized = normalize_event_record(source, lifecycle_state=row.get("state", "UNASSESSED"))
        if not normalized:
            continue
        state = normalized["lifecycle_state"]
        event_id = normalized.get("event_id")

        if state == "REACTION_REJECTED":
            rejected.append(normalized)
            continue
        if bool(row.get("terminal")):
            terminal.append(normalized)
            continue
        if event_id not in active_ids:
            contextual.append(normalized)
            continue
        if state == "REACTION_CONFIRMED_ACTIVE" and _qualified(normalized):
            if normalized["reaction_direction"] == normalized["mechanism_stance"]:
                directional.append(normalized)
            else:
                rejected.append(normalized)
        else:
            contextual.append(normalized)

    stances = {event["mechanism_stance"] for event in directional}
    if len(stances) > 1:
        stance = "UNKNOWN"
        state = "CONFLICTED_ACTIVE_EVENTS"
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
    elif view.get("active_context_count", 0):
        stance = "UNKNOWN"
        state = "ACTIVE_CONTEXT_AWAITING_OR_UNASSESSED"
        counts = False
    elif view.get("visible_event_count", 0):
        stance = "UNKNOWN"
        state = "HISTORICAL_CONTEXT_ONLY"
        counts = False
    else:
        stance = "UNKNOWN"
        state = "UNKNOWN"
        counts = False

    dependencies = sorted({
        origin
        for event in directional + rejected
        for origin in event.get("confirmation_origins", [])
        if origin
    })

    return {
        "family": "EVENT_REACTION",
        "causal_origin": "EXOGENOUS_INFORMATION",
        "independence_status": "INDEPENDENT" if counts else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": dependencies,
        "counts_for_direction": counts,
        "stance": stance,
        "state": state,
        "detail": {
            "visible_event_count": int(view.get("visible_event_count") or 0),
            "active_context_count": int(view.get("active_context_count") or 0),
            "directional_events": directional,
            "rejected_events": rejected,
            "context_only_events": contextual,
            "terminal_events": terminal,
            "reaction_confirmation_dependencies": dependencies,
            "headline_sentiment_inferred": False,
            "rejection_creates_reverse_vote": False,
            "lifecycle_required": True,
        },
    }


def event_input_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_EVENT_REACTION_INPUT_V2_LIFECYCLE_AWARE",
        "research_only": True,
        "shadow_only": True,
        "required_for_direction": [
            "point-in-time visibility",
            "active lifecycle state",
            "explicit mechanism stance",
            "materiality",
            "novelty/not stale",
            "confirmed point-in-time market reaction",
        ],
        "headline_keyword_direction_allowed": False,
        "event_rejection_reverse_vote_allowed": False,
        "reaction_dependency_declared": True,
        "historical_visibility_equals_active_relevance": False,
        "current_mind_effect": "NONE",
        "promotion_allowed": False,
    }
