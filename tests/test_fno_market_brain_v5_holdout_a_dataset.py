from __future__ import annotations

import asyncio
import inspect
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import fno_market_brain_v5_holdout_a_dataset as dataset
from app import fno_market_brain_v5_holdout_a_dataset_api as dataset_api
from app.fno_market_brain_v3_current_expiry_dataset import events_at

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def test_holdout_a_universe_windows_and_frozen_brain():
    assert dataset.STOCKS == ("HDFCBANK", "INFY", "MARUTI", "BHARTIARTL")
    assert dataset.WINDOWS == (
        ("JUNE_2026", date(2026, 6, 1), date(2026, 6, 12)),
        ("JULY_2026", date(2026, 7, 1), date(2026, 7, 14)),
    )
    assert dataset.BRAIN_FROZEN_COMMIT == "a5a39253c1aa79f6663cd11b41f7d6607b96af90"


def test_clicks_are_deterministic_unique_and_within_frozen_pool():
    day = date(2026, 6, 4)
    first = dataset.deterministic_clicks(day)
    second = dataset.deterministic_clicks(day)
    assert first == second
    assert len(first) == dataset.CLICKS_PER_DAY == 20
    assert len(set(first)) == 20
    local = [stamp.astimezone(IST) for stamp in first]
    assert min(stamp.time() for stamp in local).isoformat() >= "09:30:00"
    assert max(stamp.time() for stamp in local).isoformat() <= "14:00:00"


def test_clicks_outside_holdout_are_rejected():
    try:
        dataset.deterministic_clicks(date(2026, 8, 31))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a date outside frozen Holdout A windows")


def test_july_inflation_event_is_point_in_time_only():
    knowledge = dataset.load_knowledge()
    events = list(knowledge["events"])
    before = events_at(
        "HDFCBANK",
        "PRIVATE_BANKING",
        datetime(2026, 7, 13, 14, 0, tzinfo=IST),
        events,
    )
    after = events_at(
        "HDFCBANK",
        "PRIVATE_BANKING",
        datetime(2026, 7, 14, 9, 30, tzinfo=IST),
        events,
    )
    before_ids = {item["id"] for item in before}
    after_ids = {item["id"] for item in after}
    assert "INDIA_CPI_2026_07_13" not in before_ids
    assert "INDIA_CPI_2026_07_13" in after_ids


def test_holdout_contract_blocks_forward_and_outcome_evaluation():
    safety = dataset.architecture_contract()
    assert safety["historical_backtest_dataset_only"] is True
    assert safety["forward_test"] is False
    assert safety["future_outcomes_resolved"] is False
    assert safety["evaluation_metrics_computed"] is False
    assert safety["options_read_for_decision"] is False
    assert safety["futures_read_for_decision"] is False
    assert safety["v3_development_rows_used_as_holdout"] is False
    assert safety["live_execution"] is False
    assert safety["capital_committed"] == 0
    assert safety["history_transport_cache_only"] is True
    assert safety["history_cache_may_change_decisions"] is False


def test_knowledge_archive_was_frozen_without_outcomes():
    knowledge = dataset.load_knowledge()
    policy = knowledge["policy"]
    assert policy["outcomes_used_to_build_archive"] is False
    assert policy["future_bars_used_for_decision"] is False
    assert policy["options_used_for_decision"] is False
    assert policy["futures_used_for_decision"] is False


def test_history_segment_cache_reuses_exact_transport_window():
    calls = []
    cache = {}
    start = datetime(2026, 6, 1, 9, 15, tzinfo=IST)
    end = datetime(2026, 6, 2, 15, 30, tzinfo=IST)
    rows = [[1, 100.0, 101.0, 99.0, 100.5, 1000.0]]

    async def fake_chunk(provider, symbol, timeframe, window_start, window_end):
        calls.append((symbol, timeframe, window_start, window_end))
        return list(rows)

    async def cache_get(symbol, timeframe, window_start, window_end):
        return cache.get((symbol, timeframe, window_start, window_end))

    async def cache_put(symbol, timeframe, window_start, window_end, value):
        cache[(symbol, timeframe, window_start, window_end)] = list(value)

    async def run_twice():
        first, _ = await dataset._fetch_cached(
            object(), "INFY", "15m", start, end,
            cache_get=cache_get, cache_put=cache_put,
        )
        second, _ = await dataset._fetch_cached(
            object(), "INFY", "15m", start, end,
            cache_get=cache_get, cache_put=cache_put,
        )
        return first, second

    with patch.object(dataset.baseline, "_chunk", new=fake_chunk), patch.object(
        dataset.baseline, "_merge", side_effect=lambda value: value
    ):
        first, second = asyncio.run(run_twice())

    assert first == rows
    assert second == rows
    assert len(calls) == 1


def test_stale_worker_detection_is_time_based():
    now = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
    recent = {
        "status": "RUNNING",
        "heartbeat_at": (now - timedelta(minutes=2)).isoformat(),
    }
    stale = {
        "status": "RUNNING",
        "heartbeat_at": (now - timedelta(minutes=20)).isoformat(),
    }
    assert dataset_api._is_stale(recent, now=now) is False
    assert dataset_api._is_stale(stale, now=now) is True


def test_status_and_result_polling_are_read_only():
    source = inspect.getsource(dataset_api.register_fno_market_brain_v5_holdout_a_dataset_routes)
    status_block = source.split('@app.get("/v1/internal/fno/v5-holdout-a-dataset/status")', 1)[1]
    status_block, result_block = status_block.split('@app.get("/v1/internal/fno/v5-holdout-a-dataset/result")', 1)
    assert "_worker(" not in status_block
    assert "_worker(" not in result_block
    start_block = source.split('@app.post("/v1/internal/fno/v5-holdout-a-dataset/start")', 1)[1].split(
        '@app.get("/v1/internal/fno/v5-holdout-a-dataset/status")', 1
    )[0]
    assert "_worker(" in start_block


def test_full_result_compression_round_trip_preserves_frozen_payload():
    payload = {
        "status": "COMPLETED",
        "protocol_id": dataset.PROTOCOL_ID,
        "brain_frozen_commit": dataset.BRAIN_FROZEN_COMMIT,
        "experiment": {"future_outcomes_resolved": False},
        "rows": [
            {"symbol": "INFY", "click_at": "2026-06-01T04:00:00+00:00", "decision": {"action": "NO_TRADE"}},
            {"symbol": "HDFCBANK", "click_at": "2026-06-01T04:05:00+00:00", "decision": {"action": "LONG"}},
        ],
        "frozen_5m_tape_by_stock": {"INFY": [[1, 2, 3, 4, 5, 6]]},
        "safety": dataset.architecture_contract(),
    }
    blob = dataset_api._encode_result(payload)
    restored = dataset_api._decode_result(blob)
    assert restored == payload
    assert len(blob) < len(str(payload).encode("utf-8"))
    assert dataset_api.RESULT_BLOB_CODEC == "zlib-json-v1"
    assert sum(dataset_api.FINAL_DB_RETRY_DELAYS_SECONDS) >= 240
