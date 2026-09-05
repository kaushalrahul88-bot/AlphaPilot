"""Research-only Crypto Knowledge Brain foundation.

The module separates durable domain knowledge, historical market memory, and
live intelligence. Knowledge creates hypotheses and context; it never creates
an order. Every source class carries an explicit trust policy so social/media
signals cannot silently become hard trading facts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

SourceTier = Literal[
    "A_PRIMARY",
    "B_INSTITUTIONAL_RESEARCH",
    "C_PROFESSIONAL",
    "D_COMMUNITY",
    "E_UNVERIFIED",
]
MemoryClass = Literal["DURABLE_KNOWLEDGE", "HISTORICAL_MEMORY", "LIVE_INTELLIGENCE"]
EvidenceStatus = Literal["ESTABLISHED_CONTEXT", "EMPIRICAL_HYPOTHESIS", "DISCOVERY_ONLY"]

DEFAULT_CRYPTO_PLATFORM = "COINDCX"

CRYPTO_KNOWLEDGE_DOMAINS = (
    "blockchain_fundamentals",
    "bitcoin_network",
    "ethereum_network",
    "solana_network",
    "tokenomics_and_supply",
    "cex_market_structure",
    "dex_market_structure",
    "spot_microstructure",
    "perpetual_futures",
    "dated_futures",
    "options_and_volatility",
    "liquidations_and_leverage",
    "on_chain_flows",
    "holder_cohorts",
    "miners_and_validators",
    "stablecoins_and_liquidity",
    "defi_lending_and_liquidations",
    "bridges_and_cross_chain_flows",
    "etfs_and_institutional_flows",
    "macro_and_cross_asset",
    "regulation_and_tax",
    "security_hacks_and_exploits",
    "protocol_upgrades_and_forks",
    "social_sentiment_and_narratives",
    "behavioural_finance",
    "historical_market_events",
    "quantitative_research",
    "risk_and_execution",
    "exchange_specific_coindcx",
)


@dataclass(frozen=True)
class SourcePolicy:
    tier: SourceTier
    authority_weight: float
    requires_corroboration: bool
    can_create_direction_vote: bool
    description: str


SOURCE_POLICIES: dict[SourceTier, SourcePolicy] = {
    "A_PRIMARY": SourcePolicy(
        "A_PRIMARY", 1.00, False, True,
        "Protocol/exchange/regulator filings, raw blockchain data, and original documents.",
    ),
    "B_INSTITUTIONAL_RESEARCH": SourcePolicy(
        "B_INSTITUTIONAL_RESEARCH", 0.90, False, True,
        "Peer-reviewed, academic, institutional, and transparent methodology research.",
    ),
    "C_PROFESSIONAL": SourcePolicy(
        "C_PROFESSIONAL", 0.75, True, False,
        "Professional research, specialist analysts, books, magazines, and established journalism.",
    ),
    "D_COMMUNITY": SourcePolicy(
        "D_COMMUNITY", 0.45, True, False,
        "X, Reddit, YouTube, Telegram, Discord, forums, podcasts, and practitioner commentary.",
    ),
    "E_UNVERIFIED": SourcePolicy(
        "E_UNVERIFIED", 0.20, True, False,
        "Anonymous claims, screenshots, forwarded messages, and unattributed rumours.",
    ),
}


@dataclass(frozen=True)
class KnowledgeItem:
    id: str
    domain: str
    claim: str
    mechanism: str
    horizon: str
    source_name: str
    source_ref: str
    source_tier: SourceTier
    memory_class: MemoryClass
    status: EvidenceStatus
    hypothesis_hook: str
    instrument_neutral: bool = True
    production_rule: bool = False


SEED_KNOWLEDGE_V1 = (
    KnowledgeItem(
        "CRYPTO_PERP_FUNDING_CONTEXT", "perpetual_futures",
        "Perpetual funding is positioning/carry context, not a standalone direction signal.",
        "Funding helps align perpetual and spot markets while reflecting leveraged demand imbalance.",
        "minutes_to_days", "Derivatives market mechanics", "concept:perpetual-funding",
        "A_PRIMARY", "DURABLE_KNOWLEDGE", "ESTABLISHED_CONTEXT",
        "Test funding level, change, duration and percentile jointly with price/OI/liquidations.",
    ),
    KnowledgeItem(
        "CRYPTO_OI_CONTEXT", "perpetual_futures",
        "Open interest measures outstanding derivatives exposure and must be interpreted with price and volume.",
        "Rising/falling OI can represent leverage build-up, position closure, or liquidation depending on context.",
        "minutes_to_days", "Derivatives market mechanics", "concept:open-interest",
        "A_PRIMARY", "DURABLE_KNOWLEDGE", "ESTABLISHED_CONTEXT",
        "Learn price+OI+funding+liquidation state transitions instead of hard-coded OI rules.",
    ),
    KnowledgeItem(
        "CRYPTO_ONCHAIN_TRANSFER_NOT_TRADE", "on_chain_flows",
        "A blockchain transfer is not equivalent to a buy or sell.",
        "Transfers can be custody reshuffles, exchange internals, collateral, OTC settlement, bridges, or trading preparation.",
        "seconds_to_days", "Public blockchain mechanics", "concept:onchain-transfer",
        "A_PRIMARY", "DURABLE_KNOWLEDGE", "ESTABLISHED_CONTEXT",
        "Require entity classification, destination, history and market confirmation before directional use.",
    ),
    KnowledgeItem(
        "CRYPTO_STABLECOIN_LIQUIDITY", "stablecoins_and_liquidity",
        "Stablecoin flows can represent deployable crypto-market liquidity but derivatives deposits may be collateral for either side.",
        "Stablecoin location and venue type change the interpretation of the same nominal flow.",
        "minutes_to_weeks", "Market structure research", "concept:stablecoin-liquidity",
        "B_INSTITUTIONAL_RESEARCH", "DURABLE_KNOWLEDGE", "EMPIRICAL_HYPOTHESIS",
        "Separate spot-exchange buying power from derivatives collateral and test both historically.",
    ),
    KnowledgeItem(
        "CRYPTO_OPTIONS_TRANSLATION", "options_and_volatility",
        "Correct underlying direction does not guarantee a profitable long option.",
        "IV, Greeks, strike, expiry, liquidity and path determine option-premium translation.",
        "minutes_to_months", "Options market mechanics", "concept:option-translation",
        "A_PRIMARY", "DURABLE_KNOWLEDGE", "ESTABLISHED_CONTEXT",
        "Evaluate option economics independently after the shared underlying market state is formed.",
    ),
    KnowledgeItem(
        "CRYPTO_SOCIAL_TRUTH_VS_IMPACT", "social_sentiment_and_narratives",
        "Truth probability and immediate market-impact probability are distinct.",
        "A low-confidence rumour can still move a reflexive leveraged market before verification.",
        "seconds_to_hours", "Market microstructure hypothesis", "concept:rumour-impact",
        "C_PROFESSIONAL", "DURABLE_KNOWLEDGE", "EMPIRICAL_HYPOTHESIS",
        "Track claim verification and market reaction separately; community claims require corroboration.",
    ),
)


def source_policy(tier: SourceTier) -> SourcePolicy:
    return SOURCE_POLICIES[tier]


def source_quality_score(
    tier: SourceTier,
    *,
    recency_score: float = 1.0,
    evidence_score: float = 1.0,
    historical_reliability: float = 1.0,
) -> float:
    """Return a bounded quality score without converting knowledge into a trade rule."""
    policy = source_policy(tier)
    components = (recency_score, evidence_score, historical_reliability)
    bounded = [max(0.0, min(1.0, float(value))) for value in components]
    score = policy.authority_weight * (0.30 * bounded[0] + 0.40 * bounded[1] + 0.30 * bounded[2])
    return round(max(0.0, min(1.0, score)), 4)


def crypto_knowledge_pack_v1() -> dict:
    return {
        "version": "CRYPTO_KNOWLEDGE_BRAIN_V1",
        "default_platform": DEFAULT_CRYPTO_PLATFORM,
        "research_only": True,
        "live_collection_enabled": False,
        "broker_execution_enabled": False,
        "knowledge_domains": list(CRYPTO_KNOWLEDGE_DOMAINS),
        "memory_classes": {
            "DURABLE_KNOWLEDGE": "Slow-changing mechanics, concepts, literature and validated principles.",
            "HISTORICAL_MEMORY": "Timestamped past market states, events, outcomes and failed signals.",
            "LIVE_INTELLIGENCE": "Fast-decaying observations that must carry observed/first-seen timestamps.",
        },
        "source_policy": {tier: asdict(policy) for tier, policy in SOURCE_POLICIES.items()},
        "principles": {
            "knowledge_is_not_trading_rule": True,
            "social_media_requires_corroboration": True,
            "truth_probability_separate_from_market_impact": True,
            "failed_signals_are_learning_material": True,
            "time_sensitive_information_never_stored_as_undated_knowledge": True,
            "options_and_futures_share_market_context_not_trade_generation": True,
        },
        "seed_items": [asdict(item) for item in SEED_KNOWLEDGE_V1],
    }
