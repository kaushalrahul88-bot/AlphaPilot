from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.commodity_time import parse_ist_timestamp
from app.copper_market_brain_abstention_audit import normalize_candle_rows
from app.current_mind_copper_replay import evaluate_current_mind_replay
from app.current_mind_news_reaction_comparison import compare_no_news_vs_reaction_guard
from scripts.audit_market_news_reactions import audit as audit_news_reactions


def _candles(payload):
    if isinstance(payload, dict):
        return list(payload.get("candles") or payload.get("records") or [])
    return list(payload or [])


def _latest_timestamp(candles: list[dict]) -> str:
    parsed = []
    for candle in candles:
        if isinstance(candle, dict):
            raw = candle.get("timestamp") or candle.get("time") or candle.get("datetime")
        elif isinstance(candle, (list, tuple)) and candle:
            raw = candle[0]
        else:
            raw = None
        if raw is None:
            continue
        try:
            parsed.append(parse_ist_timestamp(raw))
        except (TypeError, ValueError):
            continue
    if not parsed:
        raise RuntimeError("Frozen candle artifact has no valid timestamps")
    return max(parsed).isoformat()


def run(news_payload: dict, candle_payload) -> dict:
    candles = _candles(candle_payload)
    rows = normalize_candle_rows(candles)
    if not rows:
        raise RuntimeError("Frozen candle artifact has no usable OHLC rows")
    baseline = evaluate_current_mind_replay(rows)
    reaction_audit = audit_news_reactions(news_payload, candles, as_of=_latest_timestamp(candles))
    comparison = compare_no_news_vs_reaction_guard(baseline, reaction_audit)
    comparison["sources"] = {
        "frozen_candles": {
            "mode": candle_payload.get("mode") if isinstance(candle_payload, dict) else None,
            "trading_symbol": candle_payload.get("trading_symbol") if isinstance(candle_payload, dict) else None,
            "network_refetch": candle_payload.get("network_refetch") if isinstance(candle_payload, dict) else None,
            "point_in_time": candle_payload.get("point_in_time") if isinstance(candle_payload, dict) else None,
            "candles": len(candles),
        },
        "news": {
            "records": len(news_payload.get("records") or []),
            "metadata": news_payload.get("metadata"),
        },
        "reaction_audit": {
            "as_of": reaction_audit.get("as_of"),
            "events": reaction_audit.get("events"),
            "classified": reaction_audit.get("classified"),
            "coverage_counts": reaction_audit.get("coverage_counts"),
            "materiality_qualified_path_counts": reaction_audit.get("materiality_qualified_path_counts"),
        },
        "baseline_replay": {
            "mode": baseline.get("mode"),
            "reference_contract": baseline.get("reference_contract"),
            "scheduled_clicks": baseline.get("scheduled_clicks"),
            "evaluated_clicks": baseline.get("evaluated_clicks"),
            "click_coverage_exact": baseline.get("click_coverage_exact"),
            "complete_sessions": baseline.get("complete_sessions"),
            "excluded_partial_sessions": baseline.get("excluded_partial_sessions"),
        },
    }
    return comparison


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--news", required=True)
    parser.add_argument("--candles", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    news = json.loads(Path(args.news).read_text())
    candle_payload = json.loads(Path(args.candles).read_text())
    result = run(news, candle_payload)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
