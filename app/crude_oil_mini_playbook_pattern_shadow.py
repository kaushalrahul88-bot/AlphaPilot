from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_market_perception import clean_ohlcv, latest_visible_index
from .playbook_pattern_confirmation_shadow import (
    ACTION_DIRECTION,
    assess_declared_playbook_pattern,
    pattern_gate_action,
)


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _signed_forward_60(row: dict, action_key: str) -> float | None:
    action = str(row.get(action_key) or "")
    if action not in ACTION_DIRECTION:
        return None
    raw = _dict(row.get("future_returns_pct")).get("60")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if action == "BUY_CE" else -value


def _performance(rows: list[dict], action_key: str) -> dict:
    actionable = [row for row in rows if str(row.get(action_key) or "") in ACTION_DIRECTION]
    resolved = [
        row for row in actionable
        if _dict(row.get("baseline_outcome")).get("result") in {"TARGET", "STOP"}
    ]
    realized = [float(_dict(row.get("baseline_outcome")).get("realized_r") or 0.0) for row in resolved]
    signed = [
        value for row in actionable
        if (value := _signed_forward_60(row, action_key)) is not None
    ]
    return {
        "actions": dict(sorted(Counter(str(row.get(action_key) or "UNKNOWN") for row in rows).items())),
        "trades": len(actionable),
        "resolved_trades": len(resolved),
        "targets": sum(_dict(row.get("baseline_outcome")).get("result") == "TARGET" for row in actionable),
        "stops": sum(_dict(row.get("baseline_outcome")).get("result") == "STOP" for row in actionable),
        "no_entry": sum(_dict(row.get("baseline_outcome")).get("result") == "NO_ENTRY" for row in actionable),
        "session_end": sum(_dict(row.get("baseline_outcome")).get("result") == "SESSION_END" for row in actionable),
        "resolved_r_sum": round(sum(realized), 4),
        "expectancy_r_resolved": round(mean(realized), 4) if realized else None,
        "direction_60m": {
            "observations": len(signed),
            "alignment_pct": round(sum(value > 0 for value in signed) / len(signed) * 100.0, 2) if signed else None,
            "avg_signed_return_pct": round(mean(signed), 4) if signed else None,
        },
    }


def evaluate_crude_playbook_pattern_shadow(candles, baseline_report: dict) -> dict:
    """Apply Copper's frozen literal-pattern semantics to Crude as a shadow only.

    The shared pattern definitions and candidate gate predate this Crude Oil Mini
    evaluation and contain no Crude outcome-fitted threshold. The gate is frozen
    before each historical outcome is read. It can only delay an existing CE/PE
    action to WAIT; it cannot create a setup, reverse direction, or upgrade a WAIT.
    """
    rows = clean_ohlcv(candles)
    source_decisions = list(baseline_report.get("decisions") or [])
    evaluated = []
    pattern_status = Counter()
    declared = Counter()
    confirmed = Counter()
    suppressed_outcomes = Counter()
    suppressed_playbooks = Counter()
    reason_counts = defaultdict(Counter)

    for source in source_decisions:
        click = source.get("click_timestamp")
        baseline_action = str(source.get("action") or "WAIT")
        decision = _dict(source.get("decision"))
        playbook = str(decision.get("playbook") or "NO_DECLARED_PLAYBOOK")
        index = latest_visible_index(rows, click) if click else None

        if index is None:
            pattern = {
                "mode": "PLAYBOOK_PATTERN_CONFIRMATION_SHADOW_V1",
                "status": "CLICK_ROW_UNAVAILABLE",
                "confirmed": False,
                "declared_playbook": decision.get("playbook"),
                "baseline_action": baseline_action,
            }
        else:
            visible_start = parse_ist_timestamp(rows[index][0]).isoformat()
            expected_start = parse_ist_timestamp(source.get("latest_visible_bar_start")).isoformat()
            if visible_start != expected_start:
                raise RuntimeError(
                    f"Crude pattern shadow visibility mismatch at {click}: "
                    f"shadow={visible_start} replay={expected_start}"
                )
            journal = {
                "decision": {**decision, "action": baseline_action},
                "regime": _dict(source.get("regime")),
            }
            pattern = assess_declared_playbook_pattern(rows, index, journal)

        # Freeze the candidate action before touching the historical outcome below.
        gate = pattern_gate_action(baseline_action, pattern)
        candidate_action = gate["action"]

        outcome = _dict(source.get("outcome"))
        if baseline_action in ACTION_DIRECTION:
            declared[playbook] += 1
            pattern_status[str(pattern.get("status") or "UNKNOWN")] += 1
            reason = str(_dict(pattern.get("detail")).get("reason") or pattern.get("status") or "UNKNOWN")
            reason_counts[playbook][reason] += 1
            if bool(pattern.get("confirmed")):
                confirmed[playbook] += 1
        if gate.get("changed"):
            suppressed_outcomes[str(outcome.get("result") or "UNKNOWN")] += 1
            suppressed_playbooks[playbook] += 1

        evaluated.append({
            "session": source.get("session"),
            "click_timestamp": click,
            "latest_visible_bar_start": source.get("latest_visible_bar_start"),
            "baseline_action": baseline_action,
            "pattern_gate_action": candidate_action,
            "changed": bool(gate.get("changed")),
            "change_reason": gate.get("reason"),
            "declared_playbook": decision.get("playbook"),
            "pattern": pattern,
            "future_returns_pct": source.get("future_returns_pct"),
            "baseline_outcome": outcome,
            "decision_fingerprint": source.get("decision_fingerprint"),
        })

    baseline = _performance(evaluated, "baseline_action")
    gated = _performance(evaluated, "pattern_gate_action")
    base_exp = baseline.get("expectancy_r_resolved")
    gate_exp = gated.get("expectancy_r_resolved")
    per_playbook = {}
    for playbook in sorted(declared):
        per_playbook[playbook] = {
            "declared": declared[playbook],
            "confirmed": confirmed[playbook],
            "confirmation_rate_pct": round(confirmed[playbook] / declared[playbook] * 100.0, 2) if declared[playbook] else None,
            "pattern_reason_counts": dict(sorted(reason_counts[playbook].items())),
        }

    return {
        "mode": "CRUDE_OIL_MINI_PLAYBOOK_PATTERN_GATE_SHADOW_V1",
        "product": "CRUDE_OIL_MINI",
        "reference_contract": baseline_report.get("reference_contract"),
        "research_only": True,
        "descriptive_only": True,
        "shadow_only": True,
        "production_rules_changed": False,
        "strategy_rules_changed": False,
        "news_used": False,
        "option_market_data_used": False,
        "copper_market_data_used": False,
        "shared_pattern_architecture_used": True,
        "pattern_definition_source": "PLAYBOOK_PATTERN_CONFIRMATION_SHADOW_V1",
        "pattern_definition_predates_crude_outcome_review": True,
        "candidate_gate_predates_crude_outcome_review": True,
        "outcomes_used_to_select_pattern_thresholds": False,
        "clicks": len(evaluated),
        "baseline": baseline,
        "pattern_gate": gated,
        "delta": {
            "trades": gated["trades"] - baseline["trades"],
            "resolved_trades": gated["resolved_trades"] - baseline["resolved_trades"],
            "targets": gated["targets"] - baseline["targets"],
            "stops": gated["stops"] - baseline["stops"],
            "resolved_r_sum": round(gated["resolved_r_sum"] - baseline["resolved_r_sum"], 4),
            "expectancy_r_resolved": (
                round(gate_exp - base_exp, 4)
                if gate_exp is not None and base_exp is not None else None
            ),
        },
        "pattern_status_counts": dict(sorted(pattern_status.items())),
        "per_playbook": per_playbook,
        "changed_clicks": sum(bool(row["changed"]) for row in evaluated),
        "suppressed_baseline_outcomes": dict(sorted(suppressed_outcomes.items())),
        "suppressed_playbooks": dict(sorted(suppressed_playbooks.items())),
        "validation_status": "SHADOW_DIAGNOSTIC_NOT_PRODUCTION_GATE",
        "rows": evaluated,
        "guardrails": [
            "The frozen Crude baseline action, click and completed-bar snapshot are reused exactly.",
            "Literal pattern confirmation comes from the shared Copper-tested Current Mind architecture, not Copper market data.",
            "The shared pattern definitions and gate existed before this Crude outcome review and are not refit to Crude results.",
            "The shadow gate can only delay an existing BUY_CE/BUY_PE action to WAIT.",
            "The shadow gate cannot create a trade, reverse direction, or upgrade WAIT/NO_TRADE.",
            "Historical outcome is read only after the shadow action is frozen for the click.",
            "No production decision path changes from this diagnostic; promotion requires a separate explicit decision.",
        ],
    }
