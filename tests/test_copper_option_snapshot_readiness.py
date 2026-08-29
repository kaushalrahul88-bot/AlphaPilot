from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.copper_option_snapshot_readiness import summarize_snapshot_readiness


IST=ZoneInfo("Asia/Kolkata")


def row(day,buckets,snapshots,ce,pe,underlying,two_sided,contracts=20):
    return (
        date.fromisoformat(day),
        buckets,
        snapshots,
        ce,
        pe,
        underlying,
        two_sided,
        datetime.fromisoformat(day+"T09:05:00+05:30"),
        datetime.fromisoformat(day+"T23:25:00+05:30"),
        contracts,
    )


def test_readiness_is_descriptive_and_no_data_is_not_a_failure():
    result=summarize_snapshot_readiness([])
    assert result["status"]=="NO_DATA"
    assert result["strategy_gate"] is False
    assert result["promotion_eligible"] is False
    assert result["snapshots"]==0


def test_readiness_reports_bucket_side_and_quote_coverage():
    rows=[
        row("2026-08-31",170,3400,1700,1700,3400,2720),
        row("2026-09-01",160,3200,1600,1600,3040,1600),
    ]
    result=summarize_snapshot_readiness(rows)
    assert result["status"]=="ACCUMULATING"
    assert result["trading_days"]==2
    assert result["snapshots"]==6600
    assert result["ce_snapshots"]==3300
    assert result["pe_snapshots"]==3300
    assert result["days_with_both_option_sides"]==2
    assert result["underlying_price_coverage_pct"]==94.55
    assert result["two_sided_quote_coverage_pct"]==65.45
    assert result["median_distinct_buckets_per_day"]==165.0
    assert result["daily"][0]["both_option_sides_present"] is True


def test_twenty_days_only_unlocks_descriptive_replay_review_not_promotion():
    rows=[
        row(f"2026-09-{day:02d}",150,3000,1500,1500,3000,2400)
        for day in range(1,21)
    ]
    result=summarize_snapshot_readiness(rows)
    assert result["status"]=="DESCRIPTIVE_REPLAY_SAMPLE_AVAILABLE"
    assert result["trading_days"]==20
    assert result["strategy_gate"] is False
    assert result["promotion_eligible"] is False
