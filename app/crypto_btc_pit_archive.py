"""Immutable point-in-time archive contract for irrecoverable BTC Crypto Brain data.

Storage-backend agnostic by design. This module defines the semantic contract a
future Postgres/object-store/etc implementation must preserve: first-seen wins,
no retrospective overwrite, deterministic content hashes, no outcome fields, and
visibility only after the record's first_seen_at.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from app.crypto_btc_source_capabilities import capability_for


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


FORBIDDEN_OUTCOME_KEYS = {
    "future_return",
    "future_price",
    "future_move",
    "trade_outcome",
    "backtest_result",
    "realized_pnl",
    "realized_r",
    "target_hit",
    "stop_hit",
    "win_loss",
    "post_click_label",
}


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key).strip().lower())
            keys.update(_walk_keys(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            keys.update(_walk_keys(nested))
    return keys


def same_immutable_observation(existing: dict, candidate: dict) -> bool:
    """Return True when a later poll rediscovered the same provider observation.

    ``first_seen_at`` is deliberately excluded. The earliest stored first-seen
    timestamp wins forever, while a restart/poll that sees identical provider
    content for the same natural key is safely idempotent.
    """
    fields = ("dataset", "provider", "source_key", "event_at", "source_version", "payload_hash")
    return all(existing.get(field) == candidate.get(field) for field in fields)


@dataclass(frozen=True)
class BtcPitArchiveRecord:
    dataset: str
    provider: str
    source_key: str
    first_seen_at: datetime
    payload: dict
    event_at: datetime | None = None
    source_version: str | None = None
    point_in_time_proven: bool = True

    def validated(self) -> "BtcPitArchiveRecord":
        capability = capability_for(self.dataset)
        if capability.can_reconstruct_later:
            raise ValueError("reconstructible public history is not admitted to the irrecoverable PIT archive")
        if not str(self.provider or "").strip() or not str(self.source_key or "").strip():
            raise ValueError("provider and source_key are required")
        if self.point_in_time_proven is not True:
            raise ValueError("PIT archive requires point_in_time_proven=True")
        if not isinstance(self.payload, dict) or not self.payload:
            raise ValueError("payload must be a non-empty dict")
        if self.event_at is not None and _utc(self.event_at) > _utc(self.first_seen_at):
            raise ValueError("event_at cannot be after first_seen_at")
        forbidden = _walk_keys(self.payload) & FORBIDDEN_OUTCOME_KEYS
        if forbidden:
            raise ValueError(f"future/outcome fields are forbidden in PIT archive payload: {sorted(forbidden)}")
        return self

    @property
    def natural_key(self) -> str:
        self.validated()
        raw = f"{self.dataset}|{self.provider}|{self.source_key}".encode("utf-8")
        return sha256(raw).hexdigest()

    @property
    def payload_hash(self) -> str:
        self.validated()
        return sha256(_canonical(self.payload)).hexdigest()

    @property
    def record_fingerprint(self) -> str:
        self.validated()
        identity = {
            "dataset": self.dataset,
            "provider": self.provider,
            "source_key": self.source_key,
            "event_at": None if self.event_at is None else _utc(self.event_at).isoformat(),
            "first_seen_at": _utc(self.first_seen_at).isoformat(),
            "source_version": self.source_version,
            "payload_hash": self.payload_hash,
        }
        return sha256(_canonical(identity)).hexdigest()

    def frozen_dict(self) -> dict:
        self.validated()
        return {
            "dataset": self.dataset,
            "provider": self.provider,
            "source_key": self.source_key,
            "natural_key": self.natural_key,
            "event_at": None if self.event_at is None else _utc(self.event_at).isoformat(),
            "first_seen_at": _utc(self.first_seen_at).isoformat(),
            "source_version": self.source_version,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
            "record_fingerprint": self.record_fingerprint,
            "point_in_time_proven": True,
        }


class ImmutableBtcPitLedger:
    """Reference in-memory semantics for a future persistent archive backend."""

    def __init__(self) -> None:
        self._records: dict[str, dict] = {}

    def insert_first_seen(self, record: BtcPitArchiveRecord) -> dict:
        frozen = record.frozen_dict()
        key = frozen["natural_key"]
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = frozen
            return {"status": "INSERTED_FIRST_SEEN", "record": frozen}
        if same_immutable_observation(existing, frozen):
            return {"status": "IDEMPOTENT_DUPLICATE", "record": existing}
        raise ValueError("conflicting later observation cannot overwrite immutable first-seen record")

    def visible_as_of(self, as_of: datetime, *, dataset: str | None = None) -> list[dict]:
        cutoff = _utc(as_of)
        rows = [
            row for row in self._records.values()
            if _utc(datetime.fromisoformat(row["first_seen_at"])) <= cutoff
            and (dataset is None or row["dataset"] == dataset)
        ]
        return sorted(rows, key=lambda row: (row["first_seen_at"], row["dataset"], row["source_key"]))

    def manifest(self) -> dict:
        by_dataset: dict[str, int] = {}
        for row in self._records.values():
            by_dataset[row["dataset"]] = by_dataset.get(row["dataset"], 0) + 1
        return {
            "version": "BTC_PIT_ARCHIVE_MANIFEST_V1",
            "record_count": len(self._records),
            "by_dataset": dict(sorted(by_dataset.items())),
            "immutable_first_seen": True,
            "overwrite_allowed": False,
            "outcome_fields_allowed": False,
        }


def archive_record_from_capture(
    *,
    dataset: str,
    provider: str,
    source_key: str,
    first_seen_at: datetime,
    payload: dict,
    event_at: datetime | None = None,
    source_version: str | None = None,
) -> BtcPitArchiveRecord:
    return BtcPitArchiveRecord(
        dataset=dataset,
        provider=provider,
        source_key=source_key,
        first_seen_at=first_seen_at,
        event_at=event_at,
        source_version=source_version,
        payload=payload,
        point_in_time_proven=True,
    ).validated()


def architecture_contract() -> dict:
    return {
        "version": "BTC_PIT_ARCHIVE_CONTRACT_V2",
        "storage_backend_selected": False,
        "semantic_contract_storage_agnostic": True,
        "irrecoverable_pit_data_only": True,
        "reconstructible_public_history_admitted": False,
        "first_seen_wins": True,
        "overwrite_existing_first_seen_record": False,
        "idempotent_exact_duplicate_allowed": True,
        "same_provider_observation_seen_later_is_idempotent": True,
        "earliest_first_seen_is_preserved": True,
        "conflicting_duplicate_allowed": False,
        "future_outcome_fields_allowed": False,
        "record_visible_before_first_seen": False,
        "payload_hash_required": True,
        "record_fingerprint_required": True,
        "options_and_futures_trade_generation_separate": True,
        "broker_execution_enabled": False,
        "research_only": True,
    }
