from __future__ import annotations

import json
import os

from app.fno_data_readiness_audit_v2 import (
    CADENCE_SQL,
    DAILY_MARKET_COVERAGE_SQL,
    LEG_COMPLETENESS_SQL,
    SNAPSHOT_SUMMARY_SQL,
    TABLE_CAPABILITY_SQL,
    assess_readiness,
)


def _one(cur, sql):
    cur.execute(sql)
    columns = [item.name for item in cur.description]
    row = cur.fetchone()
    return dict(zip(columns, row)) if row else {}


def main():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    import psycopg

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        connection.execute("BEGIN READ ONLY")
        with connection.cursor() as cur:
            snapshot = _one(cur, SNAPSHOT_SUMMARY_SQL)
            leg = _one(cur, LEG_COMPLETENESS_SQL)
            cadence = _one(cur, CADENCE_SQL)
            cur.execute(DAILY_MARKET_COVERAGE_SQL)
            daily_columns = [item.name for item in cur.description]
            daily = [dict(zip(daily_columns, row)) for row in cur.fetchall()]
            cur.execute(TABLE_CAPABILITY_SQL)
            tables = {row[0] for row in cur.fetchall()}

    market_days = sum(1 for row in daily if int(row.get("market_hours_snapshots") or 0) > 0)
    result = assess_readiness(
        snapshot,
        leg,
        cadence,
        market_hours_days=market_days,
        fno_decision_ledger_present="fno_prospective_episodes_v1" in tables,
        fno_outcome_ledger_present="fno_prospective_outcomes_v1" in tables,
        selected_contract_tape_present="fno_selected_contract_observations_v1" in tables,
        historical_option_candle_probe_reliable=False,
    )
    result["daily_market_coverage"] = daily
    result["prospective_tables_present"] = sorted(
        tables
        & {
            "fno_prospective_episodes_v1",
            "fno_selected_contract_observations_v1",
            "fno_prospective_outcomes_v1",
        }
    )
    print(json.dumps(result, indent=2, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
