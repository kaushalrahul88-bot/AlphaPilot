"""Prospective Copper Current Mind validation with explicit 5-minute bar completion.

This module is intentionally separate from the frozen August V1 replay.  The
August replay remains reproducible, while forward validation uses timestamps at
which a Groww 5-minute candle is actually knowable: candle_start + 5 minutes.

Phase 1 is preregistered before any scored session is evaluated:
- 2026-09-01 is implementation/shakedown and warm-up only.
- 2026-09-02 through 2026-09-11 is the untouched score window.
- 20 timestamp-only deterministic clicks are fixed per complete MCX session.
- No rule, threshold, seed, or candidate gate may be changed from Phase-1 P&L.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
import hashlib
import random
from statistics import mean
from zoneinfo import ZoneInfo

from .china_copper_macro_context import china_copper_macro_records
from .commodity_backtest import _fetch_chunked, _ts
from .commodity_time import parse_ist_timestamp
from .commodities import resolve_nearest_mcx_future
from .copper_experience_memory import build_experiences, query_memory, _snapshot_vector
from .copper_market_brain_direction_audit import _session_quality
from .copper_research_brain import (
    _build_copper_snapshot_clean,
    _precompute_information_quality,
    clean_ohlcv,
)
from .current_mind_copper_replay import (
    CLICKS_PER_COMPLETE_SESSION,
    MEMORY_K,
    MEMORY_MIN_EDGE_PP,
    MEMORY_MIN_RESOLVED_EACH_SIDE,
    _dominant_direction,
    _evidence_items,
    _f,
    _macro_evidence,
    _missed_move,
    _news_evidence,
    _regime_features,
    _resolve_setup,
    _trade_geometry,
)
from .current_mind_integrated_replay import current_mind_click
from .current_mind_replay_scorecard import replay_scorecard
from .mcx_calendar import mcx_metal_day_schedule

IST = ZoneInfo("Asia/Kolkata")
BAR_MINUTES = 5
BAR_DURATION = timedelta(minutes=BAR_MINUTES)
FORWARD_MODE = "COPPER_CURRENT_MIND_FORWARD_PHASE1_V1"
FORWARD_SEED = "COPPER_CURRENT_MIND_FORWARD_PHASE1_20_CLICKS"
FORWARD_WARMUP_START = datetime(2026, 9, 1, 9, 0, tzinfo=IST)
# Acquisition starts earlier than the registered warm-up day solely so the
# frozen 50-bar perception window exists before the first scored click. These
# additional bars are context only: they never add score sessions or clicks.
FORWARD_PERCEPTION_FETCH_START = FORWARD_WARMUP_START - timedelta(days=7)
FORWARD_SCORE_START = datetime(2026, 9, 2, 0, 0, tzinfo=IST)
FORWARD_SCORE_END = datetime(2026, 9, 11, 23, 59, 59, tzinfo=IST)
FORWARD_EXPECTED_SESSIONS = 8
FORWARD_EXPECTED_CLICKS = FORWARD_EXPECTED_SESSIONS * CLICKS_PER_COMPLETE_SESSION
WARMUP_BARS = 24
TAIL_BARS = 12
MIN_GLOBAL_INDEX = 50


def candle_completed_at(row) -> datetime:
    """Return when a 5-minute Groww candle can first be treated as complete."""
    return parse_ist_timestamp(row[0]) + BAR_DURATION


def _session_close(day: date) -> datetime | None:
    schedule = mcx_metal_day_schedule(day)
    windows = schedule.get("session_windows") or []
    if not windows:
        return None
    raw = str(windows[-1]["end"])
    return datetime.combine(day, time.fromisoformat(raw), tzinfo=IST)


def _scheduled_bar_starts(day: date) -> list[datetime]:
    schedule = mcx_metal_day_schedule(day)
    starts: list[datetime] = []
    for window in schedule.get("session_windows") or []:
        cursor = datetime.combine(day, time.fromisoformat(str(window["start"])), tzinfo=IST)
        end = datetime.combine(day, time.fromisoformat(str(window["end"])), tzinfo=IST)
        while cursor < end:
            starts.append(cursor)
            cursor += BAR_DURATION
    return starts


def scheduled_forward_clicks(day: date) -> list[dict]:
    """Freeze click times from the exchange clock only; prices are never inspected."""
    starts = _scheduled_bar_starts(day)
    if not starts:
        return []
    eligible = starts[WARMUP_BARS:len(starts) - TAIL_BARS if TAIL_BARS else None]
    if len(eligible) < CLICKS_PER_COMPLETE_SESSION:
        return []
    digest = hashlib.sha256(f"{FORWARD_SEED}:{day.isoformat()}".encode()).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    chosen = sorted(rng.sample(eligible, CLICKS_PER_COMPLETE_SESSION))
    return [
        {
            "session": day.isoformat(),
            "source_candle_at": start.isoformat(),
            "click_timestamp": (start + BAR_DURATION).isoformat(),
            "sampling": "PREREGISTERED_TIMESTAMP_ONLY_DETERMINISTIC_RANDOM",
        }
        for start in chosen
    ]


def preregistered_phase1_click_schedule() -> list[dict]:
    out: list[dict] = []
    day = FORWARD_SCORE_START.date()
    while day <= FORWARD_SCORE_END.date():
        out.extend(scheduled_forward_clicks(day))
        day += timedelta(days=1)
    return out


def _experience_available_at(experience: dict) -> datetime:
    """Conservative availability of a historical analogue outcome.

    Experience timestamps identify candle starts.  A path event detected in a
    later candle is knowable only when that later 5-minute candle completes.
    Session-end/no-event outcomes are knowable only at the scheduled session end.
    """
    start = parse_ist_timestamp(experience["timestamp"])
    minutes = experience.get("minutes_to_event")
    if minutes is not None:
        return start + timedelta(minutes=float(minutes)) + BAR_DURATION
    close = _session_close(start.date())
    return close or (start + BAR_DURATION)


def safe_forward_experience_pool(experiences: list[dict], click: datetime) -> list[dict]:
    safe = []
    for experience in experiences:
        observed_at = parse_ist_timestamp(experience["timestamp"]) + BAR_DURATION
        if observed_at >= click:
            continue
        if _experience_available_at(experience) >= click:
            continue
        item = dict(experience)
        item["timestamp"] = observed_at.isoformat()
        item["available_at"] = _experience_available_at(experience).isoformat()
        safe.append(item)
    return safe


def _forward_memory_evidence(experiences: list[dict], features: dict, click: datetime) -> dict:
    query = {
        "timestamp": click.isoformat(),
        "vector": _snapshot_vector(features),
        "structure": features.get("structure"),
        "opening_range_break": features.get("opening_range_break"),
        "price_oi_state": features.get("price_oi_state"),
    }
    safe = safe_forward_experience_pool(experiences, click)
    result = query_memory(safe, query, MEMORY_K)
    item = {
        "lane": "EXPERIENCE",
        "stance": "UNKNOWN",
        "source": "walk_forward_memory_bar_completion_pit",
        "detail": result,
    }
    if result.get("status") != "READY":
        return item
    bullish = (result.get("by_direction") or {}).get("BULLISH") or {}
    bearish = (result.get("by_direction") or {}).get("BEARISH") or {}
    if min(int(bullish.get("resolved") or 0), int(bearish.get("resolved") or 0)) < MEMORY_MIN_RESOLVED_EACH_SIDE:
        return item
    bp = _f(bullish.get("target_first_pct_resolved"))
    sp = _f(bearish.get("target_first_pct_resolved"))
    if bp is None or sp is None or abs(bp - sp) < MEMORY_MIN_EDGE_PP:
        return item
    item["stance"] = "BULLISH" if bp > sp else "BEARISH"
    item["edge_pp"] = round(abs(bp - sp), 2)
    return item


def _outcome_available_at(outcome: dict, click: datetime, day: date) -> datetime:
    result = str((outcome or {}).get("result") or "")
    if result == "INVALID_LEVELS":
        return click
    exit_at = (outcome or {}).get("exit_at")
    if exit_at:
        return parse_ist_timestamp(exit_at) + BAR_DURATION
    close = _session_close(day)
    return close or click


def _previous_market_close(rows: list[list], day: date) -> datetime | None:
    prior = [candle_completed_at(row) for row in rows if parse_ist_timestamp(row[0]).date() < day]
    return max(prior) if prior else None


def _evaluate_one_click(
    *,
    rows: list[list],
    index: int,
    click: datetime,
    reference_contract: str,
    information_quality: list[dict],
    experiences: list[dict],
    macro_records: list[dict],
    memory_cases: list[dict],
    news_records: list[dict] | None,
) -> dict:
    source_start = parse_ist_timestamp(rows[index][0])
    if source_start + BAR_DURATION != click:
        raise RuntimeError("Forward click must occur exactly at source 5-minute candle completion")
    if any(candle_completed_at(row) > click for row in rows[:index + 1]):
        raise RuntimeError("Forward perception contains a candle not completed by click")

    features = _build_copper_snapshot_clean(rows, index, information_quality=information_quality)
    memory = _forward_memory_evidence(experiences, features, click)
    macro = _macro_evidence(click)
    session_start = datetime.combine(click.date(), time(9, 0), tzinfo=IST)
    previous_close = _previous_market_close(rows, click.date())
    news = _news_evidence(
        click,
        news_records,
        session_start=session_start,
        previous_market_bar=previous_close,
    ) if news_records is not None else None
    evidence = _evidence_items(features, memory, macro, news)
    direction = _dominant_direction(evidence)
    market = _regime_features(features)
    geometry = _trade_geometry(rows, index, direction, features) if direction else {}
    market.update(geometry)

    context = [{
        "series": "MCX_COPPER",
        "observed_at": click.isoformat(),
        "available_at": click.isoformat(),
        "source": reference_contract,
        "value": {"price": features.get("price"), "source_candle_at": source_start.isoformat()},
        "quality": "OBSERVED_COMPLETED_5M_BAR",
    }]
    context.extend(record for record in macro_records if parse_ist_timestamp(record["available_at"]) <= click)
    if news_records is not None:
        visible_news = [record for record in news_records if parse_ist_timestamp(record["available_at"]) <= click]
        if visible_news:
            context.append(visible_news[-1])

    journal = current_mind_click(
        click_timestamp=click.isoformat(),
        context_records=context,
        market_features=market,
        evidence_items=evidence,
        memory_cases=memory_cases,
    )
    journal["bar_timing"] = {
        "mode": "GROWW_5M_BAR_COMPLETION_PIT_V1",
        "source_candle_at": source_start.isoformat(),
        "candle_completed_at": click.isoformat(),
        "interval_minutes": BAR_MINUTES,
        "future_candle_visible": False,
    }
    decision = journal["decision"]
    if decision.get("action") in {"BUY_CE", "BUY_PE"} and geometry:
        decision["replay_levels"] = {
            "entry": geometry["entry_price"],
            "stop": geometry["stop_price"],
            "target": geometry["target_price"],
        }
    return journal


def evaluate_forward_window(
    candles,
    *,
    reference_contract: str,
    as_of: datetime,
    news_records: list[dict] | None = None,
    news_metadata: dict | None = None,
) -> dict:
    """Score only complete preregistered Phase-1 sessions through ``as_of``.

    Future bars may exist in the supplied dataset, but every decision is built at
    its preregistered completion timestamp and all perception/memory/context paths
    are clipped to information available strictly by that time.
    """
    as_of = _ts(as_of).astimezone(IST)
    rows = clean_ohlcv(candles)
    if not rows:
        raise ValueError("Forward validation requires Copper 5-minute candles")
    rows = [row for row in rows if candle_completed_at(row) <= as_of]
    quality = _session_quality(rows)
    information_quality = _precompute_information_quality(rows)
    experiences = build_experiences(rows, 3)
    macro_records = china_copper_macro_records()
    index_by_start = {parse_ist_timestamp(row[0]): index for index, row in enumerate(rows)}

    eligible_days: list[date] = []
    excluded_sessions: list[dict] = []
    day = FORWARD_SCORE_START.date()
    score_end = min(as_of, FORWARD_SCORE_END)
    while day <= score_end.date():
        close = _session_close(day)
        if close is None:
            day += timedelta(days=1)
            continue
        if close > as_of:
            excluded_sessions.append({"date": day.isoformat(), "reason": "SESSION_NOT_COMPLETE_AS_OF"})
            day += timedelta(days=1)
            continue
        day_quality = quality.get(day) or {}
        if not day_quality.get("primary_score_eligible"):
            excluded_sessions.append({"date": day.isoformat(), "reason": "INSUFFICIENT_SESSION_CANDLE_COVERAGE", "quality": day_quality})
            day += timedelta(days=1)
            continue
        schedule = scheduled_forward_clicks(day)
        missing = [item["source_candle_at"] for item in schedule if parse_ist_timestamp(item["source_candle_at"]) not in index_by_start]
        if len(schedule) != CLICKS_PER_COMPLETE_SESSION or missing:
            excluded_sessions.append({"date": day.isoformat(), "reason": "MISSING_PREREGISTERED_CLICK_BAR", "missing": missing})
            day += timedelta(days=1)
            continue
        eligible_days.append(day)
        day += timedelta(days=1)

    decisions: list[dict] = []
    memory_cases: list[dict] = []
    for score_day in eligible_days:
        for scheduled in scheduled_forward_clicks(score_day):
            source_start = parse_ist_timestamp(scheduled["source_candle_at"])
            click = parse_ist_timestamp(scheduled["click_timestamp"])
            index = index_by_start[source_start]
            if index < MIN_GLOBAL_INDEX:
                raise RuntimeError(
                    "Preregistered click lacks the frozen 50-bar perception minimum; "
                    f"available_prior_bars={index}, required={MIN_GLOBAL_INDEX}, "
                    f"acquisition_start={FORWARD_PERCEPTION_FETCH_START.isoformat()}"
                )
            journal = _evaluate_one_click(
                rows=rows,
                index=index,
                click=click,
                reference_contract=reference_contract,
                information_quality=information_quality,
                experiences=experiences,
                macro_records=macro_records,
                memory_cases=memory_cases,
                news_records=news_records,
            )
            decision = journal["decision"]
            outcome = _resolve_setup(rows, index, decision)
            if outcome is None:
                outcome = _missed_move(rows, index)
            available_at = _outcome_available_at(outcome, click, score_day)
            outcome = dict(outcome)
            outcome["resolved_at"] = available_at.isoformat()
            journal["outcome"] = outcome
            journal["forward_phase"] = "PHASE1_UNTOUCHED"
            decisions.append(journal)
            memory_cases.append({
                "available_at": available_at.isoformat(),
                "regime": journal.get("regime"),
                "evidence": journal.get("evidence"),
                "action": decision.get("action"),
                "outcome": outcome,
                "decision_fingerprint": journal.get("decision_fingerprint"),
            })

    score = replay_scorecard([
        dict(
            item["decision"],
            outcome=item.get("outcome"),
            lookahead_violation=False,
            contradictions=item["decision"].get("contradictions", []),
            missing_context=item["decision"].get("missing_context", []),
        )
        for item in decisions
    ])
    trades = [item for item in decisions if item["decision"].get("action") in {"BUY_CE", "BUY_PE"}]
    resolved = [item for item in trades if (item.get("outcome") or {}).get("result") in {"TARGET", "STOP"}]
    resolved_r = [float(item["outcome"]["realized_r"]) for item in resolved]
    action_counts = Counter(item["decision"].get("action") for item in decisions)

    completed_phase = len(eligible_days) == FORWARD_EXPECTED_SESSIONS and as_of >= FORWARD_SCORE_END
    return {
        "mode": FORWARD_MODE,
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "trade_instrument": "OPTIONS",
        "underlying_reference_role": "REFERENCE_ONLY",
        "reference_contract": reference_contract,
        "bar_timing": "CANDLE_START_PLUS_5_MINUTES",
        "preregistration": {
            "perception_fetch_start": FORWARD_PERCEPTION_FETCH_START.isoformat(),
            "perception_fetch_only_not_scored": True,
            "warmup_start": FORWARD_WARMUP_START.isoformat(),
            "score_start": FORWARD_SCORE_START.isoformat(),
            "score_end": FORWARD_SCORE_END.isoformat(),
            "phase1_expected_sessions": FORWARD_EXPECTED_SESSIONS,
            "phase1_expected_clicks": FORWARD_EXPECTED_CLICKS,
            "clicks_per_complete_session": CLICKS_PER_COMPLETE_SESSION,
            "seed": FORWARD_SEED,
            "september_1_role": "SHAKEDOWN_AND_WARMUP_NOT_SCORED",
            "performance_based_rule_changes_allowed_during_phase": False,
        },
        "as_of": as_of.isoformat(),
        "eligible_sessions": [day.isoformat() for day in eligible_days],
        "excluded_sessions": excluded_sessions,
        "phase1_complete": completed_phase,
        "scheduled_clicks": len(eligible_days) * CLICKS_PER_COMPLETE_SESSION,
        "evaluated_clicks": len(decisions),
        "click_coverage_exact": len(decisions) == len(eligible_days) * CLICKS_PER_COMPLETE_SESSION,
        "actions": dict(action_counts),
        "trades": len(trades),
        "resolved_trades": len(resolved),
        "targets": sum((item.get("outcome") or {}).get("result") == "TARGET" for item in trades),
        "stops": sum((item.get("outcome") or {}).get("result") == "STOP" for item in trades),
        "no_entry": sum((item.get("outcome") or {}).get("result") == "NO_ENTRY" for item in trades),
        "session_end": sum((item.get("outcome") or {}).get("result") == "SESSION_END" for item in trades),
        "expectancy_r_resolved": round(mean(resolved_r), 3) if resolved_r else None,
        "scorecard": score,
        "decisions": decisions,
        "news_metadata": news_metadata if news_records is not None else None,
        "validation_status": "PHASE1_COMPLETE_READY_FOR_REVIEW" if completed_phase else "WAITING_FOR_PREREGISTERED_FORWARD_SAMPLE",
        "guardrails": [
            "Groww 5-minute candle timestamps are treated as candle starts; OHLC becomes visible only at timestamp + 5 minutes.",
            "Pre-score acquisition is extended only to satisfy the frozen perception history; it creates no additional scored session or click.",
            "September 1 is warm-up/shakedown only and contributes no Phase-1 score.",
            "Phase-1 click timestamps are derived only from the MCX calendar and a frozen deterministic seed.",
            "A scored session is excluded if any preregistered click bar is missing even when aggregate coverage otherwise passes.",
            "Historical analogue outcomes become visible only after the event candle completes; no-event cases become visible only at session close.",
            "Decision-memory outcomes carry explicit availability timestamps and same-timestamp memory is withheld by Current Mind.",
            "Future bars are used only after each decision fingerprint is frozen to score its path.",
            "No performance-based strategy, threshold, playbook, or news-policy changes are allowed during the Phase-1 window.",
        ],
    }


async def run_forward_phase1_from_provider(provider, as_of: datetime | None = None) -> dict:
    """Fetch the active Copper contract and evaluate only completed Phase-1 sessions."""
    as_of = _ts(as_of or datetime.now(IST)).astimezone(IST)
    contract = await resolve_nearest_mcx_future("COPPER", force=True)
    fetch_end = min(as_of, FORWARD_SCORE_END + BAR_DURATION)
    candles = await _fetch_chunked(
        provider,
        contract,
        BAR_MINUTES,
        FORWARD_PERCEPTION_FETCH_START,
        fetch_end,
    )
    report = evaluate_forward_window(
        candles,
        reference_contract=str(contract.get("trading_symbol") or ""),
        as_of=as_of,
    )
    report["contract_metadata"] = {
        "trading_symbol": contract.get("trading_symbol"),
        "groww_symbol": contract.get("groww_symbol"),
        "expiry_date": contract.get("expiry_date"),
        "discovery": "DYNAMIC_NEAREST_ACTIVE_MCX_FUTURE",
        "perception_fetch_start": FORWARD_PERCEPTION_FETCH_START.isoformat(),
    }
    return report
