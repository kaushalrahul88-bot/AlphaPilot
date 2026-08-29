from datetime import datetime, timezone

from app.copper_fx_level_downtrend_forward_validation import (
    DISCOVERY_CUTOFF,
    _gate,
    _is_low_fx,
)


def test_low_fx_threshold_is_frozen():
    assert _is_low_fx({"usdinr_expanding_percentile":0.25}) is True
    assert _is_low_fx({"usdinr_expanding_percentile":0.2501}) is False
    assert _is_low_fx({"usdinr_expanding_percentile":None}) is False


def test_forward_cutoff_is_frozen_and_timezone_aware():
    assert DISCOVERY_CUTOFF == datetime(2026,8,29,12,16,54,tzinfo=timezone.utc)


def test_gate_requires_sample_expectancy_pf_and_baseline_improvement():
    baseline=[{"net_pct":0.01,"usdinr_expanding_percentile":0.5} for _ in range(20)]
    candidate=[{"net_pct":0.10,"usdinr_expanding_percentile":0.2} for _ in range(20)]
    report=_gate(baseline,candidate)
    assert report["checks"]["minimum_20_signals"] is True
    assert report["checks"]["positive_avg_net_return"] is True
    assert report["checks"]["beats_contemporaneous_baseline"] is True
    assert report["checks"]["known_context_only"] is True
