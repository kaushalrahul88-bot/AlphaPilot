from __future__ import annotations
import argparse,json
from collections import Counter
from pathlib import Path
from app.trader_evidence_roles import role_geometry

_DIRECTIONAL={"BULLISH","BEARISH"}


def _v1_direction(evidence:dict):
    b=len(evidence.get("independent_bullish_lanes") or [])
    s=len(evidence.get("independent_bearish_lanes") or [])
    return "BULLISH" if b>s else "BEARISH" if s>b else None


def _v2_geometry(evidence:dict):
    return role_geometry(evidence)


def compare(payload:dict)->dict:
    rows=[];transitions=Counter();actionable_conflicts=Counter()
    for click in payload.get("decisions") or []:
        evidence=click.get("evidence") or {}
        v1=_v1_direction(evidence)
        v2=_v2_geometry(evidence)
        anchor=v2.get("actionable_direction")
        transition=f"{v1 or 'NONE'}->{anchor or 'NONE'}"
        transitions[transition]+=1
        action=str((click.get("decision") or {}).get("action") or "UNKNOWN")
        if action in {"BUY_CE","BUY_PE"} and v1!=anchor:
            actionable_conflicts[transition]+=1
        if v1!=anchor:
            rows.append({
                "click_timestamp":click.get("click_timestamp"),
                "v1_direction":v1,
                "v2_anchor_direction":anchor,
                "v2_anchor_state":v2.get("anchor_state"),
                "recorded_action":action,
                "v2_confirmations":v2.get("confirmations"),
                "v2_contradictions":v2.get("contradictions"),
                "v2_context":v2.get("context"),
                "v2_memory":v2.get("memory"),
            })
    return {
        "mode":"TRADER_EVIDENCE_V1_V2_SHADOW_COMPARISON_V1",
        "outcome_blind":True,
        "outcomes_read":False,
        "clicks":len(payload.get("decisions") or []),
        "direction_differences":len(rows),
        "transition_counts":dict(sorted(transitions.items())),
        "recorded_action_conflicts":dict(sorted(actionable_conflicts.items())),
        "differences":rows,
        "guardrails":[
            "Historical outcomes are not read by this comparator.",
            "Recorded action is descriptive only and is never used to derive V2 direction.",
            "V2 remains shadow-only; this script does not change Current Mind decisions.",
        ],
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("result_json",type=Path)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    result=compare(json.loads(args.result_json.read_text()))
    text=json.dumps(result,indent=2,sort_keys=True)
    if args.output:args.output.write_text(text+"\n")
    else:print(text)


if __name__=="__main__":main()
