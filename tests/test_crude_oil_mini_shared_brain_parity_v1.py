import json

from app.crude_oil_mini_episode_ledger_v1 import build_episode_capture
from app.crude_oil_mini_shared_brain_parity_v1 import (
    build_shared_brain_parity,
    build_shared_shadow_from_legacy_families,
)


def family(name, origin, stance="UNKNOWN", *, counts=False, dependencies=None):
    return {
        "family": name,
        "causal_origin": origin,
        "independence_status": "INDEPENDENT" if counts else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on_origins": dependencies or [],
        "counts_for_direction": counts,
        "stance": stance,
        "state": "TEST",
        "detail": {},
    }


def legacy_shadow(*, local="UNKNOWN", participation="UNKNOWN", global_crude="UNKNOWN", memory="UNKNOWN"):
    families = {
        "LOCAL_STRUCTURE": family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", local, counts=local != "UNKNOWN"),
        "PARTICIPATION": family("PARTICIPATION", "OPTION_OI_PREMIUM_FLOW", participation, counts=participation != "UNKNOWN"),
        "GLOBAL_CRUDE": family("GLOBAL_CRUDE", "CROSS_MARKET_CRUDE", global_crude, counts=global_crude != "UNKNOWN"),
        "EVENT_REACTION": family("EVENT_REACTION", "EXOGENOUS_INFORMATION"),
        "DIRECTION_MEMORY": family("DIRECTION_MEMORY", "HISTORICAL_ANALOGUE", memory, counts=memory != "UNKNOWN"),
    }
    directional = [name for name, row in families.items() if row["counts_for_direction"]]
    bullish = [name for name in directional if families[name]["stance"] == "BULLISH"]
    bearish = [name for name in directional if families[name]["stance"] == "BEARISH"]
    if bullish and bearish:
        direction, confidence, supporting, opposing = "UNKNOWN", "CONFLICTED", [], sorted(bullish + bearish)
    else:
        supporting = sorted(bullish or bearish)
        opposing = []
        if len(supporting) >= 2:
            direction = "BULLISH" if bullish else "BEARISH"
            confidence = "STRONG" if len(supporting) >= 3 else "MODERATE"
        else:
            direction, confidence = "UNKNOWN", "WEAK"
    return {
        "direction": direction,
        "confidence": confidence,
        "thesis_state": "TEST",
        "supporting_families": supporting,
        "opposing_families": opposing,
        "families": families,
        "decision_effect": "NONE",
    }


def test_shared_uses_exact_family_snapshot_and_preserves_three_origin_thesis():
    legacy = legacy_shadow(local="BULLISH", participation="BULLISH", global_crude="BULLISH")
    shared = build_shared_shadow_from_legacy_families(legacy)
    parity = build_shared_brain_parity(legacy=legacy, shared=shared)

    assert shared["status"] == "EVALUATED"
    assert shared["same_pit_family_snapshot_as_legacy"] is True
    assert shared["direction"] == "BULLISH"
    assert shared["direction_confidence"] == "STRONG"
    assert parity["full_thesis_agreement"] is True
    assert parity["divergence_reason"] == "NONE"
    assert parity["decision_effect"] == "NONE"
    assert shared["capital_committed"] == 0


def test_memory_only_second_confirmation_is_removed_and_labeled():
    legacy = legacy_shadow(local="BULLISH", memory="BULLISH")
    assert legacy["direction"] == "BULLISH"
    assert legacy["confidence"] == "MODERATE"

    shared = build_shared_shadow_from_legacy_families(legacy)
    parity = build_shared_brain_parity(legacy=legacy, shared=shared)

    assert shared["direction"] == "UNKNOWN"
    assert shared["direction_confidence"] == "WEAK"
    assert shared["families"]["DIRECTION_MEMORY"]["counts_for_direction"] is False
    assert shared["families"]["DIRECTION_MEMORY"]["role"] == "EXPERIENCE_CONTEXT"
    assert parity["direction_agreement"] is False
    assert parity["divergence_reason"] == "MEMORY_CONTEXT_ONLY_CORRECTION"
    assert parity["memory_policy"]["shared_memory_counts_as_independent_confirmation"] is False


def test_incomplete_parity_input_fails_open_for_research():
    shared = build_shared_shadow_from_legacy_families({"families": {}})
    parity = build_shared_brain_parity(legacy={}, shared=shared)

    assert shared["status"] == "UNAVAILABLE"
    assert shared["decision_effect"] == "NONE"
    assert shared["execution_effect"] == "NONE"
    assert shared["capital_committed"] == 0
    assert parity["status"] == "UNAVAILABLE"
    assert parity["divergence_reason"] == "SHARED_PARITY_UNAVAILABLE"
    assert parity["direction_agreement"] is None


def test_episode_payload_persists_parity_without_changing_legacy_columns():
    legacy = legacy_shadow(local="BULLISH", participation="BULLISH")
    shared = build_shared_shadow_from_legacy_families(legacy)
    parity = build_shared_brain_parity(legacy=legacy, shared=shared)
    result = {
        "mode": "CRUDE_OIL_MINI_CURRENT_MIND_LIVE_SHADOW_V2_OPTION_OI_NEWS",
        "symbol": "CRUDEOILM",
        "trade_instrument": "OPTIONS_ONLY",
        "click_at": "2026-09-07T10:00:00+05:30",
        "latest_completed_bar_available_at": "2026-09-07T10:00:00+05:30",
        "current_mind": {"action": "NO_TRADE", "direction": "BULLISH", "evidence_quality": "TEST"},
        "integrated_v2_shadow": legacy,
        "shared_commodity_brain_shadow_v1": shared,
        "shared_commodity_brain_parity_v1": parity,
        "execution": {
            "paper_signal_only": True,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "capital_committed": 0,
            "option_expression": None,
        },
        "journal": {"decision_fingerprint": "test-fingerprint"},
    }
    candles = [
        [f"2026-09-07T09:{minute:02d}:00+05:30", 100, 101, 99, 100 + minute / 100, 1]
        for minute in range(15)
    ]

    capture = build_episode_capture(result, candles, captured_at="2026-09-07T10:00:01+05:30")
    payload = json.loads(capture["payload"])

    assert capture["integrated_v2_direction"] == legacy["direction"]
    assert capture["integrated_v2_confidence"] == legacy["confidence"]
    assert capture["integrated_v2_decision_effect"] == "NONE"
    assert payload["decision"]["shared_commodity_brain_shadow_v1"]["direction"] == shared["direction"]
    assert payload["decision"]["shared_commodity_brain_parity_v1"]["decision_effect"] == "NONE"
    assert payload["capture_rule"] == "IMMUTABLE_DECISION_BEFORE_FORWARD_OUTCOME"
