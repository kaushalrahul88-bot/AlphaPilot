from __future__ import annotations
from .commodity_time import parse_ist_timestamp
from .current_mind_information_board import information_board
from .market_regime_observer import observe_regime
from .trader_evidence_synthesis import synthesize_evidence,evidence_quality
from .trader_experience_memory import retrieve_similar
from .trader_scenario_board import build_scenario_board
from .trader_decision_journal import journal_decision
from .setup_playbook_selector import playbook_selection_semantics


def _memory_case_available_at(case):
    """Return the earliest timestamp at which a replay case outcome is knowable.

    Explicit availability wins. Otherwise only outcomes carrying a concrete
    resolution/exit timestamp are admissible. Cases with session-end, no-entry,
    missed-move, or other outcome labels but no auditable availability timestamp
    remain hidden rather than being guessed into the past.
    """
    if not isinstance(case,dict):
        return None
    for value in (case.get("available_at"),case.get("resolved_at")):
        if value:
            try:return parse_ist_timestamp(value)
            except (TypeError,ValueError,OverflowError):return None
    outcome=case.get("outcome") or {}
    if isinstance(outcome,dict):
        for key in ("available_at","resolved_at","exit_at"):
            value=outcome.get(key)
            if value:
                try:return parse_ist_timestamp(value)
                except (TypeError,ValueError,OverflowError):return None
    return None


def _visible_memory_cases(memory_cases,click_timestamp):
    """Expose only decision cases whose outcomes were known before this click."""
    click=parse_ist_timestamp(click_timestamp)
    visible=[]
    for case in memory_cases or []:
        available=_memory_case_available_at(case)
        # Strictly earlier avoids same-bar outcome leakage when candle timestamps
        # do not prove whether the event happened before or after the click instant.
        if available is not None and available<click:
            visible.append(case)
    return visible


def current_mind_click(*,click_timestamp,context_records,market_features,evidence_items,memory_cases=None,decision_builder=None,option_expression=None):
    board=information_board(context_records,click_timestamp)
    regime=observe_regime(market_features)
    evidence=synthesize_evidence(evidence_items)
    provided_memory=list(memory_cases or [])
    visible_memory=_visible_memory_cases(provided_memory,click_timestamp)
    memory=retrieve_similar(visible_memory,{"regime":regime,"evidence":evidence})
    scenario=build_scenario_board(evidence_items,{"similar_cases":memory})
    if decision_builder is None:
        from .current_mind_thesis_builder import build_current_mind_decision
        decision=build_current_mind_decision(board,regime,evidence,scenario,memory,market_features)
    else:
        decision=decision_builder(board,regime,evidence,scenario,memory)
    journal=journal_decision(click_timestamp=click_timestamp,information_board=board,regime=regime,
      evidence=evidence,scenario=scenario,thesis=decision.get("thesis"),decision=decision,
      option_expression=option_expression)
    # Audit annotations are intentionally attached after decision freeze/fingerprinting.
    journal["memory_visibility"]={
      "mode":"POINT_IN_TIME_DECISION_MEMORY_V1",
      "provided_cases":len(provided_memory),
      "visible_cases":len(visible_memory),
      "withheld_cases":len(provided_memory)-len(visible_memory),
      "same_timestamp_allowed":False,
      "unknown_availability_policy":"WITHHOLD",
      "rule":"A replay decision case may influence a click only after its outcome availability is auditable and strictly earlier than the click.",
    }
    # This clarifies playbook semantics but cannot alter the frozen decision or replay memory.
    journal["playbook_semantics"]=playbook_selection_semantics(
      decision.get("playbook"),default_selector=decision_builder is None)
    return journal


def _default_decision(board,regime,evidence,scenario,memory):
    return {"action":"NO_TRADE","reason":"NO_DECISION_BUILDER_CONNECTED",
            "evidence_quality":evidence_quality(evidence),
            "thesis":"Current Mind observation complete; no fabricated trade without an explicit thesis builder."}
