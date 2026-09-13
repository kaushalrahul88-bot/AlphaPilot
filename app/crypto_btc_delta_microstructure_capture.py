"""Point-in-time Delta India BTC microstructure capture for the Crypto Brain.

Uses only documented unauthenticated public endpoints and archives what AlphaPilot
actually observed at capture time: BTCUSD ticker/open interest, L2 depth and
recent-trade imbalance. The snapshot is context only and cannot create orders.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from app.crypto_btc_pit_archive import archive_record_from_capture
from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore

logger = logging.getLogger("alphapilot.crypto.btc.delta-microstructure")

DATASET = "BTC_DELTA_MICROSTRUCTURE_SNAPSHOT"
PROVIDER = "DELTA_EXCHANGE_INDIA"
SYMBOL = "BTCUSD"
TICKER_URL = f"https://api.india.delta.exchange/v2/tickers/{SYMBOL}"
ORDERBOOK_URL = f"https://api.india.delta.exchange/v2/l2orderbook/{SYMBOL}"
TRADES_URL = f"https://api.india.delta.exchange/v2/trades/{SYMBOL}"
ENV_ENABLED = "ALPHAPILOT_CRYPTO_BTC_DELTA_MICROSTRUCTURE_ENABLED"
ENV_POLL_SECONDS = "ALPHAPILOT_CRYPTO_BTC_DELTA_MICROSTRUCTURE_POLL_SECONDS"
ENV_DATABASE_URL = "DATABASE_URL"
MIN_POLL_SECONDS = 30
DEPTH_LEVELS = 10


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


def _int(value: str | None, default: int) -> int:
    if value is None or not str(value).strip():
        return int(default)
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"invalid integer environment value: {value!r}") from exc


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _event_time(value: Any) -> datetime | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    if number >= 1e15:
        number /= 1_000_000.0
    elif number >= 1e12:
        number /= 1_000.0
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _result(payload: Any, *, endpoint: str) -> Any:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError(f"invalid Delta {endpoint} response")
    if "result" not in payload:
        raise ValueError(f"Delta {endpoint} response has no result")
    return payload["result"]


def _levels(value: Any, *, reverse: bool) -> list[dict[str, float]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, float]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        price, size = _number(raw.get("price")), _number(raw.get("size"))
        if price is None or size is None or price <= 0 or size < 0:
            continue
        rows.append({"price": price, "size": size})
    rows.sort(key=lambda row: row["price"], reverse=reverse)
    return rows[:DEPTH_LEVELS]


def _trades(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("trades")
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        side = str(raw.get("side") or "").lower()
        price, size = _number(raw.get("price")), _number(raw.get("size"))
        stamp = _event_time(raw.get("timestamp") or raw.get("time"))
        if side not in {"buy", "sell"} or price is None or size is None or price <= 0 or size < 0:
            continue
        rows.append({"side": side, "price": price, "size": size, "timestamp": stamp})
    return rows


def normalize_delta_microstructure_snapshot(
    ticker_payload: Any,
    orderbook_payload: Any,
    trades_payload: Any,
    *,
    first_seen_at: datetime,
) -> dict[str, Any]:
    first_seen = _utc(first_seen_at)
    ticker = _result(ticker_payload, endpoint="ticker")
    book = _result(orderbook_payload, endpoint="orderbook")
    recent = _result(trades_payload, endpoint="trades")
    if not isinstance(ticker, dict) or not isinstance(book, dict):
        raise ValueError("Delta ticker/orderbook result must be an object")

    bids, asks = _levels(book.get("buy"), reverse=True), _levels(book.get("sell"), reverse=False)
    trade_rows = _trades(recent)
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    bid_size, ask_size = sum(r["size"] for r in bids), sum(r["size"] for r in asks)
    depth_total = bid_size + ask_size
    depth_imbalance = None if depth_total <= 0 else (bid_size - ask_size) / depth_total
    spread_bps = None
    if best_bid is not None and best_ask is not None and best_ask >= best_bid:
        mid = (best_bid + best_ask) / 2.0
        spread_bps = None if mid <= 0 else (best_ask - best_bid) / mid * 10_000.0

    buy_size = sum(r["size"] for r in trade_rows if r["side"] == "buy")
    sell_size = sum(r["size"] for r in trade_rows if r["side"] == "sell")
    buy_notional = sum(r["size"] * r["price"] for r in trade_rows if r["side"] == "buy")
    sell_notional = sum(r["size"] * r["price"] for r in trade_rows if r["side"] == "sell")
    trade_total = buy_notional + sell_notional
    trade_imbalance = None if trade_total <= 0 else (buy_notional - sell_notional) / trade_total

    ticker_event = _event_time(ticker.get("timestamp") or ticker.get("time"))
    orderbook_event = _event_time(book.get("last_updated_at") or book.get("timestamp"))
    trade_events = [r["timestamp"] for r in trade_rows if r["timestamp"] is not None]
    provider_events = [s for s in (ticker_event, orderbook_event, *trade_events) if s is not None]
    event_at = max((s for s in provider_events if s <= first_seen), default=None)
    quotes = ticker.get("quotes") if isinstance(ticker.get("quotes"), dict) else {}

    payload = {
        "symbol": SYMBOL,
        "context_only": True,
        "instrument_role": "UNDERLYING_AND_DERIVATIVES_CONTEXT_ONLY",
        "ticker": {
            "close": _number(ticker.get("close")),
            "mark_price": _number(ticker.get("mark_price")),
            "spot_price": _number(ticker.get("spot_price")),
            "open_interest_contracts": _number(ticker.get("oi")),
            "open_interest_value": _number(ticker.get("oi_value")),
            "open_interest_value_usd": _number(ticker.get("oi_value_usd")),
            "volume": _number(ticker.get("volume")),
            "turnover": _number(ticker.get("turnover")),
            "turnover_usd": _number(ticker.get("turnover_usd")),
            "best_bid": _number(quotes.get("best_bid")),
            "best_ask": _number(quotes.get("best_ask")),
            "provider_timestamp": None if ticker_event is None else ticker_event.isoformat(),
        },
        "orderbook": {
            "levels_per_side": DEPTH_LEVELS,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_bps": spread_bps,
            "bid_size_top_levels": bid_size,
            "ask_size_top_levels": ask_size,
            "depth_imbalance": depth_imbalance,
            "bids": bids,
            "asks": asks,
            "provider_timestamp": None if orderbook_event is None else orderbook_event.isoformat(),
        },
        "recent_trades": {
            "sample_count": len(trade_rows),
            "buy_count": sum(r["side"] == "buy" for r in trade_rows),
            "sell_count": sum(r["side"] == "sell" for r in trade_rows),
            "buy_size": buy_size,
            "sell_size": sell_size,
            "buy_notional": buy_notional,
            "sell_notional": sell_notional,
            "notional_imbalance": trade_imbalance,
            "earliest_timestamp": None if not trade_events else min(trade_events).isoformat(),
            "latest_timestamp": None if not trade_events else max(trade_events).isoformat(),
        },
        "provenance": {
            "ticker_url": TICKER_URL,
            "orderbook_url": ORDERBOOK_URL,
            "trades_url": TRADES_URL,
            "authentication_required": False,
            "first_seen_at": first_seen.isoformat(),
            "future_rows_used": False,
            "historical_reconstruction": False,
        },
        "may_generate_options_trade": False,
        "may_generate_futures_trade": False,
        "may_satisfy_options_contract_quote": False,
        "live_execution": False,
        "capital_committed_inr": 0,
    }
    return {"first_seen_at": first_seen, "event_at": event_at, "payload": payload}


@dataclass(frozen=True)
class DeltaMicrostructureRuntimeConfig:
    enabled: bool = False
    database_url: str = ""
    poll_seconds: int = 60
    timeout_seconds: float = 12.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DeltaMicrostructureRuntimeConfig":
        source = os.environ if env is None else env
        return cls(
            enabled=_bool(source.get(ENV_ENABLED), False),
            database_url=str(source.get(ENV_DATABASE_URL, "") or "").strip(),
            poll_seconds=_int(source.get(ENV_POLL_SECONDS), 60),
        ).validated()

    def validated(self) -> "DeltaMicrostructureRuntimeConfig":
        if self.enabled and not self.database_url:
            raise ValueError("Delta BTC microstructure capture enabled but DATABASE_URL is missing")
        if int(self.poll_seconds) < MIN_POLL_SECONDS:
            raise ValueError(f"Delta BTC microstructure poll_seconds must be >= {MIN_POLL_SECONDS}")
        if not math.isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise ValueError("Delta BTC microstructure timeout_seconds must be finite and > 0")
        return self


class DeltaMicrostructureProvider:
    def __init__(self, *, timeout_seconds: float = 12.0, client: httpx.AsyncClient | None = None) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.client = client

    async def _get(self, client: httpx.AsyncClient, url: str) -> Any:
        response = await client.get(url, headers={"Accept": "application/json"}, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    async def capture(self, *, first_seen_at: datetime | None = None) -> dict[str, Any]:
        stamp = _utc(first_seen_at or datetime.now(timezone.utc))
        if self.client is not None:
            ticker, book, trades = await asyncio.gather(
                self._get(self.client, TICKER_URL),
                self._get(self.client, ORDERBOOK_URL),
                self._get(self.client, TRADES_URL),
            )
        else:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                ticker, book, trades = await asyncio.gather(
                    self._get(client, TICKER_URL), self._get(client, ORDERBOOK_URL), self._get(client, TRADES_URL)
                )
        return normalize_delta_microstructure_snapshot(ticker, book, trades, first_seen_at=stamp)


def delta_microstructure_archive_record(snapshot: dict[str, Any]):
    first_seen = _utc(snapshot["first_seen_at"])
    return archive_record_from_capture(
        dataset=DATASET,
        provider=PROVIDER,
        source_key=f"{SYMBOL}:{int(first_seen.timestamp() * 1000)}",
        first_seen_at=first_seen,
        event_at=snapshot.get("event_at"),
        source_version="DELTA_INDIA_PUBLIC_MICROSTRUCTURE_V1",
        payload=dict(snapshot["payload"]),
    )


async def run_delta_microstructure_service(
    config: DeltaMicrostructureRuntimeConfig,
    *,
    stop_event: asyncio.Event,
    provider: DeltaMicrostructureProvider | None = None,
    store: Any | None = None,
) -> dict[str, Any]:
    cfg = config.validated()
    if not cfg.enabled:
        return {"status": "BTC_DELTA_MICROSTRUCTURE_DISABLED", "cycles": 0, "inserted": 0, "failures": 0}
    archive = store or PostgresBtcPitArchiveStore(cfg.database_url)
    await archive.initialize()
    source = provider or DeltaMicrostructureProvider(timeout_seconds=cfg.timeout_seconds)
    cycles = inserted = duplicates = failures = 0
    while not stop_event.is_set():
        cycles += 1
        try:
            snapshot = await source.capture(first_seen_at=datetime.now(timezone.utc))
            record = delta_microstructure_archive_record(snapshot)
            result = archive.insert_first_seen(record)
            if hasattr(result, "__await__"):
                result = await result
            status = str((result or {}).get("status") or "")
            inserted += int(status == "INSERTED_FIRST_SEEN")
            duplicates += int(status == "IDEMPOTENT_DUPLICATE")
        except Exception as exc:
            failures += 1
            logger.warning("Delta BTC microstructure capture failed error=%s: %s", exc.__class__.__name__, str(exc)[:500])
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=cfg.poll_seconds)
        except TimeoutError:
            pass
    return {
        "status": "BTC_DELTA_MICROSTRUCTURE_STOPPED",
        "cycles": cycles,
        "inserted": inserted,
        "idempotent_duplicates": duplicates,
        "failures": failures,
    }


def register_delta_btc_microstructure_startup(app) -> None:
    state: dict[str, Any] = {"stop_event": None, "task": None}

    @app.on_event("startup")
    async def _start_delta_microstructure() -> None:
        try:
            config = DeltaMicrostructureRuntimeConfig.from_env()
        except Exception as exc:
            logger.error("Delta BTC microstructure configuration invalid: %s", str(exc)[:500])
            return
        if not config.enabled:
            logger.info("Delta BTC microstructure capture disabled")
            return
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            run_delta_microstructure_service(config, stop_event=stop_event),
            name="alphapilot-btc-delta-microstructure",
        )
        state["stop_event"] = stop_event
        state["task"] = task
        logger.info("Delta BTC microstructure capture started poll_seconds=%s", config.poll_seconds)

    @app.on_event("shutdown")
    async def _stop_delta_microstructure() -> None:
        stop_event, task = state.get("stop_event"), state.get("task")
        if isinstance(stop_event, asyncio.Event):
            stop_event.set()
        if isinstance(task, asyncio.Task):
            try:
                await asyncio.wait_for(task, timeout=10)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        state["stop_event"] = None
        state["task"] = None


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "BTC_DELTA_MICROSTRUCTURE_CAPTURE_CONTRACT_V1",
        "dataset": DATASET,
        "venue": PROVIDER,
        "symbol": SYMBOL,
        "public_unauthenticated_endpoints": [TICKER_URL, ORDERBOOK_URL, TRADES_URL],
        "ticker_open_interest_captured": True,
        "l2_depth_captured": True,
        "recent_trade_imbalance_captured": True,
        "historical_reconstruction": False,
        "first_seen_timestamp_required": True,
        "collection_enabled_by_default": False,
        "minimum_poll_seconds": MIN_POLL_SECONDS,
        "options_quote_substitution_allowed": False,
        "options_trade_generation_allowed": False,
        "futures_trade_generation_allowed": False,
        "live_execution": False,
        "capital_committed_inr": 0,
        "research_only": True,
    }
