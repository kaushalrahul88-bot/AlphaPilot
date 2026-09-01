from __future__ import annotations

# This map describes which observable point-in-time series could test each domain
# hypothesis.  It is deliberately metadata-only: it does not create evidence
# votes or modify Current Mind decisions.
CRUDE_DOMAIN_CONTEXT_MAP_V1 = {
    "cross_market": ("WTI_CRUDE", "BRENT_CRUDE", "USDINR", "DXY"),
    "fundamentals": ("EIA_CRUDE_BALANCE", "OPEC_POLICY", "GLOBAL_CRUDE_DEMAND"),
    "inventories": ("EIA_CRUDE_INVENTORY", "EIA_GASOLINE_INVENTORY", "EIA_DISTILLATE_INVENTORY"),
    "refining": ("EIA_REFINERY_UTILIZATION", "EIA_CRUDE_INPUTS"),
    "supply": ("OPEC_POLICY", "US_CRUDE_PRODUCTION", "SPR_FLOWS", "PRODUCER_OUTAGE"),
    "geopolitics": ("CRUDE_NEWS", "HORMUZ_SHIPPING", "SANCTIONS_EXPORT_POLICY"),
    "weather": ("CRUDE_WEATHER_RISK", "REFINERY_OUTAGE", "PRODUCER_OUTAGE"),
    "term_structure": ("WTI_CURVE", "BRENT_CURVE"),
    "demand": ("CHINA_CRUDE_IMPORTS", "CHINA_REFINERY_RUNS", "GLOBAL_CRUDE_DEMAND"),
    "options_volatility": ("MCX_CRUDEOILM_OPTION",),
}


def crude_domain_context_map_v1() -> dict:
    return {
        "version": "CRUDE_DOMAIN_CONTEXT_MAP_V1",
        "decision_effect": "NONE",
        "series_by_family": {key: list(value) for key, value in CRUDE_DOMAIN_CONTEXT_MAP_V1.items()},
        "policy": {
            "series_must_be_observed_point_in_time": True,
            "missing_series_stays_unknown": True,
            "mapping_does_not_create_directional_vote": True,
            "news_series_remains_disabled_in_no_news_baseline": True,
            "option_series_remains_disabled_in_underlying_market_brain_baseline": True,
        },
    }
