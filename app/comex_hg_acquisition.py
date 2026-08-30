from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class CmeDataMineConfig:
    dataset_code:str
    exchange_code:str="XCEC"
    product_code:str="HG"
    foi_indicator:str="FUT"

def normalize_comex_bar(row:dict[str,Any], *, source:str, available_at:str)->dict:
    """Normalize a licensed COMEX HG observation into AlphaPilot PIT context."""
    ts=row.get("timestamp") or row.get("observed_at")
    if not ts: raise ValueError("COMEX row requires timestamp/observed_at")
    value={k:row.get(k) for k in ("open","high","low","close","volume","open_interest") if row.get(k) is not None}
    if not value: raise ValueError("COMEX row has no market values")
    return {"series":"COMEX_HG","observed_at":str(ts),"available_at":str(available_at),
            "source":source,"value":value,"quality":"OBSERVED",
            "metadata":{"exchange":"COMEX","product":"HG","licensed":True}}

def entitlement_query(config:CmeDataMineConfig,period_date:str)->dict:
    return {"exchange_code":config.exchange_code,"product_code":config.product_code,
            "dataset_code":config.dataset_code,"period_date":period_date,
            "foi_indicator":config.foi_indicator,"limit":"1000","offset":"0"}

def acquisition_status(*,credentials_configured:bool,entitled_files:int=0)->dict:
    if not credentials_configured:
        state="CREDENTIALS_REQUIRED"
    elif entitled_files<=0:
        state="ENTITLEMENT_REQUIRED"
    else:
        state="READY_TO_DOWNLOAD"
    return {"series":"COMEX_HG","state":state,
            "accepted_source":"CME DataMine or another explicitly licensed CME historical feed",
            "replay_allowed":state=="READY_TO_DOWNLOAD",
            "rule":"Do not substitute public delayed/current quotes or end-of-day values for historical intraday click-time observations."}
