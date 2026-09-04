from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .china_copper_macro_context import china_copper_macro_records
from .commodity_time import parse_ist_timestamp
from .copper_candle_observation_store import (
    PROVENANCE_ID as CANDLE_PROVENANCE_ID,
    TABLE_NAME as CANDLE_TABLE,
    CopperCandleObservationStore,
)
from .copper_option_observation_store import (
    PROVENANCE_ID as OPTION_PROVENANCE_ID,
    TABLE_NAME as OPTION_TABLE,
)
from .copper_point_in_time_context import visible_at
from .copper_research_brain import build_copper_snapshot
from .historical_context import PostgresHistoricalContextStore


IST = ZoneInfo("Asia/Kolkata")
MODEL_ID = "COPPER_PIT_INFORMATION_BOARD_V2"
LOOKBACK_DAYS = 7

LATEST_VISIBLE_CONTRACT_SQL = f"""
SELECT trading_symbol
FROM {CANDLE_TABLE}
WHERE timeframe_minutes = 5
  AND candle_at + (timeframe_minutes * INTERVAL '1 minute') <= %s
  AND collected_at <= %s
ORDER BY candle_at DESC, collected_at DESC, trading_symbol ASC
LIMIT 1
"""

OPTION_LATEST_BUCKET_SQL = f"""
SELECT
    trading_symbol, expiry_date, strike, option_type, lot_size,
    sample_bucket_at, observed_at, underlying_price, last_price,
    volume, open_interest, bid_price, ask_price, collected_at
FROM {OPTION_TABLE}
WHERE sample_bucket_at = (
    SELECT MAX(sample_bucket_at)
    FROM {OPTION_TABLE}
    WHERE sample_bucket_at <= %s
      AND observed_at <= %s
      AND collected_at <= %s
)
  AND sample_bucket_at <= %s
  AND observed_at <= %s
  AND collected_at <= %s
ORDER BY expiry_date ASC, option_type ASC, strike ASC, trading_symbol ASC
"""


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _stamp(value) -> datetime:
    if isinstance(value, datetime):
        stamp = value
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        return stamp.astimezone(IST)
    return parse_ist_timestamp(value).astimezone(IST)


def _number(value):
    try:
        if value is None:
            return None
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError, OverflowError):
        return None


def _age_seconds(as_of: datetime, available_at) -> float | None:
    if available_at in (None, ""):
        return None
    try:
        return round(max(0.0, (as_of - _stamp(available_at)).total_seconds()), 3)
    except Exception:
        return None


def _latest_visible_contract_sync(database_url: str, as_of: datetime) -> str | None:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(LATEST_VISIBLE_CONTRACT_SQL, (as_of, as_of))
            row = cursor.fetchone()
    return str(row[0]) if row and row[0] else None


def _read_latest_option_rows_sync(database_url: str, as_of: datetime) -> list[dict]:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                OPTION_LATEST_BUCKET_SQL,
                (as_of, as_of, as_of, as_of, as_of, as_of),
            )
            rows = cursor.fetchall()
    keys = (
        "trading_symbol",
        "expiry_date",
        "strike",
        "option_type",
        "lot_size",
        "sample_bucket_at",
        "observed_at",
        "underlying_price",
        "last_price",
        "volume",
        "open_interest",
        "bid_price",
        "ask_price",
        "collected_at",
    )
    return [dict(zip(keys, row)) for row in rows]


def summarize_market_tape(
    rows: list[list],
    *,
    trading_symbol: str | None,
    as_of,
) -> dict:
    observed = _stamp(as_of)
    if not rows:
        return {
            "status": "UNAVAILABLE",
            "series": "MCX_COPPER",
            "reason": "NO_FIRST_SEEN_PIT_COPPER_5M_CANDLES",
            "source_table": CANDLE_TABLE,
            "first_seen_immutable": True,
            "provenance_id": CANDLE_PROVENANCE_ID,
            "historical_backfill_used": False,
            "decision_effect": "NONE",
        }

    latest = rows[-1]
    latest_start = _stamp(latest[0])
    latest_available = latest_start + timedelta(minutes=5)
    snapshot = None
    snapshot_reason = None
    if len(rows) >= 51:
        try:
            snapshot = build_copper_snapshot(rows, len(rows) - 1)
        except Exception as exc:
            snapshot_reason = f"{exc.__class__.__name__}: {str(exc)[:240]}"
    else:
        snapshot_reason = "COPPER_PERCEPTION_REQUIRES_50_COMPLETED_WARMUP_BARS"

    return {
        "status": "AVAILABLE",
        "series": "MCX_COPPER",
        "trading_symbol": trading_symbol,
        "timeframe_minutes": 5,
        "visible_candles": len(rows),
        "latest_candle_at": latest_start.isoformat(),
        "latest_bar_completed_at": latest_available.isoformat(),
        "age_seconds_from_completion": _age_seconds(observed, latest_available),
        "latest_close": _number(latest[4]) if len(latest) > 4 else None,
        "latest_volume": _number(latest[5]) if len(latest) > 5 else None,
        "latest_open_interest": _number(latest[6]) if len(latest) > 6 else None,
        "perception_snapshot": snapshot,
        "perception_status": "READY" if snapshot is not None else "WARMING_UP",
        "perception_reason": snapshot_reason,
        "source_table": CANDLE_TABLE,
        "first_seen_immutable": True,
        "provenance_id": CANDLE_PROVENANCE_ID,
        "point_in_time_rule": "BAR_COMPLETED_AND_COLLECTED_AT_OR_BEFORE_CLICK",
        "historical_backfill_used": False,
        "mutable_generic_fallback_used": False,
        "current_mind_effect": "NONE",
        "decision_effect": "NONE",
    }


def summarize_option_tape(rows: list[dict], *, as_of) -> dict:
    observed = _stamp(as_of)
    if not rows:
        return {
            "status": "UNAVAILABLE",
            "series": "MCX_COPPER_OPTION",
            "reason": "NO_IMMUTABLE_PIT_COPPER_OPTION_SNAPSHOT",
            "source_table": OPTION_TABLE,
            "first_seen_immutable": True,
            "provenance_id": OPTION_PROVENANCE_ID,
            "historical_backfill_used": False,
            "raw_oi_directional_vote_allowed": False,
            "decision_effect": "NONE",
        }

    visible = []
    for row in rows:
        try:
            sample = _stamp(row.get("sample_bucket_at"))
            seen = _stamp(row.get("observed_at"))
            collected = _stamp(row.get("collected_at"))
        except Exception:
            continue
        if sample <= observed and seen <= observed and collected <= observed:
            visible.append(row)
    if not visible:
        return summarize_option_tape([], as_of=observed)

    bucket = max(_stamp(row["sample_bucket_at"]) for row in visible)
    bucket_rows = [row for row in visible if _stamp(row["sample_bucket_at"]) == bucket]
    expiries = sorted({str(row.get("expiry_date")) for row in bucket_rows if row.get("expiry_date")})
    nearest_expiry = expiries[0] if expiries else None
    nearest_rows = [row for row in bucket_rows if str(row.get("expiry_date")) == nearest_expiry] if nearest_expiry else bucket_rows

    ce = [row for row in nearest_rows if str(row.get("option_type") or "").upper() == "CE"]
    pe = [row for row in nearest_rows if str(row.get("option_type") or "").upper() == "PE"]
    ce_oi = sum(max(0.0, _number(row.get("open_interest")) or 0.0) for row in ce)
    pe_oi = sum(max(0.0, _number(row.get("open_interest")) or 0.0) for row in pe)
    pcr = (pe_oi / ce_oi) if ce_oi > 0 else None
    latest_collected = max(_stamp(row["collected_at"]) for row in bucket_rows)
    underlying_prices = [
        _number(row.get("underlying_price"))
        for row in bucket_rows
        if _number(row.get("underlying_price")) is not None
    ]
    strikes_by_type = {
        option_type: sorted(
            {
                _number(row.get("strike"))
                for row in nearest_rows
                if str(row.get("option_type") or "").upper() == option_type
                and _number(row.get("strike")) is not None
            }
        )
        for option_type in ("CE", "PE")
    }

    return {
        "status": "AVAILABLE",
        "series": "MCX_COPPER_OPTION",
        "sample_bucket_at": bucket.isoformat(),
        "available_at": latest_collected.isoformat(),
        "age_seconds": _age_seconds(observed, latest_collected),
        "contracts_visible": len(bucket_rows),
        "nearest_expiry": nearest_expiry,
        "nearest_expiry_contracts": len(nearest_rows),
        "option_type_counts": dict(Counter(str(row.get("option_type") or "UNKNOWN").upper() for row in nearest_rows)),
        "strikes_by_type": strikes_by_type,
        "underlying_price": underlying_prices[-1] if underlying_prices else None,
        "ce_open_interest": round(ce_oi, 6),
        "pe_open_interest": round(pe_oi, 6),
        "put_call_oi_ratio": round(pcr, 6) if pcr is not None else None,
        "source_table": OPTION_TABLE,
        "first_seen_immutable": True,
        "provenance_id": OPTION_PROVENANCE_ID,
        "point_in_time_rule": "SAMPLE_AND_OBSERVED_AND_COLLECTED_AT_OR_BEFORE_CLICK",
        "historical_backfill_used": False,
        "mutable_generic_fallback_used": False,
        "raw_oi_directional_vote_allowed": False,
        "interpretation": "POSITIONING_CONTEXT_ONLY_UNTIL_SEPARATE_CAUSAL_OI_PLUS_PREMIUM_RULE_IS_REGISTERED",
        "current_mind_effect": "NONE",
        "decision_effect": "NONE",
    }


def summarize_slow_context(items, *, as_of) -> dict:
    observed = _stamp(as_of)
    latest = {}
    for item in items or []:
        kind = str(getattr(item, "kind", "") or "").upper()
        try:
            available = _stamp(getattr(item, "available_at"))
        except Exception:
            continue
        if available > observed:
            continue
        current = latest.get(kind)
        if current is None or _stamp(current.available_at) <= available:
            latest[kind] = item

    def record(kind: str, role: str) -> dict:
        item = latest.get(kind)
        if item is None:
            return {
                "status": "UNAVAILABLE",
                "kind": kind,
                "role": role,
                "directional_vote_allowed": False,
            }
        available = _stamp(item.available_at)
        return {
            "status": "AVAILABLE",
            "kind": kind,
            "role": role,
            "context_id": item.context_id,
            "observed_at": item.observed_at,
            "available_at": item.available_at,
            "age_seconds": _age_seconds(observed, available),
            "source": item.source_name,
            "source_tier": item.source_tier,
            "frequency": item.frequency,
            "value": item.values,
            "notes": item.notes,
            "directional_vote_allowed": False,
            "classification": "SLOW_CONTEXT_ONLY",
        }

    return {
        "status": "AVAILABLE" if latest else "UNAVAILABLE",
        "series": {
            "FX": record("FX", "MCX_CURRENCY_TRANSLATION_SLOW_CONTEXT"),
            "POSITIONING": record("POSITIONING", "WEEKLY_GLOBAL_POSITIONING_CONTEXT"),
        },
        "source_table": "commodity_historical_context",
        "availability_timestamp_enforced": True,
        "intraday_direction_creation_allowed": False,
        "current_mind_effect": "NONE",
        "decision_effect": "NONE",
    }


def summarize_china_macro(*, as_of) -> dict:
    observed = _stamp(as_of)
    records = visible_at(china_copper_macro_records(), observed)
    if not records:
        return {
            "status": "UNAVAILABLE",
            "series": "MACRO_RELEASE",
            "reason": "NO_OFFICIAL_CHINA_MACRO_RELEASE_AVAILABLE_BY_CLICK",
            "directional_vote_allowed": False,
            "decision_effect": "NONE",
        }
    latest_available = max(_stamp(row["available_at"]) for row in records)
    newest = [row for row in records if _stamp(row["available_at"]) == latest_available]
    return {
        "status": "AVAILABLE",
        "series": "MACRO_RELEASE",
        "available_at": latest_available.isoformat(),
        "age_seconds": _age_seconds(observed, latest_available),
        "records": newest,
        "source_class": "OFFICIAL_NBS_RELEASE",
        "classification": "SLOW_MACRO_CONTEXT_ONLY",
        "directional_vote_allowed": False,
        "headline_or_indicator_score_allowed": False,
        "current_mind_effect": "NONE",
        "decision_effect": "NONE",
    }


def unavailable_external_feeds() -> dict:
    return {
        "COMEX_HG": {
            "status": "UNAVAILABLE",
            "reason": "NO_LICENSED_FIRST_SEEN_INTRADAY_COMEX_HG_TAPE_CONNECTED",
            "public_yahoo_substitution_allowed": False,
            "directional_vote_allowed": False,
        },
        "LME_COPPER": {
            "status": "UNAVAILABLE",
            "reason": "NO_ENTITLED_FIRST_SEEN_LME_COPPER_TAPE_CONNECTED",
            "directional_vote_allowed": False,
        },
        "COPPER_NEWS": {
            "status": "UNAVAILABLE",
            "reason": "NO_PROSPECTIVE_FIRST_DETECTED_COPPER_NEWS_STORE_CONNECTED",
            "historical_gdelt_substitution_allowed": False,
            "headline_sentiment_direction_allowed": False,
            "directional_vote_allowed": False,
        },
        "USDINR_INTRADAY": {
            "status": "UNAVAILABLE",
            "reason": "ONLY_SLOW_REFERENCE_FX_IS_CURRENTLY_PROVEN_IN_COPPER_CONTEXT_STORE",
            "daily_reference_substitution_allowed": False,
            "directional_vote_allowed": False,
        },
    }


def build_information_board(
    *,
    as_of,
    market_tape: dict,
    option_tape: dict,
    slow_context: dict,
    china_macro: dict,
) -> dict:
    observed = _stamp(as_of)
    external = unavailable_external_feeds()
    groups = {
        "primary_market": {"MCX_COPPER": market_tape},
        "option_market": {"MCX_COPPER_OPTION": option_tape},
        "global_copper": {
            "COMEX_HG": external["COMEX_HG"],
            "LME_COPPER": external["LME_COPPER"],
        },
        "currency": {
            "USDINR_INTRADAY": external["USDINR_INTRADAY"],
            "SLOW_REFERENCE_FX": (slow_context.get("series") or {}).get("FX", {}),
        },
        "positioning": {
            "CFTC_COPPER": (slow_context.get("series") or {}).get("POSITIONING", {}),
        },
        "china_macro": {"MACRO_RELEASE": china_macro},
        "news": {"COPPER_NEWS": external["COPPER_NEWS"]},
    }
    statuses = [
        item.get("status")
        for group in groups.values()
        for item in group.values()
        if isinstance(item, dict)
    ]
    available = sum(status == "AVAILABLE" for status in statuses)
    total = len(statuses)
    return {
        "status": "AVAILABLE" if market_tape.get("status") == "AVAILABLE" else "WARMING_UP",
        "model_id": MODEL_ID,
        "product": "COPPER",
        "trade_instrument": "OPTIONS_ONLY",
        "as_of": observed.isoformat(),
        "groups": groups,
        "availability": {
            "available": available,
            "total": total,
            "pct": round(available / total * 100.0, 2) if total else 0.0,
        },
        "rules": [
            "Only first-seen immutable COPPER 5m candles are primary intraday market evidence.",
            "Only first-seen immutable Copper option observations are option positioning evidence.",
            "Raw option OI cannot create direction without a separately registered causal OI-plus-premium rule.",
            "Daily FX and weekly CFTC records are slow context only and cannot stand in for click-time intraday evidence.",
            "Public delayed/current COMEX quotes cannot substitute for a licensed first-seen intraday COMEX tape.",
            "Historical GDELT Copper news cannot substitute for a prospective first-detected news store.",
            "Unavailable evidence remains unavailable and is never converted into bullish or bearish evidence.",
        ],
        "sealed_copper_current_mind_effect": "NONE",
        "direction_v2_effect": "NONE",
        "option_expression_effect": "NONE",
        "production_rules_changed": False,
        "historical_backfill_used": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
        "promotion_eligible": False,
    }


async def read_copper_information_board(database_url: str, *, as_of=None) -> dict:
    database_url = str(database_url or "").strip()
    if not database_url:
        return {
            "status": "UNAVAILABLE",
            "model_id": MODEL_ID,
            "reason": "DATABASE_NOT_CONFIGURED",
            "sealed_copper_current_mind_effect": "NONE",
            "promotion_eligible": False,
        }

    observed = _stamp(as_of or datetime.now(IST))
    candle_store = CopperCandleObservationStore(database_url)
    await candle_store.initialize()
    trading_symbol = await asyncio.to_thread(_latest_visible_contract_sync, database_url, observed)
    rows = (
        await candle_store.read_pit(
            observed - timedelta(days=LOOKBACK_DAYS),
            observed,
            observed,
            trading_symbol=trading_symbol,
        )
        if trading_symbol
        else []
    )
    market = summarize_market_tape(rows, trading_symbol=trading_symbol, as_of=observed)

    option_rows, slow_items = await asyncio.gather(
        asyncio.to_thread(_read_latest_option_rows_sync, database_url, observed),
        asyncio.to_thread(
            PostgresHistoricalContextStore(database_url).read_available,
            "COPPER",
            observed,
            ("FX", "POSITIONING"),
        ),
    )
    options = summarize_option_tape(option_rows, as_of=observed)
    slow = summarize_slow_context(slow_items, as_of=observed)
    macro = summarize_china_macro(as_of=observed)
    return build_information_board(
        as_of=observed,
        market_tape=market,
        option_tape=options,
        slow_context=slow,
        china_macro=macro,
    )
