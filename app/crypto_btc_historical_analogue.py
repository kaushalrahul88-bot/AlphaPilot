"""Point-in-time historical analogue memory for the BTC Crypto Brain.

The analogue engine compares the current completed 1h BTC structure with older
completed 1h states whose forward outcome was already fully observable before the
current decision time. It is deliberately Experience/Memory context only: even a
strong historical match cannot create the current BTC direction or a trade.

No future candle relative to ``decision_at`` is admitted. A candidate analogue is
eligible only when both its feature history and its complete forward outcome were
available by the current decision time. Missing hourly bars fail closed rather
than allowing a stale close to masquerade as the requested anchor or outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import median

from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow
from app.crypto_btc_perception import BtcHistoricalAnalogue, historical_analogue_context
from app.crypto_market_intelligence import Evidence


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class BtcHistoricalAnaloguePolicy:
    lookback_hours: int = 24 * 14
    outcome_horizon_hours: int = 1
    min_candidate_spacing_hours: int = 4
    max_analogues: int = 20
    min_analogues: int = 12
    neutral_outcome_band_pct: float = 0.10

    def validated(self) -> "BtcHistoricalAnaloguePolicy":
        if int(self.lookback_hours) < 48:
            raise ValueError("lookback_hours must be >= 48")
        if int(self.outcome_horizon_hours) < 1:
            raise ValueError("outcome_horizon_hours must be >= 1")
        if int(self.min_candidate_spacing_hours) < 1:
            raise ValueError("min_candidate_spacing_hours must be >= 1")
        if int(self.max_analogues) < 1:
            raise ValueError("max_analogues must be >= 1")
        if not 1 <= int(self.min_analogues) <= int(self.max_analogues):
            raise ValueError("min_analogues must be between 1 and max_analogues")
        band = float(self.neutral_outcome_band_pct)
        if not isfinite(band) or band < 0:
            raise ValueError("neutral_outcome_band_pct must be finite and >= 0")
        return self


def _visible_rows(
    rows: list[BtcSpotCandleArchiveRow],
    *,
    decision_at: datetime,
    lookback_hours: int,
) -> list[BtcSpotCandleArchiveRow]:
    decision = _utc(decision_at)
    start = decision - timedelta(hours=int(lookback_hours) + 30)
    visible = [
        row.validated()
        for row in rows
        if row.provenance.point_in_time_proven
        and start <= _utc(row.available_at) <= decision
    ]
    visible.sort(key=lambda row: _utc(row.available_at))
    return visible


def _close_exactly_at(rows: list[BtcSpotCandleArchiveRow], target: datetime) -> float | None:
    """Return the close for the requested completed-hour timestamp only.

    Historical analogue features/outcomes must not silently fall back to an older
    bar when the requested hour is missing. CoinDCX-normalized 1h candles use the
    completed bar timestamp as ``available_at``, so exact UTC equality is the
    correct continuity gate here.
    """
    target = _utc(target)
    for row in rows:
        at = _utc(row.available_at)
        if at == target:
            return float(row.close)
        if at > target:
            break
    return None


def _feature_vector(rows: list[BtcSpotCandleArchiveRow], at: datetime) -> tuple[float, float, float] | None:
    at = _utc(at)
    current = _close_exactly_at(rows, at)
    if current is None or current <= 0:
        return None
    anchors = [_close_exactly_at(rows, at - timedelta(hours=hours)) for hours in (1, 4, 24)]
    if any(value is None or value <= 0 for value in anchors):
        return None
    return tuple((current - float(anchor)) / float(anchor) * 100.0 for anchor in anchors)  # type: ignore[arg-type]


def _distance(current: tuple[float, float, float], candidate: tuple[float, float, float]) -> float:
    # Fixed horizon scales keep similarity deterministic and prevent a volatile
    # sample from redefining its own distance metric after the click.
    scales = (0.75, 1.75, 4.0)
    return sum(abs(a - b) / scale for a, b, scale in zip(current, candidate, scales, strict=True)) / 3.0


def _similarity(distance: float) -> float:
    # Smooth bounded transform: exact match -> 1, increasingly different states
    # asymptotically approach 0 without introducing fitted parameters.
    return 1.0 / (1.0 + max(0.0, float(distance)))


def derive_btc_historical_analogue_evidence(
    rows: list[BtcSpotCandleArchiveRow],
    *,
    decision_at: datetime,
    policy: BtcHistoricalAnaloguePolicy | None = None,
) -> Evidence | None:
    """Build historical-memory evidence using only outcomes known by decision_at."""
    policy = (policy or BtcHistoricalAnaloguePolicy()).validated()
    decision = _utc(decision_at)
    visible = _visible_rows(rows, decision_at=decision, lookback_hours=policy.lookback_hours)
    if not visible:
        return None

    latest = visible[-1]
    current_at = _utc(latest.available_at)
    current_features = _feature_vector(visible, current_at)
    if current_features is None:
        return None

    earliest_candidate_at = max(
        _utc(visible[0].available_at) + timedelta(hours=24),
        decision - timedelta(hours=int(policy.lookback_hours)),
    )
    latest_candidate_at = decision - timedelta(hours=int(policy.outcome_horizon_hours))
    if latest_candidate_at <= earliest_candidate_at:
        return None

    candidates: list[dict] = []
    last_candidate_at: datetime | None = None
    for row in visible:
        candidate_at = _utc(row.available_at)
        if candidate_at < earliest_candidate_at or candidate_at > latest_candidate_at:
            continue
        if last_candidate_at is not None and (
            candidate_at - last_candidate_at
        ) < timedelta(hours=int(policy.min_candidate_spacing_hours)):
            continue

        features = _feature_vector(visible, candidate_at)
        outcome_available_at = candidate_at + timedelta(hours=int(policy.outcome_horizon_hours))
        outcome_close = _close_exactly_at(visible, outcome_available_at)
        if features is None or outcome_close is None or float(row.close) <= 0:
            continue
        if outcome_available_at > decision:
            continue

        forward_return_pct = (float(outcome_close) - float(row.close)) / float(row.close) * 100.0
        distance = _distance(current_features, features)
        candidates.append({
            "candidate_at": candidate_at,
            "outcome_available_at": outcome_available_at,
            "features": features,
            "forward_return_pct": forward_return_pct,
            "distance": distance,
            "similarity": _similarity(distance),
        })
        last_candidate_at = candidate_at

    if len(candidates) < int(policy.min_analogues):
        return None

    candidates.sort(key=lambda item: (float(item["distance"]), item["candidate_at"]))
    selected = candidates[: int(policy.max_analogues)]
    returns = [float(item["forward_return_pct"]) for item in selected]
    similarities = [float(item["similarity"]) for item in selected]
    band = float(policy.neutral_outcome_band_pct)
    bullish = sum(value > band for value in returns)
    bearish = sum(value < -band for value in returns)
    neutral = len(returns) - bullish - bearish
    bullish_fraction = bullish / len(returns)
    bearish_fraction = bearish / len(returns)
    mean_similarity = sum(similarities) / len(similarities)

    base = historical_analogue_context(BtcHistoricalAnalogue(
        observed_at=current_at,
        analogue_count=len(selected),
        bullish_fraction=bullish_fraction,
        similarity=mean_similarity,
        source="COINDCX_COMPLETED_SPOT_HISTORICAL_ANALOGUES",
    ))
    metadata = dict(base.metadata)
    metadata.update({
        "policy_lookback_hours": int(policy.lookback_hours),
        "outcome_horizon_hours": int(policy.outcome_horizon_hours),
        "min_candidate_spacing_hours": int(policy.min_candidate_spacing_hours),
        "neutral_outcome_band_pct": band,
        "bearish_fraction": bearish_fraction,
        "neutral_fraction": neutral / len(returns),
        "median_forward_return_pct": median(returns),
        "nearest_similarity": max(similarities),
        "mean_similarity": mean_similarity,
        "current_features": {
            "return_1h_pct": current_features[0],
            "return_4h_pct": current_features[1],
            "return_24h_pct": current_features[2],
        },
        "selected_analogue_times": [item["candidate_at"].isoformat() for item in selected],
        "latest_selected_outcome_available_at": max(item["outcome_available_at"] for item in selected).isoformat(),
        "all_selected_outcomes_known_by_decision": all(item["outcome_available_at"] <= decision for item in selected),
        "missing_hour_may_be_substituted_by_stale_close": False,
        "current_decision_at": decision.isoformat(),
        "direction_creator": False,
        "trade_generated": False,
    })
    return replace(base, metadata=metadata)


def architecture_contract() -> dict:
    return {
        "version": "BTC_HISTORICAL_ANALOGUE_MEMORY_V2",
        "source": "COINDCX_COMPLETED_SPOT_CANDLES",
        "candidate_features_use_only_completed_candles": True,
        "candidate_outcome_must_be_known_by_current_decision": True,
        "future_relative_to_current_decision_allowed": False,
        "missing_hour_may_be_substituted_by_stale_close": False,
        "minimum_candidate_spacing_prevents_dense_overlap": True,
        "fitted_model_used": False,
        "historical_memory_may_create_current_direction": False,
        "context_only": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "research_only": True,
    }
