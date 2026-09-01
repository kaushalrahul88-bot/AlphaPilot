from __future__ import annotations

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_information_board import information_board
from .current_mind_thesis_builder import build_current_mind_decision
from .market_regime_observer import observe_regime
from .setup_playbook_selector import playbook_selection_semantics
from .trader_decision_journal import journal_decision
from .trader_evidence_synthesis import synthesize_evidence
from .trader_experience_memory import retrieve_similar
from .trader_scenario_board import build_scenario_board


def _case_available_at(case: dict):
    if not isinstance(case, dict):
        return None
    for value in (case.get("available_at"), case.get("resolved_at")):
        if value:
            try:
                return parse_ist_timestamp(value)
            except Exception:
                return None
    outcome = case.get("outcome") or {}
    if isinstance(outcome, dict):
        for key in ("available_at", "resolved_at", "exit_at"):
            value = outcome.get(key)
            if value:
                try:
                    return parse_ist_timestamp(value)
                except Exception:
                    return None
    return None


def _visible_memory_cases(memory_cases: list[dict], click_timestamp: str) -> list[dict]:
    click = parse_ist_timestamp(click_timestamp)
    visible = []
    for case in memory_cases or []:
        available = _case_available_at(case)
        if available is not None and available < click:
            visible.append(case)
    return visible


def crude_oil_mini_current_mind_click(
    *,
    click_timestamp: str,
    context_records: list[dict],
    market_features: dict,
    evidence_items: list[dict],
    memory_cases: list[dict] | None = None,
) -> dict:
    """Run the same Trader-Mind layers as Copper with Crude-specific perception inputs."""
    board = information_board(context_records, click_timestamp)
    regime = observe_regime(market_features)
    evidence = synthesize_evidence(evidence_items)
    provided_memory = list(memory_cases or [])
    visible_memory = _visible_memory_cases(provided_memory, click_timestamp)
    memory = retrieve_similar(visible_memory, {"regime": regime, "evidence": evidence})
    scenario = build_scenario_board(evidence_items, {"similar_cases": memory})
    decision = build_current_mind_decision(board, regime, evidence, scenario, memory, market_features)
    journal = journal_decision(
        click_timestamp=click_timestamp,
        information_board=board,
        regime=regime,
        evidence=evidence,
        scenario=scenario,
        thesis=decision.get("thesis"),
        decision=decision,
        option_expression=None,
    )
    journal["memory_visibility"] = {
        "mode": "CRUDE_OIL_MINI_POINT_IN_TIME_DECISION_MEMORY_V1",
        "provided_cases": len(provided_memory),
        "visible_cases": len(visible_memory),
        "withheld_cases": len(provided_memory) - len(visible_memory),
        "same_timestamp_allowed": False,
        "unknown_availability_policy": "WITHHOLD",
    }
    journal["playbook_semantics"] = playbook_selection_semantics(decision.get("playbook"), default_selector=True)
    journal["architecture"] = {
        "shared_layers": [
            "MARKET_REGIME_OBSERVER_V1",
            "TRADER_EVIDENCE_SYNTHESIS_V1",
            "TRADER_EXPERIENCE_MEMORY",
            "TRADER_SCENARIO_BOARD",
            "CURRENT_MIND_THESIS_BUILDER",
            "SETUP_RISK_REVIEW",
            "TRADER_DECISION_JOURNAL",
        ],
        "commodity_specific_layers": [
            "CRUDE_OIL_MINI_CURRENT_MIND_INFORMATION_BOARD_V1",
            "CRUDE_OIL_MINI_MARKET_PERCEPTION_V1",
            "CRUDE_OIL_MINI_EXPERIENCE_MEMORY_V1",
        ],
    }
    return journal
