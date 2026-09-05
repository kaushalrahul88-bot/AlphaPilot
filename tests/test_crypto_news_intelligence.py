from datetime import datetime, timedelta, timezone

import pytest

from app.crypto_news_intelligence import (
    CryptoNewsItem,
    architecture_contract,
    deduplicate_news,
    news_event_context,
    news_replay_eligible,
)


def _t(hour: int = 4, minute: int = 0):
    return datetime(2026, 9, 5, hour, minute, tzinfo=timezone.utc)


def _confirmed(**overrides):
    values = {
        "news_id": "n1",
        "event_key": "evt:btc:regulatory",
        "assets": ("BTC",),
        "headline": "Confirmed material crypto development",
        "published_at": _t(3, 50),
        "first_seen_at": _t(3, 51),
        "source_name": "Professional News",
        "source_class": "BREAKING_NEWS",
        "source_tier": "C_PROFESSIONAL",
        "verification_state": "CONFIRMED",
        "truth_confidence": 0.95,
        "market_impact_confidence": 0.85,
        "direction_hint": "BULLISH",
        "materiality": "HIGH",
        "novelty": "NEW",
        "expected_horizon": "intraday",
    }
    values.update(overrides)
    return CryptoNewsItem(**values)


def test_replay_uses_first_seen_not_hindsight():
    item = _confirmed(first_seen_at=_t(4, 5))
    assert news_replay_eligible(item, decision_at=_t(4, 4)) is False
    assert news_replay_eligible(item, decision_at=_t(4, 5)) is True
    with pytest.raises(ValueError):
        news_event_context(
            item,
            decision_at=_t(4, 4),
            independent_corroboration_count=2,
            market_confirmation=True,
        )


def test_confirmed_corroborated_market_confirmed_news_can_be_one_directional_origin():
    evidence = news_event_context(
        _confirmed(),
        decision_at=_t(4, 0),
        independent_corroboration_count=2,
        market_confirmation=True,
    )
    assert evidence.stance == "BULLISH"
    assert evidence.context_only is False
    assert evidence.causal_origin == "EVENT_INFORMATION"
    assert evidence.metadata["generates_instrument_trade"] is False


def test_unverified_rumour_can_have_high_impact_but_remains_context_only():
    item = _confirmed(
        source_name="Anonymous Social Account",
        source_class="X_SOCIAL",
        source_tier="E_UNVERIFIED",
        verification_state="UNVERIFIED",
        truth_confidence=0.25,
        market_impact_confidence=0.95,
    )
    evidence = news_event_context(
        item,
        decision_at=_t(4, 0),
        independent_corroboration_count=5,
        market_confirmation=True,
    )
    assert evidence.stance == "UNKNOWN"
    assert evidence.context_only is True
    assert evidence.metadata["truth_confidence"] == 0.25
    assert evidence.metadata["market_impact_confidence"] == 0.95


def test_confirmed_professional_news_still_requires_corroboration_and_market_confirmation():
    no_corroboration = news_event_context(_confirmed(), decision_at=_t(4, 0), market_confirmation=True)
    no_market_confirmation = news_event_context(
        _confirmed(),
        decision_at=_t(4, 0),
        independent_corroboration_count=2,
        market_confirmation=False,
    )
    assert no_corroboration.context_only is True
    assert no_market_confirmation.context_only is True


def test_primary_confirmed_news_can_satisfy_corroboration_gate_but_not_market_gate():
    item = _confirmed(source_name="Regulator", source_class="OFFICIAL_ANNOUNCEMENTS", source_tier="A_PRIMARY")
    admitted = news_event_context(item, decision_at=_t(4, 0), primary_source_confirmed=True, market_confirmation=True)
    not_market_confirmed = news_event_context(item, decision_at=_t(4, 0), primary_source_confirmed=True, market_confirmation=False)
    assert admitted.context_only is False
    assert not_market_confirmed.context_only is True


def test_duplicate_reports_do_not_manufacture_independent_events():
    first = _confirmed(news_id="n1", first_seen_at=_t(3, 51))
    second = _confirmed(news_id="n2", first_seen_at=_t(3, 52), source_name="Second Outlet")
    result = deduplicate_news([second, first])
    assert result["unique_event_count"] == 1
    assert result["report_count"] == 2
    assert result["canonical"][0]["news_id"] == "n1"


def test_first_seen_cannot_precede_publication():
    item = _confirmed(published_at=_t(4, 0), first_seen_at=_t(3, 59))
    with pytest.raises(ValueError):
        item.normalized()


def test_news_architecture_never_generates_options_or_futures_trade():
    contract = architecture_contract()
    assert contract["news_first_class_live_intelligence"] is True
    assert contract["truth_confidence_separate_from_market_impact_confidence"] is True
    assert contract["headline_is_trade_signal"] is False
    assert contract["options_trade_generated"] is False
    assert contract["futures_trade_generated"] is False
