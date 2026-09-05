"""Optional Glassnode BTC on-chain provider for point-in-time research capture.

Glassnode PiT metrics are documented as append-only/immutable, while exchange
entity metrics can change as labels/heuristics improve. For strict click replay
this provider still records AlphaPilot first_seen_at for every fetch; immutable
PiT history may additionally support slower historical research.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any

import httpx

BASE_URL = "https://api.glassnode.com/v1/metrics"
METRICS = {
    "MVRV": {"path": "/indicators/mvrv_account_based_pit", "pit_immutable": True, "unit": "ratio"},
    "SOPR": {"path": "/indicators/sopr_account_based_pit", "pit_immutable": True, "unit": "ratio"},
    "EXCHANGE_NETFLOW": {"path": "/transactions/transfers_volume_exchanges_net", "pit_immutable": False, "unit": "BTC"},
    "WHALE_EXCHANGE_FLOW": {"path": "/transactions/transfers_volume_whales_to_exchanges_sum", "pit_immutable": False, "unit": "BTC"},
}
SUPPORTED_INTERVALS = {"10m", "1h", "24h"}


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite(name: str, value: Any) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _provider_time(value: Any) -> datetime:
    number = _finite("Glassnode timestamp", value)
    if number <= 0:
        raise ValueError("Glassnode timestamp must be > 0")
    return datetime.fromtimestamp(number, tz=timezone.utc)


@dataclass(frozen=True)
class GlassnodeBtcOnchainPolicy:
    enabled: bool = False
    api_key: str = ""
    timeout_seconds: float = 10.0
    interval: str = "1h"
    lookback_hours: int = 4
    metrics: tuple[str, ...] = tuple(METRICS)

    def validated(self) -> "GlassnodeBtcOnchainPolicy":
        if self.enabled and not str(self.api_key or "").strip():
            raise ValueError("Glassnode capture enabled but API key is missing")
        if self.interval not in SUPPORTED_INTERVALS:
            raise ValueError("unsupported Glassnode interval")
        if not 1 <= int(self.lookback_hours) <= 24 * 30:
            raise ValueError("Glassnode lookback_hours must be 1..720")
        timeout = float(self.timeout_seconds)
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("Glassnode timeout_seconds must be finite and > 0")
        unknown = sorted(set(self.metrics) - set(METRICS))
        if unknown:
            raise ValueError(f"unsupported Glassnode metrics: {unknown}")
        if len(set(self.metrics)) != len(self.metrics) or not self.metrics:
            raise ValueError("Glassnode metrics must be non-empty and unique")
        return self


@dataclass(frozen=True)
class GlassnodeMetricCapture:
    metric: str
    first_seen_at: datetime
    provider_time: datetime
    value: float
    interval: str
    endpoint: str
    unit: str
    historical_content_immutable: bool
    provider: str = "GLASSNODE"
    asset: str = "BTC"

    def validated(self) -> "GlassnodeMetricCapture":
        if self.metric not in METRICS:
            raise ValueError("unsupported Glassnode metric")
        if self.interval not in SUPPORTED_INTERVALS:
            raise ValueError("unsupported Glassnode interval")
        if _utc(self.provider_time) > _utc(self.first_seen_at):
            raise ValueError("provider_time cannot be after first_seen_at")
        _finite("Glassnode metric value", self.value)
        expected = METRICS[self.metric]
        if bool(self.historical_content_immutable) != bool(expected["pit_immutable"]):
            raise ValueError("historical-content immutability does not match documented metric family")
        return self


class GlassnodeBtcOnchainProvider:
    def __init__(self, policy: GlassnodeBtcOnchainPolicy | None = None, client: httpx.Client | None = None):
        self.policy = (policy or GlassnodeBtcOnchainPolicy()).validated()
        self._client = client

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise RuntimeError("Glassnode BTC on-chain collection is disabled by policy")

    def _get_metric(self, metric: str, *, first_seen_at: datetime) -> GlassnodeMetricCapture:
        self._require_enabled()
        spec = METRICS[metric]
        first_seen = _utc(first_seen_at)
        start = first_seen - timedelta(hours=self.policy.lookback_hours)
        url = BASE_URL + spec["path"]
        params = {
            "a": "BTC",
            "s": int(start.timestamp()),
            "u": int(first_seen.timestamp()),
            "i": self.policy.interval,
            "f": "json",
        }
        headers = {"X-Api-Key": self.policy.api_key}
        if self._client is not None:
            response = self._client.get(url, params=params, headers=headers, timeout=self.policy.timeout_seconds)
        else:
            with httpx.Client(timeout=self.policy.timeout_seconds) as client:
                response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("invalid Glassnode metric response")
        rows = []
        for raw in payload:
            if not isinstance(raw, dict) or raw.get("t") is None or raw.get("v") is None:
                continue
            stamp = _provider_time(raw["t"])
            if stamp <= first_seen:
                rows.append((stamp, raw))
        if not rows:
            raise ValueError(f"Glassnode {metric} response has no row visible by first_seen_at")
        rows.sort(key=lambda item: item[0])
        stamp, raw = rows[-1]
        return GlassnodeMetricCapture(
            metric=metric,
            first_seen_at=first_seen,
            provider_time=stamp,
            value=_finite(metric, raw["v"]),
            interval=self.policy.interval,
            endpoint=url,
            unit=spec["unit"],
            historical_content_immutable=bool(spec["pit_immutable"]),
        ).validated()

    def capture_metric(self, metric: str, *, first_seen_at: datetime) -> GlassnodeMetricCapture:
        key = str(metric or "").upper()
        if key not in self.policy.metrics:
            raise ValueError("Glassnode metric is not enabled by policy")
        return self._get_metric(key, first_seen_at=first_seen_at)

    def capture_all(self, *, first_seen_at: datetime) -> list[GlassnodeMetricCapture]:
        return [self.capture_metric(metric, first_seen_at=first_seen_at) for metric in self.policy.metrics]


def architecture_contract() -> dict:
    return {
        "version": "GLASSNODE_BTC_ONCHAIN_PROVIDER_V1",
        "collection_enabled_by_default": False,
        "api_key_required_when_enabled": True,
        "pit_metric_history_immutable": [name for name, spec in METRICS.items() if spec["pit_immutable"]],
        "mutable_entity_label_metrics": [name for name, spec in METRICS.items() if not spec["pit_immutable"]],
        "pit_content_immutability_equals_exact_delivery_time": False,
        "first_seen_at_still_required_for_click_replay": True,
        "exchange_label_history_assumed_immutable": False,
        "raw_onchain_metric_is_trade_signal": False,
        "trade_generation_allowed": False,
        "research_only": True,
    }
