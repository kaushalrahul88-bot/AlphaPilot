"""Prospective BTC thesis proof tape for Crypto Brain V1.

This is the proof-oriented bridge between AlphaPilot's already validated BTC
Market Brain and real future evidence collection. A decision is frozen first
using only evidence visible at ``decision_at``. BTC price outcomes may be
attached only later, after the predefined evaluation horizon is due.

The tape deliberately stops before Options translation and execution. It uses
exactly the same decision/outcome scoring primitives as
``crypto_btc_underlying_thesis_validation`` so historical and prospective proof
cannot drift into different definitions of a hit, miss, abstention, or flat
outcome.

Research/shadow only: no option contract, premium, P&L, order, Futures route, or
capital is created here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Iterable

from app.crypto_btc_information_board import build_btc_information_board
from app.crypto_btc_random_click_experience import BtcForwardPriceObservation
from app.crypto_btc_underlying_thesis_validation import (
    _decision_fingerprint,
    _decision_snapshot,
    _evaluate_outcome,
)
from app.crypto_market_intelligence import Evidence


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stamp(value) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _finite(name: str, value: float, *, nonnegative: bool = False, positive: bool = False) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be >= 0")
    if positive and number <= 0:
        raise ValueError(f"{name} must be > 0")
    return number


def _digest(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ProspectiveBtcThesisTapePolicy:
    trade_horizon: str
    evaluation_horizon_hours: float
    terminal_price_max_gap_seconds: int
    neutral_band_pct: float
    large_move_threshold_pct: float

    def validated(self) -> "ProspectiveBtcThesisTapePolicy":
        if self.trade_horizon not in {"scalp", "intraday", "swing", "position"}:
            raise ValueError("unsupported trade_horizon")
        _finite("evaluation_horizon_hours", self.evaluation_horizon_hours, positive=True)
        if int(self.terminal_price_max_gap_seconds) < 0:
            raise ValueError("terminal_price_max_gap_seconds must be >= 0")
        neutral = _finite("neutral_band_pct", self.neutral_band_pct, nonnegative=True)
        large = _finite("large_move_threshold_pct", self.large_move_threshold_pct, positive=True)
        if large <= neutral:
            raise ValueError("large_move_threshold_pct must be greater than neutral_band_pct")
        return self

    def frozen_dict(self) -> dict:
        self.validated()
        return {
            "trade_horizon": self.trade_horizon,
            "evaluation_horizon_hours": float(self.evaluation_horizon_hours),
            "terminal_price_max_gap_seconds": int(self.terminal_price_max_gap_seconds),
            "neutral_band_pct": float(self.neutral_band_pct),
            "large_move_threshold_pct": float(self.large_move_threshold_pct),
        }


def _policy_from_frozen(row: dict) -> ProspectiveBtcThesisTapePolicy:
    if not isinstance(row, dict):
        raise ValueError("frozen policy must be a dict")
    return ProspectiveBtcThesisTapePolicy(
        trade_horizon=str(row.get("trade_horizon") or ""),
        evaluation_horizon_hours=float(row.get("evaluation_horizon_hours")),
        terminal_price_max_gap_seconds=int(row.get("terminal_price_max_gap_seconds")),
        neutral_band_pct=float(row.get("neutral_band_pct")),
        large_move_threshold_pct=float(row.get("large_move_threshold_pct")),
    ).validated()


def _verify_underlying_decision(decision: dict) -> bool:
    if not isinstance(decision, dict):
        return False
    expected = decision.get("decision_fingerprint")
    if not isinstance(expected, str) or not expected:
        return False
    payload = {key: value for key, value in decision.items() if key != "decision_fingerprint"}
    return _decision_fingerprint(payload) == expected


def freeze_prospective_btc_thesis(
    *,
    click_id: str,
    decision_at: datetime,
    btc_spot_price: float,
    evidence: list[Evidence],
    policy: ProspectiveBtcThesisTapePolicy,
) -> dict:
    """Freeze one prospective BTC Market Brain decision before any outcome exists."""
    click_id = str(click_id or "").strip()
    if not click_id:
        raise ValueError("click_id is required")
    policy.validated()
    decision_time = _utc(decision_at)
    spot = _finite("btc_spot_price", btc_spot_price, positive=True)

    for row in evidence:
        if _utc(row.observed_at) > decision_time:
            raise ValueError("prospective thesis contains evidence observed after decision_at")

    board = build_btc_information_board(
        evidence,
        decision_at=decision_time,
        trade_horizon=policy.trade_horizon,
    )
    decision = _decision_snapshot(
        click_id=click_id,
        decision_at=decision_time,
        btc_price=spot,
        board=board,
        evidence=evidence,
    )
    due_at = decision_time + timedelta(hours=float(policy.evaluation_horizon_hours))
    frozen_policy = policy.frozen_dict()
    identity = {
        "version": "BTC_PROSPECTIVE_THESIS_TAPE_V1",
        "asset": "BTC",
        "instrument_type": "UNDERLYING_RESEARCH",
        "decision": decision,
        "outcome_policy": frozen_policy,
        "outcome_due_at": due_at.isoformat(),
    }
    tape_fingerprint = _digest(identity)
    return {
        **identity,
        "status": "PROSPECTIVE_THESIS_FROZEN",
        "tape_fingerprint": tape_fingerprint,
        "decision_frozen_before_outcome": True,
        "future_outcome_present_in_decision": False,
        "options_contract_data_used": False,
        "options_execution_metadata_used": False,
        "options_pnl_measured": False,
        "options_trade_generated": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
    }


def verify_frozen_prospective_btc_thesis(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("status") != "PROSPECTIVE_THESIS_FROZEN":
        return False
    if record.get("instrument_type") != "UNDERLYING_RESEARCH":
        return False
    if record.get("futures_route_invoked") is True or record.get("futures_trade_generated") is True:
        return False
    decision = record.get("decision")
    if not _verify_underlying_decision(decision):
        return False
    try:
        policy = _policy_from_frozen(record.get("outcome_policy"))
        decision_at = _stamp(decision.get("decision_at"))
        due_at = _stamp(record.get("outcome_due_at"))
    except (TypeError, ValueError, KeyError):
        return False
    expected_due = decision_at + timedelta(hours=float(policy.evaluation_horizon_hours))
    if due_at != expected_due:
        return False
    identity = {
        "version": record.get("version"),
        "asset": record.get("asset"),
        "instrument_type": record.get("instrument_type"),
        "decision": decision,
        "outcome_policy": record.get("outcome_policy"),
        "outcome_due_at": record.get("outcome_due_at"),
    }
    return _digest(identity) == record.get("tape_fingerprint")


def resolve_prospective_btc_thesis(
    *,
    frozen_record: dict,
    resolution_at: datetime,
    forward_prices: list[BtcForwardPriceObservation],
) -> dict:
    """Attach a later BTC-only outcome without mutating the frozen thesis."""
    if not verify_frozen_prospective_btc_thesis(frozen_record):
        raise ValueError("prospective BTC thesis fingerprint mismatch")
    resolved_at = _utc(resolution_at)
    due_at = _stamp(frozen_record["outcome_due_at"])
    decision = dict(frozen_record["decision"])
    decision_at = _stamp(decision["decision_at"])

    for row in forward_prices:
        row.validated()
        if _utc(row.observed_at) > resolved_at:
            raise ValueError("resolver received BTC observation from after resolution_at")

    if resolved_at < due_at:
        return {
            "version": "BTC_PROSPECTIVE_THESIS_RESOLUTION_V1",
            "status": "THESIS_OUTCOME_NOT_DUE",
            "click_id": decision["click_id"],
            "decision_at": decision_at.isoformat(),
            "outcome_due_at": due_at.isoformat(),
            "resolution_at": resolved_at.isoformat(),
            "decision_fingerprint": decision["decision_fingerprint"],
            "tape_fingerprint": frozen_record["tape_fingerprint"],
            "outcome": None,
            "performance_eligible": False,
            "decision_rewritten": False,
            "options_pnl_measured": False,
            "trade_generated": False,
        }

    policy = _policy_from_frozen(frozen_record["outcome_policy"])
    frozen_decision_fingerprint = decision["decision_fingerprint"]
    outcome = _evaluate_outcome(
        decision_at=decision_at,
        entry_price=float(decision["decision_btc_price"]),
        market_direction=str(decision.get("market_direction") or "UNKNOWN"),
        forward_prices=forward_prices,
        policy=policy,
    )
    if decision["decision_fingerprint"] != frozen_decision_fingerprint:
        raise AssertionError("outcome evaluation mutated the frozen decision")

    status = "THESIS_OUTCOME_RESOLVED" if outcome.get("status") == "OUTCOME_RESOLVED" else "THESIS_OUTCOME_UNRESOLVED"
    payload = {
        "version": "BTC_PROSPECTIVE_THESIS_RESOLUTION_V1",
        "status": status,
        "click_id": decision["click_id"],
        "decision_at": decision_at.isoformat(),
        "outcome_due_at": due_at.isoformat(),
        "resolution_at": resolved_at.isoformat(),
        "decision_fingerprint": frozen_decision_fingerprint,
        "tape_fingerprint": frozen_record["tape_fingerprint"],
        "outcome": outcome,
        "performance_eligible": outcome.get("performance_eligible") is True,
        "decision_rewritten": False,
        "outcome_used_for_decision": False,
        "options_contract_data_used": False,
        "options_execution_metadata_used": False,
        "options_pnl_measured": False,
        "options_trade_generated": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
    }
    fingerprint_payload = dict(payload)
    payload["resolution_fingerprint"] = _digest(fingerprint_payload)
    return payload


def verify_prospective_btc_thesis_resolution(resolution: dict) -> bool:
    if not isinstance(resolution, dict) or resolution.get("status") != "THESIS_OUTCOME_RESOLVED":
        return False
    expected = resolution.get("resolution_fingerprint")
    if not isinstance(expected, str) or not expected:
        return False
    payload = {key: value for key, value in resolution.items() if key != "resolution_fingerprint"}
    return _digest(payload) == expected


class ImmutableProspectiveBtcThesisTape:
    """In-memory semantic tape; persistence may plug in later without changing rules."""

    def __init__(self) -> None:
        self._decisions: dict[str, dict] = {}
        self._resolutions: dict[str, dict] = {}

    def insert_frozen(self, record: dict) -> dict:
        if not verify_frozen_prospective_btc_thesis(record):
            raise ValueError("invalid frozen prospective BTC thesis")
        click_id = str(record["decision"]["click_id"])
        existing = self._decisions.get(click_id)
        if existing is None:
            self._decisions[click_id] = record
            return {"status": "INSERTED_FROZEN_THESIS", "record": record}
        if existing.get("tape_fingerprint") == record.get("tape_fingerprint"):
            return {"status": "IDEMPOTENT_FROZEN_THESIS", "record": existing}
        raise ValueError("conflicting prospective thesis cannot overwrite frozen decision")

    def attach_resolution(self, resolution: dict) -> dict:
        if not verify_prospective_btc_thesis_resolution(resolution):
            raise ValueError("only resolved, fingerprint-valid thesis outcomes may enter the tape")
        click_id = str(resolution.get("click_id") or "")
        decision = self._decisions.get(click_id)
        if decision is None:
            raise ValueError("cannot attach resolution before frozen thesis exists")
        if resolution.get("tape_fingerprint") != decision.get("tape_fingerprint"):
            raise ValueError("resolution does not belong to frozen thesis")
        if resolution.get("decision_fingerprint") != decision["decision"].get("decision_fingerprint"):
            raise ValueError("resolution does not match frozen decision fingerprint")
        existing = self._resolutions.get(click_id)
        if existing is None:
            self._resolutions[click_id] = resolution
            return {"status": "ATTACHED_THESIS_RESOLUTION", "resolution": resolution}
        if existing.get("resolution_fingerprint") == resolution.get("resolution_fingerprint"):
            return {"status": "IDEMPOTENT_THESIS_RESOLUTION", "resolution": existing}
        raise ValueError("conflicting later outcome cannot overwrite resolved thesis tape")

    def pending_as_of(self, as_of: datetime) -> list[dict]:
        cutoff = _utc(as_of)
        rows = []
        for click_id, decision in self._decisions.items():
            if click_id in self._resolutions:
                continue
            if _stamp(decision["outcome_due_at"]) <= cutoff:
                rows.append(decision)
        return sorted(rows, key=lambda row: (row["outcome_due_at"], row["decision"]["click_id"]))

    def rows(self) -> list[dict]:
        result = []
        for click_id, decision in self._decisions.items():
            result.append({
                "decision": decision,
                "resolution": self._resolutions.get(click_id),
            })
        return sorted(result, key=lambda row: row["decision"]["decision"]["decision_at"])

    def manifest(self) -> dict:
        directions: dict[str, int] = {}
        for row in self._decisions.values():
            direction = str(row["decision"].get("market_direction") or "UNKNOWN").upper()
            directions[direction] = directions.get(direction, 0) + 1
        return {
            "version": "BTC_PROSPECTIVE_THESIS_TAPE_MANIFEST_V1",
            "decision_count": len(self._decisions),
            "resolved_count": len(self._resolutions),
            "pending_count": len(self._decisions) - len(self._resolutions),
            "by_market_direction": dict(sorted(directions.items())),
            "decisions_immutable": True,
            "outcomes_stored_separately": True,
            "unresolved_outcome_admitted_as_resolution": False,
            "options_pnl_measured": False,
            "futures_route_allowed": False,
            "live_execution": False,
        }


def architecture_contract() -> dict:
    return {
        "version": "BTC_PROSPECTIVE_THESIS_TAPE_CONTRACT_V1",
        "purpose": "PROVE_BTC_MARKET_BRAIN_BEFORE_OPTIONS_ECONOMICS",
        "uses_same_historical_thesis_scoring_primitives": True,
        "decision_evidence_must_be_visible_by_click": True,
        "decision_frozen_before_outcome": True,
        "outcome_cannot_resolve_before_frozen_horizon": True,
        "outcome_observation_after_resolution_time_allowed": False,
        "decision_and_resolution_stored_separately": True,
        "conflicting_decision_overwrite_allowed": False,
        "conflicting_resolution_overwrite_allowed": False,
        "unresolved_outcome_admitted_as_resolution": False,
        "options_contract_data_required": False,
        "options_execution_metadata_required": False,
        "options_pnl_measured": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed": 0,
        "automatic_provider_or_scheduler_added": False,
        "research_only": True,
    }
