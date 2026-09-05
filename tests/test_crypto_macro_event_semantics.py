import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_macro_event_intelligence import MacroNumericSurprise
from app.crypto_macro_event_semantics import (
    MacroMarketReaction,
    architecture_contract,
    macro_event_evidence,
    normalize_macro_surprise,
)


def _now():
    return datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)


def _surprise(event_key, event_type, release_at, surprise):
    values = {key: float(value) for key, value in surprise.items()}
    return MacroNumericSurprise(
        event_key=event_key,
        event_type=event_type,
        release_at=release_at,
        release_first_seen_at=release_at + timedelta(seconds=2),
        consensus_first_seen_at=release_at - timedelta(minutes=30),
        actual=values,
        consensus={key: 0.0 for key in values},
        surprise=values,
        units={key: "TEST_UNIT" for key in values},
    ).validated()


def _cpi_current(headline, core):
    return _surprise(
        "BLS:CPI:CURRENT", "CPI", _now(),
        {"headline_mom_pct": headline, "core_mom_pct": core},
    )


def _cpi_history(count=20):
    rows = []
    for i in range(count):
        value = -0.20 + i * 0.02
        rows.append(_surprise(
            f"BLS:CPI:PRIOR:{i}",
            "CPI",
            _now() - timedelta(days=30 * (i + 1)),
            {"headline_mom_pct": value, "core_mom_pct": value * 0.9},
        ))
    return rows


def _employment_current(payroll, unemployment, earnings):
    return _surprise(
        "BLS:EMPLOYMENT:CURRENT", "EMPLOYMENT_SITUATION", _now(),
        {
            "payroll_change_k": payroll,
            "unemployment_rate_pct": unemployment,
            "avg_hourly_earnings_mom_pct": earnings,
        },
    )


def _employment_history(count=20):
    rows = []
    for i in range(count):
        centered = -1.0 + 2.0 * i / max(1, count - 1)
        rows.append(_surprise(
            f"BLS:EMPLOYMENT:PRIOR:{i}",
            "EMPLOYMENT_SITUATION",
            _now() - timedelta(days=30 * (i + 1)),
            {
                "payroll_change_k": centered * 100.0,
                "unemployment_rate_pct": centered * 0.2,
                "avg_hourly_earnings_mom_pct": centered * 0.1,
            },
        ))
    return rows


def _reaction(event_key, *, bullish, cross_assets=3, move_percentile=0.9, minutes=10, first_seen_after=1):
    sign = 1.0 if bullish else -1.0
    values = {
        "nasdaq_return_pct": 0.8 * sign,
        "broad_usd_return_pct": -0.3 * sign,
        "real_yield_change_bps": -4.0 * sign,
    }
    if cross_assets < 3:
        values["real_yield_change_bps"] = 4.0 * sign
    if cross_assets < 2:
        values["broad_usd_return_pct"] = 0.3 * sign
    observed = _now() + timedelta(minutes=minutes)
    return MacroMarketReaction(
        event_key=event_key,
        release_at=_now(),
        observed_at=observed,
        first_seen_at=observed + timedelta(seconds=first_seen_after),
        btc_return_pct=1.0 * sign,
        btc_abs_move_percentile=move_percentile,
        source="VERIFIED_CROSS_ASSET_MARKET_DATA",
        source_verified=True,
        **values,
    )


class CryptoMacroEventSemanticsTests(unittest.TestCase):
    def test_extreme_aligned_cpi_upside_is_prior_normalized_hawkish(self):
        normalized = normalize_macro_surprise(_cpi_current(0.5, 0.5), _cpi_history())
        self.assertEqual(normalized.semantic_state, "HAWKISH_SHOCK")
        self.assertGreaterEqual(normalized.metric_percentiles["headline_mom_pct"], 0.8)
        self.assertGreaterEqual(normalized.metric_percentiles["core_mom_pct"], 0.8)
        self.assertEqual(normalized.prior_sample_count, 20)
        self.assertEqual(normalized.direction, "UNKNOWN")
        self.assertFalse(normalized.standalone_direction_allowed)

    def test_extreme_aligned_cpi_downside_is_prior_normalized_dovish(self):
        normalized = normalize_macro_surprise(_cpi_current(-0.5, -0.5), _cpi_history())
        self.assertEqual(normalized.semantic_state, "DOVISH_SHOCK")
        self.assertLessEqual(normalized.metric_percentiles["headline_mom_pct"], 0.2)
        self.assertLessEqual(normalized.metric_percentiles["core_mom_pct"], 0.2)

    def test_conflicting_cpi_headline_and_core_remain_ambiguous(self):
        normalized = normalize_macro_surprise(_cpi_current(0.5, -0.5), _cpi_history())
        self.assertEqual(normalized.semantic_state, "MIXED_OR_AMBIGUOUS")

    def test_insufficient_prior_history_fails_closed(self):
        normalized = normalize_macro_surprise(_cpi_current(0.5, 0.5), _cpi_history(19))
        self.assertEqual(normalized.semantic_state, "INSUFFICIENT_PRIOR_HISTORY")
        self.assertEqual(normalized.metric_percentiles, {})

    def test_future_or_current_event_samples_do_not_enter_prior_distribution(self):
        history = _cpi_history(20)
        history.extend([
            _surprise("FUTURE", "CPI", _now() + timedelta(days=1), {"headline_mom_pct": 9, "core_mom_pct": 9}),
            _surprise("CURRENT-DUP", "CPI", _now(), {"headline_mom_pct": 9, "core_mom_pct": 9}),
        ])
        normalized = normalize_macro_surprise(_cpi_current(0.5, 0.5), history)
        self.assertEqual(normalized.prior_sample_count, 20)
        self.assertEqual(normalized.semantic_state, "HAWKISH_SHOCK")

    def test_employment_tightness_requires_multi_metric_alignment(self):
        hawkish = normalize_macro_surprise(
            _employment_current(180.0, -0.3, 0.2),
            _employment_history(),
        )
        self.assertEqual(hawkish.semantic_state, "HAWKISH_SHOCK")
        mixed = normalize_macro_surprise(
            _employment_current(180.0, 0.3, -0.2),
            _employment_history(),
        )
        self.assertEqual(mixed.semantic_state, "MIXED_OR_AMBIGUOUS")

    def test_hawkish_cpi_plus_aligned_market_reaction_becomes_one_bearish_macro_origin(self):
        normalized = normalize_macro_surprise(_cpi_current(0.5, 0.5), _cpi_history())
        reaction = _reaction(normalized.event_key, bullish=False)
        evidence = macro_event_evidence(
            normalized,
            reaction,
            decision_at=_now() + timedelta(minutes=11),
        )
        self.assertEqual(evidence.stance, "BEARISH")
        self.assertFalse(evidence.context_only)
        self.assertEqual(evidence.causal_origin, "GLOBAL_RISK_LIQUIDITY")
        self.assertEqual(evidence.metadata["macro_event_suborigin"], "OFFICIAL_MACRO_EVENT_SHOCK")
        self.assertEqual(evidence.metadata["causal_origin_dedup_group"], "GLOBAL_RISK_LIQUIDITY")
        self.assertEqual(evidence.metadata["cross_asset_alignment_count"], 3)
        self.assertFalse(evidence.metadata["standalone_macro_surprise_direction_allowed"])
        self.assertFalse(evidence.metadata["may_generate_options_trade"])
        self.assertFalse(evidence.metadata["may_generate_futures_trade"])

    def test_dovish_cpi_plus_aligned_market_reaction_becomes_one_bullish_macro_origin(self):
        normalized = normalize_macro_surprise(_cpi_current(-0.5, -0.5), _cpi_history())
        evidence = macro_event_evidence(
            normalized,
            _reaction(normalized.event_key, bullish=True),
            decision_at=_now() + timedelta(minutes=11),
        )
        self.assertEqual(evidence.stance, "BULLISH")
        self.assertFalse(evidence.context_only)

    def test_dramatic_surprise_without_two_cross_asset_confirmations_stays_unknown(self):
        normalized = normalize_macro_surprise(_cpi_current(0.5, 0.5), _cpi_history())
        evidence = macro_event_evidence(
            normalized,
            _reaction(normalized.event_key, bullish=False, cross_assets=1),
            decision_at=_now() + timedelta(minutes=11),
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_small_btc_event_move_stays_unknown_even_when_signs_align(self):
        normalized = normalize_macro_surprise(_cpi_current(0.5, 0.5), _cpi_history())
        evidence = macro_event_evidence(
            normalized,
            _reaction(normalized.event_key, bullish=False, move_percentile=0.5),
            decision_at=_now() + timedelta(minutes=11),
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_reaction_outside_allowed_window_stays_unknown(self):
        normalized = normalize_macro_surprise(_cpi_current(0.5, 0.5), _cpi_history())
        evidence = macro_event_evidence(
            normalized,
            _reaction(normalized.event_key, bullish=False, minutes=31),
            decision_at=_now() + timedelta(minutes=32),
        )
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_reaction_not_yet_first_seen_by_click_is_rejected(self):
        normalized = normalize_macro_surprise(_cpi_current(0.5, 0.5), _cpi_history())
        reaction = _reaction(normalized.event_key, bullish=False)
        with self.assertRaises(ValueError):
            macro_event_evidence(
                normalized,
                reaction,
                decision_at=reaction.observed_at,
            )

    def test_non_finite_market_reaction_is_rejected(self):
        reaction = _reaction("BLS:CPI:CURRENT", bullish=False)
        with self.assertRaises(ValueError):
            MacroMarketReaction(
                event_key=reaction.event_key,
                release_at=reaction.release_at,
                observed_at=reaction.observed_at,
                first_seen_at=reaction.first_seen_at,
                btc_return_pct=float("nan"),
                nasdaq_return_pct=reaction.nasdaq_return_pct,
                broad_usd_return_pct=reaction.broad_usd_return_pct,
                real_yield_change_bps=reaction.real_yield_change_bps,
                btc_abs_move_percentile=reaction.btc_abs_move_percentile,
                source=reaction.source,
                source_verified=True,
            ).validated()

    def test_fomc_stays_unsupported_until_separate_semantic_classifier_exists(self):
        current = _surprise(
            "FED:FOMC:CURRENT",
            "FOMC_STATEMENT",
            _now(),
            {"target_midpoint_pct": 0.25},
        )
        normalized = normalize_macro_surprise(current, [])
        self.assertEqual(normalized.semantic_state, "UNSUPPORTED_EVENT_TYPE")
        self.assertEqual(normalized.direction, "UNKNOWN")

    def test_architecture_is_prior_normalized_trade_separated_and_macro_deduplicated(self):
        contract = architecture_contract()
        self.assertEqual(contract["supported_directional_events"], ["CPI", "EMPLOYMENT_SITUATION"])
        self.assertFalse(contract["fixed_raw_surprise_thresholds_used"])
        self.assertTrue(contract["strictly_prior_surprise_distribution_required"])
        self.assertEqual(contract["default_min_prior_samples"], 20)
        self.assertTrue(contract["cpi_headline_and_core_alignment_required"])
        self.assertTrue(contract["employment_multi_metric_alignment_required"])
        self.assertFalse(contract["fomc_numeric_or_text_direction_supported"])
        self.assertTrue(contract["btc_market_confirmation_required"])
        self.assertEqual(contract["minimum_cross_asset_confirmations"], 2)
        self.assertTrue(contract["btc_event_move_prior_percentile_required"])
        self.assertTrue(contract["macro_event_can_be_one_independent_causal_origin_vs_spot_or_derivatives"])
        self.assertTrue(contract["shares_causal_origin_with_generic_macro_lane"])
        self.assertFalse(contract["same_macro_cause_may_be_double_counted"])
        self.assertFalse(contract["macro_event_directly_generates_options_trade"])
        self.assertFalse(contract["macro_event_directly_generates_futures_trade"])


if __name__ == "__main__":
    unittest.main()
