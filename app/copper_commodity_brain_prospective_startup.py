from __future__ import annotations

from .copper_commodity_brain_prospective_store_v1 import initialize_store


def register_copper_commodity_brain_prospective_startup(app, settings) -> None:
    """Materialize the shared Copper prospective ledger at service startup.

    Startup performs schema initialization only. It never reads market data,
    evaluates the Commodity Brain, changes sealed Copper Current Mind Phase 1,
    writes outcomes, generates trade geometry, or enables execution. Creating the
    ledger before the first open-session sample removes first-click schema risk
    while preserving the prospective evidence boundary.
    """

    @app.on_event("startup")
    async def _initialize_copper_commodity_brain_prospective_store() -> None:
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            return
        await initialize_store(database_url)
