from __future__ import annotations


KEY_SEGMENTS = ("immediate", "confirmation", "assimilation")
DIRECTIONAL = {"UP", "DOWN"}


def _band(normalized_abs_move) -> str:
    try:
        value = float(normalized_abs_move)
    except (TypeError, ValueError):
        return "VOLATILITY_REFERENCE_UNAVAILABLE"
    if value < 1.0:
        return "SUB_BASELINE"
    if value < 2.0:
        return "BASELINE_TO_2X"
    return "ELEVATED_2X_PLUS"


def assess_observed_path_materiality(
    observed_path: dict | None,
    volatility_context: dict | None,
) -> dict:
    """Audit how much fixed-floor path directions exceed prior same-clock motion.

    This is deliberately diagnostic. It does not recalculate or replace the observed
    path state. The existing fixed 0.05% floor remains untouched. Instead, each
    already-observed directional segment is compared with the prior same-clock median
    absolute move supplied by the volatility shadow. No news stance, trade outcome or
    P&L is read.
    """
    observed_path = observed_path if isinstance(observed_path, dict) else {}
    volatility_context = volatility_context if isinstance(volatility_context, dict) else {}
    directions = observed_path.get("directions") or {}
    moves = observed_path.get("moves_from_pre") or {}
    volatility_segments = volatility_context.get("segments") or {}

    segments = {}
    directional_segments = 0
    directional_with_reference = 0
    directional_at_or_above_baseline = 0
    directional_sub_baseline = 0

    for name in KEY_SEGMENTS:
        direction = directions.get(name) or "UNKNOWN"
        volatility = volatility_segments.get(name) or {}
        normalized = volatility.get("normalized_abs_move") if volatility.get("status") == "AVAILABLE" else None
        band = _band(normalized)
        directional = direction in DIRECTIONAL
        if directional:
            directional_segments += 1
            if band != "VOLATILITY_REFERENCE_UNAVAILABLE":
                directional_with_reference += 1
                if band == "SUB_BASELINE":
                    directional_sub_baseline += 1
                else:
                    directional_at_or_above_baseline += 1
        segments[name] = {
            "direction": direction,
            "move_from_pre": moves.get(name),
            "normalized_abs_move": normalized,
            "materiality_band": band,
            "fixed_floor_directional": directional,
            "at_or_above_prior_median": directional and band in {"BASELINE_TO_2X", "ELEVATED_2X_PLUS"},
        }

    if observed_path.get("observation_status") != "OBSERVED":
        state = "UNOBSERVED"
    elif directional_segments == 0:
        state = "NO_FIXED_FLOOR_DIRECTIONAL_SEGMENTS"
    elif directional_with_reference == 0:
        state = "VOLATILITY_REFERENCE_UNAVAILABLE"
    elif directional_with_reference < directional_segments:
        state = "PARTIAL_VOLATILITY_REFERENCE"
    elif directional_sub_baseline == 0:
        state = "ALL_DIRECTIONAL_SEGMENTS_AT_OR_ABOVE_BASELINE"
    elif directional_at_or_above_baseline == 0:
        state = "ALL_DIRECTIONAL_SEGMENTS_SUB_BASELINE"
    else:
        state = "MIXED_DIRECTIONAL_MATERIALITY"

    return {
        "mode": "MARKET_NEWS_PATH_MATERIALITY_SHADOW_V1",
        "outcome_blind": True,
        "shadow_only": True,
        "classification_unchanged": True,
        "observed_path_state": observed_path.get("path_state") or "UNOBSERVED",
        "materiality_state": state,
        "directional_segments": directional_segments,
        "directional_with_reference": directional_with_reference,
        "directional_at_or_above_baseline": directional_at_or_above_baseline,
        "directional_sub_baseline": directional_sub_baseline,
        "segments": segments,
        "reference_multiples": {"prior_median": 1.0, "elevated": 2.0},
        "rule": (
            "The existing observed path is not reclassified. This shadow only reports whether "
            "directions created by the fixed raw noise floor are small or large relative to prior "
            "same-clock market motion."
        ),
    }
