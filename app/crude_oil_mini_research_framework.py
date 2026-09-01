from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime

from .crude_oil_mini_abstention_audit import evaluate_abstention_audit
from .crude_oil_mini_context_ablation import evaluate_crude_context_ablation
from .crude_oil_mini_context_tape import PostgresCrudeContextTapeStore, build_or_read_frozen_context_tape
from .crude_oil_mini_current_mind_error_attribution import evaluate_error_attribution
from .crude_oil_mini_memory_evidence_audit import evaluate_memory_evidence
from .crude_oil_mini_playbook_pattern_shadow import evaluate_crude_playbook_pattern_shadow
from .crude_oil_mini_point_in_time_context import acquisition_manifest, audit_context_coverage
from .crude_oil_mini_research_tape import (
    FROZEN_CURRENT_CONTRACT,
    FROZEN_RESEARCH_END,
    read_frozen_research_tape,
    refresh_frozen_research_tape,
)
from .current_mind_crude_oil_mini_replay import evaluate_crude_oil_mini_current_mind_no_news


def _sha256_json(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _primary_context_from_replay(replay: dict) -> list[dict]:
    records = []
    for decision in replay.get("decisions") or []:
        records.append({
            "series": "MCX_CRUDEOILM",
            "observed_at": decision["latest_visible_bar_start"],
            "available_at": decision["latest_visible_bar_available_at"],
            "source": replay.get("reference_contract") or FROZEN_CURRENT_CONTRACT,
            "value": {"price": (decision.get("features") or {}).get("price")},
            "quality": "OBSERVED",
        })
    return records


def framework_summary(report: dict) -> dict:
    replay = report.get("no_news_replay") or {}
    memory = report.get("memory_evidence_audit") or {}
    abstention = report.get("abstention_audit") or {}
    error = report.get("error_attribution") or {}
    pattern = report.get("playbook_pattern_shadow") or {}
    tape = report.get("research_tape") or {}
    context_tape = ((report.get("discovery_context_tape") or {}).get("certification") or {})
    context_ablation = report.get("discovery_context_ablation") or {}
    variants = context_ablation.get("variants") or {}
    return {
        "mode": report.get("mode"),
        "status": report.get("status"),
        "reference_contract": report.get("reference_contract"),
        "tape_status": tape.get("status"),
        "tape_sha256": tape.get("tape_sha256"),
        "complete_sessions": replay.get("complete_sessions"),
        "scheduled_clicks": replay.get("scheduled_clicks"),
        "evaluated_clicks": replay.get("evaluated_clicks"),
        "click_coverage_exact": replay.get("click_coverage_exact"),
        "click_schedule_sha256": report.get("click_schedule_sha256"),
        "no_news_performance": replay.get("performance"),
        "memory_selected_setups": memory.get("selected_setups"),
        "memory_target_first_pct_resolved": memory.get("target_first_pct_resolved"),
        "waits": (abstention.get("overall") or {}).get("waits"),
        "large_move_candidates_after_wait": (abstention.get("overall") or {}).get("large_move_candidates"),
        "error_trade_observations": error.get("trade_observations"),
        "stable_good_states": len(error.get("stable_above_50_pct_states") or []),
        "stable_bad_states": len(error.get("stable_below_50_pct_states") or []),
        "literal_pattern_confirmed_trades": (pattern.get("pattern_gate") or {}).get("trades"),
        "literal_pattern_changed_clicks": pattern.get("changed_clicks"),
        "literal_pattern_candidate_expectancy_r": (pattern.get("pattern_gate") or {}).get("expectancy_r_resolved"),
        "context_tape_status": context_tape.get("status"),
        "context_tape_sha256": context_tape.get("tape_sha256"),
        "context_source_grade": context_tape.get("source_grade"),
        "context_variant_A": ((variants.get("A") or {}).get("summary")),
        "context_variant_B": ((variants.get("B") or {}).get("summary")),
        "context_variant_C": ((variants.get("C") or {}).get("summary")),
        "context_promotion_allowed": context_ablation.get("promotion_allowed"),
        "no_news_brain_freeze_status": report.get("no_news_brain_freeze_status"),
        "next_step": report.get("next_step"),
    }


async def run_crude_oil_mini_research_framework(
    provider,
    store,
    *,
    now: datetime | None = None,
    context_tape_store=None,
) -> dict:
    """Run the Crude no-news Research Brain from one certified durable tape.

    The local CRUDEOILM tape and click schedule are frozen before outcome-aware
    audits are inspected. Optional global context is also frozen independently
    before its exploratory ablation reads outcomes. Context ablation is shadow-only
    and cannot mutate the Current Mind decisions produced by this run.
    """
    tape = await refresh_frozen_research_tape(provider, store, now=now)
    if tape.get("status") != "CERTIFIED":
        raise RuntimeError(f"CRUDEOILM research tape certification failed: {tape}")
    contract, candles = await read_frozen_research_tape(store, end=FROZEN_RESEARCH_END)
    contract.update({
        "trading_symbol": FROZEN_CURRENT_CONTRACT,
        "lot_size": 10,
    })

    replay = await asyncio.to_thread(evaluate_crude_oil_mini_current_mind_no_news, candles, contract)
    if replay.get("reference_contract") != FROZEN_CURRENT_CONTRACT:
        raise RuntimeError("No-news replay changed the frozen Crude reference contract")
    if replay.get("scheduled_clicks") != replay.get("evaluated_clicks"):
        raise RuntimeError("No-news replay did not evaluate every frozen click")
    if replay.get("scheduled_clicks") != int(replay.get("complete_sessions") or 0) * 20:
        raise RuntimeError("No-news replay did not preserve exactly 20 clicks per complete session")
    if replay.get("click_coverage_exact") is not True:
        raise RuntimeError("No-news replay click coverage is not exact")
    if replay.get("news_enabled") is not False or replay.get("option_market_data_used") is not False:
        raise RuntimeError("No-news replay unexpectedly used news or option-market data")

    click_schedule = [
        {
            "session": row.get("session"),
            "click_timestamp": row.get("click_timestamp"),
            "sampling": row.get("sampling"),
        }
        for row in replay.get("decisions") or []
    ]
    decision_fingerprints = [
        {
            "click_timestamp": row.get("click_timestamp"),
            "decision_fingerprint": row.get("decision_fingerprint"),
            "action": row.get("action"),
        }
        for row in replay.get("decisions") or []
    ]
    click_schedule_sha256 = _sha256_json(click_schedule)
    no_news_decision_sha256 = _sha256_json(decision_fingerprints)

    # Freeze discovery-grade external context before any context/outcome interaction
    # is evaluated. A persistent snapshot is immutable once inserted.
    if context_tape_store is None:
        database_url = str(getattr(store, "database_url", "") or "").strip()
        if database_url:
            context_tape_store = PostgresCrudeContextTapeStore(database_url)
    discovery_context_tape = None
    discovery_context_ablation = None
    if context_tape_store is not None:
        discovery_context_tape = await build_or_read_frozen_context_tape(context_tape_store)
        certification = discovery_context_tape.get("certification") or {}
        if certification.get("status") != "CERTIFIED_DISCOVERY":
            raise RuntimeError(f"Discovery Crude context tape failed certification: {certification}")
        discovery_context_ablation = await asyncio.to_thread(
            evaluate_crude_context_ablation,
            replay,
            discovery_context_tape["payload"],
        )
        if discovery_context_ablation.get("decision_path_changed") is not False:
            raise RuntimeError("Context ablation attempted to mutate the Crude decision path")
        if discovery_context_ablation.get("promotion_allowed") is not False:
            raise RuntimeError("Discovery-grade context was incorrectly marked promotable")

    memory = await asyncio.to_thread(evaluate_memory_evidence, candles, 3)
    abstention = await asyncio.to_thread(evaluate_abstention_audit, replay)
    error = await asyncio.to_thread(evaluate_error_attribution, replay)
    pattern = await asyncio.to_thread(evaluate_crude_playbook_pattern_shadow, candles, replay)

    primary_records = _primary_context_from_replay(replay)
    click_timestamps = [row["click_timestamp"] for row in replay.get("decisions") or []]
    context_coverage = audit_context_coverage(primary_records, click_timestamps)
    if not all(row.get("required_context_complete") for row in context_coverage.get("clicks") or []):
        raise RuntimeError("Required MCX_CRUDEOILM context is missing at one or more replay clicks")

    report = {
        "mode": "CRUDE_OIL_MINI_COPPER_FRAMEWORK_RESEARCH_V1",
        "status": "AUDIT_COMPLETE",
        "research_only": True,
        "reference_contract": FROZEN_CURRENT_CONTRACT,
        "research_tape": tape,
        "click_schedule": click_schedule,
        "click_schedule_sha256": click_schedule_sha256,
        "no_news_decision_sha256": no_news_decision_sha256,
        "no_news_replay": replay,
        "memory_evidence_audit": memory,
        "abstention_audit": abstention,
        "error_attribution": error,
        "playbook_pattern_shadow": pattern,
        "context_coverage_primary_only": context_coverage,
        "context_acquisition_manifest": acquisition_manifest(),
        "discovery_context_tape": discovery_context_tape,
        "discovery_context_ablation": discovery_context_ablation,
        "no_news_brain_freeze_status": "HUMAN_REVIEW_REQUIRED",
        "next_step": (
            "Review Crude-only Memory/WAIT/error attribution together with the discovery-grade global-context ablation. "
            "Do not promote Yahoo-derived WTI/Brent/USDINR/DXY into the Current Mind from this already-inspected "
            "June-August sample. If the context effect is coherent, reproduce it on an authorized/independent data "
            "source and then validate it chronologically or prospectively before freezing the no-news Brain. News "
            "remains a later same-click experiment after that freeze."
        ),
        "integrity": {
            "architecture_reference": "COPPER_CURRENT_MIND",
            "architecture_replicated_not_market_values": True,
            "crude_specific_market_values_only": True,
            "current_contract_only": True,
            "regular_crude_used": False,
            "copper_market_data_used": False,
            "copper_fitted_thresholds_used": False,
            "shared_playbook_pattern_architecture_used": True,
            "playbook_pattern_shadow_can_mutate_decisions": False,
            "news_used_in_decisions": False,
            "option_market_data_used": False,
            "synthetic_option_premium_used": False,
            "outcome_audits_can_mutate_decisions": False,
            "discovery_context_can_mutate_decisions": False,
            "discovery_context_promotion_allowed": False,
            "discovery_context_requires_independent_validation": True,
            "performance_is_ci_gate": False,
            "live_execution_enabled": False,
        },
    }
    report["summary"] = framework_summary(report)
    return report
