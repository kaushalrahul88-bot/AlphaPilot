from datetime import datetime

from app import fno_four_stock_context_enrichment_v1 as v1
from app import fno_four_stock_context_enrichment_v2 as v2


def test_v2_keeps_v1_decision_thresholds():
    assert v2.PROMOTE_SCORE == v1.PROMOTE_SCORE
    assert v2.VETO_SCORE == v1.VETO_SCORE
    assert v2.MOMENTUM_DEADBAND_PCT == v1.MOMENTUM_DEADBAND_PCT
    assert v2.RELATIVE_STRENGTH_DEADBAND_PCT == v1.RELATIVE_STRENGTH_DEADBAND_PCT
    contract = v2.architecture_contract()
    assert contract["decision_thresholds_identical_to_v1"] is True
    assert contract["thresholds_retuned_from_v1_results"] is False


def test_sector_event_applies_only_to_matching_stock():
    event = {
        "scope": "SECTOR:INFORMATION_TECHNOLOGY",
        "effective_at": "2026-08-21T09:15:00+05:30",
        "direction": "NEGATIVE",
        "weight": 1,
    }
    click = datetime.fromisoformat("2026-08-21T10:00:00+05:30")
    assert len(v2.events_at("LTIM", click, [event])) == 1
    assert v2.events_at("SBIN", click, [event]) == []
    assert v2.events_at("ONGC", click, [event]) == []
    assert v2.events_at("SUNPHARMA", click, [event]) == []


def test_future_event_is_forbidden():
    event = {
        "symbol": "LTIM",
        "effective_at": "2026-08-21T10:38:00+05:30",
        "direction": "NEGATIVE",
        "weight": 2,
    }
    before = datetime.fromisoformat("2026-08-21T10:35:00+05:30")
    after = datetime.fromisoformat("2026-08-21T10:40:00+05:30")
    assert v2.events_at("LTIM", before, [event]) == []
    assert len(v2.events_at("LTIM", after, [event])) == 1


def test_market_event_applies_to_all_four_stocks():
    event = {
        "symbol": "MARKET",
        "effective_at": "2026-08-10T07:55:00+05:30",
        "direction": "NEGATIVE",
        "weight": 1,
    }
    click = datetime.fromisoformat("2026-08-10T10:00:00+05:30")
    for symbol in ("ONGC", "LTIM", "SBIN", "SUNPHARMA"):
        assert len(v2.events_at(symbol, click, [event])) == 1


def test_v2_archive_is_materially_deeper_than_v1():
    assert len(v2._load_events()) > len(v1._load_events())
    assert len(v2._load_events()) >= 20


def test_architecture_remains_shadow_only():
    contract = v2.architecture_contract()
    assert contract["options_read"] is False
    assert contract["futures_read"] is False
    assert contract["live_execution"] is False
    assert contract["capital_committed"] == 0
    assert contract["outcome_used_to_build_context"] is False
