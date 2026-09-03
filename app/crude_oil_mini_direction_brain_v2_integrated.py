from __future__ import annotations

from .crude_oil_mini_direction_memory import query_direction_memory
from .crude_oil_mini_evidence_dependency import audit_directional_independence
from .crude_oil_mini_event_lifecycle import event_lifecycle_view
from .crude_oil_mini_event_reaction_v2 import EVENT_SERIES, build_event_reaction_family_from_lifecycle
from .crude_oil_mini_global_crude_perception_v2 import build_global_crude_perception
from .crude_oil_mini_participation_v2 import build_participation_observation
from .crude_oil_mini_point_in_time_context import latest_known_as_of

MODE = "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_INTEGRATED_SHADOW"
DIRECTIONAL = {"BULLISH", "BEARISH"}
PRIMARY_FAMILIES = (
    "LOCAL_STRUCTURE",
    "PARTICIPATION",
    "GLOBAL_CRUDE",
    "EVENT_REACTION",
    "DIRECTION_MEMORY",
)


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
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


def _local_structure(snapshot: dict) -> dict:
    structure = str(snapshot.get("structure") or "UNKNOWN").upper()
    structure_stance = (
        "BULLISH" if structure == "UPTREND"
        else "BEARISH" if structure == "DOWNTREND"
        else "UNKNOWN"
    )
    momentum_15 = _direction_from_number(snapshot.get("return_15m_pct"))
    momentum_60 = _direction_from_number(snapshot.get("return_60m_pct"))
    momentum = [stance for stance in (momentum_15, momentum_60) if stance in DIRECTIONAL]

    if structure_stance in DIRECTIONAL:
        opposing = [stance for stance in momentum if stance != structure_stance]
        supporting = [stance for stance in momentum if stance == structure_stance]
        if opposing:
            stance = "UNKNOWN"
            state = "INTERNAL_CONTRADICTION"
        elif supporting:
            stance = structure_stance
            state = "STRUCTURE_CONFIRMED_BY_MOMENTUM"
        else:
            stance = structure_stance
            state = "STRUCTURE_ONLY"
    elif len(momentum) == 2 and len(set(momentum)) == 1:
        stance = momentum[0]
        state = "MOMENTUM_COHERENT_WITHOUT_STRUCTURE"
    else:
        stance = "UNKNOWN"
        state = "NO_COHERENT_LOCAL_DIRECTION"

    directional = stance in DIRECTIONAL
    return {
        "family": "LOCAL_STRUCTURE",
        "causal_origin": "LOCAL_PRICE_STRUCTURE",
        "independence_status": "INDEPENDENT" if directional else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": directional,
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


def _missing_participation() -> dict:
    return {
        "family": "PARTICIPATION",
        "causal_origin": "POSITIONING_FLOW",
        "independence_status": "NOT_DIRECTIONAL",
        "depends_on": [],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": "RAW_MINI_CANDLES_NOT_SUPPLIED",
        "detail": {
            "legacy_price_volume_fallback_allowed": False,
            "reason": "Missing OI/acceptance evidence must remain missing rather than reverting to the old price-derived vote.",
        },
    }


def _memory_family(memory_cases: list[dict], snapshot: dict, click_timestamp: str) -> dict:
    result = query_direction_memory(memory_cases or [], snapshot, click_timestamp)
    stance = str(result.get("stance") or "UNKNOWN").upper()
    directional = stance in DIRECTIONAL
    return {
        "family": "DIRECTION_MEMORY",
        "causal_origin": "HISTORICAL_ANALOGUE",
        "independence_status": "INDEPENDENT" if directional else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on": [],
        "counts_for_direction": directional,
        "stance": stance if directional else "UNKNOWN",
        "state": result.get("status") or "UNKNOWN",
        "detail": result,
    }


def _fx_translation(context_records: list[dict], click_timestamp: str, global_crude: dict) -> dict:
    latest = latest_known_as_of(context_records or [], click_timestamp)
    usd_inr = _record_stance(latest.get("USDINR"))
    global_stance = str(global_crude.get("stance") or "UNKNOWN").upper()

    if usd_inr not in DIRECTIONAL or global_stance not in DIRECTIONAL:
        state = "UNRESOLVED"
    elif usd_inr == global_stance:
        state = "REINFORCES_GLOBAL_CRUDE_TRANSLATION"
    else:
        state = "OFFSETS_GLOBAL_CRUDE_TRANSLATION"

    return {
        "family": "FX_TRANSLATION",
        "causal_origin": "CURRENCY_TRANSLATION",
        "independence_status": "MODIFIER_ONLY",
        "depends_on": ["CROSS_MARKET_CRUDE"],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": state,
        "detail": {
            "global_crude_stance": global_stance,
            "usd_inr_stance": usd_inr,
            "rule": "USDINR changes MCX translation context but cannot create or reverse the underlying Crude direction thesis by itself.",
        },
    }


def _event_family(event_records: list[dict], click_timestamp: str) -> tuple[dict, dict]:
    records = [
        row for row in (event_records or [])
        if isinstance(row, dict) and str(row.get("series") or "").upper() in EVENT_SERIES
    ]
    lifecycle = event_lifecycle_view(records, click_timestamp)
    family = build_event_reaction_family_from_lifecycle(lifecycle)
    return family, lifecycle


def _thesis_from_families(families: list[dict]) -> dict:
    audit = audit_directional_independence(families)
    counted = audit["counted"]
    bullish = [row["family"] for row in counted if row.get("stance") == "BULLISH"]
    bearish = [row["family"] for row in counted if row.get("stance") == "BEARISH"]

    if bullish and bearish:
        return {
            "direction": "UNKNOWN",
            "confidence": "CONFLICTED",
            "state": "INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION",
            "supporting_families": [],
            "opposing_families": sorted(bullish + bearish),
            "dependency_audit": audit,
        }

    supporting = bullish or bearish
    if len(supporting) < 2:
        return {
            "direction": "UNKNOWN",
            "confidence": "WEAK",
            "state": "INSUFFICIENT_INDEPENDENT_CONFIRMATION",
            "supporting_families": sorted(supporting),
            "opposing_families": [],
            "dependency_audit": audit,
        }

    return {
        "direction": "BULLISH" if bullish else "BEARISH",
        "confidence": "STRONG" if len(supporting) >= 3 else "MODERATE",
        "state": "COHERENT_DIRECTION_THESIS",
        "supporting_families": sorted(supporting),
        "opposing_families": [],
        "dependency_audit": audit,
    }


def evaluate_integrated_direction_brain_v2_shadow(
    *,
    click_timestamp: str,
    snapshot: dict,
    profile: dict,
    context_records: list[dict] | None = None,
    global_context_probe: dict | None = None,
    crude_candles=None,
    event_records: list[dict] | None = None,
    direction_memory_cases: list[dict] | None = None,
    participation_observation: dict | None = None,
) -> dict:
    """Compose the redesigned Crude Direction Brain V2 in research-only shadow mode.

    This function deliberately stops at an underlying direction thesis. It cannot
    produce BUY_CE/BUY_PE, entry readiness, geometry, option selection, position size,
    or any Current Mind mutation.
    """
    local = _local_structure(snapshot or {})

    if participation_observation is not None:
        participation = dict(participation_observation)
    elif crude_candles is not None:
        participation = build_participation_observation(
            crude_candles,
            click_timestamp=click_timestamp,
            snapshot=snapshot or {},
            profile=profile or {},
        )
    else:
        participation = _missing_participation()

    participation.setdefault("family", "PARTICIPATION")
    participation.setdefault("causal_origin", "POSITIONING_FLOW")
    participation.setdefault("independence_status", "NOT_DIRECTIONAL")
    participation.setdefault("depends_on", [])
    participation.setdefault("counts_for_direction", False)
    participation.setdefault("stance", "UNKNOWN")

    global_crude = build_global_crude_perception(global_context_probe or {}, click_timestamp)
    event, lifecycle = _event_family(
        event_records if event_records is not None else (context_records or []),
        click_timestamp,
    )
    memory = _memory_family(direction_memory_cases or [], snapshot or {}, click_timestamp)

    families = [local, participation, global_crude, event, memory]
    thesis = _thesis_from_families(families)
    fx = _fx_translation(context_records or [], click_timestamp, global_crude)

    latest = latest_known_as_of(context_records or [], click_timestamp)
    probe_feeds = (global_context_probe or {}).get("feeds") or {}
    available_global = sorted(
        series for series, feed in probe_feeds.items()
        if isinstance(feed, dict) and feed.get("status") == "AVAILABLE"
    )

    return {
        "mode": MODE,
        "research_only": True,
        "shadow_only": True,
        "decision_path_changed": False,
        "current_mind_action": None,
        "option_brain_action": None,
        "geometry_generated": False,
        "entry_readiness": "NOT_EVALUATED_DIRECTION_ONLY",
        "promotion_allowed": False,
        "click_timestamp": click_timestamp,
        "direction": thesis["direction"],
        "direction_confidence": thesis["confidence"],
        "thesis_state": thesis["state"],
        "supporting_families": thesis["supporting_families"],
        "opposing_families": thesis["opposing_families"],
        "families": {row["family"]: row for row in families},
        "modifiers": {"FX_TRANSLATION": fx},
        "dependency_audit": thesis["dependency_audit"],
        "event_lifecycle": lifecycle,
        "persistence": (memory.get("detail") or {}).get("persistence", "UNRESOLVED"),
        "available_context_series": sorted(set(latest) | set(available_global)),
        "rules": [
            "No weighted indicator score is used.",
            "At least two unique independent primary causal origins must align; any counted opposite origin forces UNKNOWN/CONFLICTED.",
            "Price plus volume cannot masquerade as independent participation when its direction comes from local price momentum.",
            "Participation can vote only from independent positioning/commitment plus acceptance evidence.",
            "Global Crude uses completed WTI/Brent market structure and multi-hour momentum; a single latest hourly sign cannot vote.",
            "WTI and Brent are one correlated GLOBAL_CRUDE family, never two votes.",
            "Historical event visibility is separate from active relevance; Event Reaction consumes the PIT event lifecycle.",
            "Headline sentiment cannot create an event vote; mechanism, materiality, novelty and confirmed PIT reaction are required.",
            "Event rejection removes the event vote and never automatically creates the opposite direction.",
            "USDINR is a translation modifier only and cannot create or reverse the underlying direction thesis.",
            "Direction Memory remains geometry-independent and may use only historically matured cases.",
            "Direction never implies setup confirmation, entry readiness, option selection or execution.",
        ],
        "integration_contract": integration_contract(),
    }


def integration_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_CLEAN_INTEGRATION_V1",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_brain_effect": "NONE",
        "independent_primary_families": list(PRIMARY_FAMILIES),
        "causal_origin_deduplication": True,
        "legacy_price_volume_participation_fallback_allowed": False,
        "global_crude_single_hour_sign_vote_allowed": False,
        "wti_brent_count_as_one_family": True,
        "event_lifecycle_required": True,
        "headline_sentiment_vote_allowed": False,
        "event_rejection_reverse_vote_allowed": False,
        "fx_independent_vote_allowed": False,
        "inspected_august_threshold_search_allowed": False,
        "diagnostic_replay_protocol_frozen_here": False,
        "diagnostic_replay_requires_separate_approval": True,
        "prospective_schedule_defined_here": False,
        "fixed_direction_horizon_defined_here": False,
        "promotion_allowed": False,
    }
