from __future__ import annotations


DIRECTIONAL = {"UP", "DOWN"}


def _qualified_direction(raw_direction: str, materiality_segment: dict | None) -> str:
    if raw_direction not in DIRECTIONAL:
        return raw_direction if raw_direction in {"MUTED", "UNKNOWN"} else "UNKNOWN"
    segment = materiality_segment if isinstance(materiality_segment, dict) else {}
    if segment.get("at_or_above_prior_median") is True:
        return raw_direction
    if segment.get("materiality_band") == "SUB_BASELINE":
        return "MUTED"
    return "UNKNOWN"


def _state(immediate: str, assimilation: str) -> str:
    if immediate == "UNKNOWN" or assimilation == "UNKNOWN":
        return "UNOBSERVED"
    if immediate == "MUTED" and assimilation == "MUTED":
        return "MUTED_PATH"
    if immediate == "UP" and assimilation == "UP":
        return "UP_FOLLOW_THROUGH"
    if immediate == "DOWN" and assimilation == "DOWN":
        return "DOWN_FOLLOW_THROUGH"
    if immediate == "UP" and assimilation == "DOWN":
        return "UP_THEN_REVERSED"
    if immediate == "DOWN" and assimilation == "UP":
        return "DOWN_THEN_REVERSED"
    if immediate == "MUTED" and assimilation == "UP":
        return "DELAYED_UP"
    if immediate == "MUTED" and assimilation == "DOWN":
        return "DELAYED_DOWN"
    return "FADED_OR_MIXED_PATH"


def assess_materiality_qualified_path(
    observed_path: dict | None,
    path_materiality: dict | None,
) -> dict:
    """Shadow the observed path after requiring directional moves to clear normal motion.

    The production observed-path classifier is not changed. A raw UP/DOWN segment is
    retained only when the existing path-materiality shadow says it is at or above the
    prior same-clock median absolute move. A raw directional segment below that median
    becomes MUTED in this shadow. Missing volatility reference fails closed to UNKNOWN.
    No headline stance, trade outcome, P&L, or trade action is read.
    """
    observed_path = observed_path if isinstance(observed_path, dict) else {}
    path_materiality = path_materiality if isinstance(path_materiality, dict) else {}
    raw_directions = observed_path.get("directions") or {}
    materiality_segments = path_materiality.get("segments") or {}

    if observed_path.get("observation_status") != "OBSERVED":
        return {
            "mode": "MARKET_NEWS_MATERIALITY_QUALIFIED_PATH_SHADOW_V1",
            "outcome_blind": True,
            "shadow_only": True,
            "classification_unchanged": True,
            "observation_status": "INCOMPLETE",
            "raw_path_state": observed_path.get("path_state") or "UNOBSERVED",
            "qualified_path_state": "UNOBSERVED",
            "raw_directions": {name: raw_directions.get(name, "UNKNOWN") for name in ("immediate", "confirmation", "assimilation")},
            "qualified_directions": {name: "UNKNOWN" for name in ("immediate", "confirmation", "assimilation")},
            "direction_changes": 0,
            "path_state_changed": False,
            "rule": "Materiality qualification is shadow-only and cannot change production classification or trading behavior.",
        }

    qualified = {
        name: _qualified_direction(raw_directions.get(name, "UNKNOWN"), materiality_segments.get(name))
        for name in ("immediate", "confirmation", "assimilation")
    }
    raw = {name: raw_directions.get(name, "UNKNOWN") for name in qualified}
    qualified_state = _state(qualified["immediate"], qualified["assimilation"])
    raw_state = observed_path.get("path_state") or "UNOBSERVED"
    direction_changes = sum(raw[name] != qualified[name] for name in qualified)

    return {
        "mode": "MARKET_NEWS_MATERIALITY_QUALIFIED_PATH_SHADOW_V1",
        "outcome_blind": True,
        "shadow_only": True,
        "classification_unchanged": True,
        "observation_status": "OBSERVED",
        "raw_path_state": raw_state,
        "qualified_path_state": qualified_state,
        "raw_directions": raw,
        "qualified_directions": qualified,
        "direction_changes": direction_changes,
        "path_state_changed": qualified_state != raw_state,
        "qualification_reference": "PRIOR_SAME_CLOCK_MEDIAN_ABSOLUTE_MOVE_1X",
        "rule": (
            "Retain an existing raw UP/DOWN segment only when it is at or above the prior "
            "same-clock median absolute move; sub-baseline directional segments become MUTED; "
            "missing reference becomes UNKNOWN. This is diagnostic only."
        ),
    }
