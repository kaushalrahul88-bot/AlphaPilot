from datetime import datetime, timedelta, timezone

from app.crypto_btc_enriched24h_underlying_backtest import (
    Enriched24hReadinessPolicy,
    evaluate_enriched_24h_readiness,
)
from app.crypto_deribit_options_pit import DATASET as OPTIONS_CONTEXT_DATASET
from app.crypto_stablecoin_pit_capture import STABLECOIN_SUPPLY_DATASET

UTC = timezone.utc
BASE = datetime(2026, 9, 8, 4, 30, tzinfo=UTC)
POLICY = Enriched24hReadinessPolicy(
    options_min_prior_samples=20,
    options_max_age_seconds=15 * 60,
    stablecoin_comparison_hours=24,
    stablecoin_max_age_seconds=2 * 60 * 60,
).validated()


def _options(start: datetime, end: datetime) -> list[datetime]:
    rows = []
    at = start
    while at <= end:
        rows.append(at)
        at += timedelta(minutes=5)
    return rows


def _stable(start: datetime, end: datetime) -> list[datetime]:
    rows = []
    at = start
    while at <= end:
        rows.append(at)
        at += timedelta(hours=1)
    return rows


def test_readiness_waits_for_real_24h_stablecoin_comparison_history():
    checked = BASE + timedelta(hours=7)
    result = evaluate_enriched_24h_readiness(
        {
            OPTIONS_CONTEXT_DATASET: _options(BASE, checked),
            STABLECOIN_SUPPLY_DATASET: _stable(BASE, checked),
        },
        as_of=checked,
        policy=POLICY,
    )

    assert result["ready"] is False
    assert result["status"] == "BUILDING_STABLECOIN_COMPARISON_HISTORY"
    assert result["covered_clicks"] == 0
    assert result["theoretical_earliest_start"] == (BASE + timedelta(hours=24)).isoformat()
    assert result["theoretical_earliest_complete"] == (BASE + timedelta(hours=48)).isoformat()
    assert result["safety"]["capital_committed_inr"] == 0


def test_full_contiguous_enriched_window_becomes_ready_only_after_outcome_window_exists():
    start = BASE + timedelta(hours=24)
    end = start + timedelta(hours=24)
    timestamps = {
        OPTIONS_CONTEXT_DATASET: _options(BASE - timedelta(hours=2), end + timedelta(minutes=5)),
        STABLECOIN_SUPPLY_DATASET: _stable(BASE, end + timedelta(hours=1)),
    }

    before_end = evaluate_enriched_24h_readiness(
        timestamps,
        as_of=end - timedelta(minutes=10),
        policy=POLICY,
    )
    assert before_end["ready"] is False

    result = evaluate_enriched_24h_readiness(
        timestamps,
        as_of=end + timedelta(minutes=1),
        policy=POLICY,
    )
    assert result["ready"] is True
    assert result["status"] == "READY"
    assert result["window_start"] == start.isoformat()
    assert result["window_end_exclusive"] == end.isoformat()
    assert result["covered_clicks"] == 96
    assert result["coverage_pct"] == 100.0


def test_options_gap_prevents_false_ready_window():
    start = BASE + timedelta(hours=24)
    end = start + timedelta(hours=24)
    options = _options(BASE - timedelta(hours=2), end + timedelta(minutes=5))
    gap_start = start + timedelta(hours=8)
    gap_end = gap_start + timedelta(minutes=45)
    options = [row for row in options if not (gap_start <= row <= gap_end)]

    result = evaluate_enriched_24h_readiness(
        {
            OPTIONS_CONTEXT_DATASET: options,
            STABLECOIN_SUPPLY_DATASET: _stable(BASE, end + timedelta(hours=1)),
        },
        as_of=end + timedelta(minutes=1),
        policy=POLICY,
    )
    assert result["ready"] is False
    assert result["covered_clicks"] < 96
    assert result["first_incomplete_reason"] == "OPTIONS_CONTEXT_MISSING_OR_STALE"


def test_future_rows_cannot_make_an_earlier_as_of_ready():
    start = BASE + timedelta(hours=24)
    end = start + timedelta(hours=24)
    timestamps = {
        OPTIONS_CONTEXT_DATASET: _options(BASE - timedelta(hours=2), end + timedelta(days=1)),
        STABLECOIN_SUPPLY_DATASET: _stable(BASE, end + timedelta(days=1)),
    }
    as_of = start + timedelta(hours=3)
    result = evaluate_enriched_24h_readiness(timestamps, as_of=as_of, policy=POLICY)
    assert result["ready"] is False
    assert result["window_end_exclusive"] == end.isoformat()
    assert result["covered_clicks"] <= 13
