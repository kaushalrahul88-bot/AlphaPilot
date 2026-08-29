"""Timestamp-safe historical context records for commodity research.

Research only. A context value is usable in replay only if its availability
timestamp is not later than the simulated decision timestamp.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Literal

ContextKind = Literal["FX","GLOBAL_FUTURE","POSITIONING","NEWS_EVENT","OPTION_MARKET"]


@dataclass(frozen=True)
class HistoricalContext:
    context_id: str
    commodity: str
    kind: ContextKind
    observed_at: str
    available_at: str
    source_name: str
    source_url: str
    source_tier: str
    values: dict
    frequency: str
    notes: str = ""


def replay_eligible(item: HistoricalContext, decision_at: str) -> bool:
    return bool(item.available_at and item.available_at <= decision_at)


def latest_available(items, decision_at: str, *, kind: str | None = None):
    eligible=[
        x for x in items
        if replay_eligible(x, decision_at) and (kind is None or x.kind == kind)
    ]
    return max(eligible,key=lambda x:x.available_at) if eligible else None


def historical_context_manifest_v1() -> dict:
    return {
        "version":"COPPER_HISTORICAL_CONTEXT_MANIFEST_V1",
        "research_only":True,
        "production_rules_changed":False,
        "lookahead_rule":"Use available_at, never observation date alone, when replaying a historical decision.",
        "feeds":[
            {
                "id":"USDINR_FED_H10_DAILY",
                "kind":"FX","frequency":"daily",
                "source_name":"Federal Reserve H.10 / FRED",
                "source_url":"https://fred.stlouisfed.org/series/DEXINUS",
                "role":"Daily USD/INR translation context for MCX Copper.",
                "replay_note":"Value becomes usable only at its recorded publication/availability time; never backfill it into earlier intraday decisions.",
            },
            {
                "id":"CFTC_COPPER_DISAGGREGATED",
                "kind":"POSITIONING","frequency":"weekly",
                "source_name":"CFTC Commitments of Traders Public Reporting Environment",
                "source_url":"https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
                "role":"Producer/Merchant, Swap Dealer, Managed Money and Other Reportables positioning context.",
                "replay_note":"Tuesday positions are not available on Tuesday; use report release availability timestamp.",
            },
            {
                "id":"GLOBAL_COPPER_FUTURES",
                "kind":"GLOBAL_FUTURE","frequency":"intraday_or_eod",
                "source_name":"Licensed/authorized global copper market-data provider",
                "source_url":"",
                "role":"International copper direction, magnitude and volatility context.",
                "replay_note":"Do not fabricate historical intraday prices. Provider must preserve point-in-time timestamps and licensing.",
            },
            {
                "id":"COPPER_NEWS_EVENTS",
                "kind":"NEWS_EVENT","frequency":"event",
                "source_name":"Primary releases plus timestamped professional reporting",
                "source_url":"",
                "role":"Supply, demand, policy, macro and disruption events.",
                "replay_note":"Use first_detected/available timestamp, deduplicate syndicated copies, and distinguish expected versus actual for scheduled releases.",
            },
            {
                "id":"MCX_COPPER_OPTION_MARKET",
                "kind":"OPTION_MARKET","frequency":"intraday",
                "source_name":"Authorized MCX/broker option-chain history",
                "source_url":"",
                "role":"Premium, strike, expiry, bid/ask, OI, volume and IV/Greeks where available.",
                "replay_note":"Required before claiming historical option profitability; underlying correctness alone is insufficient.",
            },
        ],
        "minimum_replay_join":{
            "keys":["commodity","decision_timestamp"],
            "rule":"Attach only the latest context whose available_at <= decision_timestamp.",
            "missing_data":"UNKNOWN; never forward-fill from future publications.",
        },
    }
