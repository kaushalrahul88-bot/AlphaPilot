"""Trading Economics pre-release consensus provider for exact BTC macro events.

V1 captures only the representative-economist ``Forecast`` field from the
Trading Economics economic calendar. ``TEForecast`` is intentionally ignored:
it is Trading Economics' own model projection, not survey consensus.

Each configured target supplies the official AlphaPilot event key, reference
period and exact release timestamp. Calendar rows must match that official time,
have DateSpan=0, come from the U.S. Bureau of Labor Statistics, carry no Actual
value yet, and be first seen strictly before release. Provider ``LastUpdate`` is
preserved separately from AlphaPilot ``first_seen_at``.

Supported V1 event bundles:
- CPI: Inflation Rate MoM + Core Inflation Rate MoM
- Employment Situation: Non Farm Payrolls + Unemployment Rate +
  Average Hourly Earnings MoM

No numeric surprise, BTC direction, Options/Futures trade, or execution is
created in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
import re
from typing import Callable, Literal
from urllib.parse import quote

import httpx

from app.crypto_macro_event_intelligence import MacroConsensusSnapshot

TradingEconomicsEventType = Literal["CPI", "EMPLOYMENT_SITUATION"]
BASE_URL = "https://api.tradingeconomics.com"
COUNTRY = "United States"


def _utc_exact(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _provider_utc(value: object, *, name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Trading Economics {name} is required")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid Trading Economics {name}: {text!r}") from exc
    if parsed.tzinfo is None:
        # Trading Economics calendar API documents Date/LastUpdate as UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_label(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower())
    return re.sub(r"\s+", " ", text).strip()


def _reference_period(row: dict) -> str:
    raw = row.get("ReferenceDate")
    if raw in (None, ""):
        raise ValueError("Trading Economics ReferenceDate is required for exact event matching")
    reference = _provider_utc(raw, name="ReferenceDate")
    return f"{reference.year:04d}-{reference.month:02d}"


def _bls_source(row: dict) -> bool:
    source = _normalized_label(row.get("Source"))
    source_url = str(row.get("SourceURL") or "").strip().lower()
    return "bureau of labor statistics" in source or "bls.gov" in source_url


def _actual_missing(row: dict) -> bool:
    actual = row.get("Actual")
    actual_value = row.get("ActualValue")
    return (actual is None or str(actual).strip() == "") and actual_value in (None, "")


def _parse_forecast_text(value: object, *, unit: str) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*([KMB%]?)", text, re.IGNORECASE)
    if not match:
        raise ValueError(f"unsupported Trading Economics Forecast format: {value!r}")
    number = float(match.group(1))
    suffix = match.group(2).upper()
    if not isfinite(number):
        raise ValueError("Trading Economics Forecast must be finite")
    normalized_unit = str(unit or "").strip().upper()
    if normalized_unit in {"%", "PERCENT"}:
        if suffix not in {"", "%"}:
            raise ValueError("percentage consensus has incompatible Forecast suffix")
        return number
    if normalized_unit == "K":
        if suffix not in {"", "K"}:
            raise ValueError("thousand-unit consensus has incompatible Forecast suffix")
        return number
    raise ValueError(f"unsupported Trading Economics consensus unit: {unit!r}")


def _forecast_value(row: dict, *, canonical_unit: str) -> float:
    unit = str(row.get("Unit") or "").strip()
    text_value = _parse_forecast_text(row.get("Forecast"), unit=unit)
    numeric_raw = row.get("ForecastValue")
    numeric_value: float | None = None
    if numeric_raw not in (None, ""):
        numeric = float(numeric_raw)
        if not isfinite(numeric):
            raise ValueError("Trading Economics ForecastValue must be finite")
        normalized_unit = unit.upper()
        if normalized_unit in {"%", "PERCENT"}:
            numeric_value = numeric
        elif normalized_unit == "K":
            # values=true schema documents 175K as ForecastValue=175000.
            # If both representations are present, accept either provider
            # encoding only when it agrees with the human-readable Forecast.
            if text_value is not None and abs(numeric - text_value) <= 1e-9:
                numeric_value = numeric
            else:
                numeric_value = numeric / 1000.0
        else:
            raise ValueError(f"unsupported Trading Economics consensus unit: {unit!r}")
    if text_value is None and numeric_value is None:
        raise ValueError("representative-economist Forecast is missing")
    if text_value is not None and numeric_value is not None and abs(text_value - numeric_value) > 1e-6:
        raise ValueError("Trading Economics Forecast and ForecastValue disagree")
    value = text_value if text_value is not None else numeric_value
    if value is None:
        raise ValueError("representative-economist Forecast is missing")
    if canonical_unit == "PERCENT":
        if unit.upper() not in {"%", "PERCENT"}:
            raise ValueError("expected percentage Trading Economics unit")
        return float(value)
    if canonical_unit == "THOUSAND_PERSONS":
        if unit.upper() != "K":
            raise ValueError("expected K unit for Non Farm Payrolls consensus")
        return float(value)
    raise ValueError(f"unsupported canonical consensus unit: {canonical_unit!r}")


@dataclass(frozen=True)
class TradingEconomicsConsensusTarget:
    event_key: str
    event_type: TradingEconomicsEventType
    reference_period: str
    expected_release_at: datetime

    def validated(self) -> "TradingEconomicsConsensusTarget":
        if not str(self.event_key or "").strip():
            raise ValueError("event_key is required")
        if self.event_type not in {"CPI", "EMPLOYMENT_SITUATION"}:
            raise ValueError("Trading Economics V1 supports CPI and EMPLOYMENT_SITUATION only")
        if not re.fullmatch(r"\d{4}-\d{2}", str(self.reference_period or "")):
            raise ValueError("reference_period must use YYYY-MM")
        release = _utc_exact(self.expected_release_at, name="expected_release_at")
        if self.event_type == "CPI" and not str(self.event_key).startswith("BLS:CPI:"):
            raise ValueError("CPI target event_key must use BLS:CPI:<reference> identity")
        if self.event_type == "EMPLOYMENT_SITUATION" and not str(self.event_key).startswith("BLS:EMPLOYMENT_SITUATION:"):
            raise ValueError("Employment target event_key must use BLS:EMPLOYMENT_SITUATION:<reference> identity")
        if not self.event_key.endswith(self.reference_period):
            raise ValueError("event_key reference period must match target reference_period")
        if release.year < 2000:
            raise ValueError("expected_release_at is implausibly old")
        return self

    @property
    def release_at_utc(self) -> datetime:
        return _utc_exact(self.expected_release_at, name="expected_release_at")


@dataclass(frozen=True)
class TradingEconomicsConsensusPolicy:
    enabled: bool = False
    api_key: str = ""
    timeout_seconds: float = 10.0

    def validated(self) -> "TradingEconomicsConsensusPolicy":
        timeout = float(self.timeout_seconds)
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        if self.enabled and not str(self.api_key or "").strip():
            raise ValueError("Trading Economics consensus capture requires api_key")
        return self


_METRICS: dict[str, tuple[tuple[str, tuple[str, ...], str], ...]] = {
    "CPI": (
        ("headline_mom_pct", ("inflation rate mom",), "PERCENT"),
        ("core_mom_pct", ("core inflation rate mom",), "PERCENT"),
    ),
    "EMPLOYMENT_SITUATION": (
        ("payroll_change_k", ("non farm payrolls", "nonfarm payrolls"), "THOUSAND_PERSONS"),
        ("unemployment_rate_pct", ("unemployment rate",), "PERCENT"),
        ("avg_hourly_earnings_mom_pct", ("average hourly earnings mom",), "PERCENT"),
    ),
}


class TradingEconomicsMacroConsensusProvider:
    def __init__(
        self,
        policy: TradingEconomicsConsensusPolicy | None = None,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.policy = (policy or TradingEconomicsConsensusPolicy()).validated()
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("Trading Economics macro consensus collection is disabled by policy")

    @staticmethod
    def _matching_row(rows: list[dict], aliases: tuple[str, ...]) -> dict:
        normalized_aliases = {_normalized_label(alias) for alias in aliases}
        matches = []
        for row in rows:
            event = _normalized_label(row.get("Event"))
            category = _normalized_label(row.get("Category"))
            if event in normalized_aliases or (not event and category in normalized_aliases):
                matches.append(row)
        if len(matches) != 1:
            raise ValueError(f"expected exactly one Trading Economics calendar row for aliases={aliases!r}; found {len(matches)}")
        return matches[0]

    def parse_consensus(
        self,
        rows: object,
        *,
        target: TradingEconomicsConsensusTarget,
        first_seen_at: datetime,
    ) -> MacroConsensusSnapshot:
        target = target.validated()
        seen = _utc_exact(first_seen_at, name="first_seen_at")
        release = target.release_at_utc
        if seen >= release:
            raise ValueError("Trading Economics consensus first_seen_at must be strictly before official release")
        if not isinstance(rows, list):
            raise ValueError("Trading Economics calendar response must be a list")
        dictionaries = [row for row in rows if isinstance(row, dict)]
        if len(dictionaries) != len(rows):
            raise ValueError("Trading Economics calendar response contains non-object row")

        values: dict[str, float] = {}
        units: dict[str, str] = {}
        provider_times: list[datetime] = []
        calendar_ids: list[str] = []
        for metric, aliases, canonical_unit in _METRICS[target.event_type]:
            row = self._matching_row(dictionaries, aliases)
            if str(row.get("Country") or "").strip() != COUNTRY:
                raise ValueError("Trading Economics consensus row must be United States")
            if not _bls_source(row):
                raise ValueError("Trading Economics exact CPI/Employment row must identify U.S. Bureau of Labor Statistics")
            if str(row.get("DateSpan") or "").strip() != "0":
                raise ValueError("Trading Economics consensus requires DateSpan=0 exact event timing")
            if _reference_period(row) != target.reference_period:
                raise ValueError("Trading Economics consensus reference period does not match target")
            row_release = _provider_utc(row.get("Date"), name="Date")
            if row_release != release:
                raise ValueError("Trading Economics calendar release time does not match official expected_release_at")
            if not _actual_missing(row):
                raise ValueError("Trading Economics row already contains Actual; post-release consensus is not admitted")
            provider_time = _provider_utc(row.get("LastUpdate"), name="LastUpdate")
            if provider_time > seen:
                raise ValueError("Trading Economics LastUpdate cannot be after AlphaPilot first_seen_at")
            if provider_time >= release:
                raise ValueError("Trading Economics consensus provider time must be before official release")
            calendar_id = str(row.get("CalendarId") or "").strip()
            if not calendar_id:
                raise ValueError("Trading Economics CalendarId is required")
            values[metric] = _forecast_value(row, canonical_unit=canonical_unit)
            units[metric] = canonical_unit
            provider_times.append(provider_time)
            calendar_ids.append(calendar_id)

        source_ref = (
            f"{BASE_URL}/calendar/country/{quote(COUNTRY.lower())}/"
            f"{release.date().isoformat()}/{release.date().isoformat()}"
            f"?values=true&f=json#calendar_ids={','.join(sorted(calendar_ids))}"
        )
        return MacroConsensusSnapshot(
            event_key=target.event_key,
            event_type=target.event_type,
            release_at=release,
            provider_time=max(provider_times),
            first_seen_at=seen,
            source_name="TRADING_ECONOMICS",
            source_ref=source_ref,
            values=values,
            units=units,
            source_verified=True,
        ).validated()

    def fetch_consensus(self, *, target: TradingEconomicsConsensusTarget) -> MacroConsensusSnapshot:
        self._require_enabled()
        target = target.validated()
        release = target.release_at_utc
        date_text = release.date().isoformat()
        url = f"{BASE_URL}/calendar/country/{quote(COUNTRY.lower())}/{date_text}/{date_text}"
        params = {"c": str(self.policy.api_key).strip(), "values": "true", "f": "json"}
        if self._client is not None:
            response = self._client.get(url, params=params, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(url, params=params)
        response.raise_for_status()
        rows = response.json()
        first_seen = _utc_exact(self._clock(), name="clock first_seen_at")
        return self.parse_consensus(rows, target=target, first_seen_at=first_seen)


def architecture_contract() -> dict:
    return {
        "version": "TRADING_ECONOMICS_EXACT_MACRO_CONSENSUS_PROVIDER_V1",
        "enabled_by_default": False,
        "country": COUNTRY,
        "representative_economist_forecast_used": True,
        "te_model_forecast_used": False,
        "values_true_numeric_fields_supported": True,
        "calendar_id_preserved_in_source_ref": True,
        "official_expected_release_timestamp_required": True,
        "date_span_zero_required": True,
        "reference_period_match_required": True,
        "bls_source_required_for_supported_events": True,
        "actual_must_be_missing": True,
        "provider_last_update_preserved": True,
        "provider_last_update_must_precede_release": True,
        "alpha_first_seen_must_precede_release": True,
        "post_release_consensus_admitted": False,
        "point_in_time_historical_api_documented": True,
        "historical_backfill_enabled_in_v1": False,
        "cpi_components": ["headline_mom_pct", "core_mom_pct"],
        "employment_components": ["payroll_change_k", "unemployment_rate_pct", "avg_hourly_earnings_mom_pct"],
        "numeric_surprise_generated": False,
        "btc_direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "network_request_at_import": False,
        "research_only": True,
    }
