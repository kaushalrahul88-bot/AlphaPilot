from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .copper_research_brain import (
    _brain_a_attribution_observations,
    _f,
    _segment_stats,
    build_copper_experiences,
)
from .commodity_time import parse_ist_timestamp


IST = ZoneInfo("Asia/Kolkata")
RESEARCH_CUTOFF_AT = datetime.fromisoformat("2026-08-28T23:00:00+05:30")

# Frozen from the preregistered four-window development study.
# These are hypotheses only, not production filters.
AVOIDANCE_HYPOTHESES_V1 = (
    {
        "id": "BUY_UPPER_QUARTER",
        "conditions": {"signal": "BUY", "session_location_bucket": "UPPER_QUARTER"},
        "reason": "Brain A BUY at the upper session quartile was negative in all four development windows.",
    },
    {
        "id": "BUY_VWAP_ABOVE_FAR",
        "conditions": {"signal": "BUY", "vwap_location_bucket": "ABOVE_FAR"},
        "reason": "Brain A BUY far above session VWAP was negative in all four development windows.",
    },
    {
        "id": "BUY_OPENING_RANGE_ABOVE",
        "conditions": {"signal": "BUY", "opening_range_break": "ABOVE"},
        "reason": "Brain A BUY above the completed opening range was negative in all four development windows.",
    },
    {
        "id": "SELL_UPPER_MIDDLE",
        "conditions": {"signal": "SELL", "session_location_bucket": "UPPER_MIDDLE"},
        "reason": "Brain A SELL in the upper-middle session location was negative in all four development windows.",
    },
    {
        "id": "SELL_VWAP_BELOW_NEAR",
        "conditions": {"signal": "SELL", "vwap_location_bucket": "BELOW_NEAR"},
        "reason": "Brain A SELL only slightly below session VWAP was negative in all four development windows.",
    },
    {
        "id": "SELL_OPENING_RANGE_INSIDE",
        "conditions": {"signal": "SELL", "opening_range_break": "INSIDE"},
        "reason": "Brain A SELL while price remained inside the completed opening range was negative in all four development windows.",
    },
    {
        "id": "SELL_TADVOL_NORMAL",
        "conditions": {"signal": "SELL", "time_adjusted_volume_bucket": "NORMAL"},
        "reason": "Brain A SELL on merely normal same-time cumulative volume was negative in all four development windows.",
    },
)

MIN_VALIDATION_TRADING_DAYS = 10
MIN_AVOIDED_SIGNALS = 40
MAX_AVOIDED_PROFIT_FACTOR = 0.80


def _matches(row, conditions):
    return all(row.get(key) == value for key, value in conditions.items())


def matched_avoidance_hypotheses(row):
    return [
        hypothesis["id"]
        for hypothesis in AVOIDANCE_HYPOTHESES_V1
        if _matches(row, hypothesis["conditions"])
    ]


def _dates(rows):
    days = set()
    for row in rows:
        try:
            days.add(parse_ist_timestamp(row.get("timestamp")).date())
        except Exception:
            continue
    return days


def evaluate_forward_avoidance(experiences, cutoff_at=RESEARCH_CUTOFF_AT):
    observations = _brain_a_attribution_observations(
        experiences, horizon_minutes=60, round_trip_cost_bps=4.0,
    )
    future = []
    for row in observations:
        try:
            stamp = parse_ist_timestamp(row.get("timestamp"))
        except Exception:
            continue
        if stamp <= cutoff_at:
            continue
        matched = matched_avoidance_hypotheses(row)
        future.append({**row, "matched_avoidance_hypotheses": matched})

    avoided = [row for row in future if row["matched_avoidance_hypotheses"]]
    kept = [row for row in future if not row["matched_avoidance_hypotheses"]]
    all_stats = _segment_stats(future)
    avoided_stats = _segment_stats(avoided)
    kept_stats = _segment_stats(kept)
    days = sorted(_dates(future))

    avoided_pf = _f(avoided_stats.get("profit_factor"))
    all_pf = _f(all_stats.get("profit_factor"), 0.0) or 0.0
    kept_pf = _f(kept_stats.get("profit_factor"), 0.0) or 0.0
    enough_data = len(days) >= MIN_VALIDATION_TRADING_DAYS and len(avoided) >= MIN_AVOIDED_SIGNALS
    avoided_is_harmful = (
        len(avoided) > 0
        and avoided_stats["avg_net_return_pct"] < 0
        and avoided_pf is not None
        and avoided_pf < MAX_AVOIDED_PROFIT_FACTOR
    )
    filter_improves = (
        kept_stats["avg_net_return_pct"] > all_stats["avg_net_return_pct"]
        and kept_pf > all_pf
        and kept_stats["net_return_sum_pct"] > all_stats["net_return_sum_pct"]
    )
    validated = enough_data and avoided_is_harmful and filter_improves

    hypothesis_stats = []
    for hypothesis in AVOIDANCE_HYPOTHESES_V1:
        rows = [
            row for row in future
            if hypothesis["id"] in row["matched_avoidance_hypotheses"]
        ]
        hypothesis_stats.append({
            "id": hypothesis["id"],
            "conditions": hypothesis["conditions"],
            "signals": len(rows),
            "trading_days": len(_dates(rows)),
            "stats": _segment_stats(rows),
        })

    return {
        "mode": "COPPER_AVOIDANCE_FORWARD_VALIDATION_V1",
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "research_cutoff_at": cutoff_at.isoformat(),
        "validation_only_after_cutoff": True,
        "status": "VALIDATED_HYPOTHESIS" if validated else "COLLECTING" if not enough_data else "NOT_VALIDATED",
        "requirements": {
            "minimum_validation_trading_days": MIN_VALIDATION_TRADING_DAYS,
            "minimum_avoided_signals": MIN_AVOIDED_SIGNALS,
            "maximum_avoided_profit_factor": MAX_AVOIDED_PROFIT_FACTOR,
            "filter_must_improve_expectancy": True,
            "filter_must_improve_profit_factor": True,
            "filter_must_improve_net_return_sum": True,
        },
        "coverage": {
            "future_brain_a_signals": len(future),
            "validation_trading_days": len(days),
            "first_validation_day": days[0].isoformat() if days else None,
            "last_validation_day": days[-1].isoformat() if days else None,
            "avoided_signals": len(avoided),
            "kept_signals": len(kept),
        },
        "comparison": {
            "unfiltered_brain_a": all_stats,
            "frozen_avoidance_contexts": avoided_stats,
            "brain_a_after_hypothetical_avoidance": kept_stats,
        },
        "gates": {
            "enough_fresh_data": enough_data,
            "avoided_contexts_remain_harmful": avoided_is_harmful,
            "hypothetical_filter_improves_brain_a": filter_improves,
            "validated": validated,
        },
        "hypotheses": hypothesis_stats,
        "guardrail": (
            "This report cannot change production decisions. A validated result only permits "
            "a later explicit promotion review; it does not self-enable an avoidance filter."
        ),
    }


async def run_copper_avoidance_forward_validation_from_store(
    store,
    days=365,
    sample_every_bars=3,
):
    end = datetime.now(IST)
    start = min(RESEARCH_CUTOFF_AT - timedelta(days=7), end - timedelta(days=max(21, int(days))))
    step = max(1, min(int(sample_every_bars), 12))
    await store.initialize()
    segments = await store.read_symbol_contract_segments("COPPER", 5, start, end)

    experiences = []
    segments_used = []
    for segment in segments:
        candles = segment.get("candles") or []
        built = build_copper_experiences(candles, sample_every_bars=step)
        experiences.extend(built)
        segments_used.append({
            "trading_symbol": segment.get("trading_symbol"),
            "expiry_date": segment.get("expiry_date"),
            "candles": len(candles),
            "experiences": len(built),
        })
    experiences.sort(key=lambda item: str((item.get("features") or {}).get("timestamp") or ""))

    report = evaluate_forward_avoidance(experiences)
    return {
        **report,
        "data_source": "POSTGRES_COMMODITY_CANDLES",
        "contract_segments": segments_used,
        "frozen_hypothesis_count": len(AVOIDANCE_HYPOTHESES_V1),
    }
