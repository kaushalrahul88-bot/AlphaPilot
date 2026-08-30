from __future__ import annotations

def build_scenario_board(observations:list[dict],memory:dict|None=None)->dict:
    buckets={"BULLISH":[],"BEARISH":[],"CAUTION":[],"UNKNOWN":[]}
    for x in observations:
        stance=str(x.get("stance") or "UNKNOWN").upper()
        if stance not in buckets: stance="UNKNOWN"
        buckets[stance].append(x)
    mem=memory or {"status":"UNAVAILABLE"}
    return {
      "mode":"TRADER_SCENARIO_BOARD_V1",
      "bullish_case":{"evidence":buckets["BULLISH"],"historical_experience":mem.get("bullish")},
      "bearish_case":{"evidence":buckets["BEARISH"],"historical_experience":mem.get("bearish")},
      "caution_case":{"evidence":buckets["CAUTION"]},
      "unknown":{"evidence":buckets["UNKNOWN"]},
      "decision_policy":[
        "Do not count evidence items as votes.",
        "Seek a coherent market thesis and explicit contradictory evidence.",
        "Prefer WAIT when a thesis needs confirmation.",
        "Prefer NO_TRADE when reward/risk, context, or evidence quality is inadequate.",
        "BUY_CE/BUY_PE require a trigger and invalidation; direction alone is not a setup.",
      ],
    }

def propose_action(board:dict, *, thesis:str|None=None, trigger:str|None=None,
                   invalidation:str|None=None, reward_risk_ok:bool=False,
                   needs_confirmation:bool=False)->dict:
    if needs_confirmation:
        return {"action":"WAIT","reason":"THESIS_NEEDS_CONFIRMATION","thesis":thesis}
    if not thesis or not trigger or not invalidation or not reward_risk_ok:
        return {"action":"NO_TRADE","reason":"NO_ACTIONABLE_SETUP","thesis":thesis}
    return {"action":"SETUP_CANDIDATE","reason":"ACTIONABLE_THESIS_PRESENT",
            "thesis":thesis,"entry_trigger":trigger,"invalidation":invalidation}
