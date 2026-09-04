from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp


MODEL_ID = "CRUDE_OIL_MINI_OPTION_PREMIUM_MEMORY_V1"
PROVIDER = "GROWW"
UNDERLYING = "CRUDEOILM"
MAX_INTRADAY_GAP_MINUTES = 30.0
MIN_UNDERLYING_MOVE_FOR_SENSITIVITY = 1.0
MIN_DESCRIPTIVE_SEGMENTS = 20
MIN_DESCRIPTIVE_TRADING_DAYS = 2
IST = ZoneInfo("Asia/Kolkata")


READ_SQL = """
SELECT
    underlying_symbol, trading_symbol, expiry_date, strike, option_type, lot_size,
    sample_bucket_at, observed_at, collected_at, underlying_price, last_price,
    volume, open_interest, bid_price, ask_price
FROM commodity_option_snapshots
WHERE provider = %s
  AND underlying_symbol = %s
  AND sample_bucket_at >= %s
  AND sample_bucket_at <= %s
  AND observed_at <= %s
  AND collected_at <= %s
ORDER BY trading_symbol, sample_bucket_at, collected_at
"""


def _number(value):
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _stamp(value) -> datetime | None:
    try:
        return parse_ist_timestamp(value).astimezone(IST)
    except Exception:
        return None


def _iso(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _normalize_row(row: dict, as_of: datetime) -> dict | None:
    if str(row.get("underlying_symbol") or "").upper() != UNDERLYING:
        return None
    trading_symbol = str(row.get("trading_symbol") or "").upper().strip()
    if not trading_symbol.startswith(UNDERLYING):
        return None
    option_type = str(row.get("option_type") or "").upper().strip()
    if option_type not in {"CE", "PE"}:
        return None

    sample = _stamp(row.get("sample_bucket_at"))
    observed = _stamp(row.get("observed_at"))
    collected = _stamp(row.get("collected_at"))
    if not sample or not observed or not collected:
        return None
    if sample > as_of or observed > as_of or collected > as_of:
        return None

    premium = _number(row.get("last_price"))
    underlying_price = _number(row.get("underlying_price"))
    strike = _number(row.get("strike"))
    if premium is None or premium <= 0 or underlying_price is None or underlying_price <= 0:
        return None
    if strike is None or strike <= 0:
        return None

    return {
        "underlying_symbol": UNDERLYING,
        "trading_symbol": trading_symbol,
        "expiry_date": _iso(row.get("expiry_date")),
        "strike": strike,
        "option_type": option_type,
        "lot_size": int(float(row["lot_size"])) if row.get("lot_size") not in (None, "") else None,
        "sample_bucket_at": sample,
        "observed_at": observed,
        "collected_at": collected,
        "underlying_price": underlying_price,
        "last_price": premium,
        "volume": _number(row.get("volume")),
        "open_interest": _number(row.get("open_interest")),
        "bid_price": _number(row.get("bid_price")),
        "ask_price": _number(row.get("ask_price")),
    }


def _median(values):
    usable = [float(value) for value in values if value is not None]
    return round(median(usable), 6) if usable else None


def _contract_memory(rows: list[dict], max_gap_minutes: float) -> dict:
    first = rows[0]
    option_type = first["option_type"]
    segments = []
    intraday_gaps = []

    for previous, current in zip(rows, rows[1:]):
        if previous["sample_bucket_at"].date() != current["sample_bucket_at"].date():
            continue
        elapsed = (current["sample_bucket_at"] - previous["sample_bucket_at"]).total_seconds() / 60.0
        if elapsed <= 0:
            continue
        intraday_gaps.append(elapsed)
        if elapsed > max_gap_minutes:
            continue

        underlying_change = current["underlying_price"] - previous["underlying_price"]
        premium_change = current["last_price"] - previous["last_price"]
        raw_sensitivity = None
        directional_sensitivity = None
        if abs(underlying_change) >= MIN_UNDERLYING_MOVE_FOR_SENSITIVITY:
            raw_sensitivity = premium_change / underlying_change
            directional_sensitivity = raw_sensitivity if option_type == "CE" else -raw_sensitivity

        segments.append(
            {
                "start_at": previous["sample_bucket_at"].isoformat(),
                "end_at": current["sample_bucket_at"].isoformat(),
                "available_at": current["collected_at"].isoformat(),
                "elapsed_minutes": round(elapsed, 3),
                "underlying_change": round(underlying_change, 6),
                "premium_change": round(premium_change, 6),
                "raw_sensitivity": round(raw_sensitivity, 6) if raw_sensitivity is not None else None,
                "directional_sensitivity": (
                    round(directional_sensitivity, 6) if directional_sensitivity is not None else None
                ),
            }
        )

    trading_days = sorted({row["sample_bucket_at"].date().isoformat() for row in rows})
    usable_sensitivities = [
        segment["directional_sensitivity"]
        for segment in segments
        if segment["directional_sensitivity"] is not None
    ]
    descriptive_ready = (
        len(segments) >= MIN_DESCRIPTIVE_SEGMENTS
        and len(trading_days) >= MIN_DESCRIPTIVE_TRADING_DAYS
    )

    return {
        "trading_symbol": first["trading_symbol"],
        "expiry_date": first["expiry_date"],
        "strike": first["strike"],
        "option_type": option_type,
        "lot_size": first["lot_size"],
        "snapshot_count": len(rows),
        "response_segments": len(segments),
        "sensitivity_segments": len(usable_sensitivities),
        "trading_days": len(trading_days),
        "first_sample_at": rows[0]["sample_bucket_at"].isoformat(),
        "last_sample_at": rows[-1]["sample_bucket_at"].isoformat(),
        "max_intraday_gap_minutes": round(max(intraday_gaps), 3) if intraday_gaps else None,
        "median_elapsed_minutes": _median(segment["elapsed_minutes"] for segment in segments),
        "median_directional_sensitivity": _median(usable_sensitivities),
        "status": "DESCRIPTIVE_READY" if descriptive_ready else "COLLECTING",
        "risk_translation_effect": "NONE",
        "promotion_eligible": False,
    }


def analyze_premium_memory_rows(
    rows: list[dict],
    *,
    as_of,
    max_gap_minutes: float = MAX_INTRADAY_GAP_MINUTES,
) -> dict:
    """Build exact-contract, PIT-visible option-premium response memory.

    This is descriptive research memory only. It never interpolates missing option
    prices, never switches contracts inside a response segment, and never creates
    an entry/stop/target or changes Current Mind.
    """
    click = parse_ist_timestamp(as_of).astimezone(IST)
    normalized = []
    for row in rows or []:
        item = _normalize_row(dict(row), click)
        if item is not None:
            normalized.append(item)

    # One immutable research observation per exact contract/sample bucket. If a
    # caller supplies duplicates, retain the earliest collected observation.
    deduped = {}
    for row in sorted(normalized, key=lambda item: item["collected_at"]):
        key = (row["trading_symbol"], row["sample_bucket_at"])
        deduped.setdefault(key, row)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in deduped.values():
        grouped[row["trading_symbol"]].append(row)
    for contract_rows in grouped.values():
        contract_rows.sort(key=lambda item: item["sample_bucket_at"])

    contracts = [
        _contract_memory(contract_rows, float(max_gap_minutes))
        for contract_rows in grouped.values()
        if contract_rows
    ]
    contracts.sort(key=lambda item: (-item["response_segments"], item["trading_symbol"]))

    total_segments = sum(item["response_segments"] for item in contracts)
    trading_days = {
        row["sample_bucket_at"].date().isoformat()
        for contract_rows in grouped.values()
        for row in contract_rows
    }
    option_types = {item["option_type"] for item in contracts}
    descriptive_ready = (
        total_segments >= MIN_DESCRIPTIVE_SEGMENTS
        and len(trading_days) >= MIN_DESCRIPTIVE_TRADING_DAYS
        and option_types == {"CE", "PE"}
    )

    return {
        "status": "DESCRIPTIVE_READY" if descriptive_ready else "COLLECTING",
        "model_id": MODEL_ID,
        "underlying_symbol": UNDERLYING,
        "as_of": click.isoformat(),
        "data_type": "LIVE_PIT_OPTION_LTP_SNAPSHOTS_NOT_OHLC",
        "snapshot_count": len(deduped),
        "contract_count": len(contracts),
        "response_segments": total_segments,
        "trading_days": len(trading_days),
        "max_segment_gap_minutes": float(max_gap_minutes),
        "contracts": contracts,
        "method": "EXACT_CONTRACT_CONSECUTIVE_INTRADAY_OBSERVATIONS_NO_INTERPOLATION",
        "pit_filter": "sample_bucket_at, observed_at and collected_at must all be <= as_of",
        "first_seen_immutable": False,
        "storage_note": "Existing generic snapshot buckets may be updated on same-bucket conflict; promotion remains blocked.",
        "risk_translation_effect": "NONE",
        "current_mind_effect": "NONE",
        "integrated_v2_effect": "NONE",
        "promotion_eligible": False,
        "paper_signal_only": True,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
    }


def _read_sync(database_url: str, start: datetime, as_of: datetime) -> list[dict]:
    import psycopg

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(READ_SQL, (PROVIDER, UNDERLYING, start, as_of, as_of, as_of))
            columns = [description.name for description in cursor.description]
            return [dict(zip(columns, values)) for values in cursor.fetchall()]


async def read_crude_oil_mini_premium_memory(
    database_url: str,
    *,
    as_of=None,
    lookback_days: int = 7,
    max_gap_minutes: float = MAX_INTRADAY_GAP_MINUTES,
) -> dict:
    database_url = str(database_url or "").strip()
    if not database_url:
        return {
            "status": "UNAVAILABLE",
            "model_id": MODEL_ID,
            "underlying_symbol": UNDERLYING,
            "reason": "DATABASE_NOT_CONFIGURED",
            "risk_translation_effect": "NONE",
            "promotion_eligible": False,
        }

    click = parse_ist_timestamp(as_of or datetime.now(IST)).astimezone(IST)
    days = max(1, min(int(lookback_days), 30))
    start = click - timedelta(days=days)
    rows = await asyncio.to_thread(_read_sync, database_url, start, click)
    result = analyze_premium_memory_rows(rows, as_of=click, max_gap_minutes=max_gap_minutes)
    result["lookback_days"] = days
    return result
