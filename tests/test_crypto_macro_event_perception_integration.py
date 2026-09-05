import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_perception import (
    BtcMacroCrossAssetSnapshot,
    BtcSpotStructureSnapshot,
    assemble_btc_perception,
    macro_cross_asset_context,
    spot_structure_context,
)
from app.crypto_macro_event_semantics import MacroMarketReaction, NormalizedMacroSurprise, macro_event_evidence


def _t(minutes=0):
    return datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _macro_event_bullish():
    normalized = NormalizedMacroSurprise(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        release_at=_t(),
        semantic_state="DOVISH_SHOCK",
        metric_percentiles={"headline_mom_pct": 0.05, "core_mom_pct": 0.10},
        prior_sample_count=24,
        lower_percentile=0.20,
        upper_percentile=0.80,
    ).validated()
    reaction = MacroMarketReaction(
        event_key=normalized.event_key,
        release_at=_t(),
        observed_at=_t(10),
        first_seen_at=_t(10) + timedelta(seconds=1),
        btc_return_pct=1.0,
        nasdaq_return_pct=0.8,
        broad_usd_return_pct=-0.3,
        real_yield_change_bps=-4.0,
        btc_abs_move_percentile=0.9,
        source="VERIFIED_CROSS_ASSET_MARKET_DATA",
        source_verified=True,
    ).validated()
    return macro_event_evidence(normalized, reaction, decision_at=_t(11))


def _generic_macro_bullish():
    return macro_cross_asset_context(BtcMacroCrossAssetSnapshot(
        observed_at=_t(10),
        dxy_change_pct=-0.5,
        nasdaq_change_pct=1.0,
        real_yield_change_bps=-7.0,
    ))


def _spot_bullish():
    return spot_structure_context(BtcSpotStructureSnapshot(
        observed_at=_t(10),
        price=100_000,
        return_1h_pct=0.8,
        return_4h_pct=2.0,
        return_24h_pct=3.0,
        close_location=0.8,
        volume_percentile=0.7,
        breakout_state="UPSIDE_CONFIRMED",
    ))


class CryptoMacroEventPerceptionIntegrationTests(unittest.TestCase):
    def test_exact_macro_and_generic_macro_are_one_causal_origin_not_two(self):
        exact = _macro_event_bullish()
        generic = _generic_macro_bullish()
        self.assertEqual(exact.causal_origin, "GLOBAL_RISK_LIQUIDITY")
        self.assertEqual(generic.causal_origin, "GLOBAL_RISK_LIQUIDITY")
        state = assemble_btc_perception([exact, generic], decision_at=_t(11), trade_horizon="intraday")
        self.assertEqual(state["direction"], "UNKNOWN")
        self.assertEqual(state["state"], "INSUFFICIENT_INDEPENDENT_CONFIRMATION")
        self.assertEqual(len(state["counted_evidence"]), 1)
        self.assertEqual(len(state["duplicate_origin_evidence"]), 1)

    def test_spot_plus_one_deduplicated_macro_origin_can_form_bullish_btc_thesis(self):
        state = assemble_btc_perception(
            [_spot_bullish(), _macro_event_bullish(), _generic_macro_bullish()],
            decision_at=_t(11),
            trade_horizon="intraday",
        )
        self.assertEqual(state["direction"], "BULLISH")
        self.assertEqual(state["state"], "COHERENT_DIRECTION_THESIS")
        origins = {row["causal_origin"] for row in state["counted_evidence"]}
        self.assertEqual(origins, {"SPOT_PRICE_STRUCTURE", "GLOBAL_RISK_LIQUIDITY"})
        self.assertEqual(len(state["duplicate_origin_evidence"]), 1)
        self.assertFalse(state["options_trade_generated"])
        self.assertFalse(state["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
