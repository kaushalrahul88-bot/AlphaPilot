from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean

from app.playbook_pattern_confirmation_shadow import ACTION_DIRECTION, pattern_gate_action


def _dict(value):
    return value if isinstance(value, dict) else {}


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


def run(thesis_report: dict) -> dict:
    source_rows = list(thesis_report.get("rows") or [])
    rows = []
    changed_results = Counter()
    changed_playbooks = Counter()

    for source in source_rows:
        baseline_action = str(source.get("baseline_action") or "NO_TRADE")
        pattern = _dict(source.get("playbook_pattern_confirmation_shadow"))
        # Freeze candidate action before reading the historical outcome.
        gate = pattern_gate_action(baseline_action, pattern)
        playbook = str(source.get("declared_playbook") or "NO_DECLARED_PLAYBOOK")

        # Historical outcome is read only after the gate action above is frozen.
        outcome = _dict(source.get("baseline_outcome"))
        if gate["changed"]:
            changed_results[str(outcome.get("result") or "UNKNOWN")] += 1
            changed_playbooks[playbook] += 1
        rows.append({
            "click_timestamp": source.get("click_timestamp"),
            "baseline_action": baseline_action,
            "pattern_gate_action": gate["action"],
            "changed": gate["changed"],
            "change_reason": gate["reason"],
            "declared_playbook": source.get("declared_playbook"),
            "pattern_status": pattern.get("status"),
            "pattern_confirmed": bool(pattern.get("confirmed")),
            "baseline_outcome": outcome,
        })

    baseline = _performance(rows, "baseline_action")
    gated = _performance(rows, "pattern_gate_action")
    baseline_exp = baseline.get("expectancy_r_resolved")
    gated_exp = gated.get("expectancy_r_resolved")
    return {
        "mode": "COPPER_PLAYBOOK_PATTERN_GATE_SHADOW_V1",
        "research_only": True,
        "descriptive_only": True,
        "shadow_only": True,
        "production_rules_changed": False,
        "strategy_rules_changed": False,
        "validation_status": "POST_HOC_DIAGNOSTIC_REQUIRES_UNTOUCHED_FORWARD_TEST",
        "candidate_policy": {
            "name": "REQUIRE_LITERAL_DECLARED_PLAYBOOK_PATTERN_V1",
            "can_create_trade": False,
            "can_reverse_direction": False,
            "can_upgrade_abstention": False,
            "unconfirmed_action_effect": "DELAY_EXISTING_ACTION_TO_WAIT",
            "pattern_definition_changed_after_august_outcomes_seen": False,
            "gate_formalized_after_august_pattern_outcomes_seen": True,
            "promotion_from_august_result_allowed": False,
            "untouched_forward_validation_required": True,
        },
        "outcome_integrity": {
            "outcomes_read_for_gate_action": False,
            "outcomes_read_after_gate_action_freeze_for_evaluation": True,
        },
        "clicks": len(rows),
        "baseline": baseline,
        "pattern_gate": gated,
        "delta": {
            "trades": gated["trades"] - baseline["trades"],
            "resolved_trades": gated["resolved_trades"] - baseline["resolved_trades"],
            "targets": gated["targets"] - baseline["targets"],
            "stops": gated["stops"] - baseline["stops"],
            "resolved_r_sum": round(gated["resolved_r_sum"] - baseline["resolved_r_sum"], 4),
            "expectancy_r_resolved": (
                round(gated_exp - baseline_exp, 4)
                if gated_exp is not None and baseline_exp is not None else None
            ),
        },
        "changed_clicks": sum(bool(row["changed"]) for row in rows),
        "suppressed_baseline_outcomes": dict(sorted(changed_results.items())),
        "suppressed_playbooks": dict(sorted(changed_playbooks.items())),
        "rows": rows,
        "guardrails": [
            "The baseline action and literal playbook-pattern shadow are frozen before the candidate gate is applied.",
            "The candidate gate only delays an existing BUY_CE/BUY_PE action when its declared playbook pattern is not confirmed.",
            "The candidate gate cannot create a trade, reverse CE/PE direction, or upgrade WAIT/NO_TRADE.",
            "Historical outcomes are read only after the candidate action is frozen.",
            "The literal pattern definitions were frozen before their August outcome split was inspected.",
            "The gate itself was formalized after the August pattern outcome split was inspected, so August performance cannot validate or promote it.",
            "Promotion requires untouched forward data with the definitions and gate held fixed.",
            "No production Market Brain, Option Brain, risk rule, or live execution behavior changes from this diagnostic.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--thesis-report", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    source = json.loads(Path(args.thesis_report).read_text())
    report = run(source)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
