from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import fno_market_brain_v3_current_expiry_dataset as source_dataset
from app import fno_v1_current_expiry_comparator as comparator

UTC = timezone.utc


def _tape():
    start = datetime(2026, 9, 1, 3, 55, tzinfo=UTC)
    rows = []
    for index in range(74):
        stamp = start + timedelta(minutes=5 * index)
        close = 100.0 + 0.01 * index
        rows.append([
            stamp.isoformat(),
            close - 0.02,
            close + 0.05,
            close - 0.05,
            close,
            1000 + index,
        ])
    return rows


def _dataset():
    tape = _tape()
    tapes = {symbol: list(tape) for symbol in source_dataset.STOCKS}
    hashes = {
        symbol: source_dataset._tape_hash(rows)
        for symbol, rows in tapes.items()
    }
    return {
        "protocol_id": source_dataset.PROTOCOL_ID,
        "status": "COMPLETED",
        "experiment": {
            "stocks": [
                {"symbol": "HDFCBANK", "category": "PRIVATE_BANKING"},
                {"symbol": "INFY", "category": "INFORMATION_TECHNOLOGY"},
                {"symbol": "MARUTI", "category": "PASSENGER_AUTOMOBILES"},
                {"symbol": "BHARTIARTL", "category": "TELECOMMUNICATIONS"},
            ],
            "expiry_cycle_start": "2026-08-26",
            "expiry_date": "2026-09-29",
            "data_frozen_through": "2026-09-07",
            "completed_sessions": ["2026-09-01"],
            "session_count": 1,
            "clicks_per_session": source_dataset.CLICKS_PER_DAY,
            "observations": 1,
        },
        "rows": [
            {
                "trade_date": "2026-09-01",
                "click_at": "2026-09-01T04:00:00+00:00",
                "symbol": "HDFCBANK",
                "category": "PRIVATE_BANKING",
                "technical": {
                    "status": "SETUP",
                    "direction": "LONG",
                    "signal": "TEST_LONG",
                    "entry": 100.0,
                    "stop_loss": 99.0,
                    "target1": 101.0,
                    "target2": 102.0,
                    "risk_reward": 1.0,
                    "multi_timeframe_score": 3,
                    "timeframe_votes": {"5m": "LONG"},
                },
                "context": {"event_component": -999.0},
                "decision": {"action": "NO_TRADE", "protocol_id": "FROZEN_V3_TEST"},
            }
        ],
        "frozen_5m_tape_by_stock": tapes,
        "frozen_5m_tape_hashes": hashes,
        "safety": {
            "brain_protocol_id": "FROZEN_V3_TEST",
            "completed_candles_only": True,
            "point_in_time_context_only": True,
            "clicks_deterministic_and_frozen": True,
            "future_tape_frozen": True,
            "future_outcomes_resolved": False,
            "evaluation_metrics_computed": False,
            "options_read_for_decision": False,
            "futures_read_for_decision": False,
        },
    }


def test_architecture_contract_is_strictly_frozen_and_derivative_free():
    contract = comparator.architecture_contract()
    assert contract["same_frozen_clicks_as_v3"] is True
    assert contract["same_frozen_future_tapes_as_v3"] is True
    assert contract["frozen_technical_snapshots_reused"] is True
    assert contract["old_v1_decision_logic_reused"] is True
    assert contract["source_context_ignored_for_v1_decision"] is True
    assert contract["source_news_ignored_for_v1_decision"] is True
    assert contract["source_v3_action_ignored_for_v1_decision"] is True
    assert contract["options_read"] is False
    assert contract["futures_read"] is False
    assert contract["thresholds_retuned"] is False
    assert contract["result_rows_persisted"] is False


def test_original_v1_action_is_derived_from_frozen_technical_not_v3_action_or_context():
    result = comparator.evaluate_frozen_dataset(_dataset())
    assert result["status"] == "COMPLETED"
    assert result["source"]["observations"] == 1
    assert result["summary"]["action_counts"] == {"LONG": 1}
    assert result["summary"]["actionable"] == 1
    comparison = result["decision_comparison"]
    assert comparison["same_action"] == 0
    assert comparison["v1_only_actionable"] == 1
    assert comparison["transition_counts_v3_to_v1"] == {"NO_TRADE->LONG": 1}
    assert result["methodology"]["v3_context_used_for_v1_decision"] is False
    assert result["methodology"]["v3_news_used_for_v1_decision"] is False
    assert result["methodology"]["v3_action_used_for_v1_decision"] is False
    assert "rows" not in result
