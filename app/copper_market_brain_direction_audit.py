from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean, median
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .copper_research_brain import (
    _build_copper_snapshot_clean,
    _precompute_information_quality,
    brain_a_signal,
    brain_b_signal,
    clean_ohlcv,
)
from .mcx_calendar import mcx_metal_day_schedule

IST = ZoneInfo("Asia/Kolkata")
REFERENCE_CONTRACT = "COPPER31AUG26FUT"
PRIMARY_START = datetime(2026, 8, 3, 9, 0, tzinfo=IST)
PRIMARY_END = datetime(2026, 8, 28, 23, 30, tzinfo=IST)
HORIZON_BARS = {30: 6, 60: 12, 120: 24}
DEFAULT_SAMPLE_EVERY_BARS = 3
MIN_COMPLETE_SESSION_COVERAGE = 0.95


def _pct(n, d):
    return round(float(n) / float(d) * 100.0, 2) if d else 0.0


def _same_session_future(rows, index, bars):
    entry_day = parse_ist_timestamp(rows[index][0]).date()
    end = index + int(bars)
    if end >= len(rows):
        return None
    future = rows[index + 1:end + 1]
    if len(future) != int(bars):
        return None
    try:
        if any(parse_ist_timestamp(row[0]).date() != entry_day for row in future):
            return None
    except Exception:
        return None
    return future


def _outcome(rows, index, signal, horizon_minutes):
    bars = HORIZON_BARS[int(horizon_minutes)]
    future = _same_session_future(rows, index, bars)
    if not future:
        return None
    entry = float(rows[index][4])
    final = float(future[-1][4])
    raw_forward = (final / entry - 1.0) * 100.0
    if signal == "BUY":
        signed = raw_forward
        favorable = (max(float(row[2]) for row in future) / entry - 1.0) * 100.0
        adverse = max(0.0, (entry / min(float(row[3]) for row in future) - 1.0) * 100.0)
        option_side = "CE"
    elif signal == "SELL":
        signed = -raw_forward
        favorable = (entry / min(float(row[3]) for row in future) - 1.0) * 100.0
        adverse = max(0.0, (max(float(row[2]) for row in future) / entry - 1.0) * 100.0)
        option_side = "PE"
    else:
        return None
    return {
        "signed_forward_pct": signed,
        "direction_correct": signed > 0,
        "favorable_excursion_pct": max(0.0, favorable),
        "adverse_excursion_pct": adverse,
        "option_side_intent": option_side,
    }


def _summary(rows):
    if not rows:
        return {
            "observations": 0,
            "direction_accuracy_pct": 0.0,
            "avg_signed_forward_pct": 0.0,
            "median_signed_forward_pct": 0.0,
            "avg_favorable_excursion_pct": 0.0,
            "avg_adverse_excursion_pct": 0.0,
            "meaningful_move_ge_0_10_pct": 0.0,
            "meaningful_move_ge_0_20_pct": 0.0,
            "meaningful_move_ge_0_30_pct": 0.0,
        }
    signed = [float(row["signed_forward_pct"]) for row in rows]
    favorable = [float(row["favorable_excursion_pct"]) for row in rows]
    adverse = [float(row["adverse_excursion_pct"]) for row in rows]
    return {
        "observations": len(rows),
        "direction_accuracy_pct": _pct(sum(1 for value in signed if value > 0), len(rows)),
        "avg_signed_forward_pct": round(mean(signed), 4),
        "median_signed_forward_pct": round(median(signed), 4),
        "avg_favorable_excursion_pct": round(mean(favorable), 4),
        "avg_adverse_excursion_pct": round(mean(adverse), 4),
        "meaningful_move_ge_0_10_pct": _pct(sum(1 for value in signed if value >= 0.10), len(rows)),
        "meaningful_move_ge_0_20_pct": _pct(sum(1 for value in signed if value >= 0.20), len(rows)),
        "meaningful_move_ge_0_30_pct": _pct(sum(1 for value in signed if value >= 0.30), len(rows)),
    }


def _session_quality(rows):
    grouped = defaultdict(list)
    for row in rows:
        try:
            grouped[parse_ist_timestamp(row[0]).date()].append(row)
        except Exception:
            continue

    quality = {}
    for day, day_rows in grouped.items():
        schedule = mcx_metal_day_schedule(day)
        expected = int(schedule["expected_5m_bars"])
        observed = len({parse_ist_timestamp(row[0]).replace(second=0, microsecond=0) for row in day_rows})
        coverage = (observed / expected) if expected else 0.0
        quality[day] = {
            "date": day.isoformat(),
            "expected_5m_bars": expected,
            "observed_5m_bars": observed,
            "coverage_pct": round(coverage * 100.0, 2),
            "primary_score_eligible": bool(
                schedule["expected_open"]
                and expected > 0
                and coverage >= MIN_COMPLETE_SESSION_COVERAGE
            ),
        }
    return quality


def evaluate_market_brain_direction(
    candles,
    sample_every_bars=DEFAULT_SAMPLE_EVERY_BARS,
):
    rows = clean_ohlcv(candles)
    step = max(1, int(sample_every_bars))
    quality = _session_quality(rows)
    information_quality = _precompute_information_quality(rows)

    reports = {
        "A": {minutes: [] for minutes in HORIZON_BARS},
        "B": {minutes: [] for minutes in HORIZON_BARS},
    }
    excluded_partial_days = sorted(
        row["date"] for row in quality.values()
        if not row["primary_score_eligible"]
    )

    for index in range(50, len(rows), step):
        stamp = parse_ist_timestamp(rows[index][0])
        day_quality = quality.get(stamp.date()) or {}
        if not day_quality.get("primary_score_eligible"):
            continue

        features = _build_copper_snapshot_clean(
            rows,
            index,
            information_quality=information_quality,
        )
        signals = {
            "A": brain_a_signal(features),
            "B": brain_b_signal(features),
        }
        for brain, signal in signals.items():
            if signal == "NO_TRADE":
                continue
            for horizon in HORIZON_BARS:
                outcome = _outcome(rows, index, signal, horizon)
                if outcome is None:
                    continue
                reports[brain][horizon].append({
                    "timestamp": stamp.isoformat(),
                    "signal": signal,
                    "option_side_intent": outcome["option_side_intent"],
                    "structure": features.get("structure"),
                    "entry_reference_price": float(features.get("price")),
                    "horizon_minutes": horizon,
                    **outcome,
                })

    brain_reports = {}
    for brain, by_horizon in reports.items():
        brain_reports[brain] = {
            str(horizon): {
                **_summary(observations),
                "by_signal": {
                    "BUY": _summary([row for row in observations if row["signal"] == "BUY"]),
                    "SELL": _summary([row for row in observations if row["signal"] == "SELL"]),
                },
                "latest_observations": observations[-100:],
            }
            for horizon, observations in by_horizon.items()
        }

    complete_days = sorted(
        row["date"] for row in quality.values()
        if row["primary_score_eligible"]
    )
    return {
        "mode": "COPPER_MARKET_BRAIN_DIRECTION_AUDIT_V1",
        "research_only": True,
        "descriptive_only": True,
        "trade_instrument": "OPTIONS",
        "underlying_reference_role": "REFERENCE_ONLY",
        "futures_pnl_calculated": False,
        "synthetic_option_premium_used": False,
        "same_session_only": True,
        "sample_every_bars": step,
        "sample_interval_minutes": step * 5,
        "horizons_minutes": sorted(HORIZON_BARS),
        "primary_score_days": complete_days,
        "excluded_partial_days": excluded_partial_days,
        "session_quality": [
            quality[day] for day in sorted(quality)
        ],
        "brains": brain_reports,
        "interpretation": {
            "brain_a": "Frozen technical baseline.",
            "brain_b": "Frozen structure/participation/regime filter layered on Brain A.",
            "option_mapping": "BUY directional context implies CE intent; SELL implies PE intent. No exact option contract or option P&L is inferred here.",
            "magnitude": "Meaningful-move rates measure correctly signed underlying movement of at least 0.10%, 0.20%, or 0.30% within the stated horizon.",
        },
        "guardrails": [
            "No future bar is used in feature construction.",
            "Forward outcomes never cross into a later trading date.",
            "Provider-confirmed sparse sessions are excluded from the primary score rather than filled synthetically.",
            "Overlapping 15-minute checkpoints are descriptive observations, not independent trades.",
            "This audit measures whether AlphaPilot reads Copper direction/magnitude; it is not evidence of option profitability.",
        ],
    }


async def run_market_brain_direction_audit_from_store(
    store,
    sample_every_bars=DEFAULT_SAMPLE_EVERY_BARS,
):
    await store.initialize()
    segments = await store.read_symbol_contract_segments(
        "COPPER",
        5,
        PRIMARY_START,
        PRIMARY_END,
    )
    target = next(
        (
            segment for segment in segments
            if str(segment.get("trading_symbol") or "").upper() == REFERENCE_CONTRACT
        ),
        None,
    )
    if not target:
        raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    candles = target.get("candles") or []
    if len(candles) < 500:
        raise RuntimeError(f"Insufficient stored {REFERENCE_CONTRACT} history ({len(candles)} candles)")

    report = evaluate_market_brain_direction(
        candles,
        sample_every_bars=sample_every_bars,
    )
    report["reference_contract"] = {
        "trading_symbol": target.get("trading_symbol"),
        "expiry_date": target.get("expiry_date"),
        "candles": len(candles),
        "start": str(candles[0][0]) if candles else None,
        "end": str(candles[-1][0]) if candles else None,
    }
    return report
