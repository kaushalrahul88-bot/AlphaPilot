"""Verified point-in-time ingestion boundary for CoinDCX BTC Options data.

CoinDCX exposes an Options product UI, but the public API documentation used by
AlphaPilot does not currently prove a public historical Options market-data API.
This module therefore performs no network discovery or private-endpoint reverse
engineering. It only accepts an externally obtained, explicitly verified
point-in-time capture and converts it into immutable archive records plus the
existing historical replay row types.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Literal

from app.crypto_btc_historical_data_adapter import (
    BtcOptionContractArchiveRow,
    BtcOptionQuoteArchiveRow,
    HistoricalProvenance,
)
from app.crypto_btc_options_contract_selector import BtcOptionContractSnapshot
from app.crypto_btc_pit_archive import BtcPitArchiveRecord, archive_record_from_capture

OPTIONS_CHAIN_DATASET = "COINDCX_BTC_OPTION_CHAIN_GREEKS_IV_OI_QUOTES"
OPTION_EXIT_QUOTES_DATASET = "BTC_OPTION_EXIT_QUOTES"
VerifiedSourceType = Literal[
    "VERIFIED_COINDCX_EXPORT",
    "VERIFIED_COINDCX_UI_CAPTURE",
    "VERIFIED_PROVIDER_ARCHIVE",
]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite(name: str, value: float | None, *, required: bool = False) -> float | None:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True)
class VerifiedBtcOptionPitCapture:
    symbol: str
    option_type: Literal["CALL", "PUT"]
    strike: float
    expiry_at: datetime
    observed_at: datetime
    first_seen_at: datetime
    bid: float
    ask: float
    mark: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    implied_volatility: float | None
    open_interest: float | None
    volume_24h: float | None
    source_type: VerifiedSourceType
    source_artifact_id: str
    source_version: str | None = None
    provider: str = "COINDCX"
    underlying: str = "BTC"

    def validated(self) -> "VerifiedBtcOptionPitCapture":
        if str(self.provider).upper() != "COINDCX":
            raise ValueError("verified BTC Options PIT capture must be CoinDCX")
        if str(self.underlying).upper() != "BTC":
            raise ValueError("verified Options PIT capture must be BTC")
        if not str(self.symbol or "").strip() or not str(self.source_artifact_id or "").strip():
            raise ValueError("symbol and source_artifact_id are required")
        if self.option_type not in {"CALL", "PUT"}:
            raise ValueError("option_type must be CALL or PUT")
        if self.source_type not in {
            "VERIFIED_COINDCX_EXPORT", "VERIFIED_COINDCX_UI_CAPTURE", "VERIFIED_PROVIDER_ARCHIVE"
        }:
            raise ValueError("unsupported verified Options source_type")

        observed = _utc(self.observed_at)
        seen = _utc(self.first_seen_at)
        expiry = _utc(self.expiry_at)
        if observed > seen:
            raise ValueError("observed_at cannot be after first_seen_at")
        if expiry <= observed:
            raise ValueError("option expiry must be after observed_at")

        strike = _finite("strike", self.strike, required=True)
        bid = _finite("bid", self.bid, required=True)
        ask = _finite("ask", self.ask, required=True)
        if strike is None or strike <= 0:
            raise ValueError("strike must be > 0")
        if bid is None or ask is None or bid < 0 or ask <= 0 or ask < bid:
            raise ValueError("invalid option bid/ask")
        mark = _finite("mark", self.mark)
        if mark is not None and mark <= 0:
            raise ValueError("mark must be > 0 when supplied")
        for name in ("delta", "gamma", "theta", "vega", "implied_volatility"):
            _finite(name, getattr(self, name))
        for name in ("open_interest", "volume_24h"):
            value = _finite(name, getattr(self, name))
            if value is not None and value < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.implied_volatility is not None and float(self.implied_volatility) <= 0:
            raise ValueError("implied_volatility must be > 0 when supplied")
        return self

    def snapshot(self) -> BtcOptionContractSnapshot:
        self.validated()
        # Selector visibility is anchored to first_seen_at, not provider event time.
        return BtcOptionContractSnapshot(
            symbol=self.symbol,
            option_type=self.option_type,
            strike=float(self.strike),
            expiry_at=_utc(self.expiry_at),
            observed_at=_utc(self.first_seen_at),
            bid=float(self.bid),
            ask=float(self.ask),
            mark=None if self.mark is None else float(self.mark),
            delta=None if self.delta is None else float(self.delta),
            gamma=None if self.gamma is None else float(self.gamma),
            theta=None if self.theta is None else float(self.theta),
            vega=None if self.vega is None else float(self.vega),
            implied_volatility=None if self.implied_volatility is None else float(self.implied_volatility),
            open_interest=None if self.open_interest is None else float(self.open_interest),
            volume_24h=None if self.volume_24h is None else float(self.volume_24h),
            source=f"COINDCX_OPTIONS_{self.source_type}",
            platform="COINDCX",
            underlying="BTC",
        )

    def _base_payload(self) -> dict:
        self.validated()
        return {
            "symbol": self.symbol,
            "option_type": self.option_type,
            "strike": float(self.strike),
            "expiry_at": _utc(self.expiry_at).isoformat(),
            "provider_observed_at": _utc(self.observed_at).isoformat(),
            "first_seen_at": _utc(self.first_seen_at).isoformat(),
            "bid": float(self.bid),
            "ask": float(self.ask),
            "mark": self.mark,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "implied_volatility": self.implied_volatility,
            "open_interest": self.open_interest,
            "volume_24h": self.volume_24h,
            "source_type": self.source_type,
            "source_artifact_id": self.source_artifact_id,
            "source_version": self.source_version,
            "fabricated": False,
            "point_in_time_proven": True,
        }

    def archive_records(self) -> tuple[BtcPitArchiveRecord, BtcPitArchiveRecord]:
        payload = self._base_payload()
        seen_ms = int(_utc(self.first_seen_at).timestamp() * 1000)
        shared_key = f"{self.source_artifact_id}:{self.symbol}:{seen_ms}"
        chain = archive_record_from_capture(
            dataset=OPTIONS_CHAIN_DATASET,
            provider="COINDCX",
            source_key=shared_key,
            first_seen_at=_utc(self.first_seen_at),
            event_at=_utc(self.observed_at),
            source_version=self.source_version,
            payload=payload,
        )
        quote = archive_record_from_capture(
            dataset=OPTION_EXIT_QUOTES_DATASET,
            provider="COINDCX_OR_VERIFIED_ARCHIVE",
            source_key=shared_key,
            first_seen_at=_utc(self.first_seen_at),
            event_at=_utc(self.observed_at),
            source_version=self.source_version,
            payload={
                "symbol": self.symbol,
                "bid": float(self.bid),
                "ask": float(self.ask),
                "provider_observed_at": _utc(self.observed_at).isoformat(),
                "first_seen_at": _utc(self.first_seen_at).isoformat(),
                "source_type": self.source_type,
                "source_artifact_id": self.source_artifact_id,
                "fabricated": False,
                "point_in_time_proven": True,
            },
        )
        return chain, quote

    def historical_rows(self) -> tuple[BtcOptionContractArchiveRow, BtcOptionQuoteArchiveRow]:
        self.validated()
        provenance = HistoricalProvenance(
            provider="COINDCX",
            source_id=f"{self.source_artifact_id}:{self.symbol}",
            availability_basis="FIRST_SEEN_CAPTURE",
            point_in_time_proven=True,
            immutable_archive=True,
            reconstructible_public_data=False,
        )
        contract = BtcOptionContractArchiveRow(
            snapshot=self.snapshot(),
            event_at=_utc(self.observed_at),
            available_at=_utc(self.first_seen_at),
            provenance=provenance,
        ).validated()
        quote = BtcOptionQuoteArchiveRow(
            symbol=self.symbol,
            event_at=_utc(self.observed_at),
            available_at=_utc(self.first_seen_at),
            bid=float(self.bid),
            ask=float(self.ask),
            provenance=provenance,
        ).validated()
        return contract, quote


async def _insert(store: Any, record: BtcPitArchiveRecord) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("Options PIT store must return dict from insert_first_seen")
    return result


async def ingest_verified_option_capture(store: Any, capture: VerifiedBtcOptionPitCapture) -> dict:
    chain_record, quote_record = capture.archive_records()
    chain_result = await _insert(store, chain_record)
    quote_result = await _insert(store, quote_record)
    return {
        "status": "BTC_OPTIONS_PIT_CAPTURE_ARCHIVED",
        "symbol": capture.symbol,
        "first_seen_at": _utc(capture.first_seen_at).isoformat(),
        "chain_storage_status": chain_result.get("status"),
        "quote_storage_status": quote_result.get("status"),
        "historical_contract_ready": True,
        "historical_quote_ready": True,
        "fabricated": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_OPTIONS_PIT_INGEST_CONTRACT_V1",
        "network_endpoint_discovered_by_this_module": False,
        "private_endpoint_reverse_engineering_allowed": False,
        "public_historical_options_api_claimed": False,
        "verified_external_capture_required": True,
        "provider_observed_at_and_first_seen_at_required": True,
        "selector_visibility_uses_first_seen_at": True,
        "option_chain_may_be_fabricated": False,
        "greeks_iv_oi_may_be_fabricated": False,
        "actual_bid_ask_may_be_fabricated": False,
        "options_trade_generation_allowed": False,
        "futures_fallback_allowed": False,
        "broker_execution_enabled": False,
        "research_only": True,
    }
