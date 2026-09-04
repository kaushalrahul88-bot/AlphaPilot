from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_option_observation_store import TABLE_NAME as OPTION_TABLE_NAME
from .crude_oil_mini_research_protocol_v1 import (
    ATR_PERIOD,
    BASELINE_ID,
    MISSED_MOVE_ATR_MULTIPLE,
    MISSED_MOVE_OPPOSITE_MAX_ATR_MULTIPLE,
    OUTCOME_HORIZONS_MINUTES,
    PRIMARY_OUTCOME_HORIZON_MINUTES,
    PROTOCOL_ID,
    baseline_manifest,
    validate_baseline_result,
)


IST = ZoneInfo("Asia/Kolkata")
PROVIDER = "GROWW"
SYMBOL = "CRUDEOILM"
TIMEFRAME_MINUTES = 5
EPISODE_TABLE = "crude_oil_mini_research_episodes_v1"
OUTCOME_TABLE = "crude_oil_mini_episode_outcomes_v1"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {EPISODE_TABLE} (
    episode_id TEXT PRIMARY KEY,
    baseline_id TEXT NOT NULL,
    protocol_id TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    click_at TIMESTAMPTZ NOT NULL,
    reference_at TIMESTAMPTZ,
    decision_fingerprint TEXT,
    action TEXT NOT NULL,
    direction TEXT,
    evidence_quality TEXT,
    reference_price NUMERIC,
    atr14 NUMERIC,
    entry_price NUMERIC,
    stop_price NUMERIC,
    target_price NUMERIC,
    option_trading_symbol TEXT,
    option_type TEXT,
    option_premium_reference NUMERIC,
    option_sample_bucket_at TIMESTAMPTZ,
    option_collected_at TIMESTAMPTZ,
    current_mind_mode TEXT NOT NULL,
    integrated_v2_direction TEXT,
    integrated_v2_confidence TEXT,
    integrated_v2_decision_effect TEXT NOT NULL,
    paper_signal_only BOOLEAN NOT NULL,
    live_execution_enabled BOOLEAN NOT NULL,
    broker_order_placement_enabled BOOLEAN NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS crude_oil_mini_research_episodes_v1_click_idx
    ON {EPISODE_TABLE} (click_at DESC);
CREATE INDEX IF NOT EXISTS crude_oil_mini_research_episodes_v1_baseline_idx
    ON {EPISODE_TABLE} (baseline_id, click_at DESC);

CREATE TABLE IF NOT EXISTS {OUTCOME_TABLE} (
    episode_id TEXT NOT NULL REFERENCES {EPISODE_TABLE}(episode_id) ON DELETE CASCADE,
    horizon_minutes INTEGER NOT NULL,
    horizon_end_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ,
    resolution_status TEXT NOT NULL,
    underlying_end_close NUMERIC,
    underlying_return_pct NUMERIC,
    max_up_points NUMERIC,
    max_down_points NUMERIC,
    max_up_atr NUMERIC,
    max_down_atr NUMERIC,
    directional_favorable_points NUMERIC,
    directional_adverse_points NUMERIC,
    geometry_outcome TEXT,
    diagnosis TEXT,
    option_observations INTEGER NOT NULL DEFAULT 0,
    option_end_premium NUMERIC,
    option_return_pct NUMERIC,
    option_max_premium NUMERIC,
    option_min_premium NUMERIC,
    payload TEXT NOT NULL,
    PRIMARY KEY (episode_id, horizon_minutes)
);
CREATE INDEX IF NOT EXISTS crude_oil_mini_episode_outcomes_v1_horizon_idx
    ON {OUTCOME_TABLE} (horizon_minutes, resolution_status, resolved_at DESC);
"""

EPISODE_INSERT_SQL = f"""
INSERT INTO {EPISODE_TABLE} (
    episode_id, baseline_id, protocol_id, captured_at, click_at, reference_at,
    decision_fingerprint, action, direction, evidence_quality, reference_price,
    atr14, entry_price, stop_price, target_price, option_trading_symbol,
    option_type, option_premium_reference, option_sample_bucket_at,
    option_collected_at, current_mind_mode, integrated_v2_direction,
    integrated_v2_confidence, integrated_v2_decision_effect, paper_signal_only,
    live_execution_enabled, broker_order_placement_enabled, payload
) VALUES (
    %(episode_id)s, %(baseline_id)s, %(protocol_id)s, %(captured_at)s,
    %(click_at)s, %(reference_at)s, %(decision_fingerprint)s, %(action)s,
    %(direction)s, %(evidence_quality)s, %(reference_price)s, %(atr14)s,
    %(entry_price)s, %(stop_price)s, %(target_price)s,
    %(option_trading_symbol)s, %(option_type)s, %(option_premium_reference)s,
    %(option_sample_bucket_at)s, %(option_collected_at)s, %(current_mind_mode)s,
    %(integrated_v2_direction)s, %(integrated_v2_confidence)s,
    %(integrated_v2_decision_effect)s, %(paper_signal_only)s,
    %(live_execution_enabled)s, %(broker_order_placement_enabled)s, %(payload)s
)
ON CONFLICT (episode_id) DO NOTHING
RETURNING episode_id;
"""

OUTCOME_INSERT_SQL = f"""
INSERT INTO {OUTCOME_TABLE} (
    episode_id, horizon_minutes, horizon_end_at, resolved_at, available_at,
    resolution_status, underlying_end_close, underlying_return_pct,
    max_up_points, max_down_points, max_up_atr, max_down_atr,
    directional_favorable_points, directional_adverse_points,
    geometry_outcome, diagnosis, option_observations, option_end_premium,
    option_return_pct, option_max_premium, option_min_premium, payload
) VALUES (
    %(episode_id)s, %(horizon_minutes)s, %(horizon_end_at)s, %(resolved_at)s,
    %(available_at)s, %(resolution_status)s, %(underlying_end_close)s,
    %(underlying_return_pct)s, %(max_up_points)s, %(max_down_points)s,
    %(max_up_atr)s, %(max_down_atr)s, %(directional_favorable_points)s,
    %(directional_adverse_points)s, %(geometry_outcome)s, %(diagnosis)s,
    %(option_observations)s, %(option_end_premium)s, %(option_return_pct)s,
    %(option_max_premium)s, %(option_min_premium)s, %(payload)s
)
ON CONFLICT (episode_id, horizon_minutes) DO NOTHING
RETURNING episode_id;
"""


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url, connect_timeout=10)


def _stamp(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return parse_ist_timestamp(value).astimezone(IST)
    except Exception:
        return None


def _number(value):
    try:
        if value in (None, ""):
            return None
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError, OverflowError):
        return None


def _round(value, digits: int = 6):
    return round(float(value), digits) if value is not None else None


def _initialize_sync(database_url: str) -> None:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)


async def initialize_episode_ledger(database_url: str) -> None:
    await asyncio.to_thread(_initialize_sync, database_url)


def _atr14(candles: list[list]) -> float | None:
    rows = list(candles or [])
    if len(rows) < ATR_PERIOD + 1:
        return None
    recent = rows[-(ATR_PERIOD + 1):]
    true_ranges = []
    for previous, current in zip(recent, recent[1:]):
        try:
            previous_close = float(previous[4])
            high = float(current[2])
            low = float(current[3])
        except (TypeError, ValueError, IndexError):
            return None
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(true_ranges) / len(true_ranges) if len(true_ranges) == ATR_PERIOD else None


def _episode_id(result: dict) -> str:
    click = str(result.get("click_at") or "")
    fingerprint = str((result.get("journal") or {}).get("decision_fingerprint") or "")
    stable = f"{BASELINE_ID}|{click}|{fingerprint}|{result.get('mode')}"
    return "cmep-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:28]


def build_episode_capture(result: dict, candles: list[list], *, captured_at=None) -> dict:
    """Freeze one decision episode before any forward outcome is attached."""
    validate_baseline_result(result)
    click = _stamp(result.get("click_at"))
    if click is None:
        raise ValueError("A point-in-time click timestamp is required")
    current_mind = dict(result.get("current_mind") or {})
    integrated = dict(result.get("integrated_v2_shadow") or {})
    execution = dict(result.get("execution") or {})
    expression = dict(execution.get("option_expression") or {})
    reference_at = _stamp(result.get("latest_completed_bar_available_at"))
    reference_price = _number(candles[-1][4]) if candles else None
    captured = _stamp(captured_at) or datetime.now(IST)

    payload = {
        "episode_version": 1,
        "baseline": baseline_manifest(),
        "captured_at": captured.isoformat(),
        "decision": result,
        "capture_rule": "IMMUTABLE_DECISION_BEFORE_FORWARD_OUTCOME",
    }
    return {
        "episode_id": _episode_id(result),
        "baseline_id": BASELINE_ID,
        "protocol_id": PROTOCOL_ID,
        "captured_at": captured,
        "click_at": click,
        "reference_at": reference_at,
        "decision_fingerprint": (result.get("journal") or {}).get("decision_fingerprint"),
        "action": str(current_mind.get("action") or "NO_TRADE").upper(),
        "direction": current_mind.get("direction"),
        "evidence_quality": current_mind.get("evidence_quality"),
        "reference_price": reference_price,
        "atr14": _atr14(candles),
        "entry_price": _number(current_mind.get("entry_price")),
        "stop_price": _number(current_mind.get("stop_price")),
        "target_price": _number(current_mind.get("target_price")),
        "option_trading_symbol": expression.get("trading_symbol") if expression.get("status") == "EXPRESSED" else None,
        "option_type": expression.get("option_type") if expression.get("status") == "EXPRESSED" else None,
        "option_premium_reference": _number(expression.get("premium_reference")) if expression.get("status") == "EXPRESSED" else None,
        "option_sample_bucket_at": _stamp(expression.get("sample_bucket_at")) if expression.get("status") == "EXPRESSED" else None,
        "option_collected_at": _stamp(expression.get("collected_at")) if expression.get("status") == "EXPRESSED" else None,
        "current_mind_mode": str(result.get("mode") or ""),
        "integrated_v2_direction": integrated.get("direction"),
        "integrated_v2_confidence": integrated.get("confidence"),
        "integrated_v2_decision_effect": str(integrated.get("decision_effect") or "NONE"),
        "paper_signal_only": execution.get("paper_signal_only") is True,
        "live_execution_enabled": execution.get("live_execution_enabled") is True,
        "broker_order_placement_enabled": execution.get("broker_order_placement_enabled") is True,
        "payload": json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str),
    }


def _capture_sync(database_url: str, capture: dict) -> bool:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(EPISODE_INSERT_SQL, capture)
            return cursor.fetchone() is not None


def _read_episodes_sync(database_url: str) -> list[dict]:
    sql = f"""
        SELECT episode_id, baseline_id, protocol_id, captured_at, click_at,
               reference_at, action, direction, reference_price, atr14,
               entry_price, stop_price, target_price, option_trading_symbol,
               option_type, option_premium_reference, option_sample_bucket_at,
               option_collected_at
        FROM {EPISODE_TABLE}
        WHERE baseline_id = %s
          AND reference_at IS NOT NULL
          AND reference_price IS NOT NULL
        ORDER BY click_at ASC
    """
    keys = (
        "episode_id", "baseline_id", "protocol_id", "captured_at", "click_at",
        "reference_at", "action", "direction", "reference_price", "atr14",
        "entry_price", "stop_price", "target_price", "option_trading_symbol",
        "option_type", "option_premium_reference", "option_sample_bucket_at",
        "option_collected_at",
    )
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (BASELINE_ID,))
            return [dict(zip(keys, values)) for values in cursor.fetchall()]


def _existing_horizons_sync(database_url: str, episode_id: str) -> set[int]:
    sql = f"SELECT horizon_minutes FROM {OUTCOME_TABLE} WHERE episode_id = %s"
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (episode_id,))
            return {int(row[0]) for row in cursor.fetchall()}


def _read_future_candles_sync(database_url: str, reference_at: datetime, as_of: datetime) -> list[dict]:
    start = reference_at - timedelta(minutes=TIMEFRAME_MINUTES)
    end = reference_at + timedelta(minutes=max(OUTCOME_HORIZONS_MINUTES))
    sql = """
        SELECT candle_at, open, high, low, close, volume, collected_at
        FROM commodity_candles
        WHERE provider = %s
          AND symbol = %s
          AND timeframe_minutes = %s
          AND candle_at >= %s
          AND candle_at <= %s
          AND candle_at + (%s * INTERVAL '1 minute') <= %s
          AND collected_at <= %s
        ORDER BY candle_at ASC
    """
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                (PROVIDER, SYMBOL, TIMEFRAME_MINUTES, start, end, TIMEFRAME_MINUTES, as_of, as_of),
            )
            rows = cursor.fetchall()
    return [
        {
            "candle_at": row[0].astimezone(IST),
            "visible_at": (row[0] + timedelta(minutes=TIMEFRAME_MINUTES)).astimezone(IST),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5] or 0),
            "collected_at": row[6].astimezone(IST),
        }
        for row in rows
    ]


def _read_future_options_sync(database_url: str, episode: dict, as_of: datetime) -> list[dict]:
    symbol = str(episode.get("option_trading_symbol") or "").strip()
    if not symbol:
        return []
    start_bucket = episode.get("option_sample_bucket_at") or episode["click_at"]
    end = episode["reference_at"] + timedelta(minutes=max(OUTCOME_HORIZONS_MINUTES))
    sql = f"""
        SELECT sample_bucket_at, observed_at, collected_at, last_price,
               underlying_price, volume, open_interest
        FROM {OPTION_TABLE_NAME}
        WHERE provider = %s
          AND underlying_symbol = %s
          AND trading_symbol = %s
          AND sample_bucket_at > %s
          AND sample_bucket_at <= %s
          AND observed_at <= %s
          AND collected_at <= %s
        ORDER BY sample_bucket_at ASC, collected_at ASC
    """
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (PROVIDER, SYMBOL, symbol, start_bucket, end, as_of, as_of))
            rows = cursor.fetchall()
    return [
        {
            "sample_bucket_at": row[0].astimezone(IST),
            "observed_at": row[1].astimezone(IST),
            "collected_at": row[2].astimezone(IST),
            "last_price": float(row[3]),
            "underlying_price": _number(row[4]),
            "volume": _number(row[5]),
            "open_interest": _number(row[6]),
        }
        for row in rows
    ]


def _touches(bar: dict, level: float | None, *, side: str) -> bool:
    if level is None:
        return False
    return bar["high"] >= level if side == "UP" else bar["low"] <= level


def _geometry_outcome(episode: dict, path: list[dict]) -> str:
    action = str(episode.get("action") or "").upper()
    if action not in {"BUY_CE", "BUY_PE"}:
        return "NOT_APPLICABLE"
    entry = _number(episode.get("entry_price"))
    stop = _number(episode.get("stop_price"))
    target = _number(episode.get("target_price"))
    if entry is None or stop is None or target is None:
        return "GEOMETRY_UNAVAILABLE"

    bullish = action == "BUY_CE"
    entry_side = "UP" if bullish else "DOWN"
    stop_side = "DOWN" if bullish else "UP"
    target_side = "UP" if bullish else "DOWN"
    entered = False
    entry_index = None
    for index, bar in enumerate(path):
        if not entered:
            if not _touches(bar, entry, side=entry_side):
                continue
            entered = True
            entry_index = index
            if _touches(bar, stop, side=stop_side) or _touches(bar, target, side=target_side):
                return "ENTRY_AND_EXIT_SAME_BAR_AMBIGUOUS"
            continue

        stop_hit = _touches(bar, stop, side=stop_side)
        target_hit = _touches(bar, target, side=target_side)
        if stop_hit and target_hit:
            return "STOP_TARGET_SAME_BAR_AMBIGUOUS"
        if stop_hit:
            return "STOP_FIRST"
        if target_hit:
            return "TARGET_FIRST"

    if entry_index is None:
        return "NO_ENTRY"
    return "OPEN_AT_HORIZON"


def _diagnosis(episode: dict, max_up: float | None, max_down: float | None) -> str:
    action = str(episode.get("action") or "").upper()
    if action not in {"WAIT", "NO_TRADE"}:
        return "TRADE_EPISODE"
    atr = _number(episode.get("atr14"))
    if atr is None or atr <= 0 or max_up is None or max_down is None:
        return "ABSTENTION_INSUFFICIENT_ATR_DIAGNOSTIC"
    clean = MISSED_MOVE_ATR_MULTIPLE * atr
    opposite_max = MISSED_MOVE_OPPOSITE_MAX_ATR_MULTIPLE * atr
    if max_up >= clean and max_down >= clean:
        return "TWO_SIDED_EXPANSION_AFTER_ABSTENTION"
    if max_up >= clean and max_down <= opposite_max:
        return "MISSED_BULLISH_CLEAN_EXPANSION"
    if max_down >= clean and max_up <= opposite_max:
        return "MISSED_BEARISH_CLEAN_EXPANSION"
    return "NO_LARGE_CLEAN_MOVE_AFTER_ABSTENTION"


def _option_outcome(episode: dict, rows: list[dict], horizon_end: datetime) -> dict:
    start = _number(episode.get("option_premium_reference"))
    visible = [
        row
        for row in rows
        if row["sample_bucket_at"] <= horizon_end
        and row.get("observed_at", row["sample_bucket_at"]) <= horizon_end
    ]
    if start is None or start <= 0 or not visible:
        return {
            "option_observations": 0 if not visible else len(visible),
            "option_end_premium": None,
            "option_return_pct": None,
            "option_max_premium": None,
            "option_min_premium": None,
            "latest_option_available_at": None,
            "endpoint_basis": "NO_EXACT_CONTRACT_FUTURE_OBSERVATION",
        }
    end = visible[-1]["last_price"]
    premiums = [row["last_price"] for row in visible]
    return {
        "option_observations": len(visible),
        "option_end_premium": end,
        "option_return_pct": (end / start - 1.0) * 100.0,
        "option_max_premium": max(premiums),
        "option_min_premium": min(premiums),
        "latest_option_available_at": max(row["collected_at"] for row in visible),
        "endpoint_basis": "LATEST_IMMUTABLE_EXACT_CONTRACT_LTP_OBSERVED_AT_OR_BEFORE_HORIZON",
    }


def analyze_episode_outcome(
    episode: dict,
    candles: list[dict],
    option_rows: list[dict],
    *,
    horizon_minutes: int,
    resolved_at,
) -> dict | None:
    """Resolve one preregistered horizon without rewriting the source episode."""
    reference_at = _stamp(episode.get("reference_at"))
    resolved = _stamp(resolved_at)
    reference_price = _number(episode.get("reference_price"))
    if reference_at is None or resolved is None or reference_price is None or reference_price <= 0:
        return None
    horizon = int(horizon_minutes)
    if horizon not in OUTCOME_HORIZONS_MINUTES:
        raise ValueError("Unsupported Crude Mini outcome horizon")
    horizon_end = reference_at + timedelta(minutes=horizon)
    if resolved < horizon_end:
        return None

    # Every path is hard-capped at its own preregistered horizon. A later bar from
    # the same session is never allowed to leak into a shorter incomplete horizon.
    future = [bar for bar in candles if reference_at < bar["visible_at"] <= horizon_end]
    endpoint = next((bar for bar in future if bar["visible_at"] == horizon_end), None)
    full_horizon = endpoint is not None
    if not full_horizon and resolved.date() <= reference_at.date():
        return None

    if full_horizon:
        path = future
        resolution_status = "RESOLVED"
    else:
        path = future
        resolution_status = "TRUNCATED_OR_INCOMPLETE_HORIZON"
        endpoint = path[-1] if path else None

    end_close = endpoint["close"] if endpoint else None
    max_up = max((max(0.0, bar["high"] - reference_price) for bar in path), default=None)
    max_down = max((max(0.0, reference_price - bar["low"]) for bar in path), default=None)
    atr = _number(episode.get("atr14"))
    action = str(episode.get("action") or "").upper()
    if action == "BUY_CE":
        favorable, adverse = max_up, max_down
    elif action == "BUY_PE":
        favorable, adverse = max_down, max_up
    else:
        favorable = adverse = None

    option = _option_outcome(episode, option_rows, horizon_end)
    availability = [bar["collected_at"] for bar in path]
    if option.get("latest_option_available_at") is not None:
        availability.append(option["latest_option_available_at"])
    available_at = max(availability) if availability else None
    diagnosis = _diagnosis(episode, max_up, max_down)
    geometry = _geometry_outcome(episode, path)

    payload = {
        "protocol_id": PROTOCOL_ID,
        "baseline_id": BASELINE_ID,
        "episode_id": episode["episode_id"],
        "horizon_minutes": horizon,
        "horizon_end_at": horizon_end.isoformat(),
        "resolution_status": resolution_status,
        "underlying": {
            "reference_at": reference_at.isoformat(),
            "reference_price": reference_price,
            "end_close": end_close,
            "max_up_points": max_up,
            "max_down_points": max_down,
            "atr14_at_click": atr,
            "path_bars": len(path),
            "geometry_outcome": geometry,
        },
        "option": {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in option.items()
        },
        "diagnosis": diagnosis,
        "rules": {
            "no_intrabar_order_assumption": True,
            "same_bar_entry_exit": "AMBIGUOUS",
            "same_bar_stop_target": "AMBIGUOUS",
            "horizon_path_never_exceeds_horizon_end": True,
            "option_prices": "IMMUTABLE_EXACT_CONTRACT_LTP_SNAPSHOTS_ONLY",
            "option_observation_time_must_not_exceed_horizon": True,
            "missed_move_threshold_atr": MISSED_MOVE_ATR_MULTIPLE,
            "missed_move_opposite_max_atr": MISSED_MOVE_OPPOSITE_MAX_ATR_MULTIPLE,
            "decision_effect": "NONE",
        },
    }
    return {
        "episode_id": episode["episode_id"],
        "horizon_minutes": horizon,
        "horizon_end_at": horizon_end,
        "resolved_at": resolved,
        "available_at": available_at,
        "resolution_status": resolution_status,
        "underlying_end_close": end_close,
        "underlying_return_pct": ((end_close / reference_price - 1.0) * 100.0) if end_close is not None else None,
        "max_up_points": max_up,
        "max_down_points": max_down,
        "max_up_atr": (max_up / atr) if max_up is not None and atr not in (None, 0) else None,
        "max_down_atr": (max_down / atr) if max_down is not None and atr not in (None, 0) else None,
        "directional_favorable_points": favorable,
        "directional_adverse_points": adverse,
        "geometry_outcome": geometry,
        "diagnosis": diagnosis,
        "option_observations": option["option_observations"],
        "option_end_premium": option["option_end_premium"],
        "option_return_pct": option["option_return_pct"],
        "option_max_premium": option["option_max_premium"],
        "option_min_premium": option["option_min_premium"],
        "payload": json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str),
    }


def _insert_outcome_sync(database_url: str, outcome: dict) -> bool:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(OUTCOME_INSERT_SQL, outcome)
            return cursor.fetchone() is not None


def _resolve_pending_sync(database_url: str, as_of: datetime) -> int:
    added = 0
    for episode in _read_episodes_sync(database_url):
        existing = _existing_horizons_sync(database_url, episode["episode_id"])
        if len(existing) == len(OUTCOME_HORIZONS_MINUTES):
            continue
        episode["reference_at"] = _stamp(episode["reference_at"])
        episode["click_at"] = _stamp(episode["click_at"])
        episode["option_sample_bucket_at"] = _stamp(episode.get("option_sample_bucket_at"))
        candles = _read_future_candles_sync(database_url, episode["reference_at"], as_of)
        option_rows = _read_future_options_sync(database_url, episode, as_of)
        for horizon in OUTCOME_HORIZONS_MINUTES:
            if horizon in existing:
                continue
            outcome = analyze_episode_outcome(
                episode,
                candles,
                option_rows,
                horizon_minutes=horizon,
                resolved_at=as_of,
            )
            if outcome is not None and _insert_outcome_sync(database_url, outcome):
                added += 1
    return added


def _summary_sync(database_url: str) -> dict:
    with _connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT action, COUNT(*) FROM {EPISODE_TABLE} WHERE baseline_id=%s GROUP BY action",
                (BASELINE_ID,),
            )
            action_counts = {str(action): int(count) for action, count in cursor.fetchall()}
            cursor.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT episode_id) FROM {OUTCOME_TABLE}",
            )
            outcome_rows, episodes_with_outcomes = cursor.fetchone()
            cursor.execute(
                f"""SELECT geometry_outcome, COUNT(*) FROM {OUTCOME_TABLE}
                    WHERE horizon_minutes=%s GROUP BY geometry_outcome""",
                (PRIMARY_OUTCOME_HORIZON_MINUTES,),
            )
            primary_geometry = {str(label): int(count) for label, count in cursor.fetchall()}
            cursor.execute(
                f"""SELECT diagnosis, COUNT(*) FROM {OUTCOME_TABLE}
                    WHERE horizon_minutes=%s GROUP BY diagnosis""",
                (PRIMARY_OUTCOME_HORIZON_MINUTES,),
            )
            primary_diagnosis = {str(label): int(count) for label, count in cursor.fetchall()}
            cursor.execute(
                f"SELECT COUNT(*) FROM {EPISODE_TABLE} WHERE baseline_id=%s",
                (BASELINE_ID,),
            )
            episode_count = int(cursor.fetchone()[0])
    return {
        "status": "ACTIVE",
        "baseline_id": BASELINE_ID,
        "protocol_id": PROTOCOL_ID,
        "episode_count": episode_count,
        "action_counts": action_counts,
        "outcome_rows": int(outcome_rows),
        "episodes_with_outcomes": int(episodes_with_outcomes),
        "primary_horizon_minutes": PRIMARY_OUTCOME_HORIZON_MINUTES,
        "primary_geometry": primary_geometry,
        "primary_diagnosis": primary_diagnosis,
        "research_stage": "CAPTURE_AND_OBSERVE",
        "decision_effect": "NONE",
        "promotion_eligible": False,
    }


async def capture_episode_and_resolve_prior(
    database_url: str,
    *,
    result: dict,
    candles: list[list],
    as_of=None,
) -> dict:
    """Capture the current frozen decision, then attach only future outcomes to prior episodes."""
    database_url = str(database_url or "").strip()
    if not database_url:
        return {"status": "UNAVAILABLE", "reason": "DATABASE_NOT_CONFIGURED"}
    observed = _stamp(as_of) or datetime.now(IST)
    await initialize_episode_ledger(database_url)
    capture = build_episode_capture(result, candles, captured_at=observed)
    inserted = await asyncio.to_thread(_capture_sync, database_url, capture)
    resolved = await asyncio.to_thread(_resolve_pending_sync, database_url, observed)
    summary = await asyncio.to_thread(_summary_sync, database_url)
    return {
        **summary,
        "current_episode_id": capture["episode_id"],
        "current_episode_captured": inserted,
        "new_outcomes_resolved": resolved,
        "capture_rule": "FIRST_DECISION_STATE_IMMUTABLE_OUTCOMES_SEPARATE",
        "historical_backfill_used": False,
    }


async def read_episode_ledger_summary(database_url: str) -> dict:
    database_url = str(database_url or "").strip()
    if not database_url:
        return {"status": "UNAVAILABLE", "reason": "DATABASE_NOT_CONFIGURED"}
    await initialize_episode_ledger(database_url)
    return await asyncio.to_thread(_summary_sync, database_url)
