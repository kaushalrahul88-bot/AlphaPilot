import unittest
from datetime import datetime, timezone

from app.crypto_bls_macro_release_capture_scheduler import (
    BlsExactReleasePitCaptureScheduler,
    BlsReleaseCapturePolicy,
    BlsReleaseCaptureTarget,
    architecture_contract,
)
from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_macro_event_intelligence import OfficialMacroRelease
from app.crypto_macro_event_pit import RELEASE_DATASET

CPI_URL = "https://www.bls.gov/news.release/cpi.nr0.htm"
EMP_URL = "https://www.bls.gov/news.release/empsit.nr0.htm"


def _release(event_key="BLS:CPI:2026-08", event_type="CPI", first_seen_hour=13):
    if event_type == "CPI":
        values = {"headline_mom_pct": 0.2, "core_mom_pct": 0.3}
        units = {"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"}
        reference = event_key.rsplit(":", 1)[-1]
    else:
        values = {"payroll_change_k": 162.0, "unemployment_rate_pct": 4.1, "avg_hourly_earnings_mom_pct": 0.3}
        units = {"payroll_change_k": "THOUSAND_PERSONS", "unemployment_rate_pct": "PERCENT", "avg_hourly_earnings_mom_pct": "PERCENT"}
        reference = event_key.rsplit(":", 1)[-1]
    return OfficialMacroRelease(
        event_key=event_key,
        event_type=event_type,
        reference_period=reference,
        release_at=datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc),
        first_seen_at=datetime(2026, 9, 11, first_seen_hour, 0, tzinfo=timezone.utc),
        official_source="BLS",
        official_source_ref=CPI_URL if event_type == "CPI" else EMP_URL,
        values=values,
        units=units,
    ).validated()


class _Provider:
    def __init__(self, releases=None, error=None):
        self.releases = list(releases or [])
        self.error = error
        self.calls = []

    def fetch_release(self, *, url, event_type):
        self.calls.append((url, event_type))
        if self.error is not None:
            raise self.error
        if not self.releases:
            raise AssertionError("no fake BLS release queued")
        return self.releases.pop(0)


class CryptoBlsMacroReleaseCaptureSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def test_target_requires_event_type_specific_expected_key_and_official_url(self):
        valid = BlsReleaseCaptureTarget(CPI_URL, "CPI", "BLS:CPI:2026-08").validated()
        self.assertEqual(valid.event_type, "CPI")
        with self.assertRaises(ValueError):
            BlsReleaseCaptureTarget("https://example.com/news.release/cpi.nr0.htm", "CPI", "BLS:CPI:2026-08").validated()
        with self.assertRaises(ValueError):
            BlsReleaseCaptureTarget(CPI_URL, "CPI", "BLS:EMPLOYMENT_SITUATION:2026-08").validated()
        with self.assertRaises(ValueError):
            BlsReleaseCaptureTarget(CPI_URL, "PPI", "BLS:PPI:2026-08").validated()

    def test_enabled_scheduler_requires_targets_and_rejects_duplicates(self):
        with self.assertRaises(ValueError):
            BlsExactReleasePitCaptureScheduler(
                provider=_Provider(),
                store=ImmutableBtcPitLedger(),
                targets=[],
                policy=BlsReleaseCapturePolicy(enabled=True),
            )
        target = BlsReleaseCaptureTarget(CPI_URL, "CPI", "BLS:CPI:2026-08")
        with self.assertRaises(ValueError):
            BlsExactReleasePitCaptureScheduler(
                provider=_Provider(),
                store=ImmutableBtcPitLedger(),
                targets=[target, target],
            )

    async def test_disabled_cycle_calls_nothing(self):
        provider = _Provider([_release()])
        ledger = ImmutableBtcPitLedger()
        scheduler = BlsExactReleasePitCaptureScheduler(
            provider=provider,
            store=ledger,
            targets=[],
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "BLS_EXACT_RELEASE_CAPTURE_DISABLED")
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(provider.calls, [])
        self.assertEqual(ledger.manifest()["record_count"], 0)
        self.assertFalse(result["trade_generated"])

    async def test_matching_release_is_archived_without_consensus_or_direction(self):
        provider = _Provider([_release()])
        ledger = ImmutableBtcPitLedger()
        scheduler = BlsExactReleasePitCaptureScheduler(
            provider=provider,
            store=ledger,
            targets=[BlsReleaseCaptureTarget(CPI_URL, "CPI", "BLS:CPI:2026-08")],
            policy=BlsReleaseCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "BLS_EXACT_RELEASE_CAPTURE_CYCLE_COMPLETE")
        self.assertEqual(result["provider_calls"], 1)
        self.assertEqual(result["captured"][0]["event_key"], "BLS:CPI:2026-08")
        self.assertEqual(result["captured"][0]["dataset"], RELEASE_DATASET)
        self.assertEqual(result["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertFalse(result["captured"][0]["consensus_present"])
        self.assertFalse(result["captured"][0]["surprise_direction_assigned"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(len(ledger.visible_as_of(datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc), dataset=RELEASE_DATASET)), 1)

    async def test_same_official_release_repoll_is_idempotent(self):
        provider = _Provider([_release(first_seen_hour=13), _release(first_seen_hour=14)])
        ledger = ImmutableBtcPitLedger()
        scheduler = BlsExactReleasePitCaptureScheduler(
            provider=provider,
            store=ledger,
            targets=[BlsReleaseCaptureTarget(CPI_URL, "CPI", "BLS:CPI:2026-08")],
            policy=BlsReleaseCapturePolicy(enabled=True),
        )
        first = await scheduler.run_cycle()
        second = await scheduler.run_cycle()
        self.assertEqual(first["captured"][0]["storage_status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(second["captured"][0]["storage_status"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(scheduler.inserted_records, 1)
        self.assertEqual(scheduler.idempotent_duplicates, 1)
        rows = ledger.visible_as_of(datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc), dataset=RELEASE_DATASET)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["first_seen_at"], datetime(2026, 9, 11, 13, 0, tzinfo=timezone.utc).isoformat())

    async def test_rolling_url_previous_release_is_rejected_without_archive(self):
        provider = _Provider([_release(event_key="BLS:CPI:2026-07")])
        ledger = ImmutableBtcPitLedger()
        scheduler = BlsExactReleasePitCaptureScheduler(
            provider=provider,
            store=ledger,
            targets=[BlsReleaseCaptureTarget(CPI_URL, "CPI", "BLS:CPI:2026-08")],
            policy=BlsReleaseCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "BLS_EXACT_RELEASE_CAPTURE_CYCLE_FAILURE")
        self.assertEqual(result["captured"], [])
        self.assertEqual(result["errors"][0]["expected_event_key"], "BLS:CPI:2026-08")
        self.assertIn("currently exposes", result["errors"][0]["message"])
        self.assertFalse(result["errors"][0]["wrong_or_missing_release_treated_as_neutral"])
        self.assertEqual(ledger.manifest()["record_count"], 0)

    async def test_one_target_failure_does_not_discard_another_valid_release(self):
        provider = _Provider([
            _release(event_key="BLS:CPI:2026-07"),
            _release(event_key="BLS:EMPLOYMENT_SITUATION:2026-08", event_type="EMPLOYMENT_SITUATION"),
        ])
        ledger = ImmutableBtcPitLedger()
        scheduler = BlsExactReleasePitCaptureScheduler(
            provider=provider,
            store=ledger,
            targets=[
                BlsReleaseCaptureTarget(CPI_URL, "CPI", "BLS:CPI:2026-08"),
                BlsReleaseCaptureTarget(EMP_URL, "EMPLOYMENT_SITUATION", "BLS:EMPLOYMENT_SITUATION:2026-08"),
            ],
            policy=BlsReleaseCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "BLS_EXACT_RELEASE_CAPTURE_CYCLE_PARTIAL_FAILURE")
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(len(result["captured"]), 1)
        self.assertEqual(result["captured"][0]["event_type"], "EMPLOYMENT_SITUATION")
        self.assertEqual(ledger.manifest()["record_count"], 1)

    async def test_provider_failure_is_explicit_and_not_neutralized(self):
        scheduler = BlsExactReleasePitCaptureScheduler(
            provider=_Provider(error=RuntimeError("bls unavailable")),
            store=ImmutableBtcPitLedger(),
            targets=[BlsReleaseCaptureTarget(CPI_URL, "CPI", "BLS:CPI:2026-08")],
            policy=BlsReleaseCapturePolicy(enabled=True),
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "BLS_EXACT_RELEASE_CAPTURE_CYCLE_FAILURE")
        self.assertEqual(result["errors"][0]["error_type"], "RuntimeError")
        self.assertFalse(result["errors"][0]["wrong_or_missing_release_treated_as_neutral"])
        self.assertFalse(result["trade_generated"])

    def test_policy_and_architecture_keep_capture_gated_and_non_directional(self):
        with self.assertRaises(ValueError):
            BlsReleaseCapturePolicy(poll_seconds=9).validated()
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertTrue(contract["expected_event_key_required"])
        self.assertFalse(contract["rolling_url_previous_release_may_be_archived_as_new_event"])
        self.assertTrue(contract["official_first_seen_preserved"])
        self.assertFalse(contract["wrong_or_missing_release_treated_as_neutral"])
        self.assertFalse(contract["consensus_supplied_by_scheduler"])
        self.assertFalse(contract["surprise_direction_assigned"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["automatic_startup_registration"])


if __name__ == "__main__":
    unittest.main()
