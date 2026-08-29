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
FRED_DEXINUS_CSV="https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXINUS"


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


def fetch_fred_usdinr_daily()->list[HistoricalContext]:
    rows=csv.DictReader(io.StringIO(_get(FRED_DEXINUS_CSV).decode()))
    out=[]
    for row in rows:
        date=row.get("observation_date") or row.get("DATE")
        raw=row.get("DEXINUS")
        if not date or raw in (None,"","."):continue
        try:value=float(raw)
        except ValueError:continue
        observed=datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
        # H.10/FRED is not an intraday FX feed. Conservative rule: prior day's
        # observation is only usable from the following UTC day in replay.
        available=(observed+timedelta(days=1))
        out.append(HistoricalContext(
            context_id=f"DEXINUS_{date}",commodity="COPPER",kind="FX",
            observed_at=observed.isoformat(),available_at=available.isoformat(),
            source_name="Federal Reserve H.10 via FRED",source_url=FRED_DEXINUS_CSV,
            source_tier="A_PRIMARY",values={"usdinr":value},frequency="daily",
            notes="Daily reference context only; never substitute for intraday USD/INR.",
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
