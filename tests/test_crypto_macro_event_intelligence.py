import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_macro_event_intelligence import (
    MacroConsensusSnapshot,
    OfficialMacroRelease,
    architecture_contract,
    compute_numeric_surprise,
)


def _release_time():
    return datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)  # 08:30 ET during EDT


def _release(**overrides):
    data = dict(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        reference_period="2026-08",
        release_at=_release_time(),
        first_seen_at=_release_time() + timedelta(seconds=2),
        official_source="BLS",
        official_source_ref="official-cpi-release",
        values={"headline_mom_pct": 0.2, "core_mom_pct": 0.3},
        units={"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"},
    )
    data.update(overrides)
    return OfficialMacroRelease(**data)


def _consensus(**overrides):
    data = dict(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        release_at=_release_time(),
        provider_time=_release_time() - timedelta(minutes=10),
        first_seen_at=_release_time() - timedelta(minutes=9),
        source_name="VERIFIED_FORECAST_PROVIDER",
        source_ref="forecast-snapshot-1",
        values={"headline_mom_pct": 0.1, "core_mom_pct": 0.2},
        units={"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"},
        source_verified=True,
    )
    data.update(overrides)
    return MacroConsensusSnapshot(**data)


class CryptoMacroEventIntelligenceTests(unittest.TestCase):
    def test_numeric_surprise_requires_actual_first_release_and_pre_release_consensus(self):
        surprise = compute_numeric_surprise(_release(), _consensus())
        self.assertEqual(surprise.event_type, "CPI")
        self.assertAlmostEqual(surprise.surprise["headline_mom_pct"], 0.1)
        self.assertAlmostEqual(surprise.surprise["core_mom_pct"], 0.1)
        self.assertEqual(surprise.direction, "UNKNOWN")
        self.assertFalse(surprise.standalone_direction_allowed)

    def test_consensus_first_seen_at_or_after_release_is_rejected(self):
        with self.assertRaises(ValueError):
            _consensus(first_seen_at=_release_time()).validated()
        with self.assertRaises(ValueError):
            _consensus(first_seen_at=_release_time() + timedelta(seconds=1)).validated()

    def test_unverified_consensus_is_rejected(self):
        with self.assertRaises(ValueError):
            _consensus(source_verified=False).validated()

    def test_consensus_provider_time_cannot_be_after_first_seen(self):
        with self.assertRaises(ValueError):
            _consensus(
                provider_time=_release_time() - timedelta(minutes=5),
                first_seen_at=_release_time() - timedelta(minutes=6),
            ).validated()

    def test_release_cannot_be_first_seen_before_official_release_time(self):
        with self.assertRaises(ValueError):
            _release(first_seen_at=_release_time() - timedelta(seconds=1)).validated()

    def test_exact_release_timestamp_must_be_timezone_aware(self):
        naive = datetime(2026, 9, 11, 8, 30)
        with self.assertRaises(ValueError):
            _release(release_at=naive).validated()
        with self.assertRaises(ValueError):
            _consensus(release_at=naive).validated()

    def test_revision_cannot_replace_first_release_in_surprise(self):
        revision = _release(
            release_stage="REVISION",
            revision_number=1,
            revises_event_key="BLS:CPI:2026-08",
            first_seen_at=_release_time() + timedelta(days=30),
        )
        revision.validated()
        with self.assertRaises(ValueError):
            compute_numeric_surprise(revision, _consensus())

    def test_first_release_revision_fields_fail_closed(self):
        with self.assertRaises(ValueError):
            _release(revision_number=1).validated()
        with self.assertRaises(ValueError):
            _release(revises_event_key="old-event").validated()

    def test_release_and_consensus_must_match_event_and_timestamp(self):
        with self.assertRaises(ValueError):
            compute_numeric_surprise(_release(), _consensus(event_key="OTHER"))
        with self.assertRaises(ValueError):
            compute_numeric_surprise(
                _release(),
                _consensus(release_at=_release_time() + timedelta(minutes=1)),
            )

    def test_metric_units_must_match(self):
        bad_units = {
            "headline_mom_pct": "BASIS_POINTS",
            "core_mom_pct": "PERCENT",
        }
        with self.assertRaises(ValueError):
            compute_numeric_surprise(_release(), _consensus(units=bad_units))

    def test_fomc_statement_and_press_conference_are_distinct_event_types(self):
        statement = _release(
            event_key="FED:FOMC:2026-09-16:STATEMENT",
            event_type="FOMC_STATEMENT",
            reference_period="2026-09-16",
            release_at=datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc),
            first_seen_at=datetime(2026, 9, 16, 18, 0, 2, tzinfo=timezone.utc),
            official_source="FEDERAL_RESERVE",
            values={"target_midpoint_pct": 3.625},
            units={"target_midpoint_pct": "PERCENT"},
        )
        press = _release(
            event_key="FED:FOMC:2026-09-16:PRESS_CONFERENCE",
            event_type="FOMC_PRESS_CONFERENCE",
            reference_period="2026-09-16",
            release_at=datetime(2026, 9, 16, 18, 30, tzinfo=timezone.utc),
            first_seen_at=datetime(2026, 9, 16, 18, 30, 1, tzinfo=timezone.utc),
            official_source="FEDERAL_RESERVE",
            values={"semantic_signal": 0.0},
            units={"semantic_signal": "MODEL_INPUT_PLACEHOLDER"},
        )
        self.assertEqual(statement.validated().event_type, "FOMC_STATEMENT")
        self.assertEqual(press.validated().event_type, "FOMC_PRESS_CONFERENCE")
        self.assertNotEqual(statement.event_key, press.event_key)
        self.assertNotEqual(statement.release_at, press.release_at)

    def test_architecture_requires_event_specific_semantics_before_direction(self):
        contract = architecture_contract()
        self.assertTrue(contract["official_release_timestamp_required"])
        self.assertTrue(contract["timezone_aware_release_timestamp_required"])
        self.assertTrue(contract["consensus_required_for_numeric_surprise"])
        self.assertTrue(contract["consensus_must_be_first_seen_before_release"])
        self.assertFalse(contract["unverified_consensus_allowed"])
        self.assertFalse(contract["revision_may_replace_first_release"])
        self.assertFalse(contract["generic_numeric_surprise_assigns_btc_direction"])
        self.assertTrue(contract["event_specific_semantics_required_for_direction"])
        self.assertTrue(contract["market_confirmation_required_before_directional_admission"])
        self.assertTrue(contract["fomc_statement_and_press_conference_are_separate_events"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])


if __name__ == "__main__":
    unittest.main()
