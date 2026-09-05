import unittest
from datetime import date, datetime, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_fred_macro_pit import DATASET, architecture_contract, fred_macro_live_archive_record, provider_state_hash
from app.fred_btc_macro_regime_provider import FredBtcMacroRegimeCapture, FredSeriesVintageChange, SERIES


def _t(hour=12):
    return datetime(2026, 9, 5, hour, 0, tzinfo=timezone.utc)


def _series(series_id, *, latest, previous, change, unit):
    vintage = date(2026, 9, 5)
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


def _capture(*, first_seen=_t(), nasdaq_latest=101.0, historical=False):
    return FredBtcMacroRegimeCapture(
        vintage_date=date(2026, 9, 5),
        first_seen_at=first_seen,
        broad_usd=_series(SERIES["BROAD_USD"], latest=99.0, previous=100.0, change=-1.0, unit="PCT"),
        real_yield_10y=_series(SERIES["REAL_YIELD_10Y"], latest=2.20, previous=2.25, change=-5.0, unit="BPS"),
        nasdaq_composite=_series(
            SERIES["NASDAQ_COMPOSITE"],
            latest=nasdaq_latest,
            previous=100.0,
            change=(nasdaq_latest - 100.0),
            unit="PCT",
        ),
        vix=_series(SERIES["VIX"], latest=18.0, previous=20.0, change=-10.0, unit="PCT"),
        historical_vintage_reconstruction=historical,
        exact_intraday_availability_proven=not historical,
    ).validated()


class CryptoFredMacroPitTests(unittest.TestCase):
    def test_live_snapshot_archives_full_vintage_state_without_event_time_claim(self):
        record = fred_macro_live_archive_record(_capture())
        frozen = record.frozen_dict()
        self.assertEqual(frozen["dataset"], DATASET)
        self.assertEqual(frozen["provider"], "FRED_ALFRED")
        self.assertIsNone(frozen["event_at"])
        self.assertEqual(frozen["first_seen_at"], _t().isoformat())
        payload = frozen["payload"]
        self.assertEqual(payload["broad_usd"]["series_id"], "DTWEXBGS")
        self.assertEqual(payload["real_yield_10y"]["series_id"], "DFII10")
        self.assertEqual(payload["nasdaq_composite"]["series_id"], "NASDAQCOM")
        self.assertEqual(payload["vix"]["series_id"], "VIXCLS")
        self.assertFalse(payload["provider_event_time_available"])
        self.assertFalse(payload["exact_macro_release_time_proven"])
        self.assertTrue(payload["daily_regime_context_only"])
        self.assertFalse(payload["may_supply_second_intraday_origin"])
        self.assertFalse(payload["options_trade_generated"])
        self.assertFalse(payload["futures_trade_generated"])

    def test_historical_alfred_reconstruction_is_rejected_from_irrecoverable_archive(self):
        with self.assertRaises(ValueError):
            fred_macro_live_archive_record(_capture(historical=True))

    def test_unchanged_later_same_day_poll_is_idempotent_and_keeps_earliest_first_seen(self):
        ledger = ImmutableBtcPitLedger()
        first = fred_macro_live_archive_record(_capture(first_seen=_t(10)))
        later = fred_macro_live_archive_record(_capture(first_seen=_t(14)))
        first_result = ledger.insert_first_seen(first)
        later_result = ledger.insert_first_seen(later)
        self.assertEqual(first_result["status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(later_result["status"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(later_result["record"]["first_seen_at"], _t(10).isoformat())
        self.assertEqual(first.source_key, later.source_key)

    def test_changed_same_day_provider_state_creates_new_immutable_record(self):
        ledger = ImmutableBtcPitLedger()
        first = fred_macro_live_archive_record(_capture(first_seen=_t(10), nasdaq_latest=101.0))
        changed = fred_macro_live_archive_record(_capture(first_seen=_t(14), nasdaq_latest=102.0))
        self.assertNotEqual(provider_state_hash(_capture(nasdaq_latest=101.0)), provider_state_hash(_capture(nasdaq_latest=102.0)))
        self.assertNotEqual(first.source_key, changed.source_key)
        self.assertEqual(ledger.insert_first_seen(first)["status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(ledger.insert_first_seen(changed)["status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(len(ledger.visible_as_of(_t(15), dataset=DATASET)), 2)

    def test_live_snapshot_is_invisible_before_actual_first_seen(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(fred_macro_live_archive_record(_capture(first_seen=_t(12))))
        self.assertEqual(ledger.visible_as_of(_t(11), dataset=DATASET), [])
        self.assertEqual(len(ledger.visible_as_of(_t(12), dataset=DATASET)), 1)

    def test_live_vintage_must_match_first_seen_calendar_date(self):
        capture = _capture(first_seen=datetime(2026, 9, 6, 0, 1, tzinfo=timezone.utc))
        with self.assertRaises(ValueError):
            fred_macro_live_archive_record(capture)

    def test_architecture_separates_live_pit_from_reconstructible_vintage_history(self):
        contract = architecture_contract()
        self.assertFalse(contract["historical_vintage_reconstruction_admitted"])
        self.assertTrue(contract["live_first_seen_required"])
        self.assertTrue(contract["same_day_unchanged_repoll_is_idempotent"])
        self.assertTrue(contract["same_day_changed_provider_state_creates_new_record"])
        self.assertFalse(contract["provider_event_time_claimed"])
        self.assertFalse(contract["exact_macro_release_time_claimed"])
        self.assertFalse(contract["may_supply_second_intraday_origin"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
