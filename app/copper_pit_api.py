from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Header

from .copper_candle_observation_store import CopperCandleObservationStore
from .copper_commodity_brain_prospective_store_v1 import (
    CopperCommodityBrainProspectiveStore,
    build_prospective_record as build_shared_prospective_record,
)
from .copper_commodity_brain_prospective_v1 import (
    STREAM_ID as SHARED_PROSPECTIVE_STREAM_ID,
    evaluate_copper_commodity_brain_prospective,
)
from .copper_direction_brain_v2_shadow_v2 import evaluate_copper_direction_v2_shadow
from .copper_direction_v2_prospective_store import (
    CopperDirectionV2ProspectiveStore,
    build_prospective_record,
)
from .copper_option_participation_v1 import (
    read_copper_information_board_with_option_participation,
)
from .copper_pit_candles import collect_copper_pit_candles
from .providers.factory import get_provider


IST = ZoneInfo("Asia/Kolkata")


def _shared_unavailable(exc: Exception) -> dict:
    return {
        "status": "UNAVAILABLE",
        "prospective_stream_id": SHARED_PROSPECTIVE_STREAM_ID,
        "evaluation_class": "PROSPECTIVE_SHADOW",
        "prospective": True,
        "research_only": True,
        "shadow_only": True,
        "reason": f"{exc.__class__.__name__}: {str(exc)[:500]}",
        "sealed_current_mind_effect": "NONE",
        "direction_v2_history_effect": "NONE",
        "decision_effect": "NONE",
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
        "promotion_eligible": False,
    }


def register_copper_pit_routes(app, settings, collector_auth) -> None:
    @app.post("/v1/internal/copper/pit-candles/collect")
    async def copper_pit_candles_collect(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        store = CopperCandleObservationStore(settings.database_url)
        return await collect_copper_pit_candles(
            get_provider(settings),
            store,
        )

    @app.get("/v1/internal/copper/pit-candles/status")
    async def copper_pit_candles_status(
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        store = CopperCandleObservationStore(settings.database_url)
        await store.initialize()
        status = await store.status()
        return {
            **status,
            "research_only": True,
            "production_rules_changed": False,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "capital_committed": 0,
        }

    @app.get("/v1/internal/copper/information-board")
    async def copper_information_board(
        as_of: str | None = None,
        x_collector_token: str | None = Header(default=None),
    ):
        collector_auth(x_collector_token)
        return await read_copper_information_board_with_option_participation(
            settings.database_url,
            as_of=as_of,
        )

    @app.get("/v1/internal/copper/direction-v2-shadow")
    async def copper_direction_v2_shadow(
        as_of: str | None = None,
        x_collector_token: str | None = Header(default=None),
    ):
        """Read Direction V2 at any PIT timestamp without creating provenance."""
        collector_auth(x_collector_token)
        board = await read_copper_information_board_with_option_participation(
            settings.database_url,
            as_of=as_of,
        )
        return evaluate_copper_direction_v2_shadow(board)

    @app.post("/v1/internal/copper/direction-v2-shadow/evaluate")
    async def copper_direction_v2_shadow_evaluate(
        x_collector_token: str | None = Header(default=None),
    ):
        """Capture exactly-now Direction V2 plus shared-core prospective evidence.

        Both syntheses consume the same immutable first-seen PIT information board.
        Shared-core capture is research-only and fail-open: it cannot block or alter
        the established Direction V2 prospective stream or sealed Current Mind.
        """
        collector_auth(x_collector_token)
        evaluated_at = datetime.now(IST)
        board = await read_copper_information_board_with_option_participation(
            settings.database_url,
            as_of=evaluated_at.isoformat(),
        )
        evaluation = evaluate_copper_direction_v2_shadow(board)
        record = build_prospective_record(
            board,
            evaluation,
            evaluated_at=evaluated_at,
        )
        store = CopperDirectionV2ProspectiveStore(settings.database_url)
        await store.initialize()
        inserted = await store.insert_first_seen(record)

        try:
            shared = evaluate_copper_commodity_brain_prospective(board)
            shared_record = build_shared_prospective_record(
                board,
                shared,
                evaluated_at=evaluated_at,
            )
            shared_store = CopperCommodityBrainProspectiveStore(settings.database_url)
            await shared_store.initialize()
            shared_inserted = await shared_store.insert_first_seen(shared_record)
            shared_result = {
                **shared,
                "status": "EVALUATED",
                "same_pit_board_as_direction_v2": True,
                "prospective_persistence": {
                    "model_id": shared_record["model_id"],
                    "contract_version": shared_record["contract_version"],
                    "evaluation_id": shared_record["evaluation_id"],
                    "record_hash": shared_record["record_hash"],
                    "board_as_of": shared_record["board_as_of"],
                    "evaluated_at": shared_record["evaluated_at"],
                    "inserted_first_seen": shared_inserted,
                    "first_seen_immutable": True,
                    "outcome_fields_stored": False,
                    "historical_as_of_allowed": False,
                    "prospective_memory_eligible": False,
                },
            }
        except Exception as exc:
            shared_result = _shared_unavailable(exc)

        return {
            **evaluation,
            "prospective_persistence": {
                "contract_version": record["contract_version"],
                "evaluation_id": record["evaluation_id"],
                "record_hash": record["record_hash"],
                "board_as_of": record["board_as_of"],
                "evaluated_at": record["evaluated_at"],
                "inserted_first_seen": inserted,
                "first_seen_immutable": True,
                "outcome_fields_stored": False,
                "historical_as_of_allowed": False,
            },
            "shared_commodity_brain_prospective_v1": shared_result,
        }

    @app.get("/v1/internal/copper/direction-v2-shadow/prospective-status")
    async def copper_direction_v2_shadow_prospective_status(
        x_collector_token: str | None = Header(default=None),
    ):
        """Outcome-blind coverage only; this endpoint makes no performance claim."""
        collector_auth(x_collector_token)
        store = CopperDirectionV2ProspectiveStore(settings.database_url)
        await store.initialize()
        return await store.coverage()

    @app.get("/v1/internal/copper/shared-commodity-brain/prospective-status")
    async def copper_shared_commodity_brain_prospective_status(
        x_collector_token: str | None = Header(default=None),
    ):
        """Coverage of new shared-core prospective observations only."""
        collector_auth(x_collector_token)
        store = CopperCommodityBrainProspectiveStore(settings.database_url)
        await store.initialize()
        return await store.coverage()
