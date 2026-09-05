import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_macro_live_availability_audit import MacroLiveAvailabilityAttempt
from app.crypto_macro_live_availability_scheduler import (
    MacroLiveAvailabilityAuditScheduler,
    MacroLiveAvailabilityCapturePolicy,
    MacroLiveAvailabilityTarget,
    architecture_contract,
)
from app.massive_macro_futures_reaction_provider import (
    MassiveMacroFuturesReaction,
    MassiveMacroFuturesReactionPolicy,
    SelectedFuturesContract,
)


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
WINDOW_END = RELEASE + timedelta(minutes=10)
TARGET = MacroLiveAvailabilityTarget(
    event_key="BLS:CPI:2026-08",
    event_type="CPI",
    release_at=RELEASE,
)


def _contract(product, ticker, release_at, reference_close):
    return SelectedFuturesContract(
        product_code=product,
        ticker=ticker,
        trading_venue="XCME",
        event_date=release_at.date().isoformat(),
        days_to_maturity=7,
        pre_release_volume=100.0,
        selection_window_start=release_at - timedelta(minutes=30),
        selection_window_end=release_at,
        reference_close=reference_close,
    )


def _reaction(event_key, event_type, release_at, retrieved_at):
    observed = release_at + timedelta(minutes=10)
    return MassiveMacroFuturesReaction(
        event_key=event_key,
        event_type=event_type,
        release_at=release_at,
        observed_at=observed,
        reconstructible_available_at=observed,
        retrieved_at=max(retrieved_at, observed),
        nasdaq_futures_return_pct=-0.5,
        eurusd_futures_return_pct=-0.2,
        usd_strength_proxy_return_pct=0.2,
        nasdaq_contract=_contract("NQ", "NQU6", release_at, 20000.0),
        euro_fx_contract=_contract("6E", "6EU6", release_at, 1.10),
    )


class _MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class _FakeProvider:
    def __init__(self, clock, *, fail=False):
        self.policy = MassiveMacroFuturesReactionPolicy(
            enabled=True,
            api_key="KEY",
            reaction_window_minutes=10,
        )
        self.clock = clock
        self.fail = fail
        self.calls = []

    def fetch_reaction(self, *, event_key, event_type, release_at):
        self.calls.append((event_key, event_type, release_at))
        if self.fail:
            raise ValueError("bar unavailable")
        return _reaction(event_key, event_type, release_at, self.clock())


class _FakeStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.insert_calls = 0
        self.list_calls = 0

    def list_attempts(self):
        self.list_calls += 1
        return list(self.rows)

    def insert_attempt(self, attempt):
        self.insert_calls += 1
        for existing in self.rows:
            if existing.fingerprint() == attempt.fingerprint():
                return {"status": "IDEMPOTENT_AVAILABILITY_ATTEMPT"}
        self.rows.append(attempt)
        return {"status": "INSERTED_AVAILABILITY_ATTEMPT"}


def _stored_attempt(event_key, release_at, *, attempted_seconds=30, status="AVAILABLE_WITHIN_LATENCY"):
    end = release_at + timedelta(minutes=10)
    success = status in {"AVAILABLE_WITHIN_LATENCY", "AVAILABLE_TOO_LATE"}
    return MacroLiveAvailabilityAttempt(
        event_key=event_key,
        event_type="CPI",
        release_at=release_at,
        reaction_window_end=end,
        attempted_at=end + timedelta(seconds=attempted_seconds),
        completed_at=end + timedelta(seconds=attempted_seconds),
        status=status,
        availability_latency_seconds=float(attempted_seconds),
        nasdaq_contract_ticker="NQU6" if success else None,
        euro_fx_contract_ticker="6EU6" if success else None,
        failure_kind=None if success else "ValueError",
    )


class CryptoMacroLiveAvailabilitySchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_scheduler_calls_nothing(self):
        clock = _MutableClock(WINDOW_END + timedelta(seconds=30))
        provider = _FakeProvider(clock)
        store = _FakeStore()
        scheduler = MacroLiveAvailabilityAuditScheduler(
            provider=provider,
            store=store,
            targets=[TARGET],
            policy=MacroLiveAvailabilityCapturePolicy(enabled=False),
            clock=clock,
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["status"], "MACRO_LIVE_AVAILABILITY_AUDIT_DISABLED")
        self.assertEqual(provider.calls, [])
        self.assertEqual(store.list_calls, 0)
        self.assertEqual(store.insert_calls, 0)
        self.assertFalse(result["live_confirmation_enabled"])

    async def test_before_reaction_window_is_pending_without_provider_call(self):
        clock = _MutableClock(WINDOW_END - timedelta(seconds=1))
        provider = _FakeProvider(clock)
        store = _FakeStore()
        scheduler = MacroLiveAvailabilityAuditScheduler(
            provider=provider,
            store=store,
            targets=[TARGET],
            policy=MacroLiveAvailabilityCapturePolicy(enabled=True),
            clock=clock,
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["pending_targets"], [TARGET.event_key])
        self.assertEqual(provider.calls, [])
        self.assertEqual(store.insert_calls, 0)

    async def test_success_inside_latency_is_persisted_and_event_closes(self):
        clock = _MutableClock(WINDOW_END + timedelta(seconds=30))
        provider = _FakeProvider(clock)
        store = _FakeStore()
        scheduler = MacroLiveAvailabilityAuditScheduler(
            provider=provider,
            store=store,
            targets=[TARGET],
            policy=MacroLiveAvailabilityCapturePolicy(enabled=True),
            clock=clock,
        )
        result = await scheduler.run_cycle()
        self.assertTrue(result["provider_called"])
        self.assertTrue(result["store_written"])
        self.assertEqual(result["attempts"][0]["status"], "AVAILABLE_WITHIN_LATENCY")
        self.assertEqual(result["closed_targets"], [TARGET.event_key])
        self.assertFalse(result["live_confirmation_enabled"])
        self.assertEqual(store.insert_calls, 1)

    async def test_failed_attempt_can_retry_inside_latency_budget_after_poll_interval(self):
        clock = _MutableClock(WINDOW_END + timedelta(seconds=30))
        provider = _FakeProvider(clock, fail=True)
        store = _FakeStore()
        scheduler = MacroLiveAvailabilityAuditScheduler(
            provider=provider,
            store=store,
            targets=[TARGET],
            policy=MacroLiveAvailabilityCapturePolicy(enabled=True, poll_seconds=15),
            clock=clock,
        )
        first = await scheduler.run_cycle()
        self.assertEqual(first["attempts"][0]["status"], "UNAVAILABLE_OR_PROVIDER_ERROR")
        self.assertNotIn(TARGET.event_key, first["closed_targets"])
        provider.fail = False
        clock.value = WINDOW_END + timedelta(seconds=40)
        throttled = await scheduler.run_cycle()
        self.assertFalse(throttled["provider_called"])
        clock.value = WINDOW_END + timedelta(seconds=46)
        second = await scheduler.run_cycle()
        self.assertTrue(second["provider_called"])
        self.assertEqual(second["attempts"][0]["status"], "AVAILABLE_WITHIN_LATENCY")
        self.assertEqual(len(provider.calls), 2)

    async def test_stored_success_survives_scheduler_restart_and_prevents_repoll(self):
        success = _stored_attempt(TARGET.event_key, RELEASE)
        clock = _MutableClock(WINDOW_END + timedelta(seconds=60))
        provider = _FakeProvider(clock)
        store = _FakeStore([success])
        scheduler = MacroLiveAvailabilityAuditScheduler(
            provider=provider,
            store=store,
            targets=[TARGET],
            policy=MacroLiveAvailabilityCapturePolicy(enabled=True),
            clock=clock,
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["closed_targets"], [TARGET.event_key])
        self.assertEqual(provider.calls, [])
        self.assertEqual(store.insert_calls, 0)

    async def test_one_terminal_post_deadline_attempt_is_allowed_then_closed(self):
        prior = _stored_attempt(
            TARGET.event_key,
            RELEASE,
            attempted_seconds=90,
            status="UNAVAILABLE_OR_PROVIDER_ERROR",
        )
        clock = _MutableClock(WINDOW_END + timedelta(seconds=121))
        provider = _FakeProvider(clock)
        store = _FakeStore([prior])
        scheduler = MacroLiveAvailabilityAuditScheduler(
            provider=provider,
            store=store,
            targets=[TARGET],
            policy=MacroLiveAvailabilityCapturePolicy(enabled=True, max_latency_seconds=120),
            clock=clock,
        )
        first = await scheduler.run_cycle()
        self.assertEqual(first["attempts"][0]["status"], "AVAILABLE_TOO_LATE")
        self.assertEqual(first["closed_targets"], [TARGET.event_key])
        second = await scheduler.run_cycle()
        self.assertFalse(second["provider_called"])
        self.assertEqual(len(provider.calls), 1)

    async def test_three_persisted_unique_successes_only_qualify_for_manual_review(self):
        rows = [
            _stored_attempt("BLS:CPI:2026-06", RELEASE - timedelta(days=62)),
            _stored_attempt("BLS:CPI:2026-07", RELEASE - timedelta(days=31)),
            _stored_attempt("BLS:CPI:2026-08", RELEASE),
        ]
        clock = _MutableClock(WINDOW_END + timedelta(seconds=60))
        provider = _FakeProvider(clock)
        store = _FakeStore(rows)
        scheduler = MacroLiveAvailabilityAuditScheduler(
            provider=provider,
            store=store,
            targets=[TARGET],
            policy=MacroLiveAvailabilityCapturePolicy(enabled=True, min_unique_events=3),
            clock=clock,
        )
        result = await scheduler.run_cycle()
        self.assertEqual(result["qualification"]["status"], "QUALIFIED_FOR_MANUAL_REVIEW")
        self.assertFalse(result["qualification"]["auto_enable_live_confirmation"])
        self.assertFalse(result["live_confirmation_enabled"])
        self.assertFalse(result["trade_generated"])

    async def test_duplicate_targets_are_rejected(self):
        clock = _MutableClock(WINDOW_END)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            MacroLiveAvailabilityAuditScheduler(
                provider=_FakeProvider(clock),
                store=_FakeStore(),
                targets=[TARGET, TARGET],
                policy=MacroLiveAvailabilityCapturePolicy(enabled=True),
                clock=clock,
            )

    def test_policy_and_architecture_fail_closed(self):
        with self.assertRaises(ValueError):
            MacroLiveAvailabilityCapturePolicy(poll_seconds=4).validated()
        with self.assertRaises(ValueError):
            MacroLiveAvailabilityCapturePolicy(poll_seconds=60, max_latency_seconds=30).validated()
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertTrue(contract["reaction_window_completion_required_before_provider_call"])
        self.assertTrue(contract["retries_allowed_inside_latency_budget"])
        self.assertTrue(contract["stored_attempts_reloaded_each_cycle"])
        self.assertFalse(contract["restart_resets_qualification_history"])
        self.assertTrue(contract["qualification_is_manual_review_only"])
        self.assertFalse(contract["live_confirmation_auto_enabled"])
        self.assertFalse(contract["direction_generated"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
