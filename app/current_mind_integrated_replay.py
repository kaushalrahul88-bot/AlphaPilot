from __future__ import annotations
from .current_mind_information_board import information_board
from .market_regime_observer import observe_regime
from .trader_evidence_synthesis import synthesize_evidence,evidence_quality
from .trader_experience_memory import retrieve_similar
from .trader_scenario_board import build_scenario_board
from .trader_decision_journal import journal_decision
from .setup_playbook_selector import playbook_selection_semantics

def current_mind_click(*,click_timestamp,context_records,market_features,evidence_items,memory_cases=None,decision_builder=None,option_expression=None):
    board=information_board(context_records,click_timestamp)
    regime=observe_regime(market_features)
    evidence=synthesize_evidence(evidence_items)
    memory=retrieve_similar(memory_cases or [],{"regime":regime,"evidence":evidence})
    scenario=build_scenario_board(evidence_items,{"similar_cases":memory})
    if decision_builder is None:
        from .current_mind_thesis_builder import build_current_mind_decision
        decision=build_current_mind_decision(board,regime,evidence,scenario,memory,market_features)
    else:
        decision=decision_builder(board,regime,evidence,scenario,memory)
    journal=journal_decision(click_timestamp=click_timestamp,information_board=board,regime=regime,
      evidence=evidence,scenario=scenario,thesis=decision.get("thesis"),decision=decision,
      option_expression=option_expression)
    # Audit annotation is intentionally attached after decision freeze/fingerprinting.
    # It clarifies semantics but cannot alter the frozen decision or replay memory.
    journal["playbook_semantics"]=playbook_selection_semantics(
      decision.get("playbook"),default_selector=decision_builder is None)
    return journal

def _default_decision(board,regime,evidence,scenario,memory):
    return {"action":"NO_TRADE","reason":"NO_DECISION_BUILDER_CONNECTED",
            "evidence_quality":evidence_quality(evidence),
            "thesis":"Current Mind observation complete; no fabricated trade without an explicit thesis builder."}
