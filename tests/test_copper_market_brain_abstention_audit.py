from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.copper_market_brain_abstention_audit import (
    _abstention_origin,
    _abstention_outcome,
    _summary,
    evaluate_market_brain_abstention,
    normalize_candle_rows,
)

IST = ZoneInfo("Asia/Kolkata")


def _full_session_rows():
    start = datetime(2026, 8, 3, 9, 0, tzinfo=IST)
    rows = []
    price = 100.0
    for i in range(174):
        stamp = start + timedelta(minutes=5 * i)
        # Keep the early information set flat so NO_TRADE is genuine, then create a
        # later move that can be measured only after the abstention timestamp.
        if i >= 80:
            price = 100.0 + (i - 79) * 0.05
        high = price + 0.02
        low = price - 0.02
        rows.append([stamp.isoformat(), price, high, low, price, 1000.0, None])
    return rows


def test_normalize_frozen_dict_candles():
    rows = normalize_candle_rows([
        {"timestamp": "2026-08-03T09:00:00+05:30", "open": 100, "high": 101, "low": 99,
         "close": 100.5, "volume": 1234},
    ])
    assert rows == [["2026-08-03T09:00:00+05:30", 100, 101, 99, 100.5, 1234, None]]


def test_abstention_origin_separates_brain_b_filtering():
    assert _abstention_origin("A", "NO_TRADE", "NO_TRADE") == "BASELINE_ABSTENTION"
    assert _abstention_origin("B", "BUY", "NO_TRADE") == "B_FILTERED_BUY"
    assert _abstention_origin("B", "SELL", "NO_TRADE") == "B_FILTERED_SELL"
    assert _abstention_origin("B", "NO_TRADE", "NO_TRADE") == "BASELINE_ABSTENTION"


def test_abstention_outcome_is_same_session_and_direction_agnostic():
    rows = _full_session_rows()
    outcome = _abstention_outcome(rows, 75, 60)
    assert outcome is not None
    assert outcome["max_excursion_pct"] > 0
    assert outcome["dominant_path_direction"] == "UP"
    # No hypothetical BUY/SELL is required to compute the post-abstention path.
    assert "signed_forward_pct" not in outcome
    assert "option_side_intent" not in outcome


def test_summary_reports_all_preregistered_move_bands():
    rows = [
        {"absolute_forward_pct": 0.05, "max_excursion_pct": 0.08, "dominant_path_direction": "UP"},
        {"absolute_forward_pct": 0.12, "max_excursion_pct": 0.22, "dominant_path_direction": "DOWN"},
        {"absolute_forward_pct": 0.31, "max_excursion_pct": 0.35, "dominant_path_direction": "UP"},
    ]
    report = _summary(rows)
    assert report["no_trade_observations"] == 3
    assert report["path_excursion_thresholds"]["ge_0_10_pct"]["count"] == 2
    assert report["path_excursion_thresholds"]["ge_0_20_pct"]["count"] == 2
    assert report["path_excursion_thresholds"]["ge_0_30_pct"]["count"] == 1
    assert report["close_move_thresholds"]["ge_0_30_pct"]["count"] == 1


def test_end_to_end_abstention_audit_is_descriptive_and_options_only():
    report = evaluate_market_brain_abstention(_full_session_rows(), sample_every_bars=3)
    assert report["mode"] == "COPPER_MARKET_BRAIN_ABSTENTION_AUDIT_V1"
    assert report["research_only"] is True
    assert report["descriptive_only"] is True
    assert report["production_rules_changed"] is False
    assert report["strategy_rules_changed"] is False
    assert report["trade_instrument"] == "OPTIONS"
    assert report["underlying_reference_role"] == "REFERENCE_ONLY"
    assert report["futures_pnl_calculated"] is False
    assert report["synthetic_option_premium_used"] is False
    assert report["same_session_only"] is True
    assert report["move_thresholds_pct"] == [0.1, 0.2, 0.3]
    assert report["brains"]["A"]["60"]["no_trade_observations"] > 0
    assert report["brains"]["B"]["60"]["no_trade_observations"] > 0
    assert report["gap_attribution_status"] == "NOT_RUN"
