from __future__ import annotations
from hashlib import sha256
import json

def journal_decision(*,click_timestamp,information_board,regime,evidence,scenario,thesis,decision,option_expression=None):
    payload={"click_timestamp":click_timestamp,"information_board":information_board,"regime":regime,
      "evidence":evidence,"scenario":scenario,"thesis":thesis,"decision":decision,
      "option_expression":option_expression,
      "outcome":None,"review":None,
      "rule":"Decision state is frozen before outcome attachment; review may learn from outcome but may not rewrite the original decision."}
    canonical=json.dumps(payload,sort_keys=True,separators=(",",":"),default=str)
    payload["decision_fingerprint"]=sha256(canonical.encode()).hexdigest()
    return payload

def attach_outcome(entry:dict,outcome:dict)->dict:
    x=dict(entry)
    before=x.get("decision_fingerprint")
    x["outcome"]=dict(outcome)
    x["decision_fingerprint"]=before
    return x

def review_decision(entry:dict,review:dict)->dict:
    x=dict(entry);x["review"]=dict(review);return x
