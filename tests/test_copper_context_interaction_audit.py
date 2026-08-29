from app.copper_context_interaction_audit import _level_bucket, _magnitude_bucket, _stats


def test_context_interaction_buckets_are_frozen_and_outcome_independent():
    assert _level_bucket(None) == "UNKNOWN"
    assert _level_bucket(0.25) == "LOW"
    assert _level_bucket(0.50) == "MID"
    assert _level_bucket(0.75) == "HIGH"

    history = [0.10, 0.20, 0.30]
    assert _magnitude_bucket(None, history) == "UNKNOWN"
    assert _magnitude_bucket(0.10, history) == "NORMAL"
    assert _magnitude_bucket(0.20, history) == "LARGE"
    assert _magnitude_bucket(0.30, history) == "LARGE"


def test_context_interaction_stats_report_without_promoting_rules():
    rows = [{"net_pct": 0.20}, {"net_pct": -0.10}, {"net_pct": 0.30}]
    stats = _stats(rows)
    assert stats["signals"] == 3
    assert stats["win_rate_pct"] == 66.67
    assert stats["avg_net_return_pct"] == 0.1333
    assert stats["net_return_sum_pct"] == 0.4
    assert stats["profit_factor"] == 5.0
