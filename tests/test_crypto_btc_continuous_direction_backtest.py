from datetime import datetime, timedelta, timezone

from app.crypto_btc_continuous_direction_backtest import (
    _classification,
    _click_grid,
    _summary,
    architecture_contract as replay_contract,
)
from app.crypto_btc_research_direction import (
    architecture_contract as lean_contract,
    derive_btc_research_directional_lean,
)
from app.crypto_market_intelligence import Evidence, assemble_market_state

UTC = timezone.utc
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _evidence(*, origin: str, stance: str, context_only: bool = False, confidence: float = 0.75):
    return Evidence(
        family=f"FAMILY_{origin}",
        causal_origin=origin,
        stance=stance,
        strength="MEDIUM",
        confidence=confidence,
        observed_at=NOW - timedelta(minutes=5),
        reason="test",
        context_only=context_only,
        source=f"SOURCE_{origin}",
        metadata={},
    )


def test_research_lean_can_measure_single_origin_without_weakening_production_gate():
    evidence = [_evidence(origin="SPOT_PRICE_STRUCTURE", stance="BULLISH")]
    production = assemble_market_state(evidence, decision_at=NOW, trade_horizon="intraday")
    research = derive_btc_research_directional_lean(evidence, decision_at=NOW)

    assert production["direction"] == "UNKNOWN"
    assert production["state"] == "INSUFFICIENT_INDEPENDENT_CONFIRMATION"
    assert research["direction"] == "UP"
    assert research["confidence_band"] == "LOW"
    assert research["production_two_origin_gate_changed"] is False
    assert research["research_only"] is True
    assert research["tradeable"] is False
    assert research["future_prices_used"] is False


def test_missing_and_context_only_lanes_do_not_count_as_bearish_votes():
    evidence = [
        _evidence(origin="SPOT_PRICE_STRUCTURE", stance="BULLISH"),
        _evidence(origin="HISTORICAL_MEMORY", stance="BEARISH", context_only=True),
    ]
    research = derive_btc_research_directional_lean(evidence, decision_at=NOW)
    assert research["direction"] == "UP"
    assert research["independent_directional_origins"] == 1
    assert research["context_rows_available"] == 1
    assert research["missing_evidence_penalty"] == 0.0


def test_opposing_independent_origins_produce_wait_when_nearly_balanced():
    evidence = [
        _evidence(origin="SPOT_PRICE_STRUCTURE", stance="BULLISH", confidence=0.75),
        _evidence(origin="LEVERAGED_POSITIONING", stance="BEARISH", confidence=0.75),
    ]
    research = derive_btc_research_directional_lean(evidence, decision_at=NOW)
    assert research["direction"] == "WAIT"
    assert research["normalized_edge"] == 0.0


def test_same_origin_conflict_fails_closed_in_research_lean():
    evidence = [
        _evidence(origin="LEVERAGED_POSITIONING", stance="BULLISH"),
        _evidence(origin="LEVERAGED_POSITIONING", stance="BEARISH"),
    ]
    research = derive_btc_research_directional_lean(evidence, decision_at=NOW)
    assert research["direction"] == "WAIT"
    assert research["conflicted_origins"] == ["LEVERAGED_POSITIONING"]
    assert research["independent_directional_origins"] == 0


def test_click_grid_uses_quarter_hour_boundaries_across_full_span():
    first = datetime(2026, 9, 5, 17, 49, 35, tzinfo=UTC)
    last = datetime(2026, 9, 5, 18, 34, 2, tzinfo=UTC)
    clicks = _click_grid(first, last)
    assert clicks == [
        datetime(2026, 9, 5, 18, 0, tzinfo=UTC),
        datetime(2026, 9, 5, 18, 15, tzinfo=UTC),
        datetime(2026, 9, 5, 18, 30, tzinfo=UTC),
    ]


def test_direction_classification_separates_wait_from_hit_miss():
    up = {"status": "RESOLVED", "realized_direction": "UP"}
    assert _classification("UP", up) == "HIT"
    assert _classification("DOWN", up) == "MISS"
    assert _classification("WAIT", up) == "ABSTENTION"


def test_replay_and_lean_contracts_keep_execution_disabled():
    replay = replay_contract()
    lean = lean_contract()
    assert replay["uses_all_available_pit_span"] is True
    assert replay["pit_gaps_fabricated_as_continuity"] is False
    assert replay["missing_lanes_are_negative_votes"] is False
    assert replay["production_two_origin_gate_changed"] is False
    assert replay["options_profitability_evaluated"] is False
    assert replay["futures_trade_generated"] is False
    assert replay["live_execution"] is False
    assert lean["production_two_origin_requirement_preserved"] is True
    assert lean["context_only_rows_may_create_direction"] is False
    assert lean["outcome_data_used_for_lean"] is False


def test_summary_scores_directional_accuracy_and_reports_waits():
    clicks = [
        {
            "research_direction": "UP",
            "production_direction": "UNKNOWN",
            "research_confidence": "LOW",
            "outcomes": {
                "15": {"status": "RESOLVED", "realized_direction": "UP"},
                "30": {"status": "RESOLVED", "realized_direction": "UP"},
                "60": {"status": "RESOLVED", "realized_direction": "DOWN"},
            },
            "classifications": {"15": "HIT", "30": "HIT", "60": "MISS"},
        },
        {
            "research_direction": "WAIT",
            "production_direction": "UNKNOWN",
            "research_confidence": "NONE",
            "outcomes": {
                "15": {"status": "RESOLVED", "realized_direction": "DOWN"},
                "30": {"status": "RESOLVED", "realized_direction": "DOWN"},
                "60": {"status": "RESOLVED", "realized_direction": "DOWN"},
            },
            "classifications": {"15": "ABSTENTION", "30": "ABSTENTION", "60": "ABSTENTION"},
        },
    ]
    summary = _summary(clicks)
    assert summary["primary_directional_accuracy_pct"] == 100.0
    assert summary["research_coverage_pct"] == 50.0
    assert summary["waits"] == 1
    assert summary["wait_followed_down"] == 1
    assert summary["options_profitability_evaluated"] is False
