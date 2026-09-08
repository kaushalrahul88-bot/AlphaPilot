"""Authenticated API for the fixed BTC first-day replay."""
from __future__ import annotations

from fastapi import Header, HTTPException

from app.crypto_btc_first24h_backtest import run_first24h_15m
from app.crypto_btc_first24h_underlying_backtest import run_first24h_underlying_15m


def register_crypto_btc_first24h_backtest_routes(app, settings, collector_auth) -> None:
    @app.post("/v1/internal/crypto/btc/backtest/first-24h-15m")
    async def run_backtest(x_collector_token: str | None = Header(default=None)):
        collector_auth(x_collector_token)
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            raise HTTPException(status_code=503, detail="DATABASE_URL is unavailable")
        return await run_first24h_15m(database_url)

    @app.post("/v1/internal/crypto/btc/backtest/first-24h-15m-underlying")
    async def run_underlying_backtest(x_collector_token: str | None = Header(default=None)):
        collector_auth(x_collector_token)
        database_url = str(getattr(settings, "database_url", "") or "").strip()
        if not database_url:
            raise HTTPException(status_code=503, detail="DATABASE_URL is unavailable")
        return await run_first24h_underlying_15m(database_url)
