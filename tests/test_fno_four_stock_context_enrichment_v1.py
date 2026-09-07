from datetime import datetime
from zoneinfo import ZoneInfo

from app.fno_four_stock_context_enrichment_v1 import (
    PEER_BASKETS,
    architecture_contract,
    enriched_decision,
    events_at,
)
from app.fno_four_stock_context_replay_v1 import outcome_for_action

IST = ZoneInfo("Asia/Kolkata")


def test_four_stock_peer_baskets_are_frozen_and_distinct():
    assert set(PEER_BASKETS) == {"ONGC", "LTIM", "SBIN", "SUNPHARMA"}
    assert all(len(peers) >= 4 for peers in PEER_BASKETS.values())
    assert all(symbol not in peers for symbol, peers in PEER_BASKETS.items())


def test_event_after_click_is_never_visible():
    events = [
        {
            "symbol": "SUNPHARMA",
            "effective_at": "2026-08-18T09:15:00+05:30",
            "direction": "POSITIVE",
            "weight": 1,
        }
    ]
    before = datetime(2026, 8, 18, 9, 14, tzinfo=IST)
    at_time = datetime(2026, 8, 18, 9, 15, tzinfo=IST)
    assert events_at("SUNPHARMA", before, events) == []
    assert len(events_at("SUNPHARMA", at_time, events)) == 1


def test_missing_context_cannot_promote_no_trade():
    decision = enriched_decision(
        {"action": "NO_TRADE"},
        {"timeframes": {"5m": {"signal": "BUY"}, "15m": {"signal": "BUY"}, "1h": {"signal": "BUY"}}},
        {"complete": False, "context_score": 5},
    )
    assert decision["action"] == "NO_TRADE"
    assert decision["reason"] == "CONTEXT_INCOMPLETE_FAIL_CLOSED"


def test_strong_aligned_context_can_promote_only_with_technical_alignment():
    technical = {"timeframes": {"5m": {"signal": "BUY"}, "15m": {"signal": "BUY"}, "1h": {"signal": "BUY"}}}
    promoted = enriched_decision(
        {"action": "NO_TRADE"}, technical, {"complete": True, "context_score": 4}
    )
    assert promoted["action"] == "LONG"

    opposed = enriched_decision(
        {"action": "NO_TRADE"}, technical, {"complete": True, "context_score": -4}
    )
    assert opposed["action"] == "NO_TRADE"


def test_outcome_reuses_raw_tape_and_only_changes_directional_view():
    base = {
        "reference_price": 100,
        "status": "RESOLVED",
        "checkpoints": {
            "15m": {
                "raw_return_pct": 1.0,
                "max_up_pct": 1.5,
                "max_down_pct": -0.4,
                "max_abs_excursion_pct": 1.5,
                "resolved": True,
            }
        },
        "eod": {
            "raw_return_pct": -2.0,
            "max_up_pct": 0.5,
            "max_down_pct": -2.5,
            "max_abs_excursion_pct": 2.5,
            "resolved": True,
        },
    }
    short = outcome_for_action(base, "SHORT")
    assert short["checkpoints"]["15m"]["directional_return_pct"] == -1.0
    assert short["checkpoints"]["15m"]["mfe_pct"] == 0.4
    assert short["checkpoints"]["15m"]["mae_pct"] == -1.5
    assert short["eod"]["directional_return_pct"] == 2.0
    assert short["eod"]["raw_return_pct"] == -2.0


def test_architecture_forbids_derivative_and_execution_inputs():
    contract = architecture_contract()
    assert contract["same_click_schedule_as_baseline"] is True
    assert contract["event_after_click_forbidden"] is True
    assert contract["options_read"] is False
    assert contract["futures_read"] is False
    assert contract["live_execution"] is False
    assert contract["capital_committed"] == 0
    assert contract["outcome_used_to_build_context"] is False
