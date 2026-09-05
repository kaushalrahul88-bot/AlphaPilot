import unittest
from datetime import date, datetime, timezone

from app.crypto_btc_perception import BtcSpotStructureSnapshot, assemble_btc_perception, spot_structure_context
from app.crypto_fred_macro_regime import architecture_contract, fred_macro_regime_context
from app.fred_btc_macro_regime_provider import FredBtcMacroRegimeCapture, FredSeriesVintageChange, SERIES


def _t(day=5, hour=12):
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)


def _series(series_id, vintage, latest, previous, change, unit):
    return FredSeriesVintageChange(
        series_id=series_id,
        vintage_date=vintage,
        latest_observation_date=date(2026, 9, 4),
        previous_observation_date=date(2026, 9, 3),
        latest_value=latest,
        previous_value=previous,
        realtime_start=vintage,
        realtime_end=vintage,
        change_value=change,
        change_unit=unit,
    ).validated()


def _capture(*, vintage=date(2026, 9, 5), first_seen=_t(), historical=False, supportive=True):
    if supportive:
        usd, ry, nasdaq, vix = -0.5, -7.0, 1.1, -8.0
    else:
        usd, ry, nasdaq, vix = 0.5, 7.0, -1.1, 8.0
    return FredBtcMacroRegimeCapture(
        vintage_date=vintage,
        first_seen_at=first_seen,
        broad_usd=_series(SERIES["BROAD_USD"], vintage, 99.5, 100.0, usd, "PCT"),
        real_yield_10y=_series(SERIES["REAL_YIELD_10Y"], vintage, 2.18, 2.25, ry, "BPS"),
        nasdaq_composite=_series(SERIES["NASDAQ_COMPOSITE"], vintage, 101.1, 100.0, nasdaq, "PCT"),
        vix=_series(SERIES["VIX"], vintage, 18.4, 20.0, vix, "PCT"),
        historical_vintage_reconstruction=historical,
        exact_intraday_availability_proven=not historical,
    ).validated()


def _bullish_spot():
    return spot_structure_context(BtcSpotStructureSnapshot(
        observed_at=_t(),
        price=100_000,
        return_1h_pct=0.8,
        return_4h_pct=2.0,
        return_24h_pct=3.0,
        close_location=0.8,
        volume_percentile=0.7,
        breakout_state="UPSIDE_CONFIRMED",
    ))


class CryptoFredMacroRegimeTests(unittest.TestCase):
    def test_supportive_daily_regime_is_context_only_unknown(self):
        evidence = fred_macro_regime_context(_capture(), decision_at=_t(hour=13))
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)
        self.assertEqual(evidence.metadata["regime"], "RISK_LIQUIDITY_SUPPORTIVE")
        self.assertEqual(evidence.metadata["supportive_votes"], 4)
        self.assertEqual(evidence.metadata["broad_usd_series"], "DTWEXBGS")
        self.assertFalse(evidence.metadata["standalone_direction_allowed"])
        self.assertFalse(evidence.metadata["may_supply_second_intraday_causal_origin"])
        self.assertFalse(evidence.metadata["btc_direction_generated"])

    def test_restrictive_daily_regime_is_also_context_only(self):
        evidence = fred_macro_regime_context(_capture(supportive=False), decision_at=_t(hour=13))
        self.assertEqual(evidence.metadata["regime"], "RISK_LIQUIDITY_RESTRICTIVE")
        self.assertEqual(evidence.metadata["restrictive_votes"], 4)
        self.assertEqual(evidence.stance, "UNKNOWN")
        self.assertTrue(evidence.context_only)

    def test_daily_regime_cannot_supply_second_btc_directional_origin(self):
        macro = fred_macro_regime_context(_capture(), decision_at=_t(hour=13))
        state = assemble_btc_perception([_bullish_spot(), macro], decision_at=_t(hour=13), trade_horizon="intraday")
        self.assertEqual(state["direction"], "UNKNOWN")
        self.assertEqual(state["state"], "INSUFFICIENT_INDEPENDENT_CONFIRMATION")

    def test_live_capture_first_seen_after_click_is_rejected(self):
        with self.assertRaises(ValueError):
            fred_macro_regime_context(_capture(first_seen=_t(hour=14)), decision_at=_t(hour=13))

    def test_historical_same_day_vintage_is_rejected_for_intraday_click(self):
        capture = _capture(
            vintage=date(2026, 9, 5),
            first_seen=_t(hour=18),
            historical=True,
        )
        with self.assertRaises(ValueError):
            fred_macro_regime_context(capture, decision_at=_t(hour=13))

    def test_prior_calendar_day_alfred_vintage_is_allowed_as_context(self):
        capture = _capture(
            vintage=date(2026, 9, 4),
            first_seen=_t(hour=18),
            historical=True,
        )
        evidence = fred_macro_regime_context(capture, decision_at=_t(hour=13))
        self.assertEqual(evidence.metadata["visibility_basis"], "PRIOR_CALENDAR_DAY_ALFRED_VINTAGE")
        self.assertFalse(evidence.metadata["exact_intraday_availability_proven"])
        self.assertEqual(evidence.observed_at, _t(hour=13))
        self.assertTrue(evidence.context_only)

    def test_architecture_separates_daily_regime_from_exact_macro_events(self):
        contract = architecture_contract()
        self.assertTrue(contract["daily_regime_context_only"])
        self.assertFalse(contract["daily_regime_may_create_btc_direction"])
        self.assertFalse(contract["daily_regime_may_supply_second_intraday_origin"])
        self.assertFalse(contract["historical_same_day_intraday_vintage_allowed"])
        self.assertTrue(contract["broad_usd_is_not_labeled_dxy"])
        self.assertTrue(contract["exact_macro_event_surprise_is_separate_lane"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
