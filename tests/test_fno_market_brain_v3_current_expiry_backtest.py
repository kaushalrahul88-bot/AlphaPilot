from datetime import date, datetime, time, timedelta

import pytest

from app import fno_market_brain_v3_current_expiry_backtest as backtest
from app import fno_market_brain_v3_current_expiry_dataset as source


def _tape(day=date(2026, 9, 1)):
    rows = []
    current = datetime.combine(day, time(9, 15), tzinfo=source.IST)
    end = datetime.combine(day, time(15, 25), tzinfo=source.IST)
    index = 0
    while current <= end:
        close = 100.0 + index * 0.05
        rows.append([
            current.isoformat(),
            round(close - 0.02, 4),
            round(close + 0.08, 4),
            round(close - 0.08, 4),
            round(close, 4),
            1000 + index,
        ])
        current += timedelta(minutes=5)
        index += 1
    return rows


def _dataset():
    hdfc = _tape()
    tapes = {
        "HDFCBANK": hdfc,
        "INFY": [],
        "MARUTI": [],
        "BHARTIARTL": [],
    }
    hashes = {symbol: source._tape_hash(rows) for symbol, rows in tapes.items()}
    click = datetime(2026, 9, 1, 9, 30, tzinfo=source.IST).astimezone(source.UTC)
    return {
        "protocol_id": source.PROTOCOL_ID,
        "status": "COMPLETED",
        "experiment": {
            "stocks": [
                {"symbol": symbol, "category": category}
                for symbol, category in source.FROZEN_STOCKS
            ],
            "expiry_cycle_start": source.EXPIRY_START.isoformat(),
            "expiry_date": source.EXPIRY_DATE.isoformat(),
            "data_frozen_through": source.DATA_END.isoformat(),
            "completed_sessions": ["2026-09-01"],
            "session_count": 1,
            "clicks_per_session": 20,
            "observations": 1,
        },
        "rows": [{
            "trade_date": "2026-09-01",
            "click_at": click.isoformat(),
            "symbol": "HDFCBANK",
            "category": "PRIVATE_BANKING",
            "technical": {
                "entry": 100.0,
                "stop_loss": 99.0,
                "target1": 101.5,
                "target2": 103.0,
            },
            "decision": {
                "protocol_id": "FNO_MARKET_BRAIN_V3_2026-09-07",
                "action": "LONG",
                "expansion": {"ready": True},
                "direction": {"side": "LONG"},
                "reasons": ["EXPANSION_AND_DIRECTION_CONFIRMED"],
            },
        }],
        "frozen_5m_tape_by_stock": tapes,
        "frozen_5m_tape_hashes": hashes,
        "safety": {
            "brain_protocol_id": "FNO_MARKET_BRAIN_V3_2026-09-07",
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


def test_criteria_match_original_underlying_random_replay():
    contract = backtest.architecture_contract()
    assert contract["same_outcome_resolver_as_first_four_stock_replay"] is True
    assert contract["outcome_horizons"] == ["15m", "30m", "60m", "90m", "EOD"]
    assert contract["same_no_trade_large_move_thresholds"] == [0.5, 1.0]
    assert contract["new_strategy_thresholds_added"] is False
    assert contract["options_read"] is False
    assert contract["futures_read"] is False


def test_evaluator_uses_frozen_v3_action_and_original_horizons():
    result = backtest.evaluate_frozen_dataset(_dataset())
    assert result["status"] == "COMPLETED"
    assert result["source"]["observations"] == 1
    assert result["summary"]["action_counts"] == {"LONG": 1}
    assert list(result["summary"]["horizons"]) == ["15m", "30m", "60m", "90m", "EOD"]
    assert result["summary"]["horizons"]["60m"]["direction_correct"] == 1
    assert result["summary"]["horizons"]["EOD"]["mean_directional_return_pct"] > 0
    assert result["safety"]["source_decisions_recomputed"] is False
    assert result["criteria"]["new_pass_threshold_added"] is False


def test_source_tape_hash_change_is_rejected():
    dataset = _dataset()
    dataset["frozen_5m_tape_by_stock"]["HDFCBANK"][0][4] = 999.0
    with pytest.raises(ValueError, match="SOURCE_FROZEN_TAPE_HASH_MISMATCH"):
        backtest.evaluate_frozen_dataset(dataset)


def test_source_with_options_decision_input_is_rejected():
    dataset = _dataset()
    dataset["safety"]["options_read_for_decision"] = True
    with pytest.raises(ValueError, match="SOURCE_OPTIONS_DECISION_INPUT_PRESENT"):
        backtest.evaluate_frozen_dataset(dataset)
