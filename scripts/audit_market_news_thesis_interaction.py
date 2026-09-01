from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.commodity_time import parse_ist_timestamp
from app.copper_market_brain_abstention_audit import normalize_candle_rows
from app.current_mind_copper_replay import evaluate_current_mind_replay
from app.market_news_catalyst_control import catalyst_control_context
from app.market_news_thesis_interaction import assess_news_thesis_interaction
from app.playbook_pattern_confirmation_shadow import assess_declared_playbook_pattern
from scripts.audit_market_news_reactions import audit as audit_news_reactions


def _dict(value):
    return value if isinstance(value, dict) else {}


def _candles(payload):
    if isinstance(payload, dict):
        return list(payload.get("candles") or payload.get("records") or [])
    return list(payload or [])


def _latest_timestamp(candles: list) -> str:
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


def _market_structure(journal: dict) -> str:
    return str(
        _dict(_dict(journal.get("regime")).get("observations")).get("trend_structure")
        or "UNKNOWN"
    ).upper()


def _result(outcome: dict) -> str:
    return str(_dict(outcome).get("result") or "UNKNOWN")


def run(news_payload: dict, candle_payload) -> dict:
    candles = _candles(candle_payload)
    if not candles:
        raise RuntimeError("Frozen candle artifact is empty")
    normalized_rows = normalize_candle_rows(candles)
    if not normalized_rows:
        raise RuntimeError("Frozen candle artifact has no usable normalized OHLC rows")

    baseline = evaluate_current_mind_replay(normalized_rows)
    reaction_audit = audit_news_reactions(news_payload, candles, as_of=_latest_timestamp(candles))
    reaction_records = list(reaction_audit.get("records") or [])
    index_by_ts = {parse_ist_timestamp(row[0]): i for i, row in enumerate(normalized_rows)}

    rows = []
    control_counts = Counter()
    interaction_counts = Counter()
    playbook_counts = Counter()
    semantic_counts = Counter()
    pattern_status_counts = Counter()
    pattern_status_by_playbook = defaultdict(Counter)
    confirmed_pattern_by_playbook = Counter()
    opposing_playbook_counts = Counter()
    active_opposing_playbook_counts = Counter()
    active_opposing_pattern_counts = Counter()
    active_opposing_outcomes = Counter()
    interaction_outcomes = defaultdict(Counter)
    pattern_outcomes = defaultdict(Counter)

    for journal in baseline.get("decisions") or []:
        click = journal.get("click_timestamp") or journal.get("timestamp")
        if not click:
            raise RuntimeError("Baseline replay decision is missing click timestamp")
        click_ts = parse_ist_timestamp(click)
        index = index_by_ts.get(click_ts)
        if index is None:
            raise RuntimeError(f"Frozen replay click not found in normalized rows: {click}")

        control = catalyst_control_context(
            reaction_records,
            candles,
            click_timestamp=click,
            market_structure=_market_structure(journal),
            max_horizon_hours=8.0,
        )
        # Freeze both diagnostics before looking at the historical outcome.
        interaction = assess_news_thesis_interaction(journal, control)
        pattern = assess_declared_playbook_pattern(normalized_rows, index, journal)

        control_state = str(control.get("state") or "UNKNOWN")
        interaction_state = str(interaction.get("interaction") or "UNKNOWN")
        playbook = str(interaction.get("playbook") or "NO_DECLARED_PLAYBOOK")
        semantic = str(_dict(interaction.get("playbook_audit")).get("status") or "UNKNOWN")
        pattern_status = str(pattern.get("status") or "UNKNOWN")
        control_counts[control_state] += 1
        interaction_counts[interaction_state] += 1
        playbook_counts[playbook] += 1
        semantic_counts[semantic] += 1
        pattern_status_counts[pattern_status] += 1
        pattern_status_by_playbook[playbook][pattern_status] += 1
        if pattern.get("confirmed"):
            confirmed_pattern_by_playbook[playbook] += 1
        if interaction.get("alignment") == "OPPOSED":
            opposing_playbook_counts[playbook] += 1
            if control_state == "CONTROL_ACTIVE":
                active_opposing_playbook_counts[playbook] += 1
                active_opposing_pattern_counts[pattern_status] += 1

        # Historical outcome is attached only after both shadow states are frozen.
        outcome = _dict(journal.get("outcome"))
        result = _result(outcome)
        interaction_outcomes[interaction_state][result] += 1
        pattern_outcomes[pattern_status][result] += 1
        if interaction.get("alignment") == "OPPOSED" and control_state == "CONTROL_ACTIVE":
            active_opposing_outcomes[result] += 1

        regime = _dict(journal.get("regime"))
        decision = _dict(journal.get("decision"))
        rows.append({
            "click_timestamp": click,
            "baseline_action": decision.get("action"),
            "baseline_direction": interaction.get("baseline_direction"),
            "declared_playbook": interaction.get("playbook"),
            "playbook_family": interaction.get("playbook_family"),
            "regime_labels": regime.get("regime_labels") or [],
            "regime_observations": _dict(regime.get("observations")),
            "catalyst_control_shadow": control,
            "news_thesis_interaction_shadow": interaction,
            "playbook_pattern_confirmation_shadow": pattern,
            "baseline_outcome": outcome,
        })

    active_opposing_rows = [
        row for row in rows
        if _dict(row.get("news_thesis_interaction_shadow")).get("alignment") == "OPPOSED"
        and _dict(row.get("catalyst_control_shadow")).get("state") == "CONTROL_ACTIVE"
    ]

    return {
        "mode": "COPPER_NEWS_CATALYST_THESIS_INTERACTION_SHADOW_V2",
        "research_only": True,
        "descriptive_only": True,
        "production_rules_changed": False,
        "strategy_rules_changed": False,
        "validation_status": "DIAGNOSTIC_REPLAY_NOT_UNTOUCHED_HOLDOUT",
        "shadow_policy": {
            "outcome_blind": True,
            "shadow_only": True,
            "changes_decision": False,
            "headline_stance_used_for_direction": False,
            "catalyst_direction_source": "MATERIALITY_QUALIFIED_ASSIMILATION_PRICE_PATH",
            "playbook_source": "FROZEN_BASELINE_DECISION",
            "literal_playbook_pattern_confirmation_added": True,
            "pattern_confirmation_reads_only_bars_at_or_before_click": True,
            "pattern_lookback_bars": 6,
            "pattern_lookback_source": "FROZEN_CURRENT_MIND_INVALIDATION_WINDOW",
            "pattern_specific_confirmation_required_for_semantic_verification": True,
            "generic_regime_eligibility_is_not_pattern_confirmation": True,
            "generic_recent_high_low_trigger_is_not_pattern_confirmation": True,
            "thresholds_fitted_to_august_outcomes": False,
        },
        "outcome_integrity": {
            "outcomes_read_for_thesis_interaction_shadow": False,
            "outcomes_read_for_pattern_confirmation_shadow": False,
            "outcomes_read_after_both_shadows_freeze_for_descriptive_evaluation": True,
        },
        "clicks": len(rows),
        "catalyst_control_counts": dict(sorted(control_counts.items())),
        "thesis_interaction_counts": dict(sorted(interaction_counts.items())),
        "declared_playbook_counts": dict(sorted(playbook_counts.items())),
        "playbook_semantic_status_counts": dict(sorted(semantic_counts.items())),
        "pattern_confirmation_status_counts": dict(sorted(pattern_status_counts.items())),
        "pattern_confirmation_by_playbook": {
            key: dict(sorted(value.items())) for key, value in sorted(pattern_status_by_playbook.items())
        },
        "confirmed_pattern_by_playbook": dict(sorted(confirmed_pattern_by_playbook.items())),
        "opposing_catalyst_playbook_counts": dict(sorted(opposing_playbook_counts.items())),
        "active_opposing_catalyst_playbook_counts": dict(sorted(active_opposing_playbook_counts.items())),
        "active_opposing_pattern_counts": dict(sorted(active_opposing_pattern_counts.items())),
        "active_opposing_catalyst_outcomes_post_freeze": dict(sorted(active_opposing_outcomes.items())),
        "interaction_outcomes_post_freeze": {
            key: dict(sorted(value.items())) for key, value in sorted(interaction_outcomes.items())
        },
        "pattern_outcomes_post_freeze": {
            key: dict(sorted(value.items())) for key, value in sorted(pattern_outcomes.items())
        },
        "active_opposing_rows": active_opposing_rows,
        "rows": rows,
        "sources": {
            "frozen_candles": {
                "mode": candle_payload.get("mode") if isinstance(candle_payload, dict) else None,
                "trading_symbol": candle_payload.get("trading_symbol") if isinstance(candle_payload, dict) else None,
                "network_refetch": candle_payload.get("network_refetch") if isinstance(candle_payload, dict) else None,
                "point_in_time": candle_payload.get("point_in_time") if isinstance(candle_payload, dict) else None,
                "candles": len(candles),
                "normalized_rows": len(normalized_rows),
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
        },
        "guardrails": [
            "The no-news Current Mind decision is frozen before catalyst/thesis interaction and pattern confirmation are assessed.",
            "Both shadows read only frozen decision/regime fields and market information visible at or before the click.",
            "Neither shadow reads historical outcomes or P&L while assigning its state.",
            "Historical outcomes are attached only after both shadow states are frozen for descriptive review.",
            "TREND_PULLBACK requires a countertrend EMA20 touch within the already-frozen six-bar invalidation window and click-time reacceptance in the trend direction.",
            "BREAKOUT_RETEST requires an opening-range breakout close, a subsequent boundary retest, and retained breakout side using only bars through the click.",
            "RANGE_EDGE_REVERSAL requires a same-session prior edge sweep and close back inside with a reversal candle body while structure is RANGE.",
            "FAILED_BREAKOUT requires a recent close beyond the opening-range boundary followed by click-time reclaim/rejection through that boundary.",
            "No missing pullback, retest, range-edge rejection, or failed-break pattern is reconstructed from later candles.",
            "The shadows cannot create, reverse, suppress, delay, upgrade, or otherwise change a trade.",
            "This August dataset has already been inspected during AlphaPilot research, so results are diagnostic rather than untouched out-of-sample validation.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--news", required=True)
    parser.add_argument("--candles", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    news_payload = json.loads(Path(args.news).read_text())
    candle_payload = json.loads(Path(args.candles).read_text())
    report = run(news_payload, candle_payload)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
