from __future__ import annotations

import asyncio
import json

from .copper_commodity_brain_prospective_store_v1 import (
    CONTRACT_VERSION as COPPER_CONTRACT_VERSION,
    MODEL_ID as COPPER_MODEL_ID,
    TABLE_NAME as COPPER_TABLE,
)
from .crude_oil_mini_episode_ledger_v1 import EPISODE_TABLE as CRUDE_EPISODE_TABLE

MODE = "SHARED_COMMODITY_BRAIN_DASHBOARD_V1"
CRUDE_SHARED_MODE = "CRUDE_OIL_MINI_COMMODITY_BRAIN_SHARED_SHADOW_V1"
CRUDE_PARITY_MODE = "CRUDE_OIL_MINI_SHARED_BRAIN_PARITY_V1"


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _safe_parity_view(parity: dict | None) -> dict | None:
    if not isinstance(parity, dict):
        return None
    legacy = parity.get("legacy") or {}
    shared = parity.get("shared") or {}
    memory = parity.get("memory_policy") or {}
    return {
        "mode": parity.get("mode") or CRUDE_PARITY_MODE,
        "status": parity.get("status"),
        "legacy": {
            "direction": legacy.get("direction"),
            "confidence": legacy.get("confidence"),
            "thesis_state": legacy.get("thesis_state"),
            "supporting_families": list(legacy.get("supporting_families") or []),
            "opposing_families": list(legacy.get("opposing_families") or []),
        },
        "shared": {
            "direction": shared.get("direction"),
            "confidence": shared.get("confidence"),
            "thesis_state": shared.get("thesis_state"),
            "supporting_families": list(shared.get("supporting_families") or []),
            "opposing_families": list(shared.get("opposing_families") or []),
        },
        "direction_agreement": parity.get("direction_agreement"),
        "confidence_agreement": parity.get("confidence_agreement"),
        "full_thesis_agreement": parity.get("full_thesis_agreement"),
        "divergence_reason": parity.get("divergence_reason"),
        "memory_policy": {
            "legacy_memory_counted": memory.get("legacy_memory_counted"),
            "shared_memory_role": memory.get("shared_memory_role") or "EXPERIENCE_CONTEXT",
            "shared_memory_counts_as_independent_confirmation": False,
        },
    }


def _dashboard_status_from_rows(copper_rows: list[tuple], crude_rows: list[tuple]) -> dict:
    copper_by_direction: dict[str, int] = {}
    copper_by_confidence: dict[str, int] = {}
    for row in copper_rows:
        direction = str(row[1] or "UNKNOWN").upper()
        confidence = str(row[2] or "UNKNOWN").upper()
        copper_by_direction[direction] = copper_by_direction.get(direction, 0) + 1
        copper_by_confidence[confidence] = copper_by_confidence.get(confidence, 0) + 1

    copper_latest = None
    if copper_rows:
        row = copper_rows[-1]
        copper_latest = {
            "board_as_of": row[0].isoformat(),
            "direction": str(row[1] or "UNKNOWN").upper(),
            "confidence": str(row[2] or "UNKNOWN").upper(),
            "thesis_state": row[3],
            "supporting_families": list(row[4] or []),
            "opposing_families": list(row[5] or []),
        }

    parity_count = 0
    latest_parity = None
    latest_parity_click = None
    for click_at, payload_text in crude_rows:
        try:
            payload = json.loads(payload_text or "{}")
            decision = payload.get("decision") or {}
            parity = decision.get("shared_commodity_brain_parity_v1")
        except Exception:
            parity = None
        safe = _safe_parity_view(parity)
        if safe is not None:
            parity_count += 1
            latest_parity = safe
            latest_parity_click = click_at.isoformat() if click_at else None

    return {
        "mode": MODE,
        "status": "ACTIVE",
        "research_only": True,
        "read_only": True,
        "trade_instrument": "OPTIONS_ONLY",
        "shared_core": {
            "minimum_independent_confirmations": 2,
            "weighted_score_used": False,
            "memory_role": "EXPERIENCE_CONTEXT",
            "memory_counts_as_independent_confirmation": False,
        },
        "copper": {
            "product": "COPPER",
            "stream_id": COPPER_MODEL_ID,
            "contract_version": COPPER_CONTRACT_VERSION,
            "status": "ACTIVE" if copper_rows else "WAITING_FOR_FIRST_PROSPECTIVE_SAMPLE",
            "prospective_evaluations": len(copper_rows),
            "directional_evaluations": sum(
                count for direction, count in copper_by_direction.items()
                if direction in {"BULLISH", "BEARISH"}
            ),
            "abstentions": copper_by_direction.get("UNKNOWN", 0),
            "by_direction": copper_by_direction,
            "by_confidence": copper_by_confidence,
            "latest": copper_latest,
            "first_seen_immutable": True,
            "historical_backfill_used": False,
            "same_pit_board_as_direction_v2": True,
            "sealed_current_mind_phase1_visible": False,
            "sealed_current_mind_effect": "NONE",
            "decision_effect": "NONE",
            "execution_effect": "NONE",
            "capital_committed": 0,
            "promotion_eligible": False,
        },
        "crude_oil_mini": {
            "product": "CRUDEOILM",
            "shared_mode": CRUDE_SHARED_MODE,
            "parity_mode": CRUDE_PARITY_MODE,
            "status": "ACTIVE" if parity_count else "WAITING_FOR_FIRST_SHARED_PROSPECTIVE_SAMPLE",
            "prospective_episodes": len(crude_rows),
            "shared_parity_episodes": parity_count,
            "latest_parity_click": latest_parity_click,
            "latest_parity": latest_parity,
            "same_pit_family_snapshot_as_legacy": True,
            "decision_effect": "NONE",
            "execution_effect": "NONE",
            "capital_committed": 0,
            "promotion_eligible": False,
        },
        "safety": {
            "copper_phase1_sealed_outputs_exposed": False,
            "outcomes_or_pnl_exposed": False,
            "collector_credentials_exposed": False,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "capital_committed": 0,
        },
    }


def _read_dashboard_status_sync(database_url: str) -> dict:
    copper_sql = f"""
        SELECT board_as_of, direction, direction_confidence, thesis_state,
               supporting_families, opposing_families
        FROM {COPPER_TABLE}
        WHERE model_id = %s
        ORDER BY board_as_of ASC
    """
    crude_sql = f"""
        SELECT click_at, payload
        FROM {CRUDE_EPISODE_TABLE}
        ORDER BY click_at ASC
    """
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(copper_sql, (COPPER_MODEL_ID,))
            copper_rows = cursor.fetchall()
            cursor.execute(crude_sql)
            crude_rows = cursor.fetchall()
    return _dashboard_status_from_rows(copper_rows, crude_rows)


async def read_dashboard_status(database_url: str) -> dict:
    database_url = str(database_url or "").strip()
    if not database_url:
        return {
            "mode": MODE,
            "status": "UNAVAILABLE",
            "research_only": True,
            "read_only": True,
            "reason": "DATABASE_URL_NOT_CONFIGURED",
        }
    return await asyncio.to_thread(_read_dashboard_status_sync, database_url)


def register_shared_commodity_brain_dashboard_routes(app, settings) -> None:
    @app.get("/v1/dashboard/shared-commodity-brain/status")
    async def shared_commodity_brain_dashboard_status():
        return await read_dashboard_status(getattr(settings, "database_url", ""))
