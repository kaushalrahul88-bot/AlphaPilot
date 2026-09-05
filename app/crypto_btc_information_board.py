"""Point-in-time BTC information board for the research-only Crypto Brain.

Missing evidence is reported explicitly. The board is descriptive: it does not
fabricate absent lanes, create a trade, or allow an instrument-specific route to
rewrite the shared BTC thesis.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.crypto_btc_perception import assemble_btc_perception
from app.crypto_market_intelligence import Evidence, evidence_is_fresh

LANES = (
    "SPOT_STRUCTURE",
    "DERIVATIVES_POSITIONING",
    "OPTIONS_MARKET",
    "ONCHAIN",
    "STABLECOIN_LIQUIDITY",
    "MACRO_CROSS_ASSET",
    "NEWS",
    "SOCIAL_NARRATIVE",
    "HISTORICAL_MEMORY",
)

FAMILY_TO_LANE = {
    "BTC_SPOT_STRUCTURE": "SPOT_STRUCTURE",
    "DERIVATIVES_POSITIONING": "DERIVATIVES_POSITIONING",
    "BTC_OPTIONS_MARKET": "OPTIONS_MARKET",
    "ONCHAIN_FLOW": "ONCHAIN",
    "ONCHAIN_METRIC": "ONCHAIN",
    "TOKEN_UNLOCK": "ONCHAIN",
    "STABLECOIN_LIQUIDITY": "STABLECOIN_LIQUIDITY",
    "BTC_MACRO_CROSS_ASSET": "MACRO_CROSS_ASSET",
    "CRYPTO_NEWS": "NEWS",
    "CRYPTO_SOCIAL_NARRATIVE": "SOCIAL_NARRATIVE",
    "BTC_HISTORICAL_ANALOGUE": "HISTORICAL_MEMORY",
}


def _lane_for(evidence: Evidence) -> str:
    return FAMILY_TO_LANE.get(evidence.family, "OTHER")


def build_btc_information_board(
    evidence: list[Evidence],
    *,
    decision_at: datetime,
    trade_horizon: str,
) -> dict:
    fresh: list[Evidence] = []
    stale: list[Evidence] = []
    for row in evidence:
        if evidence_is_fresh(row, decision_at=decision_at, trade_horizon=trade_horizon):
            fresh.append(row)
        else:
            stale.append(row)

    lane_rows: dict[str, list[Evidence]] = defaultdict(list)
    for row in fresh:
        lane_rows[_lane_for(row)].append(row)

    perception = assemble_btc_perception(fresh, decision_at=decision_at, trade_horizon=trade_horizon)
    lane_status = {}
    for lane in LANES:
        rows = lane_rows.get(lane, [])
        lane_status[lane] = {
            "available": bool(rows),
            "fresh_count": len(rows),
            "directional_count": sum(
                1
                for row in rows
                if not row.context_only and row.stance in {"BULLISH", "BEARISH"}
            ),
            "families": sorted({row.family for row in rows}),
            "sources": sorted({row.source for row in rows}),
        }

    spot_available = lane_status["SPOT_STRUCTURE"]["available"]
    options_context_available = lane_status["OPTIONS_MARKET"]["available"]
    derivatives_context_available = lane_status["DERIVATIVES_POSITIONING"]["available"]

    return {
        "version": "BTC_INFORMATION_BOARD_V1",
        "asset": "BTC",
        "decision_at": perception["decision_at"],
        "trade_horizon": trade_horizon,
        "default_platform": "COINDCX",
        "global_market_intelligence": True,
        "lane_status": lane_status,
        "missing_lanes": [lane for lane in LANES if not lane_status[lane]["available"]],
        "stale_evidence_count": len(stale),
        "other_fresh_evidence_count": len(lane_rows.get("OTHER", [])),
        "underlying_market_state": perception,
        "underlying_thesis_available": spot_available and perception["direction"] in {"BULLISH", "BEARISH"},
        "options_translation_context_available": options_context_available,
        "futures_specific_context_available": derivatives_context_available,
        "missing_options_context_blocks_underlying_thesis": False,
        "missing_futures_context_blocks_options_route": False,
        "options_and_futures_trade_generation_separate": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "broker_execution_enabled": False,
        "capital_committed": 0,
    }


def architecture_contract() -> dict:
    return {
        "version": "BTC_INFORMATION_BOARD_CONTRACT_V1",
        "missing_data_is_explicit": True,
        "missing_data_equals_neutral_vote": False,
        "spot_lane_required_for_underlying_thesis": True,
        "options_lane_can_create_underlying_direction": False,
        "historical_memory_can_create_underlying_direction": False,
        "social_lane_can_create_underlying_direction": False,
        "options_and_futures_trade_generation_separate": True,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "broker_execution_enabled": False,
    }
