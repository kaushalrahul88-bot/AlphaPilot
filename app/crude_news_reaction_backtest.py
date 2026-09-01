from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .crude_historical_news import crude_historical_news_v1
from .crude_news_intelligence import apply_crude_news_intelligence

DEFAULT_HORIZONS_MINUTES = (15, 30, 60, 240)


def _clean_rows(candles):
    rows = []
    for row in candles or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            ts = parse_ist_timestamp(row[0])
            o, h, l, c = [float(x) for x in row[1:5]]
            v = float(row[5] or 0.0) if len(row) > 5 else 0.0
        except (TypeError, ValueError):
            continue
        if min(o, h, l, c) <= 0 or h < l:
            continue
        rows.append((ts, o, h, l, c, max(0.0, v)))
    return sorted(rows, key=lambda x: x[0])


def _first_tradable_row(rows, event_at):
    # Conservative timing: never use the bar that starts exactly when a headline
    # becomes visible. The next 5-minute bar is the first replay-tradable anchor.
    return next((i for i, row in enumerate(rows) if row[0] > event_at), None)


def _window(rows, start_index, minutes):
    start = rows[start_index][0]
    end = start + __import__("datetime").timedelta(minutes=minutes)
    selected = [row for row in rows[start_index:] if row[0] < end]
    return selected


def _reaction(rows, start_index, effect, horizons):
    entry = rows[start_index][1]
    sign = 1.0 if effect == "BULLISH" else -1.0
    result = {
        "entry_bar_start": rows[start_index][0].isoformat(),
        "entry_price": entry,
        "horizons": {},
    }
    for minutes in horizons:
        window = _window(rows, start_index, minutes)
        if not window:
            continue
        last = window[-1]
        close_return = (last[4] / entry - 1.0) * 100.0
        max_up = (max(row[2] for row in window) / entry - 1.0) * 100.0
        max_down = (min(row[3] for row in window) / entry - 1.0) * 100.0
        signed_return = sign * close_return
        result["horizons"][str(minutes)] = {
            "close_return_pct": round(close_return, 4),
            "signed_return_pct": round(signed_return, 4),
            "direction_aligned": signed_return > 0,
            "mfe_pct": round(max_up if effect == "BULLISH" else -max_down, 4),
            "mae_pct": round(-max_down if effect == "BULLISH" else max_up, 4),
            "last_bar_start": last[0].isoformat(),
            "bars": len(window),
        }
    return result


def _summary(records, horizons):
    by_horizon = {}
    for minutes in horizons:
        key = str(minutes)
        observations = [r["reaction"]["horizons"][key] for r in records if key in r["reaction"]["horizons"]]
        by_horizon[key] = {
            "events": len(observations),
            "direction_alignment_pct": round(100.0 * sum(x["direction_aligned"] for x in observations) / len(observations), 1) if observations else None,
            "average_signed_return_pct": round(mean(x["signed_return_pct"] for x in observations), 4) if observations else None,
            "average_mfe_pct": round(mean(x["mfe_pct"] for x in observations), 4) if observations else None,
            "average_mae_pct": round(mean(x["mae_pct"] for x in observations), 4) if observations else None,
        }
    families = defaultdict(lambda: {"events": 0, "aligned_60m": 0, "eligible_60m": 0})
    for row in records:
        family = row["news_intelligence"].get("event_type") or "UNKNOWN"
        families[family]["events"] += 1
        h = row["reaction"]["horizons"].get("60")
        if h:
            families[family]["eligible_60m"] += 1
            families[family]["aligned_60m"] += int(h["direction_aligned"])
    family_summary = {}
    for family, data in families.items():
        n = data["eligible_60m"]
        family_summary[family] = {
            **data,
            "alignment_60m_pct": round(100.0 * data["aligned_60m"] / n, 1) if n else None,
        }
    return {"by_horizon_minutes": by_horizon, "by_event_type": family_summary}


def evaluate_crude_news_reactions(candles, news_records=None, horizons=DEFAULT_HORIZONS_MINUTES):
    """Measure post-event MCX Crude reactions without using outcomes to classify news.

    The classifier runs first. Only then are future candles attached. This module is
    deliberately not a strategy optimiser: it does not alter Market Brain thresholds,
    create orders, choose event families from outcomes, or translate to option premium.
    """
    rows = _clean_rows(candles)
    intelligence = apply_crude_news_intelligence(
        crude_historical_news_v1() if news_records is None else news_records
    )
    reactions = []
    skipped = []
    for record in intelligence["allowed_records"]:
        ni = record["news_intelligence"]
        event_at = parse_ist_timestamp(ni["available_at"])
        start_index = _first_tradable_row(rows, event_at)
        if start_index is None:
            skipped.append({"event_id": ni.get("event_id"), "reason": "NO_CANDLE_AFTER_EVENT"})
            continue
        # Do not silently pair a stale event with a much later contract/session.
        gap_hours = (rows[start_index][0] - event_at).total_seconds() / 3600.0
        if gap_hours > 18.0:
            skipped.append({"event_id": ni.get("event_id"), "reason": "NEXT_CANDLE_TOO_FAR_AFTER_EVENT", "gap_hours": round(gap_hours, 2)})
            continue
        reactions.append({
            "event_id": ni.get("event_id"),
            "underlying_event_id": ni.get("underlying_event_id"),
            "available_at": ni.get("available_at"),
            "source": ni.get("source"),
            "headline": ni.get("headline"),
            "news_intelligence": ni,
            "reaction": _reaction(rows, start_index, ni["effect"], tuple(horizons)),
        })
    return {
        "mode": "CRUDE_NEWS_REACTION_BACKTEST_V1",
        "status": "READY" if rows else "NO_CANDLES",
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "option_premium_scored": False,
        "classification_frozen_before_outcomes_attached": True,
        "bar_timing": "GROWW_CANDLE_TIMESTAMP_IS_BAR_START; NEXT_BAR_START_AFTER_EVENT_IS_FIRST_TRADABLE_ANCHOR",
        "input_candles": len(rows),
        "news_counts": intelligence["counts"],
        "scored_directional_events": len(reactions),
        "skipped": skipped,
        "summary": _summary(reactions, tuple(horizons)),
        "events": reactions,
        "limitations": [
            "Seed historical-news coverage is intentionally incomplete.",
            "Historical EIA consensus reconstructed after release cannot vote until pre-release provenance is available.",
            "This scores underlying MCX Crude reaction, not option-premium P&L.",
            "Event reaction evidence must pass a larger sample before integration into Current Mind.",
        ],
    }
