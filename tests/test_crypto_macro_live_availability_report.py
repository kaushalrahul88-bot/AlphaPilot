import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_macro_live_availability_audit import (
    MacroLiveAvailabilityAttempt,
    MacroLiveAvailabilityPolicy,
)
from app.crypto_macro_live_availability_report import (
    architecture_contract,
    build_macro_live_availability_report,
    build_macro_live_availability_report_from_store,
    macro_live_availability_report_payload,
)


BASE_RELEASE = datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)
GENERATED = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)


def _attempt(
    event_key,
    release_at,
    *,
    event_type="CPI",
    attempted_seconds=30,
    completed_seconds=None,
    status="AVAILABLE_WITHIN_LATENCY",
):
    completed_seconds = attempted_seconds if completed_seconds is None else completed_seconds
    window_end = release_at + timedelta(minutes=10)
    available = status in {"AVAILABLE_WITHIN_LATENCY", "AVAILABLE_TOO_LATE"}
    return MacroLiveAvailabilityAttempt(
        event_key=event_key,
        event_type=event_type,
        release_at=release_at,
        reaction_window_end=window_end,
        attempted_at=window_end + timedelta(seconds=attempted_seconds),
        completed_at=window_end + timedelta(seconds=completed_seconds),
        status=status,
        availability_latency_seconds=float(completed_seconds),
        nasdaq_contract_ticker="NQU6" if available else None,
        euro_fx_contract_ticker="6EU6" if available else None,
        failure_kind=None if available else "ValueError",
    )


class _ReadOnlyStore:
    def __init__(self, rows):
        self.rows = list(rows)
        self.list_calls = 0
        self.initialize_calls = 0
        self.insert_calls = 0

    async def list_attempts(self):
        self.list_calls += 1
        return list(self.rows)

    async def initialize(self):
        self.initialize_calls += 1
        raise AssertionError("report must not initialize store")

    async def insert_attempt(self, attempt):
        self.insert_calls += 1
        raise AssertionError("report must not write store")


class CryptoMacroLiveAvailabilityReportTests(unittest.TestCase):
    def test_empty_history_is_descriptive_and_insufficient(self):
        report = build_macro_live_availability_report([], generated_at=GENERATED)
        self.assertEqual(report.qualification.status, "INSUFFICIENT_PROSPECTIVE_EVENTS")
        self.assertEqual(report.unique_events_observed, 0)
        self.assertEqual(report.successful_event_count, 0)
        self.assertEqual(report.too_late_only_event_count, 0)
        self.assertEqual(report.unavailable_only_event_count, 0)
        self.assertIsNone(report.successful_event_latency_min_seconds)
        self.assertIsNone(report.successful_event_latency_median_seconds)
        self.assertIsNone(report.successful_event_latency_max_seconds)
        self.assertFalse(report.manual_review_required)
        self.assertFalse(report.live_confirmation_enabled)

    def test_duplicate_attempts_do_not_increase_unique_event_sample(self):
        release = BASE_RELEASE
        rows = [
            _attempt("BLS:CPI:2026-06", release, attempted_seconds=20),
            _attempt("BLS:CPI:2026-06", release, attempted_seconds=30),
            _attempt("BLS:CPI:2026-06", release, attempted_seconds=40),
        ]
        report = build_macro_live_availability_report(rows, generated_at=GENERATED)
        self.assertEqual(report.unique_events_observed, 1)
        self.assertEqual(report.successful_event_count, 1)
        self.assertEqual(report.events[0].attempt_count, 3)
        self.assertEqual(report.qualification.status, "INSUFFICIENT_PROSPECTIVE_EVENTS")

    def test_mixed_history_preserves_success_delay_and_failure_classes(self):
        rows = [
            _attempt("BLS:CPI:2026-06", BASE_RELEASE, attempted_seconds=25),
            _attempt(
                "BLS:CPI:2026-07",
                BASE_RELEASE + timedelta(days=31),
                attempted_seconds=130,
                status="AVAILABLE_TOO_LATE",
            ),
            _attempt(
                "BLS:EMPLOYMENT:2026-08",
                BASE_RELEASE + timedelta(days=56),
                event_type="EMPLOYMENT_SITUATION",
                attempted_seconds=45,
                status="UNAVAILABLE_OR_PROVIDER_ERROR",
            ),
        ]
        report = build_macro_live_availability_report(rows, generated_at=GENERATED)
        self.assertEqual(report.unique_events_observed, 3)
        self.assertEqual(report.successful_event_count, 1)
        self.assertEqual(report.too_late_only_event_count, 1)
        self.assertEqual(report.unavailable_only_event_count, 1)
        self.assertEqual(report.event_type_counts, {"CPI": 2, "EMPLOYMENT_SITUATION": 1})
        self.assertEqual(report.event_type_success_counts, {"CPI": 1, "EMPLOYMENT_SITUATION": 0})
        self.assertEqual(report.qualification.status, "NOT_QUALIFIED")
        self.assertFalse(report.manual_review_required)

    def test_earliest_in_latency_success_drives_success_latency_statistics(self):
        release1 = BASE_RELEASE
        release2 = BASE_RELEASE + timedelta(days=31)
        release3 = BASE_RELEASE + timedelta(days=62)
        rows = [
            _attempt("BLS:CPI:2026-06", release1, attempted_seconds=50),
            _attempt("BLS:CPI:2026-06", release1, attempted_seconds=20),
            _attempt("BLS:CPI:2026-07", release2, attempted_seconds=40),
            _attempt("BLS:CPI:2026-08", release3, attempted_seconds=30),
        ]
        report = build_macro_live_availability_report(rows, generated_at=GENERATED)
        self.assertEqual(report.qualification.status, "QUALIFIED_FOR_MANUAL_REVIEW")
        self.assertTrue(report.manual_review_required)
        self.assertEqual(report.successful_event_count, 3)
        self.assertEqual(report.successful_event_latency_min_seconds, 20.0)
        self.assertEqual(report.successful_event_latency_median_seconds, 30.0)
        self.assertEqual(report.successful_event_latency_max_seconds, 40.0)
        first = report.events[0]
        self.assertEqual(first.earliest_in_latency_success_seconds, 20.0)
        self.assertTrue(first.has_in_latency_success)
        self.assertFalse(report.live_confirmation_enabled)
        self.assertFalse(report.runtime_mutated)

    def test_failure_then_success_for_same_event_counts_one_successful_event(self):
        release = BASE_RELEASE
        rows = [
            _attempt(
                "BLS:CPI:2026-06",
                release,
                attempted_seconds=20,
                status="UNAVAILABLE_OR_PROVIDER_ERROR",
            ),
            _attempt("BLS:CPI:2026-06", release, attempted_seconds=40),
        ]
        report = build_macro_live_availability_report(rows, generated_at=GENERATED)
        event = report.events[0]
        self.assertEqual(report.unique_events_observed, 1)
        self.assertEqual(report.successful_event_count, 1)
        self.assertTrue(event.has_provider_failure)
        self.assertTrue(event.has_in_latency_success)
        self.assertEqual(event.terminal_status, "AVAILABLE_WITHIN_LATENCY")

    def test_event_identity_collision_fails_closed(self):
        rows = [
            _attempt("EVENT-X", BASE_RELEASE),
            _attempt(
                "EVENT-X",
                BASE_RELEASE + timedelta(days=1),
                event_type="EMPLOYMENT_SITUATION",
            ),
        ]
        with self.assertRaisesRegex(ValueError, "multiple event identities"):
            build_macro_live_availability_report(rows, generated_at=GENERATED)

    def test_report_respects_custom_qualification_policy(self):
        rows = [
            _attempt("BLS:CPI:2026-06", BASE_RELEASE, attempted_seconds=50),
            _attempt("BLS:CPI:2026-07", BASE_RELEASE + timedelta(days=31), attempted_seconds=50),
        ]
        report = build_macro_live_availability_report(
            rows,
            policy=MacroLiveAvailabilityPolicy(max_latency_seconds=60, min_unique_events=2),
            generated_at=GENERATED,
        )
        self.assertEqual(report.qualification.status, "QUALIFIED_FOR_MANUAL_REVIEW")
        self.assertTrue(report.manual_review_required)
        self.assertFalse(report.live_confirmation_enabled)

    def test_report_does_not_read_market_direction_pnl_or_trade_outcomes(self):
        report = build_macro_live_availability_report(
            [_attempt("BLS:CPI:2026-06", BASE_RELEASE)],
            generated_at=GENERATED,
        )
        rendered = repr(report)
        self.assertNotIn("btc_return", rendered.lower())
        self.assertNotIn("pnl", rendered.lower())
        self.assertFalse(report.direction_generated)
        self.assertFalse(report.options_trade_generated)
        self.assertFalse(report.futures_trade_generated)

    def test_api_payload_is_explicitly_whitelisted_and_contains_no_credentials(self):
        report = build_macro_live_availability_report(
            [_attempt("BLS:CPI:2026-06", BASE_RELEASE)],
            generated_at=GENERATED,
        )
        payload = macro_live_availability_report_payload(report)
        self.assertEqual(payload["status"], "MACRO_LIVE_AVAILABILITY_REPORT_READY")
        self.assertEqual(payload["generated_at"], GENERATED.isoformat())
        self.assertEqual(payload["coverage"]["unique_events_observed"], 1)
        self.assertEqual(payload["events"][0]["event_key"], "BLS:CPI:2026-06")
        self.assertFalse(payload["qualification"]["auto_enable_live_confirmation"])
        self.assertFalse(payload["live_confirmation_enabled"])
        self.assertFalse(payload["provider_network_called"])
        self.assertFalse(payload["store_written"])
        self.assertFalse(payload["runtime_mutated"])
        rendered = repr(payload).lower()
        self.assertNotIn("database_url", rendered)
        self.assertNotIn("api_key", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("secret", rendered)

    def test_architecture_is_read_only_manual_review_only_and_nontrading(self):
        contract = architecture_contract()
        self.assertTrue(contract["read_only"])
        self.assertFalse(contract["provider_network_call_allowed"])
        self.assertFalse(contract["store_write_allowed"])
        self.assertFalse(contract["store_initialization_allowed"])
        self.assertFalse(contract["runtime_mutation_allowed"])
        self.assertFalse(contract["duplicate_attempts_increase_unique_event_sample"])
        self.assertFalse(contract["event_identity_collision_allowed"])
        self.assertFalse(contract["market_direction_or_returns_required"])
        self.assertFalse(contract["pnl_or_option_outcomes_read"])
        self.assertTrue(contract["qualification_reused_from_core_audit"])
        self.assertTrue(contract["qualified_state_requires_manual_review"])
        self.assertFalse(contract["live_confirmation_auto_enabled"])
        self.assertFalse(contract["api_payload_contains_credentials"])
        self.assertFalse(contract["direction_generated"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


class CryptoMacroLiveAvailabilityReportAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_report_path_only_reads_list_attempts(self):
        store = _ReadOnlyStore([
            _attempt("BLS:CPI:2026-06", BASE_RELEASE),
        ])
        report = await build_macro_live_availability_report_from_store(
            store,
            generated_at=GENERATED,
        )
        self.assertEqual(store.list_calls, 1)
        self.assertEqual(store.initialize_calls, 0)
        self.assertEqual(store.insert_calls, 0)
        self.assertEqual(report.unique_events_observed, 1)
        self.assertFalse(report.provider_network_called)
        self.assertFalse(report.store_written)

    async def test_store_must_return_list(self):
        class BadStore:
            async def list_attempts(self):
                return ()

        with self.assertRaisesRegex(ValueError, "must return a list"):
            await build_macro_live_availability_report_from_store(BadStore(), generated_at=GENERATED)


if __name__ == "__main__":
    unittest.main()
