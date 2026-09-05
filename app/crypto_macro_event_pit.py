"""Immutable PIT adapters for exact official macro releases and consensus states."""
from __future__ import annotations

from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture
from app.crypto_macro_event_intelligence import MacroConsensusSnapshot, OfficialMacroRelease

RELEASE_DATASET = "SCHEDULED_MACRO_RELEASES"
CONSENSUS_DATASET = "MACRO_CONSENSUS_SNAPSHOTS"


def official_macro_release_archive_record(release: OfficialMacroRelease) -> BtcPitArchiveRecord:
    row = release.validated()
    payload = {
        "event_key": row.event_key,
        "event_type": row.event_type,
        "reference_period": row.reference_period,
        "release_at": row.normalized_release_at.isoformat(),
        "official_source": row.official_source,
        "official_source_ref": row.official_source_ref,
        "values": {key: float(value) for key, value in sorted(row.values.items())},
        "units": dict(sorted(row.units.items())),
        "release_stage": row.release_stage,
        "revision_number": int(row.revision_number),
        "revises_event_key": row.revises_event_key,
        "official_release_timestamp_proven": True,
        "first_seen_controls_click_visibility": True,
        "revision_may_replace_first_release": False,
        "generic_direction_assigned": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
    }
    return archive_record_from_capture(
        dataset=RELEASE_DATASET,
        provider=row.official_source,
        source_key=f"MACRO_RELEASE:{row.event_key}:{row.release_stage}:{int(row.revision_number)}",
        first_seen_at=row.normalized_first_seen_at,
        event_at=row.normalized_release_at,
        source_version="EXACT_OFFICIAL_MACRO_RELEASE_V1",
        payload=payload,
    )


def macro_consensus_archive_record(consensus: MacroConsensusSnapshot) -> BtcPitArchiveRecord:
    row = consensus.validated()
    provider_time = None if row.provider_time is None else row.provider_time.astimezone(row.first_seen_at.tzinfo).astimezone(__import__("datetime").timezone.utc)
    first_seen = row.first_seen_at.astimezone(__import__("datetime").timezone.utc)
    release_at = row.release_at.astimezone(__import__("datetime").timezone.utc)
    payload = {
        "event_key": row.event_key,
        "event_type": row.event_type,
        "release_at": release_at.isoformat(),
        "source_name": row.source_name,
        "source_ref": row.source_ref,
        "values": {key: float(value) for key, value in sorted(row.values.items())},
        "units": dict(sorted(row.units.items())),
        "source_verified": True,
        "provider_time": None if provider_time is None else provider_time.isoformat(),
        "consensus_first_seen_before_release": True,
        "post_release_consensus_admitted": False,
        "standalone_direction_assigned": False,
        "generic_surprise_direction_assigned": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
    }
    return archive_record_from_capture(
        dataset=CONSENSUS_DATASET,
        provider=row.source_name,
        source_key=f"MACRO_CONSENSUS:{row.event_key}:{row.state_hash}",
        first_seen_at=first_seen,
        event_at=provider_time,
        source_version="EXACT_MACRO_CONSENSUS_PIT_V1",
        payload=payload,
    )


def architecture_contract() -> dict:
    return {
        "version": "EXACT_MACRO_EVENT_PIT_V1",
        "release_dataset": RELEASE_DATASET,
        "consensus_dataset": CONSENSUS_DATASET,
        "official_release_event_at_preserved": True,
        "official_release_first_seen_preserved": True,
        "revisions_are_separate_records": True,
        "revision_may_replace_first_release": False,
        "consensus_state_hash_identity": True,
        "consensus_must_precede_release": True,
        "post_release_consensus_admitted": False,
        "generic_direction_assigned": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
