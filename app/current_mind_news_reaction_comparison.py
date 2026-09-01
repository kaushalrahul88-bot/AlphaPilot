from __future__ import annotations

from collections import Counter
from datetime import timedelta
from statistics import mean
from typing import Any

from .commodity_time import parse_ist_timestamp
from .market_news_catalyst_control import catalyst_control_context

REACTION_CONTEXT_HOURS = 8.0
ACTION_DIRECTION = {"BUY_CE": "BULLISH", "BUY_PE": "BEARISH"}
QUALIFIED_DIRECTION = {"UP": "BULLISH", "DOWN": "BEARISH"}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _reaction_context_for_click(reaction_records: list[dict], click_timestamp: str) -> dict:
    """Expose only completed, materiality-qualified reaction evidence available by the click.

    A reaction is eligible only after its +60 minute assimilation observation exists and only
    during the already-frozen eight-hour relevance horizon from the reaction anchor. The latest
    qualified assimilation direction is price-derived context; headline stance never supplies the
    direction here. Conflicting active reactions fail closed to a non-directional context state.
    """
    click = parse_ist_timestamp(click_timestamp)
    eligible = []
    for record in reaction_records or []:
        if record.get("coverage_status") != "CLASSIFIABLE":
            continue
        event = _dict(record.get("event"))
        if str(event.get("disposition") or "").upper() == "BLOCK":
            continue
        window = _dict(record.get("window"))
        qualified = _dict(record.get("materiality_qualified_path"))
        if qualified.get("observation_status") != "OBSERVED":
            continue
        anchor_raw = window.get("reaction_anchor_timestamp")
        assimilation_raw = _dict(window.get("assimilation")).get("timestamp")
        if not anchor_raw or not assimilation_raw:
            continue
        try:
            anchor = parse_ist_timestamp(anchor_raw)
            assimilation = parse_ist_timestamp(assimilation_raw)
        except (TypeError, ValueError):
            continue
        if click < assimilation or click > anchor + timedelta(hours=REACTION_CONTEXT_HOURS):
            continue
        raw_direction = _dict(qualified.get("qualified_directions")).get("assimilation")
        direction = QUALIFIED_DIRECTION.get(str(raw_direction or "").upper())
        if direction is None:
            continue
        eligible.append({
            "event_timestamp": window.get("event_timestamp") or event.get("available_at"),
            "reaction_anchor_timestamp": anchor_raw,
            "assimilation_observed_at": assimilation_raw,
            "direction": direction,
            "qualified_path_state": qualified.get("qualified_path_state"),
            "headline": event.get("headline"),
            "source": event.get("source"),
            "headline_stance": event.get("stance"),
            "disposition": event.get("disposition"),
            "materiality": event.get("materiality"),
        })

    directions = {row["direction"] for row in eligible}
    if len(directions) == 1:
        state = "COHERENT_MATERIAL_REACTION"
        direction = next(iter(directions))
    elif len(directions) > 1:
        state = "CONFLICTING_MATERIAL_REACTIONS"
        direction = "UNKNOWN"
    else:
        state = "NO_MATERIAL_REACTION_CONTEXT"
        direction = "UNKNOWN"

    return {
        "state": state,
        "direction": direction,
        "active_reactions": eligible,
        "active_reaction_count": len(eligible),
        "context_hours": REACTION_CONTEXT_HOURS,
        "direction_source": "MATERIALITY_QUALIFIED_ASSIMILATION_PRICE_PATH",
    }


def reaction_guard_action(baseline_action: str, reaction_context: dict) -> dict:
    """Apply the preregistered conservative News Brain rule without reading outcomes.

    News/reaction context may delay an existing option-buy thesis when a completed material market
    reaction points the other way. It cannot create a trade, reverse CE/PE direction, or upgrade a
    weak/no-trade baseline into an actionable setup.
    """
    baseline_action = str(baseline_action or "NO_TRADE")
    baseline_direction = ACTION_DIRECTION.get(baseline_action)
    reaction_direction = str(_dict(reaction_context).get("direction") or "UNKNOWN")
    if (
        baseline_direction in {"BULLISH", "BEARISH"}
        and reaction_direction in {"BULLISH", "BEARISH"}
        and reaction_direction != baseline_direction
    ):
        return {
            "action": "WAIT",
            "changed": True,
            "reason": "MATERIAL_NEWS_REACTION_CONFLICT",
            "baseline_direction": baseline_direction,
            "reaction_direction": reaction_direction,
        }
    return {
        "action": baseline_action,
        "changed": False,
        "reason": "NO_REACTION_GUARD_CHANGE",
        "baseline_direction": baseline_direction,
        "reaction_direction": reaction_direction,
    }


def _performance(rows: list[dict], action_key: str) -> dict:
    actionable = [row for row in rows if row.get(action_key) in ACTION_DIRECTION]
    resolved = [
        row for row in actionable
        if _dict(row.get("baseline_outcome")).get("result") in {"TARGET", "STOP"}
    ]
    realized = [float(_dict(row.get("baseline_outcome")).get("realized_r") or 0.0) for row in resolved]
    actions = Counter(str(row.get(action_key) or "UNKNOWN") for row in rows)
    return {
        "actions": dict(sorted(actions.items())),
        "trades": len(actionable),
        "resolved_trades": len(resolved),
        "targets": sum(_dict(row.get("baseline_outcome")).get("result") == "TARGET" for row in actionable),
        "stops": sum(_dict(row.get("baseline_outcome")).get("result") == "STOP" for row in actionable),
        "no_entry": sum(_dict(row.get("baseline_outcome")).get("result") == "NO_ENTRY" for row in actionable),
        "session_end": sum(_dict(row.get("baseline_outcome")).get("result") == "SESSION_END" for row in actionable),
        "resolved_r_sum": round(sum(realized), 4),
        "expectancy_r_resolved": round(mean(realized), 4) if realized else None,
    }


def _market_structure(journal: dict) -> str:
    return str(
        _dict(_dict(journal.get("regime")).get("observations")).get("trend_structure")
        or "UNKNOWN"
    ).upper()


def _unavailable_control_shadow() -> dict:
    return {
        "mode": "MARKET_NEWS_CATALYST_CONTROL_CONTEXT_SHADOW_V1",
        "outcome_blind": True,
        "shadow_only": True,
        "state": "SHADOW_INPUT_UNAVAILABLE",
        "direction": "UNKNOWN",
        "controls_direction": False,
        "primary": None,
        "controls": [],
        "reason": "FROZEN_CANDLES_NOT_SUPPLIED_TO_COMPARISON",
    }


def compare_no_news_vs_reaction_guard(
    baseline_report: dict,
    reaction_audit: dict,
    *,
    candles: list | None = None,
) -> dict:
    """Compare frozen Current Mind with and without a conservative reaction-aware guard.

    The V1 decision overlay remains outcome-blind and unchanged. In parallel, when frozen candles
    are supplied, a new shadow-only catalyst-control model asks whether each completed reaction is
    still retained, assimilating, contested, or overridden at the click. That shadow is diagnostic
    only and is never consulted by the V1 overlay action. Historical outcomes are attached only
    after both counterfactual contexts are frozen.
    """
    decisions = list(baseline_report.get("decisions") or [])
    reaction_records = list(reaction_audit.get("records") or [])
    rows = []
    context_counts = Counter()
    control_state_counts = Counter()
    control_alignment_counts = Counter()
    for journal in decisions:
        click = journal.get("click_timestamp") or journal.get("timestamp")
        if not click:
            raise ValueError("Baseline decision missing click timestamp")
        baseline_action = str(_dict(journal.get("decision")).get("action") or "NO_TRADE")
        baseline_direction = ACTION_DIRECTION.get(baseline_action)

        context = _reaction_context_for_click(reaction_records, click)
        overlay = reaction_guard_action(baseline_action, context)

        if candles is None:
            control_shadow = _unavailable_control_shadow()
        else:
            control_shadow = catalyst_control_context(
                reaction_records,
                candles,
                click_timestamp=click,
                market_structure=_market_structure(journal),
                max_horizon_hours=REACTION_CONTEXT_HOURS,
            )
        control_state = str(control_shadow.get("state") or "UNKNOWN")
        control_state_counts[control_state] += 1
        control_direction = str(control_shadow.get("direction") or "UNKNOWN")
        if control_direction in {"BULLISH", "BEARISH"}:
            if baseline_direction == control_direction:
                alignment = "SUPPORTS_BASELINE"
            elif baseline_direction in {"BULLISH", "BEARISH"}:
                alignment = "OPPOSES_BASELINE"
            else:
                alignment = "BASELINE_ABSTAINS"
            control_alignment_counts[f"{control_state}__{alignment}"] += 1

        # Outcome is deliberately read only after both the V1 overlay and shadow context are frozen.
        outcome = _dict(journal.get("outcome"))
        if context["direction"] in {"BULLISH", "BEARISH"}:
            if baseline_direction == context["direction"]:
                context_counts["SUPPORTS_BASELINE_DIRECTION"] += 1
            elif baseline_direction in {"BULLISH", "BEARISH"}:
                context_counts["OPPOSES_BASELINE_DIRECTION"] += 1
            else:
                context_counts["REACTION_PRESENT_BASELINE_ABSTAINS"] += 1
        elif context["state"] == "CONFLICTING_MATERIAL_REACTIONS":
            context_counts["CONFLICTING_REACTIONS"] += 1
        else:
            context_counts["NO_MATERIAL_REACTION"] += 1
        rows.append({
            "click_timestamp": click,
            "baseline_action": baseline_action,
            "reaction_guard_action": overlay["action"],
            "changed": overlay["changed"],
            "change_reason": overlay["reason"],
            "baseline_direction": overlay["baseline_direction"],
            "reaction_direction": overlay["reaction_direction"],
            "reaction_context": context,
            "catalyst_control_shadow": control_shadow,
            "baseline_outcome": outcome,
        })

    baseline_perf = _performance(rows, "baseline_action")
    guard_perf = _performance(rows, "reaction_guard_action")
    changed = [row for row in rows if row["changed"]]
    changed_results = Counter(_dict(row.get("baseline_outcome")).get("result") or "UNKNOWN" for row in changed)
    baseline_exp = baseline_perf.get("expectancy_r_resolved")
    guard_exp = guard_perf.get("expectancy_r_resolved")
    return {
        "mode": "COPPER_CURRENT_MIND_NEWS_REACTION_VS_NO_NEWS_V1",
        "research_only": True,
        "descriptive_only": True,
        "production_rules_changed": False,
        "strategy_rules_changed": False,
        "validation_status": "DIAGNOSTIC_REPLAY_NOT_UNTOUCHED_HOLDOUT",
        "decision_policy": {
            "name": "MATERIAL_REACTION_CONFLICT_GUARD_V1",
            "context_hours": REACTION_CONTEXT_HOURS,
            "requires_completed_assimilation": True,
            "requires_materiality_qualified_assimilation_direction": True,
            "can_create_trade": False,
            "can_reverse_direction": False,
            "can_upgrade_no_trade": False,
            "conflicting_reactions_fail_closed_to_no_change": True,
            "opposing_reaction_effect": "DELAY_EXISTING_ACTION_TO_WAIT",
        },
        "catalyst_control_shadow_policy": {
            "name": "MARKET_NEWS_CATALYST_CONTROL_SHADOW_V1",
            "shadow_only": True,
            "changes_v1_guard_action": False,
            "outcomes_used_for_state": False,
            "headline_stance_used_for_direction": False,
            "direction_source": "MATERIALITY_QUALIFIED_ASSIMILATION_PRICE_PATH",
            "origin_reference": "PRE_EVENT_CLOSE",
            "accepted_reaction_reference": "PLUS_60M_ASSIMILATION_CLOSE",
            "active_rule": "RETAIN_ACCEPTED_REACTION_LEVEL_WITHOUT_OPPOSITE_CLICK_TIME_STRUCTURE",
            "assimilating_rule": "RETAIN_REACTION_SIDE_OF_ORIGIN_AFTER_LOSS_OF_ACCEPTED_LEVEL",
            "contested_rule": "OPPOSITE_CLICK_TIME_STRUCTURE_BEFORE_ORIGIN_BREAK",
            "overridden_rule": "ANY_POST_ASSIMILATION_CLOSE_THROUGH_ORIGIN_OPPOSITE_REACTION_DIRECTION",
            "overridden_is_latched_within_inspected_path": True,
            "maximum_observation_horizon_hours": REACTION_CONTEXT_HOURS,
            "thresholds_fitted_to_august_outcomes": False,
        },
        "outcome_integrity": {
            "outcomes_read_for_overlay_decision": False,
            "outcomes_read_for_catalyst_control_shadow": False,
            "outcomes_read_after_overlay_freeze_for_evaluation": True,
            "headline_stance_used_as_reaction_direction": False,
            "reaction_direction_source": "MATERIALITY_QUALIFIED_ASSIMILATION_PRICE_PATH",
        },
        "clicks": len(rows),
        "baseline": baseline_perf,
        "reaction_guard": guard_perf,
        "delta": {
            "trades": guard_perf["trades"] - baseline_perf["trades"],
            "resolved_trades": guard_perf["resolved_trades"] - baseline_perf["resolved_trades"],
            "targets": guard_perf["targets"] - baseline_perf["targets"],
            "stops": guard_perf["stops"] - baseline_perf["stops"],
            "resolved_r_sum": round(guard_perf["resolved_r_sum"] - baseline_perf["resolved_r_sum"], 4),
            "expectancy_r_resolved": (
                round(guard_exp - baseline_exp, 4)
                if guard_exp is not None and baseline_exp is not None else None
            ),
        },
        "reaction_context_counts": dict(sorted(context_counts.items())),
        "catalyst_control_shadow_counts": dict(sorted(control_state_counts.items())),
        "catalyst_control_alignment_counts": dict(sorted(control_alignment_counts.items())),
        "changed_clicks": len(changed),
        "suppressed_baseline_outcomes": dict(sorted(changed_results.items())),
        "prevented_stops": int(changed_results.get("STOP") or 0),
        "blocked_targets": int(changed_results.get("TARGET") or 0),
        "suppressed_no_entry": int(changed_results.get("NO_ENTRY") or 0),
        "suppressed_session_end": int(changed_results.get("SESSION_END") or 0),
        "changed_rows": changed,
        "rows": rows,
        "guardrails": [
            "The no-news Current Mind decision is frozen before reaction context is applied.",
            "Reaction context cannot manufacture BUY_CE or BUY_PE and cannot reverse an existing option side.",
            "Only a completed +60m materiality-qualified assimilation direction may delay an opposing baseline action in the unchanged V1 guard.",
            "The catalyst-control model is shadow-only and cannot alter any V1 guard action or production decision.",
            "Catalyst control uses only candles at or before the click plus click-time market structure; future candles and trade outcomes are invisible.",
            "Headline sentiment does not provide either reaction or catalyst-control direction; observed material price assimilation does.",
            "A post-assimilation close through the pre-event origin latches the inspected catalyst as overridden even if price later recovers.",
            "No elapsed-time threshold was tuned against August outcomes; the existing eight-hour horizon is retained only as the frozen maximum context envelope.",
            "Historical trade outcomes are evaluated only after the reaction-guard action and catalyst-control shadow are frozen.",
            "This August dataset has already been inspected during AlphaPilot research, so the result is diagnostic rather than untouched out-of-sample validation.",
            "No production Market Brain, Option Brain, risk rule, or live execution behavior changes from this comparison.",
        ],
    }
