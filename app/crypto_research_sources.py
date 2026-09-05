"""Research-source inventory and ingestion policy for the Crypto Knowledge Brain.

The system stores source metadata and extracted knowledge claims/provenance. It
does not assume permission to persist complete copyrighted works. Community
sources are discovery/context until corroborated and empirically validated.
News is a first-class live-intelligence family rather than being hidden inside
magazine/research coverage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Medium = Literal[
    "BOOK",
    "ACADEMIC_PAPER",
    "WHITEPAPER",
    "OFFICIAL_DOCUMENTATION",
    "EXCHANGE_DOCUMENTATION",
    "REGULATORY_DOCUMENT",
    "INSTITUTIONAL_RESEARCH",
    "NEWS",
    "NEWS_MAGAZINE",
    "SOCIAL_X",
    "REDDIT_FORUM",
    "VIDEO_PODCAST",
    "TELEGRAM_DISCORD",
    "ONCHAIN_DATA",
    "MARKET_DATA",
]
IngestionMode = Literal["METADATA_ONLY", "CLAIM_EXTRACTION", "STRUCTURED_DATA"]


@dataclass(frozen=True)
class SourceClass:
    id: str
    medium: Medium
    default_tier: str
    ingestion_mode: IngestionMode
    full_text_persistence_default: bool
    requires_corroboration: bool
    timestamp_required: bool
    historical_reliability_tracking: bool
    notes: str


SOURCE_CLASSES = (
    SourceClass("BOOK_LIBRARY", "BOOK", "C_PROFESSIONAL", "CLAIM_EXTRACTION", False, True, False, True,
                "Extract concepts, mechanisms, hypotheses and references; do not treat publication as live evidence."),
    SourceClass("ACADEMIC_PAPERS", "ACADEMIC_PAPER", "B_INSTITUTIONAL_RESEARCH", "CLAIM_EXTRACTION", False, False, True, True,
                "Retain methodology, dataset period and limitations so stale findings are not silently generalized."),
    SourceClass("PROTOCOL_WHITEPAPERS", "WHITEPAPER", "A_PRIMARY", "CLAIM_EXTRACTION", False, False, True, False,
                "Primary mechanics/specification evidence; distinguish design claims from observed market behaviour."),
    SourceClass("PROTOCOL_DOCS", "OFFICIAL_DOCUMENTATION", "A_PRIMARY", "CLAIM_EXTRACTION", False, False, True, False,
                "Version and timestamp documentation because protocol behaviour can change."),
    SourceClass("OFFICIAL_ANNOUNCEMENTS", "OFFICIAL_DOCUMENTATION", "A_PRIMARY", "CLAIM_EXTRACTION", False, False, True, True,
                "Issuer, protocol, exchange, regulator or company announcements are primary event evidence; retain exact publication and first-seen times."),
    SourceClass("EXCHANGE_DOCS", "EXCHANGE_DOCUMENTATION", "A_PRIMARY", "CLAIM_EXTRACTION", False, False, True, False,
                "Contract, fee, settlement, margin and API semantics; platform-specific and versioned."),
    SourceClass("REGULATORY_PRIMARY", "REGULATORY_DOCUMENT", "A_PRIMARY", "CLAIM_EXTRACTION", False, False, True, False,
                "Primary legal/regulatory text; jurisdiction and effective date required."),
    SourceClass("INSTITUTIONAL_RESEARCH", "INSTITUTIONAL_RESEARCH", "B_INSTITUTIONAL_RESEARCH", "CLAIM_EXTRACTION", False, False, True, True,
                "Research context/hypotheses; retain methodology and historical period."),
    SourceClass("BREAKING_NEWS", "NEWS", "C_PROFESSIONAL", "CLAIM_EXTRACTION", False, True, True, True,
                "Time-sensitive reporting. Preserve publication/first-seen timestamps, distinguish confirmed facts from claims, and prioritize primary corroboration."),
    SourceClass("FINANCIAL_NEWS", "NEWS", "C_PROFESSIONAL", "CLAIM_EXTRACTION", False, True, True, True,
                "Mainstream financial reporting and market-moving macro/company coverage; separate original reporting from opinion/commentary."),
    SourceClass("CRYPTO_NATIVE_NEWS", "NEWS", "C_PROFESSIONAL", "CLAIM_EXTRACTION", False, True, True, True,
                "Crypto-specialist reporting can surface exchange, protocol, regulatory and ecosystem developments quickly; material claims require provenance/corroboration."),
    SourceClass("NEWS_AND_MAGAZINES", "NEWS_MAGAZINE", "C_PROFESSIONAL", "CLAIM_EXTRACTION", False, True, True, True,
                "Long-form reporting, interviews and magazines; separate original reporting from commentary and verify material claims against primary evidence."),
    SourceClass("X_SOCIAL", "SOCIAL_X", "D_COMMUNITY", "CLAIM_EXTRACTION", False, True, True, True,
                "Track truth confidence and market-impact confidence separately; never standalone hard evidence by default."),
    SourceClass("REDDIT_AND_FORUMS", "REDDIT_FORUM", "D_COMMUNITY", "CLAIM_EXTRACTION", False, True, True, True,
                "Useful for narrative/discovery and practitioner experience; corroboration required."),
    SourceClass("VIDEO_AND_PODCASTS", "VIDEO_PODCAST", "D_COMMUNITY", "CLAIM_EXTRACTION", False, True, True, True,
                "Extract attributed claims with publication time; distinguish education from predictions."),
    SourceClass("TELEGRAM_AND_DISCORD", "TELEGRAM_DISCORD", "D_COMMUNITY", "CLAIM_EXTRACTION", False, True, True, True,
                "High-noise discovery channel; provenance and first-seen timestamp mandatory."),
    SourceClass("ONCHAIN_PROVIDERS", "ONCHAIN_DATA", "B_INSTITUTIONAL_RESEARCH", "STRUCTURED_DATA", False, True, True, True,
                "Provider/entity-label methodology must be retained; cross-provider disagreement is uncertainty, not an error to hide."),
    SourceClass("GLOBAL_MARKET_DATA", "MARKET_DATA", "A_PRIMARY", "STRUCTURED_DATA", False, False, True, True,
                "Spot/derivatives/options observations require venue, symbol, timestamp and contract identity."),
)


def source_inventory_v1() -> dict:
    return {
        "version": "CRYPTO_RESEARCH_SOURCE_INVENTORY_V1",
        "coverage_goal": "maximum_practical_breadth_with_ranked_evidence_quality",
        "full_text_persistence_requires_explicit_rights": True,
        "default_book_ingestion": "extract concepts/hypotheses/provenance rather than persisting full copyrighted text",
        "community_source_standalone_trade_signal": False,
        "news_is_first_class_live_intelligence": True,
        "news_truth_and_market_impact_scored_separately": True,
        "news_primary_corroboration_preferred": True,
        "first_seen_timestamp_required_for_live_information": True,
        "cross_source_corroboration_supported": True,
        "source_historical_reliability_tracking": True,
        "sources": [asdict(item) for item in SOURCE_CLASSES],
    }


def source_class(source_id: str) -> SourceClass:
    key = str(source_id or "").upper()
    for item in SOURCE_CLASSES:
        if item.id == key:
            return item
    raise KeyError(f"unknown crypto research source class: {source_id}")
