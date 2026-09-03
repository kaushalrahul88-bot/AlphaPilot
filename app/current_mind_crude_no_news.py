from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime

from .crude_directional_asymmetry_candidate import CANDIDATE_ID, long_only_shadow_signal
from .crude_random_click_replay import (
    _forward_60m,
    _index_for_click,
    _score_trades,
    preregister_click_schedule,
)
from .crude_research_brain import _build_snapshot_clean, _f, clean_ohlcv

CURRENT_MIND_ID = "CRUDE_CURRENT_MIND_NO_NEWS_V1"
DECISION_RULE_SOURCE = CANDIDATE_ID


def _stance(value, positive="BULLISH", negative="BEARISH"):
    number = _f(value)
    if number is None or number == 0:
        return "NEUTRAL"
    return positive if number > 0 else negative


def _evidence_annotations(features):
    """Describe visible evidence without changing the frozen decision."""
    return {
        "structure": {
            "value": features.get("structure") or "UNKNOWN",
            "role": "FROZEN_DECISION_INPUT",
        },
        "momentum_15m": {
            "value_pct": _f(features.get("return_15m_pct")),
            "stance": _stance(features.get("return_15m_pct")),
            "role": "FROZEN_DECISION_INPUT",
        },
        "ema20_location": {
            "gap_pct": _f(features.get("ema20_gap_pct")),
            "stance": _stance(features.get("ema20_gap_pct")),
            "role": "FROZEN_DECISION_INPUT",
        },
        "ema50_location": {
            "gap_pct": _f(features.get("ema50_gap_pct")),
            "stance": _stance(features.get("ema50_gap_pct")),
            "role": "FROZEN_DECISION_INPUT",
        },
        "session_context": {
            "session_return_pct": _f(features.get("session_return_pct")),
            "session_range_position": _f(features.get("session_range_position")),
            "session_vwap_gap_pct": _f(features.get("session_vwap_gap_pct")),
            "role": "ANNOTATION_ONLY",
        },
        "participation_context": {
            "relative_volume": _f(features.get("relative_volume")),
            "price_oi_state": features.get("price_oi_state") or "UNKNOWN",
            "oi_change_15m_pct": _f(features.get("oi_change_15m_pct")),
            "role": "ANNOTATION_ONLY",
        },
        "volatility_context": {
            "atr_pct": _f(features.get("atr_pct")),
            "role": "ANNOTATION_ONLY",
        },
    }


def _decision_fingerprint(payload):
    frozen = {
        "current_mind_id": payload["current_mind_id"],
        "decision_rule_source": payload["decision_rule_source"],
        "action": payload["action"],
        "direction": payload["direction"],
        "bar_start": payload["bar_start"],
        "available_at": payload["available_at"],
    }
    raw = json.dumps(frozen, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def current_mind_crude_no_news_decision(features):
    """Frozen no-news Crude Current Mind.

    The action is exactly the already-validated long-only shadow action. Context
    added to the journal is descriptive and cannot create, remove, or upgrade a trade.
    """
    frozen_signal = long_only_shadow_signal(features)
    action = "BUY" if frozen_signal == "BUY" else "WAIT"
    decision = {
        "current_mind_id": CURRENT_MIND_ID,
        "commodity": "CRUDEOIL",
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "option_premium_scored": False,
        "news_enabled": False,
        "news_policy": "FORBIDDEN_IN_NO_NEWS_CURRENT_MIND",
        "decision_rule_source": DECISION_RULE_SOURCE,
        "action": action,
        "direction": "LONG" if action == "BUY" else None,
        "thesis": "TECHNICAL_UPTREND_CONTINUATION" if action == "BUY" else "NO_FROZEN_LONG_SIGNAL",
        "bar_start": features.get("bar_start"),
        "available_at": features.get("available_at"),
        "evidence": _evidence_annotations(features),
        "decision_semantics": {
            "buy_rule": "UPTREND + positive 15m return + price above EMA20 and EMA50",
            "sell_policy": "SELL signals are converted to WAIT by CRUDE_LONG_ONLY_SHADOW_V1",
            "annotation_fields_can_change_action": False,
            "news_can_change_action": False,
        },
    }
    decision["decision_fingerprint"] = _decision_fingerprint(decision)
    return decision


def replay_crude_no_news_current_mind(candles, *, trading_symbol, clicks_per_session=20):
    """Parity replay on the already-preregistered random-click schedule.

    This is an assembly/parity audit, not new strategy evidence. It proves the
    Current Mind journal reproduces the previously frozen candidate exactly.
    """
    rows = clean_ohlcv(candles)
    schedule = preregister_click_schedule(rows, clicks_per_session)
    decisions = []
    parity_mismatches = []
    for scheduled in schedule:
        click_at = datetime.fromisoformat(scheduled["click_at"])
        index = _index_for_click(rows, click_at)
        if index is None or index < 50:
            parity_mismatches.append({"click_at": scheduled["click_at"], "reason": "NOT_EVALUABLE"})
            continue
        forward = _forward_60m(rows, index)
        if forward is None:
            parity_mismatches.append({"click_at": scheduled["click_at"], "reason": "NO_60M_LABEL"})
            continue
        features = _build_snapshot_clean(rows, index)
        frozen_signal = long_only_shadow_signal(features)
        expected_action = "BUY" if frozen_signal == "BUY" else "WAIT"
        journal = current_mind_crude_no_news_decision(features)
        if journal["action"] != expected_action:
            parity_mismatches.append({
                "click_at": scheduled["click_at"],
                "expected": expected_action,
                "actual": journal["action"],
            })
        decisions.append({
            "date": scheduled["date"],
            "click_at": scheduled["click_at"],
            "action": journal["action"],
            "direction": journal["direction"],
            "decision_fingerprint": journal["decision_fingerprint"],
            "forward_60m_pct": round(float(forward), 6),
            "journal": journal,
        })

    scoring_rows = [
        {
            "current_mind_action": "BUY" if row["action"] == "BUY" else "NO_TRADE",
            "forward_60m_pct": row["forward_60m_pct"],
        }
        for row in decisions
    ]
    action_counts = Counter(row["action"] for row in decisions)
    return {
        "mode": "ALPHAPILOT_CRUDE_CURRENT_MIND_NO_NEWS_V1_PARITY_REPLAY",
        "current_mind_id": CURRENT_MIND_ID,
        "commodity": "CRUDEOIL",
        "trading_symbol": str(trading_symbol),
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "option_premium_scored": False,
        "news_enabled": False,
        "decision_rule_source": DECISION_RULE_SOURCE,
        "parity_audit_only": True,
        "new_strategy_evidence_claimed": False,
        "click_schedule_reused_from": "ALPHAPILOT_CRUDE_NO_NEWS_RANDOM_CLICK_V1",
        "coverage": {
            "scheduled_clicks": len(schedule),
            "evaluated_clicks": len(decisions),
            "exact_click_coverage": len(schedule) == len(decisions),
            "parity_mismatches": len(parity_mismatches),
        },
        "actions": dict(action_counts),
        "score": _score_trades(scoring_rows, "current_mind_action"),
        "parity_mismatches": parity_mismatches,
        "decisions": decisions,
        "freeze_candidate": {
            "ready": len(schedule) == len(decisions) and not parity_mismatches,
            "freeze_id": CURRENT_MIND_ID,
            "rule_change_from_validated_long_only_candidate": False,
            "news_integration_allowed_before_freeze": False,
        },
        "next_gate": "Freeze CRUDE_CURRENT_MIND_NO_NEWS_V1. Only after freeze, build a separate point-in-time news shadow and compare on identical clicks without changing the no-news decisions.",
    }
