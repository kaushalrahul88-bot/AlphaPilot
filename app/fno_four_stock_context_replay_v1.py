"""Replay the frozen four-stock baseline with point-in-time market context."""
from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime, time, timedelta

from . import fno_candle_only_four_stock_backtest_v1 as baseline
from .fno_four_stock_context_enrichment_v1 import (
    CONTEXT_SYMBOLS,
    PROTOCOL_ID,
    architecture_contract,
    context_at,
    enriched_decision,
    _load_events,
)
from .fno_underlying_random_replay_v1 import _summarize


def _directional_block(block, action):
    output = copy.deepcopy(block or {})
    raw = output.get("raw_return_pct")
    max_up = output.get("max_up_pct")
    max_down = output.get("max_down_pct")
    if action == "LONG":
        output["directional_return_pct"] = raw
        output["mfe_pct"] = max_up
        output["mae_pct"] = max_down
    elif action == "SHORT":
        output["directional_return_pct"] = -float(raw) if raw is not None else None
        output["mfe_pct"] = -float(max_down) if max_down is not None else None
        output["mae_pct"] = -float(max_up) if max_up is not None else None
    else:
        output["directional_return_pct"] = None
        output["mfe_pct"] = None
        output["mae_pct"] = None
    return output


def outcome_for_action(base_outcome, action):
    """Reuse the baseline's frozen future tape; never fetch a different future path."""
    result = copy.deepcopy(base_outcome or {})
    result["checkpoints"] = {
        key: _directional_block(value, action)
        for key, value in (result.get("checkpoints") or {}).items()
    }
    result["eod"] = _directional_block(result.get("eod") or {}, action)
    return result


def _comparison(rows):
    changes = Counter()
    for row in rows:
        before = row["baseline_decision"]["action"]
        after = row["decision"]["action"]
        changes[f"{before}->{after}"] += 1
    return {
        "decision_transitions": dict(sorted(changes.items())),
        "changed_decisions": sum(count for key, count in changes.items() if key.split("->")[0] != key.split("->")[1]),
        "promoted_from_no_trade": sum(count for key, count in changes.items() if key.startswith("NO_TRADE->") and not key.endswith("NO_TRADE")),
        "vetoed_to_no_trade": sum(count for key, count in changes.items() if key.endswith("->NO_TRADE") and not key.startswith("NO_TRADE->")),
    }


def _context_audit(rows, history_errors, event_count):
    complete = sum(row["context"]["complete"] is True for row in rows)
    with_events = sum(bool(row["context"].get("events")) for row in rows)
    sources = Counter()
    for row in rows:
        for event in row["context"].get("events") or []:
            sources[str(event.get("source_quality") or "UNKNOWN")] += 1
    return {
        "observations": len(rows),
        "context_complete": complete,
        "context_complete_rate_pct": round(100 * complete / len(rows), 4) if rows else None,
        "observations_with_recent_event": with_events,
        "observations_with_recent_event_rate_pct": round(100 * with_events / len(rows), 4) if rows else None,
        "frozen_event_archive_events": event_count,
        "event_source_quality_occurrences": dict(sorted(sources.items())),
        "history_errors": history_errors,
        "lookahead_violations": sum(
            1 for row in rows for event in row["context"].get("events") or []
            if datetime.fromisoformat(str(event["effective_at"])) > datetime.fromisoformat(str(row["click_at"]))
        ),
    }


async def run_four_stock_context_replay(provider, baseline_result):
    if not isinstance(baseline_result, dict) or baseline_result.get("status") != "COMPLETED":
        return {"protocol_id": PROTOCOL_ID, "status": "VALIDATED_BASELINE_REQUIRED", "safety": architecture_contract()}
    base_rows = baseline_result.get("rows") or []
    if len(base_rows) != 1600:
        return {"protocol_id": PROTOCOL_ID, "status": "BASELINE_OBSERVATION_COUNT_MISMATCH", "observations": len(base_rows), "safety": architecture_contract()}

    sessions = [
        datetime.fromisoformat(day).date()
        for days in (baseline_result.get("experiment") or {}).get("tested_sessions_by_stock", {}).values()
        for day in days
    ]
    if not sessions:
        return {"protocol_id": PROTOCOL_ID, "status": "BASELINE_SESSION_METADATA_MISSING", "safety": architecture_contract()}

    start = datetime.combine(min(sessions) - timedelta(days=7), time(9, 15), tzinfo=baseline.IST)
    end = datetime.combine(baseline.END_DATE, time(15, 30), tzinfo=baseline.IST)
    symbols = tuple(sorted(set(CONTEXT_SYMBOLS).union(baseline.STOCKS)))
    histories = {}
    errors = []
    for symbol in symbols:
        try:
            histories[symbol] = await baseline._chunk(provider, symbol, "15m", start, end)
            if not histories[symbol]:
                errors.append({"symbol": symbol, "error": "EMPTY_15M_CONTEXT_TAPE"})
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{exc.__class__.__name__}: {str(exc)[:500]}"})

    required = {"NIFTY", *baseline.STOCKS}
    missing_required = sorted(symbol for symbol in required if not histories.get(symbol))
    if missing_required:
        return {
            "protocol_id": PROTOCOL_ID,
            "status": "REQUIRED_CONTEXT_DATA_INCOMPLETE",
            "missing_required": missing_required,
            "history_errors": errors,
            "safety": architecture_contract(),
        }

    events = _load_events()
    rows = []
    for base_row in base_rows:
        click = datetime.fromisoformat(str(base_row["click_at"]))
        symbol = str(base_row["symbol"])
        context = context_at(symbol, click, histories, histories, events)
        decision = enriched_decision(base_row["decision"], base_row["technical"], context)
        action = decision["action"]
        rows.append({
            "trade_date": base_row["trade_date"],
            "click_at": base_row["click_at"],
            "symbol": symbol,
            "category": base_row["category"],
            "baseline_decision": base_row["decision"],
            "decision": decision,
            "technical": base_row["technical"],
            "context": context,
            "outcome": outcome_for_action(base_row["outcome"], action),
        })

    summary = _summarize(rows)
    baseline_summary = baseline_result.get("summary") or {}
    trade_dates = sorted({row["trade_date"] for row in rows})
    return {
        "protocol_id": PROTOCOL_ID,
        "status": "COMPLETED",
        "experiment": {
            "baseline_protocol": baseline_result.get("protocol_id"),
            "baseline_observations": len(base_rows),
            "observations": len(rows),
            "same_frozen_clicks": True,
            "same_frozen_future_tape": True,
            "stocks": (baseline_result.get("experiment") or {}).get("stocks"),
            "sessions_by_stock": (baseline_result.get("experiment") or {}).get("tested_sessions_by_stock"),
        },
        "baseline_summary": baseline_summary,
        "enriched_summary": summary,
        "comparison": _comparison(rows),
        "context_audit": _context_audit(rows, errors, len(events)),
        "by_stock": {
            symbol: {
                "baseline": (baseline_result.get("by_stock") or {}).get(symbol),
                "enriched": _summarize([row for row in rows if row["symbol"] == symbol]),
                "comparison": _comparison([row for row in rows if row["symbol"] == symbol]),
            }
            for symbol in baseline.STOCKS
        },
        "by_trade_date": {
            day: _summarize([row for row in rows if row["trade_date"] == day])
            for day in trade_dates
        },
        "context_data_coverage": {
            symbol: {
                "candles": len(histories.get(symbol) or []),
                "first": str((histories.get(symbol) or [[None]])[0][0]) if histories.get(symbol) else None,
                "last": str((histories.get(symbol) or [[None]])[-1][0]) if histories.get(symbol) else None,
            }
            for symbol in symbols
        },
        "rows": rows,
        "methodology": {
            "context_timeframe": "15m",
            "context_momentum_lookback_minutes": 60,
            "market_context": ["NIFTY", "BANKNIFTY_FOR_SBIN"],
            "sector_context": "FROZEN_CASH_PEER_BASKETS",
            "stock_relative_strength_vs_nifty": True,
            "point_in_time_event_archive": True,
            "event_unknown_time_policy": "NEXT_SESSION_OPEN",
            "future_tape_reused_from_validated_baseline": True,
            "outcome_refetch_forbidden": True,
            "thresholds_frozen_before_result": True,
        },
        "safety": architecture_contract(),
    }
