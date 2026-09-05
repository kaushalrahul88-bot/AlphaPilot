import unittest
from datetime import datetime, timezone

from app.crypto_btc_perception import (
    BtcHistoricalAnalogue,
    BtcMacroCrossAssetSnapshot,
    BtcOptionsMarketSnapshot,
    BtcSpotStructureSnapshot,
    architecture_contract,
    assemble_btc_perception,
    historical_analogue_context,
    macro_cross_asset_context,
    options_market_context,
    spot_structure_context,
)
from app.crypto_market_intelligence import derivatives_context


def _t(hour=4, minute=0):
    return datetime(2026, 9, 5, hour, minute, tzinfo=timezone.utc)


def _bullish_spot():
    return spot_structure_context(
        BtcSpotStructureSnapshot(
            observed_at=_t(),
            price=100_000,
            return_1h_pct=0.8,
            return_4h_pct=2.1,
            return_24h_pct=4.0,
            close_location=0.82,
            volume_percentile=0.72,
            breakout_state="UPSIDE_CONFIRMED",
        )
    )


class BtcSpecialistPerceptionTests(unittest.TestCase):
    def test_bullish_spot_structure_can_be_one_independent_origin(self):
        row = _bullish_spot()
        self.assertEqual(row.stance, "BULLISH")
        self.assertFalse(row.context_only)
        self.assertEqual(row.causal_origin, "SPOT_PRICE_STRUCTURE")
        self.assertFalse(row.metadata["trade_generated"])

    def test_options_market_is_context_only_even_when_put_skew_and_oi_are_extreme(self):
        row = options_market_context(
            BtcOptionsMarketSnapshot(
                observed_at=_t(),
                atm_iv_percentile=0.96,
                put_call_skew_25d=9.0,
                put_call_oi_ratio=1.8,
            )
        )
        self.assertEqual(row.stance, "UNKNOWN")
        self.assertTrue(row.context_only)
        self.assertFalse(row.metadata["standalone_direction_allowed"])
        self.assertFalse(row.metadata["may_generate_options_trade"])

    def test_one_spot_origin_is_not_enough_for_btc_direction(self):
        state = assemble_btc_perception([_bullish_spot()], decision_at=_t(), trade_horizon="intraday")
        self.assertEqual(state["direction"], "UNKNOWN")
        self.assertEqual(state["state"], "INSUFFICIENT_INDEPENDENT_CONFIRMATION")

    def test_spot_plus_non_extreme_derivatives_can_form_bullish_state(self):
        derivatives = derivatives_context(
            observed_at=_t(),
            price_change_pct=2.0,
            oi_change_pct=6.0,
            funding_percentile=0.58,
            short_liquidations_usd=1_000_000,
            long_liquidations_usd=300_000,
        )
        state = assemble_btc_perception([_bullish_spot(), derivatives], decision_at=_t(), trade_horizon="intraday")
        self.assertEqual(state["direction"], "BULLISH")
        self.assertTrue(state["instrument_neutral"])
        self.assertFalse(state["options_trade_generated"])
        self.assertFalse(state["futures_trade_generated"])

    def test_extreme_long_crowding_cannot_supply_second_bullish_origin(self):
        derivatives = derivatives_context(
            observed_at=_t(),
            price_change_pct=3.0,
            oi_change_pct=12.0,
            funding_percentile=0.97,
        )
        state = assemble_btc_perception([_bullish_spot(), derivatives], decision_at=_t(), trade_horizon="intraday")
        self.assertEqual(state["direction"], "UNKNOWN")

    def test_coherent_macro_can_be_independent_bullish_origin(self):
        macro = macro_cross_asset_context(
            BtcMacroCrossAssetSnapshot(
                observed_at=_t(),
                dxy_change_pct=-0.5,
                nasdaq_change_pct=1.1,
                real_yield_change_bps=-7.0,
            )
        )
        self.assertEqual(macro.stance, "BULLISH")
        self.assertFalse(macro.context_only)
        state = assemble_btc_perception([_bullish_spot(), macro], decision_at=_t(), trade_horizon="intraday")
        self.assertEqual(state["direction"], "BULLISH")

    def test_mixed_macro_stays_context_only(self):
        macro = macro_cross_asset_context(
            BtcMacroCrossAssetSnapshot(
                observed_at=_t(),
                dxy_change_pct=0.5,
                nasdaq_change_pct=1.0,
                real_yield_change_bps=0.0,
            )
        )
        self.assertEqual(macro.stance, "UNKNOWN")
        self.assertTrue(macro.context_only)

    def test_historical_memory_cannot_supply_second_confirmation(self):
        memory = historical_analogue_context(
            BtcHistoricalAnalogue(
                observed_at=_t(),
                analogue_count=80,
                bullish_fraction=0.78,
                similarity=0.82,
            )
        )
        self.assertTrue(memory.context_only)
        self.assertEqual(memory.stance, "UNKNOWN")
        state = assemble_btc_perception([_bullish_spot(), memory], decision_at=_t(), trade_horizon="intraday")
        self.assertEqual(state["direction"], "UNKNOWN")

    def test_options_context_cannot_manufacture_second_confirmation(self):
        options = options_market_context(
            BtcOptionsMarketSnapshot(
                observed_at=_t(),
                atm_iv_percentile=0.4,
                put_call_skew_25d=-8.0,
                put_call_oi_ratio=0.55,
            )
        )
        state = assemble_btc_perception([_bullish_spot(), options], decision_at=_t(), trade_horizon="intraday")
        self.assertEqual(state["direction"], "UNKNOWN")

    def test_architecture_contract_preserves_instrument_separation(self):
        contract = architecture_contract()
        self.assertEqual(contract["default_platform"], "COINDCX")
        self.assertFalse(contract["options_market_may_create_underlying_direction"])
        self.assertFalse(contract["historical_memory_may_create_current_direction"])
        self.assertFalse(contract["social_narrative_may_create_current_direction"])
        self.assertTrue(contract["two_independent_causal_origins_required"])
        self.assertFalse(contract["mixed_instrument_trade_allowed"])
        self.assertFalse(contract["broker_execution_enabled"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
