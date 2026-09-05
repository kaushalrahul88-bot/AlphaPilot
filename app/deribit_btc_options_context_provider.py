"""Optional Deribit BTC options context provider for Crypto Brain research.

Deribit is used only as a global options-market context source. It is NOT a
CoinDCX execution substitute: its contracts, quotes and marks cannot select or
fill a CoinDCX option. The provider consumes documented public endpoints for the
active option instrument list and periodic chain summaries.

The full-chain summary exposes mark IV and open interest, while instrument
metadata exposes expiry, strike and call/put type. Instrument metadata is seeded
lazily and cached; ordinary context polling does not repeatedly call
``get_instruments``. V1 deliberately does not approximate 25-delta skew from
strike; skew is supplied separately by the documented Deribit ticker-Greeks PIT
pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import median
from typing import Any, Callable

import httpx

BASE_URL = "https://www.deribit.com/api/v2"
INSTRUMENTS_URL = f"{BASE_URL}/public/get_instruments"
BOOK_SUMMARY_URL = f"{BASE_URL}/public/get_book_summary_by_currency"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _finite(name: str, value: Any, *, nonnegative: bool = False, positive: bool = False) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be >= 0")
    if positive and number <= 0:
        raise ValueError(f"{name} must be > 0")
    return number


def _from_ms(value: Any) -> datetime:
    milliseconds = _finite("expiration_timestamp", value, positive=True)
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)


@dataclass(frozen=True)
class DeribitBtcOptionsContextPolicy:
    enabled: bool = False
    timeout_seconds: float = 10.0
    currency: str = "BTC"
    min_seconds_to_expiry: int = 3600
    max_expiries_for_term_structure: int = 12

    def validated(self) -> "DeribitBtcOptionsContextPolicy":
        if str(self.currency).upper() != "BTC":
            raise ValueError("BTC Options context provider supports BTC only")
        if not isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        if int(self.min_seconds_to_expiry) < 0:
            raise ValueError("min_seconds_to_expiry must be >= 0")
        if int(self.max_expiries_for_term_structure) < 2:
            raise ValueError("max_expiries_for_term_structure must be >= 2")
        return self


@dataclass(frozen=True)
class DeribitBtcOptionsContextCapture:
    first_seen_at: datetime
    underlying_price_usd: float
    nearest_expiry_at: datetime
    next_expiry_at: datetime | None
    atm_mark_iv_pct: float
    next_expiry_atm_mark_iv_pct: float | None
    term_structure_slope_iv_points: float | None
    total_call_open_interest_btc: float
    total_put_open_interest_btc: float
    put_call_open_interest_ratio: float | None
    matched_contract_count: int
    active_contract_count: int
    valid_expiry_count: int
    provider: str = "DERIBIT_PUBLIC_API"
    currency: str = "BTC"
    skew_25d: float | None = None

    def validated(self) -> "DeribitBtcOptionsContextCapture":
        _finite("underlying_price_usd", self.underlying_price_usd, positive=True)
        _finite("atm_mark_iv_pct", self.atm_mark_iv_pct, positive=True)
        if self.next_expiry_atm_mark_iv_pct is not None:
            _finite("next_expiry_atm_mark_iv_pct", self.next_expiry_atm_mark_iv_pct, positive=True)
        if self.term_structure_slope_iv_points is not None:
            _finite("term_structure_slope_iv_points", self.term_structure_slope_iv_points)
        call_oi = _finite("total_call_open_interest_btc", self.total_call_open_interest_btc, nonnegative=True)
        put_oi = _finite("total_put_open_interest_btc", self.total_put_open_interest_btc, nonnegative=True)
        if self.put_call_open_interest_ratio is not None:
            ratio = _finite("put_call_open_interest_ratio", self.put_call_open_interest_ratio, nonnegative=True)
            if call_oi <= 0:
                raise ValueError("put/call OI ratio cannot exist with zero call OI")
            expected = put_oi / call_oi
            if abs(ratio - expected) > max(1e-12, abs(expected) * 1e-9):
                raise ValueError("put_call_open_interest_ratio is inconsistent with captured OI")
        if _utc(self.nearest_expiry_at) <= _utc(self.first_seen_at):
            raise ValueError("nearest_expiry_at must be after first_seen_at")
        if self.next_expiry_at is not None and _utc(self.next_expiry_at) <= _utc(self.nearest_expiry_at):
            raise ValueError("next_expiry_at must be after nearest_expiry_at")
        if int(self.matched_contract_count) <= 0 or int(self.active_contract_count) <= 0:
            raise ValueError("options context requires non-empty active/matched chain")
        if int(self.matched_contract_count) > int(self.active_contract_count):
            raise ValueError("matched_contract_count cannot exceed active_contract_count")
        if int(self.valid_expiry_count) <= 0:
            raise ValueError("at least one valid paired ATM expiry is required")
        if self.skew_25d is not None:
            raise ValueError("periodic chain context does not infer 25-delta skew")
        return self


class DeribitBtcOptionsContextProvider:
    def __init__(
        self,
        policy: DeribitBtcOptionsContextPolicy | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.policy = (policy or DeribitBtcOptionsContextPolicy()).validated()
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._instrument_rows_cache: list[dict] | None = None

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("Deribit BTC options context collection is disabled by policy")

    def _get_result(self, url: str, *, params: dict) -> list[dict]:
        self._require_enabled()
        if self._client is not None:
            response = self._client.get(url, params=params, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
            raise ValueError("invalid Deribit JSON-RPC result payload")
        return [row for row in payload["result"] if isinstance(row, dict)]

    def refresh_instruments(self) -> int:
        """Explicitly refresh cached instrument metadata.

        Normal snapshot polling never calls this after the initial lazy seed.
        A long-running WebSocket lifecycle tracker can replace this explicit
        refresh in a later version without changing snapshot/evidence semantics.
        """
        rows = self._get_result(
            INSTRUMENTS_URL,
            params={"currency": "BTC", "kind": "option", "expired": "false"},
        )
        if not rows:
            raise ValueError("Deribit returned an empty BTC option instrument list")
        self._instrument_rows_cache = rows
        return len(rows)

    def instrument_rows(self, *, refresh: bool = False) -> list[dict]:
        """Return a defensive copy of the authoritative cached instrument seed.

        Calling this method may perform exactly one lazy ``get_instruments``
        request when the cache is empty. Subsequent calls are cache-only unless
        the caller explicitly requests ``refresh=True``. It performs no trade or
        ticker subscription.
        """
        self._require_enabled()
        if refresh:
            self.refresh_instruments()
        return list(self._seed_instruments_if_needed())

    def _seed_instruments_if_needed(self) -> list[dict]:
        if self._instrument_rows_cache is None:
            self.refresh_instruments()
        return list(self._instrument_rows_cache or [])

    def _fetch_chain(self) -> tuple[list[dict], list[dict]]:
        instruments = self._seed_instruments_if_needed()
        summaries = self._get_result(
            BOOK_SUMMARY_URL,
            params={"currency": "BTC", "kind": "option"},
        )
        return instruments, summaries

    @staticmethod
    def _normalize_instruments(rows: list[dict], *, first_seen_at: datetime, policy: DeribitBtcOptionsContextPolicy) -> dict[str, dict]:
        minimum_expiry = _utc(first_seen_at) + timedelta(seconds=int(policy.min_seconds_to_expiry))
        normalized: dict[str, dict] = {}
        for raw in rows:
            name = str(raw.get("instrument_name") or "").strip()
            option_type = str(raw.get("option_type") or "").lower()
            if not name or raw.get("kind") != "option" or option_type not in {"call", "put"}:
                continue
            if raw.get("is_active") is False or str(raw.get("state") or "open").lower() not in {"open", "locked"}:
                continue
            try:
                expiry = _from_ms(raw["expiration_timestamp"])
                strike = _finite("strike", raw["strike"], positive=True)
            except (KeyError, TypeError, ValueError):
                continue
            if expiry <= minimum_expiry:
                continue
            normalized[name] = {
                "instrument_name": name,
                "expiry": expiry,
                "strike": strike,
                "option_type": option_type,
            }
        if not normalized:
            raise ValueError("Deribit returned no active BTC option instruments inside policy horizon")
        return normalized

    @staticmethod
    def _normalize_summaries(rows: list[dict]) -> dict[str, dict]:
        normalized: dict[str, dict] = {}
        for raw in rows:
            name = str(raw.get("instrument_name") or "").strip()
            if not name:
                continue
            try:
                mark_iv = _finite("mark_iv", raw["mark_iv"], positive=True)
                open_interest = _finite("open_interest", raw["open_interest"], nonnegative=True)
                underlying = _finite("underlying_price", raw["underlying_price"], positive=True)
            except (KeyError, TypeError, ValueError):
                continue
            normalized[name] = {
                "instrument_name": name,
                "mark_iv": mark_iv,
                "open_interest": open_interest,
                "underlying_price": underlying,
            }
        if not normalized:
            raise ValueError("Deribit option summary contains no valid mark-IV/OI rows")
        return normalized

    @staticmethod
    def _paired_atm_by_expiry(matched: list[dict], *, max_expiries: int) -> list[dict]:
        by_expiry: dict[datetime, list[dict]] = {}
        for row in matched:
            by_expiry.setdefault(row["expiry"], []).append(row)

        results: list[dict] = []
        for expiry in sorted(by_expiry)[: int(max_expiries)]:
            rows = by_expiry[expiry]
            underlying = median([float(row["underlying_price"]) for row in rows])
            by_strike: dict[float, dict[str, dict]] = {}
            for row in rows:
                by_strike.setdefault(float(row["strike"]), {})[row["option_type"]] = row
            paired = [(strike, sides) for strike, sides in by_strike.items() if {"call", "put"}.issubset(sides)]
            if not paired:
                continue
            strike, sides = min(paired, key=lambda item: abs(item[0] - underlying))
            call_iv = float(sides["call"]["mark_iv"])
            put_iv = float(sides["put"]["mark_iv"])
            results.append({
                "expiry": expiry,
                "underlying_price": underlying,
                "atm_strike": strike,
                "atm_mark_iv": (call_iv + put_iv) / 2.0,
            })
        return results

    def capture_context(self) -> DeribitBtcOptionsContextCapture:
        instruments_raw, summaries_raw = self._fetch_chain()
        first_seen = _utc(self._clock())
        instruments = self._normalize_instruments(instruments_raw, first_seen_at=first_seen, policy=self.policy)
        summaries = self._normalize_summaries(summaries_raw)

        matched: list[dict] = []
        call_oi = 0.0
        put_oi = 0.0
        for name, instrument in instruments.items():
            summary = summaries.get(name)
            if summary is None:
                continue
            row = {**instrument, **summary}
            matched.append(row)
            if row["option_type"] == "call":
                call_oi += float(row["open_interest"])
            else:
                put_oi += float(row["open_interest"])
        if not matched:
            raise ValueError("Deribit instrument and summary responses have no matched BTC options")

        term = self._paired_atm_by_expiry(matched, max_expiries=self.policy.max_expiries_for_term_structure)
        if not term:
            raise ValueError("Deribit chain has no expiry with paired call/put ATM IV")
        nearest = term[0]
        second = term[1] if len(term) >= 2 else None
        ratio = None if call_oi <= 0 else put_oi / call_oi
        slope = None if second is None else float(second["atm_mark_iv"]) - float(nearest["atm_mark_iv"])

        return DeribitBtcOptionsContextCapture(
            first_seen_at=first_seen,
            underlying_price_usd=float(nearest["underlying_price"]),
            nearest_expiry_at=nearest["expiry"],
            next_expiry_at=None if second is None else second["expiry"],
            atm_mark_iv_pct=float(nearest["atm_mark_iv"]),
            next_expiry_atm_mark_iv_pct=None if second is None else float(second["atm_mark_iv"]),
            term_structure_slope_iv_points=slope,
            total_call_open_interest_btc=call_oi,
            total_put_open_interest_btc=put_oi,
            put_call_open_interest_ratio=ratio,
            matched_contract_count=len(matched),
            active_contract_count=len(instruments),
            valid_expiry_count=len(term),
        ).validated()


def architecture_contract() -> dict:
    return {
        "version": "DERIBIT_BTC_OPTIONS_CONTEXT_PROVIDER_V2",
        "provider": "DERIBIT_PUBLIC_API",
        "collection_enabled_by_default": False,
        "authentication_required": False,
        "documented_instruments_endpoint": INSTRUMENTS_URL,
        "documented_book_summary_endpoint": BOOK_SUMMARY_URL,
        "instrument_kind": "option",
        "currency": "BTC",
        "instrument_list_seeded_lazily": True,
        "instrument_seed_cached": True,
        "instrument_rows_public_seed_method": True,
        "instrument_refresh_explicit": True,
        "instrument_metadata_polled_each_context_cycle": False,
        "mark_iv_captured": True,
        "open_interest_captured": True,
        "expiry_strike_option_type_captured": True,
        "skew_25d_inferred_from_strike": False,
        "skew_25d_captured_by_periodic_context": False,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "underlying_direction_generation_allowed": False,
        "options_trade_generation_allowed": False,
        "futures_trade_generation_allowed": False,
        "broker_execution_enabled": False,
        "research_only": True,
    }
