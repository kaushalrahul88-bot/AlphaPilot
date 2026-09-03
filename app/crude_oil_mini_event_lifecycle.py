from __future__ import annotations

from copy import deepcopy

from .commodity_time import parse_ist_timestamp

DIRECTIONAL = {"BULLISH", "BEARISH"}
PERIODIC_SERIES = {"EIA_CRUDE_INVENTORY", "CRUDE_MACRO_RELEASE"}
ACTIVE_STATES = {
    "REACTION_CONFIRMED_ACTIVE",
    "AWAITING_REACTION",
    "ACTIVE_UNRESOLVED",
}
TERMINAL_STATES = {
    "RESOLVED_EXPLICIT",
    "EXPIRED_EXPLICIT",
    "SUPERSEDED_EXPLICIT",
    "SUPERSEDED_BY_NEW_RELEASE",
    "REACTION_REJECTED",
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


def _timestamp(record: dict, *keys: str):
    for key in keys:
        raw = _nested(record, key)
        if raw:
            try:
                return parse_ist_timestamp(str(raw))
            except Exception:
                continue
    return None


def _event_id(record: dict) -> str:
    return str(record.get("event_id") or _nested(record, "event_id") or "")


def _event_type(record: dict) -> str:
    return str(record.get("event_type") or _nested(record, "event_type") or "UNKNOWN").upper()


def _series(record: dict) -> str:
    return str(record.get("series") or "UNKNOWN").upper()


def _visible(record: dict, click_timestamp: str) -> bool:
    click = parse_ist_timestamp(click_timestamp)
    observed = _timestamp(record, "observed_at", "available_at")
    available = _timestamp(record, "available_at", "observed_at")
    return observed is not None and available is not None and observed <= click and available <= click


def _explicit_supersession(record: dict, visible_records: list[dict]) -> str | None:
    event_id = _event_id(record)
    explicit_by = str(_nested(record, "superseded_by_event_id") or "")
    if explicit_by and any(_event_id(row) == explicit_by for row in visible_records):
        return explicit_by

    for row in visible_records:
        supersedes = _nested(row, "supersedes_event_id")
        if supersedes and str(supersedes) == event_id:
            return _event_id(row) or "EXPLICIT_SUCCESSOR"
    return None


def _periodic_successor(record: dict, visible_records: list[dict]) -> str | None:
    series = _series(record)
    if series not in PERIODIC_SERIES:
        return None
    event_type = _event_type(record)
    available = _timestamp(record, "available_at", "observed_at")
    if available is None:
        return None
    successors = []
    for row in visible_records:
        if row is record:
            continue
        if _series(row) != series or _event_type(row) != event_type:
            continue
        candidate = _timestamp(row, "available_at", "observed_at")
        if candidate is not None and candidate > available:
            successors.append((candidate, _event_id(row)))
    if not successors:
        return None
    successors.sort(key=lambda item: item[0])
    return successors[0][1] or "NEWER_PERIODIC_RELEASE"


def _reaction(record: dict) -> tuple[str, bool]:
    raw = _nested(record, "reaction")
    raw = raw if isinstance(raw, dict) else {}
    direction = str(
        raw.get("direction")
        or _nested(record, "reaction_direction")
        or _nested(record, "price_reaction_direction")
        or "UNKNOWN"
    ).upper()
    if direction not in DIRECTIONAL:
        direction = "UNKNOWN"
    confirmed = bool(
        raw.get("confirmed")
        or _nested(record, "reaction_confirmed", False)
        or _nested(record, "price_confirmed", False)
    )
    return direction, confirmed


def classify_event_lifecycle(record: dict, visible_records: list[dict], click_timestamp: str) -> dict:
    """Classify one visible event without any universal time-based expiry rule.

    Age alone never expires an event. A lifecycle transition requires explicit metadata,
    a visible superseding periodic release, or an explicit point-in-time reaction state.
    """
    click = parse_ist_timestamp(click_timestamp)
    if not _visible(record, click_timestamp):
        return {
            "event_id": _event_id(record),
            "series": _series(record),
            "event_type": _event_type(record),
            "state": "NOT_VISIBLE_AT_CLICK",
            "active_context": False,
            "eligible_for_direction": False,
        }

    resolved_at = _timestamp(record, "resolved_at")
    active_until = _timestamp(record, "active_until")
    successor = _explicit_supersession(record, visible_records)
    periodic_successor = _periodic_successor(record, visible_records)
    mechanism = str(_nested(record, "mechanism_stance") or _nested(record, "stance") or "UNKNOWN").upper()
    materiality = str(_nested(record, "materiality_status") or _nested(record, "materiality") or "UNASSESSED").upper()
    novelty = str(_nested(record, "novelty_status") or _nested(record, "novelty") or "UNASSESSED").upper()
    declared_status = str(_nested(record, "lifecycle_status") or "UNASSESSED").upper()
    reaction_direction, reaction_confirmed = _reaction(record)

    material = materiality in {"MATERIAL", "HIGH", "CONFIRMED_MATERIAL"}
    novel = novelty not in {"REPEATED", "STALE", "ALREADY_KNOWN", "UNASSESSED", "UNKNOWN"}
    directional_mechanism = mechanism in DIRECTIONAL

    if resolved_at is not None and resolved_at <= click:
        state = "RESOLVED_EXPLICIT"
        reason = "explicit resolved_at is visible by the click"
    elif active_until is not None and active_until <= click:
        state = "EXPIRED_EXPLICIT"
        reason = "explicit active_until has passed"
    elif successor:
        state = "SUPERSEDED_EXPLICIT"
        reason = f"superseded by visible event {successor}"
    elif periodic_successor:
        state = "SUPERSEDED_BY_NEW_RELEASE"
        reason = f"newer visible periodic release {periodic_successor} supersedes this release"
    elif reaction_confirmed and directional_mechanism and reaction_direction in DIRECTIONAL:
        if reaction_direction == mechanism:
            state = "REACTION_CONFIRMED_ACTIVE"
            reason = "explicit point-in-time reaction confirms the directional mechanism"
        else:
            state = "REACTION_REJECTED"
            reason = "explicit point-in-time reaction rejects the directional mechanism"
    elif directional_mechanism and material and novel:
        state = "AWAITING_REACTION"
        reason = "directional material novel event is visible but reaction is not confirmed"
    elif declared_status in {"ACTIVE", "CURRENT", "ONGOING", "UNRESOLVED"}:
        state = "ACTIVE_UNRESOLVED"
        reason = "archive explicitly marks the event as active/unresolved"
    else:
        state = "VISIBLE_CONTEXT_UNASSESSED"
        reason = "visible fact lacks enough lifecycle/reaction metadata to be treated as active directional evidence"

    eligible = state == "REACTION_CONFIRMED_ACTIVE"
    return {
        "event_id": _event_id(record),
        "series": _series(record),
        "event_type": _event_type(record),
        "available_at": str(record.get("available_at") or record.get("observed_at") or ""),
        "state": state,
        "reason": reason,
        "active_context": state in ACTIVE_STATES,
        "terminal": state in TERMINAL_STATES,
        "eligible_for_direction": eligible,
        "mechanism_stance": mechanism if mechanism in DIRECTIONAL else "UNKNOWN",
        "reaction_direction": reaction_direction,
        "reaction_confirmed": reaction_confirmed,
        "materiality_status": materiality,
        "novelty_status": novelty,
        "source_record": deepcopy(record),
    }


def event_lifecycle_view(records: list[dict], click_timestamp: str) -> dict:
    visible_records = [row for row in records or [] if isinstance(row, dict) and _visible(row, click_timestamp)]
    visible_records.sort(
        key=lambda row: (
            _timestamp(row, "available_at", "observed_at"),
            _event_id(row),
        )
    )
    classified = [
        classify_event_lifecycle(row, visible_records, click_timestamp)
        for row in visible_records
    ]
    active = [row for row in classified if row["active_context"]]
    directional = [row for row in classified if row["eligible_for_direction"]]
    return {
        "version": "CRUDE_OIL_MINI_EVENT_LIFECYCLE_V1",
        "research_only": True,
        "shadow_only": True,
        "click_timestamp": parse_ist_timestamp(click_timestamp).isoformat(),
        "visible_event_count": len(classified),
        "active_context_count": len(active),
        "direction_eligible_count": len(directional),
        "events": classified,
        "active_events": active,
        "direction_eligible_events": directional,
        "rules": [
            "Event age alone never causes expiry.",
            "Periodic releases may supersede an older release only when the newer release is itself visible by the click.",
            "Policy/geopolitical/news events require explicit resolution, supersession, or active-status metadata; no arbitrary universal hour window is imposed.",
            "A directional event vote still requires explicit mechanism, materiality, novelty, and confirmed point-in-time reaction.",
            "A rejected event thesis removes the event vote and does not create the opposite vote.",
            "Historical archive visibility is separate from active trading relevance.",
        ],
    }


def architecture_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_EVENT_LIFECYCLE_V1_CONTRACT",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "direction_v2_effect_until_explicit_wiring": "NONE",
        "universal_fixed_event_expiry_hours": None,
        "age_only_expiry_allowed": False,
        "periodic_release_supersession_allowed": True,
        "explicit_resolution_and_supersession_supported": True,
        "reaction_backfill_from_future_price_allowed": False,
        "headline_sentiment_inference_allowed": False,
        "promotion_allowed": False,
    }
