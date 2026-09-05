"""Official BLS exact-release provider for CPI and Employment Situation.

The provider accepts only BLS ``/news.release/`` URLs, parses the release's own
embargo timestamp in America/New_York, and records AlphaPilot's actual fetch
first_seen_at. Fetching an old BLS archive today therefore does not make the
release visible to an old historical click.

V1 extracts only the measures required by the validated macro semantics:
- CPI: headline monthly CPI-U and core monthly CPI (all items less food/energy)
- Employment Situation: nonfarm payroll change, unemployment rate, and average
  hourly earnings monthly percent change.

No consensus, surprise direction, trade setup, or execution is produced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from math import isfinite
import re
from typing import Callable, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from app.crypto_macro_event_intelligence import OfficialMacroRelease

BlsEventType = Literal["CPI", "EMPLOYMENT_SITUATION"]
BLS_HOST = "www.bls.gov"
BLS_RELEASE_PREFIX = "/news.release/"
EASTERN = ZoneInfo("America/New_York")

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


def _visible_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(str(html or ""))
    return re.sub(r"\s+", " ", unescape(parser.text())).strip()


def _utc_exact(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _allowed_bls_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    return parsed.scheme == "https" and parsed.hostname == BLS_HOST and parsed.path.startswith(BLS_RELEASE_PREFIX)


def _parse_embargo_release_at(text: str) -> datetime:
    pattern = re.compile(
        r"embargoed\s+until\s+(\d{1,2}):(\d{2})\s*(a\.m\.|p\.m\.)\s*\(ET\)\s*"
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2}),\s*(\d{4})",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError("BLS release text lacks exact embargo timestamp")
    hour = int(match.group(1))
    minute = int(match.group(2))
    marker = match.group(3).lower()
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        raise ValueError("invalid BLS release clock time")
    if marker.startswith("p") and hour != 12:
        hour += 12
    if marker.startswith("a") and hour == 12:
        hour = 0
    month = _MONTHS[match.group(4).lower()]
    day = int(match.group(5))
    year = int(match.group(6))
    return datetime(year, month, day, hour, minute, tzinfo=EASTERN).astimezone(timezone.utc)


def _reference_period(text: str, event_type: BlsEventType) -> tuple[str, str]:
    if event_type == "CPI":
        pattern = re.compile(r"CONSUMER\s+PRICE\s+INDEX\s*-\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})", re.IGNORECASE)
    else:
        pattern = re.compile(r"THE\s+EMPLOYMENT\s+SITUATION\s*-\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})", re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        raise ValueError(f"BLS {event_type} release lacks reference-period heading")
    month_name = match.group(1)
    year = int(match.group(2))
    month = _MONTHS[month_name.lower()]
    return f"{year:04d}-{month:02d}", month_name


def _signed_phrase_change(text: str, pattern: re.Pattern) -> float:
    match = pattern.search(text)
    if not match:
        raise ValueError("required BLS percentage change not found")
    verb = match.group("verb").lower()
    raw = match.groupdict().get("value")
    if "unchanged" in verb:
        return 0.0
    if raw is None:
        raise ValueError("BLS change verb requires numeric value")
    value = float(raw)
    if not isfinite(value):
        raise ValueError("BLS parsed change must be finite")
    negative = any(word in verb for word in ("fell", "declined", "decreased", "dropped"))
    return -abs(value) if negative else abs(value)


def _parse_cpi(text: str, month_name: str) -> tuple[dict[str, float], dict[str, str]]:
    headline = _signed_phrase_change(
        text,
        re.compile(
            r"Consumer\s+Price\s+Index\s+for\s+All\s+Urban\s+Consumers\s*\(CPI-U\)\s+"
            r"(?P<verb>increased|rose|fell|declined|decreased|was\s+unchanged)"
            r"(?:\s+(?:by\s+)?(?P<value>\d+(?:\.\d+)?)\s+percent)?"
            r".*?\s+in\s+" + re.escape(month_name),
            re.IGNORECASE,
        ),
    )
    core = _signed_phrase_change(
        text,
        re.compile(
            r"index\s+for\s+all\s+items\s+less\s+food\s+and\s+energy\s+"
            r"(?P<verb>increased|rose|fell|declined|decreased|was\s+unchanged)"
            r"(?:\s+(?:by\s+)?(?P<value>\d+(?:\.\d+)?)\s+percent)?",
            re.IGNORECASE,
        ),
    )
    return (
        {"headline_mom_pct": headline, "core_mom_pct": core},
        {"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"},
    )


def _payroll_change(text: str) -> float:
    direct = re.search(
        r"(?:Total\s+)?nonfarm\s+payroll\s+employment\s+"
        r"(?P<verb>increased|rose|fell|declined|decreased)\s+(?:by\s+)?(?P<value>\d[\d,]*)",
        text,
        re.IGNORECASE,
    )
    if direct:
        value = float(direct.group("value").replace(",", "")) / 1000.0
        if direct.group("verb").lower() in {"fell", "declined", "decreased"}:
            value = -abs(value)
        return value
    parenthetical = re.search(r"nonfarm\s+payroll\s+employment\s*\(\s*(?P<value>[+-]?[\d,]+)\s*\)", text, re.IGNORECASE)
    if parenthetical:
        return float(parenthetical.group("value").replace(",", "")) / 1000.0
    raise ValueError("BLS Employment release lacks current nonfarm payroll change")


def _unemployment_rate(text: str) -> float:
    match = re.search(
        r"unemployment\s+rate\s+(?:was\s+unchanged\s+at|was|edged\s+up\s+to|increased\s+to|rose\s+to|declined\s+to|fell\s+to)\s+(\d+(?:\.\d+)?)\s+percent",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("BLS Employment release lacks current unemployment rate")
    return float(match.group(1))


def _earnings_change(text: str) -> float:
    anchor = re.search(r"average\s+hourly\s+earnings\s+for\s+all\s+employees\s+on\s+private\s+nonfarm\s+payrolls", text, re.IGNORECASE)
    if not anchor:
        raise ValueError("BLS Employment release lacks average hourly earnings section")
    segment = text[anchor.start(): anchor.start() + 350]
    match = re.search(
        r"(?P<verb>increased|rose|fell|declined|decreased).*?\bor\s+(?P<value>\d+(?:\.\d+)?)\s+percent",
        segment,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("BLS Employment release lacks monthly average hourly earnings percent change")
    value = float(match.group("value"))
    if match.group("verb").lower() in {"fell", "declined", "decreased"}:
        value = -abs(value)
    return value


def _parse_employment(text: str) -> tuple[dict[str, float], dict[str, str]]:
    values = {
        "payroll_change_k": _payroll_change(text),
        "unemployment_rate_pct": _unemployment_rate(text),
        "avg_hourly_earnings_mom_pct": _earnings_change(text),
    }
    return values, {
        "payroll_change_k": "THOUSAND_PERSONS",
        "unemployment_rate_pct": "PERCENT",
        "avg_hourly_earnings_mom_pct": "PERCENT",
    }


@dataclass(frozen=True)
class BlsExactMacroReleasePolicy:
    enabled: bool = False
    timeout_seconds: float = 10.0

    def validated(self) -> "BlsExactMacroReleasePolicy":
        if not isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        return self


class BlsExactMacroReleaseProvider:
    def __init__(
        self,
        policy: BlsExactMacroReleasePolicy | None = None,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.policy = (policy or BlsExactMacroReleasePolicy()).validated()
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("BLS exact macro release collection is disabled by policy")

    def parse_release(
        self,
        html: str,
        *,
        url: str,
        event_type: BlsEventType,
        first_seen_at: datetime,
    ) -> OfficialMacroRelease:
        if not _allowed_bls_url(url):
            raise ValueError("BLS exact macro provider accepts only official https://www.bls.gov/news.release/ URLs")
        if event_type not in {"CPI", "EMPLOYMENT_SITUATION"}:
            raise ValueError("BLS V1 supports CPI and EMPLOYMENT_SITUATION only")
        text = _visible_text(html)
        release_at = _parse_embargo_release_at(text)
        reference_period, month_name = _reference_period(text, event_type)
        if event_type == "CPI":
            values, units = _parse_cpi(text, month_name)
            event_key = f"BLS:CPI:{reference_period}"
        else:
            values, units = _parse_employment(text)
            event_key = f"BLS:EMPLOYMENT_SITUATION:{reference_period}"
        return OfficialMacroRelease(
            event_key=event_key,
            event_type=event_type,
            reference_period=reference_period,
            release_at=release_at,
            first_seen_at=_utc_exact(first_seen_at, name="first_seen_at"),
            official_source="BLS",
            official_source_ref=url,
            values=values,
            units=units,
            release_stage="FIRST_RELEASE",
            revision_number=0,
        ).validated()

    def fetch_release(self, *, url: str, event_type: BlsEventType) -> OfficialMacroRelease:
        self._require_enabled()
        if not _allowed_bls_url(url):
            raise ValueError("BLS exact macro provider accepts only official https://www.bls.gov/news.release/ URLs")
        if self._client is not None:
            response = self._client.get(url, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(url)
        response.raise_for_status()
        first_seen = _utc_exact(self._clock(), name="clock first_seen_at")
        return self.parse_release(response.text, url=url, event_type=event_type, first_seen_at=first_seen)


def architecture_contract() -> dict:
    return {
        "version": "BLS_EXACT_MACRO_RELEASE_PROVIDER_V1",
        "enabled_by_default": False,
        "allowed_host": BLS_HOST,
        "allowed_path_prefix": BLS_RELEASE_PREFIX,
        "official_release_timestamp_from_bls_page": True,
        "eastern_timezone_dst_aware": True,
        "historical_page_fetch_may_be_backdated": False,
        "cpi_metrics": ["headline_mom_pct", "core_mom_pct"],
        "employment_metrics": ["payroll_change_k", "unemployment_rate_pct", "avg_hourly_earnings_mom_pct"],
        "consensus_provided": False,
        "surprise_direction_generated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "network_request_at_import": False,
        "research_only": True,
    }
