from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


IST = ZoneInfo("Asia/Kolkata")
PROTOCOL_REVISION = "paper-session-quality-v1-2026-08-25"
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
ATTEST_AFTER = time(15, 35)
MIN_COVERAGE_MINUTES = 210


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CriticalHealthChecks(StrictModel):
    api: bool
    quote: bool
    candles: bool
    options: bool


class SessionHealthSnapshot(StrictModel):
    captured_at: datetime
    symbol: str = Field(min_length=1, max_length=30)
    checks: CriticalHealthChecks


class SessionDataIncident(StrictModel):
    captured_at: datetime
    source: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=160)


class SessionPaperTrade(StrictModel):
    trade_id: str = Field(min_length=1, max_length=100)
    status: Literal["OPEN", "CLOSED"]
    paper_only: Literal[True] = True
    live_execution_enabled: Literal[False] = False
    order_endpoint_called: Literal[False] = False
    opened_at: datetime
    closed_at: datetime | None = None
    mark_sequence: int = Field(ge=0)
    last_source_id: str = Field(min_length=1, max_length=160)


class PaperSessionAttestationRequest(StrictModel):
    session_date: date
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    health_snapshots: list[SessionHealthSnapshot] = Field(default_factory=list)
    data_incidents: list[SessionDataIncident] = Field(default_factory=list)
    paper_trades: list[SessionPaperTrade] = Field(default_factory=list)


def _ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST)


def _in_session(value: datetime) -> bool:
    observed = _ist(value)
    clock = observed.time().replace(tzinfo=None)
    return observed.weekday() < 5 and SESSION_OPEN <= clock <= SESSION_CLOSE


def _phase(value: datetime) -> str | None:
    clock = _ist(value).time().replace(tzinfo=None)
    if time(9, 15) <= clock <= time(10, 30):
        return "EARLY"
    if time(11, 0) <= clock <= time(13, 30):
        return "MID"
    if time(14, 0) <= clock <= time(15, 30):
        return "LATE"
    return None


def _all_pass(snapshot: SessionHealthSnapshot) -> bool:
    checks = snapshot.checks
    return checks.api and checks.quote and checks.candles and checks.options


def _attestation_id(request: PaperSessionAttestationRequest) -> str:
    encoded = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "session-" + hashlib.sha256(encoded).hexdigest()[:24]


def evaluate_paper_session(request: PaperSessionAttestationRequest) -> dict:
    blockers: list[str] = []
    now = _ist(request.evaluated_at)

    if now.date() != request.session_date:
        blockers.append("ATTESTATION_DATE_MISMATCH")
    if request.session_date.weekday() >= 5:
        blockers.append("NON_TRADING_WEEKDAY")
    if now.time().replace(tzinfo=None) < ATTEST_AFTER:
        blockers.append("SESSION_NOT_FINISHED")

    snapshots = [
        row
        for row in request.health_snapshots
        if _ist(row.captured_at).date() == request.session_date and _in_session(row.captured_at)
    ]
    failed_snapshots = [row for row in snapshots if not _all_pass(row)]
    if failed_snapshots:
        blockers.append("CRITICAL_HEALTH_FAILURE_RECORDED")

    phases = {"EARLY": [], "MID": [], "LATE": []}
    for snapshot in snapshots:
        phase = _phase(snapshot.captured_at)
        if phase and _all_pass(snapshot):
            phases[phase].append(snapshot)

    for phase in ("EARLY", "MID", "LATE"):
        if not phases[phase]:
            blockers.append("MISSING_" + phase + "_HEALTH_COVERAGE")

    passing_times = sorted(
        _ist(row.captured_at)
        for rows in phases.values()
        for row in rows
    )
    coverage_minutes = (
        (passing_times[-1] - passing_times[0]).total_seconds() / 60.0
        if len(passing_times) >= 2
        else 0.0
    )
    if coverage_minutes < MIN_COVERAGE_MINUTES:
        blockers.append("INSUFFICIENT_SESSION_COVERAGE")

    incidents = [
        row
        for row in request.data_incidents
        if _ist(row.captured_at).date() == request.session_date
        and SESSION_OPEN <= _ist(row.captured_at).time().replace(tzinfo=None) <= ATTEST_AFTER
    ]
    if incidents:
        blockers.append("DATA_INCIDENT_RECORDED")

    session_trades = [
        row
        for row in request.paper_trades
        if _ist(row.opened_at).date() == request.session_date
    ]
    closed_trades = [
        row
        for row in session_trades
        if row.status == "CLOSED" and row.closed_at is not None
    ]
    if not closed_trades:
        blockers.append("NO_COMPLETED_PAPER_TRADE")
    if any(row.status == "OPEN" for row in session_trades):
        blockers.append("UNRESOLVED_PAPER_POSITION")

    for trade in session_trades:
        if not _in_session(trade.opened_at):
            blockers.append("PAPER_TRADE_OPENED_OUTSIDE_SESSION")
        if trade.status == "CLOSED":
            if trade.closed_at is None:
                blockers.append("CLOSED_TRADE_MISSING_CLOSE_TIME")
            else:
                if not _in_session(trade.closed_at):
                    blockers.append("PAPER_TRADE_CLOSED_OUTSIDE_SESSION")
                if _ist(trade.closed_at) < _ist(trade.opened_at):
                    blockers.append("PAPER_TRADE_TIME_ORDER_INVALID")
            if trade.mark_sequence < 1:
                blockers.append("PAPER_TRADE_HAS_NO_VERIFIED_MARK")
            if not trade.last_source_id.startswith("groww-chain-"):
                blockers.append("PAPER_TRADE_SOURCE_UNVERIFIED")

    blockers = list(dict.fromkeys(blockers))
    clean = not blockers
    return {
        "schema_version": 1,
        "protocol_revision": PROTOCOL_REVISION,
        "attestation_id": _attestation_id(request),
        "session_date": request.session_date.isoformat(),
        "evaluated_at": now.isoformat(),
        "status": "CLEAN_SESSION_ATTESTED" if clean else "SESSION_NOT_CLEAN",
        "clean_session_count_increment": 1 if clean else 0,
        "eligible_for_controlled_live_evidence": clean,
        "live_execution_enabled": False,
        "order_endpoint_called": False,
        "blockers": blockers,
        "coverage": {
            "passing_snapshots": len(passing_times),
            "failed_snapshots": len(failed_snapshots),
            "early_passes": len(phases["EARLY"]),
            "mid_passes": len(phases["MID"]),
            "late_passes": len(phases["LATE"]),
            "coverage_minutes": round(coverage_minutes, 1),
            "minimum_coverage_minutes": MIN_COVERAGE_MINUTES,
        },
        "evidence": {
            "data_incidents": len(incidents),
            "session_paper_trades": len(session_trades),
            "completed_paper_trades": len(closed_trades),
        },
        "scope": "BROWSER_LOCAL_PAPER_VALIDATION_NOT_TAMPER_EVIDENT",
    }
