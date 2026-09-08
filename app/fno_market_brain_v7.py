"""F&O Market Brain V7: transition persistence confirmation.

V7 is an outcome-driven development revision created only after frozen V6
Historical Holdout B failed its pre-registered strategic-edge gates. Holdout B
is therefore development data for V7 and can never be reused as V7 promotion
evidence.

The V7 change is deliberately narrow and symmetric:
- V7 can only filter an already-actionable V6 TRANSITION; it cannot create a new
  trade that V6 rejected;
- an aligned 1h price-action family confirms persistence directly;
- an opposed 1h price-action family always blocks the transition;
- when 1h price action is neutral, local persistence requires all three existing
  V5/V6 semantics together: dual-timeframe volume expansion, aligned 15m price
  action, and a directional structure boundary within 0.75 ATR;
- no new numeric threshold is fitted to Holdout B;
- decisions remain underlying-only, point-in-time, derivative-free and outcome-
  blind.

This architecture encodes the development finding that V6's OR-style evidence
was still too permissive for 90-minute directional persistence. The finding is a
hypothesis only until V7 passes a fresh unseen historical holdout.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from . import fno_market_brain_v5 as v5
from . import fno_market_brain_v6 as v6

PROTOCOL_ID = "FNO_MARKET_BRAIN_V7_PERSISTENCE_CONFIRMATION_2026-09-08"
DEVELOPMENT_SOURCE = "FNO_MARKET_BRAIN_V6_HOLDOUT_B_2026-09-08"
BASE_PROTOCOL_ID = v6.PROTOCOL_ID

ThesisTracker = v5.ThesisTracker


def persistence_state(
    technical: Mapping[str, Any],
    base_decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate V7 persistence using only point-in-time V6/V5 semantics."""
    side = str(base_decision.get("action") or "NO_TRADE").upper()
    if side not in {"LONG", "SHORT"}:
        return {
            "ready": False,
            "route": None,
            "reason": "V6_BASE_NOT_ACTIONABLE",
            "higher_timeframe_price_action_side": None,
            "higher_timeframe_price_action_aligned": False,
            "higher_timeframe_price_action_neutral": False,
            "higher_timeframe_price_action_opposed": False,
            "local_confluence": False,
        }

    wanted = v5._side_sign(side)
    hour = v5._tf(technical, "1h")
    hour_price_action_vote = v5._family_vote(hour, "price_action")
    hour_price_action_side = v5._side_from_sign(hour_price_action_vote)

    evidence = base_decision.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    dual_volume = evidence.get("dual_timeframe_volume_expansion") is True
    fifteen_price_action = evidence.get("fifteen_minute_price_action_aligned") is True
    structural_proximity = evidence.get("structure_boundary_within_0_75_atr") is True
    local_confluence = dual_volume and fifteen_price_action and structural_proximity

    aligned = hour_price_action_vote == wanted
    neutral = hour_price_action_vote == 0
    opposed = hour_price_action_vote == -wanted

    if aligned:
        route = "ALIGNED_1H_PRICE_ACTION"
        ready = True
    elif neutral and local_confluence:
        route = "NEUTRAL_1H_WITH_LOCAL_CONFLUENCE"
        ready = True
    else:
        route = None
        ready = False

    return {
        "ready": ready,
        "route": route,
        "higher_timeframe_price_action_vote": hour_price_action_vote,
        "higher_timeframe_price_action_side": hour_price_action_side,
        "higher_timeframe_price_action_aligned": aligned,
        "higher_timeframe_price_action_neutral": neutral,
        "higher_timeframe_price_action_opposed": opposed,
        "local_confluence": local_confluence,
        "local_confluence_components": {
            "dual_timeframe_volume_expansion": dual_volume,
            "fifteen_minute_price_action_aligned": fifteen_price_action,
            "structure_boundary_within_0_75_atr": structural_proximity,
        },
        "reused_semantics": {
            "price_action_family_threshold": v5.PRICE_ACTION_CONFIRM,
            "volume_expansion_threshold": v5.VOLUME_EXPANSION,
            "structure_proximity_atr": v5.STRUCTURE_PROXIMITY_ATR,
        },
    }


def _state_signature(
    symbol: str,
    side: str | None,
    base_decision: Mapping[str, Any],
    persistence: Mapping[str, Any],
) -> str:
    base_thesis = base_decision.get("thesis")
    base_thesis = base_thesis if isinstance(base_thesis, Mapping) else {}
    raw = {
        "protocol": PROTOCOL_ID,
        "symbol": symbol.upper(),
        "side": side,
        "base_state_signature": base_thesis.get("state_signature"),
        "persistence_route": persistence.get("route"),
        "one_hour_price_action_vote": persistence.get("higher_timeframe_price_action_vote"),
        "local_confluence": persistence.get("local_confluence"),
    }
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]


def _thesis_key(
    symbol: str,
    side: str,
    base_decision: Mapping[str, Any],
    persistence: Mapping[str, Any],
) -> str:
    base_thesis = base_decision.get("thesis")
    base_thesis = base_thesis if isinstance(base_thesis, Mapping) else {}
    raw = {
        "protocol": PROTOCOL_ID,
        "symbol": symbol.upper(),
        "side": side,
        "setup_type": "TRANSITION",
        "base_thesis_key": base_thesis.get("key"),
        "persistence_route": persistence.get("route"),
    }
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]


def decide(
    symbol: str,
    technical: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an outcome-blind V7 decision as a strict filter of V6."""
    base = v6.decide(symbol, technical, context)
    result = copy.deepcopy(base)
    base_action = str(base.get("action") or "NO_TRADE").upper()
    persistence = persistence_state(technical, base)

    result["protocol_id"] = PROTOCOL_ID
    result["development_source"] = DEVELOPMENT_SOURCE
    result["base_protocol_id"] = BASE_PROTOCOL_ID
    result["base_action"] = base_action
    result["persistence"] = persistence

    side = base_action if base_action in {"LONG", "SHORT"} else None
    if side is None:
        action = "NO_TRADE"
        reasons = list(base.get("reasons") or [])
    elif persistence.get("higher_timeframe_price_action_opposed"):
        action = "NO_TRADE"
        reasons = ["V7_HIGHER_TIMEFRAME_PRICE_ACTION_OPPOSED"]
    elif not persistence.get("ready"):
        action = "NO_TRADE"
        reasons = ["V7_TRANSITION_PERSISTENCE_NOT_CONFIRMED"]
    else:
        action = side
        reasons = ["V7_TRANSITION_PERSISTENCE_CONFIRMED"]

    result["action"] = action
    result["setup_type"] = "TRANSITION" if side else base.get("setup_type")
    result["reasons"] = reasons

    state_signature = _state_signature(symbol, side, base, persistence)
    thesis_key = (
        _thesis_key(symbol, side, base, persistence)
        if action in {"LONG", "SHORT"} and side
        else None
    )
    base_thesis = base.get("thesis")
    base_thesis = base_thesis if isinstance(base_thesis, Mapping) else {}
    result["thesis"] = {
        "key": thesis_key,
        "state_signature": state_signature,
        "dedupe_required": action in {"LONG", "SHORT"},
        "base_key": base_thesis.get("key"),
        "base_state_signature": base_thesis.get("state_signature"),
        "evidence_mode": base_thesis.get("evidence_mode"),
        "persistence_route": persistence.get("route"),
    }
    return result


def architecture_contract() -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "development_source": DEVELOPMENT_SOURCE,
        "v6_holdout_b_outcomes_are_development_only": True,
        "v6_holdout_b_reusable_for_v7_promotion": False,
        "v6_brain_modified_in_place": False,
        "v7_can_create_trade_rejected_by_v6": False,
        "v7_is_strict_filter_of_v6_actionable": True,
        "higher_timeframe_price_action_opposition_blocks": True,
        "higher_timeframe_price_action_alignment_confirms": True,
        "neutral_higher_timeframe_requires_local_confluence": True,
        "local_confluence_requires_dual_volume": True,
        "local_confluence_requires_15m_price_action": True,
        "local_confluence_requires_structure_proximity": True,
        "new_numeric_threshold_fit_to_holdout_b": False,
        "existing_v5_v6_semantics_reused": True,
        "long_short_rules_symmetric": True,
        "outcomes_read": False,
        "future_bars_read": False,
        "option_inputs_read": False,
        "futures_inputs_read": False,
        "forward_test_run_by_module": False,
        "live_execution": False,
    }
