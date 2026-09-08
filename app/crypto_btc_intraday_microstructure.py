"""Point-in-time BTC intraday timing layer for 15-minute direction research.

This module derives fresh microstructure from completed CoinDCX 1-minute spot
candles. It is deliberately part of the same ``SPOT_PRICE_STRUCTURE`` causal
origin as the broader 1h/4h/24h regime. The timing layer can confirm or veto a
broad spot thesis, but it can never become an independent second confirmation.

The rules are fixed before the V2 replay and use no future outcome prices. This
is research/shadow infrastructure only; it creates no Options/Futures trade and
commits no capital.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Iterable

from app.crypto_btc_historical_data_adapter import BtcSpotCandleArchiveRow
from app.crypto_market_intelligence import Evidence

UTC = timezone.utc
SPOT_CAUSAL_ORIGIN = "SPOT_PRICE_STRUCTURE"
MICRO_FAMILY = "BTC_INTRADAY_MICROSTRUCTURE"
RECONCILED_FAMILY = "BTC_SPOT_STRUCTURE"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class BtcIntradayMicrostructurePolicy:
    """Predeclared structural rules for the V2 15-minute timing experiment."""

    max_latest_age_seconds: int = 120
    minimum_window_coverage: float = 0.80
    bullish_close_location: float = 0.65
    bearish_close_location: float = 0.35
    expansion_ratio: float = 1.15
    compression_ratio: float = 0.85
    rejection_wick_fraction: float = 0.35
    minimum_direction_score: int = 4
    healthy_volume_ratio: float = 0.80
    strong_volume_ratio: float = 1.10

    def validated(self) -> "BtcIntradayMicrostructurePolicy":
        if int(self.max_latest_age_seconds) < 0:
            raise ValueError("max_latest_age_seconds must be >= 0")
        if not 0 < float(self.minimum_window_coverage) <= 1:
            raise ValueError("minimum_window_coverage must be in (0, 1]")
        if not 0 <= float(self.bearish_close_location) < float(self.bullish_close_location) <= 1:
            raise ValueError("close-location thresholds are invalid")
        if not 0 < float(self.compression_ratio) < 1 < float(self.expansion_ratio):
            raise ValueError("range-state thresholds are invalid")
        if not 0 < float(self.rejection_wick_fraction) < 1:
            raise ValueError("rejection_wick_fraction must be in (0, 1)")
        if int(self.minimum_direction_score) < 1:
            raise ValueError("minimum_direction_score must be >= 1")
        if not 0 < float(self.healthy_volume_ratio) <= float(self.strong_volume_ratio):
            raise ValueError("volume thresholds are invalid")
        return self


def _visible_completed(
    rows: Iterable[BtcSpotCandleArchiveRow],
    *,
    decision_at: datetime,
) -> list[BtcSpotCandleArchiveRow]:
    decision = _utc(decision_at)
    visible: list[BtcSpotCandleArchiveRow] = []
    for row in rows:
        row.validated()
        if row.provenance.point_in_time_proven and _utc(row.available_at) <= decision:
            visible.append(row)
    return sorted(visible, key=lambda row: _utc(row.available_at))


def _window(
    rows: list[BtcSpotCandleArchiveRow],
    *,
    end_at: datetime,
    minutes: int,
) -> list[BtcSpotCandleArchiveRow]:
    end = _utc(end_at)
    start = end - timedelta(minutes=int(minutes))
    return [row for row in rows if start < _utc(row.available_at) <= end]


def _anchor(
    rows: list[BtcSpotCandleArchiveRow],
    *,
    at_or_before: datetime,
) -> BtcSpotCandleArchiveRow | None:
    target = _utc(at_or_before)
    eligible = [row for row in rows if _utc(row.available_at) <= target]
    return eligible[-1] if eligible else None


def _return_pct(latest: float, anchor: float) -> float:
    if not isfinite(float(anchor)) or float(anchor) <= 0:
        raise ValueError("return anchor must be finite and > 0")
    return (float(latest) - float(anchor)) / float(anchor) * 100.0


def _range(rows: list[BtcSpotCandleArchiveRow]) -> tuple[float, float, float]:
    high = max(float(row.high) for row in rows)
    low = min(float(row.low) for row in rows)
    return high, low, max(0.0, high - low)


def _sum_volume(rows: list[BtcSpotCandleArchiveRow]) -> float:
    return sum(float(row.volume) for row in rows)


def derive_btc_intraday_microstructure_evidence(
    rows: Iterable[BtcSpotCandleArchiveRow],
    *,
    decision_at: datetime,
    policy: BtcIntradayMicrostructurePolicy | None = None,
) -> Evidence | None:
    """Derive an outcome-blind 5m/15m/30m timing state from completed 1m bars."""

    cfg = (policy or BtcIntradayMicrostructurePolicy()).validated()
    decision = _utc(decision_at)
    visible = _visible_completed(rows, decision_at=decision)
    if not visible:
        return None
    latest = visible[-1]
    latest_age = (decision - _utc(latest.available_at)).total_seconds()
    if latest_age < 0 or latest_age > int(cfg.max_latest_age_seconds):
        return None

    anchors = {
        minutes: _anchor(visible, at_or_before=decision - timedelta(minutes=minutes))
        for minutes in (5, 10, 15, 30)
    }
    if any(anchor is None for anchor in anchors.values()):
        return None

    recent_5 = _window(visible, end_at=decision, minutes=5)
    recent_15 = _window(visible, end_at=decision, minutes=15)
    prior_15 = [
        row for row in visible
        if decision - timedelta(minutes=30) < _utc(row.available_at) <= decision - timedelta(minutes=15)
    ]
    prior_volume_15 = [
        row for row in visible
        if decision - timedelta(minutes=20) < _utc(row.available_at) <= decision - timedelta(minutes=5)
    ]
    if not recent_5 or not recent_15 or not prior_15 or not prior_volume_15:
        return None

    recent_coverage = len(recent_15) / 15.0
    prior_coverage = len(prior_15) / 15.0
    if min(recent_coverage, prior_coverage) < float(cfg.minimum_window_coverage):
        return None

    price = float(latest.close)
    return_5m = _return_pct(price, float(anchors[5].close))
    return_15m = _return_pct(price, float(anchors[15].close))
    return_30m = _return_pct(price, float(anchors[30].close))
    prior_5m_return = _return_pct(float(anchors[5].close), float(anchors[10].close))
    acceleration_5m = return_5m - prior_5m_return

    recent_high, recent_low, recent_range = _range(recent_15)
    prior_high, prior_low, prior_range = _range(prior_15)
    close_location = 0.5 if recent_range <= 0 else _bounded((price - recent_low) / recent_range)
    range_ratio = None if prior_range <= 0 else recent_range / prior_range
    if range_ratio is None:
        range_state = "UNKNOWN"
    elif range_ratio >= float(cfg.expansion_ratio):
        range_state = "EXPANDING"
    elif range_ratio <= float(cfg.compression_ratio):
        range_state = "COMPRESSING"
    else:
        range_state = "NORMAL"

    higher_high = recent_high > prior_high
    higher_low = recent_low > prior_low
    lower_high = recent_high < prior_high
    lower_low = recent_low < prior_low
    if higher_high and higher_low:
        structure_state = "HIGHER_HIGH_HIGHER_LOW"
    elif lower_high and lower_low:
        structure_state = "LOWER_HIGH_LOWER_LOW"
    else:
        structure_state = "MIXED"

    breakout_state = "NONE"
    if price > prior_high:
        breakout_state = "UPSIDE_BREAK"
    elif price < prior_low:
        breakout_state = "DOWNSIDE_BREAK"

    five_open = float(recent_5[0].open)
    five_high = max(float(row.high) for row in recent_5)
    five_low = min(float(row.low) for row in recent_5)
    five_range = max(0.0, five_high - five_low)
    upper_wick_fraction = 0.0
    lower_wick_fraction = 0.0
    if five_range > 0:
        upper_wick_fraction = max(0.0, five_high - max(five_open, price)) / five_range
        lower_wick_fraction = max(0.0, min(five_open, price) - five_low) / five_range
    rejection_state = "NONE"
    if (
        lower_wick_fraction >= float(cfg.rejection_wick_fraction)
        and price >= five_open
    ):
        rejection_state = "LOWER_REJECTION"
    elif (
        upper_wick_fraction >= float(cfg.rejection_wick_fraction)
        and price <= five_open
    ):
        rejection_state = "UPPER_REJECTION"

    recent_5_volume = _sum_volume(recent_5)
    prior_15_volume = _sum_volume(prior_volume_15)
    baseline_5_volume = prior_15_volume / 3.0 if prior_15_volume > 0 else 0.0
    volume_ratio = None if baseline_5_volume <= 0 else recent_5_volume / baseline_5_volume
    if volume_ratio is None:
        volume_state = "UNKNOWN"
    elif volume_ratio >= float(cfg.strong_volume_ratio):
        volume_state = "STRONG"
    elif volume_ratio >= float(cfg.healthy_volume_ratio):
        volume_state = "HEALTHY"
    else:
        volume_state = "WEAK"

    distance_to_resistance_bps = (prior_high - price) / price * 10000.0
    distance_to_support_bps = (price - prior_low) / price * 10000.0

    votes: dict[str, int] = {
        "return_5m": 1 if return_5m > 0 else -1 if return_5m < 0 else 0,
        "return_15m": 1 if return_15m > 0 else -1 if return_15m < 0 else 0,
        "return_30m": 1 if return_30m > 0 else -1 if return_30m < 0 else 0,
        "acceleration_5m": 1 if acceleration_5m > 0 else -1 if acceleration_5m < 0 else 0,
        "structure": 1 if structure_state == "HIGHER_HIGH_HIGHER_LOW" else -1 if structure_state == "LOWER_HIGH_LOWER_LOW" else 0,
        "close_location": 1 if close_location >= float(cfg.bullish_close_location) else -1 if close_location <= float(cfg.bearish_close_location) else 0,
        "breakout": 2 if breakout_state == "UPSIDE_BREAK" else -2 if breakout_state == "DOWNSIDE_BREAK" else 0,
        "rejection": 1 if rejection_state == "LOWER_REJECTION" else -1 if rejection_state == "UPPER_REJECTION" else 0,
    }
    score = sum(votes.values())
    bullish_base = return_5m > 0 and return_15m > 0
    bearish_base = return_5m < 0 and return_15m < 0

    stance = "UNKNOWN"
    if bullish_base and score >= int(cfg.minimum_direction_score):
        stance = "BULLISH"
    elif bearish_base and score <= -int(cfg.minimum_direction_score):
        stance = "BEARISH"

    directional = stance in {"BULLISH", "BEARISH"}
    breakout_aligned = (
        stance == "BULLISH" and breakout_state == "UPSIDE_BREAK"
    ) or (
        stance == "BEARISH" and breakout_state == "DOWNSIDE_BREAK"
    )
    strong_participation = volume_ratio is not None and volume_ratio >= float(cfg.strong_volume_ratio)
    healthy_participation = volume_ratio is not None and volume_ratio >= float(cfg.healthy_volume_ratio)
    if directional and abs(score) >= 6 and strong_participation and (range_state == "EXPANDING" or breakout_aligned):
        strength = "HIGH"
        confidence = 0.82
    elif directional and healthy_participation:
        strength = "MEDIUM"
        confidence = 0.72
    elif directional:
        strength = "LOW"
        confidence = 0.62
    else:
        strength = "LOW"
        confidence = 0.50

    if stance == "BULLISH":
        reason = "Completed 1m BTC bars show aligned positive 5m/15m timing with a bullish majority across momentum, structure, location and micro support/resistance features."
    elif stance == "BEARISH":
        reason = "Completed 1m BTC bars show aligned negative 5m/15m timing with a bearish majority across momentum, structure, location and micro support/resistance features."
    else:
        reason = "BTC intraday timing is mixed or lacks the predeclared majority required to confirm a 15-minute direction."

    return Evidence(
        family=MICRO_FAMILY,
        causal_origin=SPOT_CAUSAL_ORIGIN,
        stance=stance,
        strength=strength,
        confidence=confidence,
        observed_at=_utc(latest.available_at),
        reason=reason,
        context_only=not directional,
        source="COINDCX_PUBLIC_SPOT_CANDLES",
        metadata={
            "version": "BTC_INTRADAY_MICROSTRUCTURE_V2",
            "decision_at": decision.isoformat(),
            "latest_available_at": _utc(latest.available_at).isoformat(),
            "latest_age_seconds": latest_age,
            "price": price,
            "return_5m_pct": return_5m,
            "return_15m_pct": return_15m,
            "return_30m_pct": return_30m,
            "prior_5m_return_pct": prior_5m_return,
            "acceleration_5m_pct_points": acceleration_5m,
            "recent_15m_high": recent_high,
            "recent_15m_low": recent_low,
            "prior_15m_high": prior_high,
            "prior_15m_low": prior_low,
            "close_location_15m": close_location,
            "structure_state": structure_state,
            "range_ratio_15m_vs_prior15m": range_ratio,
            "range_state": range_state,
            "breakout_state": breakout_state,
            "upper_wick_fraction_5m": upper_wick_fraction,
            "lower_wick_fraction_5m": lower_wick_fraction,
            "rejection_state": rejection_state,
            "recent_5m_volume": recent_5_volume,
            "prior_15m_volume": prior_15_volume,
            "volume_ratio_5m_vs_prior5m_average": volume_ratio,
            "volume_state": volume_state,
            "distance_to_prior_resistance_bps": distance_to_resistance_bps,
            "distance_to_prior_support_bps": distance_to_support_bps,
            "recent_15m_coverage": recent_coverage,
            "prior_15m_coverage": prior_coverage,
            "vote_score": score,
            "votes": votes,
            "minimum_direction_score": int(cfg.minimum_direction_score),
            "outcome_data_used": False,
            "completed_1m_candles_only": True,
            "same_causal_origin_as_broad_spot_regime": True,
            "independent_confirmation": False,
            "may_generate_options_trade": False,
            "may_generate_futures_trade": False,
        },
    )


def reconcile_spot_regime_with_intraday_timing(
    broad_regime: Evidence | None,
    intraday_timing: Evidence | None,
    *,
    decision_at: datetime,
) -> Evidence | None:
    """Return one spot-origin row after requiring fresh timing confirmation.

    V2 intentionally does not submit both regime and timing as separate evidence
    rows. They describe the same price/volume phenomenon, so they are collapsed
    into exactly one causal origin. A fresh timing disagreement or an
    unconfirmed timing state vetoes a stale broad-regime direction for the 15m
    research question.
    """

    if broad_regime is None:
        return None
    if str(broad_regime.causal_origin) != SPOT_CAUSAL_ORIGIN:
        raise ValueError("broad_regime must belong to SPOT_PRICE_STRUCTURE")
    if intraday_timing is not None and str(intraday_timing.causal_origin) != SPOT_CAUSAL_ORIGIN:
        raise ValueError("intraday_timing must belong to SPOT_PRICE_STRUCTURE")

    regime_stance = str(broad_regime.stance).upper()
    timing_stance = "UNKNOWN" if intraday_timing is None else str(intraday_timing.stance).upper()
    aligned = (
        regime_stance in {"BULLISH", "BEARISH"}
        and timing_stance == regime_stance
    )
    stance = regime_stance if aligned else "UNKNOWN"
    context_only = not aligned

    if aligned:
        rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        reverse = {0: "LOW", 1: "MEDIUM", 2: "HIGH"}
        strength = reverse[min(
            rank.get(str(broad_regime.strength).upper(), 0),
            rank.get(str(intraday_timing.strength).upper(), 0),
        )]
        confidence = min(float(broad_regime.confidence), float(intraday_timing.confidence))
        reason = "BTC broad spot regime and fresh completed-1m intraday timing agree; they are reconciled into one SPOT_PRICE_STRUCTURE origin."
    else:
        strength = "LOW"
        confidence = 0.50
        if regime_stance in {"BULLISH", "BEARISH"} and timing_stance in {"BULLISH", "BEARISH"}:
            reason = "Fresh BTC intraday timing contradicts the broad spot regime, so the spot origin is vetoed for the 15-minute research question."
        elif regime_stance in {"BULLISH", "BEARISH"}:
            reason = "Broad BTC spot regime lacks fresh intraday timing confirmation, so the spot origin is neutral for the 15-minute research question."
        else:
            reason = "Broad BTC spot regime is not directional; intraday timing cannot manufacture a standalone second spot origin."

    observed_at = _utc(broad_regime.observed_at)
    if intraday_timing is not None:
        observed_at = max(observed_at, _utc(intraday_timing.observed_at))
    return Evidence(
        family=RECONCILED_FAMILY,
        causal_origin=SPOT_CAUSAL_ORIGIN,
        stance=stance,
        strength=strength,
        confidence=confidence,
        observed_at=observed_at,
        reason=reason,
        context_only=context_only,
        source="COINDCX_PUBLIC_SPOT_CANDLES",
        metadata={
            "version": "BTC_SPOT_REGIME_TIMING_RECONCILIATION_V2",
            "decision_at": _utc(decision_at).isoformat(),
            "broad_regime_stance": regime_stance,
            "broad_regime_family": broad_regime.family,
            "broad_regime_observed_at": _utc(broad_regime.observed_at).isoformat(),
            "broad_regime_metadata": dict(broad_regime.metadata or {}),
            "intraday_timing_available": intraday_timing is not None,
            "intraday_timing_stance": timing_stance,
            "intraday_timing_observed_at": None if intraday_timing is None else _utc(intraday_timing.observed_at).isoformat(),
            "intraday_timing_metadata": {} if intraday_timing is None else dict(intraday_timing.metadata or {}),
            "timing_confirmation_required_for_v2_15m": True,
            "same_causal_origin_reconciled": True,
            "independent_spot_origins_created": 1 if aligned else 0,
            "future_prices_used": False,
            "outcome_data_used": False,
            "production_two_origin_gate_changed": False,
            "may_generate_options_trade": False,
            "may_generate_futures_trade": False,
        },
    )


def architecture_contract() -> dict[str, Any]:
    return {
        "version": "BTC_INTRADAY_MICROSTRUCTURE_CONTRACT_V2",
        "input_interval": "1m",
        "completed_candles_only": True,
        "feature_windows_minutes": [5, 15, 30],
        "causal_origin": SPOT_CAUSAL_ORIGIN,
        "independent_confirmation_created": False,
        "fresh_timing_may_veto_broad_regime": True,
        "timing_without_broad_regime_may_create_direction": False,
        "outcome_data_used": False,
        "production_two_origin_gate_changed": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "capital_committed_inr": 0,
        "research_only": True,
    }
