"""Database-level immutability hardening for the prospective BTC proof tape.

The proof-store API is already insert-only. This module adds a second line of
defense in Postgres itself: UPDATE, DELETE, and TRUNCATE are rejected for both
the frozen-decision and resolution tables. The DDL is applied transactionally
and starts no market collection, proof decision, resolution, or execution path.
"""
from __future__ import annotations

import asyncio

from app.crypto_btc_prospective_thesis_postgres import DECISION_TABLE, RESOLUTION_TABLE

FUNCTION_NAME = "crypto_btc_prospective_thesis_reject_mutation_v1"
DECISION_ROW_TRIGGER = "crypto_btc_prospective_decision_reject_row_mutation_v1"
DECISION_TRUNCATE_TRIGGER = "crypto_btc_prospective_decision_reject_truncate_v1"
RESOLUTION_ROW_TRIGGER = "crypto_btc_prospective_resolution_reject_row_mutation_v1"
RESOLUTION_TRUNCATE_TRIGGER = "crypto_btc_prospective_resolution_reject_truncate_v1"

IMMUTABILITY_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION_NAME}()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'prospective BTC thesis proof tape is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS {DECISION_ROW_TRIGGER} ON {DECISION_TABLE};
CREATE TRIGGER {DECISION_ROW_TRIGGER}
BEFORE UPDATE OR DELETE ON {DECISION_TABLE}
FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}();

DROP TRIGGER IF EXISTS {DECISION_TRUNCATE_TRIGGER} ON {DECISION_TABLE};
CREATE TRIGGER {DECISION_TRUNCATE_TRIGGER}
BEFORE TRUNCATE ON {DECISION_TABLE}
FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION_NAME}();

DROP TRIGGER IF EXISTS {RESOLUTION_ROW_TRIGGER} ON {RESOLUTION_TABLE};
CREATE TRIGGER {RESOLUTION_ROW_TRIGGER}
BEFORE UPDATE OR DELETE ON {RESOLUTION_TABLE}
FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}();

DROP TRIGGER IF EXISTS {RESOLUTION_TRUNCATE_TRIGGER} ON {RESOLUTION_TABLE};
CREATE TRIGGER {RESOLUTION_TRUNCATE_TRIGGER}
BEFORE TRUNCATE ON {RESOLUTION_TABLE}
FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION_NAME}();
"""


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


async def harden_btc_prospective_thesis_schema(database_url: str) -> dict:
    database_url = str(database_url or "").strip()
    if not database_url:
        raise ValueError("database_url is required for BTC proof immutability hardening")
    await asyncio.to_thread(_harden_sync, database_url)
    return {
        "status": "BTC_PROSPECTIVE_THESIS_DB_IMMUTABLE",
        "decision_table": DECISION_TABLE,
        "resolution_table": RESOLUTION_TABLE,
        "update_allowed": False,
        "delete_allowed": False,
        "truncate_allowed": False,
        "collection_started": False,
        "decision_frozen": False,
        "outcome_resolved": False,
        "execution_started": False,
    }


def _harden_sync(database_url: str) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(IMMUTABILITY_SQL)
        conn.commit()


def architecture_contract() -> dict:
    return {
        "version": "BTC_PROSPECTIVE_THESIS_DB_IMMUTABILITY_V1",
        "database_enforced": True,
        "decision_update_allowed": False,
        "decision_delete_allowed": False,
        "decision_truncate_allowed": False,
        "resolution_update_allowed": False,
        "resolution_delete_allowed": False,
        "resolution_truncate_allowed": False,
        "insert_path_changed": False,
        "schema_hardening_starts_collection": False,
        "schema_hardening_freezes_decision": False,
        "schema_hardening_resolves_outcome": False,
        "options_trade_generated": False,
        "futures_trade_generated": False,
        "live_execution": False,
        "research_only": True,
    }
