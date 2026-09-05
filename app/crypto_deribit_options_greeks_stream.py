"""Disabled-by-default Deribit BTC options ticker-Greeks WebSocket capture.

The service subscribes only to documented public ``ticker.*`` channels and feeds
notifications through the same point-in-time normalizer used by tests/replay.
Only a defensible observed-delta 25d pair is archived. Deribit remains global
options context: no CoinDCX contract selection, fill, P&L or trade generation is
allowed here.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Callable

from app.crypto_deribit_options_greeks_pit import DATASET, deribit_greeks_archive_record
from app.deribit_btc_options_ticker_greeks import (
    DeribitBtcOptionsGreeksBook,
    DeribitDeltaSkewPolicy,
    DeribitOptionInstrumentMeta,
    ticker_capture_from_notification,
    ticker_subscription_channels,
)

DERIBIT_PRODUCTION_WS_URL = "wss://www.deribit.com/ws/api/v2"
SUBSCRIBE_REQUEST_ID = 42


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _default_connector(url: str):
    from websockets.asyncio.client import connect

    return connect(url, ping_interval=20, ping_timeout=20, close_timeout=10)


async def _insert(store: Any, record) -> dict:
    result = store.insert_first_seen(record)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("Deribit Greeks PIT store insert_first_seen must return a dict")
    return result


@dataclass(frozen=True)
class DeribitOptionsGreeksStreamPolicy:
    enabled: bool = False
    websocket_url: str = DERIBIT_PRODUCTION_WS_URL
    ticker_interval: str = "agg2"
    max_expiries: int = 2
    max_channels: int = 300
    archive_min_interval_seconds: int = 10
    target_abs_delta: float = 0.25
    max_delta_distance: float = 0.08
    max_ticker_age_seconds: int = 15
    max_pair_first_seen_gap_seconds: int = 5
    min_seconds_to_expiry: int = 3600

    def validated(self) -> "DeribitOptionsGreeksStreamPolicy":
        if self.ticker_interval not in {"100ms", "agg2"}:
            raise ValueError("public Deribit ticker interval must be 100ms or agg2")
        if int(self.max_expiries) <= 0:
            raise ValueError("max_expiries must be > 0")
        if int(self.max_channels) <= 0:
            raise ValueError("max_channels must be > 0")
        if int(self.archive_min_interval_seconds) < 1:
            raise ValueError("archive_min_interval_seconds must be >= 1")
        if not str(self.websocket_url or "").startswith("wss://"):
            raise ValueError("Deribit WebSocket URL must use wss://")
        DeribitDeltaSkewPolicy(
            target_abs_delta=float(self.target_abs_delta),
            max_delta_distance=float(self.max_delta_distance),
            max_ticker_age_seconds=int(self.max_ticker_age_seconds),
            max_pair_first_seen_gap_seconds=int(self.max_pair_first_seen_gap_seconds),
            min_seconds_to_expiry=int(self.min_seconds_to_expiry),
            max_expiries_to_scan=int(self.max_expiries),
        ).validated()
        return self

    def skew_policy(self) -> DeribitDeltaSkewPolicy:
        self.validated()
        return DeribitDeltaSkewPolicy(
            target_abs_delta=float(self.target_abs_delta),
            max_delta_distance=float(self.max_delta_distance),
            max_ticker_age_seconds=int(self.max_ticker_age_seconds),
            max_pair_first_seen_gap_seconds=int(self.max_pair_first_seen_gap_seconds),
            min_seconds_to_expiry=int(self.min_seconds_to_expiry),
            max_expiries_to_scan=int(self.max_expiries),
        ).validated()


def select_stream_instruments(
    instruments: dict[str, DeribitOptionInstrumentMeta],
    *,
    as_of: datetime,
    policy: DeribitOptionsGreeksStreamPolicy,
) -> dict[str, DeribitOptionInstrumentMeta]:
    """Select whole strike sets for the nearest expiries without using delta."""
    policy = policy.validated()
    cutoff = _utc(as_of)
    rows = [
        row.validated()
        for row in instruments.values()
        if _utc(row.expiry_at).timestamp() - cutoff.timestamp() > int(policy.min_seconds_to_expiry)
    ]
    expiries = sorted({_utc(row.expiry_at) for row in rows})[: int(policy.max_expiries)]
    selected = {row.instrument_name: row for row in rows if _utc(row.expiry_at) in set(expiries)}
    if not selected:
        raise ValueError("no Deribit BTC option instruments qualify for Greeks streaming")
    if len(selected) > int(policy.max_channels):
        raise ValueError("nearest-expiry Deribit option set exceeds configured max_channels")
    return dict(sorted(selected.items()))


def build_public_subscribe_request(
    instruments: dict[str, DeribitOptionInstrumentMeta],
    *,
    interval: str = "agg2",
) -> dict:
    channels = ticker_subscription_channels(instruments.values(), interval=interval)
    return {
        "jsonrpc": "2.0",
        "method": "public/subscribe",
        "id": SUBSCRIBE_REQUEST_ID,
        "params": {"channels": list(channels)},
    }


def _decode_message(raw: Any) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        value = json.loads(raw)
    elif isinstance(raw, dict):
        value = raw
    else:
        raise ValueError("unsupported Deribit WebSocket message type")
    if not isinstance(value, dict):
        raise ValueError("Deribit WebSocket message must decode to an object")
    return value


class DeribitOptionsGreeksStreamService:
    def __init__(
        self,
        *,
        instruments: dict[str, DeribitOptionInstrumentMeta],
        store: Any,
        policy: DeribitOptionsGreeksStreamPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        websocket_connector: Callable[[str], Any] | None = None,
    ):
        self.policy = (policy or DeribitOptionsGreeksStreamPolicy()).validated()
        self._all_instruments = dict(instruments)
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.websocket_connector = websocket_connector or _default_connector
        self.book = DeribitBtcOptionsGreeksBook(self.policy.skew_policy())
        self._selected: dict[str, DeribitOptionInstrumentMeta] | None = None
        self._last_archive_at: datetime | None = None
        self.messages_seen = 0
        self.ticker_updates = 0
        self.snapshots_archived = 0
        self.idempotent_duplicates = 0
        self.no_pair_updates = 0
        self.throttled_updates = 0

    def selected_instruments(self, *, as_of: datetime | None = None) -> dict[str, DeribitOptionInstrumentMeta]:
        if self._selected is None:
            self._selected = select_stream_instruments(
                self._all_instruments,
                as_of=_utc(as_of or self.clock()),
                policy=self.policy,
            )
        return dict(self._selected)

    async def process_message(self, raw: Any) -> dict:
        message = _decode_message(raw)
        self.messages_seen += 1
        if message.get("method") != "subscription":
            return {"status": "NON_SUBSCRIPTION_MESSAGE_IGNORED", "trade_generated": False}

        now = _utc(self.clock())
        capture = ticker_capture_from_notification(
            message,
            instruments=self.selected_instruments(as_of=now),
            first_seen_at=now,
        )
        update = self.book.ingest(capture)
        if update["status"] == "TICKER_STATE_UPDATED":
            self.ticker_updates += 1
        snapshot = self.book.snapshot_25d(as_of=now)
        if snapshot is None:
            self.no_pair_updates += 1
            return {
                "status": "DERIBIT_GREEKS_TICKER_CAPTURED_NO_25D_PAIR",
                "instrument_name": capture.instrument_name,
                "storage_status": None,
                "trade_generated": False,
            }

        if self._last_archive_at is not None:
            elapsed = (snapshot.first_seen_at - self._last_archive_at).total_seconds()
            if elapsed < int(self.policy.archive_min_interval_seconds):
                self.throttled_updates += 1
                return {
                    "status": "DERIBIT_GREEKS_25D_ARCHIVE_THROTTLED",
                    "instrument_name": capture.instrument_name,
                    "skew_25d": snapshot.put_call_skew_25d_iv_points,
                    "storage_status": None,
                    "trade_generated": False,
                }

        stored = await _insert(self.store, deribit_greeks_archive_record(snapshot))
        storage_status = stored.get("status")
        if storage_status == "INSERTED_FIRST_SEEN":
            self.snapshots_archived += 1
            self._last_archive_at = snapshot.first_seen_at
        elif storage_status == "IDEMPOTENT_DUPLICATE":
            self.idempotent_duplicates += 1
            self._last_archive_at = snapshot.first_seen_at
        else:
            raise ValueError(f"unexpected Deribit Greeks PIT storage status: {storage_status!r}")
        return {
            "status": "DERIBIT_GREEKS_25D_ARCHIVED",
            "dataset": DATASET,
            "skew_25d": snapshot.put_call_skew_25d_iv_points,
            "call_instrument": snapshot.call.instrument_name,
            "put_instrument": snapshot.put.instrument_name,
            "first_seen_at": snapshot.first_seen_at.isoformat(),
            "storage_status": storage_status,
            "global_options_context_only": True,
            "coindcx_contract_selection_allowed": False,
            "coindcx_quote_fill_allowed": False,
            "coindcx_pnl_replay_allowed": False,
            "trade_generated": False,
        }

    async def _await_subscription_ack(self, websocket, *, request: dict) -> list[dict]:
        await websocket.send(json.dumps(request, separators=(",", ":")))
        pending: list[dict] = []
        expected_channels = set(request["params"]["channels"])
        while True:
            message = _decode_message(await websocket.recv())
            if message.get("id") == SUBSCRIBE_REQUEST_ID:
                if message.get("error") is not None:
                    raise RuntimeError(f"Deribit public/subscribe failed: {message['error']}")
                result = message.get("result")
                if not isinstance(result, list) or set(map(str, result)) != expected_channels:
                    raise ValueError("Deribit subscription acknowledgement does not match requested channels")
                return pending
            if message.get("method") == "subscription":
                pending.append(message)

    async def run_session(self, stop_event: asyncio.Event) -> dict:
        if not self.policy.enabled:
            return {
                "status": "DERIBIT_OPTIONS_GREEKS_STREAM_DISABLED",
                "connection_opened": False,
                "messages_seen": 0,
                "trade_generated": False,
            }
        selected = self.selected_instruments()
        request = build_public_subscribe_request(selected, interval=self.policy.ticker_interval)
        connector = self.websocket_connector
        async with connector(self.policy.websocket_url) as websocket:
            pending = await self._await_subscription_ack(websocket, request=request)
            for message in pending:
                await self.process_message(message)
            while not stop_event.is_set():
                recv_task = asyncio.create_task(websocket.recv())
                stop_task = asyncio.create_task(stop_event.wait())
                done, pending_tasks = await asyncio.wait(
                    {recv_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending_tasks:
                    task.cancel()
                if stop_task in done and stop_task.result() is True:
                    if recv_task not in done:
                        recv_task.cancel()
                    break
                raw = recv_task.result()
                await self.process_message(raw)
        return {
            "status": "DERIBIT_OPTIONS_GREEKS_STREAM_STOPPED",
            "connection_opened": True,
            "selected_instrument_count": len(selected),
            "messages_seen": self.messages_seen,
            "ticker_updates": self.ticker_updates,
            "snapshots_archived": self.snapshots_archived,
            "idempotent_duplicates": self.idempotent_duplicates,
            "no_pair_updates": self.no_pair_updates,
            "throttled_updates": self.throttled_updates,
            "trade_generated": False,
        }


def architecture_contract() -> dict:
    return {
        "version": "DERIBIT_OPTIONS_GREEKS_STREAM_V1",
        "enabled_by_default": False,
        "production_websocket": DERIBIT_PRODUCTION_WS_URL,
        "documented_subscription_method": "public/subscribe",
        "documented_channel_shape": "ticker.{instrument_name}.{interval}",
        "public_default_interval": "agg2",
        "authentication_required": False,
        "selection_uses_expiry_not_inferred_delta": True,
        "delta_source": "DERIBIT_TICKER_GREEKS",
        "delta_inferred_from_strike": False,
        "archive_requires_valid_25d_pair": True,
        "missing_25d_pair_is_not_estimated": True,
        "network_request_at_import": False,
        "coindcx_contract_selection_allowed": False,
        "coindcx_quote_fill_allowed": False,
        "coindcx_pnl_replay_allowed": False,
        "underlying_direction_assigned": False,
        "options_trade_generation_allowed": False,
        "futures_trade_generation_allowed": False,
        "research_only": True,
    }
