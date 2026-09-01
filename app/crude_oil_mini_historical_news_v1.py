from __future__ import annotations

"""Frozen point-in-time Crude Oil Mini historical-news ledger for the first news A/B replay.

The replay window is determined by the usable current CRUDEOILM contract history, not by Copper.
This ledger deliberately contains only records whose publication/release time can be defended.
The existing conservative Crude News Intelligence layer decides whether each record is ALLOW,
CONTEXT_ONLY, or BLOCK; the ledger itself never reads future price outcomes.
"""

from .crude_historical_news import crude_historical_news_v1


_REUTERS_EXPANSION = (
    {
        "event_id": "REUTERS_IRAN_OIL_RELIEF_20260616_160104Z",
        "underlying_event_id": "US_IRAN_OIL_RELIEF_MOU_20260616",
        "commodity": "CRUDEOIL",
        "event_type": "CEASEFIRE_DIPLOMACY",
        "available_at": "2026-06-16T21:31:04+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/us-iran-deal-allows-tehran-immediately-sell-oil-wsj-reports-2026-06-16/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "U.S.-Iran agreement would allow Tehran to sell oil once the deal is signed",
        },
        "audit": {"source_timestamp_utc": "2026-06-16T16:01:04Z"},
    },
    {
        "event_id": "REUTERS_HORMUZ_FLOW_RESUMPTION_20260621_234000Z",
        "underlying_event_id": "HORMUZ_FLOW_RESUMPTION_20260622",
        "commodity": "CRUDEOIL",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "available_at": "2026-06-22T05:10:00+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/world/middle-east/shipping-slows-after-iran-says-it-has-again-shut-strait-hormuz-2026-06-21/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "Shipping resumed through the Strait of Hormuz as tanker traffic picked up after the weekend closure",
        },
        "audit": {"source_timestamp_utc": "2026-06-21T23:40:00Z"},
    },
    {
        "event_id": "REUTERS_HORMUZ_TANKER_ATTACKS_20260707_020433Z",
        "underlying_event_id": "HORMUZ_TANKER_ATTACKS_20260707",
        "commodity": "CRUDEOIL",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "available_at": "2026-07-07T07:34:33+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/world/middle-east/iran-fires-missiles-commercial-ships-strait-hormuz-axios-reports-2026-07-07/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "Tanker attacks near the Strait of Hormuz raise shipping risk to severe",
        },
        "audit": {"source_timestamp_utc": "2026-07-07T02:04:33Z"},
    },
    {
        "event_id": "REUTERS_US_IRAN_BLOCKADE_20260712_221236Z",
        "underlying_event_id": "US_IRAN_NAVAL_BLOCKADE_20260714",
        "commodity": "CRUDEOIL",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "available_at": "2026-07-13T03:42:36+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/oil-jumps-more-than-3-after-us-iran-launch-strikes-mideast-2026-07-12/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "U.S. naval blockade covering Iranian ports and oil terminals revives Strait of Hormuz supply risk",
        },
        "audit": {"source_timestamp_utc": "2026-07-12T22:12:36Z"},
    },
    {
        "event_id": "REUTERS_HORMUZ_BLOCKADE_ACTIVE_20260715_031909Z",
        "underlying_event_id": "US_IRAN_NAVAL_BLOCKADE_20260714",
        "commodity": "CRUDEOIL",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "available_at": "2026-07-15T08:49:09+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/iran-linked-vessels-pass-through-hormuz-ahead-us-blockade-2026-07-15/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "material_update": True,
        "value": {
            "headline": "Strait of Hormuz shipping slows as the U.S. blockade takes effect and tanker attacks deter traffic",
        },
        "audit": {"source_timestamp_utc": "2026-07-15T03:19:09Z"},
    },
    {
        "event_id": "REUTERS_OPEC_SEPT_HIKE_20260802_070409Z",
        "underlying_event_id": "OPEC_PLUS_SEPTEMBER_HIKE_20260802",
        "commodity": "CRUDEOIL",
        "event_type": "OPEC_POLICY",
        "available_at": "2026-08-02T12:34:09+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/opec-has-agreement-principle-september-quota-increase-pause-thereafter-source-2026-08-02/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": True,
        "value": {
            "headline": "OPEC+ approves an output increase of about 188,000 barrels per day for September",
        },
        "audit": {"source_timestamp_utc": "2026-08-02T07:04:09Z"},
    },
    {
        "event_id": "REUTERS_GULF_EXPORTS_20260805_141804Z",
        "underlying_event_id": "GULF_EXPORT_FLOW_STATE_20260805",
        "commodity": "CRUDEOIL",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "available_at": "2026-08-05T19:48:04+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/gulf-oil-exports-steady-july-still-40-below-pre-war-mark-2026-08-05/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "Gulf crude exports remain about 40% below pre-war levels while Hormuz traffic stays constrained",
        },
        "audit": {"source_timestamp_utc": "2026-08-05T14:18:04Z"},
    },
    {
        "event_id": "REUTERS_HORMUZ_DEAL_TALKS_20260805_173000Z",
        "underlying_event_id": "HORMUZ_REOPENING_TALKS_20260805",
        "commodity": "CRUDEOIL",
        "event_type": "CEASEFIRE_DIPLOMACY",
        "available_at": "2026-08-05T23:00:00+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/world/middle-east/deal-reopen-hormuz-2026-08-05/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "Talks continue on a possible Strait of Hormuz reopening but key terms remain unresolved",
        },
        "audit": {"source_timestamp_utc": "2026-08-05T17:30:00Z"},
    },
    {
        "event_id": "REUTERS_IEA_SUPPLY_DEFICIT_20260812_080305Z",
        "underlying_event_id": "IEA_AUGUST_2026_SUPPLY_REVISION",
        "commodity": "CRUDEOIL",
        "event_type": "IEA_DEMAND_SUPPLY_REVISION",
        "available_at": "2026-08-12T13:33:05+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/iea-slashes-2026-supply-forecast-hormuz-reopening-remains-elusive-2026-08-12/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": True,
        "value": {
            "headline": "IEA slashes its 2026 oil supply forecast as Hormuz disruption deepens the global deficit",
        },
        "audit": {"source_timestamp_utc": "2026-08-12T08:03:05Z"},
    },
    {
        "event_id": "REUTERS_ADNOC_EXPANSION_20260813_122819Z",
        "underlying_event_id": "ADNOC_EXPANSION_20260813",
        "commodity": "CRUDEOIL",
        "event_type": "OPEC_POLICY",
        "available_at": "2026-08-13T17:58:19+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/adnoc-unbound-war-opec-exit-launch-emirates-oil-giant-quest-growth-2026-08-13/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "ADNOC pursues longer-term output growth after the UAE exit from OPEC",
        },
        "audit": {"source_timestamp_utc": "2026-08-13T12:28:19Z"},
    },
    {
        "event_id": "REUTERS_HORMUZ_LOW_TRAFFIC_20260826_042930Z",
        "underlying_event_id": "HORMUZ_LOW_TRAFFIC_20260826",
        "commodity": "CRUDEOIL",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "available_at": "2026-08-26T09:59:30+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/gulf-ship-traffic-via-strait-hormuz-hovers-below-10-day-average-data-shows-2026-08-26/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "Strait of Hormuz traffic fell well below the 10-day average while normal oil flows remained constrained",
        },
        "audit": {"source_timestamp_utc": "2026-08-26T04:29:30Z"},
    },
)


def crude_oil_mini_historical_news_v1() -> list[dict]:
    records = [dict(row) for row in crude_historical_news_v1()]
    records.extend(dict(row) for row in _REUTERS_EXPANSION)
    dedup = {}
    for row in records:
        event_id = str(row.get("event_id") or "")
        if not event_id:
            continue
        dedup[event_id] = row
    return sorted(dedup.values(), key=lambda row: str(row.get("available_at") or ""))


def crude_oil_mini_historical_news_metadata_v1() -> dict:
    records = crude_oil_mini_historical_news_v1()
    return {
        "mode": "CRUDE_OIL_MINI_PIT_HISTORICAL_NEWS_V1",
        "research_only": True,
        "point_in_time": True,
        "network_refetch_during_replay": False,
        "record_count": len(records),
        "coverage_is_exhaustive": False,
        "sources": sorted({str(row.get("source")) for row in records if row.get("source")}),
        "guardrails": [
            "Every record carries an explicit availability timestamp.",
            "Reuters publication times are frozen from the source article timestamp.",
            "EIA historical expectations without proof of pre-release availability remain context-only and cannot vote directionally.",
            "The ledger is frozen before the news replay reads future candles or trade outcomes.",
            "The ledger is Crude-specific and is not copied from Copper.",
        ],
    }
