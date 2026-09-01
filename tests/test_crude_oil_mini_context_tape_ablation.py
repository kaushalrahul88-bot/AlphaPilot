from __future__ import annotations

import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.crude_oil_mini_context_ablation import evaluate_crude_context_ablation
from app.crude_oil_mini_context_tape import canonicalize_context_probe, certify_context_tape

IST = ZoneInfo("Asia/Kolkata")


def _feed(ticker: str, closes: list[float]):
    start = datetime(2026, 8, 18, 10, 0, tzinfo=IST)
    data = []
    for i, close in enumerate(closes):
        stamp = start + timedelta(hours=i)
        data.append({
            "bar_start": stamp.isoformat(),
            "available_at": (stamp + timedelta(hours=1)).isoformat(),
            "open": close - 0.2,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close,
            "volume": 1000 + i,
        })
    return {
        "status": "AVAILABLE",
        "ticker": ticker,
        "bar_minutes": 60,
        "source": "fixture",
        "data": data,
    }


def _payload():
    probe = {
        "feeds": {
            "WTI_CRUDE": _feed("CL=F", [80.0, 81.0, 82.0]),
            "BRENT_CRUDE": _feed("BZ=F", [84.0, 85.0, 86.0]),
            "USDINR": _feed("INR=X", [83.0, 83.1, 83.2]),
            "DXY": _feed("DX-Y.NYB", [100.0, 99.9, 99.8]),
        }
    }
    return canonicalize_context_probe(probe)


def _replay():
    return {
        "decisions": [{
            "session": "2026-08-18",
            "click_timestamp": "2026-08-18T12:05:00+05:30",
            "action": "BUY_CE",
            "outcome": {"result": "TARGET", "realized_r": 1.5},
            "future_returns_pct": {"15": 0.1, "30": 0.2, "60": 0.3},
        }]
    }


def test_context_tape_is_completed_hour_and_discovery_only():
    payload = _payload()
    report = certify_context_tape(payload)
    assert report["status"] == "CERTIFIED_DISCOVERY"
    assert report["promotion_eligible"] is False
    assert report["source_grade"] == "E_DISCOVERY"
    assert report["tape_sha256"]
    assert report["integrity_errors"] == []
    assert payload["governance"]["outcomes_used_to_build_tape"] is False
    assert payload["governance"]["authorized_or_independent_validation_required_before_promotion"] is True


def test_context_ablation_only_filters_existing_trade():
    report = evaluate_crude_context_ablation(_replay(), _payload())
    assert report["decision_path_changed"] is False
    assert report["baseline_decisions_mutated"] is False
    assert report["promotion_allowed"] is False
    assert report["variants"]["A"]["summary"]["trades"] == 1
    assert report["variants"]["B"]["summary"]["trades"] == 1
    assert report["variants"]["C"]["summary"]["trades"] == 1


def test_context_ablation_variant_selection_is_outcome_blind():
    baseline = _replay()
    target_report = evaluate_crude_context_ablation(baseline, _payload())
    altered = copy.deepcopy(baseline)
    altered["decisions"][0]["outcome"] = {"result": "STOP", "realized_r": -1.0}
    altered["decisions"][0]["future_returns_pct"] = {"15": -0.1, "30": -0.2, "60": -0.3}
    stop_report = evaluate_crude_context_ablation(altered, _payload())
    target_row = target_report["rows"][0]
    stop_row = stop_report["rows"][0]
    assert target_row["context_state"] == stop_row["context_state"]
    assert target_row["variant_B_action"] == stop_row["variant_B_action"]
    assert target_row["variant_C_action"] == stop_row["variant_C_action"]
    assert target_report["variants"]["C"]["summary"]["expectancy_r_resolved"] != stop_report["variants"]["C"]["summary"]["expectancy_r_resolved"]


def test_opposing_global_context_turns_trade_to_wait_without_reversal():
    payload = _payload()
    for series in ("WTI_CRUDE", "BRENT_CRUDE"):
        rows = payload["feeds"][series]["rows"]
        rows[-2]["close"] = rows[-3]["close"] - 1.0
    # At 12:05 the latest visible hourly row is 11:00 (available 12:00), so use a
    # falling 10:00 -> 11:00 sequence for both global benchmarks.
    for series in ("WTI_CRUDE", "BRENT_CRUDE"):
        rows = payload["feeds"][series]["rows"]
        rows[1]["close"] = rows[0]["close"] - 1.0
    report = evaluate_crude_context_ablation(_replay(), payload)
    row = report["rows"][0]
    assert row["variant_A_action"] == "BUY_CE"
    assert row["variant_B_action"] == "WAIT"
    assert row["variant_C_action"] == "WAIT"
