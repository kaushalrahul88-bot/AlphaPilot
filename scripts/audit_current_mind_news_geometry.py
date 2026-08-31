from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.trader_evidence_synthesis import synthesize_evidence

_DIRECTIONAL = {"BULLISH", "BEARISH"}
_ACTION_DIRECTION = {"BUY_CE": "BULLISH", "BUY_PE": "BEARISH"}


def _stance(row: dict) -> str:
    return str(row.get("stance") or "UNKNOWN").upper()


def _lane(row: dict) -> str:
    return str(row.get("lane") or "OTHER").upper()


def _flatten_evidence(decision_row: dict) -> list[dict]:
    synthesis = decision_row.get("evidence") or {}
    lanes = synthesis.get("lanes") or {}
    return [dict(row) for rows in lanes.values() for row in (rows or []) if isinstance(row, dict)]


def _direction_from_synthesis(synthesis: dict) -> str | None:
    bullish = len(synthesis.get("independent_bullish_lanes") or [])
    bearish = len(synthesis.get("independent_bearish_lanes") or [])
    if max(bullish, bearish) < 2 or bullish == bearish:
        return None
    return "BULLISH" if bullish > bearish else "BEARISH"


def _legacy_direction(items: list[dict]) -> str | None:
    lanes: dict[str, set[str]] = defaultdict(set)
    for item in items:
        stance = _stance(item)
        if stance in _DIRECTIONAL:
            lanes[_lane(item)].add(stance)
    bullish = sum(stances == {"BULLISH"} for stances in lanes.values())
    bearish = sum(stances == {"BEARISH"} for stances in lanes.values())
    if max(bullish, bearish) < 2 or bullish == bearish:
        return None
    return "BULLISH" if bullish > bearish else "BEARISH"


def _news_observation(synthesis: dict) -> dict:
    rows = (synthesis.get("lanes") or {}).get("NEWS") or []
    visible = sum(int((row.get("detail") or {}).get("visible") or 0) for row in rows)
    states = sorted({str(row.get("price_interaction_state")) for row in rows if row.get("price_interaction_state")})
    roles = sorted({str(row.get("directional_role")) for row in rows if row.get("directional_role")})
    return {"visible": visible, "states": states, "roles": roles}


def _validate_contract(baseline: dict, news: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    fields = (
        "clicks_per_complete_session",
        "reference_contract",
        "scheduled_clicks",
        "evaluated_clicks",
        "complete_session_dates",
    )
    mismatches = {
        field: {"baseline": baseline.get(field), "news": news.get(field)}
        for field in fields
        if baseline.get(field) != news.get(field)
    }
    if mismatches:
        raise ValueError(f"Replay contract mismatch: {json.dumps(mismatches, sort_keys=True)}")

    baseline_rows = baseline.get("decisions") or []
    news_rows = news.get("decisions") or []
    baseline_by_ts = {row.get("click_timestamp"): row for row in baseline_rows}
    news_by_ts = {row.get("click_timestamp"): row for row in news_rows}
    if None in baseline_by_ts or None in news_by_ts:
        raise ValueError("Every replay decision must have click_timestamp")
    if len(baseline_by_ts) != len(baseline_rows) or len(news_by_ts) != len(news_rows):
        raise ValueError("Duplicate click_timestamp detected")
    if set(baseline_by_ts) != set(news_by_ts):
        raise ValueError("Click timestamp mismatch")
    return baseline_by_ts, news_by_ts


def audit_geometry(baseline: dict, news: dict) -> dict:
    """Re-synthesize stored point-in-time evidence without reading outcomes."""
    baseline_by_ts, news_by_ts = _validate_contract(baseline, news)
    rows: list[dict] = []
    interaction_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    visible_clicks = geometry_changes = old_action_changes = 0

    for timestamp in sorted(baseline_by_ts):
        baseline_row = baseline_by_ts[timestamp]
        news_row = news_by_ts[timestamp]
        baseline_action = str((baseline_row.get("decision") or {}).get("action") or "UNKNOWN")
        old_news_action = str((news_row.get("decision") or {}).get("action") or "UNKNOWN")
        items = _flatten_evidence(news_row)
        current = synthesize_evidence(items)
        current_direction = _direction_from_synthesis(current)
        legacy_direction = _legacy_direction(items)
        observation = _news_observation(current)

        visible_clicks += observation["visible"] > 0
        for state in observation["states"]:
            interaction_counts[state] += 1
        geometry_changes += current_direction != legacy_direction
        action_changed = baseline_action != old_news_action
        old_action_changes += action_changed

        baseline_direction = _ACTION_DIRECTION.get(baseline_action)
        old_news_direction = _ACTION_DIRECTION.get(old_news_action)
        if not action_changed:
            classification = "ACTION_UNCHANGED"
        elif baseline_direction and current_direction == baseline_direction:
            classification = "CURRENT_GEOMETRY_ALIGNS_BASELINE_TRADE"
        elif not baseline_direction and old_news_direction and current_direction is None:
            classification = "CURRENT_GEOMETRY_REMOVES_NEWS_CREATED_TRADE"
        elif current_direction == old_news_direction:
            classification = "CURRENT_GEOMETRY_STILL_ALIGNS_OLD_NEWS_ACTION"
        else:
            classification = "REQUIRES_FULL_REPLAY_FOR_ACTION_RESOLUTION"
        classification_counts[classification] += 1

        if observation["visible"] > 0 or action_changed or current_direction != legacy_direction:
            rows.append({
                "click_timestamp": timestamp,
                "news_visible": observation["visible"],
                "news_price_interaction_states": observation["states"],
                "news_directional_roles": observation["roles"],
                "baseline_action": baseline_action,
                "old_news_action": old_news_action,
                "legacy_geometry_direction": legacy_direction,
                "current_geometry_direction": current_direction,
                "current_independent_bullish_lanes": current.get("independent_bullish_lanes") or [],
                "current_independent_bearish_lanes": current.get("independent_bearish_lanes") or [],
                "classification": classification,
            })

    return {
        "mode": "CURRENT_MIND_NEWS_GEOMETRY_COUNTERFACTUAL_AUDIT_V1",
        "research_only": True,
        "outcome_blind": True,
        "network_refetch": False,
        "replay_contract": {
            field: baseline.get(field)
            for field in (
                "clicks_per_complete_session",
                "reference_contract",
                "scheduled_clicks",
                "evaluated_clicks",
                "complete_session_dates",
            )
        },
        "summary": {
            "clicks_compared": len(baseline_by_ts),
            "news_visible_clicks": visible_clicks,
            "old_action_changes": old_action_changes,
            "current_vs_legacy_geometry_changes": geometry_changes,
            "news_price_interaction_counts": dict(sorted(interaction_counts.items())),
            "classification_counts": dict(sorted(classification_counts.items())),
        },
        "diagnostic_rows": rows,
        "guardrails": [
            "Historical outcomes are not read by this audit.",
            "No strategy, persistence, admission, risk, target, stop or click threshold is changed.",
            "Current evidence synthesis is applied only to evidence already stored at each historical click.",
            "This diagnostic does not replace the full deterministic replay for final result publication.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--news", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit_geometry(json.loads(args.baseline.read_text()), json.loads(args.news.read_text()))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
