"""Frozen 96-click replay of the first shared Delta/PIT BTC collection day."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from app.coindcx_btc_public_provider import CoinDcxBtcProviderPolicy, CoinDcxBtcPublicProvider
from app.crypto_btc_delta_options_probe_postgres import TABLE_NAME as DELTA_TABLE
from app.crypto_btc_live_shadow_click import select_delta_option_for_shadow_entry
from app.crypto_btc_pit_postgres import PostgresBtcPitArchiveStore, TABLE_NAME as PIT_TABLE
from app.crypto_btc_prospective_proof_bridge import (
    freeze_prospective_btc_thesis_from_existing_sources,
    resolve_prospective_btc_thesis_from_coindcx,
)
from app.crypto_btc_prospective_proof_runtime import BtcProspectiveProofRuntimeConfig
from app.delta_india_btc_derivatives_context import (
    DeltaIndiaBtcDerivativesContextPolicy,
    DeltaIndiaBtcDerivativesPublicProvider,
)

UTC = timezone.utc
MODE = "BTC_FIRST_SHARED_24H_15M_REPLAY_V1"
CLICK_COUNT = 96
CLICK_STEP = timedelta(minutes=15)


def _connect(database_url: str):
    import psycopg
    return psycopg.connect(database_url, connect_timeout=10)


def frozen_clicks(start: datetime) -> list[datetime]:
    start = start.astimezone(UTC)
    return [start + index * CLICK_STEP for index in range(CLICK_COUNT)]


def _shared_window_start_from_cursor(cur) -> datetime:
    cur.execute(
        f"SELECT GREATEST((SELECT MIN(first_seen_at) FROM {DELTA_TABLE}),"
        f" (SELECT MIN(first_seen_at) FROM {PIT_TABLE}))"
    )
    start = cur.fetchone()[0]
    if start is None:
        raise ValueError("shared Delta/PIT collection window is empty")
    return start.astimezone(UTC)


def _load_shared_window_start(database_url: str) -> datetime:
    """Load only the shared replay boundary, without materializing option payloads."""
    with _connect(database_url) as conn, conn.cursor() as cur:
        return _shared_window_start_from_cursor(cur)


def _load_inputs(database_url: str) -> tuple[datetime, list[dict[str, Any]]]:
    """Load the legacy options replay inputs, including Delta payloads when required."""
    with _connect(database_url) as conn, conn.cursor() as cur:
        start = _shared_window_start_from_cursor(cur)
        end = start + timedelta(hours=28, minutes=2)
        cur.execute(
            f"SELECT first_seen_at, payload FROM {DELTA_TABLE} "
            "WHERE first_seen_at >= %s AND first_seen_at <= %s ORDER BY first_seen_at",
            (start - timedelta(minutes=3), end),
        )
        rows = [{"at": row[0].astimezone(UTC), "payload": row[1]} for row in cur.fetchall()]
    return start, rows


class _CachedCoinDcx:
    def __init__(self, rows: dict[str, list[Any]]):
        self.rows = rows

    def fetch_spot_candles(self, *, interval: str, start_at=None, end_at=None, limit=1000):
        result = [r for r in self.rows[interval]
                  if (start_at is None or r.open_at >= start_at)
                  and (end_at is None or r.open_at <= end_at)]
        return result[-int(limit):]


def _fetch_spot(provider: CoinDcxBtcPublicProvider, *, interval: str,
                start: datetime, end: datetime) -> list[Any]:
    seconds = 60 if interval == "1m" else 3600
    chunk = timedelta(seconds=seconds * 900)
    cursor, result = start, {}
    while cursor < end:
        chunk_end = min(end, cursor + chunk)
        for row in provider.fetch_spot_candles(
            interval=interval, start_at=cursor, end_at=chunk_end, limit=1000
        ):
            result[row.open_at] = row
        cursor = chunk_end
    return [result[key] for key in sorted(result)]


def _latest_snapshot(rows: list[dict], click: datetime) -> dict | None:
    visible = [row for row in rows if row["at"] <= click]
    return None if not visible else visible[-1]["payload"]


def _option_outcome(rows: list[dict], entry: dict, decision: datetime, due: datetime) -> dict:
    symbol, ask = entry["symbol"], float(entry["entry_ask"])
    observations = []
    for row in rows:
        if not decision < row["at"] <= due + timedelta(seconds=120):
            continue
        quote = next((q for q in row["payload"].get("quotes", []) if q.get("symbol") == symbol), None)
        if quote is not None and quote.get("best_bid") is not None and float(quote["best_bid"]) > 0:
            observations.append((row["at"], float(quote["best_bid"])))
    terminal = min(observations, key=lambda item: abs((item[0] - due).total_seconds())) if observations else None
    if terminal is None or abs((terminal[0] - due).total_seconds()) > 120:
        return {"status": "UNRESOLVED_TERMINAL_QUOTE", "symbol": symbol}
    returns = [(bid / ask - 1.0) * 100.0 for _, bid in observations if _ <= due]
    return {
        "status": "RESOLVED", "symbol": symbol, "entry_ask": ask,
        "terminal_bid": terminal[1], "terminal_quote_at": terminal[0].isoformat(),
        "terminal_return_pct": (terminal[1] / ask - 1.0) * 100.0,
        "mfe_bid_return_pct": max(returns) if returns else None,
        "mae_bid_return_pct": min(returns) if returns else None,
    }


async def run_first24h_15m(database_url: str) -> dict[str, Any]:
    start, snapshots = await asyncio.to_thread(_load_inputs, database_url)
    clicks, horizon = frozen_clicks(start), timedelta(hours=4)
    public = CoinDcxBtcPublicProvider(CoinDcxBtcProviderPolicy(enabled=True, timeout_seconds=25))
    one_hour, one_minute = await asyncio.gather(
        asyncio.to_thread(_fetch_spot, public, interval="1h", start=start-timedelta(hours=32), end=start+timedelta(hours=24)),
        asyncio.to_thread(_fetch_spot, public, interval="1m", start=start-timedelta(minutes=15), end=start+timedelta(hours=28, minutes=2)),
    )
    oi_provider = DeltaIndiaBtcDerivativesPublicProvider(
        DeltaIndiaBtcDerivativesContextPolicy(enabled=True, timeout_seconds=25, resolution="5m")
    )
    oi_rows = await asyncio.to_thread(
        oi_provider.fetch_oi_candles, start_at=start-timedelta(hours=2, minutes=15),
        end_at=start+timedelta(hours=24), resolution="5m",
    )
    cached = _CachedCoinDcx({"1h": one_hour, "1m": one_minute})
    pit = PostgresBtcPitArchiveStore(database_url)
    tape_policy = BtcProspectiveProofRuntimeConfig(evaluation_horizon_hours=4).tape_policy()
    results = []
    for index, click in enumerate(clicks):
        proof = await freeze_prospective_btc_thesis_from_existing_sources(
            click_id=f"first24h-15m-{index:02d}", decision_at=click, provider=cached,
            pit_store=pit, tape_policy=tape_policy, delta_oi_rows=oi_rows,
        )
        frozen = proof.get("frozen_thesis")
        direction = "UNKNOWN" if frozen is None else str((frozen.get("decision") or {}).get("market_direction", "UNKNOWN"))
        snapshot = _latest_snapshot(snapshots, click)
        option = ({"status": "NO_TRADE", "reason": "NO_PRIOR_DELTA_SNAPSHOT", "option_entry": None}
                  if snapshot is None else select_delta_option_for_shadow_entry(
                      snapshot, market_direction=direction, decision_at=click,
                      expected_holding_hours=4,
                  ))
        underlying = None
        if frozen is not None:
            underlying = await resolve_prospective_btc_thesis_from_coindcx(
                frozen_record=frozen, resolution_at=click+horizon+timedelta(minutes=2), provider=cached
            )
        entry = option.get("option_entry")
        results.append({
            "click_index": index, "decision_at": click.isoformat(), "direction": direction,
            "proof_status": proof.get("status"), "proof_reason": proof.get("reason"),
            "delta_oi_evidence_status": proof.get("delta_oi_evidence_status"),
            "derivatives_evidence_status": proof.get("derivatives_evidence_status"),
            "option_status": option.get("status"), "option_reason": option.get("reason"),
            "option_entry": entry,
            "underlying_outcome": None if underlying is None else underlying.get("outcome"),
            "option_outcome": None if entry is None else _option_outcome(snapshots, entry, click, click+horizon),
        })
    directions = Counter(r["direction"] for r in results)
    option_states = Counter(r["option_status"] for r in results)
    return {
        "mode": MODE, "status": "COMPLETED", "window_start": start.isoformat(),
        "window_end_exclusive": (start+timedelta(hours=24)).isoformat(),
        "click_interval_minutes": 15, "scheduled_clicks": len(clicks),
        "summary": {"directions": dict(directions), "option_states": dict(option_states),
                    "option_entries": option_states.get("SHADOW_ENTRY_FROZEN", 0)},
        "coverage": {"delta_snapshots_loaded": len(snapshots), "spot_1h_candles": len(one_hour),
                     "spot_1m_candles": len(one_minute), "delta_oi_5m_candles": len(oi_rows)},
        "methodology": {"click_grid_frozen": True, "end_exclusive": True,
                        "oi_visible_only_after_bar_completion": True,
                        "option_entry_fill": "OBSERVED_BEST_ASK",
                        "option_exit_fill": "OBSERVED_EXACT_CONTRACT_BEST_BID",
                        "retuned_after_outcomes": False},
        "safety": {"research_only": True, "live_execution": False, "capital_committed_inr": 0,
                   "futures_context_only": True, "futures_trade_generated": False},
        "clicks": results,
    }
