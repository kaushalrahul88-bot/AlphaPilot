"""Explicit prospective BTC proof bridge using already-implemented data sources.

This module composes point-in-time-safe inputs when a caller explicitly invokes
it:

1. reconstruct completed CoinDCX BTC spot candles by bar-completion time;
2. read derivatives rows already visible in the immutable BTC PIT store;
3. optionally admit caller-supplied completed Delta India BTC OI history;
4. derive Spot Structure + one reconciled leveraged-positioning origin;
5. freeze a ``Prospective BTC Thesis Tape`` decision;
6. later reconstruct completed CoinDCX BTC candles through the frozen horizon and
   resolve the BTC-only outcome.

Missing positioning data remains UNKNOWN context. Multiple providers sharing the
same leveraged-positioning causal origin are never double-counted; disagreement
within that origin cancels it to UNKNOWN. No Options contract, premium, P&L,
Futures trade, order, or live capital is created.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import ceil, isfinite
from typing import Any

from app.coindcx_btc_public_provider import CoinDcxBtcPublicProvider, SPOT_INTERVALS
from app.crypto_btc_derivatives_evidence import derivatives_evidence_from_full_pit_context
from app.crypto_btc_historical_data_adapter import (
    BtcHistoricalArchive,
    BtcSpotCandleArchiveRow,
    derive_spot_structure_evidence,
)
from app.crypto_btc_prospective_thesis_tape import (
    ProspectiveBtcThesisTapePolicy,
    freeze_prospective_btc_thesis,
    resolve_prospective_btc_thesis,
)
from app.crypto_btc_random_click_experience import BtcForwardPriceObservation
from app.crypto_market_intelligence import Evidence
from app.delta_india_btc_derivatives_context import (
    DeltaIndiaBtcOiCandle,
    derive_delta_oi_positioning_evidence,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _finite(name: str, value: float, *, positive: bool = False) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{name} must be > 0")
    return number


@dataclass(frozen=True)
class ProspectiveBtcProofBridgePolicy:
    structure_lookback_hours: float = 30.0
    structure_interval: str = "1h"
    decision_price_interval: str = "1m"
    structure_max_age_seconds: int = 3700
    decision_price_max_age_seconds: int = 120
    derivatives_price_lookback_hours: float = 1.0
    max_event_misalignment_seconds: int = 60
    outcome_interval: str = "1m"

    def validated(self) -> "ProspectiveBtcProofBridgePolicy":
        _finite("structure_lookback_hours", self.structure_lookback_hours, positive=True)
        _finite("derivatives_price_lookback_hours", self.derivatives_price_lookback_hours, positive=True)
        if self.structure_interval != "1h":
            raise ValueError("V1 prospective proof bridge requires 1h structure candles")
        if self.decision_price_interval not in SPOT_INTERVALS:
            raise ValueError("unsupported decision_price_interval")
        if self.outcome_interval not in SPOT_INTERVALS:
            raise ValueError("unsupported outcome_interval")
        if int(self.structure_max_age_seconds) < 0:
            raise ValueError("structure_max_age_seconds must be >= 0")
        if int(self.decision_price_max_age_seconds) < 0:
            raise ValueError("decision_price_max_age_seconds must be >= 0")
        if int(self.max_event_misalignment_seconds) < 0:
            raise ValueError("max_event_misalignment_seconds must be >= 0")
        return self


def _visible_completed(rows: list[BtcSpotCandleArchiveRow], *, as_of: datetime) -> list[BtcSpotCandleArchiveRow]:
    cutoff = _utc(as_of)
    return sorted(
        [row.validated() for row in rows if _utc(row.available_at) <= cutoff],
        key=lambda row: _utc(row.available_at),
    )


def _latest_price(rows: list[BtcSpotCandleArchiveRow], *, as_of: datetime, max_age_seconds: int) -> BtcSpotCandleArchiveRow | None:
    visible = _visible_completed(rows, as_of=as_of)
    if not visible:
        return None
    latest = visible[-1]
    age = (_utc(as_of) - _utc(latest.available_at)).total_seconds()
    return latest if 0 <= age <= int(max_age_seconds) else None


def _price_change_pct(
    structure_rows: list[BtcSpotCandleArchiveRow],
    *,
    decision_at: datetime,
    decision_price: float,
    lookback_hours: float,
) -> float | None:
    target = _utc(decision_at) - timedelta(hours=float(lookback_hours))
    eligible = [row for row in _visible_completed(structure_rows, as_of=decision_at) if _utc(row.available_at) <= target]
    if not eligible:
        return None
    anchor = float(eligible[-1].close)
    if anchor <= 0:
        return None
    return (float(decision_price) - anchor) / anchor * 100.0


def _reconcile_positioning_evidence(*rows: Evidence | None) -> Evidence | None:
    """Collapse same-origin positioning providers into exactly one causal vote."""
    candidates = [row for row in rows if row is not None]
    if not candidates:
        return None
    for row in candidates:
        if row.family != "DERIVATIVES_POSITIONING" or row.causal_origin != "LEVERAGED_POSITIONING":
            raise ValueError("positioning reconciliation accepts LEVERAGED_POSITIONING evidence only")

    directional = [row for row in candidates if not row.context_only and row.stance in {"BULLISH", "BEARISH"}]
    stances = {row.stance for row in directional}
    if len(stances) > 1:
        observed_at = max((_utc(row.observed_at) for row in directional))
        return Evidence(
            family="DERIVATIVES_POSITIONING",
            causal_origin="LEVERAGED_POSITIONING",
            stance="UNKNOWN",
            strength="LOW",
            confidence=0.5,
            observed_at=observed_at,
            reason="Leveraged-positioning providers disagree on direction, so the shared causal origin is cancelled.",
            context_only=True,
            source="MULTI_PROVIDER_POSITIONING_RECONCILIATION",
            metadata={
                "provider_stances": {row.source: row.stance for row in directional},
                "double_counted": False,
                "conflict_cancels_origin": True,
                "may_generate_futures_trade": False,
            },
        )
    if directional:
        chosen = max(
            directional,
            key=lambda row: (float(row.confidence), _utc(row.observed_at), str(row.source)),
        )
        if len(directional) == 1:
            return chosen
        metadata = dict(chosen.metadata)
        metadata.update({
            "same_origin_corroborating_sources": sorted({row.source for row in directional}),
            "double_counted": False,
        })
        return replace(chosen, metadata=metadata)

    return max(candidates, key=lambda row: (_utc(row.observed_at), float(row.confidence), str(row.source)))


async def _visible_pit_rows(store: Any, *, decision_at: datetime) -> list[dict]:
    result = store.visible_as_of(_utc(decision_at))
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, list):
        raise ValueError("BTC PIT store visible_as_of must return a list")
    future = [row for row in result if row.get("first_seen_at") is not None and _utc(datetime.fromisoformat(str(row["first_seen_at"]).replace("Z", "+00:00"))) > _utc(decision_at)]
    if future:
        raise ValueError("BTC PIT store returned row first seen after decision_at")
    return result


async def freeze_prospective_btc_thesis_from_existing_sources(
    *,
    click_id: str,
    decision_at: datetime,
    provider: CoinDcxBtcPublicProvider,
    pit_store: Any,
    tape_policy: ProspectiveBtcThesisTapePolicy,
    bridge_policy: ProspectiveBtcProofBridgePolicy | None = None,
    delta_oi_rows: list[DeltaIndiaBtcOiCandle] | None = None,
) -> dict:
    """Explicitly reconstruct visible BTC inputs and freeze one proof decision."""
    bridge = (bridge_policy or ProspectiveBtcProofBridgePolicy()).validated()
    tape_policy.validated()
    decision = _utc(decision_at)

    structure_limit = min(1000, int(ceil(float(bridge.structure_lookback_hours))) + 8)
    structure_start = decision - timedelta(hours=float(bridge.structure_lookback_hours) + 2.0)
    decision_interval_seconds = int(SPOT_INTERVALS[bridge.decision_price_interval])
    decision_start = decision - timedelta(seconds=max(10 * decision_interval_seconds, 600))

    structure_task = asyncio.to_thread(
        provider.fetch_spot_candles,
        interval=bridge.structure_interval,
        start_at=structure_start,
        end_at=decision,
        limit=structure_limit,
    )
    price_task = asyncio.to_thread(
        provider.fetch_spot_candles,
        interval=bridge.decision_price_interval,
        start_at=decision_start,
        end_at=decision,
        limit=min(1000, 20),
    )
    structure_rows, price_rows, pit_rows = await asyncio.gather(
        structure_task,
        price_task,
        _visible_pit_rows(pit_store, decision_at=decision),
    )

    if not isinstance(structure_rows, list) or not isinstance(price_rows, list):
        raise ValueError("CoinDCX BTC provider must return candle lists")
    latest = _latest_price(
        price_rows,
        as_of=decision,
        max_age_seconds=int(bridge.decision_price_max_age_seconds),
    )
    if latest is None:
        return {
            "version": "BTC_PROSPECTIVE_PROOF_BRIDGE_V2",
            "status": "PROOF_INPUT_UNRESOLVED",
            "reason": "BTC_DECISION_PRICE_MISSING_OR_STALE",
            "decision_at": decision.isoformat(),
            "frozen_thesis": None,
            "provider_called": True,
            "pit_store_read": True,
            "trade_generated": False,
        }

    visible_structure = _visible_completed(structure_rows, as_of=decision)
    archive = BtcHistoricalArchive(spot_candles=tuple(visible_structure)).validated()
    spot_evidence = derive_spot_structure_evidence(
        archive,
        decision_at=decision,
        max_spot_age_seconds=int(bridge.structure_max_age_seconds),
    )
    if spot_evidence is None:
        return {
            "version": "BTC_PROSPECTIVE_PROOF_BRIDGE_V2",
            "status": "PROOF_INPUT_UNRESOLVED",
            "reason": "BTC_SPOT_STRUCTURE_UNAVAILABLE",
            "decision_at": decision.isoformat(),
            "decision_btc_price": float(latest.close),
            "frozen_thesis": None,
            "provider_called": True,
            "pit_store_read": True,
            "trade_generated": False,
        }

    price_change = _price_change_pct(
        visible_structure,
        decision_at=decision,
        decision_price=float(latest.close),
        lookback_hours=float(bridge.derivatives_price_lookback_hours),
    )
    pit_derivatives_evidence = None
    delta_oi_evidence = None
    if price_change is not None:
        pit_derivatives_evidence = derivatives_evidence_from_full_pit_context(
            pit_rows,
            decision_at=decision,
            price_change_pct=price_change,
            max_event_misalignment_seconds=int(bridge.max_event_misalignment_seconds),
        )
        if delta_oi_rows is not None:
            delta_oi_evidence = derive_delta_oi_positioning_evidence(
                delta_oi_rows,
                decision_at=decision,
                price_change_pct=price_change,
            )
    derivatives_evidence = _reconcile_positioning_evidence(
        pit_derivatives_evidence,
        delta_oi_evidence,
    )

    evidence = [spot_evidence]
    if derivatives_evidence is not None:
        evidence.append(derivatives_evidence)

    frozen = freeze_prospective_btc_thesis(
        click_id=click_id,
        decision_at=decision,
        btc_spot_price=float(latest.close),
        evidence=evidence,
        policy=tape_policy,
    )
    dataset_counts: dict[str, int] = {}
    for row in pit_rows:
        dataset = str(row.get("dataset") or "UNKNOWN")
        dataset_counts[dataset] = dataset_counts.get(dataset, 0) + 1
    return {
        "version": "BTC_PROSPECTIVE_PROOF_BRIDGE_V2",
        "status": "PROSPECTIVE_PROOF_DECISION_FROZEN",
        "decision_at": decision.isoformat(),
        "decision_btc_price": float(latest.close),
        "structure_candle_count": len(visible_structure),
        "pit_record_count": len(pit_rows),
        "pit_dataset_counts": dict(sorted(dataset_counts.items())),
        "delta_oi_candle_count": 0 if delta_oi_rows is None else len(delta_oi_rows),
        "derivatives_price_change_pct": price_change,
        "pit_derivatives_evidence_status": None if pit_derivatives_evidence is None else pit_derivatives_evidence.stance,
        "delta_oi_evidence_status": None if delta_oi_evidence is None else delta_oi_evidence.stance,
        "derivatives_evidence_status": None if derivatives_evidence is None else derivatives_evidence.stance,
        "frozen_thesis": frozen,
        "provider_called": True,
        "pit_store_read": True,
        "options_contract_data_used": False,
        "options_pnl_measured": False,
        "futures_trade_generated": False,
        "trade_generated": False,
    }


async def resolve_prospective_btc_thesis_from_coindcx(
    *,
    frozen_record: dict,
    resolution_at: datetime,
    provider: CoinDcxBtcPublicProvider,
    bridge_policy: ProspectiveBtcProofBridgePolicy | None = None,
) -> dict:
    """Resolve a due proof decision from completed CoinDCX BTC candles only."""
    bridge = (bridge_policy or ProspectiveBtcProofBridgePolicy()).validated()
    resolved_at = _utc(resolution_at)
    due_at = _utc(datetime.fromisoformat(str(frozen_record.get("outcome_due_at")).replace("Z", "+00:00")))
    decision_at = _utc(datetime.fromisoformat(str((frozen_record.get("decision") or {}).get("decision_at")).replace("Z", "+00:00")))

    if resolved_at < due_at:
        result = resolve_prospective_btc_thesis(
            frozen_record=frozen_record,
            resolution_at=resolved_at,
            forward_prices=[],
        )
        return {**result, "provider_called": False, "completed_btc_candle_count": 0}

    interval_seconds = int(SPOT_INTERVALS[bridge.outcome_interval])
    required_bars = int(ceil((due_at - decision_at).total_seconds() / interval_seconds)) + 3
    if required_bars > 1000:
        raise ValueError("outcome interval cannot cover frozen horizon within CoinDCX 1000-candle limit")
    rows = await asyncio.to_thread(
        provider.fetch_spot_candles,
        interval=bridge.outcome_interval,
        start_at=decision_at,
        end_at=due_at + timedelta(seconds=interval_seconds),
        limit=max(2, required_bars),
    )
    if not isinstance(rows, list):
        raise ValueError("CoinDCX BTC provider must return candle list")
    completed = [
        row.validated()
        for row in rows
        if decision_at < _utc(row.available_at) <= due_at and _utc(row.available_at) <= resolved_at
    ]
    completed.sort(key=lambda row: _utc(row.available_at))
    forward = [
        BtcForwardPriceObservation(observed_at=_utc(row.available_at), btc_price=float(row.close))
        for row in completed
    ]
    result = resolve_prospective_btc_thesis(
        frozen_record=frozen_record,
        resolution_at=resolved_at,
        forward_prices=forward,
    )
    return {
        **result,
        "provider_called": True,
        "completed_btc_candle_count": len(completed),
        "outcome_source": "COINDCX_PUBLIC_COMPLETED_SPOT_CANDLES",
        "options_pnl_measured": False,
        "futures_trade_generated": False,
        "trade_generated": False,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_PROSPECTIVE_PROOF_BRIDGE_CONTRACT_V2",
        "new_provider_added": False,
        "new_scheduler_added": False,
        "new_database_schema_added": False,
        "automatic_startup_added": False,
        "explicit_invocation_required": True,
        "spot_source": "COINDCX_PUBLIC_COMPLETED_CANDLES",
        "derivatives_source": "EXISTING_IMMUTABLE_BTC_PIT_STORE_PLUS_OPTIONAL_COMPLETED_DELTA_OI",
        "optional_completed_delta_oi_context": True,
        "same_positioning_origin_double_counted": False,
        "same_origin_provider_conflict_cancels_direction": True,
        "derivatives_missing_equals_neutral_vote": False,
        "oi_liquidations_may_be_fabricated": False,
        "decision_uses_only_pit_visible_rows": True,
        "outcome_uses_only_completed_coindcx_candles": True,
        "uses_existing_prospective_thesis_tape": True,
        "options_contract_data_required": False,
        "options_pnl_measured": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "research_only": True,
    }
