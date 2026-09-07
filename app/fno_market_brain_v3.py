"""F&O Market Brain V3: point-in-time expansion detection plus direction inference.

V1/V2 forensics showed that adding context overlays did not create edge and that
many NO_TRADE observations were followed by large moves.  V3 therefore separates
*whether a move is setting up* from *which direction has the stronger evidence*.

This module is deliberately outcome-blind.  It consumes only technical snapshots
and point-in-time context already available at the click.  No options, futures,
future bars, resolved outcomes, or benchmark results are admitted into decisions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

PROTOCOL_ID = "FNO_MARKET_BRAIN_V3_2026-09-07"
TIMEFRAME_WEIGHTS = {"5m": 1.0, "15m": 1.35, "1h": 1.65}

# These are architecture thresholds, expressed in stock-normalized / dimensionless
# terms.  They are not derived from V1/V2 outcomes.
EXPANSION_READY_SCORE = 3.0
DIRECTION_ACTION_SCORE = 2.8
DIRECTION_MARGIN_SCORE = 1.25


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


def expansion_state(technical: Mapping[str, Any]) -> dict[str, Any]:
    """Estimate whether the current point-in-time state can support expansion.

    Inputs are dimensionless wherever practical: volume ratios, Bollinger width
    measured in ATRs, support/resistance distance in ATRs, and MACD in ATR units.
    This avoids applying one fixed percentage-move assumption to all stocks.
    """
    payloads = _tf_payloads(technical)
    evidence: list[Evidence] = []

    for tf, payload in payloads.items():
        weight = TIMEFRAME_WEIGHTS[tf]
        volume = _finite(payload.get("volume_ratio_raw"))
        width = _bandwidth_atr(payload)
        macd_norm = _macd_atr(payload)
        alpha = _finite(payload.get("alpha_score"))
        d_res = _finite(payload.get("distance_to_resistance_atr"))
        d_sup = _finite(payload.get("distance_to_support_atr"))

        if volume is not None:
            # >1 means participation above its own rolling baseline.
            value = _clip((volume - 1.0) / 0.75, 0.0, 1.0)
            evidence.append(Evidence(f"{tf}:volume_expansion", value, 0.85 * weight, value * 0.85 * weight))

        if width is not None:
            # Compression creates stored-energy potential; very wide bands indicate
            # that the move may already be extended. Peak readiness is near 3 ATR.
            value = _clip(1.0 - abs(width - 3.0) / 3.0, 0.0, 1.0)
            evidence.append(Evidence(f"{tf}:volatility_state", value, 0.55 * weight, value * 0.55 * weight))

        if macd_norm is not None:
            value = _clip(abs(macd_norm) / 0.35, 0.0, 1.0)
            evidence.append(Evidence(f"{tf}:momentum_impulse", value, 0.65 * weight, value * 0.65 * weight))

        if alpha is not None:
            value = _clip(abs(alpha) / 3.0, 0.0, 1.0)
            evidence.append(Evidence(f"{tf}:technical_pressure", value, 0.55 * weight, value * 0.55 * weight))

        distances = [value for value in (d_res, d_sup) if value is not None and value >= 0]
        if distances:
            nearest = min(distances)
            value = _clip((0.8 - nearest) / 0.8, 0.0, 1.0)
            evidence.append(Evidence(f"{tf}:structure_proximity", value, 0.6 * weight, value * 0.6 * weight))

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


def direction_state(technical: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
        alpha = _finite(payload.get("alpha_score"))
        macd_norm = _macd_atr(payload)

        if alpha is not None:
            value = _clip(alpha / 3.0)
            evidence.append(Evidence(f"{tf}:alpha", value, 0.9 * weight, value * 0.9 * weight))

        if price is not None and ema20 is not None and ema50 is not None:
            trend = (_sign(price - ema20) + _sign(ema20 - ema50)) / 2.0
            evidence.append(Evidence(f"{tf}:ema_structure", trend, 0.75 * weight, trend * 0.75 * weight))

        if price is not None and vwap is not None:
            value = float(_sign(price - vwap))
            evidence.append(Evidence(f"{tf}:vwap_side", value, 0.45 * weight, value * 0.45 * weight))

        if macd_norm is not None:
            value = _clip(macd_norm / 0.35)
            evidence.append(Evidence(f"{tf}:macd_impulse", value, 0.7 * weight, value * 0.7 * weight))

        structure = float(_structure_side(payload.get("market_structure")))
        if structure:
            evidence.append(Evidence(f"{tf}:market_structure", structure, 0.7 * weight, structure * 0.7 * weight))

        signal = float(_signal_side(payload.get("signal")))
        if signal:
            evidence.append(Evidence(f"{tf}:engine_signal", signal, 0.45 * weight, signal * 0.45 * weight))

    # Context is secondary evidence, not a veto/promotion layer.
    context_components = {
        "relative_strength": context.get("relative_strength_vs_nifty_pct"),
        "peer_mean": context.get("peer_mean_60m_pct"),
        "market": context.get("market_60m_pct"),
        "peer_breadth": context.get("peer_breadth"),
    }
    for name, raw in context_components.items():
        side = _sign(raw, 0.02 if name != "peer_breadth" else 0.1)
        if side:
            weight = 0.4 if name in {"relative_strength", "peer_mean"} else 0.25
            evidence.append(Evidence(f"context:{name}", float(side), weight, float(side) * weight))

    event_component = _finite((context.get("components") or {}).get("events")) if isinstance(context.get("components"), Mapping) else None
    if event_component is not None and event_component != 0:
        value = _clip(event_component / 2.0)
        evidence.append(Evidence("context:events", value, 0.25, value * 0.25))

    signed_score = sum(item.contribution for item in evidence)
    positive = sum(max(0.0, item.contribution) for item in evidence)
    negative = -sum(min(0.0, item.contribution) for item in evidence)
    margin = abs(positive - negative)
    side = "LONG" if signed_score > 0 else "SHORT" if signed_score < 0 else "NEUTRAL"
    confidence = abs(signed_score) / sum(abs(item.weight) for item in evidence) if evidence else 0.0

    return {
        "side": side,
        "signed_score": round(signed_score, 6),
        "long_evidence": round(positive, 6),
        "short_evidence": round(negative, 6),
        "margin": round(margin, 6),
        "confidence": round(confidence, 6),
        "action_threshold": DIRECTION_ACTION_SCORE,
        "margin_threshold": DIRECTION_MARGIN_SCORE,
        "evidence": [item.as_dict() for item in evidence],
    }


def decide(symbol: str, technical: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return V3's point-in-time underlying decision."""
    expansion = expansion_state(technical)
    direction = direction_state(technical, context)
    direction_strength = abs(float(direction["signed_score"]))
    margin = float(direction["margin"])

    reasons: list[str] = []
    if not expansion["ready"]:
        reasons.append("EXPANSION_NOT_READY")
    if direction_strength < DIRECTION_ACTION_SCORE:
        reasons.append("DIRECTION_EVIDENCE_WEAK")
    if margin < DIRECTION_MARGIN_SCORE:
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
        "two_stage_brain": True,
        "stage_1": "EXPANSION_READINESS",
        "stage_2": "DIRECTION_INFERENCE",
        "stock_normalization": "DIMENSIONLESS_ATR_VOLUME_AND_RELATIVE_FEATURES",
        "outcomes_read": False,
        "future_bars_read": False,
        "option_inputs_read": False,
        "futures_inputs_read": False,
        "point_in_time_context_allowed": True,
        "v1_v2_thresholds_retuned": False,
        "benchmark_results_used_in_decision": False,
        "evaluation_not_run_by_module": True,
    }
