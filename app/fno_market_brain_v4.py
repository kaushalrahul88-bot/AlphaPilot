"""F&O Market Brain V4: semantically corrected two-stage underlying brain.

V3 is intentionally left frozen.  V4 fixes two semantic defects found during the
fresh current-expiry diagnostic review without fitting any threshold to outcomes:

1. The legacy technical engine's ``alpha_score`` is a 0-100 style score centered
   around 50 (with 42/58 already used as bearish/bullish strength boundaries).
   V3 incorrectly treated raw alpha as a signed score around zero.  V4 centers it
   at 50 and maps 42 -> -1, 50 -> 0, 58 -> +1 before using it.
2. V3's conflict margin was mathematically identical to ``abs(signed_score)``.
   V4 uses a separate weighted dominance ratio.  A 0.20 minimum means the
   dominant side must hold at least a 60/40 split of signed evidence.

The module is outcome-blind.  It consumes only completed-candle technical
snapshots and optional point-in-time context.  It never reads options, futures,
future bars, resolved outcomes, or benchmark performance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

PROTOCOL_ID = "FNO_MARKET_BRAIN_V4_2026-09-07"
TIMEFRAME_WEIGHTS = {"5m": 1.0, "15m": 1.35, "1h": 1.65}

# These are architecture constants, not values selected from V1/V2/V3 outcomes.
# The alpha scale is inherited from the existing engine semantics: 42/50/58.
ALPHA_NEUTRAL = 50.0
ALPHA_FULL_SCALE_DISTANCE = 8.0
EXPANSION_READY_SCORE = 3.0
DIRECTION_ACTION_SCORE = 2.8
DIRECTION_DOMINANCE_MIN = 0.20  # == at least a 60/40 weighted evidence split.


@dataclass(frozen=True)
class Evidence:
    name: str
    value: float
    weight: float
    contribution: float
    available: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 6),
            "weight": round(self.weight, 6),
            "contribution": round(self.contribution, 6),
            "available": self.available,
        }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _clip(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _sign(value: Any, deadband: float = 0.0) -> int:
    number = _finite(value)
    if number is None or abs(number) <= deadband:
        return 0
    return 1 if number > 0 else -1


def _alpha_signal(value: Any) -> float | None:
    """Map the legacy alpha scale to a signed, dimensionless direction signal.

    Existing engine semantics already interpret 58+ as bullish strength and
    42- as bearish strength.  This mapping preserves those semantics exactly at
    full scale while keeping 50 neutral.
    """
    alpha = _finite(value)
    if alpha is None:
        return None
    return _clip((alpha - ALPHA_NEUTRAL) / ALPHA_FULL_SCALE_DISTANCE)


def _structure_side(value: Any) -> int:
    text = str(value or "").upper()
    bullish = ("BULL", "UPTREND", "HIGHER_HIGH", "HIGHER_LOW", "BREAKOUT_UP")
    bearish = ("BEAR", "DOWNTREND", "LOWER_LOW", "LOWER_HIGH", "BREAKDOWN")
    if any(token in text for token in bullish):
        return 1
    if any(token in text for token in bearish):
        return -1
    return 0


def _signal_side(value: Any) -> int:
    text = str(value or "").upper()
    if text in {"LONG", "STRONG_LONG", "WATCH_LONG"}:
        return 1
    if text in {"SHORT", "STRONG_SHORT", "WATCH_SHORT"}:
        return -1
    return 0


def _tf_payloads(technical: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    timeframes = technical.get("timeframes") or {}
    return {
        tf: payload
        for tf, payload in timeframes.items()
        if tf in TIMEFRAME_WEIGHTS and isinstance(payload, Mapping)
    }


def _bandwidth_atr(payload: Mapping[str, Any]) -> float | None:
    upper = _finite(payload.get("bollinger_upper"))
    lower = _finite(payload.get("bollinger_lower"))
    atr = _finite(payload.get("atr14"))
    if upper is None or lower is None or atr is None or atr <= 0:
        return None
    return max(0.0, (upper - lower) / atr)


def _atr_pct(payload: Mapping[str, Any]) -> float | None:
    atr = _finite(payload.get("atr14"))
    price = _finite(payload.get("price"))
    if atr is None or price is None or atr <= 0 or price <= 0:
        return None
    return 100.0 * atr / price


def _macd_atr(payload: Mapping[str, Any]) -> float | None:
    hist = _finite(payload.get("macd_hist"))
    atr = _finite(payload.get("atr14"))
    if hist is None or atr is None or atr <= 0:
        return None
    return hist / atr


def _dominance_ratio(positive: float, negative: float) -> float:
    """Return independent directional dominance in [0, 1].

    Unlike V3's margin, this is scale-free: 6 vs 4 -> 0.20, 3 vs 2 -> 0.20.
    This lets direction magnitude and evidence conflict be gated separately.
    """
    total = max(0.0, positive) + max(0.0, negative)
    if total <= 0:
        return 0.0
    return abs(max(0.0, positive) - max(0.0, negative)) / total


def _direction_aggregate(evidence: list[Evidence]) -> dict[str, float | str]:
    signed_score = sum(item.contribution for item in evidence)
    positive = sum(max(0.0, item.contribution) for item in evidence)
    negative = -sum(min(0.0, item.contribution) for item in evidence)
    dominance = _dominance_ratio(positive, negative)
    side = "LONG" if signed_score > 0 else "SHORT" if signed_score < 0 else "NEUTRAL"
    possible = sum(abs(item.weight) for item in evidence)
    strength = abs(signed_score) / possible if possible else 0.0
    # Reporting confidence combines magnitude and agreement; it is not a third
    # strategy gate and therefore cannot silently duplicate either gate.
    confidence = math.sqrt(max(0.0, min(1.0, strength)) * dominance)
    return {
        "signed_score": signed_score,
        "positive": positive,
        "negative": negative,
        "dominance": dominance,
        "side": side,
        "strength": strength,
        "confidence": confidence,
    }


def expansion_state(technical: Mapping[str, Any]) -> dict[str, Any]:
    """Estimate point-in-time expansion readiness using dimensionless evidence."""
    payloads = _tf_payloads(technical)
    evidence: list[Evidence] = []

    for tf, payload in payloads.items():
        weight = TIMEFRAME_WEIGHTS[tf]
        volume = _finite(payload.get("volume_ratio_raw"))
        width = _bandwidth_atr(payload)
        macd_norm = _macd_atr(payload)
        alpha_signal = _alpha_signal(payload.get("alpha_score"))
        d_res = _finite(payload.get("distance_to_resistance_atr"))
        d_sup = _finite(payload.get("distance_to_support_atr"))

        if volume is not None:
            value = _clip((volume - 1.0) / 0.75, 0.0, 1.0)
            weight_i = 0.85 * weight
            evidence.append(Evidence(f"{tf}:volume_expansion", value, weight_i, value * weight_i))

        if width is not None:
            # Readiness is highest around a moderate 3-ATR band width; both very
            # compressed and already-stretched states contribute less.
            value = _clip(1.0 - abs(width - 3.0) / 3.0, 0.0, 1.0)
            weight_i = 0.55 * weight
            evidence.append(Evidence(f"{tf}:volatility_state", value, weight_i, value * weight_i))

        if macd_norm is not None:
            value = _clip(abs(macd_norm) / 0.35, 0.0, 1.0)
            weight_i = 0.65 * weight
            evidence.append(Evidence(f"{tf}:momentum_impulse", value, weight_i, value * weight_i))

        if alpha_signal is not None:
            value = abs(alpha_signal)
            weight_i = 0.55 * weight
            evidence.append(Evidence(f"{tf}:technical_pressure", value, weight_i, value * weight_i))

        distances = [value for value in (d_res, d_sup) if value is not None and value >= 0]
        if distances:
            nearest = min(distances)
            value = _clip((0.8 - nearest) / 0.8, 0.0, 1.0)
            weight_i = 0.60 * weight
            evidence.append(Evidence(f"{tf}:structure_proximity", value, weight_i, value * weight_i))

    score = sum(item.contribution for item in evidence)
    possible = sum(item.weight for item in evidence)
    normalized = score / possible if possible else 0.0
    atr_by_tf = {tf: _atr_pct(payload) for tf, payload in payloads.items()}
    return {
        "score": round(score, 6),
        "normalized_score": round(normalized, 6),
        "ready": score >= EXPANSION_READY_SCORE,
        "threshold": EXPANSION_READY_SCORE,
        "atr_pct_by_timeframe": atr_by_tf,
        "evidence": [item.as_dict() for item in evidence],
        "input_timeframes": sorted(payloads),
    }


def direction_state(
    technical: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer direction independently from expansion readiness."""
    context = context or {}
    payloads = _tf_payloads(technical)
    evidence: list[Evidence] = []

    for tf, payload in payloads.items():
        weight = TIMEFRAME_WEIGHTS[tf]
        price = _finite(payload.get("price"))
        ema20 = _finite(payload.get("ema20"))
        ema50 = _finite(payload.get("ema50"))
        vwap = _finite(payload.get("vwap"))
        alpha_signal = _alpha_signal(payload.get("alpha_score"))
        macd_norm = _macd_atr(payload)

        if alpha_signal is not None:
            weight_i = 0.90 * weight
            evidence.append(Evidence(f"{tf}:alpha", alpha_signal, weight_i, alpha_signal * weight_i))

        if price is not None and ema20 is not None and ema50 is not None:
            trend = (_sign(price - ema20) + _sign(ema20 - ema50)) / 2.0
            weight_i = 0.75 * weight
            evidence.append(Evidence(f"{tf}:ema_structure", trend, weight_i, trend * weight_i))

        if price is not None and vwap is not None:
            value = float(_sign(price - vwap))
            weight_i = 0.45 * weight
            evidence.append(Evidence(f"{tf}:vwap_side", value, weight_i, value * weight_i))

        if macd_norm is not None:
            value = _clip(macd_norm / 0.35)
            weight_i = 0.70 * weight
            evidence.append(Evidence(f"{tf}:macd_impulse", value, weight_i, value * weight_i))

        structure = float(_structure_side(payload.get("market_structure")))
        if structure:
            weight_i = 0.70 * weight
            evidence.append(Evidence(f"{tf}:market_structure", structure, weight_i, structure * weight_i))

        signal = float(_signal_side(payload.get("signal")))
        if signal:
            weight_i = 0.45 * weight
            evidence.append(Evidence(f"{tf}:engine_signal", signal, weight_i, signal * weight_i))

    # Context remains secondary evidence, never a standalone veto/promotion layer.
    context_components = {
        "relative_strength": context.get("relative_strength_vs_nifty_pct"),
        "peer_mean": context.get("peer_mean_60m_pct"),
        "market": context.get("market_60m_pct"),
        "peer_breadth": context.get("peer_breadth"),
    }
    for name, raw in context_components.items():
        side = _sign(raw, 0.02 if name != "peer_breadth" else 0.1)
        if side:
            weight_i = 0.40 if name in {"relative_strength", "peer_mean"} else 0.25
            evidence.append(Evidence(f"context:{name}", float(side), weight_i, float(side) * weight_i))

    components = context.get("components")
    event_component = (
        _finite(components.get("events"))
        if isinstance(components, Mapping)
        else None
    )
    if event_component is not None and event_component != 0:
        value = _clip(event_component / 2.0)
        evidence.append(Evidence("context:events", value, 0.25, value * 0.25))

    aggregate = _direction_aggregate(evidence)
    return {
        "side": aggregate["side"],
        "signed_score": round(float(aggregate["signed_score"]), 6),
        "long_evidence": round(float(aggregate["positive"]), 6),
        "short_evidence": round(float(aggregate["negative"]), 6),
        "dominance_ratio": round(float(aggregate["dominance"]), 6),
        "strength": round(float(aggregate["strength"]), 6),
        "confidence": round(float(aggregate["confidence"]), 6),
        "action_threshold": DIRECTION_ACTION_SCORE,
        "dominance_threshold": DIRECTION_DOMINANCE_MIN,
        "evidence": [item.as_dict() for item in evidence],
    }


def decide(
    symbol: str,
    technical: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return V4's point-in-time underlying decision."""
    expansion = expansion_state(technical)
    direction = direction_state(technical, context)
    direction_strength = abs(float(direction["signed_score"]))
    dominance = float(direction["dominance_ratio"])

    reasons: list[str] = []
    if not expansion["ready"]:
        reasons.append("EXPANSION_NOT_READY")
    if direction_strength < DIRECTION_ACTION_SCORE:
        reasons.append("DIRECTION_EVIDENCE_WEAK")
    if dominance < DIRECTION_DOMINANCE_MIN:
        reasons.append("DIRECTION_CONFLICT_HIGH")
    if direction["side"] == "NEUTRAL":
        reasons.append("DIRECTION_NEUTRAL")

    actionable = not reasons
    action = direction["side"] if actionable else "NO_TRADE"
    return {
        "protocol_id": PROTOCOL_ID,
        "symbol": symbol.upper(),
        "action": action,
        "expansion": expansion,
        "direction": direction,
        "reasons": reasons or ["EXPANSION_AND_DIRECTION_CONFIRMED"],
        "underlying_only": True,
        "point_in_time_only": True,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "previous_version_frozen": "FNO_MARKET_BRAIN_V3_2026-09-07",
        "two_stage_brain": True,
        "stage_1": "EXPANSION_READINESS",
        "stage_2": "DIRECTION_INFERENCE",
        "stock_normalization": "DIMENSIONLESS_ATR_VOLUME_AND_RELATIVE_FEATURES",
        "alpha_semantics": "CENTER_50_FULL_SCALE_AT_42_58",
        "direction_conflict_metric": "INDEPENDENT_WEIGHTED_DOMINANCE_RATIO",
        "direction_dominance_min": DIRECTION_DOMINANCE_MIN,
        "direction_dominance_interpretation": "AT_LEAST_60_40_WEIGHTED_EVIDENCE_SPLIT",
        "outcomes_read": False,
        "future_bars_read": False,
        "option_inputs_read": False,
        "futures_inputs_read": False,
        "point_in_time_context_allowed": True,
        "benchmark_results_used_in_decision": False,
        "thresholds_fit_to_v1_v2_v3_results": False,
        "historical_evaluation_run_by_module": False,
        "live_execution": False,
        "capital_committed": 0,
    }
