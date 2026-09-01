from __future__ import annotations

from typing import Any

from .setup_playbook_selector import PLAYBOOKS

ACTION_DIRECTION = {"BUY_CE": "BULLISH", "BUY_PE": "BEARISH"}
PLAYBOOK_FAMILY = {
    "TREND_PULLBACK": "CONTINUATION_PULLBACK",
    "BREAKOUT_RETEST": "BREAKOUT_CONTINUATION",
    "RANGE_EDGE_REVERSAL": "MEAN_REVERSION_REVERSAL",
    "FAILED_BREAKOUT": "FAILURE_REVERSAL",
}
REVERSAL_FAMILIES = {"MEAN_REVERSION_REVERSAL", "FAILURE_REVERSAL"}
CONTINUATION_FAMILIES = {"CONTINUATION_PULLBACK", "BREAKOUT_CONTINUATION"}
DIRECTIONAL_CONTROL_STATES = {
    "CONTROL_ACTIVE",
    "CONTROL_ASSIMILATING",
    "CONTROL_CONTESTED",
    "CONTROL_OVERRIDDEN",
}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _labels(journal: dict) -> list[str]:
    labels = _dict(journal.get("regime")).get("regime_labels") or []
    return [str(x).upper() for x in labels]


def _regime_observations(journal: dict) -> dict:
    return _dict(_dict(journal.get("regime")).get("observations"))


def audit_declared_playbook(journal: dict) -> dict:
    """Audit what the frozen decision actually proves about its declared playbook.

    This deliberately does not infer missing chart-pattern evidence. Current Mind may declare a
    playbook after regime eligibility, but the frozen decision journal does not yet retain a
    dedicated pattern-specific confirmation object (for example the actual pullback/reacceptance,
    range-edge rejection, retest, or failed-break evidence). That architectural gap is surfaced
    rather than reconstructed from later candles or outcomes.
    """
    decision = _dict(journal.get("decision"))
    action = str(decision.get("action") or "NO_TRADE")
    playbook = str(decision.get("playbook") or "")
    labels = _labels(journal)
    observations = _regime_observations(journal)

    if not playbook:
        return {
            "status": "NO_DECLARED_PLAYBOOK",
            "playbook": None,
            "family": "UNDECLARED",
            "description": None,
            "regime_requirements": [],
            "regime_requirements_satisfied": None,
            "pattern_specific_confirmation_recorded": False,
            "location_proxy": observations.get("location") or "UNKNOWN",
            "rule": "No playbook is inferred when the frozen decision did not declare one.",
        }

    spec = PLAYBOOKS.get(playbook)
    if spec is None:
        return {
            "status": "UNKNOWN_DECLARED_PLAYBOOK",
            "playbook": playbook,
            "family": PLAYBOOK_FAMILY.get(playbook, "OTHER"),
            "description": None,
            "regime_requirements": [],
            "regime_requirements_satisfied": False,
            "pattern_specific_confirmation_recorded": False,
            "location_proxy": observations.get("location") or "UNKNOWN",
            "rule": "Unknown playbook declarations fail closed to an unverified semantic state.",
        }

    needs = [str(x).upper() for x in spec.get("needs") or []]
    regime_ok = all(need in labels for need in needs)
    # Dedicated pattern confirmation is intentionally explicit. Generic evidence coherence,
    # a generic recent-high/low trigger, or a regime label is not treated as proof of the
    # playbook-specific chart pattern.
    pattern_payload = decision.get("playbook_pattern_confirmation")
    pattern_recorded = isinstance(pattern_payload, dict) and bool(pattern_payload)

    if not regime_ok:
        status = "DECLARED_PLAYBOOK_REGIME_MISMATCH"
    elif pattern_recorded:
        status = "EXPLICIT_PATTERN_CONFIRMATION_RECORDED"
    elif action in ACTION_DIRECTION:
        status = "ACTIONABLE_PLAYBOOK_PATTERN_NOT_VERIFIED"
    else:
        status = "DECLARED_PLAYBOOK_PATTERN_NOT_VERIFIED"

    location = str(observations.get("location") or "UNKNOWN").upper()
    range_edge_proxy = None
    if playbook == "RANGE_EDGE_REVERSAL":
        range_edge_proxy = (
            "EDGE_LIKE_LOCATION_PROXY"
            if location in {"EXTENDED_ABOVE_VALUE", "EXTENDED_BELOW_VALUE"}
            else "NO_EDGE_LOCATION_PROXY"
        )

    return {
        "status": status,
        "playbook": playbook,
        "family": PLAYBOOK_FAMILY.get(playbook, "OTHER"),
        "description": spec.get("description"),
        "regime_requirements": needs,
        "regime_requirements_satisfied": regime_ok,
        "pattern_specific_confirmation_recorded": pattern_recorded,
        "location_proxy": location,
        "range_edge_location_proxy": range_edge_proxy,
        "rule": (
            "Regime eligibility and generic entry geometry are not treated as proof of a named "
            "playbook. Pattern-specific confirmation must be explicitly frozen in the decision "
            "journal before the playbook can be considered semantically verified."
        ),
    }


def assess_news_thesis_interaction(journal: dict, catalyst_control: dict) -> dict:
    """Describe how frozen catalyst control relates to the frozen Current Mind thesis.

    The function is strictly shadow-only and outcome-blind. It reads only the already-frozen
    decision, regime observations, and catalyst-control shadow. It never changes the decision and
    never reads outcome/P&L fields from the journal.
    """
    decision = _dict(journal.get("decision"))
    baseline_action = str(decision.get("action") or "NO_TRADE")
    baseline_direction = ACTION_DIRECTION.get(baseline_action, "UNKNOWN")
    playbook_audit = audit_declared_playbook(journal)
    family = playbook_audit["family"]

    control = _dict(catalyst_control)
    control_state = str(control.get("state") or "UNKNOWN").upper()
    catalyst_direction = str(control.get("direction") or "UNKNOWN").upper()
    controls_direction = bool(control.get("controls_direction"))

    if baseline_direction == "UNKNOWN":
        interaction = "BASELINE_NON_DIRECTIONAL"
        alignment = "NOT_APPLICABLE"
    elif catalyst_direction not in {"BULLISH", "BEARISH"}:
        interaction = "NO_DIRECTIONAL_CATALYST_CONTEXT"
        alignment = "UNKNOWN"
    elif catalyst_direction == baseline_direction:
        alignment = "ALIGNED"
        if control_state == "CONTROL_ACTIVE":
            interaction = "ACTIVE_CATALYST_ALIGNS_WITH_THESIS"
        elif control_state in DIRECTIONAL_CONTROL_STATES:
            interaction = "NONACTIVE_CATALYST_ALIGNS_WITH_THESIS"
        else:
            interaction = "CATALYST_DIRECTION_ALIGNS_WITH_THESIS"
    else:
        alignment = "OPPOSED"
        if control_state == "CONTROL_ACTIVE":
            if family in REVERSAL_FAMILIES:
                interaction = "ACTIVE_CATALYST_OPPOSES_REVERSAL_THESIS"
            elif family in CONTINUATION_FAMILIES:
                interaction = "ACTIVE_CATALYST_OPPOSES_CONTINUATION_THESIS"
            else:
                interaction = "ACTIVE_CATALYST_OPPOSES_THESIS"
        elif control_state == "CONTROL_ASSIMILATING":
            interaction = "ASSIMILATING_CATALYST_OPPOSES_THESIS"
        elif control_state == "CONTROL_CONTESTED":
            interaction = "CONTESTED_CATALYST_OPPOSES_THESIS"
        elif control_state == "CONTROL_OVERRIDDEN":
            interaction = "OVERRIDDEN_CATALYST_OPPOSES_THESIS"
        else:
            interaction = "NONCONTROLLING_CATALYST_OPPOSES_THESIS"

    return {
        "mode": "MARKET_NEWS_THESIS_INTERACTION_SHADOW_V1",
        "outcome_blind": True,
        "shadow_only": True,
        "changes_decision": False,
        "baseline_action": baseline_action,
        "baseline_direction": baseline_direction,
        "playbook": playbook_audit.get("playbook"),
        "playbook_family": family,
        "playbook_audit": playbook_audit,
        "catalyst_control_state": control_state,
        "catalyst_direction": catalyst_direction,
        "catalyst_controls_direction": controls_direction,
        "alignment": alignment,
        "interaction": interaction,
        "rule": (
            "Catalyst state qualifies context around a frozen thesis; it does not veto, create, "
            "reverse, strengthen, or weaken an action in this shadow. Named playbooks are not "
            "treated as semantically verified unless pattern-specific confirmation was explicitly "
            "recorded before outcome reveal."
        ),
    }
