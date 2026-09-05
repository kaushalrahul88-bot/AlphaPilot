import unittest
from datetime import datetime, timezone

from app.crypto_btc_options_preflight import architecture_contract, options_route_preflight
from app.crypto_btc_perception import BtcOptionsMarketSnapshot, options_market_context


def _t():
    return datetime(2026, 9, 5, 4, 0, tzinfo=timezone.utc)


def _state(direction="BULLISH", **overrides):
    values = {
        "version": "BTC_SPECIALIST_PERCEPTION_V1",
        "asset": "BTC",
        "instrument_neutral": True,
        "direction": direction,
        "options_trade_generated": False,
        "futures_trade_generated": False,
    }
    values.update(overrides)
    return values


def _options(iv=0.5, skew=0.0, oi_ratio=1.0):
    return options_market_context(
        BtcOptionsMarketSnapshot(
            observed_at=_t(),
            atm_iv_percentile=iv,
            put_call_skew_25d=skew,
            put_call_oi_ratio=oi_ratio,
        )
    )


class BtcOptionsPreflightTests(unittest.TestCase):
    def test_bullish_btc_thesis_maps_only_to_call_side_candidate(self):
        result = options_route_preflight(btc_market_state=_state("BULLISH"), options_context=_options())
        self.assertEqual(result["instrument_type"], "OPTIONS")
        self.assertEqual(result["side_candidate"], "BUY_CALL")
        self.assertTrue(result["contract_selection_allowed"])
        self.assertFalse(result["trade_generated"])
        self.assertFalse(result["futures_route_invoked"])

    def test_bearish_btc_thesis_maps_only_to_put_side_candidate(self):
        result = options_route_preflight(btc_market_state=_state("BEARISH"), options_context=_options())
        self.assertEqual(result["side_candidate"], "BUY_PUT")
        self.assertEqual(result["instrument_type"], "OPTIONS")
        self.assertFalse(result["futures_trade_generated"])

    def test_unknown_underlying_direction_stops_before_contract_selection(self):
        result = options_route_preflight(btc_market_state=_state("UNKNOWN"), options_context=_options())
        self.assertEqual(result["status"], "NO_UNDERLYING_THESIS")
        self.assertEqual(result["side_candidate"], "NO_TRADE")
        self.assertFalse(result["contract_selection_allowed"])

    def test_missing_options_context_blocks_contract_selection_not_underlying_thesis(self):
        result = options_route_preflight(btc_market_state=_state("BULLISH"), options_context=None)
        self.assertEqual(result["status"], "OPTIONS_CONTEXT_MISSING")
        self.assertEqual(result["side_candidate"], "BUY_CALL")
        self.assertFalse(result["contract_selection_allowed"])
        self.assertFalse(result["trade_generated"])

    def test_extreme_iv_is_caution_not_futures_fallback(self):
        result = options_route_preflight(btc_market_state=_state("BULLISH"), options_context=_options(iv=0.97))
        self.assertIn("IV_EXTREME_HIGH", result["cautions"])
        self.assertEqual(result["instrument_type"], "OPTIONS")
        self.assertFalse(result["futures_route_invoked"])

    def test_state_containing_futures_trade_is_hard_rejected(self):
        with self.assertRaises(ValueError):
            options_route_preflight(
                btc_market_state=_state("BULLISH", futures_trade_generated=True),
                options_context=_options(),
            )

    def test_non_options_context_is_hard_rejected(self):
        wrong = _options()
        wrong = wrong.__class__(
            family="DERIVATIVES_POSITIONING",
            causal_origin=wrong.causal_origin,
            stance=wrong.stance,
            strength=wrong.strength,
            confidence=wrong.confidence,
            observed_at=wrong.observed_at,
            reason=wrong.reason,
            context_only=wrong.context_only,
            source=wrong.source,
            metadata=wrong.metadata,
        )
        with self.assertRaises(ValueError):
            options_route_preflight(btc_market_state=_state("BULLISH"), options_context=wrong)

    def test_architecture_contract_forbids_futures_fallback(self):
        contract = architecture_contract()
        self.assertFalse(contract["underlying_direction_created_by_options_chain"])
        self.assertTrue(contract["options_context_required_before_contract_selection"])
        self.assertFalse(contract["futures_route_invoked"])
        self.assertFalse(contract["futures_leverage_allowed"])
        self.assertFalse(contract["trade_generated_here"])
        self.assertFalse(contract["broker_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
