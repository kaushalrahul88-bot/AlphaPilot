"""F&O Market Brain V6: evidence-strength transition architecture.

V6 is an outcome-driven development revision created only after V5 Historical
Holdout A failed its pre-registered gates. Holdout A is therefore development
data for V6 and can never be reused as V6 promotion evidence.

The V6 change is deliberately narrow:
- preserve V5 phase classification, relative-strength transition candidate,
  five-minute confirmation, and higher-timeframe regime semantics;
- stop treating a single weak participation cue or nearby S/R boundary as
  sufficient transition evidence;
- require either dual-timeframe volume expansion (5m AND 15m >= 1.25x) or an
  aligned 15m price-action family confirmation;
- keep structural proximity as a diagnostic, not an independent trigger;
- disable PULLBACK_REENTRY promotion until a separate unseen validation supports
  that setup class;
- remain symmetric LONG/SHORT, underlying-only, point-in-time, outcome-blind,
  and derivative-free.

No constants are fitted to Holdout A returns. V6 reuses existing engine semantic
boundaries already present in V5: volume expansion 1.25x and price-action family
confirmation +/-1.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from . import fno_market_brain_v5 as v5

PROTOCOL_ID = "FNO_MARKET_BRAIN_V6_EVIDENCE_TRANSITION_2026-09-08"
DEVELOPMENT_SOURCE = "FNO_MARKET_BRAIN_V5_HOLDOUT_A_2026-09-07"

VOLUME_EXPANSION = v5.VOLUME_EXPANSION
PRICE_ACTION_CONFIRM = v5.PRICE_ACTION_CONFIRM

ThesisTracker = v5.ThesisTracker


def _evidence_mode(dual_volume: bool, price_action: bool) -> str:
    if dual_volume and price_action:
        return "DUAL_VOLUME_AND_15M_PRICE_ACTION"
    if dual_volume:
        return "DUAL_VOLUME"
    if price_action:
        return "15M_PRICE_ACTION"
    return "NONE"


def transition_evidence_state(
    technical: Mapping[str, Any],
    side: str | None,
) -> dict[str, Any]:
    """Return V6 transition evidence without reading future outcomes."""
    if side not in {"LONG", "SHORT"}:
        return {
            "ready": False,
            "mode": "NONE",
            "reason": "NO_DIRECTIONAL_TRANSITION",
        }

    five = v5._tf(technical, "5m")
    fifteen = v5._tf(technical, "15m")
    wanted = v5._side_sign(side)
    five_confirmation = v5._five_minute_confirmation(technical, side)

    five_volume = v5._volume(five)
    fifteen_volume = v5._volume(fifteen)
    dual_volume = (
        five_volume is not None
        and fifteen_volume is not None
        and five_volume >= VOLUME_EXPANSION
        and fifteen_volume >= VOLUME_EXPANSION
    )
    price_action = v5._family_vote(fifteen, "price_action") == wanted
    structural_proximity = any(
        v5._near(v5._side_distance(payload, side))
        for payload in (five, fifteen)
    )
    evidence_mode = _evidence_mode(dual_volume, price_action)
    ready = five_confirmation["confirmed"] and (dual_volume or price_action)

    return {
        "ready": ready,
        "mode": evidence_mode,
        "five_minute_confirmation": five_confirmation,
        "dual_timeframe_volume_expansion": dual_volume,
        "fifteen_minute_price_action_aligned": price_action,
        "structure_boundary_within_0_75_atr": structural_proximity,
        "structure_boundary_is_diagnostic_only": True,
        "five_minute_volume_ratio": five_volume,
        "fifteen_minute_volume_ratio": fifteen_volume,
        "volume_threshold": VOLUME_EXPANSION,
        "price_action_threshold": PRICE_ACTION_CONFIRM,
    }


def _state_signature(
    symbol: str,
    side: str | None,
    phase: Mapping[str, Any],
    evidence_mode: str,
) -> str:
    regime = (phase.get("higher_timeframe_regime") or {}).get("regime")
    raw = {
        "protocol": PROTOCOL_ID,
        "symbol": symbol.upper(),
        "side": side,
        "phase": phase.get("phase"),
        "regime": regime,
        "15m_structure": phase.get("fifteen_minute_structure"),
        "evidence_mode": evidence_mode,
    }
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]


def _thesis_key(
    symbol: str,
    side: str,
    phase: Mapping[str, Any],
    evidence_mode: str,
) -> str:
    regime = (phase.get("higher_timeframe_regime") or {}).get("regime")
    raw = {
        "protocol": PROTOCOL_ID,
        "symbol": symbol.upper(),
        "side": side,
        "setup_type": "TRANSITION",
        "regime": regime,
        "15m_structure": phase.get("fifteen_minute_structure"),
        "evidence_mode": evidence_mode,
    }
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]


def decide(
    symbol: str,
    technical: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an outcome-blind V6 underlying decision."""
    context = context or {}
    phase = v5.market_phase(technical, context)
    regime = phase["higher_timeframe_regime"]
    reasons: list[str] = []

    side: str | None = None
    if phase.get("phase") == "TRANSITION":
        side = (phase.get("transition") or {}).get("side")
    elif phase.get("phase") == "MATURE_TREND":
        reasons.append("PULLBACK_REENTRY_DISABLED_PENDING_UNSEEN_EVIDENCE")
    elif phase.get("phase") == "EXHAUSTION":
        reasons.append("EXHAUSTION_REQUIRES_NEW_STRUCTURE")
    elif phase.get("phase") == "DEVELOPING_OR_COUNTERTREND":
        reasons.append("TREND_NOT_CONFIRMED_BY_HIGHER_TIMEFRAME")
    else:
        reasons.append("NO_TRANSITION_SETUP")

    evidence = transition_evidence_state(technical, side)

    counter_regime = False
    reversal = {"confirmed": False}
    if side:
        regime_side = regime.get("side")
        counter_regime = regime_side in {"LONG", "SHORT"} and regime_side != side
        if counter_regime:
            reversal = v5._counter_regime_reversal_confirmed(
                technical, context, side, phase
            )
            if not reversal["confirmed"]:
                reasons.append("HIGHER_TIMEFRAME_REGIME_OPPOSED")

        if not evidence.get("ready"):
            reasons.append("TRANSITION_EVIDENCE_NOT_CONFIRMED")

    action = side if side and not reasons else "NO_TRADE"
    mode = str(evidence.get("mode") or "NONE")
    state_signature = _state_signature(symbol, side, phase, mode)
    thesis_key = (
        _thesis_key(symbol, side, phase, mode)
        if action in {"LONG", "SHORT"} and side
        else None
    )

    return {
        "protocol_id": PROTOCOL_ID,
        "development_source": DEVELOPMENT_SOURCE,
        "symbol": symbol.upper(),
        "action": action,
        "setup_type": "TRANSITION" if side else None,
        "phase": phase,
        "evidence": evidence,
        "counter_regime": counter_regime,
        "counter_regime_reversal": reversal,
        "family_states": {
            tf: v5.family_state(v5._tf(technical, tf))
            for tf in ("5m", "15m", "1h")
        },
        "context_diagnostics": {
            "relative_strength_side": v5._side_from_sign(
                v5._relative_strength_side(context)
            ),
            "news_side": v5._side_from_sign(v5._news_side(context)),
        },
        "thesis": {
            "key": thesis_key,
            "state_signature": state_signature,
            "dedupe_required": action in {"LONG", "SHORT"},
            "evidence_mode": mode,
        },
        "reasons": reasons or ["V6_TRANSITION_EVIDENCE_CONFIRMED"],
        "underlying_only": True,
        "point_in_time_only": True,
    }


def architecture_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "development_source": DEVELOPMENT_SOURCE,
        "holdout_a_outcomes_are_development_only": True,
        "holdout_a_reusable_for_v6_promotion": False,
        "v5_brain_modified_in_place": False,
        "outcomes_read": False,
        "future_bars_read": False,
        "option_inputs_read": False,
        "futures_inputs_read": False,
        "forward_test_run_by_module": False,
        "live_execution": False,
        "transition_only": True,
        "pullback_reentry_enabled": False,
        "long_short_rules_symmetric": True,
        "structure_proximity_can_trigger_alone": False,
        "single_timeframe_volume_can_trigger_alone": False,
        "dual_timeframe_volume_or_15m_price_action_required": True,
    }
