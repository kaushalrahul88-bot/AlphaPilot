import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_archive import ImmutableBtcPitLedger
from app.crypto_macro_event_intelligence import MacroConsensusSnapshot, OfficialMacroRelease
from app.crypto_macro_event_pit import (
    CONSENSUS_DATASET,
    RELEASE_DATASET,
    architecture_contract,
    macro_consensus_archive_record,
    official_macro_release_archive_record,
)


def _release_at():
    return datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)


def _release(*, first_seen=None, values=None, stage="FIRST_RELEASE", revision=0, revises=None):
    return OfficialMacroRelease(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        reference_period="2026-08",
        release_at=_release_at(),
        first_seen_at=first_seen or (_release_at() + timedelta(seconds=2)),
        official_source="BLS",
        official_source_ref="official-cpi-release",
        values=values or {"headline_mom_pct": 0.2, "core_mom_pct": 0.3},
        units={"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"},
        release_stage=stage,
        revision_number=revision,
        revises_event_key=revises,
    )


def _consensus(*, first_seen=None, provider_time=None, values=None, source_ref="forecast-1"):
    return MacroConsensusSnapshot(
        event_key="BLS:CPI:2026-08",
        event_type="CPI",
        release_at=_release_at(),
        provider_time=provider_time if provider_time is not None else _release_at() - timedelta(minutes=10),
        first_seen_at=first_seen or (_release_at() - timedelta(minutes=9)),
        source_name="VERIFIED_FORECAST_PROVIDER",
        source_ref=source_ref,
        values=values or {"headline_mom_pct": 0.1, "core_mom_pct": 0.2},
        units={"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"},
        source_verified=True,
    )


class CryptoMacroEventPitTests(unittest.TestCase):
    def test_official_first_release_preserves_release_event_time_and_first_seen(self):
        record = official_macro_release_archive_record(_release())
        frozen = record.frozen_dict()
        self.assertEqual(frozen["dataset"], RELEASE_DATASET)
        self.assertEqual(frozen["provider"], "BLS")
        self.assertEqual(frozen["event_at"], _release_at().isoformat())
        self.assertEqual(frozen["first_seen_at"], (_release_at() + timedelta(seconds=2)).isoformat())
        self.assertEqual(frozen["payload"]["release_stage"], "FIRST_RELEASE")
        self.assertTrue(frozen["payload"]["official_release_timestamp_proven"])
        self.assertFalse(frozen["payload"]["revision_may_replace_first_release"])
        self.assertFalse(frozen["payload"]["generic_direction_assigned"])

    def test_first_release_is_invisible_before_actual_first_seen(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(official_macro_release_archive_record(_release()))
        self.assertEqual(ledger.visible_as_of(_release_at() + timedelta(seconds=1), dataset=RELEASE_DATASET), [])
        self.assertEqual(len(ledger.visible_as_of(_release_at() + timedelta(seconds=2), dataset=RELEASE_DATASET)), 1)

    def test_later_revision_is_a_separate_record_and_does_not_overwrite_first_release(self):
        ledger = ImmutableBtcPitLedger()
        first = official_macro_release_archive_record(_release())
        revision = official_macro_release_archive_record(_release(
            first_seen=_release_at() + timedelta(days=30),
            values={"headline_mom_pct": 0.25, "core_mom_pct": 0.3},
            stage="REVISION",
            revision=1,
            revises="BLS:CPI:2026-08",
        ))
        self.assertNotEqual(first.source_key, revision.source_key)
        self.assertEqual(ledger.insert_first_seen(first)["status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(ledger.insert_first_seen(revision)["status"], "INSERTED_FIRST_SEEN")
        rows = ledger.visible_as_of(_release_at() + timedelta(days=31), dataset=RELEASE_DATASET)
        self.assertEqual(len(rows), 2)
        stages = {row["payload"]["release_stage"] for row in rows}
        self.assertEqual(stages, {"FIRST_RELEASE", "REVISION"})

    def test_conflicting_restatement_of_same_first_release_fails_closed(self):
        ledger = ImmutableBtcPitLedger()
        ledger.insert_first_seen(official_macro_release_archive_record(_release()))
        conflicting = official_macro_release_archive_record(_release(
            first_seen=_release_at() + timedelta(minutes=5),
            values={"headline_mom_pct": 0.4, "core_mom_pct": 0.3},
        ))
        with self.assertRaises(ValueError):
            ledger.insert_first_seen(conflicting)

    def test_consensus_preserves_provider_time_and_pre_release_first_seen(self):
        record = macro_consensus_archive_record(_consensus())
        frozen = record.frozen_dict()
        self.assertEqual(frozen["dataset"], CONSENSUS_DATASET)
        self.assertEqual(frozen["provider"], "VERIFIED_FORECAST_PROVIDER")
        self.assertEqual(frozen["event_at"], (_release_at() - timedelta(minutes=10)).isoformat())
        self.assertEqual(frozen["first_seen_at"], (_release_at() - timedelta(minutes=9)).isoformat())
        self.assertTrue(frozen["payload"]["consensus_first_seen_before_release"])
        self.assertFalse(frozen["payload"]["post_release_consensus_admitted"])
        self.assertFalse(frozen["payload"]["standalone_direction_assigned"])

    def test_unchanged_consensus_repoll_is_idempotent_but_changed_consensus_is_new_state(self):
        ledger = ImmutableBtcPitLedger()
        first = macro_consensus_archive_record(_consensus())
        repeated = macro_consensus_archive_record(_consensus(first_seen=_release_at() - timedelta(minutes=8)))
        changed = macro_consensus_archive_record(_consensus(
            first_seen=_release_at() - timedelta(minutes=5),
            provider_time=_release_at() - timedelta(minutes=6),
            values={"headline_mom_pct": 0.15, "core_mom_pct": 0.2},
            source_ref="forecast-2",
        ))
        self.assertEqual(ledger.insert_first_seen(first)["status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(ledger.insert_first_seen(repeated)["status"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(ledger.insert_first_seen(changed)["status"], "INSERTED_FIRST_SEEN")
        self.assertEqual(len(ledger.visible_as_of(_release_at() - timedelta(minutes=1), dataset=CONSENSUS_DATASET)), 2)

    def test_post_release_consensus_cannot_reach_pit_adapter(self):
        with self.assertRaises(ValueError):
            macro_consensus_archive_record(_consensus(first_seen=_release_at() + timedelta(seconds=1)))

    def test_architecture_keeps_release_consensus_revision_and_direction_separate(self):
        contract = architecture_contract()
        self.assertEqual(contract["release_dataset"], RELEASE_DATASET)
        self.assertEqual(contract["consensus_dataset"], CONSENSUS_DATASET)
        self.assertTrue(contract["official_release_event_at_preserved"])
        self.assertTrue(contract["revisions_are_separate_records"])
        self.assertFalse(contract["revision_may_replace_first_release"])
        self.assertTrue(contract["consensus_state_hash_identity"])
        self.assertTrue(contract["consensus_must_precede_release"])
        self.assertFalse(contract["post_release_consensus_admitted"])
        self.assertFalse(contract["generic_direction_assigned"])
        self.assertFalse(contract["trade_generation_allowed"])


if __name__ == "__main__":
    unittest.main()
