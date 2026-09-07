from app.fno_v2_missed_move_forensics_v1 import audit_missed_moves, research_contract


def _row(symbol, action, signal, max_abs, raw_return):
    return {
        "symbol": symbol,
        "decision": {"action": action},
        "technical": {"signal": signal},
        "outcome": {
            "eod": {
                "max_abs_excursion_pct": max_abs,
                "raw_return_pct": raw_return,
            }
        },
    }


def test_audit_counts_only_no_trade_misses_and_keeps_strategy_boundary():
    rows = [
        _row("A", "NO_TRADE", "WATCH_LONG", 1.2, 1.1),
        _row("A", "NO_TRADE", "WATCH_SHORT", 0.7, 0.6),
        _row("B", "NO_TRADE", "NO_TRADE", 0.2, 0.1),
        _row("B", "LONG", "LONG", 2.0, 1.5),
    ]

    result = audit_missed_moves(rows)
    half = result["threshold_audits"]["0.5"]
    one = result["threshold_audits"]["1.0"]

    assert result["observations"] == 4
    assert result["no_trade_observations"] == 3
    assert result["descriptive_only"] is True
    assert result["strategy_gate"] is False
    assert half["missed_moves"] == 2
    assert one["missed_moves"] == 1
    assert half["by_symbol"]["A"]["miss_rate"] == 1.0
    assert half["diagnostic_buckets"]["WATCH_STATE"] == 2
    assert half["directional_technical_signals"] == 2
    assert half["directional_match_count"] == 1

    contract = research_contract()
    assert contract["may_change_live_decision"] is False
    assert contract["may_retune_v1_v2_thresholds"] is False
    assert contract["requires_new_out_of_sample_data_before_promotion"] is True
