import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_macro_live_availability_audit import (
    MacroLiveAvailabilityPolicy,
    architecture_contract,
    audit_massive_live_availability_once,
    qualify_massive_live_availability,
)
from app.massive_macro_futures_reaction_provider import (
    MassiveMacroFuturesReaction,
    MassiveMacroFuturesReactionPolicy,
    SelectedFuturesContract,
)


RELEASE = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)
WINDOW_END = RELEASE + timedelta(minutes=10)


def _selected(product_code, ticker, *, reference_close):
    return SelectedFuturesContract(
        product_code=product_code,
        ticker=ticker,
        trading_venue="XCME",
        event_date="2026-09-11",
        days_to_maturity=7,
        pre_release_volume=100.0,
        selection_window_start=RELEASE - timedelta(minutes=30),
        selection_window_end=RELEASE,
        reference_close=reference_close,
    )


def _reaction(*, event_key="BLS:CPI:2026-08", event_type="CPI", release_at=RELEASE):
    observed = release_at + timedelta(minutes=10)
    return MassiveMacroFuturesReaction(
        event_key=event_key,
        event_type=event_type,
        release_at=release_at,
        observed_at=observed,
        reconstructible_available_at=observed,
        retrieved_at=observed + timedelta(seconds=20),
        nasdaq_futures_return_pct=-0.8,
        eurusd_futures_return_pct=-0.3,
        usd_strength_proxy_return_pct=0.3,
        nasdaq_contract=_selected("NQ", "NQU6", reference_close=20000.0),
        euro_fx_contract=_selected("6E", "6EU6", reference_close=1.10),
    )


class _SequenceClock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("clock exhausted")
        return self.values.pop(0)


class _FakeProvider:
    def __init__(self, *, reaction=None, error=None):
        self.policy = MassiveMacroFuturesReactionPolicy(
            enabled=True,
            api_key="KEY",
            reaction_window_minutes=10,
        )
        self.reaction = reaction or _reaction()
        self.error = error
        self.calls = []

    def fetch_reaction(self, *, event_key, event_type, release_at):
        self.calls.append((event_key, event_type, release_at))
        if self.error is not None:
            raise self.error
        return self.reaction


def _attempt(event_key, release_at, *, success=True):
    provider = _FakeProvider(
        reaction=_reaction(event_key=event_key, release_at=release_at),
        error=None if success else ValueError("bar not available yet"),
    )
    end = release_at + timedelta(minutes=10)
    clock = _SequenceClock(end + timedelta(seconds=30), end + timedelta(seconds=31))
    return audit_massive_live_availability_once(
        provider,
        event_key=event_key,
        event_type="CPI",
        release_at=release_at,
        clock=clock,
    )


class CryptoMacroLiveAvailabilityAuditTests(unittest.TestCase):
    def test_success_within_latency_is_observed_not_auto_enabled(self):
        provider = _FakeProvider()
        clock = _SequenceClock(WINDOW_END + timedelta(seconds=30), WINDOW_END + timedelta(seconds=31))
        attempt = audit_massive_live_availability_once(
            provider,
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
            clock=clock,
        )
        self.assertEqual(attempt.status, "AVAILABLE_WITHIN_LATENCY")
        self.assertEqual(attempt.availability_latency_seconds, 31.0)
        self.assertEqual(attempt.nasdaq_contract_ticker, "NQU6")
        self.assertEqual(attempt.euro_fx_contract_ticker, "6EU6")
        self.assertFalse(attempt.live_confirmation_enabled)
        self.assertFalse(attempt.direction_generated)
        self.assertFalse(attempt.options_trade_generated)
        self.assertFalse(attempt.futures_trade_generated)
        self.assertEqual(len(provider.calls), 1)

    def test_availability_after_threshold_is_too_late_not_realtime_proof(self):
        provider = _FakeProvider()
        policy = MacroLiveAvailabilityPolicy(max_latency_seconds=60, min_unique_events=3)
        clock = _SequenceClock(WINDOW_END + timedelta(seconds=70), WINDOW_END + timedelta(seconds=71))
        attempt = audit_massive_live_availability_once(
            provider,
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
            policy=policy,
            clock=clock,
        )
        self.assertEqual(attempt.status, "AVAILABLE_TOO_LATE")
        self.assertEqual(attempt.availability_latency_seconds, 71.0)
        self.assertFalse(attempt.live_confirmation_enabled)

    def test_provider_failure_is_explicit_and_never_neutralized_into_success(self):
        provider = _FakeProvider(error=ValueError("delayed bar unavailable"))
        clock = _SequenceClock(WINDOW_END + timedelta(seconds=30), WINDOW_END + timedelta(seconds=31))
        attempt = audit_massive_live_availability_once(
            provider,
            event_key="BLS:CPI:2026-08",
            event_type="CPI",
            release_at=RELEASE,
            clock=clock,
        )
        self.assertEqual(attempt.status, "UNAVAILABLE_OR_PROVIDER_ERROR")
        self.assertEqual(attempt.failure_kind, "ValueError")
        self.assertIsNone(attempt.nasdaq_contract_ticker)
        self.assertIsNone(attempt.euro_fx_contract_ticker)

    def test_attempt_before_reaction_window_fails_before_provider_call(self):
        provider = _FakeProvider()
        clock = _SequenceClock(WINDOW_END - timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "before reaction window completion"):
            audit_massive_live_availability_once(
                provider,
                event_key="BLS:CPI:2026-08",
                event_type="CPI",
                release_at=RELEASE,
                clock=clock,
            )
        self.assertEqual(provider.calls, [])

    def test_three_unique_successful_events_only_reach_manual_review(self):
        attempts = [
            _attempt("BLS:CPI:2026-06", RELEASE - timedelta(days=62)),
            _attempt("BLS:CPI:2026-07", RELEASE - timedelta(days=31)),
            _attempt("BLS:CPI:2026-08", RELEASE),
        ]
        result = qualify_massive_live_availability(attempts)
        self.assertEqual(result.status, "QUALIFIED_FOR_MANUAL_REVIEW")
        self.assertEqual(result.unique_events_observed, 3)
        self.assertEqual(result.events_available_within_latency, 3)
        self.assertFalse(result.auto_enable_live_confirmation)
        self.assertFalse(result.direction_generated)
        self.assertFalse(result.options_trade_generated)
        self.assertFalse(result.futures_trade_generated)

    def test_duplicate_attempts_for_one_event_do_not_manufacture_sample_size(self):
        one = _attempt("BLS:CPI:2026-08", RELEASE)
        result = qualify_massive_live_availability([one, one, one])
        self.assertEqual(result.status, "INSUFFICIENT_PROSPECTIVE_EVENTS")
        self.assertEqual(result.unique_events_observed, 1)
        self.assertEqual(result.events_available_within_latency, 1)

    def test_one_failed_event_blocks_qualification_after_minimum_sample(self):
        attempts = [
            _attempt("BLS:CPI:2026-06", RELEASE - timedelta(days=62)),
            _attempt("BLS:CPI:2026-07", RELEASE - timedelta(days=31), success=False),
            _attempt("BLS:CPI:2026-08", RELEASE),
        ]
        result = qualify_massive_live_availability(attempts)
        self.assertEqual(result.status, "NOT_QUALIFIED")
        self.assertEqual(result.failed_event_keys, ("BLS:CPI:2026-07",))
        self.assertFalse(result.auto_enable_live_confirmation)

    def test_attempt_fingerprint_is_deterministic(self):
        attempt = _attempt("BLS:CPI:2026-08", RELEASE)
        self.assertEqual(attempt.fingerprint(), attempt.fingerprint())
        self.assertEqual(len(attempt.fingerprint()), 64)

    def test_policy_keeps_latency_below_ten_minute_delayed_feed(self):
        with self.assertRaises(ValueError):
            MacroLiveAvailabilityPolicy(max_latency_seconds=600).validated()
        with self.assertRaises(ValueError):
            MacroLiveAvailabilityPolicy(min_unique_events=1).validated()

    def test_architecture_never_treats_history_or_plan_label_as_live_proof(self):
        contract = architecture_contract()
        self.assertFalse(contract["historical_reconstruction_proves_live_availability"])
        self.assertFalse(contract["configured_plan_label_proves_live_availability"])
        self.assertTrue(contract["prospective_retrieval_observation_required"])
        self.assertTrue(contract["repeated_unique_events_required"])
        self.assertFalse(contract["single_success_auto_qualifies"])
        self.assertTrue(contract["qualification_is_manual_review_only"])
        self.assertFalse(contract["live_confirmation_auto_enabled"])
        self.assertFalse(contract["direction_generated"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
