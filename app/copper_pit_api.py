from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Header

from .copper_candle_observation_store import CopperCandleObservationStore
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
        """Capture exactly-now Direction V2 as first-seen prospective evidence.

        This endpoint intentionally accepts no ``as_of`` parameter. Historical PIT
        reads are useful for diagnostics but cannot be admitted to the prospective
        evaluation ledger.
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
        return {
            **evaluation,
            "prospective_persistence": {
                "evaluation_id": record["evaluation_id"],
                "record_hash": record["record_hash"],
                "board_as_of": record["board_as_of"],
                "evaluated_at": record["evaluated_at"],
                "inserted_first_seen": inserted,
                "first_seen_immutable": True,
                "outcome_fields_stored": False,
                "historical_as_of_allowed": False,
            },
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
