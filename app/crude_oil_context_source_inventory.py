from __future__ import annotations

from .crude_historical_news import crude_historical_news_v1
from .crude_oil_domain_knowledge_context_map import crude_domain_context_map_v1
from .crude_oil_mini_point_in_time_context import acquisition_manifest


def _eia_seed_coverage() -> dict:
    rows = [row for row in crude_historical_news_v1() if row.get("event_type") == "EIA_CRUDE_INVENTORY"]
    pit_safe_expectations = [
        row for row in rows
        if bool(((row.get("value") or {}).get("expected_pit_safe")))
    ]
    return {
        "records": len(rows),
        "first_available_at": rows[0]["available_at"] if rows else None,
        "last_available_at": rows[-1]["available_at"] if rows else None,
        "pit_safe_expectation_records": len(pit_safe_expectations),
        "directional_surprise_ready": bool(rows and len(pit_safe_expectations) == len(rows)),
    }


def crude_oil_context_source_inventory_v1() -> dict:
    """Describe what Crude PIT context is genuinely usable for the frozen replay.

    This inventory is intentionally conservative. A source is not considered ready
    merely because AlphaPilot knows the economic mechanism or can fetch a current
    quote. Full-window research requires timestamped observations that were actually
    available at the simulated click.
    """
    eia = _eia_seed_coverage()
    feeds = {
        "MCX_CRUDEOILM": {
            "status": "FULL_WINDOW_PIT_READY",
            "coverage": "Frozen exact-contract CRUDEOILM21SEP26FUT 5m tape through 2026-08-31",
            "decision_use_now": True,
            "ablation_ready": True,
            "reason": "Certified local market tape with completed-bar availability semantics.",
        },
        "WTI_CRUDE": {
            "status": "HISTORICAL_INTRADAY_SOURCE_REQUIRED",
            "coverage": "Existing cross-assets path fetches only a near-live Yahoo 2d/5m snapshot.",
            "decision_use_now": False,
            "ablation_ready": False,
            "reason": "A current snapshot cannot be retroactively used at June-August clicks.",
        },
        "BRENT_CRUDE": {
            "status": "HISTORICAL_INTRADAY_SOURCE_REQUIRED",
            "coverage": "Existing cross-assets path fetches only a near-live Yahoo 2d/5m snapshot.",
            "decision_use_now": False,
            "ablation_ready": False,
            "reason": "Need revision-safe, timestamped historical observations for the same click window.",
        },
        "USDINR": {
            "status": "HISTORICAL_INTRADAY_SOURCE_REQUIRED",
            "coverage": "Existing cross-assets path is near-live only.",
            "decision_use_now": False,
            "ablation_ready": False,
            "reason": "Daily/reference FX data cannot stand in for click-time currency translation.",
        },
        "DXY": {
            "status": "HISTORICAL_INTRADAY_SOURCE_REQUIRED",
            "coverage": "Existing cross-assets path is near-live only.",
            "decision_use_now": False,
            "ablation_ready": False,
            "reason": "Need point-in-time intraday observations; daily closes are not intraday replay evidence.",
        },
        "EIA_CRUDE_INVENTORY": {
            "status": "PARTIAL_PIT_ACTUALS_ONLY",
            "coverage": eia,
            "decision_use_now": False,
            "ablation_ready": False,
            "reason": "Official release actuals are timestamped, but reconstructed historical consensus is explicitly not PIT-safe, so surprise direction cannot be used.",
        },
        "OPEC_SUPPLY": {
            "status": "PIT_RELEASE_TIMING_AUDIT_REQUIRED",
            "coverage": "Official OPEC/OPEC+ decisions are sourceable, but full intraday first-availability timestamps are not yet certified for the replay window.",
            "decision_use_now": False,
            "ablation_ready": False,
            "reason": "Day-level knowledge is not sufficient to inject an event into an intraday click replay.",
        },
        "CRUDE_NEWS": {
            "status": "DISABLED_UNTIL_NO_NEWS_FREEZE",
            "coverage": "Small audited Reuters/EIA seed ledger exists but is intentionally non-exhaustive.",
            "decision_use_now": False,
            "ablation_ready": False,
            "reason": "News is a later same-click experiment after the no-news Crude Brain is frozen.",
        },
        "MCX_CRUDEOILM_OPTION": {
            "status": "FORWARD_COLLECTION_ONLY",
            "coverage": "Live Mini option quotes/chains are usable prospectively; historical Mini premium candles are not certified for this replay.",
            "decision_use_now": False,
            "ablation_ready": False,
            "reason": "Option Brain translation is separate from the underlying Market Brain and synthetic premiums are forbidden.",
        },
    }
    ready_optional = [
        key for key, value in feeds.items()
        if key != "MCX_CRUDEOILM" and value.get("ablation_ready")
    ]
    return {
        "mode": "CRUDE_OIL_PIT_CONTEXT_SOURCE_INVENTORY_V1",
        "research_only": True,
        "decision_rules_changed": False,
        "knowledge_context_map": crude_domain_context_map_v1(),
        "acquisition_manifest": acquisition_manifest(),
        "feeds": feeds,
        "optional_context_ablation_ready": ready_optional,
        "full_window_context_ablation_ready": bool(ready_optional),
        "policy": {
            "domain_knowledge_is_not_historical_observation": True,
            "current_quote_is_not_historical_pit_data": True,
            "partial_event_coverage_is_not_full_window_coverage": True,
            "missing_context_stays_unknown": True,
            "no_news_until_no_news_brain_freeze": True,
        },
        "next_step": (
            "Acquire and certify at least one genuinely point-in-time Crude context stream over the frozen "
            "June-August click window before running a context ablation. WTI is the first-priority candidate "
            "because MCX identifies WTI as the global benchmark for its Crude Oil Mini complex."
        ),
    }
