"""Read-only F&O data-readiness audit.

All SQL is SELECT-only. The audit describes replay capability and missing data;
it does not create decisions, modify strategy policy, or write to the database.
"""
from __future__ import annotations

from typing import Any, Mapping

SNAPSHOT_SUMMARY_SQL = """
SELECT COUNT(*)::bigint AS snapshot_rows,
       MIN(observed_at) AS first_at,
       MAX(observed_at) AS last_at,
       COUNT(DISTINCT underlying_symbol)::bigint AS underlyings,
       COUNT(DISTINCT (observed_at AT TIME ZONE 'Asia/Kolkata')::date)::bigint AS local_calendar_days
FROM fno_option_chain_snapshots;
"""

DAILY_MARKET_COVERAGE_SQL = """
SELECT (observed_at AT TIME ZONE 'Asia/Kolkata')::date AS trade_date,
       COUNT(*) FILTER (
         WHERE (observed_at AT TIME ZONE 'Asia/Kolkata')::time
               BETWEEN TIME '09:15' AND TIME '15:30'
       )::bigint AS market_hours_snapshots,
       COUNT(DISTINCT underlying_symbol) FILTER (
         WHERE (observed_at AT TIME ZONE 'Asia/Kolkata')::time
               BETWEEN TIME '09:15' AND TIME '15:30'
       )::bigint AS market_hours_underlyings,
       COUNT(*)::bigint AS all_snapshots
FROM fno_option_chain_snapshots
GROUP BY 1
ORDER BY 1;
"""

LEG_COMPLETENESS_SQL = """
WITH legs AS (
  SELECT strike.value->'CE' AS leg
  FROM fno_option_chain_snapshots s
  CROSS JOIN LATERAL jsonb_each(s.payload->'data'->'payload'->'strikes') strike
  WHERE (s.observed_at AT TIME ZONE 'Asia/Kolkata')::time BETWEEN TIME '09:15' AND TIME '15:30'
  UNION ALL
  SELECT strike.value->'PE' AS leg
  FROM fno_option_chain_snapshots s
  CROSS JOIN LATERAL jsonb_each(s.payload->'data'->'payload'->'strikes') strike
  WHERE (s.observed_at AT TIME ZONE 'Asia/Kolkata')::time BETWEEN TIME '09:15' AND TIME '15:30'
)
SELECT COUNT(*)::bigint AS legs,
       COUNT(*) FILTER (WHERE leg->>'trading_symbol' IS NOT NULL)::bigint AS trading_symbol_present,
       COUNT(*) FILTER (WHERE leg->>'ltp' IS NOT NULL)::bigint AS ltp_present,
       COUNT(*) FILTER (WHERE leg->>'open_interest' IS NOT NULL)::bigint AS oi_present,
       COUNT(*) FILTER (WHERE leg->>'volume' IS NOT NULL)::bigint AS volume_present,
       COUNT(*) FILTER (WHERE leg->'greeks'->>'iv' IS NOT NULL)::bigint AS iv_present,
       COUNT(*) FILTER (WHERE leg->'greeks'->>'delta' IS NOT NULL)::bigint AS delta_present,
       COUNT(*) FILTER (WHERE COALESCE(leg->>'best_bid', leg->>'bid', leg->>'bid_price') IS NOT NULL)::bigint AS bid_present,
       COUNT(*) FILTER (WHERE COALESCE(leg->>'best_ask', leg->>'ask', leg->>'ask_price') IS NOT NULL)::bigint AS ask_present
FROM legs;
"""

CADENCE_SQL = """
WITH x AS (
  SELECT underlying_symbol,
         observed_at,
         LAG(observed_at) OVER (
           PARTITION BY underlying_symbol,
                        (observed_at AT TIME ZONE 'Asia/Kolkata')::date
           ORDER BY observed_at
         ) AS prev_at
  FROM fno_option_chain_snapshots
  WHERE (observed_at AT TIME ZONE 'Asia/Kolkata')::time BETWEEN TIME '09:15' AND TIME '15:30'
), gaps AS (
  SELECT EXTRACT(EPOCH FROM (observed_at-prev_at))/60.0 AS gap_min
  FROM x
  WHERE prev_at IS NOT NULL
)
SELECT COUNT(*)::bigint AS intervals,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gap_min) AS median_gap_min,
       PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY gap_min) AS p90_gap_min,
       MAX(gap_min) AS max_gap_min,
       COUNT(*) FILTER (WHERE gap_min > 45)::bigint AS gaps_over_45m,
       COUNT(*) FILTER (WHERE gap_min > 60)::bigint AS gaps_over_60m
FROM gaps;
"""

TABLE_CAPABILITY_SQL = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema='public'
  AND table_name IN (
    'fno_option_chain_snapshots',
    'universe_candles',
    'fno_prospective_episodes_v1',
    'fno_selected_contract_observations_v1',
    'fno_prospective_outcomes_v1'
  )
ORDER BY table_name;
"""


def _ratio(part: Any, whole: Any) -> float:
    try:
        denominator = int(whole)
        return round(int(part) / denominator, 6) if denominator > 0 else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def assess_readiness(
    snapshot_summary: Mapping[str, Any],
    leg_completeness: Mapping[str, Any],
    cadence: Mapping[str, Any],
    *,
    market_hours_days: int,
    fno_decision_ledger_present: bool,
    fno_outcome_ledger_present: bool,
    selected_contract_tape_present: bool = False,
    historical_option_candle_probe_reliable: bool = False,
) -> dict[str, Any]:
    legs = int(leg_completeness.get("legs") or 0)
    fields = {
        key: _ratio(leg_completeness.get(key), legs)
        for key in (
            "trading_symbol_present", "ltp_present", "oi_present", "volume_present",
            "iv_present", "delta_present", "bid_present", "ask_present"
        )
    }
    snapshots = int(snapshot_summary.get("snapshot_rows") or 0)
    underlyings = int(snapshot_summary.get("underlyings") or 0)
    median_gap = float(cadence.get("median_gap_min") or 0.0)
    p90_gap = float(cadence.get("p90_gap_min") or 0.0)

    chain_replay = "AVAILABLE_BUT_SHORT_WINDOW" if snapshots > 0 and market_hours_days < 20 else "AVAILABLE"
    if snapshots <= 0:
        chain_replay = "NOT_AVAILABLE"
    execution_replay = "NOT_READY"
    if fields["bid_present"] == 1.0 and fields["ask_present"] == 1.0:
        execution_replay = "PARTIAL_RESEARCH_READY"

    experience = "NOT_READY_NO_PROSPECTIVE_DECISION_OUTCOME_LEDGER"
    if fno_decision_ledger_present and fno_outcome_ledger_present:
        experience = "LEDGER_AVAILABLE_NEEDS_PROSPECTIVE_SAMPLE"

    return {
        "audit_id": "FNO_DATA_READINESS_AUDIT_V2",
        "snapshot_rows": snapshots,
        "underlyings": underlyings,
        "market_hours_days": int(market_hours_days),
        "field_presence_ratio": fields,
        "median_same_session_gap_minutes": round(median_gap, 3),
        "p90_same_session_gap_minutes": round(p90_gap, 3),
        "capabilities": {
            "option_chain_point_in_time_replay": chain_replay,
            "underlying_candles": "FETCH_ON_DEMAND_NOT_WAREHOUSED",
            "historical_option_premium_candles": "PROVIDER_DEPENDENT" if historical_option_candle_probe_reliable else "NOT_RELIABLY_PROVEN",
            "spread_slippage_replay": execution_replay,
            "selected_contract_first_seen_tape": "AVAILABLE_NEEDS_SAMPLE_AUDIT" if selected_contract_tape_present else "NOT_AVAILABLE",
            "experience_outcome_memory": experience,
            "strategy_validation": "NOT_READY" if market_hours_days < 20 or not fno_outcome_ledger_present else "NEEDS_FORMAL_HOLDOUT_AUDIT",
        },
        "data_policy": {
            "store_non_reconstructible_point_in_time_state": True,
            "warehouse_reconstructible_underlying_candles": False,
            "no_lookahead_required": True,
            "selected_contract_tape_must_be_first_seen": True,
            "bid_ask_must_not_be_fabricated": True,
        },
        "blocking_gaps": [
            gap for gap, blocked in (
                ("SHORT_POINT_IN_TIME_WINDOW", market_hours_days < 20),
                ("NO_FNO_PROSPECTIVE_DECISION_LEDGER", not fno_decision_ledger_present),
                ("NO_FNO_PROSPECTIVE_OUTCOME_LEDGER", not fno_outcome_ledger_present),
                ("NO_SELECTED_CONTRACT_FIRST_SEEN_TAPE", not selected_contract_tape_present),
                ("NO_BID_ASK_SPREAD_HISTORY", fields["bid_present"] < 1.0 or fields["ask_present"] < 1.0),
                ("HISTORICAL_OPTION_CANDLE_REPLAY_NOT_RELIABLY_PROVEN", not historical_option_candle_probe_reliable),
            ) if blocked
        ],
        "ready_for_live_money": False,
        "diagnostic_only": True,
        "strategy_policy_changed": False,
        "database_writes": False,
    }


def architecture_contract() -> dict[str, Any]:
    sql_statements = (
        SNAPSHOT_SUMMARY_SQL,
        DAILY_MARKET_COVERAGE_SQL,
        LEG_COMPLETENESS_SQL,
        CADENCE_SQL,
        TABLE_CAPABILITY_SQL,
    )
    return {
        "version": "FNO_DATA_READINESS_AUDIT_V2_CONTRACT_V2",
        "read_only": True,
        "database_writes": False,
        "select_only_sql": all(sql.lstrip().lower().startswith(("select", "with")) for sql in sql_statements),
        "decisions_created": False,
        "strategy_policy_changed": False,
        "live_execution": False,
        "capital_committed": 0,
    }
