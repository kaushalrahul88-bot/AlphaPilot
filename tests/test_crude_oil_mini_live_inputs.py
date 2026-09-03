from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.crude_oil_mini_direction_brain_v2_integrated import evaluate_integrated_direction_v2_shadow
from app.crude_oil_mini_live_inputs import prepare_news_context, summarize_option_positioning


IST = timezone(timedelta(hours=5, minutes=30))


def _option_row(bucket: str, symbol: str, option_type: str, strike: float, oi: float, premium: float):
    return {
        "trading_symbol": symbol,
        "expiry_date": "2026-09-17",
        "strike": strike,
        "option_type": option_type,
        "lot_size": 10,
        "sample_bucket_at": bucket,
        "observed_at": bucket,
        "collected_at": bucket,
        "underlying_price": 8633.0,
        "last_price": premium,
        "volume": 100.0,
        "open_interest": oi,
        "bid_price": premium - 0.5,
        "ask_price": premium + 0.5,
    }


def test_option_positioning_uses_latest_and_previous_pit_buckets_without_direction_guess():
    previous = "2026-09-03T23:00:00+05:30"
    latest = "2026-09-03T23:05:00+05:30"
    rows = [
        _option_row(previous, "CRUDEOILM17SEP268600CE", "CE", 8600, 1000, 300),
        _option_row(previous, "CRUDEOILM17SEP268600PE", "PE", 8600, 2000, 310),
        _option_row(previous, "CRUDEOILM17SEP268650CE", "CE", 8650, 1200, 280),
        _option_row(previous, "CRUDEOILM17SEP268650PE", "PE", 8650, 1600, 335),
        _option_row(latest, "CRUDEOILM17SEP268600CE", "CE", 8600, 1100, 295),
        _option_row(latest, "CRUDEOILM17SEP268600PE", "PE", 8600, 2400, 315),
        _option_row(latest, "CRUDEOILM17SEP268650CE", "CE", 8650, 1400, 275),
        _option_row(latest, "CRUDEOILM17SEP268650PE", "PE", 8650, 1800, 340),
    ]
    result = summarize_option_positioning(rows, "2026-09-03T23:07:00+05:30")
    assert result["status"] == "AVAILABLE"
    assert result["sample_bucket_at"] == latest
    assert result["previous_sample_bucket_at"] == previous
    assert result["ce_total_oi"] == 2500
    assert result["pe_total_oi"] == 4200
    assert result["ce_total_oi_change_from_previous_bucket"] == 300
    assert result["pe_total_oi_change_from_previous_bucket"] == 600
    assert result["put_call_oi_ratio"] == 4200 / 2500
    assert result["direction"] == "UNKNOWN"
    assert result["counts_for_direction"] is False
    assert result["futures_oi_required"] is False


def test_news_uses_first_detected_time_and_never_turns_headline_into_confirmed_reaction():
    rows = [
        {
            "event_id": "hormuz-1",
            "series": "CRUDE_NEWS",
            "headline": "Shipping disruption reported in Strait of Hormuz after attack",
            "source": "Reuters",
            "published_at": "2026-09-03T16:50:00+00:00",
            "observed_at": "2026-09-03T22:25:00+05:30",
            "available_at": "2026-09-03T22:25:00+05:30",
            "novelty": "NEW",
            "value": {"headline": "Shipping disruption reported in Strait of Hormuz after attack"},
        },
        {
            "event_id": "future-1",
            "series": "CRUDE_NEWS",
            "headline": "Production halted at major exporter",
            "source": "Reuters",
            "published_at": "2026-09-03T17:02:00+00:00",
            "observed_at": "2026-09-03T23:10:00+05:30",
            "available_at": "2026-09-03T23:10:00+05:30",
            "novelty": "NEW",
            "value": {"headline": "Production halted at major exporter"},
        },
    ]
    result = prepare_news_context(rows, "2026-09-03T23:00:00+05:30")
    assert result["status"] == "AVAILABLE"
    assert result["visible_count"] == 1
    assert result["transmitted_count"] == 1
    assert result["pit_basis"] == "FIRST_DETECTED_AT"
    event = result["event_records"][0]
    assert event["event_id"] == "hormuz-1"
    assert event["value"]["mechanism_stance"] == "BULLISH"
    assert event["value"]["reaction"]["confirmed"] is False
    assert event["value"]["reaction"]["confirmation_sources"] == []


def _global_feed():
    rows = []
    start = datetime(2026, 9, 3, 14, 0, tzinfo=IST)
    for i in range(8):
        level = 90.0 + i
        rows.append(
            {
                "bar_start": (start + timedelta(hours=i)).isoformat(),
                "available_at": (start + timedelta(hours=i + 1)).isoformat(),
                "open": level,
                "high": level + 1.0,
                "low": level - 1.0,
                "close": level + 0.4,
                "volume": 1000 + i * 10,
            }
        )
    return {"status": "AVAILABLE", "source": "TEST", "bar_minutes": 60, "data": rows}


def _mini_candles():
    rows = []
    start = datetime(2026, 9, 3, 22, 30, tzinfo=IST)
    for i in range(6):
        level = 8600.0 + i * 3.0
        rows.append(
            [
                (start + timedelta(minutes=5 * i)).isoformat(),
                level,
                level + 5,
                level - 4,
                level + 2,
                100 + i * 20,
                None,
            ]
        )
    return rows


def test_v2_receives_option_oi_as_primary_context_but_raw_oi_does_not_vote():
    option_positioning = {
        "status": "AVAILABLE",
        "sample_bucket_at": "2026-09-03T22:55:00+05:30",
        "ce_total_oi": 13000,
        "pe_total_oi": 21000,
        "put_call_oi_ratio": 21 / 13,
        "counts_for_direction": False,
        "futures_oi_required": False,
    }
    result = evaluate_integrated_direction_v2_shadow(
        click_timestamp="2026-09-03T23:00:00+05:30",
        snapshot={
            "structure": "UPTREND",
            "return_15m_pct": 0.3,
            "return_60m_pct": 0.8,
            "time_adjusted_relative_volume": 2.0,
            "session_vwap_gap_pct": 0.5,
        },
        profile={"participation_confirming": 1.0},
        mini_candles=_mini_candles(),
        global_context_probe={"feeds": {"WTI_CRUDE": _global_feed(), "BRENT_CRUDE": _global_feed()}},
        context_records=[],
        event_records=[],
        direction_memory_cases=[],
        option_positioning=option_positioning,
    )
    participation = result["families"]["PARTICIPATION"]
    assert participation["state"] == "OPTION_POSITIONING_CONTEXT_ONLY"
    assert participation["independence_status"] == "INDEPENDENT_CONTEXT_ONLY"
    assert participation["counts_for_direction"] is False
    assert participation["detail"]["option_positioning_primary"] is True
    assert participation["detail"]["futures_oi_required"] is False
