from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_oil_mini_option_premium_memory_v1 import analyze_premium_memory_rows


IST = ZoneInfo("Asia/Kolkata")


def _row(
    stamp,
    *,
    symbol="CRUDEOILM17SEP268600CE",
    option_type="CE",
    strike=8600,
    underlying=8600,
    premium=300,
    observed=None,
    collected=None,
    underlying_symbol="CRUDEOILM",
):
    observed = observed or stamp + timedelta(seconds=10)
    collected = collected or stamp + timedelta(seconds=15)
    return {
        "underlying_symbol": underlying_symbol,
        "trading_symbol": symbol,
        "expiry_date": "2026-09-17",
        "strike": strike,
        "option_type": option_type,
        "lot_size": 10,
        "sample_bucket_at": stamp.isoformat(),
        "observed_at": observed.isoformat(),
        "collected_at": collected.isoformat(),
        "underlying_price": underlying,
        "last_price": premium,
        "volume": 1000,
        "open_interest": 500,
        "bid_price": None,
        "ask_price": None,
    }


def test_future_and_wrong_underlying_rows_are_excluded():
    base = datetime(2026, 9, 4, 14, 0, tzinfo=IST)
    rows = [
        _row(base),
        _row(base + timedelta(minutes=10), underlying=8610, premium=305),
        _row(base + timedelta(minutes=20), underlying=8620, premium=310),
        _row(base + timedelta(minutes=40), underlying=8640, premium=320),
        _row(base + timedelta(minutes=5), underlying_symbol="CRUDEOIL"),
    ]
    result = analyze_premium_memory_rows(rows, as_of=base + timedelta(minutes=25))

    assert result["snapshot_count"] == 3
    assert result["contract_count"] == 1
    assert result["response_segments"] == 2
    assert result["pit_filter"] == "sample_bucket_at, observed_at and collected_at must all be <= as_of"
    assert result["risk_translation_effect"] == "NONE"
    assert result["current_mind_effect"] == "NONE"
    assert result["promotion_eligible"] is False


def test_exact_contract_segments_do_not_cross_large_or_overnight_gaps():
    day = datetime(2026, 9, 3, 14, 0, tzinfo=IST)
    rows = [
        _row(day, underlying=8600, premium=300),
        _row(day + timedelta(minutes=10), underlying=8610, premium=305),
        _row(day + timedelta(minutes=50), underlying=8620, premium=310),
        _row(day + timedelta(days=1), underlying=8630, premium=315),
    ]
    result = analyze_premium_memory_rows(rows, as_of=day + timedelta(days=1, minutes=5))
    contract = result["contracts"][0]

    assert contract["snapshot_count"] == 4
    assert contract["response_segments"] == 1
    assert contract["max_intraday_gap_minutes"] == 40.0


def test_put_sensitivity_is_direction_normalized():
    base = datetime(2026, 9, 4, 14, 0, tzinfo=IST)
    rows = [
        _row(
            base,
            symbol="CRUDEOILM17SEP268600PE",
            option_type="PE",
            underlying=8600,
            premium=300,
        ),
        _row(
            base + timedelta(minutes=10),
            symbol="CRUDEOILM17SEP268600PE",
            option_type="PE",
            underlying=8590,
            premium=305,
        ),
    ]
    result = analyze_premium_memory_rows(rows, as_of=base + timedelta(minutes=15))
    contract = result["contracts"][0]

    assert contract["sensitivity_segments"] == 1
    assert contract["median_directional_sensitivity"] == 0.5


def test_descriptive_readiness_requires_both_sides_and_multiple_days():
    rows = []
    for day_offset in (0, 1):
        start = datetime(2026, 9, 2 + day_offset, 14, 0, tzinfo=IST)
        for index in range(6):
            stamp = start + timedelta(minutes=10 * index)
            rows.append(
                _row(
                    stamp,
                    symbol="CRUDEOILM17SEP268600CE",
                    option_type="CE",
                    underlying=8600 + index * 2,
                    premium=300 + index,
                )
            )
            rows.append(
                _row(
                    stamp,
                    symbol="CRUDEOILM17SEP268600PE",
                    option_type="PE",
                    underlying=8600 + index * 2,
                    premium=300 - index,
                )
            )

    result = analyze_premium_memory_rows(
        rows,
        as_of=datetime(2026, 9, 3, 16, 0, tzinfo=IST),
    )

    assert result["status"] == "DESCRIPTIVE_READY"
    assert result["response_segments"] == 20
    assert result["trading_days"] == 2
    assert {item["option_type"] for item in result["contracts"]} == {"CE", "PE"}
    assert result["promotion_eligible"] is False
    assert result["live_execution_enabled"] is False
    assert result["broker_order_placement_enabled"] is False
    assert result["capital_committed"] == 0


def test_duplicate_bucket_keeps_first_collected_observation():
    base = datetime(2026, 9, 4, 14, 0, tzinfo=IST)
    first = _row(base, premium=300, collected=base + timedelta(seconds=5))
    later_duplicate = _row(base, premium=999, collected=base + timedelta(seconds=30))
    second = _row(base + timedelta(minutes=10), underlying=8610, premium=305)

    result = analyze_premium_memory_rows(
        [later_duplicate, second, first],
        as_of=base + timedelta(minutes=15),
    )

    assert result["snapshot_count"] == 2
    assert result["response_segments"] == 1
