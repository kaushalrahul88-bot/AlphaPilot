"""Immutable resolved-experience memory for the BTC Crypto Brain.

Resolved experience is deliberately NOT stored in the market-data PIT archive:
that archive correctly forbids future/outcome fields. Experience becomes a valid
input only after the old case has genuinely resolved. This store therefore uses
``resolved_at`` as its availability boundary and exposes cases only when
``resolved_at < current_decision_at``.

Research/shadow only. It never rewrites the old decision, generates an order, or
allows Futures state into the BTC Options experience path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def same_resolved_experience(existing: dict, candidate: dict) -> bool:
    fields = (
        "click_id",
        "decision_at",
        "resolved_at",
        "instrument_type",
        "outcome_type",
        "source_version",
        "payload_hash",
    )
    return all(existing.get(field) == candidate.get(field) for field in fields)


@dataclass(frozen=True)
class ResolvedBtcExperienceRecord:
    click_id: str
    decision_at: datetime
    resolved_at: datetime
    outcome_type: str
    payload: dict
    instrument_type: str = "OPTIONS"
    source_version: str = "BTC_RESOLVED_EXPERIENCE_V1"

    def validated(self) -> "ResolvedBtcExperienceRecord":
        click_id = str(self.click_id or "").strip()
        if not click_id:
            raise ValueError("click_id is required")
        decision = _utc(self.decision_at)
        resolved = _utc(self.resolved_at)
        if resolved <= decision:
            raise ValueError("resolved_at must be strictly after decision_at")
        if str(self.instrument_type or "").upper() != "OPTIONS":
            raise ValueError("BTC resolved experience store is Options-only")
        outcome_type = str(self.outcome_type or "").upper()
        if outcome_type not in {"TRADE_CLOSED", "NO_TRADE_LEARNING"}:
            raise ValueError("only resolved TRADE_CLOSED or NO_TRADE_LEARNING cases may enter memory")
        if not isinstance(self.payload, dict) or not self.payload:
            raise ValueError("resolved experience payload must be a non-empty dict")
        if str(self.payload.get("click_id") or "") != click_id:
            raise ValueError("payload click_id does not match record click_id")
        if _stamp(self.payload.get("decision_at")) != decision:
            raise ValueError("payload decision_at does not match record decision_at")
        if str(self.payload.get("instrument_type") or "").upper() != "OPTIONS":
            raise ValueError("experience payload must be Options-only")
        if self.payload.get("futures_route_invoked") is True or self.payload.get("futures_trade_generated") is True:
            raise ValueError("resolved BTC Options experience rejects Futures-route state")
        if self.payload.get("future_outcome_may_rewrite_decision") is not False:
            raise ValueError("experience payload must preserve the frozen prior decision")
        if str(self.payload.get("outcome_type") or "").upper() != outcome_type:
            raise ValueError("payload outcome_type does not match record outcome_type")

        if outcome_type == "TRADE_CLOSED":
            trade = self.payload.get("trade_outcome")
            if not isinstance(trade, dict) or trade.get("status") != "SHADOW_TRADE_CLOSED":
                raise ValueError("TRADE_CLOSED memory requires a closed shadow trade outcome")
            if trade.get("exit_at") is None:
                raise ValueError("TRADE_CLOSED memory requires exit_at")
            if resolved < _stamp(trade["exit_at"]):
                raise ValueError("resolved_at cannot precede the trade exit")
            if trade.get("actual_quote_used_for_pnl") is not True:
                raise ValueError("resolved trade memory requires actual archived option quote P&L")
            if trade.get("model_reference_used_as_fill") is True:
                raise ValueError("model reference cannot be accepted as resolved trade fill")
            if self.payload.get("performance_eligible") is not True:
                raise ValueError("resolved trade memory must be performance eligible")
        else:
            follow = self.payload.get("no_trade_follow_through")
            if not isinstance(follow, dict) or follow.get("status") != "NO_TRADE_FOLLOW_THROUGH_RESOLVED":
                raise ValueError("NO_TRADE_LEARNING memory requires resolved follow-through")
            try:
                horizon_hours = float(follow["learning_horizon_hours"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("resolved NO_TRADE memory requires learning_horizon_hours") from exc
            if horizon_hours <= 0:
                raise ValueError("learning_horizon_hours must be > 0")
            minimum_resolution = decision + timedelta(hours=horizon_hours)
            if resolved < minimum_resolution:
                raise ValueError("NO_TRADE experience cannot resolve before its full learning horizon")
            if self.payload.get("performance_eligible") is not False:
                raise ValueError("NO_TRADE learning must stay outside trade performance")
        return self

    @property
    def natural_key(self) -> str:
        self.validated()
        return sha256(f"BTC|OPTIONS|{self.click_id}".encode("utf-8")).hexdigest()

    @property
    def payload_hash(self) -> str:
        self.validated()
        return sha256(_canonical(self.payload)).hexdigest()

    @property
    def record_fingerprint(self) -> str:
        self.validated()
        identity = {
            "natural_key": self.natural_key,
            "decision_at": _utc(self.decision_at).isoformat(),
            "resolved_at": _utc(self.resolved_at).isoformat(),
            "outcome_type": str(self.outcome_type).upper(),
            "source_version": self.source_version,
            "payload_hash": self.payload_hash,
        }
        return sha256(_canonical(identity)).hexdigest()

    def frozen_dict(self) -> dict:
        self.validated()
        return {
            "natural_key": self.natural_key,
            "click_id": self.click_id,
            "decision_at": _utc(self.decision_at).isoformat(),
            "resolved_at": _utc(self.resolved_at).isoformat(),
            "instrument_type": "OPTIONS",
            "outcome_type": str(self.outcome_type).upper(),
            "source_version": self.source_version,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
            "record_fingerprint": self.record_fingerprint,
            "immutable_resolved_experience": True,
        }


class ImmutableBtcExperienceLedger:
    def __init__(self) -> None:
        self._records: dict[str, dict] = {}

    def insert_resolved(self, record: ResolvedBtcExperienceRecord) -> dict:
        frozen = record.frozen_dict()
        key = frozen["natural_key"]
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = frozen
            return {"status": "INSERTED_RESOLVED_EXPERIENCE", "record": frozen}
        if same_resolved_experience(existing, frozen):
            return {"status": "IDEMPOTENT_RESOLVED_EXPERIENCE", "record": existing}
        raise ValueError("conflicting later experience cannot overwrite immutable resolved memory")

    def visible_strictly_before(self, decision_at: datetime) -> list[dict]:
        cutoff = _utc(decision_at)
        rows = [
            row for row in self._records.values()
            if _stamp(row["resolved_at"]) < cutoff
        ]
        return sorted(rows, key=lambda row: (row["resolved_at"], row["click_id"]))

    def manifest(self) -> dict:
        by_outcome: dict[str, int] = {}
        for row in self._records.values():
            key = row["outcome_type"]
            by_outcome[key] = by_outcome.get(key, 0) + 1
        return {
            "version": "BTC_RESOLVED_EXPERIENCE_MANIFEST_V1",
            "record_count": len(self._records),
            "by_outcome_type": dict(sorted(by_outcome.items())),
            "insert_only": True,
            "outcome_memory_allowed_only_after_resolution": True,
            "visible_at_same_timestamp_as_resolution": False,
            "futures_state_allowed": False,
        }


def resolved_experience_record_from_entry(*, entry: dict, resolved_at: datetime) -> ResolvedBtcExperienceRecord:
    if not isinstance(entry, dict):
        raise ValueError("entry must be a dict")
    return ResolvedBtcExperienceRecord(
        click_id=str(entry.get("click_id") or ""),
        decision_at=_stamp(entry.get("decision_at")),
        resolved_at=_utc(resolved_at),
        outcome_type=str(entry.get("outcome_type") or ""),
        payload=entry,
        instrument_type=str(entry.get("instrument_type") or ""),
    ).validated()


def architecture_contract() -> dict:
    return {
        "version": "BTC_RESOLVED_EXPERIENCE_STORE_V1",
        "market_data_pit_archive_used_for_outcomes": False,
        "dedicated_experience_memory": True,
        "unresolved_case_admitted": False,
        "resolution_time_required": True,
        "case_visible_before_resolution": False,
        "case_visible_at_exact_resolution_timestamp": False,
        "only_strictly_prior_resolved_cases_visible": True,
        "old_decision_rewritten": False,
        "no_trade_full_horizon_required": True,
        "actual_option_quote_required_for_closed_trade_memory": True,
        "futures_state_allowed": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
