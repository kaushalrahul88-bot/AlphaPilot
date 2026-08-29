"""Authoritative historical context ingestion helpers for Copper research.

No production trading use. Publication/availability timestamps are explicit so
historical replay cannot consume information before it was actually available.
"""
from __future__ import annotations
import csv, io, json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .historical_context import HistoricalContext

CFTC_DISAGG_FUTURES_ONLY="https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
FED_H10_INR_HTML="https://www.federalreserve.gov/RELEASES/H10/hist/dat00_in.htm"


def _get(url:str, attempts:int=3, timeout_seconds:int=30)->bytes:
    import time
    req=Request(url,headers={"User-Agent":"AlphaPilot research/1.0"})
    last=None
    for attempt in range(max(1,int(attempts))):
        try:
            with urlopen(req,timeout=timeout_seconds) as r:
                return r.read()
        except Exception as exc:
            last=exc
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
    raise last


def fetch_cftc_copper_positioning(start_date:str,end_date:str)->list[HistoricalContext]:
    # COMEX Copper CFTC contract market code 085692.
    q={
      "$where":f"cftc_contract_market_code='085692' AND report_date_as_yyyy_mm_dd between '{start_date}T00:00:00.000' and '{end_date}T00:00:00.000'",
      "$order":"report_date_as_yyyy_mm_dd asc","$limit":"5000",
    }
    rows=json.loads(_get(CFTC_DISAGG_FUTURES_ONLY+"?"+urlencode(q)).decode())
    out=[]
    for row in rows:
        report=str(row.get("report_date_as_yyyy_mm_dd",""))[:10]
        if not report:continue
        # COT reflects Tuesday positions and is normally released Friday.
        d=datetime.fromisoformat(report).replace(tzinfo=timezone.utc)
        release=(d+timedelta(days=3)).replace(hour=20,minute=30)
        vals={k:row.get(k) for k in (
            "market_and_exchange_names","open_interest_all",
            "prod_merc_positions_long","prod_merc_positions_short",
            "swap_positions_long_all","swap__positions_short_all",
            "m_money_positions_long_all","m_money_positions_short_all",
            "other_rept_positions_long","other_rept_positions_short",
        )}
        out.append(HistoricalContext(
            context_id=f"CFTC_COPPER_{report}",commodity="COPPER",kind="POSITIONING",
            observed_at=d.isoformat(),available_at=release.isoformat(),
            source_name="CFTC Disaggregated Futures Only",
            source_url=CFTC_DISAGG_FUTURES_ONLY,source_tier="A_PRIMARY",
            values=vals,frequency="weekly",
            notes="Tuesday positions; conservative Friday availability timestamp for replay.",
        ))
    return out


def fetch_fred_usdinr_daily(start_date:str|None=None,end_date:str|None=None)->list[HistoricalContext]:
    """Fetch official Federal Reserve H.10 historical INR-per-USD rates."""
    import re
    html=_get(FED_H10_INR_HTML,attempts=3,timeout_seconds=45).decode("utf-8","ignore")
    text=re.sub(r"<[^>]+>"," ",html)
    text=re.sub(r"&nbsp;"," ",text)
    matches=re.findall(r"\b(\d{1,2}-[A-Z]{3}-\d{2})\s+(ND|\d+(?:\.\d+)?)\b",text,re.I)
    out=[]
    for raw_date,raw_value in matches:
        if raw_value.upper()=="ND":continue
        observed=datetime.strptime(raw_date.upper(),"%d-%b-%y").replace(tzinfo=timezone.utc)
        date=observed.date().isoformat()
        if start_date and date < start_date:continue
        if end_date and date > end_date:continue
        available=observed+timedelta(days=1)
        out.append(HistoricalContext(
            context_id=f"DEXINUS_{date}",commodity="COPPER",kind="FX",
            observed_at=observed.isoformat(),available_at=available.isoformat(),
            source_name="Federal Reserve Board H.10",source_url=FED_H10_INR_HTML,
            source_tier="A_PRIMARY",values={"usdinr":float(raw_value)},frequency="daily",
            notes="Official H.10 historical rate; daily reference context only, not intraday FX. Historical page may incorporate later corrections.",
        ))
    return out


def copper_context_snapshot(start_date:str,end_date:str)->dict:
    cot=fetch_cftc_copper_positioning(start_date,end_date)
    fx=fetch_fred_usdinr_daily()
    fx=[x for x in fx if start_date <= x.observed_at[:10] <= end_date]
    return {
      "version":"COPPER_AUTHORITATIVE_CONTEXT_INGESTION_V1",
      "research_only":True,"production_rules_changed":False,
      "cftc":[x.__dict__ for x in cot],"usdinr":[x.__dict__ for x in fx],
      "limitations":[
        "CFTC is weekly positioning context, not an intraday timing signal.",
        "FRED DEXINUS is daily reference data, not an intraday USD/INR feed.",
        "Availability timestamps are deliberately conservative to prevent lookahead.",
      ],
    }
