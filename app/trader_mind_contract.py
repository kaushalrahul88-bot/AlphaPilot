from __future__ import annotations
from dataclasses import dataclass,asdict

@dataclass(frozen=True)
class TraderMindContract:
    objective:str="BUILD_HIGH_QUALITY_OPTION_TRADE_SETUP_UNDER_UNCERTAINTY"
    prediction_is_objective:bool=False
    allowed_actions:tuple[str,...]=("WAIT","NO_TRADE","BUY_CE","BUY_PE")
    principles:tuple[str,...]=(
      "Observe the market and context before forming a thesis.",
      "Maintain bullish, bearish, and no-edge scenarios rather than forcing direction.",
      "A setup requires an actionable trigger, explicit invalidation, and justified reward versus risk.",
      "Use historical experience as evidence, not as an oracle.",
      "Missing context remains missing and reduces conviction when material.",
      "Confidence expresses evidence quality and uncertainty; it is not certainty of the next move.",
      "A valid process may lose and a poor process may win; evaluate both process and outcome.",
      "Option selection follows the underlying trade thesis and must use genuine option data when available.",
    )

def trader_mind_contract():
    return asdict(TraderMindContract())

def validate_setup(setup:dict)->dict:
    action=str(setup.get("action") or "")
    if action not in TraderMindContract().allowed_actions:
        return {"valid":False,"reason":"UNKNOWN_ACTION"}
    if action in {"WAIT","NO_TRADE"}:
        return {"valid":True,"reason":"ABSTENTION_ALLOWED"}
    required=("thesis","entry_trigger","invalidation","target_or_exit_logic","risk_reward_basis")
    missing=[x for x in required if not setup.get(x)]
    return {"valid":not missing,"reason":"READY" if not missing else "INCOMPLETE_TRADE_PLAN","missing":missing}
