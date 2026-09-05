"""Render lifecycle registration for the insert-only prospective BTC proof ledger.

Schema initialization and immutability hardening are persistence-only. They
perform no provider request, freeze no thesis, resolve no outcome, and start no
scheduler or execution path.
"""
from __future__ import annotations

import logging

from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig
from app.crypto_btc_prospective_thesis_immutability import harden_btc_prospective_thesis_schema
from app.crypto_btc_prospective_thesis_postgres import PostgresProspectiveBtcThesisTapeStore

logger = logging.getLogger("alphapilot.crypto.btc.prospective-proof")


def register_btc_prospective_proof_startup(app, settings) -> None:
    @app.on_event("startup")
    async def _initialize_btc_prospective_proof_ledger() -> None:
        try:
            config = BtcProspectiveProofRuntimeConfig.from_env()
        except Exception as exc:
            logger.error("BTC prospective proof configuration invalid: %s: %s", exc.__class__.__name__, str(exc)[:300])
            return

        if not config.postgres_enabled:
            logger.info("BTC prospective proof ledger disabled")
            return

        app_database = str(getattr(settings, "database_url", "") or "").strip()
        if not app_database or config.database_url != app_database:
            logger.error("BTC prospective proof database does not match AlphaPilot DATABASE_URL")
            return

        try:
            store = PostgresProspectiveBtcThesisTapeStore(config.database_url)
            await store.initialize()
            await harden_btc_prospective_thesis_schema(config.database_url)
        except Exception as exc:
            logger.error("BTC prospective proof schema initialization failed: %s: %s", exc.__class__.__name__, str(exc)[:300])
            return

        logger.info(
            "BTC prospective proof ledger ready horizon_hours=%s database_immutable=true automatic_decisions=false automatic_resolution=false",
            config.evaluation_horizon_hours,
        )
