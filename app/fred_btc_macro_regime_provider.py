"""FRED/ALFRED macro-regime provider for the research-only BTC Crypto Brain.

This provider intentionally serves a *daily macro regime* role, not an intraday
macro-surprise feed. FRED real-time periods allow vintage-aware reconstruction of
what values were known on a calendar date, but FRED documents that release dates
do not necessarily equal exact availability time on FRED/ALFRED. Therefore a
historical vintage reconstructed for the same calendar day as a click is not
accepted as proof of intraday visibility.

Series V1:
- DTWEXBGS: Nominal Broad U.S. Dollar Index (daily)
- DFII10: 10-year inflation-indexed Treasury yield (daily)
- NASDAQCOM: Nasdaq Composite daily close
- VIXCLS: VIX daily close

No trade or BTC direction is generated here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Any, Callable

import httpx

FRED_SERIES_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "BROAD_USD": "DTWEXBGS",
    "REAL_YIELD_10Y": "DFII10",
    "NASDAQ_COMPOSITE": "NASDAQCOM",
    "VIX": "VIXCLS",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _finite(name: str, value: Any) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _date(value: Any, *, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid {name}: {value!r}") from exc


@dataclass(frozen=True)
class FredMacroRegimePolicy:
    enabled: bool = False
    api_key: str = ""
    timeout_seconds: float = 10.0
    lookback_days: int = 45

    def validated(self) -> "FredMacroRegimePolicy":
        if self.enabled and not str(self.api_key or "").strip():
            raise ValueError("FRED macro regime enabled but API key is missing")
        if not isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        if int(self.lookback_days) < 7:
            raise ValueError("lookback_days must be >= 7")
        return self


@dataclass(frozen=True)
class FredSeriesVintageChange:
    series_id: str
    vintage_date: date
    latest_observation_date: date
    previous_observation_date: date
    latest_value: float
    previous_value: float
    realtime_start: date
    realtime_end: date
    change_value: float
    change_unit: str

    def validated(self) -> "FredSeriesVintageChange":
        if self.series_id not in set(SERIES.values()):
            raise ValueError("unsupported FRED series_id")
        if self.latest_observation_date <= self.previous_observation_date:
            raise ValueError("latest FRED observation must be after previous observation")
        _finite("latest_value", self.latest_value)
        _finite("previous_value", self.previous_value)
        _finite("change_value", self.change_value)
        if self.change_unit not in {"PCT", "BPS"}:
            raise ValueError("change_unit must be PCT or BPS")
        if self.realtime_start > self.vintage_date or self.realtime_end < self.vintage_date:
            raise ValueError("FRED observation real-time period does not cover requested vintage_date")
        return self


@dataclass(frozen=True)
class FredBtcMacroRegimeCapture:
    vintage_date: date
    first_seen_at: datetime
    broad_usd: FredSeriesVintageChange
    real_yield_10y: FredSeriesVintageChange
    nasdaq_composite: FredSeriesVintageChange
    vix: FredSeriesVintageChange
    historical_vintage_reconstruction: bool
    exact_intraday_availability_proven: bool
    provider: str = "FRED_ALFRED"

    def validated(self) -> "FredBtcMacroRegimeCapture":
        rows = (self.broad_usd, self.real_yield_10y, self.nasdaq_composite, self.vix)
        for row in rows:
            row.validated()
            if row.vintage_date != self.vintage_date:
                raise ValueError("all FRED macro rows must use the same vintage_date")
        if self.historical_vintage_reconstruction and self.exact_intraday_availability_proven:
            raise ValueError("historical FRED vintage reconstruction cannot prove exact intraday availability")
        return self

    @property
    def broad_usd_change_pct(self) -> float:
        return float(self.broad_usd.change_value)

    @property
    def real_yield_change_bps(self) -> float:
        return float(self.real_yield_10y.change_value)

    @property
    def nasdaq_change_pct(self) -> float:
        return float(self.nasdaq_composite.change_value)

    @property
    def vix_change_pct(self) -> float:
        return float(self.vix.change_value)


class FredBtcMacroRegimeProvider:
    def __init__(
        self,
        policy: FredMacroRegimePolicy | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.policy = (policy or FredMacroRegimePolicy()).validated()
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("FRED BTC macro regime collection is disabled by policy")

    def _get_json(self, *, series_id: str, vintage_date: date) -> dict:
        self._require_enabled()
        params = {
            "series_id": series_id,
            "api_key": self.policy.api_key,
            "file_type": "json",
            "realtime_start": vintage_date.isoformat(),
            "realtime_end": vintage_date.isoformat(),
            "observation_start": (vintage_date - timedelta(days=int(self.policy.lookback_days))).isoformat(),
            "observation_end": vintage_date.isoformat(),
            "sort_order": "desc",
        }
        if self._client is not None:
            response = self._client.get(FRED_SERIES_OBSERVATIONS_URL, params=params, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(FRED_SERIES_OBSERVATIONS_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
            raise ValueError("invalid FRED series observations payload")
        return payload

    @staticmethod
    def _two_latest(payload: dict, *, series_id: str, vintage_date: date, unit: str) -> FredSeriesVintageChange:
        valid: list[tuple[date, float, date, date]] = []
        for raw in payload.get("observations", []):
            if not isinstance(raw, dict):
                continue
            raw_value = raw.get("value")
            if raw_value in {None, "", "."}:
                continue
            try:
                obs_date = _date(raw.get("date"), name="observation date")
                value = _finite("FRED observation value", raw_value)
                realtime_start = _date(raw.get("realtime_start"), name="realtime_start")
                realtime_end = _date(raw.get("realtime_end"), name="realtime_end")
            except (TypeError, ValueError):
                continue
            if not (realtime_start <= vintage_date <= realtime_end):
                continue
            valid.append((obs_date, value, realtime_start, realtime_end))
        valid.sort(key=lambda row: row[0], reverse=True)
        if len(valid) < 2:
            raise ValueError(f"FRED series {series_id} has fewer than two valid observations in requested vintage")
        latest, previous = valid[0], valid[1]
        if unit == "PCT":
            if previous[1] == 0:
                raise ValueError(f"FRED series {series_id} previous value is zero; percent change undefined")
            change = (latest[1] - previous[1]) / abs(previous[1]) * 100.0
        elif unit == "BPS":
            change = (latest[1] - previous[1]) * 100.0
        else:
            raise ValueError("unsupported FRED change unit")
        return FredSeriesVintageChange(
            series_id=series_id,
            vintage_date=vintage_date,
            latest_observation_date=latest[0],
            previous_observation_date=previous[0],
            latest_value=latest[1],
            previous_value=previous[1],
            realtime_start=latest[2],
            realtime_end=latest[3],
            change_value=change,
            change_unit=unit,
        ).validated()

    def capture_regime(self, *, vintage_date: date | None = None) -> FredBtcMacroRegimeCapture:
        request_started_at = _utc(self._clock())
        target_vintage = vintage_date or request_started_at.date()
        units = {
            "BROAD_USD": "PCT",
            "REAL_YIELD_10Y": "BPS",
            "NASDAQ_COMPOSITE": "PCT",
            "VIX": "PCT",
        }
        rows: dict[str, FredSeriesVintageChange] = {}
        for key, series_id in SERIES.items():
            payload = self._get_json(series_id=series_id, vintage_date=target_vintage)
            rows[key] = self._two_latest(payload, series_id=series_id, vintage_date=target_vintage, unit=units[key])
        first_seen = _utc(self._clock())
        historical = target_vintage < first_seen.date()
        if target_vintage > first_seen.date():
            raise ValueError("FRED vintage_date cannot be in the future relative to AlphaPilot first_seen_at")
        return FredBtcMacroRegimeCapture(
            vintage_date=target_vintage,
            first_seen_at=first_seen,
            broad_usd=rows["BROAD_USD"],
            real_yield_10y=rows["REAL_YIELD_10Y"],
            nasdaq_composite=rows["NASDAQ_COMPOSITE"],
            vix=rows["VIX"],
            historical_vintage_reconstruction=historical,
            exact_intraday_availability_proven=not historical,
        ).validated()


def architecture_contract() -> dict:
    return {
        "version": "FRED_BTC_MACRO_REGIME_PROVIDER_V1",
        "provider": "FRED_ALFRED",
        "enabled_by_default": False,
        "api_key_required_when_enabled": True,
        "documented_endpoint": FRED_SERIES_OBSERVATIONS_URL,
        "real_time_period_used": True,
        "series": dict(SERIES),
        "daily_regime_not_intraday_market_feed": True,
        "historical_vintage_reconstruction_supported": True,
        "historical_same_day_intraday_visibility_proven": False,
        "release_date_equals_exact_fred_availability_time": False,
        "current_live_fetch_first_seen_proves_alphapilot_visibility": True,
        "btc_direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "research_only": True,
    }
