from __future__ import annotations

"""Small audited seed ledger for Crude News Intelligence V1.

This is intentionally conservative. Official EIA actuals are retained, but historical
consensus values reconstructed after release are marked ``expected_pit_safe=False``
and therefore cannot create directional NEWS votes. Reuters records are included only
where a publication timestamp was recoverable to the second from the source result.
The ledger is a seed for an event-reaction audit, not a claim of exhaustive coverage.
"""

EIA_WPSR_URL = "https://www.eia.gov/petroleum/supply/weekly/"
EIA_SCHEDULE_URL = "https://www.eia.gov/petroleum/supply/weekly/schedule.php"

# Values are million barrels, change in U.S. commercial crude inventories.
# The consensus figures are preserved for audit visibility only. Because this
# reconstruction did not capture them before each release, the intelligence layer
# must refuse to use them as a historical surprise signal.
_EIA_CRUDE = (
    ("2026-07-01T20:00:00+05:30", -3.775, -2.900),
    ("2026-07-08T20:00:00+05:30", 2.998, -1.900),
    ("2026-07-15T20:00:00+05:30", -1.692, -1.800),
    ("2026-07-22T20:00:00+05:30", 2.010, -2.000),
    ("2026-07-29T20:00:00+05:30", -7.167, 0.700),
    ("2026-08-05T20:00:00+05:30", 2.479, -1.500),
    ("2026-08-12T20:00:00+05:30", 17.423, -1.700),
    ("2026-08-19T20:00:00+05:30", 4.405, 0.200),
    ("2026-08-26T20:00:00+05:30", 0.095, 1.600),
)


def _eia_records() -> list[dict]:
    records = []
    for i, (available_at, actual, reconstructed_expected) in enumerate(_EIA_CRUDE, start=1):
        records.append({
            "event_id": f"EIA_CRUDE_2026_{i:02d}",
            "underlying_event_id": f"EIA_CRUDE_2026_{available_at[:10]}",
            "commodity": "CRUDEOIL",
            "event_type": "EIA_CRUDE_INVENTORY",
            "available_at": available_at,
            "source": "U.S. Energy Information Administration",
            "source_url": EIA_WPSR_URL,
            "source_tier": "A_PRIMARY",
            "scheduled": True,
            "value": {
                "headline": "EIA Weekly Petroleum Status Report: U.S. commercial crude inventory change",
                "actual_value": actual,
                "expected_value": reconstructed_expected,
                "expected_pit_safe": False,
                "unit": "million_barrels",
            },
            "audit": {
                "release_schedule_source": EIA_SCHEDULE_URL,
                "expectation_status": "POST_EVENT_RECONSTRUCTION_NOT_DIRECTIONALLY_ELIGIBLE",
            },
        })
    return records


_REUTERS_EVENTS = (
    {
        "event_id": "REUTERS_HORMUZ_20260818_011303Z",
        "underlying_event_id": "HORMUZ_CEASEFIRE_EXPIRY_20260818",
        "commodity": "CRUDEOIL",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "available_at": "2026-08-18T06:43:03+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/oil-climbs-fading-us-iran-peace-hopes-raise-supply-risks-2026-08-18/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "Iran says Strait of Hormuz remains closed as ceasefire expires and posture turns more offensive",
        },
        "audit": {"source_timestamp_utc": "2026-08-18T01:13:03Z"},
    },
    {
        "event_id": "REUTERS_SHIPPING_20260818_075510Z",
        "underlying_event_id": "CHINA_SHIPPERS_CHOKEPOINT_AVOIDANCE_20260818",
        "commodity": "CRUDEOIL",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "available_at": "2026-08-18T13:25:10+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/business/energy/chinas-state-shippers-deploy-oil-tankers-outside-gulf-avoid-chokepoints-sources-2026-08-18/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "value": {
            "headline": "Chinese state shippers avoid Strait of Hormuz and collect oil outside the Gulf",
        },
        "audit": {"source_timestamp_utc": "2026-08-18T07:55:10Z"},
    },
    {
        "event_id": "REUTERS_HORMUZ_20260818_151824Z",
        "underlying_event_id": "HORMUZ_CEASEFIRE_EXPIRY_20260818",
        "commodity": "CRUDEOIL",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "available_at": "2026-08-18T20:48:24+05:30",
        "source": "Reuters",
        "source_url": "https://www.reuters.com/world/middle-east/trump-says-no-talks-planned-with-iran-tehran-says-strait-hormuz-still-shut-2026-08-18/",
        "source_tier": "C_PROFESSIONAL",
        "scheduled": False,
        "material_update": True,
        "value": {
            "headline": "No U.S.-Iran talks are planned; Iran says Strait of Hormuz remains shut while shipping disruption persists",
        },
        "audit": {"source_timestamp_utc": "2026-08-18T15:18:24Z"},
    },
)


def crude_historical_news_v1() -> list[dict]:
    return sorted([*_eia_records(), *[dict(x) for x in _REUTERS_EVENTS]], key=lambda r: r["available_at"])


def crude_historical_news_metadata_v1() -> dict:
    records = crude_historical_news_v1()
    return {
        "mode": "CRUDE_HISTORICAL_NEWS_SEED_V1",
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "record_count": len(records),
        "coverage_is_exhaustive": False,
        "purpose": "PIT-safe event-reaction audit before any Crude Current Mind integration.",
        "known_limitations": [
            "The seed ledger is not exhaustive geopolitical-news coverage.",
            "Historical EIA consensus values were reconstructed after release and are forbidden from directional voting.",
            "More Reuters/primary-source events require exact first-availability timestamps before inclusion.",
        ],
    }
