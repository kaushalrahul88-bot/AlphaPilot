from __future__ import annotations

from collections import Counter
from typing import Iterable

CRUDE_EVENT_TYPES = {
    "EIA_CRUDE_INVENTORY",
    "EIA_GASOLINE_INVENTORY",
    "EIA_DISTILLATE_INVENTORY",
    "EIA_US_PRODUCTION",
    "EIA_SPR",
    "OPEC_POLICY",
    "PRODUCER_OUTAGE",
    "HORMUZ_SHIPPING_DISRUPTION",
    "SANCTIONS_EXPORT_POLICY",
    "REFINERY_OUTAGE",
    "CHINA_DEMAND_REFINING",
    "IEA_DEMAND_SUPPLY_REVISION",
    "WAR_ESCALATION",
    "CEASEFIRE_DIPLOMACY",
}

PRICE_RECAP = (
    "oil prices rise", "oil prices rose", "oil prices fall", "oil prices fell",
    "oil settles up", "oil settles down", "brent climbs", "brent falls",
    "wti rises", "wti falls", "crude jumps", "crude drops",
)
HORMUZ = ("hormuz", "strait of hormuz")
DISRUPTION = (
    "closed", "closure", "shut", "blockade", "mine", "mines", "attack",
    "attacked", "strike", "struck", "projectile", "transits fell",
    "transits drop", "traffic fell", "traffic drops", "shipping disruption",
)
RESTORATION_CONFIRMED = (
    "reopened", "reopens", "shipping resumed", "transits resumed",
    "corridor opened", "corridor is open", "blockade lifted", "mines cleared",
)
DIPLOMACY_ONLY = (
    "talks", "negotiations", "discuss", "discussions", "hope", "hopes",
    "proposal", "proposed", "may reopen", "could reopen",
)
SUPPLY_CUT = (
    "production cut", "output cut", "supply cut", "shut-in", "shut in",
    "production halted", "output halted", "offline", "outage",
)
SUPPLY_INCREASE = (
    "production increase", "output increase", "supply increase", "raises output",
    "boosts output", "restores output", "production resumes", "output resumes",
)
SANCTIONS_TIGHTER = (
    "new sanctions", "tightens sanctions", "secondary sanctions", "oil sanctions",
    "shipping sanctions", "shadow fleet sanctions",
)
SANCTIONS_EASIER = (
    "sanctions lifted", "lifts sanctions", "sanctions eased", "waiver granted",
    "waivers granted",
)
WAR_ESCALATION = (
    "military attacks", "missile attack", "missile strike", "air strike",
    "airstrike", "retaliation", "offensive posture", "escalation",
)
CEASEFIRE_CONFIRMED = (
    "ceasefire agreed", "ceasefire signed", "ceasefire takes effect",
    "permanent ceasefire", "hostilities ended",
)
DEMAND_BULLISH = (
    "crude imports rise", "oil demand rises", "demand forecast raised",
    "refinery runs rise", "refining throughput rises",
)
DEMAND_BEARISH = (
    "crude imports fall", "oil demand falls", "demand forecast cut",
    "refinery runs fall", "refining throughput falls",
)


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _base(record: dict) -> dict:
    value = record.get("value") or {}
    return {
        "event_id": record.get("event_id"),
        "underlying_event_id": record.get("underlying_event_id") or record.get("event_id"),
        "headline": str(value.get("headline") or record.get("headline") or "").strip(),
        "available_at": record.get("available_at") or record.get("first_detected_at"),
        "source": record.get("source") or record.get("source_name"),
        "event_type": str(record.get("event_type") or value.get("event_type") or "").upper(),
        "transmission_mechanism": "NONE",
        "materiality": "UNKNOWN",
        "novelty": str(record.get("novelty") or "NEW").upper(),
        "effect": "UNKNOWN",
        "confidence": 0.0,
        "disposition": "BLOCK",
        "reasons": [],
        "structured_surprise": None,
    }


def _structured_inventory(record: dict, result: dict) -> bool:
    if result["event_type"] not in {
        "EIA_CRUDE_INVENTORY", "EIA_GASOLINE_INVENTORY", "EIA_DISTILLATE_INVENTORY"
    }:
        return False
    value = record.get("value") or {}
    actual = value.get("actual_value", record.get("actual_value"))
    expected = value.get("expected_value", record.get("expected_value"))
    expectation_pit_safe = bool(
        value.get("expected_pit_safe", record.get("expected_pit_safe", False))
    )
    result["transmission_mechanism"] = "INVENTORY"
    result["materiality"] = "HIGH" if result["event_type"] == "EIA_CRUDE_INVENTORY" else "MEDIUM"
    if actual is None:
        result["reasons"].append("MISSING_ACTUAL_VALUE")
        return True
    if expected is None or not expectation_pit_safe:
        result.update(disposition="CONTEXT_ONLY", confidence=0.99)
        result["reasons"].append("EXPECTATION_NOT_PROVEN_AVAILABLE_BEFORE_RELEASE")
        return True
    try:
        surprise = float(actual) - float(expected)
    except (TypeError, ValueError):
        result["reasons"].append("INVALID_STRUCTURED_VALUES")
        return True
    result["structured_surprise"] = surprise
    if abs(surprise) < 1e-12:
        result.update(disposition="CONTEXT_ONLY", confidence=0.99)
        result["reasons"].append("NO_INVENTORY_SURPRISE")
        return True
    # A larger-than-expected stock build is a bearish physical-demand/supply prior;
    # a larger-than-expected draw is bullish. The market reaction is still tested,
    # never assumed.
    result["effect"] = "BEARISH" if surprise > 0 else "BULLISH"
    result.update(disposition="ALLOW", confidence=0.98)
    result["reasons"].append("PIT_SAFE_ACTUAL_VS_EXPECTED_INVENTORY_SURPRISE")
    return True


def assess_crude_news(record: dict) -> dict:
    result = _base(record)
    headline = result["headline"]
    low = headline.lower()
    if not result["available_at"]:
        result["reasons"].append("MISSING_AVAILABLE_AT")
        return result
    if _structured_inventory(record, result):
        return result
    if not headline:
        result["reasons"].append("MISSING_HEADLINE")
        return result
    if _contains_any(low, PRICE_RECAP):
        result.update(
            transmission_mechanism="PRICE_RECAP", materiality="CONTEXT",
            disposition="CONTEXT_ONLY", confidence=0.99,
        )
        result["reasons"].append("OBSERVED_PRICE_MOVE_IS_NOT_INDEPENDENT_CAUSAL_NEWS")
        return result

    if _contains_any(low, HORMUZ):
        result["event_type"] = result["event_type"] or "HORMUZ_SHIPPING_DISRUPTION"
        result["transmission_mechanism"] = "SUPPLY_ROUTE"
        result["materiality"] = "HIGH"
        if _contains_any(low, RESTORATION_CONFIRMED):
            result.update(effect="BEARISH", disposition="ALLOW", confidence=0.94)
            result["reasons"].append("CONFIRMED_RESTORATION_OF_CRITICAL_SUPPLY_ROUTE")
            return result
        if _contains_any(low, DISRUPTION):
            result.update(effect="BULLISH", disposition="ALLOW", confidence=0.94)
            result["reasons"].append("CONFIRMED_OR_REPORTED_CRITICAL_SUPPLY_ROUTE_DISRUPTION")
            return result
        if _contains_any(low, DIPLOMACY_ONLY):
            result.update(disposition="CONTEXT_ONLY", confidence=0.92)
            result["reasons"].append("DIPLOMACY_WITHOUT_CONFIRMED_FLOW_CHANGE")
            return result

    if _contains_any(low, CEASEFIRE_CONFIRMED):
        result.update(
            event_type=result["event_type"] or "CEASEFIRE_DIPLOMACY",
            transmission_mechanism="GEOPOLITICAL_SUPPLY_RISK", materiality="HIGH",
            effect="BEARISH", disposition="ALLOW", confidence=0.90,
        )
        result["reasons"].append("CONFIRMED_DEESCALATION_REDUCES_SUPPLY_RISK_PREMIUM")
        return result
    if _contains_any(low, WAR_ESCALATION):
        result.update(
            event_type=result["event_type"] or "WAR_ESCALATION",
            transmission_mechanism="GEOPOLITICAL_SUPPLY_RISK", materiality="HIGH",
            effect="BULLISH", disposition="ALLOW", confidence=0.88,
        )
        result["reasons"].append("ESCALATION_HAS_DIRECT_ENERGY_SUPPLY_RISK_CHANNEL")
        return result
    if _contains_any(low, SANCTIONS_EASIER):
        result.update(
            event_type=result["event_type"] or "SANCTIONS_EXPORT_POLICY",
            transmission_mechanism="EXPORT_AVAILABILITY", materiality="HIGH",
            effect="BEARISH", disposition="ALLOW", confidence=0.90,
        )
        result["reasons"].append("SANCTIONS_EASING_CAN_INCREASE_EXPORT_AVAILABILITY")
        return result
    if _contains_any(low, SANCTIONS_TIGHTER):
        result.update(
            event_type=result["event_type"] or "SANCTIONS_EXPORT_POLICY",
            transmission_mechanism="EXPORT_AVAILABILITY", materiality="HIGH",
            effect="BULLISH", disposition="ALLOW", confidence=0.88,
        )
        result["reasons"].append("SANCTIONS_TIGHTENING_CAN_REDUCE_EXPORT_AVAILABILITY")
        return result
    if _contains_any(low, SUPPLY_CUT):
        result.update(
            event_type=result["event_type"] or "PRODUCER_OUTAGE",
            transmission_mechanism="SUPPLY", materiality="HIGH",
            effect="BULLISH", disposition="ALLOW", confidence=0.88,
        )
        result["reasons"].append("EXPLICIT_PHYSICAL_SUPPLY_REDUCTION")
        return result
    if _contains_any(low, SUPPLY_INCREASE):
        result.update(
            event_type=result["event_type"] or "OPEC_POLICY",
            transmission_mechanism="SUPPLY", materiality="HIGH",
            effect="BEARISH", disposition="ALLOW", confidence=0.88,
        )
        result["reasons"].append("EXPLICIT_PHYSICAL_SUPPLY_INCREASE")
        return result
    if _contains_any(low, DEMAND_BULLISH):
        result.update(
            event_type=result["event_type"] or "CHINA_DEMAND_REFINING",
            transmission_mechanism="DEMAND", materiality="MEDIUM",
            effect="BULLISH", disposition="ALLOW", confidence=0.82,
        )
        result["reasons"].append("EXPLICIT_CRUDE_DEMAND_STRENGTH")
        return result
    if _contains_any(low, DEMAND_BEARISH):
        result.update(
            event_type=result["event_type"] or "CHINA_DEMAND_REFINING",
            transmission_mechanism="DEMAND", materiality="MEDIUM",
            effect="BEARISH", disposition="ALLOW", confidence=0.82,
        )
        result["reasons"].append("EXPLICIT_CRUDE_DEMAND_WEAKNESS")
        return result

    if result["event_type"] == "REFINERY_OUTAGE" or "refinery outage" in low:
        result.update(
            transmission_mechanism="REFINING", materiality="MEDIUM",
            disposition="CONTEXT_ONLY", confidence=0.90,
        )
        result["reasons"].append("REFINERY_OUTAGE_HAS_AMBIGUOUS_CRUDE_VS_PRODUCT_EFFECT")
        return result

    result.update(disposition="CONTEXT_ONLY", confidence=0.75)
    result["reasons"].append("RELEVANT_BUT_DIRECTIONAL_EFFECT_NOT_DEFENSIBLE_EX_ANTE")
    return result


def apply_crude_news_intelligence(records: list[dict]) -> dict:
    enriched = []
    seen = set()
    for record in sorted(records or [], key=lambda r: str(r.get("available_at") or r.get("first_detected_at") or "")):
        assessment = assess_crude_news(record)
        event_key = assessment.get("underlying_event_id")
        duplicate = bool(event_key and event_key in seen and not record.get("material_update"))
        if event_key:
            seen.add(event_key)
        if duplicate:
            assessment = {
                **assessment,
                "effect": "UNKNOWN",
                "disposition": "BLOCK",
                "confidence": 0.99,
                "reasons": [*assessment.get("reasons", []), "DUPLICATE_UNDERLYING_EVENT"],
            }
        enriched.append({**record, "news_intelligence": assessment})

    counts = Counter((row["news_intelligence"] or {}).get("disposition", "BLOCK") for row in enriched)
    return {
        "mode": "CRUDE_NEWS_INTELLIGENCE_V1",
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "allowed_records": [r for r in enriched if r["news_intelligence"]["disposition"] == "ALLOW"],
        "context_only_records": [r for r in enriched if r["news_intelligence"]["disposition"] == "CONTEXT_ONLY"],
        "blocked_records": [r for r in enriched if r["news_intelligence"]["disposition"] == "BLOCK"],
        "records": enriched,
        "counts": {key: int(counts.get(key, 0)) for key in ("ALLOW", "CONTEXT_ONLY", "BLOCK")},
        "policy": (
            "Only causal, point-in-time-defensible ALLOW events may contribute directional NEWS evidence. "
            "Price recaps, unconfirmed diplomacy, ambiguous refinery events, duplicate stories, and "
            "historical inventory expectations without pre-release provenance never vote."
        ),
    }
