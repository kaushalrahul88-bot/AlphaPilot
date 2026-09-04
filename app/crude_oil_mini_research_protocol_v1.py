from __future__ import annotations


BASELINE_ID = "CRUDE_OIL_MINI_FROZEN_BASELINE_V1_2026_09_04"
PROTOCOL_ID = "CRUDE_OIL_MINI_EPISODE_OUTCOME_PROTOCOL_V1"
BASELINE_STATUS = "FROZEN"
EXPECTED_CURRENT_MIND_MODE = "CRUDE_OIL_MINI_CURRENT_MIND_LIVE_SHADOW_V2_OPTION_OI_NEWS"
OUTCOME_HORIZONS_MINUTES = (15, 30, 60, 120)
PRIMARY_OUTCOME_HORIZON_MINUTES = 120
ATR_PERIOD = 14
MISSED_MOVE_ATR_MULTIPLE = 1.5
MISSED_MOVE_OPPOSITE_MAX_ATR_MULTIPLE = 0.75

FROZEN_COMPONENTS = (
    "CRUDE_OIL_MINI_CURRENT_MIND",
    "CRUDE_OIL_MINI_OPTION_OI_PREMIUM_INTERPRETATION_V1",
    "CRUDE_OIL_MINI_OPTION_EXPRESSION_V1",
)
SHADOW_COMPONENTS = ("CRUDE_OIL_MINI_INTEGRATED_DIRECTION_V2",)


def baseline_manifest() -> dict:
    return {
        "baseline_id": BASELINE_ID,
        "protocol_id": PROTOCOL_ID,
        "status": BASELINE_STATUS,
        "frozen_components": list(FROZEN_COMPONENTS),
        "shadow_components": list(SHADOW_COMPONENTS),
        "outcome_horizons_minutes": list(OUTCOME_HORIZONS_MINUTES),
        "primary_outcome_horizon_minutes": PRIMARY_OUTCOME_HORIZON_MINUTES,
        "missed_move_diagnostic": {
            "basis": f"ATR{ATR_PERIOD}",
            "clean_move_threshold_atr": MISSED_MOVE_ATR_MULTIPLE,
            "opposite_excursion_max_atr": MISSED_MOVE_OPPOSITE_MAX_ATR_MULTIPLE,
            "decision_effect": "NONE",
        },
        "rules": [
            "Decision state is captured before any forward outcome is visible.",
            "Captured episode rows are immutable; future outcomes live in a separate table.",
            "Only first-seen PIT CRUDEOILM candles and immutable option observations may resolve outcomes.",
            "No historical backfill may be presented as prospective experience.",
            "Outcome and diagnosis have zero Current Mind or V2 decision effect in V1.",
            "Any future decision-rule change requires a new baseline id before prospective comparison.",
        ],
        "paper_signal_only": True,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
    }


def validate_baseline_result(result: dict) -> None:
    if str(result.get("mode") or "") != EXPECTED_CURRENT_MIND_MODE:
        raise ValueError("Current Mind mode does not match frozen Crude Mini baseline")
    if str(result.get("symbol") or "").upper() != "CRUDEOILM":
        raise ValueError("Frozen Crude Mini baseline accepts CRUDEOILM only")
    if str(result.get("trade_instrument") or "") != "OPTIONS_ONLY":
        raise ValueError("Frozen Crude Mini baseline is options-only")
    integrated = result.get("integrated_v2_shadow") or {}
    if str(integrated.get("decision_effect") or "NONE").upper() != "NONE":
        raise ValueError("Integrated V2 must remain shadow-only in frozen baseline")
    execution = result.get("execution") or {}
    if execution.get("paper_signal_only") is not True:
        raise ValueError("Frozen baseline requires paper_signal_only=true")
    if execution.get("live_execution_enabled") is not False:
        raise ValueError("Frozen baseline forbids live execution")
    if execution.get("broker_order_placement_enabled") is not False:
        raise ValueError("Frozen baseline forbids broker order placement")
