import unittest
from datetime import date, datetime, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_fred_macro_capture_scheduler import FredMacroCapturePolicy, FredMacroPitCaptureScheduler, architecture_contract
from app.crypto_fred_macro_pit import DATASET
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


def _capture(first_seen, *, nasdaq_latest=101.0):
    return FredBtcMacroRegimeCapture(
        vintage_date=date(2026, 9, 5),
        first_seen_at=first_seen,
        broad_usd=_series(SERIES["BROAD_USD"], latest=99.0, previous=100.0, change=-1.0, unit="PCT"),
        real_yield_10y=_series(SERIES["REAL_YIELD_10Y"], latest=2.20, previous=2.25, change=-5.0, unit="BPS"),
        nasdaq_composite=_series(SERIES["NASDAQ_COMPOSITE"], latest=nasdaq_latest, previous=100.0, change=nasdaq_latest - 100.0, unit="PCT"),
        vix=_series(SERIES["VIX"], latest=18.0, previous=20.0, change=-10.0, unit="PCT"),
        historical_vintage_reconstruction=False,
        exact_intraday_availability_proven=True,
    ).validated()


class _Provider:
    def __init__(self, captures=None, error=None):
        self.captures = list(captures or [])
        self.error = error
        self.calls = 0

    def capture_regime(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        if not self.captures:
            raise AssertionError("no fake FRED capture queued")
        return self.captures.pop(0)


class CryptoFredMacroCaptureSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_scheduler_makes_no_provider_or_store_call(self):
        provider = _Provider([_capture(_t())])
        ledger = ImmutableBtcPitLedger()
        scheduler = FredMacroPitCaptureScheduler(provider=provider, store=ledger)
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "FRED_MACRO_CAPTURE_DISABLED")
        self.assertFalse(result["provider_called"])
        self.assertFalse(result["store_written"])
        self.assertEqual(provider.calls, 0)
        self.assertEqual(ledger.manifest()["record_count"], 0)
        self.assertFalse(result["trade_generated"])

    async def test_enabled_scheduler_archives_live_first_seen_snapshot_without_direction(self):
        provider = _Provider([_capture(_t(12))])
        ledger = ImmutableBtcPitLedger()
        scheduler = FredMacroPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=FredMacroCapturePolicy(enabled=True, poll_seconds=3600),
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "FRED_MACRO_CAPTURE_CYCLE_COMPLETE")
        self.assertTrue(result["provider_called"])
        self.assertTrue(result["store_written"])
        self.assertEqual(result["captured"][0]["dataset"], DATASET)
        self.assertEqual(result["captured"][0]["first_seen_at"], _t(12).isoformat())
        self.assertFalse(result["captured"][0]["direction_assigned"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(len(ledger.visible_as_of(_t(12), dataset=DATASET)), 1)

    async def test_same_provider_state_repoll_is_idempotent_and_changed_state_is_new_record(self):
        provider = _Provider([
            _capture(_t(10), nasdaq_latest=101.0),
            _capture(_t(12), nasdaq_latest=101.0),
            _capture(_t(14), nasdaq_latest=102.0),
        ])
        ledger = ImmutableBtcPitLedger()
        scheduler = FredMacroPitCaptureScheduler(
            provider=provider,
            store=ledger,
            policy=FredMacroCapturePolicy(enabled=True),
        )
        first = await scheduler.run_cycle()
        duplicate = await scheduler.run_cycle()
        changed = await scheduler.run_cycle()
        self.assertEqual(first["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(duplicate["captured"][0]["storage_status"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(changed["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(scheduler.inserted_records, 2)
        self.assertEqual(scheduler.idempotent_duplicates, 1)
        self.assertEqual(len(ledger.visible_as_of(_t(15), dataset=DATASET)), 2)

    async def test_provider_failure_is_explicit_and_not_neutralized(self):
        provider = _Provider(error=RuntimeError("fred unavailable"))
        scheduler = FredMacroPitCaptureScheduler(
            provider=provider,
            store=ImmutableBtcPitLedger(),
            policy=FredMacroCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "FRED_MACRO_CAPTURE_CYCLE_FAILURE")
        self.assertEqual(result["captured"], [])
        self.assertEqual(result["errors"][0]["error_type"], "RuntimeError")
        self.assertFalse(result["errors"][0]["missing_macro_treated_as_neutral"])
        self.assertFalse(result["trade_generated"])

    def test_policy_and_architecture_fail_closed(self):
        with self.assertRaises(ValueError):
            FredMacroCapturePolicy(poll_seconds=899).validated()
        contract = architecture_contract()
        self.assertFalse(contract["collection_enabled_by_default"])
        self.assertFalse(contract["scheduler_starts_at_import"])
        self.assertTrue(contract["provider_first_seen_controls_visibility"])
        self.assertFalse(contract["historical_reconstruction_performed_by_live_scheduler"])
        self.assertFalse(contract["missing_macro_treated_as_neutral"])
        self.assertFalse(contract["may_supply_second_intraday_origin"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
