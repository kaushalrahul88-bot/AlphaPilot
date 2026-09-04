from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_news_intelligence import apply_crude_news_intelligence
from .crude_oil_mini_option_observation_store import (
    PROVENANCE_ID as OPTION_PROVENANCE_ID,
    TABLE_NAME as OPTION_TABLE_NAME,
)
from .crude_oil_mini_option_oi_premium_v1 import interpret_option_oi_premium
from .news import latest_commodity_news


IST = ZoneInfo("Asia/Kolkata")
PROVIDER = "GROWW"

NEWS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crude_news_observations (
    event_id TEXT PRIMARY KEY,
    headline TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    first_detected_at TIMESTAMPTZ NOT NULL,
    last_detected_at TIMESTAMPTZ NOT NULL,
    url TEXT,
    event_tags TEXT,
    raw_payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS crude_news_observations_first_detected_idx
    ON crude_news_observations (first_detected_at DESC);
"""

NEWS_UPSERT_SQL = """
INSERT INTO crude_news_observations (
    event_id, headline, source, published_at, first_detected_at,
    last_detected_at, url, event_tags, raw_payload
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (event_id)
DO UPDATE SET
    last_detected_at = EXCLUDED.last_detected_at,
    url = COALESCE(EXCLUDED.url, crude_news_observations.url),
    event_tags = EXCLUDED.event_tags,
    raw_payload = EXCLUDED.raw_payload;
"""


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _as_ist(value) -> datetime:
    return parse_ist_timestamp(value).astimezone(IST)


def _number(value):
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError, OverflowError):
        return None


def _event_id(row: dict) -> str:
    stable = "|".join(
        [
            str(row.get("source") or ""),
            str(row.get("headline") or ""),
            str(row.get("published_at") or ""),
        ]
    )
    return "crude-news-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def _initialize_news_sync(database_url: str) -> None:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(NEWS_SCHEMA_SQL)


async def initialize_news_store(database_url: str) -> None:
    await asyncio.to_thread(_initialize_news_sync, database_url)


def _persist_news_sync(database_url: str, rows: list[dict], detected_at: datetime) -> int:
    values = []
    for row in rows:
        headline = str(row.get("headline") or "").strip()
        source = str(row.get("source") or "").strip() or "UNKNOWN"
        if not headline or row.get("error"):
            continue
        published = None
        if row.get("published_at"):
            try:
                published = _as_ist(row["published_at"])
            except Exception:
                published = None
        values.append(
            (
                _event_id(row),
                headline,
                source,
                published,
                detected_at,
                detected_at,
                row.get("url"),
                json.dumps(row.get("event_tags") or [], separators=(",", ":")),
                json.dumps(row, separators=(",", ":"), default=str),
            )
        )
    if not values:
        return 0
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(NEWS_UPSERT_SQL, values)
    return len(values)


async def collect_live_crude_news(database_url: str, *, detected_at=None) -> dict:
    detected = _as_ist(detected_at or datetime.now(IST))
    await initialize_news_store(database_url)
    feed = await latest_commodity_news("CRUDEOIL", limit=6)
    rows = list(feed.get("items") or [])
    stored = await asyncio.to_thread(_persist_news_sync, database_url, rows, detected)
    return {
        "status": "COLLECTED" if stored else "NO_USABLE_NEWS",
        "provider": feed.get("provider"),
        "detected_at": detected.isoformat(),
        "fetched_items": len(rows),
        "stored_items": stored,
        "event_tags": feed.get("event_tags") or [],
        "point_in_time_basis": "FIRST_DETECTED_AT",
        "live_execution_enabled": False,
    }


def _read_news_as_of_sync(database_url: str, click_at: datetime, limit: int = 30) -> list[dict]:
    sql = """
        SELECT event_id, headline, source, published_at, first_detected_at,
               last_detected_at, url, event_tags, raw_payload
        FROM crude_news_observations
        WHERE first_detected_at <= %s
          AND (published_at IS NULL OR published_at <= %s)
        ORDER BY first_detected_at DESC
        LIMIT %s
    """
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (click_at, click_at, int(limit)))
            rows = cursor.fetchall()
    output = []
    for event_id, headline, source, published_at, first_detected, last_detected, url, tags, raw in rows:
        try:
            raw_payload = json.loads(raw or "{}")
        except Exception:
            raw_payload = {}
        try:
            event_tags = json.loads(tags or "[]")
        except Exception:
            event_tags = []
        output.append(
            {
                "event_id": event_id,
                "series": "CRUDE_NEWS",
                "headline": headline,
                "source": source,
                "published_at": published_at.isoformat() if published_at else None,
                "observed_at": first_detected.isoformat(),
                "available_at": first_detected.isoformat(),
                "first_detected_at": first_detected.isoformat(),
                "last_detected_at": last_detected.isoformat(),
                "url": url,
                "event_tags": event_tags,
                "value": {
                    "headline": headline,
                    "event_tags": event_tags,
                    "source_payload": raw_payload,
                },
                "novelty": "NEW",
                "availability_basis": "FIRST_DETECTED_AT",
            }
        )
    return output


def _read_option_rows_as_of_sync(database_url: str, click_at: datetime) -> list[dict]:
    """Read only prospectively captured, immutable CRUDEOILM option observations.

    There is intentionally no fallback to ``commodity_option_snapshots``. A click
    before immutable capture began must see no option state rather than a mutable
    row whose first-seen value can no longer be reconstructed.
    """
    sql = f"""
        WITH buckets AS (
            SELECT DISTINCT sample_bucket_at
            FROM {OPTION_TABLE_NAME}
            WHERE provider = %s
              AND underlying_symbol = 'CRUDEOILM'
              AND sample_bucket_at <= %s
              AND observed_at <= %s
              AND collected_at <= %s
            ORDER BY sample_bucket_at DESC
            LIMIT 2
        )
        SELECT trading_symbol, expiry_date, strike, option_type, lot_size,
               sample_bucket_at, observed_at, collected_at, underlying_price,
               last_price, volume, open_interest, bid_price, ask_price
        FROM {OPTION_TABLE_NAME}
        WHERE provider = %s
          AND underlying_symbol = 'CRUDEOILM'
          AND sample_bucket_at IN (SELECT sample_bucket_at FROM buckets)
          AND sample_bucket_at <= %s
          AND observed_at <= %s
          AND collected_at <= %s
        ORDER BY sample_bucket_at DESC, expiry_date, strike, option_type
    """
    params = (
        PROVIDER,
        click_at,
        click_at,
        click_at,
        PROVIDER,
        click_at,
        click_at,
        click_at,
    )
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

    keys = (
        "trading_symbol",
        "expiry_date",
        "strike",
        "option_type",
        "lot_size",
        "sample_bucket_at",
        "observed_at",
        "collected_at",
        "underlying_price",
        "last_price",
        "volume",
        "open_interest",
        "bid_price",
        "ask_price",
    )
    output = []
    for values in rows:
        item = dict(zip(keys, values))
        for key in ("expiry_date", "sample_bucket_at", "observed_at", "collected_at"):
            value = item.get(key)
            item[key] = value.isoformat() if value is not None else None
        for key in (
            "strike",
            "underlying_price",
            "last_price",
            "volume",
            "open_interest",
            "bid_price",
            "ask_price",
        ):
            item[key] = _number(item.get(key))
        output.append(item)
    return output


def _immutable_option_provenance(*, verified: bool) -> dict:
    return {
        "source_table": OPTION_TABLE_NAME if verified else None,
        "first_seen_immutable": bool(verified),
        "provenance_id": OPTION_PROVENANCE_ID if verified else None,
        "historical_backfill_used": False,
        "mutable_generic_fallback_used": False,
    }


def summarize_option_positioning(
    rows: list[dict],
    click_at,
    *,
    immutable_provenance_verified: bool = False,
) -> dict:
    click = _as_ist(click_at)
    provenance = _immutable_option_provenance(verified=immutable_provenance_verified)
    buckets = sorted(
        {str(row.get("sample_bucket_at")) for row in rows if row.get("sample_bucket_at")},
        reverse=True,
    )
    if not buckets:
        return {
            "status": "UNAVAILABLE",
            "as_of": click.isoformat(),
            "reason": "NO_IMMUTABLE_PIT_OPTION_SNAPSHOT" if immutable_provenance_verified else "NO_PIT_OPTION_SNAPSHOT",
            "futures_oi_required": False,
            "counts_for_direction": False,
            "pit_filter": "sample_bucket_at, observed_at and collected_at must all be <= click",
            **provenance,
        }

    latest_bucket = buckets[0]
    previous_bucket = buckets[1] if len(buckets) > 1 else None
    latest_all = [row for row in rows if str(row.get("sample_bucket_at")) == latest_bucket]
    previous_all = [
        row for row in rows
        if previous_bucket and str(row.get("sample_bucket_at")) == previous_bucket
    ]

    expiries = sorted({str(row.get("expiry_date")) for row in latest_all if row.get("expiry_date")})
    nearest_expiry = expiries[0] if expiries else None
    latest = [
        row for row in latest_all
        if nearest_expiry is None or str(row.get("expiry_date")) == nearest_expiry
    ]
    previous = [
        row for row in previous_all
        if nearest_expiry is None or str(row.get("expiry_date")) == nearest_expiry
    ]
    previous_by_symbol = {str(row.get("trading_symbol")): row for row in previous}

    contracts = []
    for row in latest:
        prior = previous_by_symbol.get(str(row.get("trading_symbol")))
        oi = _number(row.get("open_interest"))
        prior_oi = _number((prior or {}).get("open_interest"))
        current_premium = _number(row.get("last_price"))
        prior_premium = _number((prior or {}).get("last_price"))
        contracts.append(
            {
                **row,
                "oi_change_from_previous_bucket": (
                    oi - prior_oi if oi is not None and prior_oi is not None else None
                ),
                "premium_change_from_previous_bucket": (
                    current_premium - prior_premium
                    if current_premium is not None and prior_premium is not None
                    else None
                ),
            }
        )

    ce = [row for row in contracts if str(row.get("option_type") or "").upper() == "CE"]
    pe = [row for row in contracts if str(row.get("option_type") or "").upper() == "PE"]

    def total(rows_, key):
        usable = [
            value
            for value in (_number(row.get(key)) for row in rows_)
            if value is not None
        ]
        return sum(usable) if usable else None

    ce_oi = total(ce, "open_interest")
    pe_oi = total(pe, "open_interest")
    ce_oi_change = total(ce, "oi_change_from_previous_bucket")
    pe_oi_change = total(pe, "oi_change_from_previous_bucket")
    underlying_price = next(
        (
            value
            for value in (_number(row.get("underlying_price")) for row in contracts)
            if value is not None
        ),
        None,
    )

    def top_oi(rows_):
        ranked = [row for row in rows_ if _number(row.get("open_interest")) is not None]
        ranked.sort(key=lambda row: _number(row.get("open_interest")) or 0.0, reverse=True)
        return [
            {
                "strike": row.get("strike"),
                "open_interest": row.get("open_interest"),
                "oi_change_from_previous_bucket": row.get("oi_change_from_previous_bucket"),
                "last_price": row.get("last_price"),
            }
            for row in ranked[:3]
        ]

    latest_time = _as_ist(latest_bucket)
    age_minutes = max(0.0, (click - latest_time).total_seconds() / 60.0)
    available_times = []
    for row in contracts:
        try:
            available_times.append(_as_ist(row.get("collected_at")))
        except Exception:
            continue
    latest_available_at = max(available_times).isoformat() if available_times else None

    oi_covered = sum(1 for row in contracts if _number(row.get("open_interest")) is not None)
    interpretation = interpret_option_oi_premium(
        contracts,
        previous_sample_bucket_at=previous_bucket,
    )
    return {
        "status": "AVAILABLE" if contracts else "UNAVAILABLE",
        "as_of": click.isoformat(),
        "sample_bucket_at": latest_bucket,
        "previous_sample_bucket_at": previous_bucket,
        "available_at": latest_available_at,
        "age_minutes": round(age_minutes, 3),
        "nearest_expiry": nearest_expiry,
        "underlying_price": underlying_price,
        "contracts": contracts,
        "contract_count": len(contracts),
        "ce_contracts": len(ce),
        "pe_contracts": len(pe),
        "oi_contracts": oi_covered,
        "ce_total_oi": ce_oi,
        "pe_total_oi": pe_oi,
        "put_call_oi_ratio": (
            pe_oi / ce_oi if pe_oi is not None and ce_oi not in (None, 0) else None
        ),
        "ce_total_oi_change_from_previous_bucket": ce_oi_change,
        "pe_total_oi_change_from_previous_bucket": pe_oi_change,
        "ce_total_volume": total(ce, "volume"),
        "pe_total_volume": total(pe, "volume"),
        "top_ce_oi": top_oi(ce),
        "top_pe_oi": top_oi(pe),
        "direction": interpretation["direction"],
        "counts_for_direction": interpretation["counts_for_direction"],
        "directional_inference": interpretation["status"],
        "oi_premium_interpretation": interpretation,
        "model_registration": interpretation["registration"],
        "futures_oi_required": False,
        "futures_oi_role": "OPTIONAL_SUPPORTING_CONTEXT_ONLY",
        "pit_filter": "sample_bucket_at, observed_at and collected_at must all be <= click",
        **provenance,
    }


def prepare_news_context(rows: list[dict], click_at) -> dict:
    click = _as_ist(click_at)
    visible = []
    for row in rows or []:
        try:
            if _as_ist(row.get("available_at")) <= click:
                visible.append(row)
        except Exception:
            continue
    intelligence = apply_crude_news_intelligence(visible)
    transmitted = [
        row
        for row in intelligence.get("records") or []
        if (row.get("news_intelligence") or {}).get("disposition") != "BLOCK"
    ]
    event_records = []
    for row in transmitted:
        assessment = row.get("news_intelligence") or {}
        effect = str(assessment.get("effect") or "UNKNOWN").upper()
        event_records.append(
            {
                "series": "CRUDE_NEWS",
                "event_id": row.get("event_id"),
                "event_type": assessment.get("event_type") or "CRUDE_NEWS",
                "observed_at": row.get("observed_at") or row.get("available_at"),
                "available_at": row.get("available_at") or row.get("observed_at"),
                "source": row.get("source"),
                "value": {
                    "headline": row.get("headline"),
                    "mechanism_stance": effect if effect in {"BULLISH", "BEARISH"} else "UNKNOWN",
                    "transmission_mechanism": assessment.get("transmission_mechanism"),
                    "materiality_status": assessment.get("materiality"),
                    "novelty_status": assessment.get("novelty") or "NEW",
                    "reaction": {
                        "direction": "UNKNOWN",
                        "confirmed": False,
                        "confirmation_sources": [],
                    },
                    "news_intelligence": assessment,
                },
            }
        )
    latest = transmitted[0] if transmitted else (visible[0] if visible else None)
    context_record = None
    if latest:
        assessment = latest.get("news_intelligence") or {}
        context_record = {
            "series": "CRUDE_NEWS",
            "observed_at": latest.get("observed_at") or latest.get("available_at"),
            "available_at": latest.get("available_at") or latest.get("observed_at"),
            "source": latest.get("source") or "CRUDE_NEWS_COLLECTOR",
            "quality": "PIT_FIRST_DETECTED",
            "value": {
                "headline": latest.get("headline"),
                "visible_events": len(visible),
                "transmitted_events": len(transmitted),
                "disposition": assessment.get("disposition"),
                "effect": assessment.get("effect"),
            },
        }
    return {
        "status": "AVAILABLE" if visible else "UNAVAILABLE",
        "as_of": click.isoformat(),
        "visible_count": len(visible),
        "transmitted_count": len(transmitted),
        "counts": intelligence.get("counts") or {"ALLOW": 0, "CONTEXT_ONLY": 0, "BLOCK": 0},
        "context_record": context_record,
        "event_records": event_records,
        "records": transmitted,
        "directional_vote_policy": "NO_HEADLINE_ONLY_VOTE; EVENT_REACTION_CONFIRMATION_REQUIRED",
        "pit_basis": "FIRST_DETECTED_AT",
    }


def option_context_record(option_positioning: dict) -> dict | None:
    if (option_positioning or {}).get("status") != "AVAILABLE":
        return None
    return {
        "series": "MCX_CRUDEOILM_OPTION",
        "observed_at": option_positioning.get("sample_bucket_at"),
        "available_at": option_positioning.get("available_at")
        or option_positioning.get("sample_bucket_at"),
        "source": (
            "GROWW_CRUDEOILM_FIRST_SEEN_IMMUTABLE_OPTIONS"
            if option_positioning.get("first_seen_immutable")
            else "GROWW_PERSISTED_MCX_OPTION_SNAPSHOTS"
        ),
        "quality": (
            "PIT_FIRST_SEEN_IMMUTABLE"
            if option_positioning.get("first_seen_immutable")
            else "PIT_OBSERVED"
        ),
        "value": {
            "nearest_expiry": option_positioning.get("nearest_expiry"),
            "underlying_price": option_positioning.get("underlying_price"),
            "ce_total_oi": option_positioning.get("ce_total_oi"),
            "pe_total_oi": option_positioning.get("pe_total_oi"),
            "put_call_oi_ratio": option_positioning.get("put_call_oi_ratio"),
            "ce_total_oi_change_from_previous_bucket": option_positioning.get(
                "ce_total_oi_change_from_previous_bucket"
            ),
            "pe_total_oi_change_from_previous_bucket": option_positioning.get(
                "pe_total_oi_change_from_previous_bucket"
            ),
            "top_ce_oi": option_positioning.get("top_ce_oi"),
            "top_pe_oi": option_positioning.get("top_pe_oi"),
            "direction": option_positioning.get("direction"),
            "counts_for_direction": option_positioning.get("counts_for_direction"),
            "directional_inference": option_positioning.get("directional_inference"),
            "model_id": (option_positioning.get("oi_premium_interpretation") or {}).get(
                "model_id"
            ),
            "first_seen_immutable": option_positioning.get("first_seen_immutable"),
            "provenance_id": option_positioning.get("provenance_id"),
        },
    }


async def read_live_crude_inputs(database_url: str, *, click_at) -> dict:
    click = _as_ist(click_at)
    await initialize_news_store(database_url)
    option_rows, news_rows = await asyncio.gather(
        asyncio.to_thread(_read_option_rows_as_of_sync, database_url, click),
        asyncio.to_thread(_read_news_as_of_sync, database_url, click, 30),
    )
    option_positioning = summarize_option_positioning(
        option_rows,
        click,
        immutable_provenance_verified=True,
    )
    news = prepare_news_context(news_rows, click)
    return {
        "as_of": click.isoformat(),
        "point_in_time": True,
        "option_positioning": option_positioning,
        "news": news,
        "futures_oi_required": False,
        "live_execution_enabled": False,
    }
