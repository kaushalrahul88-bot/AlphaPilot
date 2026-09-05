"""Audit Trading Economics historical calendar rows without fabricating first-seen time.

Trading Economics documents its Economic Calendar Point-in-Time product for
historical/backtest use and identifies ``Forecast`` as representative-economist
consensus. Historical event rows, however, may have ``LastUpdate`` at or after
the release because the same row is updated when the official actual arrives.
That row-update timestamp is not evidence of when AlphaPilot first observed the
pre-release survey consensus.

This module therefore validates historical consensus *values and event identity*
but intentionally refuses to convert them into ``MacroConsensusSnapshot``.
The strict exact-surprise engine continues to require a genuinely pre-release
AlphaPilot ``first_seen_at`` from prospective capture or another independently
validated source contract.

No BTC direction, Options/Futures trade, execution, or synthetic timestamp is
created here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.tradingeconomics_macro_consensus_provider import (
    COUNTRY,
    _METRICS,
    _bls_source,
    _forecast_value,
    _normalized_label,
    _provider_utc,
    _reference_period,
    TradingEconomicsConsensusTarget,
)

HistoricalAdmissionStatus = Literal[
    "PIT_VALUE_VERIFIED_FIRST_SEEN_UNPROVEN",
]


def _utc_exact(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _matching_row(rows: list[dict], aliases: tuple[str, ...]) -> dict:
    normalized_aliases = {_normalized_label(alias) for alias in aliases}
    matches = []
    for row in rows:
        event = _normalized_label(row.get("Event"))
        category = _normalized_label(row.get("Category"))
        if event in normalized_aliases or (not event and category in normalized_aliases):
            matches.append(row)
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one Trading Economics historical row for aliases={aliases!r}; found {len(matches)}"
        )
    return matches[0]


@dataclass(frozen=True)
class TradingEconomicsHistoricalPitAudit:
    event_key: str
    event_type: str
    reference_period: str
    release_at: datetime
    source_name: str
    calendar_ids: tuple[str, ...]
    values: dict[str, float]
    units: dict[str, str]
    provider_row_last_updates: tuple[datetime, ...]
    status: HistoricalAdmissionStatus = "PIT_VALUE_VERIFIED_FIRST_SEEN_UNPROVEN"
    provider_point_in_time_value_verified: bool = True
    exact_pre_release_first_seen_proven: bool = False
    macro_consensus_snapshot_may_be_constructed: bool = False
    exact_numeric_surprise_engine_admission: bool = False
    teforecast_used: bool = False
    synthetic_first_seen_assigned: bool = False
    btc_direction_generated: bool = False
    options_trade_generated: bool = False
    futures_trade_generated: bool = False

    def validated(self) -> "TradingEconomicsHistoricalPitAudit":
        if not self.event_key or not self.reference_period:
            raise ValueError("event_key and reference_period are required")
        _utc_exact(self.release_at, name="release_at")
        if self.source_name != "TRADING_ECONOMICS_POINT_IN_TIME":
            raise ValueError("historical PIT audit source must be TRADING_ECONOMICS_POINT_IN_TIME")
        if not self.calendar_ids or len(set(self.calendar_ids)) != len(self.calendar_ids):
            raise ValueError("unique Trading Economics CalendarIds are required")
        if not self.values or set(self.values) != set(self.units):
            raise ValueError("historical PIT values and units must have identical metric keys")
        if len(self.provider_row_last_updates) != len(self.calendar_ids):
            raise ValueError("one provider LastUpdate is required per CalendarId")
        if self.status != "PIT_VALUE_VERIFIED_FIRST_SEEN_UNPROVEN":
            raise ValueError("unsupported historical PIT admission status")
        if not self.provider_point_in_time_value_verified:
            raise ValueError("audit must verify provider PIT value semantics")
        if self.exact_pre_release_first_seen_proven:
            raise ValueError("historical PIT row does not prove AlphaPilot pre-release first_seen_at")
        if self.macro_consensus_snapshot_may_be_constructed or self.exact_numeric_surprise_engine_admission:
            raise ValueError("historical PIT audit cannot bypass strict consensus first-seen requirements")
        if self.teforecast_used or self.synthetic_first_seen_assigned:
            raise ValueError("TEForecast and synthetic first_seen are forbidden")
        if self.btc_direction_generated or self.options_trade_generated or self.futures_trade_generated:
            raise ValueError("historical PIT audit cannot generate direction or trades")
        return self


def audit_historical_point_in_time_rows(
    rows: object,
    *,
    target: TradingEconomicsConsensusTarget,
) -> TradingEconomicsHistoricalPitAudit:
    """Validate historical consensus values while keeping exact-surprise admission closed."""
    target = target.validated()
    release = target.release_at_utc
    if not isinstance(rows, list):
        raise ValueError("Trading Economics historical point-in-time response must be a list")
    dictionaries = [row for row in rows if isinstance(row, dict)]
    if len(dictionaries) != len(rows):
        raise ValueError("Trading Economics historical point-in-time response contains non-object row")

    values: dict[str, float] = {}
    units: dict[str, str] = {}
    calendar_ids: list[str] = []
    last_updates: list[datetime] = []

    for metric, aliases, canonical_unit in _METRICS[target.event_type]:
        row = _matching_row(dictionaries, aliases)
        if str(row.get("Country") or "").strip() != COUNTRY:
            raise ValueError("Trading Economics historical row must be United States")
        if not _bls_source(row):
            raise ValueError("historical CPI/Employment row must identify U.S. Bureau of Labor Statistics")
        if str(row.get("DateSpan") or "").strip() != "0":
            raise ValueError("historical PIT admission requires DateSpan=0 exact event timing")
        if _reference_period(row) != target.reference_period:
            raise ValueError("historical consensus reference period does not match target")
        if _provider_utc(row.get("Date"), name="Date") != release:
            raise ValueError("historical calendar release time does not match official expected_release_at")

        calendar_id = str(row.get("CalendarId") or "").strip()
        if not calendar_id:
            raise ValueError("Trading Economics CalendarId is required")
        if calendar_id in calendar_ids:
            raise ValueError("duplicate Trading Economics CalendarId in historical PIT bundle")

        # Forecast is the documented representative-economist consensus field.
        # TEForecast is deliberately not read, even when present.
        values[metric] = _forecast_value(row, canonical_unit=canonical_unit)
        units[metric] = canonical_unit
        calendar_ids.append(calendar_id)
        last_updates.append(_provider_utc(row.get("LastUpdate"), name="LastUpdate"))

    return TradingEconomicsHistoricalPitAudit(
        event_key=target.event_key,
        event_type=target.event_type,
        reference_period=target.reference_period,
        release_at=release,
        source_name="TRADING_ECONOMICS_POINT_IN_TIME",
        calendar_ids=tuple(calendar_ids),
        values=values,
        units=units,
        provider_row_last_updates=tuple(last_updates),
    ).validated()


def architecture_contract() -> dict:
    return {
        "version": "TRADING_ECONOMICS_HISTORICAL_PIT_AUDIT_V1",
        "provider_point_in_time_calendar_documented_for_backtesting": True,
        "representative_economist_forecast_field_used": True,
        "teforecast_used": False,
        "actual_may_exist_on_historical_event_row": True,
        "provider_last_update_may_be_at_or_after_release": True,
        "provider_last_update_treated_as_consensus_first_seen": False,
        "synthetic_pre_release_first_seen_allowed": False,
        "macro_consensus_snapshot_constructed": False,
        "exact_numeric_surprise_engine_admission": False,
        "prospective_first_seen_capture_remains_authoritative": True,
        "historical_backfill_auto_enabled": False,
        "btc_direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
