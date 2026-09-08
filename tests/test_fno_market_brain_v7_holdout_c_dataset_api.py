from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import fno_market_brain_v7_holdout_c_dataset_api as api


UTC = timezone.utc


def test_result_blob_round_trip_and_summary_stays_lean():
    result = {
        "status": "COMPLETED",
        "protocol_id": api.PROTOCOL_ID,
        "brain_protocol_id": "BRAIN",
        "brain_frozen_commit": "abc",
        "experiment": {"observations": 123},
        "decision_counts": {"effective_after_thesis_dedup": {"LONG": 1, "SHORT": 2, "NO_TRADE": 120}},
        "data_coverage": {"X": {}},
        "context_coverage": {"NIFTY": 10},
        "missing_optional_context": [],
        "frozen_5m_tape_hashes": {"X": "hash"},
        "frozen_5m_tape_by_stock": {"X": [[1, 2, 3, 4, 5]]},
        "rows": [{"large": "payload"}],
        "safety": {"forward_test": False},
    }
    blob = api._encode_result(result)
    assert api._decode_result(blob) == result
    summary = api._result_summary(result, len(blob))
    assert summary["status"] == "COMPLETED"
    assert summary["result_blob_codec"] == api.RESULT_BLOB_CODEC
    assert summary["result_blob_bytes"] == len(blob)
    assert "rows" not in summary
    assert "frozen_5m_tape_by_stock" not in summary


def test_stale_detection_is_heartbeat_based_and_status_summary_is_read_only_data():
    now = datetime(2026, 9, 8, 11, 0, tzinfo=UTC)
    fresh = {
        "run_id": "r1",
        "protocol_id": api.PROTOCOL_ID,
        "deployment_commit": "sha",
        "status": "RUNNING",
        "attempt_count": 1,
        "heartbeat_at": (now - timedelta(minutes=1)).isoformat(),
        "progress_json": {"stage": "FETCHING_HISTORY"},
        "result_json": None,
        "error": None,
    }
    stale = dict(fresh, heartbeat_at=(now - timedelta(minutes=11)).isoformat())
    assert api._is_stale(fresh, now=now) is False
    assert api._is_stale(stale, now=now) is True
    summary = api._summary(fresh)
    assert summary["status"] == "RUNNING"
    assert summary["progress"] == {"stage": "FETCHING_HISTORY"}
    assert summary["safety"]["forward_test"] is False
