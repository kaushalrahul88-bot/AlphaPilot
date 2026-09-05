from app.crude_oil_mini_commodity_brain_shadow_v1 import (
    integration_contract,
    synthesize_crude_shared_families,
)
from app.crude_oil_mini_direction_brain_v2_integrated import _thesis as legacy_thesis


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


def base_families():
    return {
        "local": family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE"),
        "participation": family("PARTICIPATION", "OPTION_OI_PREMIUM_FLOW"),
        "global_crude": family("GLOBAL_CRUDE", "CROSS_MARKET_CRUDE"),
        "event": family("EVENT_REACTION", "EXOGENOUS_INFORMATION"),
        "memory": family("DIRECTION_MEMORY", "HISTORICAL_ANALOGUE"),
    }


def synth(**overrides):
    rows = base_families()
    rows.update(overrides)
    return synthesize_crude_shared_families(**rows)


def test_two_aligned_independent_origins_are_moderate():
    result = synth(
        local=family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH", counts=True),
        participation=family("PARTICIPATION", "OPTION_OI_PREMIUM_FLOW", "BULLISH", counts=True),
    )
    thesis = result["thesis"]
    assert thesis["direction"] == "BULLISH"
    assert thesis["confidence"] == "MODERATE"
    assert set(thesis["supporting_families"]) == {"LOCAL_STRUCTURE", "PARTICIPATION"}


def test_three_aligned_origins_are_strong():
    result = synth(
        local=family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH", counts=True),
        participation=family("PARTICIPATION", "OPTION_OI_PREMIUM_FLOW", "BULLISH", counts=True),
        global_crude=family("GLOBAL_CRUDE", "CROSS_MARKET_CRUDE", "BULLISH", counts=True),
    )
    thesis = result["thesis"]
    assert thesis["direction"] == "BULLISH"
    assert thesis["confidence"] == "STRONG"


def test_opposing_independent_origin_forces_conflict():
    result = synth(
        local=family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH", counts=True),
        global_crude=family("GLOBAL_CRUDE", "CROSS_MARKET_CRUDE", "BEARISH", counts=True),
    )
    thesis = result["thesis"]
    assert thesis["direction"] == "UNKNOWN"
    assert thesis["confidence"] == "CONFLICTED"


def test_memory_cannot_manufacture_second_confirmation():
    result = synth(
        local=family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH", counts=True),
        memory=family("DIRECTION_MEMORY", "HISTORICAL_ANALOGUE", "BULLISH", counts=True),
    )
    thesis = result["thesis"]
    memory = next(row for row in result["families"] if row["family"] == "DIRECTION_MEMORY")
    assert thesis["direction"] == "UNKNOWN"
    assert thesis["confidence"] == "WEAK"
    assert thesis["supporting_families"] == ["LOCAL_STRUCTURE"]
    assert memory["stance"] == "BULLISH"
    assert memory["counts_for_direction"] is False
    assert memory["role"] == "EXPERIENCE_CONTEXT"
    assert memory["depends_on_origins"] == ["LOCAL_PRICE_STRUCTURE"]
    assert "DIRECTION_MEMORY" not in thesis["dependency_audit"]["counted_families"]


def test_shared_core_matches_legacy_when_memory_is_not_decisive():
    rows = base_families()
    rows["local"] = family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH", counts=True)
    rows["participation"] = family("PARTICIPATION", "OPTION_OI_PREMIUM_FLOW", "BULLISH", counts=True)
    rows["global_crude"] = family("GLOBAL_CRUDE", "CROSS_MARKET_CRUDE", "BULLISH", counts=True)
    shared = synthesize_crude_shared_families(**rows)["thesis"]
    legacy = legacy_thesis(list(rows.values()))
    assert shared["direction"] == legacy["direction"] == "BULLISH"
    assert shared["confidence"] == legacy["confidence"] == "STRONG"
    assert shared["supporting_families"] == legacy["supporting_families"]


def test_event_dependency_is_still_suppressed_by_shared_core():
    result = synth(
        local=family("LOCAL_STRUCTURE", "LOCAL_PRICE_STRUCTURE", "BULLISH", counts=True),
        event=family(
            "EVENT_REACTION",
            "EXOGENOUS_INFORMATION",
            "BULLISH",
            counts=True,
            dependencies=["LOCAL_PRICE_STRUCTURE"],
        ),
    )
    thesis = result["thesis"]
    assert thesis["direction"] == "UNKNOWN"
    assert thesis["confidence"] == "WEAK"
    assert thesis["supporting_families"] == ["LOCAL_STRUCTURE"]
    assert any(
        row["family"] == "EVENT_REACTION" and row["reason"] == "DEPENDENT_ON_COUNTED_CAUSAL_ORIGIN"
        for row in thesis["dependency_audit"]["suppressed"]
    )


def test_contract_is_shadow_only_and_does_not_change_execution():
    contract = integration_contract()
    assert contract["research_only"] is True
    assert contract["shadow_only"] is True
    assert contract["legacy_module_modified"] is False
    assert contract["legacy_outputs_rewritten"] is False
    assert contract["direction_memory_counts_as_independent_confirmation"] is False
    assert contract["current_mind_effect"] == "NONE"
    assert contract["geometry_effect"] == "NONE"
    assert contract["option_brain_effect"] == "NONE"
    assert contract["execution_effect"] == "NONE"
    assert contract["promotion_allowed"] is False
