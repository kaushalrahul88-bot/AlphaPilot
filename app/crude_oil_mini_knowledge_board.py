from __future__ import annotations

from .crude_oil_domain_knowledge import CRUDE_OIL_KNOWLEDGE_V1
from .crude_oil_mini_point_in_time_context import latest_known_as_of


# Each tuple is one sufficient all-of observation set. Multiple tuples are alternatives.
# Static knowledge never activates itself: at least one genuinely point-in-time market/event
# observation set must be present before an item is considered interpretation context.
KNOWLEDGE_REQUIREMENTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "CL_MCX_WTI_BENCHMARK": (("WTI_CRUDE",),),
    "CL_MCX_FX_TRANSLATION": (("WTI_CRUDE", "USDINR"),),
    "CL_GLOBAL_SUPPLY_DEMAND": (
        ("EIA_CRUDE_INVENTORY",),
        ("OPEC_SUPPLY",),
        ("CRUDE_NEWS",),
    ),
    "CL_EIA_INVENTORY_BALANCE": (("EIA_CRUDE_INVENTORY",),),
    "CL_REFINED_PRODUCTS_CONFIRMATION": (),
    "CL_REFINERY_UTILIZATION": (),
    "CL_OPEC_SUPPLY_MANAGEMENT": (("OPEC_SUPPLY",),),
    "CL_NON_OPEC_SUPPLY": (("CRUDE_NEWS",),),
    "CL_GEOPOLITICAL_SUPPLY_RISK": (("CRUDE_NEWS",),),
    "CL_WEATHER_DISRUPTION": (("CRUDE_NEWS",),),
    "CL_CURVE_STRUCTURE": (),
    "CL_CHINA_GLOBAL_DEMAND": (),
    "CL_OPTION_VOL_SEPARATE": (("MCX_CRUDEOILM_OPTION",),),
}


def _requirement_satisfied(alternatives: tuple[tuple[str, ...], ...], available: set[str]) -> bool:
    return any(set(required).issubset(available) for required in alternatives)


def _compact_item(item) -> dict:
    return {
        "id": item.id,
        "family": item.family,
        "claim": item.claim,
        "mechanism": item.mechanism,
        "expected_effect": item.expected_effect,
        "conditions": list(item.conditions),
        "exceptions": list(item.exceptions),
        "horizon": item.horizon,
        "source_tier": item.source_tier,
        "status": item.status,
        "option_implication": item.option_implication,
        "hypothesis_hook": item.hypothesis_hook,
        "production_rule": bool(item.production_rule),
    }


def knowledge_board(context_records: list[dict], click_timestamp: str) -> dict:
    """Attach Crude expertise without turning knowledge into directional evidence.

    The board is deliberately downstream of point-in-time visibility and upstream of
    human/research interpretation only. It is not passed to evidence synthesis,
    scenario voting, thesis construction or trade geometry. A knowledge item becomes
    `ACTIVE_INTERPRETATION_CONTEXT` only when its declared observation requirement is
    genuinely visible by the simulated click.
    """
    latest = latest_known_as_of(context_records, click_timestamp)
    available = set(latest)
    active = []
    dormant = []
    for item in CRUDE_OIL_KNOWLEDGE_V1:
        alternatives = KNOWLEDGE_REQUIREMENTS.get(item.id, ())
        if alternatives and _requirement_satisfied(alternatives, available):
            active.append({
                **_compact_item(item),
                "activation": "ACTIVE_INTERPRETATION_CONTEXT",
                "satisfied_by": [
                    list(required)
                    for required in alternatives
                    if set(required).issubset(available)
                ],
            })
        else:
            dormant.append({
                "id": item.id,
                "family": item.family,
                "activation": "DORMANT_NO_REQUIRED_PIT_OBSERVATION",
                "observation_requirements": [list(required) for required in alternatives],
            })

    return {
        "mode": "CRUDE_OIL_MINI_KNOWLEDGE_BOARD_V1",
        "knowledge_version": "CRUDE_OIL_DOMAIN_KNOWLEDGE_V1",
        "click_timestamp": click_timestamp,
        "research_only": True,
        "decision_vote": False,
        "decision_path_changed": False,
        "available_context_series": sorted(available),
        "active_items": active,
        "active_item_ids": [row["id"] for row in active],
        "dormant_items": dormant,
        "rule": (
            "Static Crude knowledge may interpret only genuinely visible point-in-time observations. "
            "Knowledge without an observation contributes zero directional evidence and cannot create, "
            "reverse, suppress or resize a trade."
        ),
    }
