from __future__ import annotations
from .current_mind_information_board import information_board
from .market_regime_observer import observe_regime
from .trader_evidence_synthesis import synthesize_evidence,evidence_quality
from .trader_experience_memory import retrieve_similar
from .trader_scenario_board import build_scenario_board
from .trader_decision_journal import journal_decision

def current_mind_click(*,click_timestamp,context_records,market_features,evidence_items,memory_cases=None,decision_builder=None,option_expression=None):
    board=information_board(context_records,click_timestamp)
    regime=observe_regime(market_features)
    evidence=synthesize_evidence(evidence_items)
    memory=retrieve_similar(memory_cases or [],{"regime":regime,"evidence":evidence})
    scenario=build_scenario_board(evidence_items,{"similar_cases":memory})
    decision=(decision_builder or _default_decision)(board,regime,evidence,scenario,memory)
    return journal_decision(click_timestamp=click_timestamp,information_board=board,regime=regime,
      evidence=evidence,scenario=scenario,thesis=decision.get("thesis"),decision=decision,
      option_expression=option_expression)

def _default_decision(board,regime,evidence,scenario,memory):
    return {"action":"NO_TRADE","reason":"NO_DECISION_BUILDER_CONNECTED",
            "evidence_quality":evidence_quality(evidence),
            "thesis":"Current Mind observation complete; no fabricated trade without an explicit thesis builder."}
