"""Exact-time macro event intelligence contract for BTC Crypto Brain.

The generic layer separates three facts that are often incorrectly collapsed in
backtests:
1. an official release and its exact release timestamp;
2. a consensus snapshot that was actually available *before* that release; and
3. later revisions or interpretations.

A numeric surprise may be computed only from (1) and (2). This generic module
never assigns BTC direction: CPI, payrolls, FOMC statements and press conferences
need event-specific semantics plus contemporaneous market confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Literal

MacroEventType = Literal[
    "CPI",
    "EMPLOYMENT_SITUATION",
    "FOMC_STATEMENT",
    "FOMC_PRESS_CONFERENCE",
    "OTHER_OFFICIAL_MACRO",
]
ReleaseStage = Literal["FIRST_RELEASE", "REVISION"]


def _utc_exact(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite_map(name: str, values: dict[str, float]) -> dict[str, float]:
    if not isinstance(values, dict) or not values:
        raise ValueError(f"{name} must be a non-empty mapping")
    normalized: dict[str, float] = {}
    for key, value in values.items():
        metric = str(key or "").strip()
        if not metric:
            raise ValueError(f"{name} metric key is required")
        number = float(value)
        if not isfinite(number):
            raise ValueError(f"{name}[{metric!r}] must be finite")
        normalized[metric] = number
    return normalized


def _state_hash(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return sha256(raw).hexdigest()


@dataclass(frozen=True)
class OfficialMacroRelease:
    event_key: str
    event_type: MacroEventType
    reference_period: str
    release_at: datetime
    first_seen_at: datetime
    official_source: str
    official_source_ref: str
    values: dict[str, float]
    units: dict[str, str]
    release_stage: ReleaseStage = "FIRST_RELEASE"
    revision_number: int = 0
    revises_event_key: str | None = None

    def validated(self) -> "OfficialMacroRelease":
        if not str(self.event_key or "").strip():
            raise ValueError("event_key is required")
        if self.event_type not in {
            "CPI", "EMPLOYMENT_SITUATION", "FOMC_STATEMENT", "FOMC_PRESS_CONFERENCE", "OTHER_OFFICIAL_MACRO"
        }:
            raise ValueError("unsupported macro event_type")
        if not str(self.reference_period or "").strip():
            raise ValueError("reference_period is required")
        release = _utc_exact(self.release_at, name="release_at")
        seen = _utc_exact(self.first_seen_at, name="first_seen_at")
        if release > seen:
            raise ValueError("official macro release cannot be first seen before its release timestamp")
        if not str(self.official_source or "").strip() or not str(self.official_source_ref or "").strip():
            raise ValueError("official_source and official_source_ref are required")
        values = _finite_map("values", self.values)
        if not isinstance(self.units, dict) or set(self.units) != set(values):
            raise ValueError("units must contain exactly the same metric keys as values")
        if any(not str(unit or "").strip() for unit in self.units.values()):
            raise ValueError("every macro value requires a unit")
        if self.release_stage == "FIRST_RELEASE":
            if int(self.revision_number) != 0 or self.revises_event_key is not None:
                raise ValueError("FIRST_RELEASE must use revision_number=0 and no revises_event_key")
        elif self.release_stage == "REVISION":
            if int(self.revision_number) <= 0 or not str(self.revises_event_key or "").strip():
                raise ValueError("REVISION requires positive revision_number and revises_event_key")
        else:
            raise ValueError("unsupported release_stage")
        return self

    @property
    def normalized_release_at(self) -> datetime:
        return _utc_exact(self.release_at, name="release_at")

    @property
    def normalized_first_seen_at(self) -> datetime:
        return _utc_exact(self.first_seen_at, name="first_seen_at")


@dataclass(frozen=True)
class MacroConsensusSnapshot:
    event_key: str
    event_type: MacroEventType
    release_at: datetime
    provider_time: datetime | None
    first_seen_at: datetime
    source_name: str
    source_ref: str
    values: dict[str, float]
    units: dict[str, str]
    source_verified: bool = True

    def validated(self) -> "MacroConsensusSnapshot":
        if not str(self.event_key or "").strip():
            raise ValueError("event_key is required")
        if self.event_type not in {
            "CPI", "EMPLOYMENT_SITUATION", "FOMC_STATEMENT", "FOMC_PRESS_CONFERENCE", "OTHER_OFFICIAL_MACRO"
        }:
            raise ValueError("unsupported macro event_type")
        release = _utc_exact(self.release_at, name="release_at")
        seen = _utc_exact(self.first_seen_at, name="first_seen_at")
        if seen >= release:
            raise ValueError("consensus first_seen_at must be strictly before macro release_at")
        if self.provider_time is not None:
            provider = _utc_exact(self.provider_time, name="provider_time")
            if provider > seen:
                raise ValueError("consensus provider_time cannot be after AlphaPilot first_seen_at")
        if not str(self.source_name or "").strip() or not str(self.source_ref or "").strip():
            raise ValueError("consensus source_name and source_ref are required")
        if self.source_verified is not True:
            raise ValueError("unverified consensus is not admitted to exact macro surprise calculation")
        values = _finite_map("values", self.values)
        if not isinstance(self.units, dict) or set(self.units) != set(values):
            raise ValueError("consensus units must contain exactly the same metric keys as values")
        if any(not str(unit or "").strip() for unit in self.units.values()):
            raise ValueError("every consensus value requires a unit")
        return self

    @property
    def state_hash(self) -> str:
        self.validated()
        return _state_hash({
            "event_key": self.event_key,
            "event_type": self.event_type,
            "release_at": _utc_exact(self.release_at, name="release_at").isoformat(),
            "provider_time": None if self.provider_time is None else _utc_exact(self.provider_time, name="provider_time").isoformat(),
            "source_name": self.source_name,
            "source_ref": self.source_ref,
            "values": _finite_map("values", self.values),
            "units": dict(sorted(self.units.items())),
        })


@dataclass(frozen=True)
class MacroNumericSurprise:
    event_key: str
    event_type: MacroEventType
    release_at: datetime
    release_first_seen_at: datetime
    consensus_first_seen_at: datetime
    actual: dict[str, float]
    consensus: dict[str, float]
    surprise: dict[str, float]
    units: dict[str, str]
    direction: str = "UNKNOWN"
    standalone_direction_allowed: bool = False

    def validated(self) -> "MacroNumericSurprise":
        _utc_exact(self.release_at, name="release_at")
        release_seen = _utc_exact(self.release_first_seen_at, name="release_first_seen_at")
        consensus_seen = _utc_exact(self.consensus_first_seen_at, name="consensus_first_seen_at")
        if consensus_seen >= _utc_exact(self.release_at, name="release_at"):
            raise ValueError("numeric surprise requires consensus known before release")
        if release_seen < _utc_exact(self.release_at, name="release_at"):
            raise ValueError("release cannot be first seen before official release time")
        if self.direction != "UNKNOWN" or self.standalone_direction_allowed:
            raise ValueError("generic macro numeric surprise cannot assign BTC direction")
        return self


def compute_numeric_surprise(
    release: OfficialMacroRelease,
    consensus: MacroConsensusSnapshot,
) -> MacroNumericSurprise:
    official = release.validated()
    expected = consensus.validated()
    if official.release_stage != "FIRST_RELEASE":
        raise ValueError("macro surprise must use immutable FIRST_RELEASE, not a later revision")
    if official.event_key != expected.event_key or official.event_type != expected.event_type:
        raise ValueError("release and consensus must refer to the same macro event")
    if official.normalized_release_at != _utc_exact(expected.release_at, name="consensus release_at"):
        raise ValueError("release and consensus release_at timestamps must match exactly")
    common = sorted(set(official.values) & set(expected.values))
    if not common:
        raise ValueError("release and consensus have no common numeric metrics")
    units: dict[str, str] = {}
    actual: dict[str, float] = {}
    expected_values: dict[str, float] = {}
    surprise: dict[str, float] = {}
    for metric in common:
        if official.units[metric] != expected.units[metric]:
            raise ValueError(f"unit mismatch for macro metric {metric!r}")
        actual[metric] = float(official.values[metric])
        expected_values[metric] = float(expected.values[metric])
        surprise[metric] = actual[metric] - expected_values[metric]
        units[metric] = official.units[metric]
    return MacroNumericSurprise(
        event_key=official.event_key,
        event_type=official.event_type,
        release_at=official.normalized_release_at,
        release_first_seen_at=official.normalized_first_seen_at,
        consensus_first_seen_at=_utc_exact(expected.first_seen_at, name="consensus first_seen_at"),
        actual=actual,
        consensus=expected_values,
        surprise=surprise,
        units=units,
    ).validated()


def architecture_contract() -> dict:
    return {
        "version": "CRYPTO_EXACT_MACRO_EVENT_INTELLIGENCE_V1",
        "official_release_timestamp_required": True,
        "timezone_aware_release_timestamp_required": True,
        "official_first_seen_required": True,
        "consensus_required_for_numeric_surprise": True,
        "consensus_must_be_first_seen_before_release": True,
        "unverified_consensus_allowed": False,
        "first_release_required_for_surprise": True,
        "revision_may_replace_first_release": False,
        "generic_numeric_surprise_assigns_btc_direction": False,
        "event_specific_semantics_required_for_direction": True,
        "market_confirmation_required_before_directional_admission": True,
        "fomc_statement_and_press_conference_are_separate_events": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
