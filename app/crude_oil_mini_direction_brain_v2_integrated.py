from __future__ import annotations

from .crude_oil_mini_direction_memory import query_direction_memory
from .crude_oil_mini_evidence_dependency import audit_directional_independence
from .crude_oil_mini_event_reaction_v3 import build_event_reaction_family
from .crude_oil_mini_global_crude_perception_v2 import build_global_crude_perception
from .crude_oil_mini_participation_v2 import build_participation_observation
from .crude_oil_mini_point_in_time_context import latest_known_as_of

MODE = "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_INTEGRATED_SHADOW"
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
        "depends_on_origins": [],
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


def _memory_family(memory_cases: list[dict], snapshot: dict, click_timestamp: str) -> dict:
    result = query_direction_memory(memory_cases, snapshot, click_timestamp)
    stance = str(result.get("stance") or "UNKNOWN").upper()
    directional = stance in DIRECTIONAL
    return {
        "family": "DIRECTION_MEMORY",
        "causal_origin": "HISTORICAL_ANALOGUE",
        "independence_status": "INDEPENDENT" if directional else "INDEPENDENT_CONTEXT_ONLY",
        "depends_on_origins": [],
        "counts_for_direction": directional,
        "stance": stance if directional else "UNKNOWN",
        "state": result.get("status"),
        "detail": result,
    }


def _fx_translation(context_records: list[dict], click_timestamp: str, global_crude: dict) -> dict:
    latest = latest_known_as_of(context_records or [], click_timestamp)
    usd_inr = _record_stance(latest.get("USDINR"))
    global_stance = str(global_crude.get("stance") or "UNKNOWN").upper()
    if usd_inr not in DIRECTIONAL or global_stance not in DIRECTIONAL:
        state = "UNRESOLVED"
    elif usd_inr == global_stance:
        state = "REINFORCES_GLOBAL_CRUDE"
    else:
        state = "OPPOSES_GLOBAL_CRUDE"
    return {
        "family": "FX_TRANSLATION",
        "causal_origin": "CURRENCY_TRANSLATION",
        "independence_status": "MODIFIER_ONLY",
        "depends_on_origins": ["CROSS_MARKET_CRUDE"],
        "counts_for_direction": False,
        "stance": "UNKNOWN",
        "state": state,
        "detail": {
            "global_crude_stance": global_stance,
            "usd_inr_stance": usd_inr,
            "rule": "USDINR is translation context only; it cannot create or reverse the underlying Crude direction thesis.",
        },
    }


def _thesis(families: list[dict]) -> dict:
    dependency = audit_directional_independence(families)
    counted = dependency["counted"]
    bullish = [row["family"] for row in counted if row.get("stance") == "BULLISH"]
    bearish = [row["family"] for row in counted if row.get("stance") == "BEARISH"]

    if bullish and bearish:
        return {
            "direction": "UNKNOWN",
            "confidence": "CONFLICTED",
            "state": "INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION",
            "supporting_families": [],
            "opposing_families": sorted(bullish + bearish),
            "dependency_audit": dependency,
        }

    supporting = bullish or bearish
    if len(supporting) < 2:
        return {
            "direction": "UNKNOWN",
            "confidence": "WEAK",
            "state": "INSUFFICIENT_INDEPENDENT_CONFIRMATION",
            "supporting_families": sorted(supporting),
            "opposing_families": [],
            "dependency_audit": dependency,
        }

    return {
        "direction": "BULLISH" if bullish else "BEARISH",
        "confidence": "STRONG" if len(supporting) >= 3 else "MODERATE",
        "state": "COHERENT_DIRECTION_THESIS",
        "supporting_families": sorted(supporting),
        "opposing_families": [],
        "dependency_audit": dependency,
    }


def evaluate_integrated_direction_v2_shadow(
    *,
    click_timestamp: str,
    snapshot: dict,
    profile: dict,
    mini_candles,
    global_context_probe: dict,
    context_records: list[dict] | None = None,
    event_records: list[dict] | None = None,
    direction_memory_cases: list[dict] | None = None,
    option_positioning: dict | None = None,
) -> dict:
    """Build the redesigned direction thesis without changing any trading decision.

    Option OI is admitted as PIT positioning context for the options-only system,
    but raw OI is not turned into a directional vote without a preregistered causal
    rule. This integration remains shadow-only.
    """
    local = _local_structure(snapshot or {})
    participation = build_participation_observation(
        mini_candles,
        click_timestamp=click_timestamp,
        snapshot=snapshot or {},
        profile=profile or {},
        option_positioning=option_positioning or {},
    )
    global_crude = build_global_crude_perception(global_context_probe or {}, click_timestamp)
    event = build_event_reaction_family(event_records or [], click_timestamp)
    memory = _memory_family(direction_memory_cases or [], snapshot or {}, click_timestamp)
    families = [local, participation, global_crude, event, memory]
    thesis = _thesis(families)
    fx = _fx_translation(context_records or [], click_timestamp, global_crude)

    return {
        "mode": MODE,
        "research_only": True,
        "shadow_only": True,
        "decision_path_changed": False,
        "current_mind_action": None,
        "geometry_generated": False,
        "option_brain_action": None,
        "promotion_allowed": False,
        "click_timestamp": click_timestamp,
        "direction": thesis["direction"],
        "direction_confidence": thesis["confidence"],
        "thesis_state": thesis["state"],
        "supporting_families": thesis["supporting_families"],
        "opposing_families": thesis["opposing_families"],
        "dependency_audit": thesis["dependency_audit"],
        "families": {row["family"]: row for row in families},
        "modifiers": {"FX_TRANSLATION": fx},
        "persistence": (memory.get("detail") or {}).get("persistence", "UNRESOLVED"),
        "entry_readiness": "NOT_EVALUATED_DIRECTION_ONLY",
        "rules": [
            "No weighted indicator score is used.",
            "At least two independent causal origins must align and no counted independent origin may oppose them.",
            "Option-chain OI is primary positioning context for this options-only system; futures OI is optional supporting context.",
            "Raw option OI is descriptive until a separate causal OI-plus-premium rule is preregistered; it cannot vote by itself.",
            "Price plus volume alone cannot make Participation an independent directional vote.",
            "WTI and Brent remain one correlated GLOBAL_CRUDE family and both must qualify before it votes.",
            "An Event vote requires the PIT lifecycle plus mechanism, materiality, novelty, confirmed reaction, and auditable reaction dependency.",
            "An Event confirmed by a simultaneously directional Local Structure or Global Crude origin is suppressed as duplicate confirmation.",
            "USDINR is translation context only and cannot independently create or reverse direction.",
            "Direction Memory remains geometry-independent and may use only historically matured cases.",
            "Direction does not imply setup validity, entry readiness, or an option trade.",
        ],
        "integration_contract": integration_contract(),
    }


def integration_contract() -> dict:
    return {
        "version": "CRUDE_OIL_MINI_DIRECTION_BRAIN_V2_INTEGRATED_SHADOW_V1",
        "research_only": True,
        "shadow_only": True,
        "current_mind_effect": "NONE",
        "geometry_effect": "NONE",
        "option_brain_effect": "NONE",
        "old_direction_v2_replaced": False,
        "independent_direction_families": list(INDEPENDENT_FAMILIES),
        "causal_origin_deduplication": True,
        "dependent_reaction_confirmation_deduplication": True,
        "legacy_price_volume_participation_vote_allowed": False,
        "options_only_system": True,
        "option_oi_primary_positioning_context": True,
        "raw_option_oi_directional_vote_allowed": False,
        "futures_oi_required": False,
        "global_crude_uses_richer_perception_v2": True,
        "event_lifecycle_required": True,
        "headline_sentiment_direction_allowed": False,
        "usd_inr_modifier_only": True,
        "inspected_august_threshold_search_allowed": False,
        "historical_performance_evaluation_run_by_this_module": False,
        "prospective_schedule_defined_here": False,
        "fixed_direction_horizon_defined_here": False,
        "promotion_allowed": False,
        "requires_separate_diagnostic_replay_approval": True,
    }
