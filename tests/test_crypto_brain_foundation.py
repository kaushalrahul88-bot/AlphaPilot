from datetime import datetime, timedelta, timezone

import pytest

from app.crypto_domain_knowledge import (
    DEFAULT_CRYPTO_PLATFORM,
    crypto_knowledge_pack_v1,
    source_quality_score,
)
from app.crypto_market_intelligence import (
    Evidence,
    MarketObservation,
    assemble_market_state,
    derivatives_context,
    evidence_is_fresh,
    onchain_transfer_context,
    stablecoin_liquidity_context,
)
from app.crypto_trade_contract import (
    architecture_contract,
    futures_trade_intent,
    options_trade_intent,
)


def _now():
    return datetime(2026, 9, 5, 3, 30, tzinfo=timezone.utc)


def test_coindcx_is_default_and_knowledge_is_not_execution():
    pack = crypto_knowledge_pack_v1()
    assert DEFAULT_CRYPTO_PLATFORM == "COINDCX"
    assert pack["default_platform"] == "COINDCX"
    assert pack["research_only"] is True
    assert pack["broker_execution_enabled"] is False
    assert pack["principles"]["knowledge_is_not_trading_rule"] is True
    assert "on_chain_flows" in pack["knowledge_domains"]
    assert "social_sentiment_and_narratives" in pack["knowledge_domains"]


def test_primary_source_scores_above_unverified_given_equal_inputs():
    assert source_quality_score("A_PRIMARY") > source_quality_score("E_UNVERIFIED")


def test_derivatives_context_can_support_shared_state_without_generating_futures_trade():
    row = derivatives_context(
        observed_at=_now(),
        price_change_pct=2.0,
        oi_change_pct=7.0,
        funding_percentile=0.55,
        short_liquidations_usd=1_000_000,
        long_liquidations_usd=200_000,
    )
    assert row.stance == "BULLISH"
    assert row.metadata["may_inform_options"] is True
    assert row.metadata["may_generate_futures_trade"] is False


def test_extreme_long_crowding_does_not_become_bullish_vote():
    row = derivatives_context(
        observed_at=_now(),
        price_change_pct=3.0,
        oi_change_pct=12.0,
        funding_percentile=0.97,
    )
    assert row.stance == "NEUTRAL"
    assert row.metadata["crowded_long"] is True


def test_unverified_whale_alert_is_context_only():
    observation = MarketObservation(
        asset="BTC",
        family="WHALE_TRANSFER",
        observed_at=_now(),
        source="X_ANONYMOUS",
        source_tier="E_UNVERIFIED",
        value=8_000,
        unit="BTC",
        confidence=0.4,
        verified=False,
        causal_origin="BLOCKCHAIN_ENTITY_FLOW",
    )
    evidence = onchain_transfer_context(
        observation,
        entity_confidence=0.3,
        destination_type="SPOT_EXCHANGE",
        historical_directional_reliability=0.8,
        market_confirmation=True,
    )
    assert evidence.stance == "UNKNOWN"
    assert evidence.context_only is True


def test_verified_exchange_deposit_needs_all_gates_before_bearish_interpretation():
    observation = MarketObservation(
        asset="BTC",
        family="ENTITY_FLOW",
        observed_at=_now(),
        source="ONCHAIN_PROVIDER",
        source_tier="B_INSTITUTIONAL_RESEARCH",
        value=2_500,
        unit="BTC",
        confidence=0.9,
        verified=True,
        causal_origin="BLOCKCHAIN_ENTITY_FLOW",
    )
    evidence = onchain_transfer_context(
        observation,
        entity_confidence=0.9,
        destination_type="SPOT_EXCHANGE",
        historical_directional_reliability=0.8,
        market_confirmation=True,
    )
    assert evidence.stance == "BEARISH"
    assert evidence.context_only is False


def test_stablecoin_derivatives_flow_stays_context_only():
    evidence = stablecoin_liquidity_context(
        observed_at=_now(),
        netflow_usd=500_000_000,
        venue_type="DERIVATIVES_EXCHANGE",
        source="ONCHAIN_PROVIDER",
    )
    assert evidence.stance == "UNKNOWN"
    assert evidence.context_only is True


def test_horizon_matching_excludes_stale_intraday_evidence():
    row = Evidence(
        family="NEWS",
        causal_origin="EVENT",
        stance="BULLISH",
        strength="MEDIUM",
        confidence=0.8,
        observed_at=_now() - timedelta(hours=8),
        reason="old",
        context_only=False,
        source="SOURCE",
        metadata={},
    )
    assert evidence_is_fresh(row, decision_at=_now(), trade_horizon="intraday") is False
    assert evidence_is_fresh(row, decision_at=_now(), trade_horizon="swing") is True


def test_market_state_requires_two_independent_causal_origins():
    derivatives = derivatives_context(
        observed_at=_now(), price_change_pct=1.5, oi_change_pct=4.0, funding_percentile=0.5
    )
    stablecoins = stablecoin_liquidity_context(
        observed_at=_now(), netflow_usd=250_000_000, venue_type="SPOT_EXCHANGE", source="CHAIN"
    )
    state = assemble_market_state([derivatives, stablecoins], decision_at=_now(), trade_horizon="intraday")
    assert state["direction"] == "BULLISH"
    assert state["instrument_neutral"] is True
    assert state["options_trade_generated"] is False
    assert state["futures_trade_generated"] is False


def test_options_and_futures_trade_fields_cannot_mix():
    options = options_trade_intent(
        asset="BTC",
        action="BUY_CALL",
        rationale="validated bullish market state and acceptable option economics",
        metadata={"option_type": "CALL", "strike": 100_000, "expiry": "2026-09-11", "premium": 500},
    )
    assert options["instrument_type"] == "OPTIONS"

    futures = futures_trade_intent(
        asset="BTC",
        action="LONG",
        rationale="separate futures-specific route",
        metadata={"leverage": 2, "liquidation_price": 80_000, "funding_rate": 0.0001},
    )
    assert futures["instrument_type"] == "FUTURES"

    with pytest.raises(ValueError):
        options_trade_intent(
            asset="BTC",
            action="BUY_CALL",
            rationale="invalid mixed route",
            metadata={"strike": 100_000, "leverage": 10},
        )
    with pytest.raises(ValueError):
        futures_trade_intent(
            asset="BTC",
            action="LONG",
            rationale="invalid mixed route",
            metadata={"leverage": 2, "strike": 100_000},
        )


def test_instrument_architecture_contract_is_hard_separated():
    contract = architecture_contract()
    assert contract["default_platform"] == "COINDCX"
    assert contract["options_and_futures_trade_generation_separate"] is True
    assert contract["mixed_instrument_trade_allowed"] is False
    assert contract["broker_execution_enabled"] is False
    assert contract["capital_committed"] == 0
