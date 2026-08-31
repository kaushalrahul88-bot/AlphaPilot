from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app.trader_evidence_synthesis import synthesize_evidence


def load(path):
    return json.loads(Path(path).read_text())


def action(row):
    return (row.get("decision") or {}).get("action")


def news(row):
    lanes=((row.get("evidence") or {}).get("lanes") or {}).get("NEWS") or []
    return lanes[0] if lanes else None


def _flatten_evidence(row):
    lanes=(row.get("evidence") or {}).get("lanes") or {}
    return [item for rows in lanes.values() for item in (rows or [])]


def news_interaction(row):
    """Reclassify stored point-in-time evidence under the current interaction model.

    This uses evidence frozen at the click only. Outcome fields are deliberately
    not read, so the classification remains usable as an outcome-blind forensic
    audit of old replay artifacts.
    """
    items=_flatten_evidence(row)
    if not items:
        return None
    synthesis=synthesize_evidence(items)
    interactions=synthesis.get("news_price_interactions") or []
    return interactions[0] if interactions else None


def action_transition(baseline_action, variant_action):
    trades={"BUY_CE","BUY_PE"}
    if baseline_action==variant_action:
        return "UNCHANGED_ACTION"
    if baseline_action not in trades and variant_action in trades:
        return "NO_TRADE_TO_TRADE"
    if baseline_action in trades and variant_action not in trades:
        return "TRADE_TO_NO_TRADE"
    if baseline_action in trades and variant_action in trades:
        return "DIRECTION_FLIP"
    return "OTHER_ACTION_CHANGE"


def _forensic_row(ts, baseline_row, variant_row):
    interaction=news_interaction(variant_row)
    return {
        "click_timestamp":ts,
        "baseline_action":action(baseline_row),
        "news_action":action(variant_row),
        "action_transition":action_transition(action(baseline_row),action(variant_row)),
        "news_interaction":interaction,
        "news":news(variant_row),
        "baseline_thesis":baseline_row.get("thesis"),
        "news_thesis":variant_row.get("thesis"),
        "baseline_outcome":baseline_row.get("outcome"),
        "news_outcome":variant_row.get("outcome"),
    }


def build_report(baseline, variant):
    bm={x["click_timestamp"]:x for x in baseline["decisions"]}
    vm={x["click_timestamp"]:x for x in variant["decisions"]}
    if set(bm)!=set(vm):
        raise RuntimeError("Click sets differ")

    changed=[]
    visible=[]
    for ts in sorted(vm):
        bx,vx=bm[ts],vm[ts]
        n=news(vx)
        detail=(n or {}).get("detail") or {}
        if detail.get("visible"):
            row=_forensic_row(ts,bx,vx)
            visible.append(row)
            if action(bx)!=action(vx):
                row=dict(row)
                row["baseline_fingerprint"]=bx.get("decision_fingerprint")
                row["news_fingerprint"]=vx.get("decision_fingerprint")
                changed.append(row)

    state_counts=Counter(
        (x.get("news_interaction") or {}).get("state") or "UNCLASSIFIED"
        for x in visible
    )
    role_counts=Counter(
        (x.get("news_interaction") or {}).get("directional_role") or "UNCLASSIFIED"
        for x in visible
    )
    changed_state_counts=Counter(
        (x.get("news_interaction") or {}).get("state") or "UNCLASSIFIED"
        for x in changed
    )
    transition_counts=Counter(x["action_transition"] for x in changed)

    return {
        "mode":"CURRENT_MIND_NEWS_CLICK_FORENSIC_V2",
        "interaction_model":"TRADER_EVIDENCE_SYNTHESIS_V1",
        "classification_is_outcome_blind":True,
        "baseline_actions":baseline.get("actions"),
        "news_actions":variant.get("actions"),
        "baseline_expectancy_r":baseline.get("expectancy_r_resolved"),
        "news_expectancy_r":variant.get("expectancy_r_resolved"),
        "news_visible_click_count":len(visible),
        "action_changed_click_count":len(changed),
        "news_interaction_state_counts":dict(sorted(state_counts.items())),
        "news_directional_role_counts":dict(sorted(role_counts.items())),
        "changed_click_interaction_state_counts":dict(sorted(changed_state_counts.items())),
        "changed_action_transition_counts":dict(sorted(transition_counts.items())),
        "changed_clicks":changed,
        "news_visible_clicks":visible,
        "guardrails":[
            "Interaction classification reads only evidence frozen at or before each click.",
            "Outcome fields are retained for descriptive attribution but are not read by interaction classification.",
            "This report does not recompute trade decisions or claim post-change replay performance.",
            "No parameter, threshold or strategy change is authorized by this report.",
        ],
    }


def main(baseline, variant, output):
    report=build_report(load(baseline),load(variant))
    Path(output).write_text(json.dumps(report,indent=2,sort_keys=True))
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--baseline",required=True)
    parser.add_argument("--variant",required=True)
    parser.add_argument("--output",default="news-click-forensic-v2.json")
    args=parser.parse_args()
    main(args.baseline,args.variant,args.output)
