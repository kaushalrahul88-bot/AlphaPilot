from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from math import sqrt
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_direction_brain_v2 import evaluate_direction_brain_v2_shadow
from .crude_oil_mini_market_perception import BAR_MINUTES, bar_visible_at, clean_ohlcv
from .mcx_calendar import mcx_metal_day_schedule

IST = ZoneInfo("Asia/Kolkata")
MODE = "CRUDE_OIL_MINI_DIRECTION_V2_PROSPECTIVE_FORWARD_V1"
VALIDATION_PHASE = "CRUDE_OIL_MINI_DIRECTION_V2_PHASE_1"
VALIDATION_START_DATE = date(2026, 9, 3)
VALIDATION_SESSIONS = 10
CLICKS_PER_SESSION = 20
WARMUP_BARS = 24
TAIL_BARS = 24  # Preserves the preregistered +120m secondary horizon before session close.
CLICK_SEED = "CRUDEOILM_DIRECTION_V2_PROSPECTIVE_PHASE_1"
HORIZONS = (15, 30, 60, 120)
PRIMARY_HORIZON_MINUTES = 60
MIN_PRIMARY_DIRECTIONAL_CALLS = 50
MIN_CAPTURE_COVERAGE_PCT = 95.0
WILSON_Z = 1.96
DIRECTIONAL = {"BULLISH", "BEARISH"}


def _canonical_sha256(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _session_bar_starts(day: date) -> list[datetime]:
    """Build the exchange-clock candidate grid without consulting price or outcomes."""
    schedule = mcx_metal_day_schedule(day)
    starts: list[datetime] = []
    for window in schedule.get("session_windows") or []:
        start = datetime.combine(day, datetime.strptime(window["start"], "%H:%M").time(), tzinfo=IST)
        end = datetime.combine(day, datetime.strptime(window["end"], "%H:%M").time(), tzinfo=IST)
        cursor = start
        while cursor + timedelta(minutes=BAR_MINUTES) <= end:
            starts.append(cursor)
            cursor += timedelta(minutes=BAR_MINUTES)
    return sorted(set(starts))


def eligible_clock_slots(day: date) -> list[datetime]:
    starts = _session_bar_starts(day)
    if len(starts) <= WARMUP_BARS + TAIL_BARS:
        return []
    return starts[WARMUP_BARS : len(starts) - TAIL_BARS]


def _day_seed(day: date) -> int:
    digest = hashlib.sha256(f"{CLICK_SEED}|{day.isoformat()}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def scheduled_clicks_for_day(day: date) -> list[dict]:
    """Return exactly 20 deterministic clock-only prospective clicks when feasible."""
    slots = eligible_clock_slots(day)
    if not mcx_metal_day_schedule(day).get("expected_open"):
        return []
    if len(slots) < CLICKS_PER_SESSION:
        raise RuntimeError(
            f"{day.isoformat()} has only {len(slots)} eligible slots after fixed warmup/tail guards"
        )
    selected = sorted(random.Random(_day_seed(day)).sample(slots, CLICKS_PER_SESSION))
    return [
        {
            "validation_phase": VALIDATION_PHASE,
            "session": day.isoformat(),
            "bar_start": stamp.isoformat(),
            "click_timestamp": (stamp + timedelta(minutes=BAR_MINUTES)).isoformat(),
            "sampling": "DETERMINISTIC_CLOCK_ONLY_SHA256_SEEDED_WITHOUT_REPLACEMENT",
        }
        for stamp in selected
    ]


def validation_days() -> list[date]:
    """Freeze the first ten expected-open MCX sessions from 3 Sep 2026 onward."""
    days: list[date] = []
    cursor = VALIDATION_START_DATE
    for _ in range(45):
        if mcx_metal_day_schedule(cursor).get("expected_open"):
            days.append(cursor)
            if len(days) == VALIDATION_SESSIONS:
                return days
        cursor += timedelta(days=1)
    raise RuntimeError("Unable to resolve ten expected-open MCX sessions for Phase 1")


def phase_schedule() -> list[dict]:
    rows = [row for day in validation_days() for row in scheduled_clicks_for_day(day)]
    expected = VALIDATION_SESSIONS * CLICKS_PER_SESSION
    if len(rows) != expected:
        raise RuntimeError(f"Phase-1 schedule must contain exactly {expected} clock-only clicks")
    return rows


def _schedule_index() -> dict[str, dict]:
    return {row["click_timestamp"]: row for row in phase_schedule()}


def capture_shadow_direction(
    *,
    click_timestamp: str,
    snapshot: dict,
    profile: dict,
    context_records: list[dict],
    direction_memory_cases: list[dict] | None = None,
) -> dict:
    """Freeze the V2 shadow thesis at the click, before any forward outcome is knowable."""
    click = parse_ist_timestamp(click_timestamp)
    canonical_click = click.isoformat()
    schedule = _schedule_index()
    if canonical_click not in schedule:
        raise ValueError("Prospective V2 capture is allowed only at a preregistered Phase-1 clock click")

    source_bar_raw = snapshot.get("timestamp")
    if not source_bar_raw:
        raise ValueError("A completed CRUDEOILM source bar timestamp is required")
    source_bar = parse_ist_timestamp(source_bar_raw)
    source_available = source_bar + timedelta(minutes=BAR_MINUTES)
    if source_available > click:
        raise ValueError("Snapshot contains a CRUDEOILM bar that was not complete at the click")
    source_price = snapshot.get("price")
    try:
        source_price = float(source_price)
    except (TypeError, ValueError):
        raise ValueError("A positive CRUDEOILM source price is required") from None
    if source_price <= 0:
        raise ValueError("A positive CRUDEOILM source price is required")

    shadow = evaluate_direction_brain_v2_shadow(
        click_timestamp=canonical_click,
        snapshot=snapshot,
        profile=profile or {},
        context_records=context_records or [],
        direction_memory_cases=direction_memory_cases or [],
    )
    capture = {
        "mode": MODE,
        "validation_phase": VALIDATION_PHASE,
        "research_only": True,
        "shadow_only": True,
        "promotion_allowed": False,
        "click_timestamp": canonical_click,
        "session": click.date().isoformat(),
        "scheduled": schedule[canonical_click],
        "source_bar_start": source_bar.isoformat(),
        "source_bar_available_at": source_available.isoformat(),
        "source_price": source_price,
        "direction": shadow.get("direction"),
        "direction_confidence": shadow.get("direction_confidence"),
        "thesis_state": shadow.get("thesis_state"),
        "supporting_families": list(shadow.get("supporting_families") or []),
        "opposing_families": list(shadow.get("opposing_families") or []),
        "families": shadow.get("families") or {},
        "modifiers": shadow.get("modifiers") or {},
        "persistence": shadow.get("persistence"),
        "available_context_series": list(shadow.get("available_context_series") or []),
        "decision_effect": "NONE",
        "geometry_effect": "NONE",
        "option_effect": "NONE",
    }
    return {**capture, "capture_fingerprint": _canonical_sha256(capture)}


def mature_underlying_outcome(capture: dict, candles, *, as_of=None) -> dict:
    """Attach underlying horizon outcomes separately without mutating the frozen thesis."""
    if capture.get("mode") != MODE or not capture.get("capture_fingerprint"):
        raise ValueError("A frozen Direction V2 prospective capture is required")
    click = parse_ist_timestamp(capture["click_timestamp"])
    now = parse_ist_timestamp(as_of) if as_of is not None else None
    source_price = float(capture["source_price"])
    rows = clean_ohlcv(candles)
    by_available = {bar_visible_at(row): row for row in rows}

    horizons = {}
    for minutes in HORIZONS:
        target_available = click + timedelta(minutes=minutes)
        if now is not None and target_available > now:
            horizons[str(minutes)] = {
                "status": "PENDING",
                "target_available_at": target_available.isoformat(),
            }
            continue
        row = by_available.get(target_available)
        if row is None:
            horizons[str(minutes)] = {
                "status": "MISSING_EXACT_COMPLETED_BAR",
                "target_available_at": target_available.isoformat(),
            }
            continue
        close = float(row[4])
        horizons[str(minutes)] = {
            "status": "MATURED",
            "available_at": target_available.isoformat(),
            "bar_start": parse_ist_timestamp(row[0]).isoformat(),
            "close": close,
            "underlying_return_pct": round((close / source_price - 1.0) * 100.0, 6),
        }

    outcome = {
        "mode": "CRUDE_OIL_MINI_DIRECTION_V2_FORWARD_OUTCOME_V1",
        "validation_phase": VALIDATION_PHASE,
        "research_only": True,
        "capture_fingerprint": capture["capture_fingerprint"],
        "click_timestamp": capture["click_timestamp"],
        "horizons": horizons,
        "trade_outcome_used": False,
        "geometry_used": False,
        "option_pnl_used": False,
    }
    return {**outcome, "outcome_fingerprint": _canonical_sha256(outcome)}


def _signed_result(direction: str, raw_return) -> str:
    try:
        value = float(raw_return)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if value == 0:
        return "FLAT"
    if direction == "BULLISH":
        return "CORRECT" if value > 0 else "WRONG"
    if direction == "BEARISH":
        return "CORRECT" if value < 0 else "WRONG"
    return "ABSTAIN"


def _wilson_interval(correct: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    p = correct / total
    z2 = WILSON_Z**2
    denom = 1.0 + z2 / total
    centre = (p + z2 / (2.0 * total)) / denom
    margin = WILSON_Z * sqrt((p * (1.0 - p) / total) + z2 / (4.0 * total**2)) / denom
    return [round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4)]


def _score_subset(pairs: list[tuple[dict, dict]], minutes: int) -> dict:
    results = []
    for capture, outcome in pairs:
        horizon = (outcome.get("horizons") or {}).get(str(minutes)) or {}
        if horizon.get("status") != "MATURED":
            continue
        results.append(_signed_result(str(capture.get("direction") or "UNKNOWN"), horizon.get("underlying_return_pct")))
    counts = Counter(results)
    known = counts["CORRECT"] + counts["WRONG"] + counts["FLAT"]
    directional_actual = counts["CORRECT"] + counts["WRONG"]
    interval = _wilson_interval(counts["CORRECT"], directional_actual)
    return {
        "matured": len(results),
        "correct": counts["CORRECT"],
        "wrong": counts["WRONG"],
        "flat": counts["FLAT"],
        "abstain": counts["ABSTAIN"],
        "unknown": counts["UNKNOWN"],
        "directional_actual": directional_actual,
        "accuracy_pct": round(counts["CORRECT"] / directional_actual * 100.0, 2) if directional_actual else None,
        "accuracy_wilson_95": interval,
        "known_outcomes": known,
    }


def evaluate_phase(captures: list[dict], outcomes: list[dict]) -> dict:
    """Score the frozen prospective phase; never search or change a Direction V2 rule."""
    scheduled = phase_schedule()
    schedule_by_click = {row["click_timestamp"]: row for row in scheduled}
    valid_captures = {}
    for capture in captures or []:
        click = str(capture.get("click_timestamp") or "")
        if capture.get("mode") != MODE or click not in schedule_by_click:
            continue
        valid_captures.setdefault(click, capture)

    outcomes_by_capture = {}
    for outcome in outcomes or []:
        fingerprint = str(outcome.get("capture_fingerprint") or "")
        if fingerprint:
            outcomes_by_capture.setdefault(fingerprint, outcome)

    pairs = [
        (capture, outcomes_by_capture[capture["capture_fingerprint"]])
        for capture in valid_captures.values()
        if capture.get("capture_fingerprint") in outcomes_by_capture
    ]
    captures_by_day = Counter(parse_ist_timestamp(click).date().isoformat() for click in valid_captures)
    complete_capture_days = sum(captures_by_day.get(day.isoformat(), 0) == CLICKS_PER_SESSION for day in validation_days())
    capture_coverage = len(valid_captures) / len(scheduled) * 100.0 if scheduled else 0.0

    horizons = {str(minutes): _score_subset(pairs, minutes) for minutes in HORIZONS}
    primary = horizons[str(PRIMARY_HORIZON_MINUTES)]
    directional_primary_calls = primary["correct"] + primary["wrong"]
    coverage_ok = capture_coverage >= MIN_CAPTURE_COVERAGE_PCT
    sessions_ok = complete_capture_days == VALIDATION_SESSIONS
    calls_ok = directional_primary_calls >= MIN_PRIMARY_DIRECTIONAL_CALLS
    data_sufficient = coverage_ok and sessions_ok and calls_ok
    interval = primary.get("accuracy_wilson_95")
    discrimination_evidence = bool(interval and interval[0] > 0.5)

    confidence_groups = defaultdict(list)
    for pair in pairs:
        confidence_groups[str(pair[0].get("direction_confidence") or "UNKNOWN")].append(pair)

    return {
        "mode": MODE,
        "validation_phase": VALIDATION_PHASE,
        "research_only": True,
        "shadow_only": True,
        "status": "READY_FOR_REVIEW" if data_sufficient else "COLLECTING",
        "promotion_allowed": False,
        "strategy_rules_changed": False,
        "threshold_search_performed": False,
        "geometry_used": False,
        "option_pnl_used": False,
        "schedule": {
            "first_day": validation_days()[0].isoformat(),
            "last_day": validation_days()[-1].isoformat(),
            "expected_open_sessions": VALIDATION_SESSIONS,
            "clicks_per_session": CLICKS_PER_SESSION,
            "scheduled_clicks": len(scheduled),
            "warmup_bars": WARMUP_BARS,
            "tail_bars": TAIL_BARS,
            "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
            "secondary_horizons_minutes": [15, 30, 120],
        },
        "coverage": {
            "captured_clicks": len(valid_captures),
            "capture_coverage_pct": round(capture_coverage, 2),
            "complete_capture_days": complete_capture_days,
            "paired_outcome_records": len(pairs),
            "directional_primary_calls": directional_primary_calls,
        },
        "requirements": {
            "minimum_capture_coverage_pct": MIN_CAPTURE_COVERAGE_PCT,
            "all_ten_sessions_complete": True,
            "minimum_primary_directional_calls": MIN_PRIMARY_DIRECTIONAL_CALLS,
        },
        "gates": {
            "capture_coverage_ok": coverage_ok,
            "all_sessions_complete": sessions_ok,
            "enough_primary_directional_calls": calls_ok,
            "data_sufficient_for_review": data_sufficient,
        },
        "horizon_score": horizons,
        "confidence_score": {
            confidence: _score_subset(group, PRIMARY_HORIZON_MINUTES)
            for confidence, group in sorted(confidence_groups.items())
        },
        "descriptive_evidence": {
            "primary_accuracy_lower_95_above_50pct": discrimination_evidence,
            "interpretation": (
                "A lower 95% accuracy bound above 50% is descriptive evidence of out-of-sample directional "
                "discrimination. It does not self-promote the shadow brain or authorize geometry/option tuning."
            ),
        },
        "guardrails": [
            "The clock schedule was frozen before Phase-1 outcomes and does not depend on price.",
            "Captured Direction V2 theses are fingerprinted before future returns mature.",
            "Only underlying 15/30/60/120-minute returns are scored; TARGET/STOP and option P&L are excluded.",
            "No rule, threshold, family, seed, click schedule or confidence definition may change during Phase 1.",
            "READY_FOR_REVIEW is a data-sufficiency state, not promotion.",
            "Any production promotion requires a separate explicit review and independent replication.",
        ],
    }


def preregistration_contract() -> dict:
    days = validation_days()
    return {
        "mode": MODE,
        "validation_phase": VALIDATION_PHASE,
        "frozen_before_first_validation_session": True,
        "validation_start": days[0].isoformat(),
        "validation_end_session": days[-1].isoformat(),
        "expected_open_sessions": VALIDATION_SESSIONS,
        "scheduled_clicks": VALIDATION_SESSIONS * CLICKS_PER_SESSION,
        "clock_only_schedule": True,
        "click_seed": CLICK_SEED,
        "warmup_bars": WARMUP_BARS,
        "tail_bars": TAIL_BARS,
        "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "secondary_horizons_minutes": [15, 30, 120],
        "minimum_capture_coverage_pct": MIN_CAPTURE_COVERAGE_PCT,
        "minimum_primary_directional_calls": MIN_PRIMARY_DIRECTIONAL_CALLS,
        "online_memory_policy": (
            "Direction Memory may expand only with cases whose full stored horizon set was already resolved and "
            "available strictly before the current click; memory algorithm and thresholds remain frozen."
        ),
        "june_august_rule": "Inspected development data may seed prior memory but cannot tune Phase-1 rules or thresholds.",
        "promotion_allowed": False,
        "current_mind_mutation_allowed": False,
        "geometry_tuning_allowed": False,
        "option_pnl_tuning_allowed": False,
    }
