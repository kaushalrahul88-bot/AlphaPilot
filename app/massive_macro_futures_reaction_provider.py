"""Replay-only CME futures reaction provider for exact U.S. macro events.

Massive supplies point-in-time futures contract reference data and timestamped
historical aggregate bars. V1 uses two CME products whose product codes are also
verified by CME Group documentation:

- NQ: E-mini Nasdaq-100 futures, an equity-risk reaction dimension.
- 6E: Euro FX futures. Its return is inverted into an explicitly labelled
  USD-strength *proxy*; it is never called DXY or a broad-USD index.

For each event, the representative contract is selected only from information
available before the release: among contracts active on the event date, choose
the single contract with the greatest aggregate volume in the configured
pre-release selection window. Post-release volume cannot influence selection.

This module is historical/replay research only. It does not claim that a user's
Massive subscription delivered the bars in real time; delayed-vs-realtime plan
availability must be handled separately before prospective live confirmation is
enabled. No Options/Futures trade is generated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Callable, Literal

import httpx

BASE_URL = "https://api.massive.com"
NQ_PRODUCT_CODE = "NQ"
EURO_FX_PRODUCT_CODE = "6E"
RESOLUTION = "1min"

MacroEventType = Literal["CPI", "EMPLOYMENT_SITUATION"]


def _utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(value: object, *, name: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _positive(value: object, *, name: str) -> float:
    number = _finite(value, name=name)
    if number <= 0:
        raise ValueError(f"{name} must be > 0")
    return number


def _ns(value: datetime) -> int:
    return int(_utc(value, name="timestamp").timestamp() * 1_000_000_000)


@dataclass(frozen=True)
class MassiveFuturesBar:
    ticker: str
    start_at: datetime
    close_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    transactions: int

    def validated(self) -> "MassiveFuturesBar":
        start = _utc(self.start_at, name="start_at")
        close_at = _utc(self.close_at, name="close_at")
        if close_at - start != timedelta(minutes=1):
            raise ValueError("Massive macro reaction requires exact 1-minute bars")
        if not str(self.ticker or "").strip():
            raise ValueError("futures bar ticker is required")
        values = {
            "open": _positive(self.open, name="open"),
            "high": _positive(self.high, name="high"),
            "low": _positive(self.low, name="low"),
            "close": _positive(self.close, name="close"),
        }
        if values["high"] < max(values.values()) or values["low"] > min(values.values()):
            raise ValueError("invalid Massive futures OHLC geometry")
        if _finite(self.volume, name="volume") < 0:
            raise ValueError("volume must be >= 0")
        if int(self.transactions) < 0:
            raise ValueError("transactions must be >= 0")
        return self


@dataclass(frozen=True)
class SelectedFuturesContract:
    product_code: str
    ticker: str
    trading_venue: str
    event_date: str
    days_to_maturity: int
    pre_release_volume: float
    selection_window_start: datetime
    selection_window_end: datetime
    reference_close: float

    def validated(self) -> "SelectedFuturesContract":
        if self.product_code not in {NQ_PRODUCT_CODE, EURO_FX_PRODUCT_CODE}:
            raise ValueError("unsupported macro reaction futures product")
        if not str(self.ticker or "").strip() or not str(self.trading_venue or "").strip():
            raise ValueError("selected futures contract requires ticker and trading venue")
        if int(self.days_to_maturity) <= 0:
            raise ValueError("selected futures contract must have positive days_to_maturity")
        if _finite(self.pre_release_volume, name="pre_release_volume") <= 0:
            raise ValueError("selected futures contract requires positive pre-release volume")
        start = _utc(self.selection_window_start, name="selection_window_start")
        end = _utc(self.selection_window_end, name="selection_window_end")
        if end <= start:
            raise ValueError("invalid contract selection window")
        _positive(self.reference_close, name="reference_close")
        return self


@dataclass(frozen=True)
class MassiveMacroFuturesReaction:
    event_key: str
    event_type: MacroEventType
    release_at: datetime
    observed_at: datetime
    reconstructible_available_at: datetime
    retrieved_at: datetime
    nasdaq_futures_return_pct: float
    eurusd_futures_return_pct: float
    usd_strength_proxy_return_pct: float
    nasdaq_contract: SelectedFuturesContract
    euro_fx_contract: SelectedFuturesContract
    provider: str = "MASSIVE_CME_FUTURES"
    reconstructible_history: bool = True
    prospective_live_availability_proven: bool = False

    def validated(self) -> "MassiveMacroFuturesReaction":
        release = _utc(self.release_at, name="release_at")
        observed = _utc(self.observed_at, name="observed_at")
        available = _utc(self.reconstructible_available_at, name="reconstructible_available_at")
        retrieved = _utc(self.retrieved_at, name="retrieved_at")
        if self.event_type not in {"CPI", "EMPLOYMENT_SITUATION"}:
            raise ValueError("unsupported macro event_type")
        if not str(self.event_key or "").strip():
            raise ValueError("event_key is required")
        if observed <= release:
            raise ValueError("reaction observed_at must be after release_at")
        if available < observed:
            raise ValueError("reconstructible availability cannot precede completed reaction window")
        if retrieved < available:
            raise ValueError("retrieved_at cannot precede reconstructible availability")
        for name in (
            "nasdaq_futures_return_pct",
            "eurusd_futures_return_pct",
            "usd_strength_proxy_return_pct",
        ):
            _finite(getattr(self, name), name=name)
        if abs(float(self.usd_strength_proxy_return_pct) + float(self.eurusd_futures_return_pct)) > 1e-9:
            raise ValueError("USD-strength proxy must be exact inverse of Euro FX return")
        if self.nasdaq_contract.validated().product_code != NQ_PRODUCT_CODE:
            raise ValueError("nasdaq_contract must be NQ")
        if self.euro_fx_contract.validated().product_code != EURO_FX_PRODUCT_CODE:
            raise ValueError("euro_fx_contract must be 6E")
        if self.reconstructible_history is not True or self.prospective_live_availability_proven is not False:
            raise ValueError("V1 Massive macro reaction is replay-only reconstructible history")
        return self


@dataclass(frozen=True)
class MassiveMacroFuturesReactionPolicy:
    enabled: bool = False
    api_key: str = ""
    timeout_seconds: float = 10.0
    selection_window_minutes: int = 30
    reaction_window_minutes: int = 10
    max_active_contracts: int = 12

    def validated(self) -> "MassiveMacroFuturesReactionPolicy":
        if not isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        if not 5 <= int(self.selection_window_minutes) <= 120:
            raise ValueError("selection_window_minutes must be between 5 and 120")
        if not 1 <= int(self.reaction_window_minutes) <= 30:
            raise ValueError("reaction_window_minutes must be between 1 and 30")
        if not 1 <= int(self.max_active_contracts) <= 50:
            raise ValueError("max_active_contracts must be between 1 and 50")
        if self.enabled and not str(self.api_key or "").strip():
            raise ValueError("Massive macro futures reaction requires api_key")
        return self


class MassiveMacroFuturesReactionProvider:
    def __init__(
        self,
        policy: MassiveMacroFuturesReactionPolicy | None = None,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.policy = (policy or MassiveMacroFuturesReactionPolicy()).validated()
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("Massive macro futures reaction provider is disabled by policy")

    def _get_json(self, path: str, *, params: dict) -> dict:
        self._require_enabled()
        url = f"{BASE_URL}{path}"
        request_params = {**params, "apiKey": str(self.policy.api_key).strip()}
        if self._client is not None:
            response = self._client.get(url, params=request_params, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(url, params=request_params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "OK":
            raise ValueError("invalid Massive futures API response")
        return payload

    def _active_contracts(self, product_code: str, event_date: str) -> list[dict]:
        payload = self._get_json(
            "/futures/v1/contracts",
            params={
                "product_code": product_code,
                "date": event_date,
                "active": "true",
                "type": "single",
                "limit": int(self.policy.max_active_contracts),
                "sort": "days_to_maturity.asc,ticker.asc",
            },
        )
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise ValueError("Massive contracts response lacks results")
        accepted: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Massive contract row must be object")
            if str(row.get("product_code") or "") != product_code:
                continue
            if row.get("active") is not True:
                continue
            if row.get("type") not in {None, "", "single"}:
                continue
            days = int(row.get("days_to_maturity") or 0)
            if days <= 0:
                continue
            if not str(row.get("ticker") or "").strip() or not str(row.get("trading_venue") or "").strip():
                continue
            accepted.append(row)
        if not accepted:
            raise ValueError(f"no active Massive futures contracts for product {product_code}")
        return accepted

    def _bars(self, ticker: str, *, start_at: datetime, end_at: datetime) -> list[MassiveFuturesBar]:
        start = _utc(start_at, name="start_at")
        end = _utc(end_at, name="end_at")
        if end <= start:
            raise ValueError("futures aggregate end_at must be after start_at")
        payload = self._get_json(
            f"/futures/v1/aggs/{ticker}",
            params={
                "resolution": RESOLUTION,
                "window_start.gte": _ns(start),
                "window_start.lt": _ns(end),
                "limit": 5000,
                "sort": "window_start.asc",
            },
        )
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise ValueError("Massive futures aggregates response lacks results")
        bars: list[MassiveFuturesBar] = []
        seen_starts: set[datetime] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError("Massive aggregate row must be object")
            if str(raw.get("ticker") or ticker) != ticker:
                raise ValueError("Massive aggregate ticker mismatch")
            start_ns = int(raw["window_start"])
            bar_start = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
            if bar_start < start or bar_start >= end:
                raise ValueError("Massive aggregate lies outside requested window")
            if bar_start in seen_starts:
                raise ValueError("duplicate Massive futures aggregate window")
            seen_starts.add(bar_start)
            bars.append(MassiveFuturesBar(
                ticker=ticker,
                start_at=bar_start,
                close_at=bar_start + timedelta(minutes=1),
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
                volume=float(raw.get("volume", 0.0)),
                transactions=int(raw.get("transactions", 0)),
            ).validated())
        return sorted(bars, key=lambda row: row.start_at)

    def _select_contract(self, product_code: str, *, release_at: datetime) -> SelectedFuturesContract:
        release = _utc(release_at, name="release_at")
        selection_start = release - timedelta(minutes=int(self.policy.selection_window_minutes))
        candidates: list[tuple[float, dict, list[MassiveFuturesBar]]] = []
        for contract in self._active_contracts(product_code, release.date().isoformat()):
            ticker = str(contract["ticker"])
            bars = self._bars(ticker, start_at=selection_start, end_at=release)
            eligible = [bar for bar in bars if bar.close_at <= release]
            volume = sum(float(bar.volume) for bar in eligible)
            if volume <= 0 or not eligible:
                continue
            if eligible[-1].close_at != release:
                # Exact pre-event close is required; a stale contract cannot
                # represent the event even if it had volume earlier.
                continue
            candidates.append((volume, contract, eligible))
        if not candidates:
            raise ValueError(f"no liquid {product_code} contract has an exact pre-release 1-minute close")
        candidates.sort(key=lambda item: (-item[0], str(item[1]["ticker"])))
        if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) <= 1e-9:
            raise ValueError(f"ambiguous {product_code} contract selection: equal pre-release volume")
        volume, contract, bars = candidates[0]
        return SelectedFuturesContract(
            product_code=product_code,
            ticker=str(contract["ticker"]),
            trading_venue=str(contract["trading_venue"]),
            event_date=release.date().isoformat(),
            days_to_maturity=int(contract["days_to_maturity"]),
            pre_release_volume=volume,
            selection_window_start=selection_start,
            selection_window_end=release,
            reference_close=float(bars[-1].close),
        ).validated()

    def _post_event_return(
        self,
        selected: SelectedFuturesContract,
        *,
        release_at: datetime,
        observed_at: datetime,
    ) -> float:
        release = _utc(release_at, name="release_at")
        observed = _utc(observed_at, name="observed_at")
        bars = self._bars(selected.ticker, start_at=release, end_at=observed)
        eligible = [bar for bar in bars if bar.close_at <= observed]
        if not eligible or eligible[-1].close_at != observed:
            raise ValueError(f"{selected.product_code} lacks exact completed reaction-window close")
        post_close = float(eligible[-1].close)
        return (post_close / float(selected.reference_close) - 1.0) * 100.0

    def fetch_reaction(
        self,
        *,
        event_key: str,
        event_type: MacroEventType,
        release_at: datetime,
    ) -> MassiveMacroFuturesReaction:
        self._require_enabled()
        release = _utc(release_at, name="release_at")
        if event_type not in {"CPI", "EMPLOYMENT_SITUATION"}:
            raise ValueError("Massive macro futures V1 supports CPI and EMPLOYMENT_SITUATION only")
        if not str(event_key or "").strip():
            raise ValueError("event_key is required")
        observed = release + timedelta(minutes=int(self.policy.reaction_window_minutes))
        retrieved = _utc(self._clock(), name="clock retrieved_at")
        if retrieved < observed:
            raise ValueError("reaction window is not complete yet")

        nasdaq = self._select_contract(NQ_PRODUCT_CODE, release_at=release)
        euro = self._select_contract(EURO_FX_PRODUCT_CODE, release_at=release)
        nasdaq_return = self._post_event_return(nasdaq, release_at=release, observed_at=observed)
        euro_return = self._post_event_return(euro, release_at=release, observed_at=observed)

        return MassiveMacroFuturesReaction(
            event_key=event_key,
            event_type=event_type,
            release_at=release,
            observed_at=observed,
            reconstructible_available_at=observed,
            retrieved_at=retrieved,
            nasdaq_futures_return_pct=nasdaq_return,
            eurusd_futures_return_pct=euro_return,
            usd_strength_proxy_return_pct=-euro_return,
            nasdaq_contract=nasdaq,
            euro_fx_contract=euro,
        ).validated()


def architecture_contract() -> dict:
    return {
        "version": "MASSIVE_MACRO_FUTURES_REACTION_PROVIDER_V1",
        "enabled_by_default": False,
        "provider": "MASSIVE",
        "exchange_data_scope": "CME_FUTURES",
        "nq_product_code": NQ_PRODUCT_CODE,
        "euro_fx_product_code": EURO_FX_PRODUCT_CODE,
        "contract_reference_is_point_in_time": True,
        "contract_selection_uses_pre_release_data_only": True,
        "post_release_volume_may_select_contract": False,
        "exact_pre_release_minute_close_required": True,
        "exact_reaction_window_close_required": True,
        "nasdaq_dimension_uses_nq_futures": True,
        "usd_dimension_uses_inverse_eurusd_proxy": True,
        "proxy_claimed_to_be_dxy": False,
        "historical_replay_reconstruction_supported": True,
        "prospective_live_availability_proven": False,
        "live_confirmation_auto_enabled": False,
        "continuous_contract_assumed": False,
        "futures_trade_generated": False,
        "options_trade_generated": False,
        "broker_execution_enabled": False,
        "research_only": True,
    }
