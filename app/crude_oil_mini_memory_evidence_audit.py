from __future__ import annotations

from collections import Counter
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_data_probe import _complete_sessions
from .crude_oil_mini_experience_memory import build_experiences, query_memory
from .crude_oil_mini_market_perception import bar_visible_at, precompute_perception


def _pct(numerator: int, denominator: int) -> float | None:
    return round(float(numerator) / float(denominator) * 100.0, 2) if denominator else None


def _side_summary(observations: list[dict], direction: str) -> dict:
    rows = [row for row in observations if row["selected_direction"] == direction]
    resolved = [row for row in rows if row["actual_outcome"] != "SESSION_END_NO_EVENT"]
    wins = [row for row in resolved if row["actual_outcome"] == "TARGET_FIRST"]
    return {
        "selected": len(rows),
        "resolved": len(resolved),
        "target_first": len(wins),
        "target_first_pct_resolved": _pct(len(wins), len(resolved)),
        "avg_direction_difference_z": round(mean(abs(float(row["direction_difference_z"])) for row in rows), 3) if rows else None,
    }


def evaluate_memory_evidence(candles, sample_every_bars: int = 3) -> dict:
    """Audit whether Crude's causal Experience Memory carries directional evidence.

    The selection rule is exactly the rule already frozen inside query_memory:
    adaptive analogue breadth from prior memory size and a two-sided |z|>=1.96
    directional stance. This audit does not search a new cutoff against outcomes.
    """
    rows, features = precompute_perception(candles)
    sessions = _complete_sessions(rows)
    complete_days = {
        row["date"] for row in sessions
        if row.get("complete_for_20_click_research")
    }
    experiences = build_experiences(rows, features, complete_days, sample_every_bars=sample_every_bars)

    experience_lookup = {
        (str(row["timestamp"]), str(row["direction"])): row
        for row in experiences
    }
    index_by_timestamp = {
        str(snapshot["timestamp"]): index
        for index, snapshot in enumerate(features)
    }
    query_timestamps = sorted({
        str(row["timestamp"])
        for row in experiences
        if row.get("direction") == "BULLISH"
    }, key=parse_ist_timestamp)

    observations = []
    ready_queries = 0
    insufficient_queries = 0
    unknown_stance_queries = 0
    for timestamp in query_timestamps:
        index = index_by_timestamp.get(timestamp)
        if index is None:
            continue
        click_at = bar_visible_at(rows[index]).isoformat()
        memory = query_memory(experiences, features[index], click_at)
        if memory.get("status") != "READY":
            insufficient_queries += 1
            continue
        ready_queries += 1
        stance = str(memory.get("stance") or "UNKNOWN")
        if stance not in {"BULLISH", "BEARISH"}:
            unknown_stance_queries += 1
            continue
        actual = experience_lookup.get((timestamp, stance))
        if actual is None:
            continue
        observations.append({
            "timestamp": timestamp,
            "click_at": click_at,
            "selected_direction": stance,
            "actual_outcome": actual.get("outcome"),
            "actual_resolved_at": actual.get("resolved_at"),
            "prior_resolved_experiences": memory.get("prior_resolved_experiences"),
            "analogues_used": memory.get("analogues_used"),
            "nearest_distance": memory.get("nearest_distance"),
            "direction_difference_z": memory.get("direction_difference_z"),
            "bullish": (memory.get("by_direction") or {}).get("BULLISH"),
            "bearish": (memory.get("by_direction") or {}).get("BEARISH"),
        })

    resolved = [row for row in observations if row["actual_outcome"] != "SESSION_END_NO_EVENT"]
    wins = [row for row in resolved if row["actual_outcome"] == "TARGET_FIRST"]
    return {
        "mode": "CRUDE_OIL_MINI_MEMORY_EVIDENCE_AUDIT_V1",
        "product": "CRUDE_OIL_MINI",
        "research_only": True,
        "descriptive_only": True,
        "production_rules_changed": False,
        "news_used": False,
        "option_market_data_used": False,
        "current_contract_only": True,
        "complete_sessions": len(complete_days),
        "experiences": len(experiences),
        "query_timestamps": len(query_timestamps),
        "ready_queries": ready_queries,
        "insufficient_memory_queries": insufficient_queries,
        "ready_but_unknown_stance": unknown_stance_queries,
        "selected_setups": len(observations),
        "resolved_setups": len(resolved),
        "target_first": len(wins),
        "target_first_pct_resolved": _pct(len(wins), len(resolved)),
        "selected_direction_counts": dict(Counter(row["selected_direction"] for row in observations)),
        "BULLISH": _side_summary(observations, "BULLISH"),
        "BEARISH": _side_summary(observations, "BEARISH"),
        "selection_rule": "Use the existing CRUDEOILM memory stance only: adaptive causal analogues and two-sided |z|>=1.96; otherwise abstain.",
        "observations": observations,
        "guardrails": [
            "Every memory query exposes only experiences whose resolved_at is strictly earlier than the simulated click.",
            "The selected current outcome is attached only after the memory stance has been determined.",
            "No Copper experience, Copper fitted threshold, news, option premium or synthetic option return is used.",
            "No threshold is searched against this audit's outcomes.",
            "SESSION_END_NO_EVENT remains unresolved evidence rather than being silently labelled a loss or win.",
        ],
    }
