from app.crude_oil_mini_event_lifecycle import (
    architecture_contract,
    event_lifecycle_view,
)


def _event(
    event_id,
    available_at,
    *,
    series="CRUDE_NEWS",
    event_type="GEOPOLITICAL",
    value=None,
):
    return {
        "series": series,
        "event_id": event_id,
        "event_type": event_type,
        "observed_at": available_at,
        "available_at": available_at,
        "value": value or {},
    }


def test_new_periodic_release_supersedes_only_after_it_is_visible():
    first = _event(
        "EIA-1",
        "2026-08-05T20:00:00+05:30",
        series="EIA_CRUDE_INVENTORY",
        event_type="WPSR",
    )
    second = _event(
        "EIA-2",
        "2026-08-12T20:00:00+05:30",
        series="EIA_CRUDE_INVENTORY",
        event_type="WPSR",
    )

    before = event_lifecycle_view([first, second], "2026-08-10T12:00:00+05:30")
    assert before["visible_event_count"] == 1
    assert before["events"][0]["state"] == "VISIBLE_CONTEXT_UNASSESSED"

    after = event_lifecycle_view([first, second], "2026-08-13T12:00:00+05:30")
    states = {row["event_id"]: row["state"] for row in after["events"]}
    assert states["EIA-1"] == "SUPERSEDED_BY_NEW_RELEASE"
    assert states["EIA-2"] == "VISIBLE_CONTEXT_UNASSESSED"


def test_old_event_does_not_expire_from_age_alone():
    ongoing = _event(
        "OPEC-ONGOING",
        "2026-08-01T12:00:00+05:30",
        series="OPEC_SUPPLY",
        event_type="POLICY_REGIME",
        value={"lifecycle_status": "ONGOING"},
    )
    view = event_lifecycle_view([ongoing], "2026-08-28T18:00:00+05:30")
    assert view["events"][0]["state"] == "ACTIVE_UNRESOLVED"
    assert view["events"][0]["active_context"] is True


def test_explicit_resolution_ends_active_event_without_age_rule():
    resolved = _event(
        "NEWS-RESOLVED",
        "2026-08-10T10:00:00+05:30",
        value={
            "lifecycle_status": "ONGOING",
            "resolved_at": "2026-08-10T16:00:00+05:30",
        },
    )
    view = event_lifecycle_view([resolved], "2026-08-10T18:00:00+05:30")
    row = view["events"][0]
    assert row["state"] == "RESOLVED_EXPLICIT"
    assert row["active_context"] is False
    assert row["eligible_for_direction"] is False


def test_direction_requires_explicit_confirmed_reaction():
    confirmed = _event(
        "NEWS-CONFIRMED",
        "2026-08-15T12:00:00+05:30",
        value={
            "mechanism_stance": "BULLISH",
            "materiality_status": "MATERIAL",
            "novelty_status": "NEW",
            "reaction": {"direction": "BULLISH", "confirmed": True},
        },
    )
    view = event_lifecycle_view([confirmed], "2026-08-15T14:00:00+05:30")
    row = view["events"][0]
    assert row["state"] == "REACTION_CONFIRMED_ACTIVE"
    assert row["eligible_for_direction"] is True


def test_rejected_reaction_removes_vote_and_never_reverses_it():
    rejected = _event(
        "NEWS-REJECTED",
        "2026-08-15T12:00:00+05:30",
        value={
            "mechanism_stance": "BULLISH",
            "materiality_status": "MATERIAL",
            "novelty_status": "NEW",
            "reaction": {"direction": "BEARISH", "confirmed": True},
        },
    )
    view = event_lifecycle_view([rejected], "2026-08-15T14:00:00+05:30")
    row = view["events"][0]
    assert row["state"] == "REACTION_REJECTED"
    assert row["eligible_for_direction"] is False
    assert view["direction_eligible_count"] == 0


def test_contract_has_no_universal_event_expiry_window():
    contract = architecture_contract()
    assert contract["universal_fixed_event_expiry_hours"] is None
    assert contract["age_only_expiry_allowed"] is False
    assert contract["reaction_backfill_from_future_price_allowed"] is False
    assert contract["headline_sentiment_inference_allowed"] is False
    assert contract["current_mind_effect"] == "NONE"
    assert contract["promotion_allowed"] is False
