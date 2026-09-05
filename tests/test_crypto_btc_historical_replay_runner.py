import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.crypto_btc_historical_data_adapter import (
    BtcHistoricalArchive,
    BtcHistoricalEvidenceRow,
    BtcOptionContractArchiveRow,
    BtcOptionQuoteArchiveRow,
    BtcOptionsExecutionArchiveRow,
    BtcSpotCandleArchiveRow,
    HistoricalProvenance,
    architecture_contract as adapter_contract,
    normalize_coindcx_spot_candles,
    option_replay_observations,
    source_coverage_at,
    visible_evidence_at,
    visible_option_contracts_at,
)
from app.crypto_btc_historical_replay_runner import (
    BtcHistoricalBacktestPolicy,
    architecture_contract as runner_contract,
    run_btc_historical_random_click_backtest,
)
from app.crypto_btc_options_contract_selector import BtcOptionContractSnapshot
from app.crypto_btc_options_risk import BtcOptionsExecutionSpec, BtcOptionsRiskPolicy
from app.crypto_btc_options_shadow_replay import BtcOptionsReplayCostSpec
from app.crypto_btc_perception import BtcOptionsMarketSnapshot, options_market_context
from app.crypto_btc_random_click_experience import BtcExperiencePolicy, BtcRandomClickPolicy
from app.crypto_market_intelligence import Evidence, derivatives_context


def _t(day=5, hour=4, minute=0, second=0):
    return datetime(2026, 9, day, hour, minute, second, tzinfo=timezone.utc)


def _prov(source_id, *, proven=True, basis="IMMUTABLE_ARCHIVE"):
    return HistoricalProvenance(
        provider="TEST_ARCHIVE",
        source_id=source_id,
        availability_basis=basis,
        point_in_time_proven=proven,
        immutable_archive=basis == "IMMUTABLE_ARCHIVE",
        reconstructible_public_data=basis == "BAR_COMPLETION_RECONSTRUCTION",
    )


def _candles(click=_t(), future_hours=6):
    start = click - timedelta(hours=30)
    rows = []
    for i in range(30 + future_hours):
        open_at = start + timedelta(hours=i)
        close_at = open_at + timedelta(hours=1)
        close = 97_000.0 + i * 100.0
        rows.append(BtcSpotCandleArchiveRow(
            open_at=open_at,
            close_at=close_at,
            available_at=close_at,
            open=close - 40.0,
            high=close + 10.0,
            low=close - 60.0,
            close=close,
            volume=100.0 + i,
            provenance=_prov(f"spot-{i}", basis="BAR_COMPLETION_RECONSTRUCTION"),
        ))
    return rows


def _execution(click=_t()):
    spec = BtcOptionsExecutionSpec(
        account_currency="INR",
        premium_currency="TEST",
        premium_to_account_rate=1.0,
        contract_multiplier=1.0,
        quantity_step=1.0,
        min_quantity=1.0,
        max_quantity=None,
        entry_slippage_pct_of_premium=0.0,
        exit_slippage_pct_of_premium=0.0,
        entry_fee_per_quantity_account=0.0,
        stop_exit_fee_per_quantity_account=0.0,
        target_exit_fee_per_quantity_account=0.0,
    )
    return BtcOptionsExecutionArchiveRow(
        available_at=click - timedelta(days=1),
        execution_spec=spec,
        selector_fee_bps_per_side=1.0,
        replay_costs=BtcOptionsReplayCostSpec(0.0, 0.0, 60),
        provenance=_prov("execution-v1"),
    )


def _evidence_rows(click=_t(), *, derivative_proven=True):
    derivative = derivatives_context(
        observed_at=click - timedelta(minutes=1),
        price_change_pct=2.0,
        oi_change_pct=6.0,
        funding_percentile=0.55,
        short_liquidations_usd=1_000_000,
        long_liquidations_usd=200_000,
    )
    options = options_market_context(BtcOptionsMarketSnapshot(
        observed_at=click - timedelta(seconds=30),
        atm_iv_percentile=0.55,
        put_call_skew_25d=1.0,
        put_call_oi_ratio=0.9,
    ))
    return [
        BtcHistoricalEvidenceRow(
            evidence=derivative,
            event_at=click - timedelta(minutes=1),
            available_at=click - timedelta(minutes=1),
            provenance=_prov("derivatives-1", proven=derivative_proven),
        ),
        BtcHistoricalEvidenceRow(
            evidence=options,
            event_at=click - timedelta(seconds=30),
            available_at=click - timedelta(seconds=30),
            provenance=_prov("options-market-1"),
        ),
    ]


def _contract(click=_t(), *, available_at=None, symbol="BTC-TEST-CALL", bid=99.0, ask=101.0):
    snapshot = BtcOptionContractSnapshot(
        symbol=symbol,
        option_type="CALL",
        strike=100_000.0,
        expiry_at=click + timedelta(hours=24),
        observed_at=available_at or click - timedelta(seconds=15),
        bid=bid,
        ask=ask,
        mark=100.0,
        delta=0.5,
        gamma=1e-8,
        theta=-1.0,
        vega=1.0,
        implied_volatility=60.0,
        open_interest=100.0,
        volume_24h=100.0,
    )
    return BtcOptionContractArchiveRow(
        snapshot=snapshot,
        event_at=available_at or click - timedelta(seconds=15),
        available_at=available_at or click - timedelta(seconds=15),
        provenance=_prov(f"contract-{symbol}-{available_at or 'base'}"),
    )


def _archive(click=_t(), *, contracts=True, derivative_proven=True):
    candles = _candles(click)
    contract_rows = [_contract(click)] if contracts else []
    quote = BtcOptionQuoteArchiveRow(
        symbol="BTC-TEST-CALL",
        event_at=click + timedelta(hours=3),
        available_at=click + timedelta(hours=3),
        bid=180.0,
        ask=182.0,
        provenance=_prov("exit-quote"),
    )
    return BtcHistoricalArchive(
        spot_candles=tuple(candles),
        evidence_rows=tuple(_evidence_rows(click, derivative_proven=derivative_proven)),
        option_contract_rows=tuple(contract_rows),
        option_quote_rows=(quote,),
        execution_rows=(_execution(click),),
    )


def _policy(click=_t()):
    return BtcHistoricalBacktestPolicy(
        click_policy=BtcRandomClickPolicy(click, click + timedelta(seconds=1), 1, 7),
        experience_policy=BtcExperiencePolicy(4.0, 3.0),
        trade_horizon="intraday",
        max_spot_age_seconds=3600,
        structural_lookback_hours=2.0,
        min_invalidation_distance_pct=0.05,
        max_invalidation_distance_pct=2.0,
        reward_multiple=1.5,
        expected_holding_hours=4.0,
        iv_stress_points=0.0,
    )


def _risk():
    return BtcOptionsRiskPolicy(
        account_equity=100_000.0,
        max_premium_allocation_pct_of_equity=20.0,
        max_planned_loss_pct_of_equity=5.0,
        max_tail_loss_pct_of_equity=20.0,
        min_net_reward_risk=0.5,
    )


class BtcHistoricalReplayRunnerTests(unittest.TestCase):
    def test_coindcx_candle_is_visible_only_at_bar_completion(self):
        open_at = _t(4, 0)
        payload = [{
            "time": int(open_at.timestamp() * 1000),
            "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 12.0,
        }]
        rows = normalize_coindcx_spot_candles(payload, interval="1h")
        self.assertEqual(rows[0].open_at, open_at)
        self.assertEqual(rows[0].available_at, open_at + timedelta(hours=1))
        self.assertTrue(rows[0].provenance.reconstructible_public_data)

    def test_unproven_evidence_cannot_influence_click(self):
        archive = _archive(derivative_proven=False)
        rows = visible_evidence_at(archive, decision_at=_t(), max_spot_age_seconds=3600)
        families = {row.family for row in rows}
        self.assertIn("BTC_SPOT_STRUCTURE", families)
        self.assertNotIn("DERIVATIVES_POSITIONING", families)
        coverage = source_coverage_at(archive, decision_at=_t(), max_spot_age_seconds=3600)
        self.assertGreaterEqual(coverage["point_in_time_unproven_row_count"], 1)
        self.assertFalse(coverage["unproven_rows_may_influence_decision"])

    def test_future_evidence_is_not_visible(self):
        archive = _archive()
        future = BtcHistoricalEvidenceRow(
            evidence=Evidence(
                family="CRYPTO_NEWS", causal_origin="EVENT_INFORMATION", stance="BULLISH", strength="HIGH",
                confidence=0.9, observed_at=_t() + timedelta(minutes=1), reason="future", context_only=False,
                source="TEST", metadata={},
            ),
            event_at=_t() + timedelta(minutes=1),
            available_at=_t() + timedelta(minutes=1),
            provenance=_prov("future-news"),
        )
        archive = replace(archive, evidence_rows=archive.evidence_rows + (future,))
        families = {row.family for row in visible_evidence_at(archive, decision_at=_t(), max_spot_age_seconds=3600)}
        self.assertNotIn("CRYPTO_NEWS", families)

    def test_latest_option_snapshot_is_selected_without_using_future_snapshot(self):
        click = _t()
        old = _contract(click, available_at=click - timedelta(minutes=2), bid=95.0, ask=97.0)
        new = _contract(click, available_at=click - timedelta(seconds=10), bid=99.0, ask=101.0)
        future = _contract(click, available_at=click + timedelta(seconds=1), bid=120.0, ask=122.0)
        archive = replace(_archive(), option_contract_rows=(old, new, future))
        rows = visible_option_contracts_at(archive, decision_at=click)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].ask, 101.0)
        self.assertEqual(rows[0].observed_at, click - timedelta(seconds=10))

    def test_later_option_quote_is_not_backfilled_into_earlier_btc_event(self):
        click = _t()
        quote = BtcOptionQuoteArchiveRow(
            symbol="BTC-TEST-CALL", event_at=click + timedelta(hours=1, seconds=30),
            available_at=click + timedelta(hours=1, seconds=30), bid=150.0, ask=152.0,
            provenance=_prov("later-quote"),
        )
        archive = replace(_archive(), option_quote_rows=(quote,))
        rows = option_replay_observations(
            archive, symbol="BTC-TEST-CALL", decision_at=click, horizon_hours=2.0, extra_quote_delay_seconds=60,
        )
        at_one_hour = [row for row in rows if row.observed_at == click + timedelta(hours=1)][0]
        at_quote = [row for row in rows if row.observed_at == click + timedelta(hours=1, seconds=30)][0]
        self.assertIsNone(at_one_hour.option_bid)
        self.assertEqual(at_quote.option_bid, 150.0)

    def test_full_historical_runner_freezes_buy_call_and_uses_actual_quote(self):
        result = run_btc_historical_random_click_backtest(archive=_archive(), policy=_policy(), risk_policy=_risk())
        self.assertEqual(result["status"], "BACKTEST_COMPLETE")
        self.assertEqual(result["click_count"], 1)
        self.assertEqual(result["input_unresolved_count"], 0)
        click = result["click_results"][0]
        self.assertEqual(click["final_decision"], "BUY_CALL")
        self.assertEqual(click["pipeline_status"], "OPTIONS_SHADOW_PLAN_READY")
        replay = click["outcome"]["replay_result"]
        self.assertEqual(replay["status"], "SHADOW_TRADE_CLOSED")
        self.assertTrue(replay["actual_quote_used_for_pnl"])
        self.assertFalse(replay["model_reference_used_as_fill"])
        self.assertFalse(click["futures_route_invoked"])

    def test_runner_is_reproducible_for_same_archive_and_seed(self):
        first = run_btc_historical_random_click_backtest(archive=_archive(), policy=_policy(), risk_policy=_risk())
        second = run_btc_historical_random_click_backtest(archive=_archive(), policy=_policy(), risk_policy=_risk())
        self.assertEqual(first["click_schedule"], second["click_schedule"])
        self.assertEqual(first["click_results"][0]["decision_fingerprint"], second["click_results"][0]["decision_fingerprint"])

    def test_future_spot_mutation_cannot_change_frozen_decision_fingerprint(self):
        base = _archive()
        first = run_btc_historical_random_click_backtest(archive=base, policy=_policy(), risk_policy=_risk())
        future = BtcSpotCandleArchiveRow(
            open_at=_t() + timedelta(hours=1), close_at=_t() + timedelta(hours=2), available_at=_t() + timedelta(hours=2),
            open=90_000.0, high=91_000.0, low=80_000.0, close=81_000.0, volume=9999.0,
            provenance=_prov("future-mutation", basis="BAR_COMPLETION_RECONSTRUCTION"),
        )
        mutated = replace(base, spot_candles=base.spot_candles + (future,))
        second = run_btc_historical_random_click_backtest(archive=mutated, policy=_policy(), risk_policy=_risk())
        self.assertEqual(first["click_results"][0]["decision_fingerprint"], second["click_results"][0]["decision_fingerprint"])

    def test_missing_contract_archive_becomes_options_no_trade_not_futures(self):
        result = run_btc_historical_random_click_backtest(archive=_archive(contracts=False), policy=_policy(), risk_policy=_risk())
        click = result["click_results"][0]
        self.assertEqual(click["final_decision"], "NO_TRADE")
        self.assertEqual(click["pipeline_status"], "NO_OPTIONS_CONTRACT")
        self.assertFalse(click["futures_route_invoked"])
        self.assertFalse(result["futures_trade_generated"])

    def test_stale_spot_is_input_unresolved_and_excluded_from_performance(self):
        policy = replace(_policy(), max_spot_age_seconds=0)
        click = _t() + timedelta(minutes=30)
        policy = replace(policy, click_policy=BtcRandomClickPolicy(click, click + timedelta(seconds=1), 1, 7))
        result = run_btc_historical_random_click_backtest(archive=_archive(), policy=policy, risk_policy=_risk())
        self.assertEqual(result["input_unresolved_count"], 1)
        self.assertEqual(result["click_results"][0]["status"], "CLICK_INPUT_UNRESOLVED")
        self.assertTrue(result["unresolved_inputs_excluded_from_performance"])

    def test_unproven_derivatives_cannot_manufacture_second_origin(self):
        result = run_btc_historical_random_click_backtest(
            archive=_archive(derivative_proven=False), policy=_policy(), risk_policy=_risk()
        )
        click = result["click_results"][0]
        self.assertEqual(click["final_decision"], "NO_TRADE")
        self.assertIn(click["pipeline_status"], {"NO_UNDERLYING_THESIS", "OPTIONS_CONTEXT_MISSING"})
        self.assertFalse(click["futures_route_invoked"])

    def test_contracts_declare_no_fabricated_options_and_no_futures_fallback(self):
        adapter = adapter_contract()
        runner = runner_contract()
        self.assertFalse(adapter["historical_options_may_be_fabricated"])
        self.assertTrue(adapter["historical_options_require_proven_archive"])
        self.assertFalse(adapter["later_option_quote_may_backfill_earlier_trigger"])
        self.assertFalse(runner["historical_options_may_be_fabricated"])
        self.assertFalse(runner["futures_fallback_allowed"])
        self.assertFalse(runner["broker_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
