from __future__ import annotations

from .copper_direction_v2_prospective_store import initialize_store


def register_copper_direction_v2_prospective_startup(app, settings) -> None:
    """Materialize prospective Direction V2 schema at service startup.

    This performs schema initialization only. It never reads market data, creates an
    evaluation, changes Current Mind, or places an order. Keeping schema migration
    independent of the first market-time evaluation ensures contract-version
    provenance is ready before the next prospective sample arrives.
    """

    @app.on_event("startup")
    async def _initialize_copper_direction_v2_prospective_store() -> None:
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            return
        await initialize_store(database_url)
