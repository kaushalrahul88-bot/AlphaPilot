"""BTC Crypto Brain source capability registry.

The registry separates data that can be reconstructed later from official/public
history from data that must be captured with first-seen timestamps while live.
It is intentionally conservative: an undocumented or merely current endpoint is
never promoted into a historical reconstruction capability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

HistoricalMode = Literal[
    "RECONSTRUCTIBLE_PUBLIC_HISTORY",
    "FIRST_SEEN_ARCHIVE_REQUIRED",
    "OFFICIAL_RELEASE_ARCHIVE",
    "EXTERNAL_PIT_ARCHIVE_REQUIRED",
    "UNCONFIRMED",
]


@dataclass(frozen=True)
class SourceCapability:
    lane: str
    dataset: str
    provider: str
    historical_mode: HistoricalMode
    point_in_time_requirement: str
    can_reconstruct_later: bool
    live_capture_priority: str
    decision_role: str
    documented_endpoint: str | None = None
    notes: str = ""

    def validated(self) -> "SourceCapability":
        if not self.lane or not self.dataset or not self.provider:
            raise ValueError("lane, dataset, and provider are required")
        if self.can_reconstruct_later and self.historical_mode != "RECONSTRUCTIBLE_PUBLIC_HISTORY":
            raise ValueError("only public-history datasets may be reconstructible later")
        if self.live_capture_priority not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            raise ValueError("unsupported live_capture_priority")
        if self.decision_role not in {"DIRECTIONAL_EVIDENCE", "CONTEXT_ONLY", "OPTIONS_TRANSLATION", "REPLAY_ONLY"}:
            raise ValueError("unsupported decision_role")
        return self


BTC_SOURCE_CAPABILITIES: tuple[SourceCapability, ...] = (
    SourceCapability(
        lane="SPOT_STRUCTURE", dataset="BTC_SPOT_OHLCV", provider="COINDCX",
        historical_mode="RECONSTRUCTIBLE_PUBLIC_HISTORY",
        point_in_time_requirement="Bar is visible only after interval completion; API time is bar-open time.",
        can_reconstruct_later=True, live_capture_priority="LOW", decision_role="DIRECTIONAL_EVIDENCE",
        documented_endpoint="GET https://api.coindcx.com/market_data/candles",
        notes="Reconstruct completed candles later; do not spend storage duplicating ordinary OHLCV unless needed operationally.",
    ),
    SourceCapability(
        lane="DERIVATIVES_POSITIONING", dataset="BTC_FUTURES_OHLCV", provider="COINDCX",
        historical_mode="RECONSTRUCTIBLE_PUBLIC_HISTORY",
        point_in_time_requirement="Bar is visible only after interval completion; futures candle time is open time.",
        can_reconstruct_later=True, live_capture_priority="LOW", decision_role="DIRECTIONAL_EVIDENCE",
        documented_endpoint="GET https://public.coindcx.com/market_data/candlesticks?...&pcode=f",
    ),
    SourceCapability(
        lane="DERIVATIVES_POSITIONING", dataset="BTC_FUTURES_FUNDING_MARK_SNAPSHOT", provider="COINDCX",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="Store provider timestamp plus AlphaPilot first_seen_at; current snapshot must never be backdated.",
        can_reconstruct_later=False, live_capture_priority="CRITICAL", decision_role="DIRECTIONAL_EVIDENCE",
        documented_endpoint="GET https://public.coindcx.com/market_data/v3/current_prices/futures/rt",
        notes="Current feed exposes fields including funding/mark price; this does not prove historical snapshot availability.",
    ),
    SourceCapability(
        lane="DERIVATIVES_POSITIONING", dataset="BTC_OPEN_INTEREST", provider="MULTI_PROVIDER",
        historical_mode="EXTERNAL_PIT_ARCHIVE_REQUIRED",
        point_in_time_requirement="Use source timestamp and first_seen_at from a provider with explicit historical/PIT semantics.",
        can_reconstruct_later=False, live_capture_priority="CRITICAL", decision_role="DIRECTIONAL_EVIDENCE",
        notes="Do not infer OI from volume or positions; provider capability must be verified before admission.",
    ),
    SourceCapability(
        lane="DERIVATIVES_POSITIONING", dataset="BTC_LIQUIDATIONS", provider="MULTI_PROVIDER",
        historical_mode="EXTERNAL_PIT_ARCHIVE_REQUIRED",
        point_in_time_requirement="Liquidation event timestamp and first_seen_at are both required.",
        can_reconstruct_later=False, live_capture_priority="CRITICAL", decision_role="DIRECTIONAL_EVIDENCE",
        notes="Never reconstruct liquidation cascades from later price candles alone.",
    ),
    SourceCapability(
        lane="OPTIONS_MARKET", dataset="BTC_GLOBAL_OPTIONS_CONTEXT", provider="DERIBIT",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="Archive each Deribit chain-context snapshot with AlphaPilot first_seen_at. Current chain summaries fetched later cannot be backdated into earlier clicks.",
        can_reconstruct_later=False, live_capture_priority="HIGH", decision_role="OPTIONS_TRANSLATION",
        documented_endpoint="GET https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option",
        notes="Global BTC options IV/OI/term-structure context only. Deribit contracts and quotes may not select, fill, or score a CoinDCX option trade.",
    ),
    SourceCapability(
        lane="OPTIONS_MARKET", dataset="BTC_GLOBAL_OPTIONS_GREEKS", provider="DERIBIT_TICKER",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="Archive observed option ticker Greeks and 25-delta pair only after AlphaPilot receives both contributing ticker states. Preserve provider timestamps and first_seen_at separately.",
        can_reconstruct_later=False, live_capture_priority="HIGH", decision_role="OPTIONS_TRANSLATION",
        documented_endpoint="WSS ticker.{instrument_name}.{interval}",
        notes="Global BTC option delta/gamma/theta/vega/rho and 25d skew context only. Delta must come from the documented ticker Greeks object; never infer delta from strike or use Deribit contracts as CoinDCX execution data.",
    ),
    SourceCapability(
        lane="OPTIONS_MARKET", dataset="COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES", provider="COINDCX",
        historical_mode="UNCONFIRMED",
        point_in_time_requirement="Require a verified historical archive or prospective first-seen capture before backtest admission.",
        can_reconstruct_later=False, live_capture_priority="CRITICAL", decision_role="OPTIONS_TRANSLATION",
        notes="Public documented historical Options API was not confirmed; never fabricate strikes, Greeks, IV, OI, or quotes.",
    ),
    SourceCapability(
        lane="OPTIONS_MARKET", dataset="BTC_OPTION_EXIT_QUOTES", provider="COINDCX_OR_VERIFIED_ARCHIVE",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="Exact contract symbol, bid/ask, source timestamp, and first_seen_at required.",
        can_reconstruct_later=False, live_capture_priority="CRITICAL", decision_role="REPLAY_ONLY",
        notes="Actual archived bid is required for realized shadow P&L; Greek model is not an exit fill.",
    ),
    SourceCapability(
        lane="NEWS", dataset="CRYPTO_NEWS_EVENTS", provider="MULTI_SOURCE",
        historical_mode="EXTERNAL_PIT_ARCHIVE_REQUIRED",
        point_in_time_requirement="published_at plus first_seen_at; use the later of relevant availability constraints.",
        can_reconstruct_later=False, live_capture_priority="HIGH", decision_role="DIRECTIONAL_EVIDENCE",
        notes="Article existence in a later archive does not prove AlphaPilot could see it at the historical click. Raw feed discovery is unverified context until enriched.",
    ),
    SourceCapability(
        lane="NEWS", dataset="CRYPTO_NEWS_ENRICHMENT", provider="ALPHAPILOT",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="Store analysis_first_seen_at separately from article first_seen/published time; verification learned later cannot be backdated.",
        can_reconstruct_later=False, live_capture_priority="HIGH", decision_role="DIRECTIONAL_EVIDENCE",
        notes="Versioned derived intelligence stores event grouping, source tier, verification, truth/impact confidence, materiality and direction without rewriting raw news capture.",
    ),
    SourceCapability(
        lane="SOCIAL_NARRATIVE", dataset="CRYPTO_SOCIAL_POSTS_AND_NARRATIVE_VELOCITY", provider="APPROVED_OR_LICENSED_SOURCE",
        historical_mode="EXTERNAL_PIT_ARCHIVE_REQUIRED",
        point_in_time_requirement="Post timestamp and AlphaPilot first_seen_at are required. Provider access, analysis-use and retention rights must be verified before archival; edit/deletion state is preserved when available.",
        can_reconstruct_later=False, live_capture_priority="HIGH", decision_role="CONTEXT_ONLY",
        notes="Public visibility is not permission to persist. Raw popularity, engagement or virality cannot become truth, market impact or standalone directional evidence.",
    ),
    SourceCapability(
        lane="SOCIAL_NARRATIVE", dataset="CRYPTO_SOCIAL_ENRICHMENT", provider="ALPHAPILOT",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="Derived event grouping, narrative velocity, source reliability and truth/impact confidence use a separate analysis_first_seen_at and cannot be backdated into raw social capture.",
        can_reconstruct_later=False, live_capture_priority="HIGH", decision_role="CONTEXT_ONLY",
        notes="Verified factual claims discovered socially must still pass Crypto News Intelligence before they may become directional event evidence.",
    ),
    SourceCapability(
        lane="ONCHAIN", dataset="BTC_ONCHAIN_ENTITY_AND_FLOW_METRICS", provider="MULTI_PROVIDER",
        historical_mode="EXTERNAL_PIT_ARCHIVE_REQUIRED",
        point_in_time_requirement="Use metric availability/publication timestamp, not merely block/event time.",
        can_reconstruct_later=False, live_capture_priority="HIGH", decision_role="DIRECTIONAL_EVIDENCE",
        notes="Provider entity labels can change retrospectively; freeze classification/version used at click time.",
    ),
    SourceCapability(
        lane="STABLECOIN_LIQUIDITY", dataset="STABLECOIN_SUPPLY_LIQUIDITY", provider="DEFILLAMA",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="Capture the aggregate USD-pegged supply snapshot with AlphaPilot first_seen_at; later API history is not proof of exact click-time availability.",
        can_reconstruct_later=False, live_capture_priority="HIGH", decision_role="CONTEXT_ONLY",
        documented_endpoint="GET https://stablecoins.llama.fi/stablecoins?includePrices=true",
        notes="Aggregate stablecoin supply measures broad liquidity capacity only. It must not be treated as exchange inflow, deployable venue buying power, or a standalone bullish/bearish signal.",
    ),
    SourceCapability(
        lane="STABLECOIN_LIQUIDITY", dataset="STABLECOIN_EXCHANGE_AND_CHAIN_FLOWS", provider="MULTI_PROVIDER",
        historical_mode="EXTERNAL_PIT_ARCHIVE_REQUIRED",
        point_in_time_requirement="Event/metric time plus first_seen/publication time and venue classification.",
        can_reconstruct_later=False, live_capture_priority="HIGH", decision_role="DIRECTIONAL_EVIDENCE",
        notes="Venue-specific stablecoin exchange/chain flows remain separate from aggregate stablecoin supply.",
    ),
    SourceCapability(
        lane="MACRO_CROSS_ASSET", dataset="FRED_MACRO_REGIME_VINTAGE_HISTORY", provider="FRED_ALFRED",
        historical_mode="RECONSTRUCTIBLE_PUBLIC_HISTORY",
        point_in_time_requirement="Use an ALFRED real-time/vintage date strictly before an intraday replay click's calendar date. Same-day historical reconstruction does not prove exact intraday availability.",
        can_reconstruct_later=True, live_capture_priority="LOW", decision_role="CONTEXT_ONLY",
        documented_endpoint="GET https://api.stlouisfed.org/fred/series/observations",
        notes="Reconstructible prior-day daily regime context only. FRED release dates do not by themselves prove exact intraday FRED/ALFRED availability and this lane cannot supply a directional BTC causal origin.",
    ),
    SourceCapability(
        lane="MACRO_CROSS_ASSET", dataset="FRED_MACRO_REGIME_LIVE_SNAPSHOT", provider="FRED_ALFRED",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="For same-day prospective use, archive the current FRED/ALFRED vintage with AlphaPilot first_seen_at. A snapshot fetched later must never be backdated into an earlier click.",
        can_reconstruct_later=False, live_capture_priority="MEDIUM", decision_role="CONTEXT_ONLY",
        documented_endpoint="GET https://api.stlouisfed.org/fred/series/observations",
        notes="First-seen same-day daily macro regime context only. It is not a timestamped CPI/NFP/FOMC surprise feed and cannot supply the second intraday BTC directional origin.",
    ),
    SourceCapability(
        lane="MACRO_CROSS_ASSET", dataset="MACRO_CONSENSUS_SNAPSHOTS", provider="TRADING_ECONOMICS",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="Archive each Trading Economics representative-economist Forecast state with provider LastUpdate and AlphaPilot first_seen_at strictly before the official release. Exact official release time/reference period and DateSpan=0 are required; a forecast learned or fetched after release can never be backdated into surprise calculation.",
        can_reconstruct_later=False, live_capture_priority="MEDIUM", decision_role="CONTEXT_ONLY",
        documented_endpoint="GET https://api.tradingeconomics.com/calendar/country/united%20states/{yyyy-mm-dd}/{yyyy-mm-dd}?values=true&f=json",
        notes="Forecast is representative-economist consensus; TEForecast is the provider's own projection and may never substitute for consensus. V1 archives prospective pre-release CPI/Employment states only; documented historical point-in-time backfill remains disabled until separately validated.",
    ),
    SourceCapability(
        lane="MACRO_CROSS_ASSET", dataset="SCHEDULED_MACRO_RELEASES", provider="OFFICIAL_OR_VERIFIED_RELEASE_SOURCE",
        historical_mode="OFFICIAL_RELEASE_ARCHIVE",
        point_in_time_requirement="Official release timestamp and historical vintage; later revisions cannot replace first release in replay.",
        can_reconstruct_later=False, live_capture_priority="MEDIUM", decision_role="DIRECTIONAL_EVIDENCE",
    ),
    SourceCapability(
        lane="MACRO_CROSS_ASSET", dataset="CROSS_ASSET_MARKET_PRICES", provider="VERIFIED_MARKET_DATA",
        historical_mode="RECONSTRUCTIBLE_PUBLIC_HISTORY",
        point_in_time_requirement="Completed bar or timestamped trade only; provider history must preserve original observation time.",
        can_reconstruct_later=True, live_capture_priority="LOW", decision_role="DIRECTIONAL_EVIDENCE",
    ),
    SourceCapability(
        lane="HISTORICAL_MEMORY", dataset="ALPHAPILOT_PRIOR_RESOLVED_EXPERIENCE", provider="ALPHAPILOT",
        historical_mode="FIRST_SEEN_ARCHIVE_REQUIRED",
        point_in_time_requirement="Use the dedicated immutable Experience Memory store; only cases with resolved_at strictly before the current click may be visible.",
        can_reconstruct_later=False, live_capture_priority="CRITICAL", decision_role="CONTEXT_ONLY",
        notes="Resolved outcomes belong in dedicated Experience Memory, not the market-data PIT archive. Memory cannot use future outcomes or create the second directional confirmation by itself.",
    ),
)


def source_capability_registry() -> list[dict]:
    rows = []
    seen = set()
    for capability in BTC_SOURCE_CAPABILITIES:
        capability.validated()
        key = (capability.lane, capability.dataset, capability.provider)
        if key in seen:
            raise ValueError(f"duplicate source capability: {key}")
        seen.add(key)
        rows.append(asdict(capability))
    return rows


def capability_for(dataset: str) -> SourceCapability:
    matches = [row for row in BTC_SOURCE_CAPABILITIES if row.dataset == dataset]
    if len(matches) != 1:
        raise KeyError(f"expected exactly one capability for dataset={dataset!r}")
    return matches[0].validated()


def live_capture_plan() -> dict:
    rows = source_capability_registry()
    critical = [row for row in rows if row["live_capture_priority"] == "CRITICAL"]
    high = [row for row in rows if row["live_capture_priority"] == "HIGH"]
    reconstructible = [row for row in rows if row["can_reconstruct_later"]]
    return {
        "version": "BTC_LIVE_CAPTURE_PLAN_V9",
        "capture_first": [row["dataset"] for row in critical],
        "capture_high_priority": [row["dataset"] for row in high],
        "capture_medium_priority": [row["dataset"] for row in rows if row["live_capture_priority"] == "MEDIUM"],
        "do_not_duplicate_by_default": [row["dataset"] for row in reconstructible],
        "rule": "STORE_IRREPLACEABLE_PIT_STATE_FIRST; RECONSTRUCT_PUBLIC_CANDLES_AND_VINTAGES_LATER",
        "options_archive_required_before_economic_backtest": True,
        "global_options_context_is_not_coindcx_execution_data": True,
        "global_options_greeks_is_separate_first_seen_state": True,
        "global_options_greeks_is_not_coindcx_execution_data": True,
        "futures_context_capture_does_not_enable_futures_execution": True,
        "news_raw_and_enrichment_have_separate_first_seen_state": True,
        "social_raw_and_enrichment_have_separate_first_seen_state": True,
        "social_provider_requires_verified_rights": True,
        "stablecoin_supply_is_separate_from_exchange_flows": True,
        "fred_vintage_history_is_reconstructible": True,
        "fred_same_day_live_snapshot_requires_first_seen_archive": True,
        "fred_daily_regime_is_not_exact_macro_event_surprise": True,
        "macro_consensus_requires_pre_release_first_seen_archive": True,
        "macro_consensus_is_not_directional_evidence": True,
        "tradingeconomics_consensus_provider_verified": True,
        "tradingeconomics_teforecast_is_not_consensus": True,
        "tradingeconomics_historical_pit_backfill_enabled": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_SOURCE_CAPABILITY_CONTRACT_V9",
        "undocumented_equals_historical_support": False,
        "current_endpoint_equals_historical_archive": False,
        "future_reconstruction_of_first_seen_state_allowed": False,
        "option_history_may_be_fabricated": False,
        "global_options_context_may_select_coindcx_contract": False,
        "global_options_context_may_fill_coindcx_replay": False,
        "global_options_greeks_may_select_coindcx_contract": False,
        "global_options_greeks_may_fill_coindcx_replay": False,
        "global_options_delta_may_be_inferred_from_strike": False,
        "provider_entity_label_revision_may_rewrite_old_click": False,
        "scheduled_macro_revision_may_replace_first_release": False,
        "macro_consensus_learned_after_release_may_be_backdated": False,
        "macro_consensus_may_assign_btc_direction": False,
        "tradingeconomics_teforecast_may_substitute_consensus": False,
        "tradingeconomics_historical_pit_backfill_enabled": False,
        "fred_historical_same_day_vintage_proves_intraday_visibility": False,
        "fred_live_first_seen_may_be_backdated": False,
        "fred_daily_regime_may_supply_second_intraday_origin": False,
        "news_verification_learned_later_may_be_backdated": False,
        "raw_news_may_be_rewritten_by_enrichment": False,
        "social_public_visibility_equals_retention_permission": False,
        "social_analysis_learned_later_may_be_backdated": False,
        "raw_social_may_be_rewritten_by_enrichment": False,
        "stablecoin_supply_equals_exchange_buying_power": False,
        "reconstructible_ohlcv_should_be_storage_priority": False,
        "irrecoverable_pit_state_should_be_storage_priority": True,
        "futures_context_may_inform_options": True,
        "futures_execution_enabled": False,
        "research_only": True,
    }
