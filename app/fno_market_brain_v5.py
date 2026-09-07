"""F&O Market Brain V5: phase/transition architecture for underlying direction research.

V5 is a development candidate built after V3 losing-trade forensics. It deliberately
moves away from summing many correlated indicators into one confidence score.

Design principles:
- classify market phase before asking for direction;
- use the existing technical engine's family-score semantics instead of counting
  EMA/VWAP/MACD/structure labels as independent confirmations;
- respect the 1h regime and require an explicit, symmetric reversal package before
  taking a counter-regime transition;
- avoid chasing mature trends unless a pullback/re-entry state is visible;
- make expansion/setup readiness selective using existing engine boundaries;
- emit a deterministic thesis key so repeated clicks can be de-duplicated;
- remain outcome-blind and underlying-only.

The 720-row V3 current-expiry sample that motivated this architecture is development
data only. No constants in this module are fit to its realized returns. Where a
boundary is needed, V5 reuses an already-existing engine semantic (for example,
trend +/-8, momentum +/-4, structure +/-7, volume 1.25/0.8, or 0.75 ATR proximity).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

PROTOCOL_ID = "FNO_MARKET_BRAIN_V5_PHASE_TRANSITION_2026-09-07"

# Existing technical-engine confirmation semantics (app/engine.py).
TREND_CONFIRM = 8.0
MOMENTUM_CONFIRM = 4.0
STRUCTURE_CONFIRM = 7.0
PRICE_ACTION_CONFIRM = 1.0
VOLUME_EXPANSION = 1.25
VOLUME_NORMAL = 0.80
STRUCTURE_PROXIMITY_ATR = 0.75
RELATIVE_STRENGTH_DEADBAND_PCT = 0.02

DIRECTIONAL_FAMILIES = ("trend", "momentum", "structure", "price_action")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _sign(value: Any, deadband: float = 0.0) -> int:
    number = _finite(value)
    if number is None or abs(number) <= deadband:
        return 0
    return 1 if number > 0 else -1


def _side_sign(side: str | None) -> int:
    text = str(side or "").upper()
    return 1 if text == "LONG" else -1 if text == "SHORT" else 0


def _side_from_sign(value: int) -> str | None:
    return "LONG" if value > 0 else "SHORT" if value < 0 else None


def _tf(technical: Mapping[str, Any], timeframe: str) -> Mapping[str, Any]:
    payload = (technical.get("timeframes") or {}).get(timeframe, {})
    return payload if isinstance(payload, Mapping) else {}


def _family(payload: Mapping[str, Any], name: str) -> float:
    scores = payload.get("family_scores")
    if not isinstance(scores, Mapping):
        return 0.0
    return _finite(scores.get(name)) or 0.0


def _family_vote(payload: Mapping[str, Any], name: str) -> int:
    value = _family(payload, name)
    boundary = {
        "trend": TREND_CONFIRM,
        "momentum": MOMENTUM_CONFIRM,
        "structure": STRUCTURE_CONFIRM,
        "price_action": PRICE_ACTION_CONFIRM,
    }[name]
    if value >= boundary:
        return 1
    if value <= -boundary:
        return -1
    return 0


def family_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    votes = {name: _family_vote(payload, name) for name in DIRECTIONAL_FAMILIES}
    positive = sum(value > 0 for value in votes.values())
    negative = sum(value < 0 for value in votes.values())
    side = "LONG" if positive >= 2 and positive > negative else "SHORT" if negative >= 2 and negative > positive else "NEUTRAL"
    return {
        "votes": votes,
        "long_families": positive,
        "short_families": negative,
        "side": side,
    }


def higher_timeframe_regime(technical: Mapping[str, Any]) -> dict[str, Any]:
    payload = _tf(technical, "1h")
    structure = str(payload.get("market_structure") or "RANGE").upper()
    trend = _family(payload, "trend")
    momentum = _family(payload, "momentum")

    if structure == "UPTREND" and trend >= TREND_CONFIRM:
        regime = "BULLISH"
        side = "LONG"
    elif structure == "DOWNTREND" and trend <= -TREND_CONFIRM:
        regime = "BEARISH"
        side = "SHORT"
    else:
        regime = "RANGE_MIXED"
        side = None

    return {
        "regime": regime,
        "side": side,
        "market_structure": structure,
        "trend_family": trend,
        "momentum_family": momentum,
        "family_state": family_state(payload),
    }


def _relative_strength_side(context: Mapping[str, Any] | None) -> int:
    context = context or {}
    return _sign(context.get("relative_strength_vs_nifty_pct"), RELATIVE_STRENGTH_DEADBAND_PCT)


def _news_side(context: Mapping[str, Any] | None) -> int:
    context = context or {}
    components = context.get("components")
    if not isinstance(components, Mapping):
        return 0
    return _sign(components.get("events"))


def _momentum_side(payload: Mapping[str, Any]) -> int:
    value = _family(payload, "momentum")
    return 1 if value >= MOMENTUM_CONFIRM else -1 if value <= -MOMENTUM_CONFIRM else 0


def _trend_side(payload: Mapping[str, Any]) -> int:
    value = _family(payload, "trend")
    return 1 if value >= TREND_CONFIRM else -1 if value <= -TREND_CONFIRM else 0


def _structure_side(payload: Mapping[str, Any]) -> int:
    value = _family(payload, "structure")
    if value >= STRUCTURE_CONFIRM:
        return 1
    if value <= -STRUCTURE_CONFIRM:
        return -1
    text = str(payload.get("market_structure") or "").upper()
    if text == "UPTREND":
        return 1
    if text == "DOWNTREND":
        return -1
    return 0


def _five_minute_confirmation(technical: Mapping[str, Any], side: str) -> dict[str, Any]:
    payload = _tf(technical, "5m")
    wanted = _side_sign(side)
    momentum = _momentum_side(payload)
    supporting = {
        "trend": _trend_side(payload) == wanted,
        "structure": _structure_side(payload) == wanted,
        "price_action": _family_vote(payload, "price_action") == wanted,
    }
    confirmed = momentum == wanted and any(supporting.values())
    return {
        "confirmed": confirmed,
        "momentum_aligned": momentum == wanted,
        "supporting_family": supporting,
        "family_state": family_state(payload),
    }


def _side_distance(payload: Mapping[str, Any], side: str) -> float | None:
    key = "distance_to_resistance_atr" if side == "LONG" else "distance_to_support_atr"
    # For an emerging LONG, resistance is the boundary to expand through.
    # For an emerging SHORT, support is the boundary to expand through.
    return _finite(payload.get(key))


def _pullback_location(payload: Mapping[str, Any], side: str) -> float | None:
    key = "distance_to_support_atr" if side == "LONG" else "distance_to_resistance_atr"
    return _finite(payload.get(key))


def _near(value: float | None) -> bool:
    return value is not None and 0.0 <= value <= STRUCTURE_PROXIMITY_ATR


def _volume(payload: Mapping[str, Any]) -> float | None:
    value = _finite(payload.get("volume_ratio_capped"))
    if value is None:
        value = _finite(payload.get("volume_ratio_raw"))
    return value


def transition_candidate(
    technical: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = _tf(technical, "15m")
    structure = str(payload.get("market_structure") or "RANGE").upper()
    momentum_side = _momentum_side(payload)
    rs_side = _relative_strength_side(context)
    side = _side_from_sign(momentum_side) if structure == "RANGE" and momentum_side and momentum_side == rs_side else None
    return {
        "side": side,
        "structure": structure,
        "momentum_side": _side_from_sign(momentum_side),
        "relative_strength_side": _side_from_sign(rs_side),
        "fifteen_minute_trend_side": _side_from_sign(_trend_side(payload)),
        "eligible": side is not None,
    }


def market_phase(
    technical: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fifteen = _tf(technical, "15m")
    structure = str(fifteen.get("market_structure") or "RANGE").upper()
    momentum_side = _momentum_side(fifteen)
    structure_side = 1 if structure == "UPTREND" else -1 if structure == "DOWNTREND" else 0
    regime = higher_timeframe_regime(technical)
    transition = transition_candidate(technical, context)

    if transition["eligible"]:
        phase = "TRANSITION"
        side = transition["side"]
    elif structure_side and momentum_side == -structure_side:
        phase = "EXHAUSTION"
        side = _side_from_sign(structure_side)
    elif structure_side and _side_sign(regime["side"]) == structure_side:
        phase = "MATURE_TREND"
        side = _side_from_sign(structure_side)
    elif structure_side:
        phase = "DEVELOPING_OR_COUNTERTREND"
        side = _side_from_sign(structure_side)
    else:
        phase = "RANGE"
        side = None

    return {
        "phase": phase,
        "side": side,
        "fifteen_minute_structure": structure,
        "fifteen_minute_momentum_side": _side_from_sign(momentum_side),
        "transition": transition,
        "higher_timeframe_regime": regime,
    }


def _transition_expansion(
    technical: Mapping[str, Any],
    side: str,
) -> dict[str, Any]:
    five = _tf(technical, "5m")
    fifteen = _tf(technical, "15m")
    five_confirmation = _five_minute_confirmation(technical, side)
    participation = any(
        value is not None and value >= VOLUME_EXPANSION
        for value in (_volume(five), _volume(fifteen))
    )
    proximity = any(
        _near(_side_distance(payload, side))
        for payload in (five, fifteen)
    )
    # Transition must have lower-timeframe directional confirmation plus at least
    # one independent expansion cue: participation or nearby structural boundary.
    ready = five_confirmation["confirmed"] and (participation or proximity)
    return {
        "ready": ready,
        "mode": "TRANSITION",
        "five_minute_confirmation": five_confirmation,
        "participation_above_1_25x": participation,
        "structure_boundary_within_0_75_atr": proximity,
        "five_minute_volume_ratio": _volume(five),
        "fifteen_minute_volume_ratio": _volume(fifteen),
    }


def _pullback_reentry(
    technical: Mapping[str, Any],
    side: str,
) -> dict[str, Any]:
    five = _tf(technical, "5m")
    wanted = _side_sign(side)
    structure = str(five.get("market_structure") or "RANGE").upper()
    trend_aligned = _trend_side(five) == wanted
    momentum_aligned = _momentum_side(five) == wanted
    location = _pullback_location(five, side)
    location_ready = structure == "RANGE" and _near(location)
    participation = any(
        value is not None and value >= VOLUME_NORMAL
        for value in (_volume(five), _volume(_tf(technical, "15m")))
    )
    ready = trend_aligned and momentum_aligned and location_ready and participation
    return {
        "ready": ready,
        "mode": "PULLBACK_REENTRY",
        "five_minute_structure": structure,
        "trend_recovered": trend_aligned,
        "momentum_recovered": momentum_aligned,
        "pullback_location_within_0_75_atr": location_ready,
        "pullback_location_atr": location,
        "participation_at_or_above_0_8x": participation,
    }


def expansion_state(
    technical: Mapping[str, Any],
    side: str | None,
    setup_type: str | None,
) -> dict[str, Any]:
    if side not in {"LONG", "SHORT"} or not setup_type:
        return {"ready": False, "mode": None, "reason": "NO_DIRECTIONAL_SETUP"}
    if setup_type == "TRANSITION":
        return _transition_expansion(technical, side)
    if setup_type == "PULLBACK_REENTRY":
        return _pullback_reentry(technical, side)
    return {"ready": False, "mode": setup_type, "reason": "UNSUPPORTED_SETUP_TYPE"}


def _counter_regime_reversal_confirmed(
    technical: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    side: str,
    phase: Mapping[str, Any],
) -> dict[str, Any]:
    fifteen = _tf(technical, "15m")
    wanted = _side_sign(side)
    five = _five_minute_confirmation(technical, side)
    fifteen_trend = _trend_side(fifteen)
    news = _news_side(context)
    confirmed = (
        phase.get("phase") == "TRANSITION"
        and fifteen_trend == wanted
        and five["confirmed"]
        and news != -wanted
    )
    return {
        "confirmed": confirmed,
        "fifteen_minute_trend_aligned": fifteen_trend == wanted,
        "five_minute_confirmation": five["confirmed"],
        "news_conflict": news == -wanted,
    }


def _state_signature(symbol: str, side: str | None, setup_type: str | None, phase: Mapping[str, Any]) -> str:
    regime = (phase.get("higher_timeframe_regime") or {}).get("regime")
    raw = {
        "symbol": symbol.upper(),
        "side": side,
        "setup_type": setup_type,
        "phase": phase.get("phase"),
        "regime": regime,
        "15m_structure": phase.get("fifteen_minute_structure"),
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


def _thesis_key(symbol: str, side: str, setup_type: str, phase: Mapping[str, Any]) -> str:
    regime = (phase.get("higher_timeframe_regime") or {}).get("regime")
    raw = {
        "protocol": PROTOCOL_ID,
        "symbol": symbol.upper(),
        "side": side,
        "setup_type": setup_type,
        "regime": regime,
        "15m_structure": phase.get("fifteen_minute_structure"),
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def decide(
    symbol: str,
    technical: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an outcome-blind V5 underlying decision.

    V5 intentionally emits only two setup types:
    - TRANSITION: 15m is still RANGE while momentum and relative strength agree,
      followed by 5m confirmation and a selective expansion cue.
    - PULLBACK_REENTRY: a mature 15m/1h trend is not chased; it becomes actionable
      only after a 5m range/pullback state recovers trend+momentum near structure.

    The rules are symmetric for LONG and SHORT.
    """
    context = context or {}
    phase = market_phase(technical, context)
    regime = phase["higher_timeframe_regime"]
    reasons: list[str] = []
    side: str | None = None
    setup_type: str | None = None

    if phase["phase"] == "TRANSITION":
        side = phase["transition"]["side"]
        setup_type = "TRANSITION"
    elif phase["phase"] == "MATURE_TREND":
        side = phase["side"]
        setup_type = "PULLBACK_REENTRY"
    elif phase["phase"] == "EXHAUSTION":
        reasons.append("EXHAUSTION_REQUIRES_NEW_STRUCTURE")
    elif phase["phase"] == "DEVELOPING_OR_COUNTERTREND":
        reasons.append("TREND_NOT_CONFIRMED_BY_HIGHER_TIMEFRAME")
    else:
        reasons.append("NO_TRANSITION_OR_REENTRY_SETUP")

    expansion = expansion_state(technical, side, setup_type)

    counter_regime = False
    reversal = {"confirmed": False}
    if side:
        regime_side = regime.get("side")
        counter_regime = regime_side in {"LONG", "SHORT"} and regime_side != side
        if counter_regime:
            reversal = _counter_regime_reversal_confirmed(technical, context, side, phase)
            if not reversal["confirmed"]:
                reasons.append("HIGHER_TIMEFRAME_REGIME_OPPOSED")

    if side and setup_type == "PULLBACK_REENTRY" and not expansion["ready"]:
        reasons.append("MATURE_TREND_CHASE_BLOCKED")
    elif side and setup_type == "TRANSITION" and not expansion["ready"]:
        reasons.append("TRANSITION_NOT_CONFIRMED")

    action = side if side and not reasons else "NO_TRADE"
    thesis_key = _thesis_key(symbol, side, setup_type, phase) if action != "NO_TRADE" and setup_type else None
    state_signature = _state_signature(symbol, side, setup_type, phase)

    return {
        "protocol_id": PROTOCOL_ID,
        "symbol": symbol.upper(),
        "action": action,
        "setup_type": setup_type if side else None,
        "phase": phase,
        "expansion": expansion,
        "counter_regime": counter_regime,
        "counter_regime_reversal": reversal,
        "family_states": {
            tf: family_state(_tf(technical, tf))
            for tf in ("5m", "15m", "1h")
        },
        "context_diagnostics": {
            "relative_strength_side": _side_from_sign(_relative_strength_side(context)),
            "news_side": _side_from_sign(_news_side(context)),
        },
        "thesis": {
            "key": thesis_key,
            "state_signature": state_signature,
            "dedupe_required": action != "NO_TRADE",
        },
        "reasons": reasons or ["PHASE_SETUP_CONFIRMED"],
        "underlying_only": True,
        "point_in_time_only": True,
    }


@dataclass
class _ActiveThesis:
    key: str
    state_signature: str


class ThesisTracker:
    """Suppress repeated emissions of the same active market thesis.

    A thesis remains active while the market-state signature is unchanged. A
    material phase/regime/setup-state change clears the prior thesis. Evaluators
    should additionally call ``reset()`` at a session boundary.
    """

    def __init__(self) -> None:
        self._active: dict[str, _ActiveThesis] = {}

    def reset(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._active.clear()
        else:
            self._active.pop(symbol.upper(), None)

    def apply(self, decision: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(decision))
        symbol = str(result.get("symbol") or "").upper()
        thesis = result.get("thesis") if isinstance(result.get("thesis"), Mapping) else {}
        state_signature = str(thesis.get("state_signature") or "")
        key = str(thesis.get("key") or "")
        active = self._active.get(symbol)

        if active and state_signature and active.state_signature != state_signature:
            self._active.pop(symbol, None)
            active = None

        if result.get("action") in {"LONG", "SHORT"} and key:
            if active and active.key == key:
                original = result["action"]
                result["suppressed_action"] = original
                result["action"] = "NO_TRADE"
                result["reasons"] = list(result.get("reasons") or []) + ["DUPLICATE_ACTIVE_THESIS"]
                result["thesis"]["duplicate_suppressed"] = True
                return result
            self._active[symbol] = _ActiveThesis(key=key, state_signature=state_signature)
            result["thesis"]["duplicate_suppressed"] = False
        return result


def architecture_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "development_source": "V3_LOSING_TRADE_FORENSICS",
        "v3_v4_mutated": False,
        "phase_first_architecture": True,
        "evidence_unit": "EXISTING_ENGINE_FAMILY_SCORES",
        "correlated_indicator_votes_summed_independently": False,
        "alpha_score_used_as_direction_gate": False,
        "symmetric_long_short_rules": True,
        "higher_timeframe_regime_control": True,
        "mature_trend_chase_blocked_without_pullback_reentry": True,
        "selective_transition_expansion": True,
        "deterministic_thesis_deduplication_supported": True,
        "v3_720_rows_are_development_only": True,
        "v3_discovered_short_pattern_declared_proven": False,
        "outcomes_read": False,
        "future_bars_read": False,
        "option_inputs_read": False,
        "futures_inputs_read": False,
        "benchmark_results_used_in_decision": False,
        "historical_holdout_evaluation_run_by_module": False,
        "forward_test_run_by_module": False,
        "live_execution": False,
    }
