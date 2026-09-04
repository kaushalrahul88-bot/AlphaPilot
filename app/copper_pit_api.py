from __future__ import annotations

from fastapi import Header

from .copper_candle_observation_store import CopperCandleObservationStore
from .copper_pit_candles import collect_copper_pit_candles
from .copper_pit_information_board_v2 import read_copper_information_board
from .providers.factory import get_provider


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
        return await read_copper_information_board(
            settings.database_url,
            as_of=as_of,
        )
