from __future__ import annotations


def _price(snapshot: dict | None) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    for key in ("price", "close", "last_price"):
        try:
            value = snapshot.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _return_from(base: float, value: float) -> float | None:
    if base == 0:
        return None
    return (value - base) / abs(base)


def _direction(value: float | None, noise_floor: float) -> str:
    if value is None:
        return "UNKNOWN"
    if value > noise_floor:
        return "UP"
    if value < -noise_floor:
        return "DOWN"
    return "MUTED"


def assess_observed_market_path(
    pre_event: dict | None,
    immediate: dict | None,
    confirmation: dict | None = None,
    assimilation: dict | None = None,
    *,
    noise_floor: float = 0.0005,
) -> dict:
    """Describe the observed post-news price path without interpreting headline direction.

    This layer deliberately does not read news stance, trade action, outcome or P&L.
    It answers only what the market did after an event. Directional news-hypothesis
    confirmation remains the responsibility of ``market_news_reaction_engine``.
    """
    if noise_floor < 0:
        raise ValueError("noise_floor must be non-negative")

    pre = _price(pre_event)
    imm = _price(immediate)
    conf = _price(confirmation)
    assim = _price(assimilation)

    if pre is None or imm is None or assim is None or pre == 0:
        return {
            "mode": "MARKET_NEWS_OBSERVED_PATH_V1",
            "outcome_blind": True,
            "observation_status": "INCOMPLETE",
            "path_state": "UNOBSERVED",
            "noise_floor": noise_floor,
            "moves_from_pre": {"immediate": None, "confirmation": None, "assimilation": None},
            "directions": {"immediate": "UNKNOWN", "confirmation": "UNKNOWN", "assimilation": "UNKNOWN"},
            "rule": "Observed market path is independent of news stance and trade outcome.",
        }

    moves = {
        "immediate": _return_from(pre, imm),
        "confirmation": _return_from(pre, conf) if conf is not None else None,
        "assimilation": _return_from(pre, assim),
    }
    directions = {name: _direction(value, noise_floor) for name, value in moves.items()}
    immediate_direction = directions["immediate"]
    assimilation_direction = directions["assimilation"]

    if immediate_direction == "MUTED" and assimilation_direction == "MUTED":
        state = "MUTED_PATH"
    elif immediate_direction == "UP" and assimilation_direction == "UP":
        state = "UP_FOLLOW_THROUGH"
    elif immediate_direction == "DOWN" and assimilation_direction == "DOWN":
        state = "DOWN_FOLLOW_THROUGH"
    elif immediate_direction == "UP" and assimilation_direction == "DOWN":
        state = "UP_THEN_REVERSED"
    elif immediate_direction == "DOWN" and assimilation_direction == "UP":
        state = "DOWN_THEN_REVERSED"
    elif immediate_direction == "MUTED" and assimilation_direction == "UP":
        state = "DELAYED_UP"
    elif immediate_direction == "MUTED" and assimilation_direction == "DOWN":
        state = "DELAYED_DOWN"
    else:
        state = "FADED_OR_MIXED_PATH"

    return {
        "mode": "MARKET_NEWS_OBSERVED_PATH_V1",
        "outcome_blind": True,
        "observation_status": "OBSERVED",
        "path_state": state,
        "noise_floor": noise_floor,
        "moves_from_pre": moves,
        "directions": directions,
        "rule": "Observed market path is independent of news stance and trade outcome.",
    }
