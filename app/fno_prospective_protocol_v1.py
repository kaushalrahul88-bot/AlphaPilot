"""Preregistered research contract for prospective NSE F&O learning.

This contract is intentionally fixed before prospective results mature. It
defines only research collection and descriptive outcome measurement. It never
places orders, commits capital, promotes a strategy, or lets future outcomes
enter a decision.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
PROTOCOL_ID = "FNO_PROSPECTIVE_LEARNING_V1_2026-09-06"
PRIMARY_HORIZON_MINUTES = 60
CAPTURE_CADENCE_MINUTES = 15
SELECTED_CONTRACT_CADENCE_MINUTES = 5
DEFAULT_BATCH_SIZE = 4
MAX_BATCH_SIZE = 8
MAX_ACTIVE_CONTRACTS_PER_PASS = 24
MAX_RESOLUTIONS_PER_PASS = 50
TIMEFRAMES = ("5m", "15m", "1h")
MIN_RISK_REWARD = 1.5


def session_outcome_eligible(decision_at: datetime) -> bool:
    """A fixed 60-minute outcome must fit inside the regular NSE session."""
    local = decision_at.astimezone(IST)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    latest = 15 * 60 + 30 - PRIMARY_HORIZON_MINUTES
    return 9 * 60 + 15 <= minutes <= latest


def protocol_manifest() -> dict:
    return {
        "protocol_id": PROTOCOL_ID,
        "research_only": True,
        "shadow_only": True,
        "primary_horizon_minutes": PRIMARY_HORIZON_MINUTES,
        "capture_cadence_minutes": CAPTURE_CADENCE_MINUTES,
        "selected_contract_cadence_minutes": SELECTED_CONTRACT_CADENCE_MINUTES,
        "default_batch_size": DEFAULT_BATCH_SIZE,
        "max_batch_size": MAX_BATCH_SIZE,
        "max_active_contracts_per_pass": MAX_ACTIVE_CONTRACTS_PER_PASS,
        "max_resolutions_per_pass": MAX_RESOLUTIONS_PER_PASS,
        "timeframes": list(TIMEFRAMES),
        "min_risk_reward": MIN_RISK_REWARD,
        "decision_actions": ["BUY_CE", "BUY_PE", "NO_TRADE"],
        "decision_memory_effect": "DESCRIPTIVE_ONLY",
        "outcomes_used_for_similarity_ranking": False,
        "future_outcome_allowed_in_decision": False,
        "database_mutation_policy": "INSERT_ONLY_IMMUTABLE",
        "underlying_outcome_source": "GROWW_HISTORICAL_5M_FETCHED_AFTER_HORIZON",
        "selected_option_source": "FIRST_SEEN_LIVE_GROWW_SELECTED_CONTRACT_TAPE",
        "bid_ask_policy": "CAPTURE_WHEN_PROVIDER_EXPOSES; NEVER_FABRICATE",
        "historical_option_chain_backfill": False,
        "options_trade_generation": "RESEARCH_ACTION_ONLY",
        "futures_trade_generation": False,
        "live_execution": False,
        "capital_committed": 0,
        "promotion_eligible": False,
    }


def architecture_contract() -> dict:
    manifest = protocol_manifest()
    return {
        "version": "FNO_PROSPECTIVE_LEARNING_CONTRACT_V1",
        **manifest,
        "late_session_episode_policy": "CAPTURE_PERCEPTION_BUT_DO_NOT_ADMIT_INCOMPLETE_FIXED_HORIZON_OUTCOME_TO_MEMORY",
        "threshold_tuning_from_prospective_results": False,
    }
