from __future__ import annotations

import asyncio
import copy
from datetime import datetime
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .copper_option_observation_store import (
    PROVENANCE_ID as OPTION_PROVENANCE_ID,
    TABLE_NAME as OPTION_TABLE,
)
from .copper_pit_information_board_v2 import read_copper_information_board


IST = ZoneInfo("Asia/Kolkata")
RULE_VERSION = "COPPER_OPTION_PARTICIPATION_V1"
MAX_BUCKET_GAP_SECONDS = 15 * 60
DIRECTIONAL = {"BULLISH", "BEARISH"}

OPTION_PARTICIPATION_TWO_BUCKETS_SQL = f"""
WITH visible_buckets AS (
    SELECT DISTINCT sample_bucket_at
    FROM {OPTION_TABLE}
    WHERE sample_bucket_at <= %s
      AND observed_at <= %s
      AND collected_at <= %s
    ORDER BY sample_bucket_at DESC
    LIMIT 2
)
SELECT
    trading_symbol, expiry_date, strike, option_type, lot_size,
    sample_bucket_at, observed_at, underlying_price, last_price,
    volume, open_interest, bid_price, ask_price, collected_at
FROM {OPTION_TABLE}
WHERE sample_bucket_at IN (SELECT sample_bucket_at FROM visible_buckets)
  AND sample_bucket_at <= %s
  AND observed_at <= %s
  AND collected_at <= %s
ORDER BY sample_bucket_at DESC, expiry_date ASC, option_type ASC,
         strike ASC, trading_symbol ASC
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


def _read_two_visible_buckets_sync(database_url: str, as_of: datetime) -> list[dict]:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                OPTION_PARTICIPATION_TWO_BUCKETS_SQL,
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


def _contract_key(row: dict) -> tuple[str, str, float | None, str]:
    return (
        str(row.get("trading_symbol") or ""),
        str(row.get("expiry_date") or ""),
        _number(row.get("strike")),
        str(row.get("option_type") or "").upper(),
    )


def _contract_evidence(current: dict, previous: dict) -> dict:
    option_type = str(current.get("option_type") or "").upper()
    current_premium = _number(current.get("last_price"))
    previous_premium = _number(previous.get("last_price"))
    current_oi = _number(current.get("open_interest"))
    previous_oi = _number(previous.get("open_interest"))
    current_volume = _number(current.get("volume"))
    previous_volume = _number(previous.get("volume"))

    premium_delta = (
        current_premium - previous_premium
        if current_premium is not None and previous_premium is not None
        else None
    )
    oi_delta = (
        current_oi - previous_oi
        if current_oi is not None and previous_oi is not None
        else None
    )
    volume_delta = (
        current_volume - previous_volume
        if current_volume is not None and previous_volume is not None
        else None
    )

    stance = "UNKNOWN"
    state = "MISSING_PREMIUM_OR_OI"
    eligible = False
    if premium_delta is not None and oi_delta is not None:
        if oi_delta <= 0:
            state = "OI_FLAT_OR_DECREASING_NON_VOTING"
        elif premium_delta == 0:
            state = "PREMIUM_UNCHANGED_NON_VOTING"
        else:
            eligible = True
            if option_type == "CE":
                stance = "BULLISH" if premium_delta > 0 else "BEARISH"
            elif option_type == "PE":
                stance = "BEARISH" if premium_delta > 0 else "BULLISH"
            else:
                stance = "UNKNOWN"
                eligible = False
                state = "UNSUPPORTED_OPTION_TYPE"
            if eligible:
                state = f"NEW_OI_{stance}_{option_type}"

    return {
        "trading_symbol": str(current.get("trading_symbol") or ""),
        "expiry_date": str(current.get("expiry_date") or ""),
        "strike": _number(current.get("strike")),
        "option_type": option_type,
        "previous_last_price": previous_premium,
        "current_last_price": current_premium,
        "premium_delta": premium_delta,
        "previous_open_interest": previous_oi,
        "current_open_interest": current_oi,
        "open_interest_delta": oi_delta,
        "previous_volume": previous_volume,
        "current_volume": current_volume,
        "volume_delta": volume_delta,
        "eligible_new_oi_evidence": eligible,
        "stance": stance if eligible and stance in DIRECTIONAL else "UNKNOWN",
        "state": state,
    }


def build_option_participation_snapshot(rows: list[dict], *, as_of) -> dict:
    """Build preregistered change evidence without using underlying direction.

    Only the latest two option buckets actually available by ``as_of`` are eligible.
    Raw OI levels, OI decreases, and underlying-price direction never create a vote.
    """
    observed = _stamp(as_of)
    visible: list[dict] = []
    for row in rows or []:
        try:
            sample = _stamp(row.get("sample_bucket_at"))
            seen = _stamp(row.get("observed_at"))
            collected = _stamp(row.get("collected_at"))
        except Exception:
            continue
        if sample <= observed and seen <= observed and collected <= observed:
            visible.append(row)

    base = {
        "rule_version": RULE_VERSION,
        "first_seen_immutable": True,
        "provenance_id": OPTION_PROVENANCE_ID,
        "raw_oi_level_directional_vote_allowed": False,
        "oi_flat_or_decreasing_directional_vote_allowed": False,
        "underlying_price_direction_used": False,
        "historical_backfill_used": False,
        "mutable_generic_fallback_used": False,
    }
    if not visible:
        return {
            **base,
            "status": "UNAVAILABLE",
            "reason": "NO_VISIBLE_FIRST_SEEN_OPTION_BUCKET",
            "contract_evidence": [],
        }

    buckets = sorted(
        {_stamp(row["sample_bucket_at"]) for row in visible},
        reverse=True,
    )
    latest_bucket = buckets[0]
    if len(buckets) < 2:
        return {
            **base,
            "status": "WARMING_UP",
            "reason": "NO_PREVIOUS_IMMUTABLE_OPTION_BUCKET",
            "latest_bucket_at": latest_bucket.isoformat(),
            "contract_evidence": [],
        }

    previous_bucket = buckets[1]
    gap_seconds = (latest_bucket - previous_bucket).total_seconds()
    if gap_seconds <= 0 or gap_seconds > MAX_BUCKET_GAP_SECONDS:
        return {
            **base,
            "status": "WARMING_UP",
            "reason": "PREVIOUS_OPTION_BUCKET_OUTSIDE_15_MINUTE_WINDOW",
            "latest_bucket_at": latest_bucket.isoformat(),
            "previous_bucket_at": previous_bucket.isoformat(),
            "bucket_gap_seconds": gap_seconds,
            "contract_evidence": [],
        }

    latest_rows = [
        row for row in visible
        if _stamp(row["sample_bucket_at"]) == latest_bucket
    ]
    expiries = sorted(
        {str(row.get("expiry_date")) for row in latest_rows if row.get("expiry_date")}
    )
    nearest_expiry = expiries[0] if expiries else None
    if nearest_expiry is None:
        return {
            **base,
            "status": "UNAVAILABLE",
            "reason": "NO_NEAREST_EXPIRY_IN_LATEST_OPTION_BUCKET",
            "latest_bucket_at": latest_bucket.isoformat(),
            "previous_bucket_at": previous_bucket.isoformat(),
            "bucket_gap_seconds": gap_seconds,
            "contract_evidence": [],
        }

    current = [
        row for row in latest_rows
        if str(row.get("expiry_date")) == nearest_expiry
    ]
    previous = [
        row for row in visible
        if _stamp(row["sample_bucket_at"]) == previous_bucket
        and str(row.get("expiry_date")) == nearest_expiry
    ]
    previous_by_key = {_contract_key(row): row for row in previous}
    evidence = [
        _contract_evidence(row, previous_by_key[_contract_key(row)])
        for row in current
        if _contract_key(row) in previous_by_key
    ]
    matched_ce = sum(row["option_type"] == "CE" for row in evidence)
    matched_pe = sum(row["option_type"] == "PE" for row in evidence)
    latest_underlying = next(
        (
            _number(row.get("underlying_price"))
            for row in current
            if _number(row.get("underlying_price")) is not None
        ),
        None,
    )
    previous_underlying = next(
        (
            _number(row.get("underlying_price"))
            for row in previous
            if _number(row.get("underlying_price")) is not None
        ),
        None,
    )

    if matched_ce < 1 or matched_pe < 1:
        status = "WARMING_UP"
        reason = "REQUIRES_MATCHED_CE_AND_PE_CONTRACTS"
    else:
        status = "READY"
        reason = None

    return {
        **base,
        "status": status,
        "reason": reason,
        "latest_bucket_at": latest_bucket.isoformat(),
        "previous_bucket_at": previous_bucket.isoformat(),
        "bucket_gap_seconds": gap_seconds,
        "nearest_expiry": nearest_expiry,
        "matched_contracts": len(evidence),
        "matched_ce_contracts": matched_ce,
        "matched_pe_contracts": matched_pe,
        "eligible_new_oi_evidence": sum(
            bool(row.get("eligible_new_oi_evidence")) for row in evidence
        ),
        "latest_underlying_price_context": latest_underlying,
        "previous_underlying_price_context": previous_underlying,
        "contract_evidence": evidence,
    }


async def enrich_copper_information_board_with_option_participation(
    database_url: str,
    board: dict,
) -> dict:
    """Attach the frozen Option Participation V1 evidence to a PIT board."""
    enriched = copy.deepcopy(board or {})
    option = (
        (((enriched.get("groups") or {}).get("option_market") or {}).get("MCX_COPPER_OPTION"))
        or {}
    )
    if not isinstance(option, dict):
        option = {}

    database_url = str(database_url or "").strip()
    as_of = enriched.get("as_of")
    if not database_url or not as_of:
        snapshot = {
            "status": "UNAVAILABLE",
            "reason": "DATABASE_OR_BOARD_AS_OF_NOT_CONFIGURED",
            "rule_version": RULE_VERSION,
            "first_seen_immutable": True,
            "raw_oi_level_directional_vote_allowed": False,
            "oi_flat_or_decreasing_directional_vote_allowed": False,
            "underlying_price_direction_used": False,
            "historical_backfill_used": False,
            "contract_evidence": [],
        }
    else:
        observed = _stamp(as_of)
        rows = await asyncio.to_thread(
            _read_two_visible_buckets_sync,
            database_url,
            observed,
        )
        snapshot = build_option_participation_snapshot(rows, as_of=observed)

    option["participation_snapshot"] = snapshot
    option["registered_participation_rule_version"] = RULE_VERSION
    option["registered_change_directional_vote_allowed"] = True
    option["raw_oi_directional_vote_allowed"] = False
    enriched.setdefault("groups", {}).setdefault("option_market", {})[
        "MCX_COPPER_OPTION"
    ] = option
    enriched["option_participation_contract_version"] = RULE_VERSION
    rules = list(enriched.get("rules") or [])
    registered_rule = (
        "Option Participation V1 may vote only from matched latest-vs-previous "
        "first-seen CE+PE new-OI premium-change evidence; raw OI levels, OI "
        "decreases and underlying direction cannot vote."
    )
    if registered_rule not in rules:
        rules.append(registered_rule)
    enriched["rules"] = rules
    return enriched


async def read_copper_information_board_with_option_participation(
    database_url: str,
    *,
    as_of=None,
) -> dict:
    board = await read_copper_information_board(database_url, as_of=as_of)
    return await enrich_copper_information_board_with_option_participation(
        database_url,
        board,
    )
