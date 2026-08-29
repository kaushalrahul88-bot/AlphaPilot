"""Research-only Copper domain knowledge and news/event schema.

Knowledge creates priors/hypotheses; it never creates production orders.
Every item carries provenance so Market Brain can distinguish established
mechanics from hypotheses that still require AlphaPilot evidence.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Literal

SourceTier = Literal["A_PRIMARY", "B_RESEARCH", "C_PROFESSIONAL", "D_PRACTITIONER", "E_DISCOVERY"]
KnowledgeStatus = Literal["ESTABLISHED_CONTEXT", "HYPOTHESIS_ONLY"]


@dataclass(frozen=True)
class KnowledgeItem:
    id: str
    commodity: str
    family: str
    claim: str
    mechanism: str
    expected_effect: str
    conditions: tuple[str, ...]
    exceptions: tuple[str, ...]
    horizon: str
    source_name: str
    source_url: str
    source_tier: SourceTier
    status: KnowledgeStatus
    option_implication: str
    hypothesis_hook: str
    production_rule: bool = False


COPPER_KNOWLEDGE_V1 = (
    KnowledgeItem(
        "CU_MCX_GLOBAL_FX", "COPPER", "cross_market",
        "Indian copper prices reflect international copper and USD/INR.",
        "MCX copper is INR-denominated while the global benchmark is foreign-currency priced.",
        "Global copper and USD/INR can reinforce or offset one another.",
        ("international copper move is observable", "USD/INR is contemporaneously observable"),
        ("basis/local positioning can cause short-term divergence",), "intraday_to_multiday",
        "Multi Commodity Exchange of India", "https://www.mcxindia.com/products/metals/copper",
        "A_PRIMARY", "ESTABLISHED_CONTEXT",
        "Direction alone is insufficient; estimate MCX magnitude and volatility before selecting CE/PE.",
        "Test whether global-copper direction plus USD/INR alignment improves MCX next-horizon outcomes.",
    ),
    KnowledgeItem(
        "CU_SUPPLY_DISRUPTION", "COPPER", "supply",
        "Unexpected mine or plant closures and strikes can affect copper prices.",
        "Disruption can reduce expected refined or mined supply relative to demand.",
        "Supply-negative surprises are a conditional bullish prior, not an automatic trade.",
        ("event is new", "materiality is credible"), ("already priced", "small/temporary disruption", "demand shock dominates"),
        "hours_to_weeks", "Multi Commodity Exchange of India",
        "https://www.mcxindia.com/products/metals/copper", "A_PRIMARY", "ESTABLISHED_CONTEXT",
        "A genuine surprise may expand realized/option volatility; Option Brain must still assess IV and liquidity.",
        "Compare post-event direction, MFE/MAE and volatility with matched non-event regimes.",
    ),
    KnowledgeItem(
        "CU_TRADE_POLICY", "COPPER", "policy",
        "Taxes, penalties, quotas and other trade-policy changes can alter copper material flows.",
        "Policy can restrict or encourage supply flows and change regional pricing/basis.",
        "Impact is conditional on scope, geography, timing and prior expectations.",
        ("policy change is confirmed",), ("headline lacks implementation detail", "market anticipated change"),
        "hours_to_months", "Multi Commodity Exchange of India",
        "https://www.mcxindia.com/products/metals/copper", "A_PRIMARY", "ESTABLISHED_CONTEXT",
        "Treat policy surprises as potential direction-and-volatility events rather than automatic option entries.",
        "Classify policy surprise and test reaction conditional on global copper and USD/INR confirmation.",
    ),
    KnowledgeItem(
        "CU_COT_CONTEXT", "COPPER", "positioning",
        "CFTC disaggregated COT separates Producer/Merchant/Processor/User, Swap Dealers, Managed Money and Other Reportables.",
        "Weekly positioning describes participation/crowding context but does not reveal each trader's motive.",
        "Positioning extremes or changes are context features; direction must be empirically learned.",
        ("report is available before decision timestamp",), ("weekly/stale for intraday timing", "position motive unknown"),
        "days_to_weeks", "U.S. Commodity Futures Trading Commission",
        "https://www.cftc.gov/MarketReports/CommitmentsofTraders/DisaggregatedExplanatoryNotes/index.htm",
        "A_PRIMARY", "ESTABLISHED_CONTEXT",
        "Use as background regime/crowding context, never as a standalone CE/PE trigger.",
        "Test whether positioning level/change modifies behaviour-family expectancy.",
    ),
)


@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    commodity: str
    event_type: str
    published_at: str
    first_detected_at: str
    source_name: str
    source_url: str
    source_tier: SourceTier
    headline: str
    scheduled: bool
    expected_value: float | None = None
    actual_value: float | None = None
    previous_value: float | None = None
    unit: str | None = None
    novelty: Literal["NEW", "UPDATE", "DUPLICATE", "STALE"] = "NEW"
    fundamental_prior: Literal["BULLISH", "BEARISH", "NEUTRAL", "MIXED", "UNKNOWN"] = "UNKNOWN"
    materiality: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"] = "UNKNOWN"
    confirmed: bool = False


def surprise(event: MarketEvent) -> float | None:
    if event.expected_value is None or event.actual_value is None:
        return None
    return event.actual_value - event.expected_value


def event_is_replay_eligible(event: MarketEvent, decision_at: str) -> bool:
    """Hard look-ahead guard: only information first detected by decision time exists."""
    return bool(event.first_detected_at and event.first_detected_at <= decision_at)


def copper_knowledge_pack_v1() -> dict:
    items=[asdict(x) for x in COPPER_KNOWLEDGE_V1]
    return {
        "version": "COPPER_DOMAIN_KNOWLEDGE_V1",
        "research_only": True,
        "production_rules_changed": False,
        "principle": "Knowledge supplies priors and hypothesis hooks; AlphaPilot evidence decides whether an edge is active.",
        "source_policy": {
            "A_PRIMARY": "authoritative context/data",
            "B_RESEARCH": "research-supported hypothesis/context",
            "C_PROFESSIONAL": "professional literature; validate empirically",
            "D_PRACTITIONER": "practitioner hypothesis; validate independently",
            "E_DISCOVERY": "discovery only; never decision evidence without corroboration",
        },
        "items": items,
        "news_event_rules": {
            "headline_is_not_trade_signal": True,
            "deduplicate_same_underlying_event": True,
            "use_first_detected_at_for_historical_replay": True,
            "scheduled_events_compare_actual_vs_expected": True,
            "states": ["BREAKING", "ACTIVE", "ABSORBED", "STALE"],
            "required_outcomes": ["underlying_direction", "MFE", "MAE", "volatility_response", "option_premium_response"],
        },
        "option_objective": "Translate validated underlying direction+magnitude+horizon+volatility into CE/PE/strike/expiry only when option economics are suitable.",
    }
