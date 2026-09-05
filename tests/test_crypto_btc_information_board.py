import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_information_board import architecture_contract, build_btc_information_board
from app.crypto_btc_perception import (
    BtcOptionsMarketSnapshot,
    BtcSpotStructureSnapshot,
    options_market_context,
    spot_structure_context,
)
from app.crypto_market_intelligence import Evidence, derivatives_context
from app.crypto_social_intelligence import SocialNarrativeSignal, social_narrative_context


def _t(hour=4, minute=0):
    return datetime(2026, 9, 5, hour, minute, tzinfo=timezone.utc)


def _spot():
    return spot_structure_context(
        BtcSpotStructureSnapshot(
            observed_at=_t(),
            price=100_000,
            return_1h_pct=0.7,
            return_4h_pct=1.8,
            return_24h_pct=3.5,
            close_location=0.78,
            volume_percentile=0.68,
            breakout_state="UPSIDE_CONFIRMED",
        )
    )


def _derivatives():
    return derivatives_context(
        observed_at=_t(),
        price_change_pct=1.8,
        oi_change_pct=5.0,
        funding_percentile=0.55,
    )


class BtcInformationBoardTests(unittest.TestCase):
    def test_board_makes_missing_lanes_explicit(self):
        board = build_btc_information_board([_spot(), _derivatives()], decision_at=_t(), trade_horizon="intraday")
        self.assertTrue(board["underlying_thesis_available"])
        self.assertIn("OPTIONS_MARKET", board["missing_lanes"])
        self.assertIn("ONCHAIN", board["missing_lanes"])
        self.assertFalse(board["options_translation_context_available"])
        self.assertFalse(board["missing_options_context_blocks_underlying_thesis"])

    def test_options_lane_is_visible_but_does_not_create_direction(self):
        options = options_market_context(
            BtcOptionsMarketSnapshot(
                observed_at=_t(),
                atm_iv_percentile=0.92,
                put_call_skew_25d=-7.0,
                put_call_oi_ratio=0.6,
            )
        )
        board = build_btc_information_board([_spot(), options], decision_at=_t(), trade_horizon="intraday")
        self.assertTrue(board["lane_status"]["OPTIONS_MARKET"]["available"])
        self.assertEqual(board["lane_status"]["OPTIONS_MARKET"]["directional_count"], 0)
        self.assertEqual(board["underlying_market_state"]["direction"], "UNKNOWN")

    def test_social_lane_is_visible_without_becoming_directional(self):
        social = social_narrative_context(
            SocialNarrativeSignal(
                signal_id="s1",
                event_key="evt:btc:narrative",
                assets=("BTC",),
                platform="X",
                first_seen_at=_t(3, 55),
                source_tier="D_COMMUNITY",
                claim="Fast-moving BTC narrative",
                mention_velocity_percentile=0.96,
                source_historical_reliability=0.7,
                truth_confidence=0.4,
                market_impact_confidence=0.9,
                direction_hint="BULLISH",
            ),
            decision_at=_t(),
        )
        board = build_btc_information_board([_spot(), social], decision_at=_t(), trade_horizon="intraday")
        self.assertTrue(board["lane_status"]["SOCIAL_NARRATIVE"]["available"])
        self.assertEqual(board["lane_status"]["SOCIAL_NARRATIVE"]["directional_count"], 0)
        self.assertEqual(board["underlying_market_state"]["direction"], "UNKNOWN")

    def test_stale_evidence_is_reported_not_silently_used(self):
        stale = Evidence(
            family="DERIVATIVES_POSITIONING",
            causal_origin="LEVERAGED_POSITIONING",
            stance="BULLISH",
            strength="MEDIUM",
            confidence=0.8,
            observed_at=_t() - timedelta(hours=8),
            reason="stale",
            context_only=False,
            source="GLOBAL_DERIVATIVES",
            metadata={},
        )
        board = build_btc_information_board([_spot(), stale], decision_at=_t(), trade_horizon="intraday")
        self.assertEqual(board["stale_evidence_count"], 1)
        self.assertFalse(board["lane_status"]["DERIVATIVES_POSITIONING"]["available"])
        self.assertEqual(board["underlying_market_state"]["direction"], "UNKNOWN")

    def test_board_never_generates_an_instrument_trade(self):
        board = build_btc_information_board([_spot(), _derivatives()], decision_at=_t(), trade_horizon="intraday")
        self.assertFalse(board["options_trade_generated"])
        self.assertFalse(board["futures_trade_generated"])
        self.assertFalse(board["broker_execution_enabled"])
        self.assertEqual(board["capital_committed"], 0)

    def test_architecture_contract_fails_closed_on_missing_data(self):
        contract = architecture_contract()
        self.assertTrue(contract["missing_data_is_explicit"])
        self.assertFalse(contract["missing_data_equals_neutral_vote"])
        self.assertTrue(contract["spot_lane_required_for_underlying_thesis"])
        self.assertFalse(contract["options_lane_can_create_underlying_direction"])
        self.assertFalse(contract["social_lane_can_create_underlying_direction"])


if __name__ == "__main__":
    unittest.main()
