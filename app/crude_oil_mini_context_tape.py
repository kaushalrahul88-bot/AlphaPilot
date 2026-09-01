from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_pit_context_probe import probe_crude_oil_pit_context

IST = ZoneInfo("Asia/Kolkata")
SNAPSHOT_ID = "CRUDEOILM_CONTEXT_20260601_20260831_DISCOVERY_V1"
FROZEN_START = "2026-06-01T00:00:00+05:30"
FROZEN_END_EXCLUSIVE = "2026-09-01T00:00:00+05:30"
REQUIRED_SERIES = ("WTI_CRUDE", "BRENT_CRUDE", "USDINR", "DXY")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crude_oil_mini_research_context_tapes (
    snapshot_id TEXT PRIMARY KEY,
    source_grade TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end_exclusive TIMESTAMPTZ NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _sha256(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonicalize_context_probe(probe: dict) -> dict:
    feeds = probe.get("feeds") or {}
    out = {}
    for series in REQUIRED_SERIES:
        feed = feeds.get(series) or {}
        if feed.get("status") != "AVAILABLE":
            raise RuntimeError(f"{series} is not available for the frozen context window")
        rows = []
        seen = set()
        for raw in feed.get("data") or []:
            start = parse_ist_timestamp(raw.get("bar_start")).astimezone(IST)
            available = parse_ist_timestamp(raw.get("available_at")).astimezone(IST)
            if available != start + timedelta(minutes=60):
                raise RuntimeError(f"{series} violates one-hour completed-bar visibility at {start.isoformat()}")
            key = start.isoformat()
            if key in seen:
                raise RuntimeError(f"{series} contains duplicate bar {key}")
            seen.add(key)
            rows.append({
                "bar_start": key,
                "available_at": available.isoformat(),
                "open": float(raw["open"]),
                "high": float(raw["high"]),
                "low": float(raw["low"]),
                "close": float(raw["close"]),
                "volume": None if raw.get("volume") is None else float(raw["volume"]),
            })
        rows.sort(key=lambda row: row["bar_start"])
        if not rows:
            raise RuntimeError(f"{series} has no canonical rows")
        out[series] = {
            "ticker": str(feed.get("ticker") or ""),
            "bar_minutes": 60,
            "source": "Yahoo Finance public chart",
            "source_grade": "E_DISCOVERY",
            "rows": rows,
        }
    return {
        "snapshot_id": SNAPSHOT_ID,
        "window_start": FROZEN_START,
        "window_end_exclusive": FROZEN_END_EXCLUSIVE,
        "source_grade": "E_DISCOVERY",
        "promotion_eligible": False,
        "research_only": True,
        "feeds": out,
        "governance": {
            "point_in_time_completed_bars_only": True,
            "context_never_substitutes_for_crude_oil_mini": True,
            "news_included": False,
            "option_market_data_included": False,
            "outcomes_used_to_build_tape": False,
            "authorized_or_independent_validation_required_before_promotion": True,
        },
    }


def certify_context_tape(payload: dict) -> dict:
    integrity_errors = []
    per_series = {}
    for series in REQUIRED_SERIES:
        feed = (payload.get("feeds") or {}).get(series) or {}
        rows = list(feed.get("rows") or [])
        starts = []
        for row in rows:
            try:
                start = parse_ist_timestamp(row["bar_start"]).astimezone(IST)
                available = parse_ist_timestamp(row["available_at"]).astimezone(IST)
                if available != start + timedelta(minutes=60):
                    integrity_errors.append(f"{series}:visibility:{start.isoformat()}")
                o, h, l, c = (float(row[key]) for key in ("open", "high", "low", "close"))
                if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h):
                    integrity_errors.append(f"{series}:ohlc:{start.isoformat()}")
                starts.append(start)
            except Exception:
                integrity_errors.append(f"{series}:invalid_row")
        if len(starts) != len(set(starts)):
            integrity_errors.append(f"{series}:duplicate_timestamp")
        if any(b <= a for a, b in zip(starts, starts[1:])):
            integrity_errors.append(f"{series}:non_monotonic")
        per_series[series] = {
            "rows": len(rows),
            "first_bar_start": starts[0].isoformat() if starts else None,
            "last_bar_start": starts[-1].isoformat() if starts else None,
            "source_grade": feed.get("source_grade"),
        }
    ready = bool(payload) and not integrity_errors and all(per_series[s]["rows"] > 0 for s in REQUIRED_SERIES)
    return {
        "mode": "CRUDE_OIL_MINI_FROZEN_CONTEXT_TAPE_V1",
        "status": "CERTIFIED_DISCOVERY" if ready else "REJECTED",
        "snapshot_id": payload.get("snapshot_id"),
        "window_start": payload.get("window_start"),
        "window_end_exclusive": payload.get("window_end_exclusive"),
        "source_grade": payload.get("source_grade"),
        "promotion_eligible": False,
        "research_only": True,
        "tape_sha256": _sha256(payload) if payload else None,
        "series": per_series,
        "integrity_errors": integrity_errors,
        "governance": payload.get("governance") or {},
    }


class PostgresCrudeContextTapeStore:
    def __init__(self, database_url: str):
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for Crude context tape storage")

    def _connect(self):
        import psycopg
        return psycopg.connect(self.database_url, connect_timeout=10)

    def _initialize_sync(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    def _read_sync(self, snapshot_id: str):
        sql = """SELECT payload_json, payload_sha256, source_grade
                 FROM crude_oil_mini_research_context_tapes WHERE snapshot_id=%s"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (snapshot_id,))
                row = cur.fetchone()
        if not row:
            return None
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {"payload": payload, "stored_sha256": row[1], "source_grade": row[2]}

    async def read(self, snapshot_id: str = SNAPSHOT_ID):
        return await asyncio.to_thread(self._read_sync, snapshot_id)

    def _insert_once_sync(self, payload: dict, digest: str):
        sql = """INSERT INTO crude_oil_mini_research_context_tapes
                 (snapshot_id, source_grade, window_start, window_end_exclusive, payload_sha256, payload_json)
                 VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                 ON CONFLICT (snapshot_id) DO NOTHING"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    SNAPSHOT_ID,
                    "E_DISCOVERY",
                    FROZEN_START,
                    FROZEN_END_EXCLUSIVE,
                    digest,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ))
                inserted = cur.rowcount == 1
        return inserted

    async def insert_once(self, payload: dict, digest: str):
        return await asyncio.to_thread(self._insert_once_sync, payload, digest)


async def build_or_read_frozen_context_tape(store: PostgresCrudeContextTapeStore) -> dict:
    await store.initialize()
    existing = await store.read(SNAPSHOT_ID)
    if existing:
        payload = existing["payload"]
        certification = certify_context_tape(payload)
        if certification["tape_sha256"] != existing["stored_sha256"]:
            raise RuntimeError("Stored Crude context tape digest does not match its payload")
        return {"created": False, "payload": payload, "certification": certification}

    probe = await probe_crude_oil_pit_context(FROZEN_START, FROZEN_END_EXCLUSIVE)
    missing = sorted(set(REQUIRED_SERIES) - set(probe.get("full_window_hourly_candidates") or []))
    if missing:
        raise RuntimeError(f"Cannot freeze Crude discovery context; missing full-window series: {missing}")
    payload = canonicalize_context_probe(probe)
    certification = certify_context_tape(payload)
    if certification["status"] != "CERTIFIED_DISCOVERY":
        raise RuntimeError(f"Crude context tape certification failed: {certification}")
    await store.insert_once(payload, certification["tape_sha256"])
    stored = await store.read(SNAPSHOT_ID)
    if not stored:
        raise RuntimeError("Crude context tape was not persisted")
    if stored["stored_sha256"] != certification["tape_sha256"]:
        # Another process may have won the immutable insert; use the already frozen tape.
        payload = stored["payload"]
        certification = certify_context_tape(payload)
        if certification["tape_sha256"] != stored["stored_sha256"]:
            raise RuntimeError("Persisted Crude context tape is internally inconsistent")
        return {"created": False, "payload": payload, "certification": certification}
    return {"created": True, "payload": stored["payload"], "certification": certification}
