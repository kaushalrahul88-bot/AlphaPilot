from __future__ import annotations

from .crude_oil_mini_direction_memory import query_direction_memory
from .crude_oil_mini_point_in_time_context import latest_known_as_of

MODE = "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_SHADOW"
DIRECTIONAL = {"BULLISH", "BEARISH"}
INDEPENDENT_FAMILIES = (
    "LOCAL_STRUCTURE",
    "PARTICIPATION",
    "GLOBAL_CRUDE",
    "EVENT_REACTION",
    "DIRECTION_MEMORY",
)


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _direction_from_number(value) -> str:
    value = _f(value)
    if value is None or value == 0:
        return "UNKNOWN"
    return "BULLISH" if value > 0 else "BEARISH"


def _record_stance(record: dict | None) -> str:
    if not isinstance(record, dict):
        return "UNKNOWN"
    candidates = [record.get("stance"), record.get("direction")]
    value = record.get("value")
    if isinstance(value, dict):
        candidates.extend((value.get("stance"), value.get("direction")))
    for raw in candidates:
        stance = str(raw or "UNKNOWN").upper()
        if stance in DIRECTIONAL:
            return stance
    return "UNKNOWN"


def _record_flag(record: dict | None, *keys: str) -> bool:
    if not isinstance(record, dict):
        return False
    value = record.get("value")
    for key in keys:
        if bool(record.get(key)):
            return True
        if isinstance(value, dict) and bool(value.get(key)):
            return True
    return False


def _local_structure(snapshot: dict) -> dict:
    structure = str(snapshot.get("structure") or "UNKNOWN").upper()
    structure_stance = (
        "BULLISH" if structure == "UPTREND"
        else "BEARISH" if structure == "DOWNTREND"
        else "UNKNOWN"
    )
    momentum_15 = _direction_from_number(snapshot.get("return_15m_pct"))
    momentum_60 = _direction_from_number(snapshot.get("return_60m_pct"))
    directional_momentum = [stance for stance in (momentum_15, momentum_60) if stance in DIRECTIONAL]

    if structure_stance in DIRECTIONAL:
        opposing = [stance for stance in directional_momentum if stance != structure_stance]
        supporting = [stance for stance in directional_momentum if stance == structure_stance]
        if opposing:
            stance = "UNKNOWN"
            state = "INTERNAL_CONTRADICTION"
        elif supporting:
            stance = structure_stance
            state = "STRUCTURE_CONFIRMED_BY_MOMENTUM"
        else:
            stance = structure_stance
            state = "STRUCTURE_ONLY"
    elif len(directional_momentum) == 2 and len(set(directional_momentum)) == 1:
        stance = directional_momentum[0]
        state = "MOMENTUM_COHERENT_WITHOUT_STRUCTURE"
    else:
        stance = "UNKNOWN"
        state = "NO_COHERENT_LOCAL_DIRECTION"

    return {
        "family": "LOCAL_STRUCTURE",
        "independent": True,
        "counts_for_direction": stance in DIRECTIONAL,
        "stance": stance,
        "state": state,
        "detail": {
            "structure": structure,
            "return_15m_pct": _f(snapshot.get("return_15m_pct")),
            "return_60m_pct": _f(snapshot.get("return_60m_pct")),
            "momentum_15m": momentum_15,
            "momentum_60m": momentum_60,
        },
    }


def _participation(snapshot: dict, profile: dict) -> dict:
    relative = _f(snapshot.get("time_adjusted_relative_volume"))
    confirming = _f(profile.get("participation_confirming"))
    momentum = _direction_from_number(snapshot.get("return_15m_pct"))
    active = (
        relative is not None
        and confirming is not None
        and relative >= confirming
        and momentum in DIRECTIONAL
    )
    return {
        "family": "PARTICIPATION",
        "independent": True,
        "counts_for_direction": active,
        "stance": momentum if active else "UNKNOWN",
        "state": "DIRECTIONALLY_CONFIRMING" if active else "NOT_DIRECTIONALLY_CONFIRMING",
        "detail": {
            "time_adjusted_relative_volume": relative,
            "prior_confirming_level": confirming,
            "momentum_15m": momentum,
        },
    }


def _global_crude(latest: dict[str, dict]) -> dict:
    observations = []
    for series in ("WTI_CRUDE", "BRENT_CRUDE"):
        record = latest.get(series)
        stance = _record_stance(record)
        if stance in DIRECTIONAL:
            observations.append((series, stance))

    stances = {stance for _, stance in observations}
    if len(stances) > 1:
        stance = "UNKNOWN"
        state = "WTI_BRENT_CONTRADICTION"
    elif len(stances) == 1:
        stance = next(iter(stances))
        state = "WTI_BRENT_CONFIRMED" if len(observations) == 2 else "PARTIAL_GLOBAL_CRUDE"
    else:
        stance = "UNKNOWN"
        state = "GLOBAL_CRUDE_UNAVAILABLE_OR_NON_DIRECTIONAL"

    return {
        "family": "GLOBAL_CRUDE",
        "independent": True,
        "counts_for_direction": stance in DIRECTIONAL,
        "stance": stance,
        "state": state,
        "detail": {
            "directional_observations": [
                {"series": series, "stance": item_stance}
                for series, item_stance in observations
            ],
            "breadth": len(observations),
        },
    }


def _fx_translation(latest: dict[str, dict], global_crude: dict) -> dict:
    usd_inr = _record_stance(latest.get("USDINR"))
    global_stance = global_crude.get("stance")
    if usd_inr not in DIRECTIONAL or global_stance not in DIRECTIONAL:
        state = "UNRESOLVED"
    elif usd_inr == global_stance:
        state = "REINFORCES_GLOBAL_CRUDE"
    else:
        state = "OPPOSES_GLOBAL_CRUDE"
    return {
        "family": "FX_TRANSLATION",
        "independent": False,
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": state,
        "detail": {
            "global_crude_stance": global_stance,
            "usd_inr_stance": usd_inr,
            "rule": "USDINR modifies MCX translation context; it cannot independently create or reverse a Crude direction thesis.",
        },
    }


def _event_reaction(latest: dict[str, dict]) -> dict:
    directional = []
    contextual = []
    for series in ("EIA_CRUDE_INVENTORY", "OPEC_SUPPLY", "CRUDE_MACRO_RELEASE", "CRUDE_NEWS"):
        record = latest.get(series)
        if not record:
            continue
        stance = _record_stance(record)
        confirmed = _record_flag(record, "price_confirmed", "reaction_confirmed")
        row = {"series": series, "stance": stance, "price_reaction_confirmed": confirmed}
        if stance in DIRECTIONAL and confirmed:
            directional.append(row)
        else:
            contextual.append(row)

    stances = {row["stance"] for row in directional}
    if len(stances) > 1:
        stance = "UNKNOWN"
        state = "EVENT_REACTION_CONTRADICTED"
    elif len(stances) == 1:
        stance = next(iter(stances))
        state = "PRICE_CONFIRMED_EVENT_REACTION"
    else:
        stance = "UNKNOWN"
        state = "CONTEXT_ONLY_OR_UNAVAILABLE"
    return {
        "family": "EVENT_REACTION",
        "independent": True,
        "counts_for_direction": stance in DIRECTIONAL,
        "stance": stance,
        "state": state,
        "detail": {"directional": directional, "context_only": contextual},
    }


def _memory_family(memory_cases: list[dict], snapshot: dict, click_timestamp: str) -> dict:
    result = query_direction_memory(memory_cases, snapshot, click_timestamp)
    stance = str(result.get("stance") or "UNKNOWN").upper()
    return {
        "family": "DIRECTION_MEMORY",
        "independent": True,
        "counts_for_direction": stance in DIRECTIONAL,
        "stance": stance if stance in DIRECTIONAL else "UNKNOWN",
        "state": result.get("status"),
        "detail": result,
    }


def _thesis_from_families(families: list[dict]) -> dict:
    counted = [
        row for row in families
        if row.get("independent") and row.get("counts_for_direction") and row.get("stance") in DIRECTIONAL
    ]
    bullish = [row["family"] for row in counted if row["stance"] == "BULLISH"]
    bearish = [row["family"] for row in counted if row["stance"] == "BEARISH"]

    if bullish and bearish:
        return {
            "direction": "UNKNOWN",
            "confidence": "CONFLICTED",
            "state": "INDEPENDENT_FAMILY_CONTRADICTION",
            "supporting_families": [],
            "opposing_families": sorted(bullish + bearish),
        }

    supporting = bullish or bearish
    if len(supporting) < 2:
        return {
            "direction": "UNKNOWN",
            "confidence": "WEAK",
            "state": "INSUFFICIENT_INDEPENDENT_CONFIRMATION",
            "supporting_families": sorted(supporting),
            "opposing_families": [],
        }

    return {
        "direction": "BULLISH" if bullish else "BEARISH",
        "confidence": "STRONG" if len(supporting) >= 3 else "MODERATE",
        "state": "COHERENT_DIRECTION_THESIS",
        "supporting_families": sorted(supporting),
        "opposing_families": [],
    }


def evaluate_direction_brain_v2_shadow(
    *,
    click_timestamp: str,
    snapshot: dict,
    profile: dict,
    context_records: list[dict],
    direction_memory_cases: list[dict] | None = None,
) -> dict:
    """Build a non-voting Crude direction thesis without touching Current Mind.

    V2 deliberately separates direction from setup geometry. It can describe direction,
    persistence and contradictions, but it cannot produce BUY_CE/BUY_PE, entry readiness,
    stop/target levels, option selection or position size.
    """
    latest = latest_known_as_of(context_records or [], click_timestamp)
    local = _local_structure(snapshot)
    participation = _participation(snapshot, profile or {})
    global_crude = _global_crude(latest)
    event = _event_reaction(latest)
    memory = _memory_family(direction_memory_cases or [], snapshot, click_timestamp)
    families = [local, participation, global_crude, event, memory]
    fx = _fx_translation(latest, global_crude)
    thesis = _thesis_from_families(families)

    return {
        "mode": MODE,
        "research_only": True,
        "shadow_only": True,
        "decision_path_changed": False,
        "current_mind_action": None,
        "option_brain_action": None,
        "geometry_generated": False,
        "promotion_allowed": False,
        "click_timestamp": click_timestamp,
        "direction": thesis["direction"],
        "direction_confidence": thesis["confidence"],
        "thesis_state": thesis["state"],
        "supporting_families": thesis["supporting_families"],
        "opposing_families": thesis["opposing_families"],
        "families": {row["family"]: row for row in families},
        "modifiers": {"FX_TRANSLATION": fx},
        "persistence": (memory.get("detail") or {}).get("persistence", "UNRESOLVED"),
        "entry_readiness": "NOT_EVALUATED_DIRECTION_ONLY",
        "available_context_series": sorted(latest),
        "rules": [
            "No weighted indicator score is used.",
            "At least two independent directional families must align and no independent family may oppose them.",
            "WTI and Brent are one correlated GLOBAL_CRUDE family, never two votes.",
            "USDINR is a translation modifier and cannot independently create or reverse direction.",
            "Event/news direction counts only when an explicit point-in-time stance has price/reaction confirmation.",
            "Direction memory uses future underlying returns only after those horizons became historically available; it never uses trade geometry.",
            "Direction does not imply entry readiness or an option trade.",
        ],
        "validation": preregistration_contract(),
    }


def preregistration_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_SHADOW_PREREG_V1",
        "development_sample_end": "2026-08-31",
        "prospective_validation_not_before": "2026-09-03",
        "primary_horizon_minutes": 60,
        "secondary_horizons_minutes": [15, 30, 120],
        "primary_question": "Does the frozen shadow direction thesis improve 60-minute underlying directional discrimination out of sample?",
        "geometry_tuning_allowed": False,
        "option_pnl_tuning_allowed": False,
        "threshold_search_on_june_august_allowed": False,
        "current_mind_mutation_allowed": False,
        "promotion_allowed_from_june_august": False,
    }


def architecture_contract() -> dict:
    return {
        "mode": MODE,
        "research_only": True,
        "shadow_only": True,
        "current_mind_imported": False,
        "decision_effect": "NONE",
        "geometry_effect": "NONE",
        "option_effect": "NONE",
        "independent_direction_families": list(INDEPENDENT_FAMILIES),
        "correlated_global_crude_collapsed": True,
        "fx_is_modifier_only": True,
        "event_requires_price_reaction_confirmation": True,
        "direction_memory_geometry_independent": True,
    }
