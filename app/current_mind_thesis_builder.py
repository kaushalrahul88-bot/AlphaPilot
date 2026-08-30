from __future__ import annotations
from .setup_playbook_selector import eligible_playbooks,setup_candidate
from .trade_thesis_contract import build_trade_thesis,gate_trade_thesis
from .setup_risk_review import review_setup_risk

def build_current_mind_decision(board,regime,evidence,scenario,memory,market:dict|None=None):
    market=market or {}
    labels=regime.get("regime_labels",[])
    plays=eligible_playbooks(labels)
    quality=_quality(evidence)
    bull=len(evidence.get("independent_bullish_lanes",[]))
    bear=len(evidence.get("independent_bearish_lanes",[]))
    contradictions=len(evidence.get("contradictory_lanes",[]))
    if quality in {"WEAK","CONFLICTED"} or contradictions:
        return {"action":"NO_TRADE","reason":"EVIDENCE_NOT_COHERENT","evidence_quality":quality,
                "thesis":"No sufficiently coherent risk-defined opportunity at this click.","playbooks":plays}
    direction="BULLISH" if bull>bear else "BEARISH" if bear>bull else None
    if not direction:
        return {"action":"NO_TRADE","reason":"NO_DIRECTIONAL_OPPORTUNITY","evidence_quality":quality,"playbooks":plays}
    eligible=plays.get("eligible",[])
    if not eligible:
        return {"action":"WAIT","reason":"NO_REGIME_APPROPRIATE_PLAYBOOK","evidence_quality":quality,"direction":direction,"playbooks":plays}
    play=eligible[0]["playbook"]
    confirmation=market.get("confirmation")
    trigger=market.get("entry_trigger");invalidation=market.get("invalidation")
    target=market.get("target_logic")
    if not all((confirmation,trigger,invalidation,target)):
        return {"action":"WAIT","reason":"PLAYBOOK_CONFIRMATION_PENDING","direction":direction,"playbook":play,
                "evidence_quality":quality,"waiting_for":[k for k,v in {"confirmation":confirmation,"entry_trigger":trigger,"invalidation":invalidation,"target_logic":target}.items() if not v]}
    thesis=build_trade_thesis(direction=direction,thesis=f"{play}: coherent {direction.lower()} evidence in current regime",
      entry_trigger=trigger,invalidation=invalidation,target_logic=target,risk_reward_basis=market.get("risk_reward_basis") or "STRUCTURAL_REVIEW_REQUIRED",
      contradictions=evidence.get("contradictory_lanes",[]),evidence_quality=quality)
    gated=gate_trade_thesis(thesis)
    if gated["action"] not in {"BUY_CE","BUY_PE"}:return gated
    if not all(market.get(k) is not None for k in ("entry_price","stop_price","target_price")):
        return {"action":"WAIT","reason":"RISK_LEVELS_PENDING","direction":direction,"playbook":play,"thesis":thesis}
    risk=review_setup_risk({"direction":direction,"entry_price":market["entry_price"],"stop_price":market["stop_price"],"target_price":market["target_price"]},market)
    if risk["status"]!="PASS_TO_OPTION_BRAIN":
        return {"action":"NO_TRADE","reason":risk["reason"],"direction":direction,"playbook":play,"thesis":thesis,"risk_review":risk}
    return {"action":"BUY_CE" if direction=="BULLISH" else "BUY_PE","reason":"CURRENT_MIND_ACTIONABLE_SETUP",
            "direction":direction,"playbook":play,"thesis":thesis,"risk_review":risk,
            "entry_trigger":trigger,"invalidation":invalidation,"target_or_exit_logic":target,
            "risk_reward_basis":thesis["risk_reward_basis"],"contradictions":thesis["contradictions"],
            "missing_context":[k for k,v in (board.get("groups") or {}).items() if all(x.get("status")=="UNAVAILABLE" for x in v)]}

def _quality(e):
    from .trader_evidence_synthesis import evidence_quality
    return evidence_quality(e)
