import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow, HistoricalProvenance
from app.crypto_macro_event_semantics import NormalizedMacroSurprise, macro_event_evidence
from app.crypto_macro_market_reaction import (
    BTC_SOURCE,
    USD_PROXY_KIND,
    RawMacroMarketReaction,
    architecture_contract,
    assemble_raw_macro_reaction,
    normalize_macro_market_reaction,
)
from app.massive_macro_futures_reaction_provider import (
    MassiveMacroFuturesReaction,
    SelectedFuturesContract,
)


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
OBSERVED = RELEASE + timedelta(minutes=10)


def _prov(source_id):
    return HistoricalProvenance(
        provider="COINDCX",
        source_id=source_id,
        availability_basis="BAR_COMPLETION_RECONSTRUCTION",
        point_in_time_proven=True,
        reconstructible_public_data=True,
    )


def _btc_candle(close_at, close, source_id):
    return BtcSpotCandleArchiveRow(
        open_at=close_at - timedelta(minutes=1),
        close_at=close_at,
        available_at=close_at,
        open=close,
        high=close * 1.001,
        low=close * 0.999,
        close=close,
        volume=100.0,
        provenance=_prov(source_id),
    ).validated()


def _selected(product, ticker, ref):
    return SelectedFuturesContract(
        product_code=product,
        ticker=ticker,
        trading_venue="XCME",
        event_date=RELEASE.date().isoformat(),
        days_to_maturity=7,
        pre_release_volume=1000.0,
        selection_window_start=RELEASE - timedelta(minutes=30),
        selection_window_end=RELEASE,
        reference_close=ref,
    ).validated()


def _cross(event_key="BLS:CPI:2026-08"):
    return MassiveMacroFuturesReaction(
        event_key=event_key,
        event_type="CPI",
        release_at=RELEASE,
        observed_at=OBSERVED,
        reconstructible_available_at=OBSERVED,
        retrieved_at=OBSERVED + timedelta(days=1),
        nasdaq_futures_return_pct=-0.8,
        eurusd_futures_return_pct=-0.3,
        usd_strength_proxy_return_pct=0.3,
        nasdaq_contract=_selected("NQ", "NQU6", 20000),
        euro_fx_contract=_selected("6E", "6EU6", 1.1),
    ).validated()


def _raw_prior(i, *, event_type="CPI", window=10, release_at=None, btc_return=None, btc_source=BTC_SOURCE):
    release = release_at or (RELEASE - timedelta(days=30 * (i + 1)))
    observed = release + timedelta(minutes=window)
    value = btc_return if btc_return is not None else (-0.95 + i * 0.1)
    return RawMacroMarketReaction(
        event_key=f"PRIOR:{event_type}:{i}",
        event_type=event_type,
        release_at=release,
        observed_at=observed,
        reconstructible_available_at=observed,
        window_minutes=window,
        btc_return_pct=value,
        nasdaq_return_pct=-0.2,
        usd_strength_proxy_return_pct=0.1,
        btc_source=btc_source,
        cross_asset_source="MASSIVE_CME_FUTURES",
    ).validated()


class CryptoMacroMarketReactionTests(unittest.TestCase):
    def test_assembles_exact_btc_and_cross_asset_window(self):
        raw = assemble_raw_macro_reaction(
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
            btc_candles=[
                _btc_candle(RELEASE, 100000.0, "pre"),
                _btc_candle(OBSERVED, 99000.0, "post"),
            ],
            cross_asset_reaction=_cross(),
        )
        self.assertAlmostEqual(raw.btc_return_pct, -1.0)
        self.assertAlmostEqual(raw.nasdaq_return_pct, -0.8)
        self.assertAlmostEqual(raw.usd_strength_proxy_return_pct, 0.3)
        self.assertEqual(raw.window_minutes, 10)
        self.assertEqual(raw.reconstructible_available_at, OBSERVED)
        self.assertFalse(raw.prospective_live_availability_proven)

    def test_missing_exact_btc_pre_or_post_bar_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "closing at"):
            assemble_raw_macro_reaction(
                event_key="BLS:CPI:2026-08",
                event_type="CPI",
                release_at=RELEASE,
                btc_candles=[_btc_candle(OBSERVED, 99000.0, "post")],
                cross_asset_reaction=_cross(),
            )
        with self.assertRaisesRegex(ValueError, "closing at"):
            assemble_raw_macro_reaction(
                event_key="BLS:CPI:2026-08",
                event_type="CPI",
                release_at=RELEASE,
                btc_candles=[_btc_candle(RELEASE, 100000.0, "pre")],
                cross_asset_reaction=_cross(),
            )

    def test_cross_asset_event_identity_must_match_exactly(self):
        with self.assertRaisesRegex(ValueError, "same macro event"):
            assemble_raw_macro_reaction(
                event_key="BLS:CPI:2026-08",
                event_type="CPI",
                release_at=RELEASE,
                btc_candles=[
                    _btc_candle(RELEASE, 100000.0, "pre"),
                    _btc_candle(OBSERVED, 99000.0, "post"),
                ],
                cross_asset_reaction=_cross("BLS:CPI:WRONG"),
            )

    def test_normalizer_requires_twenty_strictly_prior_comparable_reactions(self):
        current = RawMacroMarketReaction(
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
            observed_at=OBSERVED,
            reconstructible_available_at=OBSERVED,
            window_minutes=10,
            btc_return_pct=-1.0,
            nasdaq_return_pct=-0.8,
            usd_strength_proxy_return_pct=0.3,
            btc_source=BTC_SOURCE,
            cross_asset_source="MASSIVE_CME_FUTURES",
        ).validated()
        with self.assertRaisesRegex(ValueError, "19 < 20"):
            normalize_macro_market_reaction(current, [_raw_prior(i) for i in range(19)])

        normalized = normalize_macro_market_reaction(current, [_raw_prior(i) for i in range(20)])
        self.assertEqual(normalized.source, "COINDCX_SPOT_PLUS_MASSIVE_CME_FUTURES_RECONSTRUCTED")
        self.assertGreaterEqual(normalized.btc_abs_move_percentile, 0.0)
        self.assertLessEqual(normalized.btc_abs_move_percentile, 1.0)
        self.assertEqual(normalized.usd_strength_proxy_kind, USD_PROXY_KIND)
        self.assertIsNone(normalized.broad_usd_return_pct)
        self.assertIsNone(normalized.real_yield_change_bps)

    def test_future_wrong_event_type_window_and_btc_source_do_not_enter_distribution(self):
        current = RawMacroMarketReaction(
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
            observed_at=OBSERVED,
            reconstructible_available_at=OBSERVED,
            window_minutes=10,
            btc_return_pct=-1.0,
            nasdaq_return_pct=-0.8,
            usd_strength_proxy_return_pct=0.3,
            btc_source=BTC_SOURCE,
            cross_asset_source="MASSIVE_CME_FUTURES",
        ).validated()
        priors = [_raw_prior(i) for i in range(20)]
        priors.extend([
            _raw_prior(100, event_type="EMPLOYMENT_SITUATION"),
            _raw_prior(101, window=5),
            _raw_prior(102, btc_source="OTHER"),
            _raw_prior(103, release_at=RELEASE + timedelta(days=1)),
            _raw_prior(104, release_at=RELEASE),
        ])
        normalized = normalize_macro_market_reaction(current, priors)
        expected = sum(1 for row in priors[:20] if abs(row.btc_return_pct) <= 1.0) / 20
        self.assertAlmostEqual(normalized.btc_abs_move_percentile, expected)

    def test_full_hawkish_chain_can_create_one_bearish_macro_origin_only(self):
        raw = assemble_raw_macro_reaction(
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
            btc_candles=[
                _btc_candle(RELEASE, 100000.0, "pre"),
                _btc_candle(OBSERVED, 98500.0, "post"),
            ],
            cross_asset_reaction=_cross(),
        )
        # Prior BTC event moves are deliberately smaller so current magnitude is extreme.
        priors = [_raw_prior(i, btc_return=0.05 + i * 0.02) for i in range(20)]
        market = normalize_macro_market_reaction(raw, priors)
        state = NormalizedMacroSurprise(
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
            semantic_state="HAWKISH_SHOCK",
            metric_percentiles={"headline_mom_pct": 0.95, "core_mom_pct": 0.90},
            prior_sample_count=24,
            lower_percentile=0.20,
            upper_percentile=0.80,
        ).validated()
        evidence = macro_event_evidence(
            state,
            market,
            decision_at=OBSERVED + timedelta(seconds=1),
        )
        self.assertEqual(market.btc_abs_move_percentile, 1.0)
        self.assertEqual(evidence.stance, "BEARISH")
        self.assertEqual(evidence.causal_origin, "GLOBAL_RISK_LIQUIDITY")
        self.assertEqual(evidence.metadata["cross_asset_alignment_count"], 2)
        self.assertFalse(evidence.metadata["may_generate_options_trade"])
        self.assertFalse(evidence.metadata["may_generate_futures_trade"])

    def test_architecture_separates_raw_provider_from_prior_normalizer_and_live_use(self):
        contract = architecture_contract()
        self.assertFalse(contract["provider_supplies_btc_move_percentile"])
        self.assertTrue(contract["strictly_prior_btc_move_distribution_required"])
        self.assertTrue(contract["same_event_type_required_for_btc_move_percentile"])
        self.assertTrue(contract["same_window_required_for_btc_move_percentile"])
        self.assertTrue(contract["same_btc_source_required_for_btc_move_percentile"])
        self.assertEqual(contract["default_min_prior_events"], 20)
        self.assertFalse(contract["future_or_unresolved_prior_reaction_may_enter_distribution"])
        self.assertFalse(contract["missing_reaction_treated_as_neutral"])
        self.assertFalse(contract["prospective_live_confirmation_enabled"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
