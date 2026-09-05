import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_macro_event_semantics import (
    MacroMarketReaction,
    NormalizedMacroSurprise,
    architecture_contract,
    macro_event_evidence,
)


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
OBSERVED = RELEASE + timedelta(minutes=10)
SEEN = OBSERVED + timedelta(seconds=1)


def _hawkish_state():
    return NormalizedMacroSurprise(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        release_at=RELEASE,
        semantic_state="HAWKISH_SHOCK",
        metric_percentiles={"headline_mom_pct": 0.95, "core_mom_pct": 0.90},
        prior_sample_count=24,
        lower_percentile=0.20,
        upper_percentile=0.80,
    ).validated()


class CryptoMacroUsdProxySemanticsTests(unittest.TestCase):
    def test_inverse_eurusd_proxy_can_fill_one_usd_confirmation_dimension(self):
        reaction = MacroMarketReaction(
            event_key="BLS:CPI:2026-08",
            release_at=RELEASE,
            observed_at=OBSERVED,
            first_seen_at=SEEN,
            btc_return_pct=-1.1,
            nasdaq_return_pct=-0.8,
            broad_usd_return_pct=None,
            real_yield_change_bps=None,
            btc_abs_move_percentile=0.92,
            source="COINDCX_PLUS_MASSIVE_FUTURES",
            source_verified=True,
            usd_strength_proxy_return_pct=0.35,
            usd_strength_proxy_kind="INVERSE_EURUSD_FUTURES_6E",
        ).validated()
        evidence = macro_event_evidence(
            _hawkish_state(), reaction, decision_at=SEEN + timedelta(seconds=1)
        )
        self.assertEqual(evidence.stance, "BEARISH")
        self.assertFalse(evidence.context_only)
        self.assertEqual(evidence.metadata["cross_asset_alignment_count"], 2)
        self.assertEqual(
            evidence.metadata["usd_confirmation_dimension_source"],
            "PROXY:INVERSE_EURUSD_FUTURES_6E",
        )
        self.assertFalse(evidence.metadata["broad_usd_and_proxy_counted_separately"])

    def test_direct_broad_usd_and_proxy_never_count_as_two_dimensions(self):
        reaction = MacroMarketReaction(
            event_key="BLS:CPI:2026-08",
            release_at=RELEASE,
            observed_at=OBSERVED,
            first_seen_at=SEEN,
            btc_return_pct=-1.1,
            nasdaq_return_pct=None,
            broad_usd_return_pct=0.25,
            real_yield_change_bps=None,
            btc_abs_move_percentile=0.92,
            source="VERIFIED_MARKET_DATA",
            source_verified=True,
            usd_strength_proxy_return_pct=0.40,
            usd_strength_proxy_kind="INVERSE_EURUSD_FUTURES_6E",
        ).validated()
        evidence = macro_event_evidence(
            _hawkish_state(), reaction, decision_at=SEEN + timedelta(seconds=1)
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_direct_broad_usd_takes_precedence_over_conflicting_proxy(self):
        reaction = MacroMarketReaction(
            event_key="BLS:CPI:2026-08",
            release_at=RELEASE,
            observed_at=OBSERVED,
            first_seen_at=SEEN,
            btc_return_pct=-1.1,
            nasdaq_return_pct=-0.8,
            broad_usd_return_pct=-0.25,
            real_yield_change_bps=None,
            btc_abs_move_percentile=0.92,
            source="VERIFIED_MARKET_DATA",
            source_verified=True,
            usd_strength_proxy_return_pct=0.40,
            usd_strength_proxy_kind="INVERSE_EURUSD_FUTURES_6E",
        ).validated()
        evidence = macro_event_evidence(
            _hawkish_state(), reaction, decision_at=SEEN + timedelta(seconds=1)
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_proxy_kind_is_required_and_cannot_exist_without_proxy_value(self):
        with self.assertRaisesRegex(ValueError, "usd_strength_proxy_kind is required"):
            MacroMarketReaction(
                event_key="BLS:CPI:2026-08",
                release_at=RELEASE,
                observed_at=OBSERVED,
                first_seen_at=SEEN,
                btc_return_pct=-1.0,
                nasdaq_return_pct=-0.5,
                broad_usd_return_pct=None,
                real_yield_change_bps=None,
                btc_abs_move_percentile=0.9,
                source="VERIFIED",
                source_verified=True,
                usd_strength_proxy_return_pct=0.2,
            ).validated()
        with self.assertRaisesRegex(ValueError, "cannot be supplied"):
            MacroMarketReaction(
                event_key="BLS:CPI:2026-08",
                release_at=RELEASE,
                observed_at=OBSERVED,
                first_seen_at=SEEN,
                btc_return_pct=-1.0,
                nasdaq_return_pct=-0.5,
                broad_usd_return_pct=None,
                real_yield_change_bps=None,
                btc_abs_move_percentile=0.9,
                source="VERIFIED",
                source_verified=True,
                usd_strength_proxy_kind="INVERSE_EURUSD_FUTURES_6E",
            ).validated()

    def test_architecture_allows_only_one_usd_confirmation_dimension(self):
        contract = architecture_contract()
        self.assertEqual(contract["version"], "CRYPTO_MACRO_EVENT_SEMANTICS_V3")
        self.assertTrue(contract["verified_usd_strength_proxy_may_fill_usd_dimension"])
        self.assertFalse(contract["direct_and_proxy_usd_may_double_count"])
        self.assertEqual(contract["minimum_cross_asset_confirmations"], 2)
        self.assertFalse(contract["macro_event_directly_generates_options_trade"])
        self.assertFalse(contract["macro_event_directly_generates_futures_trade"])


if __name__ == "__main__":
    unittest.main()
