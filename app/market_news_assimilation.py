from __future__ import annotations


FOLLOW_THROUGH = {"UP_FOLLOW_THROUGH", "DOWN_FOLLOW_THROUGH"}
REVERSALS = {"UP_THEN_REVERSED", "DOWN_THEN_REVERSED"}
DELAYED = {"DELAYED_UP", "DELAYED_DOWN"}


def _motion_band(volatility_context: dict | None) -> tuple[str, float | None]:
    if not isinstance(volatility_context, dict):
        return "UNAVAILABLE", None
    segment = (volatility_context.get("segments") or {}).get("assimilation") or {}
    if segment.get("status") != "AVAILABLE":
        return "UNAVAILABLE", None
    try:
        ratio = float(segment.get("normalized_abs_move"))
    except (TypeError, ValueError):
        return "UNAVAILABLE", None
    if ratio < 1.0:
        return "BELOW_PRIOR_MEDIAN", ratio
    if ratio < 2.0:
        return "PRIOR_MEDIAN_TO_2X", ratio
    return "AT_LEAST_2X_PRIOR_MEDIAN", ratio


def assess_market_news_assimilation(
    observed_path: dict | None,
    volatility_context: dict | None,
    participation: dict | None = None,
) -> dict:
    """Summarize what the market did after news without turning it into a trade signal.

    The observed price path remains primary. Same-clock volatility contributes only a
    transparent magnitude descriptor: below the prior median, 1x-2x the prior median,
    or at least 2x. Those reference multiples are descriptive and are not fitted to
    trade outcomes. Participation is carried as context but cannot manufacture a path.
    """
    observed_path = observed_path if isinstance(observed_path, dict) else {}
    participation = participation if isinstance(participation, dict) else {}
    path_state = observed_path.get("path_state") or "UNOBSERVED"
    observation_status = observed_path.get("observation_status") or "INCOMPLETE"
    motion_band, normalized_move = _motion_band(volatility_context)
    participation_state = participation.get("participation_state") or "UNKNOWN"

    if observation_status != "OBSERVED" or path_state == "UNOBSERVED":
        state = "UNOBSERVED"
    elif path_state in FOLLOW_THROUGH:
        if motion_band == "AT_LEAST_2X_PRIOR_MEDIAN":
            state = "DIRECTIONAL_FOLLOW_THROUGH_HIGHLY_ELEVATED"
        elif motion_band == "PRIOR_MEDIAN_TO_2X":
            state = "DIRECTIONAL_FOLLOW_THROUGH_AT_OR_ABOVE_BASELINE"
        elif motion_band == "BELOW_PRIOR_MEDIAN":
            state = "DIRECTIONAL_FOLLOW_THROUGH_SUB_BASELINE"
        else:
            state = "DIRECTIONAL_FOLLOW_THROUGH_VOLATILITY_UNKNOWN"
    elif path_state in REVERSALS:
        state = "REVERSAL_COUNTERFORCE"
    elif path_state in DELAYED:
        state = "DELAYED_ASSIMILATION"
    elif path_state == "MUTED_PATH":
        state = "MUTED_ASSIMILATION"
    else:
        state = "FADED_OR_MIXED_ASSIMILATION"

    return {
        "mode": "MARKET_NEWS_ASSIMILATION_SHADOW_V1",
        "outcome_blind": True,
        "shadow_only": True,
        "classification_unchanged": True,
        "assimilation_state": state,
        "path_state": path_state,
        "motion_band": motion_band,
        "assimilation_normalized_abs_move": normalized_move,
        "participation_state": participation_state,
        "reference_multiples": {"prior_median": 1.0, "highly_elevated": 2.0},
        "rule": (
            "Observed price path is summarized independently of headline stance; same-clock "
            "volatility and participation are descriptive context only and cannot generate a trade."
        ),
    }
