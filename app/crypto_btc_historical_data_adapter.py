"""Point-in-time historical data adapter for the BTC Crypto Brain.

Research/backtest only. Historical data may influence a click only when its
availability timestamp is proven and no later than the click. CoinDCX candle
`time` values are bar-open timestamps, so reconstructed candles become visible
only at bar completion. Historical Options snapshots/Greeks/quotes are admitted
only from explicitly point-in-time-proven archives; they are never fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Literal

from app.crypto_btc_options_contract_selector import BtcOptionContractSnapshot
from app.crypto_btc_options_risk import BtcOptionsExecutionSpec
from app.crypto_btc_options_shadow_replay import BtcOptionsReplayCostSpec, BtcOptionsReplayObservation
from app.crypto_btc_perception import BtcSpotStructureSnapshot, spot_structure_context
from app.crypto_btc_random_click_experience import BtcForwardPriceObservation
from app.crypto_market_intelligence import Evidence

AvailabilityBasis = Literal[
    "BAR_COMPLETION_RECONSTRUCTION",
    "FIRST_SEEN_CAPTURE",
    "IMMUTABLE_ARCHIVE",
    "OFFICIAL_RELEASE",
]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _positive(name: str, value: float) -> float:
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")
    return value


def _nonnegative(name: str, value: float) -> float:
    value = float(value)
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and >= 0")
    return value


@dataclass(frozen=True)
class HistoricalProvenance:
    provider: str
    source_id: str
    availability_basis: AvailabilityBasis
    point_in_time_proven: bool
    immutable_archive: bool = False
    reconstructible_public_data: bool = False

    def validated(self) -> "HistoricalProvenance":
        if not str(self.provider or "").strip() or not str(self.source_id or "").strip():
            raise ValueError("provider and source_id are required")
        if self.availability_basis not in {
            "BAR_COMPLETION_RECONSTRUCTION", "FIRST_SEEN_CAPTURE", "IMMUTABLE_ARCHIVE", "OFFICIAL_RELEASE"
        }:
            raise ValueError("unsupported availability_basis")
        if self.immutable_archive and self.reconstructible_public_data:
            raise ValueError("source cannot be both immutable and reconstructible")
        return self


@dataclass(frozen=True)
class BtcSpotCandleArchiveRow:
    open_at: datetime
    close_at: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    provenance: HistoricalProvenance
    source: str = "COINDCX_PUBLIC_SPOT_CANDLES"

    def validated(self) -> "BtcSpotCandleArchiveRow":
        self.provenance.validated()
        if _utc(self.close_at) <= _utc(self.open_at):
            raise ValueError("close_at must be after open_at")
        if _utc(self.available_at) < _utc(self.close_at):
            raise ValueError("candle cannot be visible before bar completion")
        for name in ("open", "high", "low", "close"):
            _positive(name, getattr(self, name))
        _nonnegative("volume", self.volume)
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("invalid OHLC geometry")
        return self


@dataclass(frozen=True)
class BtcHistoricalEvidenceRow:
    evidence: Evidence
    available_at: datetime
    provenance: HistoricalProvenance
    event_at: datetime | None = None

    def validated(self) -> "BtcHistoricalEvidenceRow":
        self.provenance.validated()
        if self.event_at is not None and _utc(self.available_at) < _utc(self.event_at):
            raise ValueError("available_at cannot precede event_at")
        return self

    def visible_evidence(self) -> Evidence:
        self.validated()
        metadata = dict(self.evidence.metadata or {})
        metadata["historical_provenance"] = {
            "provider": self.provenance.provider,
            "source_id": self.provenance.source_id,
            "availability_basis": self.provenance.availability_basis,
            "point_in_time_proven": self.provenance.point_in_time_proven,
            "event_at": None if self.event_at is None else _utc(self.event_at).isoformat(),
            "available_at": _utc(self.available_at).isoformat(),
        }
        return replace(self.evidence, observed_at=_utc(self.available_at), metadata=metadata)


@dataclass(frozen=True)
class BtcOptionContractArchiveRow:
    snapshot: BtcOptionContractSnapshot
    available_at: datetime
    provenance: HistoricalProvenance
    event_at: datetime | None = None

    def validated(self) -> "BtcOptionContractArchiveRow":
        self.provenance.validated()
        if self.event_at is not None and _utc(self.available_at) < _utc(self.event_at):
            raise ValueError("available_at cannot precede event_at")
        if str(self.snapshot.platform).upper() != "COINDCX" or str(self.snapshot.underlying).upper() != "BTC":
            raise ValueError("historical contract must be CoinDCX BTC Options")
        if not str(self.snapshot.symbol or "").strip():
            raise ValueError("option symbol is required")
        return self

    def visible_snapshot(self) -> BtcOptionContractSnapshot:
        self.validated()
        return replace(self.snapshot, observed_at=_utc(self.available_at))


@dataclass(frozen=True)
class BtcOptionQuoteArchiveRow:
    symbol: str
    available_at: datetime
    bid: float | None
    ask: float | None
    provenance: HistoricalProvenance
    event_at: datetime | None = None

    def validated(self) -> "BtcOptionQuoteArchiveRow":
        self.provenance.validated()
        if self.event_at is not None and _utc(self.available_at) < _utc(self.event_at):
            raise ValueError("available_at cannot precede event_at")
        if not str(self.symbol or "").strip():
            raise ValueError("option quote symbol is required")
        for name in ("bid", "ask"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative(name, value)
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("option ask cannot be below bid")
        return self


@dataclass(frozen=True)
class BtcOptionsExecutionArchiveRow:
    available_at: datetime
    execution_spec: BtcOptionsExecutionSpec
    selector_fee_bps_per_side: float
    replay_costs: BtcOptionsReplayCostSpec
    provenance: HistoricalProvenance

    def validated(self) -> "BtcOptionsExecutionArchiveRow":
        self.provenance.validated()
        self.execution_spec.validated()
        self.replay_costs.validated()
        _nonnegative("selector_fee_bps_per_side", self.selector_fee_bps_per_side)
        return self


@dataclass(frozen=True)
class BtcHistoricalArchive:
    spot_candles: tuple[BtcSpotCandleArchiveRow, ...] = ()
    evidence_rows: tuple[BtcHistoricalEvidenceRow, ...] = ()
    option_contract_rows: tuple[BtcOptionContractArchiveRow, ...] = ()
    option_quote_rows: tuple[BtcOptionQuoteArchiveRow, ...] = ()
    execution_rows: tuple[BtcOptionsExecutionArchiveRow, ...] = ()

    def validated(self) -> "BtcHistoricalArchive":
        for rows in (self.spot_candles, self.evidence_rows, self.option_contract_rows, self.option_quote_rows, self.execution_rows):
            for row in rows:
                row.validated()
        return self


_INTERVAL_SECONDS = {"1m": 60, "15m": 900, "1h": 3600, "1d": 86400}


def normalize_coindcx_spot_candles(payload: list[dict] | dict, *, interval: str) -> list[BtcSpotCandleArchiveRow]:
    """Normalize CoinDCX spot candles; documented `time` is bar open in ms."""
    if interval not in _INTERVAL_SECONDS:
        raise ValueError("unsupported CoinDCX spot interval")
    raw_rows = payload if isinstance(payload, list) else payload.get("data")
    if not isinstance(raw_rows, list):
        raise ValueError("candle payload must be a list or contain data list")
    duration = timedelta(seconds=_INTERVAL_SECONDS[interval])
    by_open: dict[datetime, BtcSpotCandleArchiveRow] = {}
    for raw in raw_rows:
        open_at = datetime.fromtimestamp(float(raw["time"]) / 1000.0, tz=timezone.utc)
        close_at = open_at + duration
        row = BtcSpotCandleArchiveRow(
            open_at=open_at,
            close_at=close_at,
            available_at=close_at,
            open=float(raw["open"]), high=float(raw["high"]), low=float(raw["low"]), close=float(raw["close"]),
            volume=float(raw.get("volume", 0.0)),
            provenance=HistoricalProvenance(
                provider="COINDCX",
                source_id=f"btc-usdt:{interval}:{int(float(raw['time']))}",
                availability_basis="BAR_COMPLETION_RECONSTRUCTION",
                point_in_time_proven=True,
                reconstructible_public_data=True,
            ),
        ).validated()
        if open_at in by_open and by_open[open_at] != row:
            raise ValueError("conflicting duplicate candle")
        by_open[open_at] = row
    return sorted(by_open.values(), key=lambda row: _utc(row.open_at))


def _visible_spot(archive: BtcHistoricalArchive, as_of: datetime) -> list[BtcSpotCandleArchiveRow]:
    as_of = _utc(as_of)
    return sorted(
        [row for row in archive.spot_candles if row.provenance.point_in_time_proven and _utc(row.available_at) <= as_of],
        key=lambda row: _utc(row.available_at),
    )


def latest_spot_candle(archive: BtcHistoricalArchive, *, as_of: datetime, max_age_seconds: int) -> BtcSpotCandleArchiveRow | None:
    if int(max_age_seconds) < 0:
        raise ValueError("max_age_seconds must be >= 0")
    rows = _visible_spot(archive, as_of)
    if not rows:
        return None
    latest = rows[-1]
    return latest if (_utc(as_of) - _utc(latest.available_at)).total_seconds() <= int(max_age_seconds) else None


def _close_at_or_before(rows: list[BtcSpotCandleArchiveRow], target: datetime) -> BtcSpotCandleArchiveRow | None:
    eligible = [row for row in rows if _utc(row.available_at) <= _utc(target)]
    return eligible[-1] if eligible else None


def _hour_volume(rows: list[BtcSpotCandleArchiveRow], end_at: datetime) -> float | None:
    end, start = _utc(end_at), _utc(end_at) - timedelta(hours=1)
    window = [row for row in rows if start < _utc(row.available_at) <= end]
    return None if not window else sum(float(row.volume) for row in window)


def derive_spot_structure_evidence(archive: BtcHistoricalArchive, *, decision_at: datetime, max_spot_age_seconds: int) -> Evidence | None:
    """Derive BTC 1h/4h/24h structure from completed, visible candles only."""
    decision = _utc(decision_at)
    rows = _visible_spot(archive, decision)
    latest = latest_spot_candle(archive, as_of=decision, max_age_seconds=max_spot_age_seconds)
    if latest is None:
        return None
    anchors = {h: _close_at_or_before(rows, decision - timedelta(hours=h)) for h in (1, 4, 24)}
    if any(row is None for row in anchors.values()):
        return None
    price = float(latest.close)
    returns = {h: (price - float(anchors[h].close)) / float(anchors[h].close) * 100.0 for h in (1, 4, 24)}
    recent = [row for row in rows if decision - timedelta(hours=1) < _utc(row.available_at) <= decision]
    if not recent:
        return None
    high, low = max(row.high for row in recent), min(row.low for row in recent)
    close_location = 0.5 if high <= low else max(0.0, min(1.0, (price - low) / (high - low)))
    current_volume = _hour_volume(rows, decision)
    history = [_hour_volume(rows, decision - timedelta(hours=i)) for i in range(1, 25)]
    history = [value for value in history if value is not None]
    if current_volume is None or len(history) < 12:
        return None
    volume_percentile = sum(value <= current_volume for value in history) / len(history)
    return spot_structure_context(BtcSpotStructureSnapshot(
        observed_at=_utc(latest.available_at), price=price,
        return_1h_pct=returns[1], return_4h_pct=returns[4], return_24h_pct=returns[24],
        close_location=close_location, volume_percentile=volume_percentile,
        breakout_state="NONE", source=latest.source,
    ))


def visible_evidence_at(archive: BtcHistoricalArchive, *, decision_at: datetime, max_spot_age_seconds: int) -> list[Evidence]:
    decision = _utc(decision_at)
    latest_by_source: dict[str, BtcHistoricalEvidenceRow] = {}
    for row in archive.evidence_rows:
        row.validated()
        if not row.provenance.point_in_time_proven or _utc(row.available_at) > decision:
            continue
        prior = latest_by_source.get(row.provenance.source_id)
        if prior is None or _utc(row.available_at) > _utc(prior.available_at):
            latest_by_source[row.provenance.source_id] = row
    evidence = [row.visible_evidence() for row in latest_by_source.values()]
    spot = derive_spot_structure_evidence(archive, decision_at=decision, max_spot_age_seconds=max_spot_age_seconds)
    if spot is not None:
        evidence.append(spot)
    return sorted(evidence, key=lambda row: _utc(row.observed_at), reverse=True)


def visible_option_contracts_at(archive: BtcHistoricalArchive, *, decision_at: datetime) -> list[BtcOptionContractSnapshot]:
    decision = _utc(decision_at)
    latest: dict[str, BtcOptionContractArchiveRow] = {}
    for row in archive.option_contract_rows:
        row.validated()
        if not row.provenance.point_in_time_proven or _utc(row.available_at) > decision:
            continue
        symbol = str(row.snapshot.symbol)
        if symbol not in latest or _utc(row.available_at) > _utc(latest[symbol].available_at):
            latest[symbol] = row
    return [latest[symbol].visible_snapshot() for symbol in sorted(latest)]


def latest_execution_metadata_at(archive: BtcHistoricalArchive, *, decision_at: datetime) -> BtcOptionsExecutionArchiveRow | None:
    decision = _utc(decision_at)
    rows = [row for row in archive.execution_rows if row.provenance.point_in_time_proven and _utc(row.available_at) <= decision]
    return None if not rows else max(rows, key=lambda row: _utc(row.available_at)).validated()


def structural_spot_window(archive: BtcHistoricalArchive, *, decision_at: datetime, lookback_hours: float) -> list[BtcSpotCandleArchiveRow]:
    _positive("lookback_hours", lookback_hours)
    decision, start = _utc(decision_at), _utc(decision_at) - timedelta(hours=float(lookback_hours))
    return [row for row in _visible_spot(archive, decision) if start < _utc(row.available_at) <= decision]


def forward_btc_prices(archive: BtcHistoricalArchive, *, decision_at: datetime, horizon_hours: float) -> list[BtcForwardPriceObservation]:
    _positive("horizon_hours", horizon_hours)
    decision, end = _utc(decision_at), _utc(decision_at) + timedelta(hours=float(horizon_hours))
    rows = sorted(
        [row for row in archive.spot_candles if row.provenance.point_in_time_proven and decision < _utc(row.available_at) <= end],
        key=lambda row: _utc(row.available_at),
    )
    return [BtcForwardPriceObservation(observed_at=_utc(row.available_at), btc_price=float(row.close)) for row in rows]


def option_replay_observations(
    archive: BtcHistoricalArchive, *, symbol: str, decision_at: datetime,
    horizon_hours: float, extra_quote_delay_seconds: int = 0,
) -> list[BtcOptionsReplayObservation]:
    """Union BTC close events with actual quote events without future backfill."""
    if not str(symbol or "").strip():
        raise ValueError("symbol is required")
    _positive("horizon_hours", horizon_hours)
    if int(extra_quote_delay_seconds) < 0:
        raise ValueError("extra_quote_delay_seconds must be >= 0")
    decision = _utc(decision_at)
    end = decision + timedelta(hours=float(horizon_hours), seconds=int(extra_quote_delay_seconds))
    future_spot = [row for row in archive.spot_candles if row.provenance.point_in_time_proven and decision < _utc(row.available_at) <= end]
    future_quotes = [row for row in archive.option_quote_rows if row.provenance.point_in_time_proven and row.symbol == symbol and decision < _utc(row.available_at) <= end]
    for row in future_quotes:
        row.validated()
    event_times = sorted({_utc(row.available_at) for row in future_spot} | {_utc(row.available_at) for row in future_quotes})
    all_spot = sorted([row for row in archive.spot_candles if row.provenance.point_in_time_proven and _utc(row.available_at) <= end], key=lambda row: _utc(row.available_at))
    quote_at = {_utc(row.available_at): row for row in future_quotes}
    result = []
    for event_at in event_times:
        visible = [row for row in all_spot if _utc(row.available_at) <= event_at]
        if not visible:
            continue
        quote = quote_at.get(event_at)
        result.append(BtcOptionsReplayObservation(
            observed_at=event_at, btc_price=float(visible[-1].close),
            option_bid=None if quote is None else quote.bid,
            option_ask=None if quote is None else quote.ask,
            source="BTC_HISTORICAL_ARCHIVE",
        ))
    return result


def source_coverage_at(archive: BtcHistoricalArchive, *, decision_at: datetime, max_spot_age_seconds: int) -> dict:
    evidence = visible_evidence_at(archive, decision_at=decision_at, max_spot_age_seconds=max_spot_age_seconds)
    families = {row.family for row in evidence}
    unproven = sum(
        1 for rows in (archive.spot_candles, archive.evidence_rows, archive.option_contract_rows, archive.option_quote_rows, archive.execution_rows)
        for row in rows if not row.provenance.point_in_time_proven
    )
    return {
        "version": "BTC_HISTORICAL_SOURCE_COVERAGE_V1",
        "decision_at": _utc(decision_at).isoformat(),
        "spot_structure": "BTC_SPOT_STRUCTURE" in families,
        "derivatives": "DERIVATIVES_POSITIONING" in families,
        "options_market": "BTC_OPTIONS_MARKET" in families,
        "onchain": bool(families & {"ONCHAIN_FLOW", "ONCHAIN_METRIC", "TOKEN_UNLOCK"}),
        "stablecoins": "STABLECOIN_LIQUIDITY" in families,
        "macro": "BTC_MACRO_CROSS_ASSET" in families,
        "news": "CRYPTO_NEWS" in families,
        "social": "CRYPTO_SOCIAL_NARRATIVE" in families,
        "memory": "BTC_HISTORICAL_ANALOGUE" in families,
        "option_contract_snapshot_count": len(visible_option_contracts_at(archive, decision_at=decision_at)),
        "execution_metadata_available": latest_execution_metadata_at(archive, decision_at=decision_at) is not None,
        "point_in_time_unproven_row_count": unproven,
        "unproven_rows_may_influence_decision": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_HISTORICAL_DATA_ADAPTER_CONTRACT_V1",
        "research_only": True,
        "coindcx_candle_open_time_is_visibility_time": False,
        "completed_candles_only": True,
        "future_rows_may_influence_click": False,
        "point_in_time_unproven_rows_may_influence_click": False,
        "historical_options_may_be_fabricated": False,
        "historical_options_require_proven_archive": True,
        "later_option_quote_may_backfill_earlier_trigger": False,
        "options_and_futures_trade_generation_separate": True,
        "futures_trade_generated": False,
        "broker_execution_enabled": False,
    }
