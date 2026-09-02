from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commodity_candle_collector import _records
from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_contracts import (
    CRUDE_OIL_MINI,
    fetch_crude_oil_mini_master,
    resolve_crude_oil_mini_universe,
)
from .crude_oil_mini_data_probe import _complete_sessions
from .crude_oil_mini_direction_capture_store import PostgresCrudeDirectionCaptureStore
from .crude_oil_mini_direction_forward import (
    HORIZONS,
    MODE,
    VALIDATION_PHASE,
    capture_shadow_direction,
    evaluate_phase,
    mature_underlying_outcome,
    phase_schedule,
)
from .crude_oil_mini_direction_memory import make_direction_case
from .crude_oil_mini_market_perception import (
    bar_visible_at,
    causal_profiles,
    latest_visible_index,
    precompute_perception,
)
from .crude_oil_mini_research_tape import (
    FROZEN_CURRENT_CONTRACT,
    FROZEN_RESEARCH_END,
    FROZEN_RESEARCH_START,
    _completed_end,
    _fetch_exact_range,
    _storage_contract,
    certify_frozen_research_tape,
)
from .crude_oil_pit_context_probe import probe_crude_oil_pit_context

IST = ZoneInfo("Asia/Kolkata")
MAX_CAPTURE_LATENESS_MINUTES = 10
SEED_STRIDE_BARS = 3
SEED_WARMUP_BARS = 24
SEED_TAIL_BARS = 24
LIVE_TAPE_START = datetime(2026, 9, 1, 0, 0, tzinfo=IST)
CONTEXT_LOOKBACK_DAYS = 3


def _sha256(payload) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def classify_schedule(schedule, now, captured_clicks=None, missed_clicks=None) -> dict:
    """Classify the frozen clock schedule without looking at any price or outcome."""
    observed = parse_ist_timestamp(now).astimezone(IST)
    captured = {parse_ist_timestamp(value).isoformat() for value in (captured_clicks or [])}
    missed = {parse_ist_timestamp(value).isoformat() for value in (missed_clicks or [])}
    due, expired, future, resolved = [], [], [], []
    max_late = timedelta(minutes=MAX_CAPTURE_LATENESS_MINUTES)
    for row in schedule or []:
        click = parse_ist_timestamp(row["click_timestamp"]).astimezone(IST)
        key = click.isoformat()
        if key in captured or key in missed:
            resolved.append(row)
        elif observed < click:
            future.append(row)
        elif observed < click + max_late:
            due.append(row)
        else:
            expired.append(row)
    return {
        "due": due,
        "expired": expired,
        "future": future,
        "resolved": resolved,
        "max_capture_lateness_minutes": MAX_CAPTURE_LATENESS_MINUTES,
    }


async def _refresh_live_mini_tape(provider, candle_store, now: datetime) -> dict:
    """Persist only exact CRUDEOILM 5m candles; regular CRUDEOIL is untouched."""
    observed = parse_ist_timestamp(now).astimezone(IST)
    rows = await fetch_crude_oil_mini_master()
    universe = resolve_crude_oil_mini_universe(rows, observed)
    contract = _storage_contract(dict(universe["future"]))
    trading_symbol = str(contract.get("trading_symbol") or "").upper()
    if trading_symbol != FROZEN_CURRENT_CONTRACT:
        raise RuntimeError(
            f"Phase 1 is frozen to {FROZEN_CURRENT_CONTRACT}; resolved {trading_symbol}"
        )
    await candle_store.initialize()
    latest = await candle_store.latest_candle_at(trading_symbol, 5)
    if latest is None:
        raise RuntimeError(
            "Canonical CRUDEOILM research tape is absent from persistent storage; "
            "prospective capture refuses to reconstruct its development state from scratch"
        )
    start = max(LIVE_TAPE_START, parse_ist_timestamp(latest).astimezone(IST) - timedelta(minutes=10))
    end = _completed_end(observed)
    if end < start:
        return {
            "status": "UP_TO_DATE",
            "contract": trading_symbol,
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "fetched": 0,
            "upserted": 0,
        }
    fetched = await _fetch_exact_range(provider, contract, start, end)
    records = _records(CRUDE_OIL_MINI, contract, 5, fetched, observed)
    upserted = await candle_store.upsert(records)
    return {
        "status": "REFRESHED",
        "contract": trading_symbol,
        "contract_expiry": contract.get("expiry_date"),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "fetched": len(fetched),
        "upserted": upserted,
        "regular_crude_used": False,
        "timeframe_minutes": 5,
    }


async def _read_exact_mini_tape(candle_store, end) -> list[list]:
    segments = await candle_store.read_symbol_contract_segments(
        CRUDE_OIL_MINI,
        5,
        FROZEN_RESEARCH_START,
        parse_ist_timestamp(end).astimezone(IST),
    )
    exact = [
        segment for segment in segments
        if str(segment.get("trading_symbol") or "").upper() == FROZEN_CURRENT_CONTRACT
    ]
    if len(exact) != 1:
        raise RuntimeError(
            f"Prospective CRUDEOILM tape requires exactly one {FROZEN_CURRENT_CONTRACT} segment; found {len(exact)}"
        )
    return list(exact[0].get("candles") or [])


def build_seed_direction_memory(candles) -> dict:
    """Freeze geometry-free June-Aug direction memory using a clock-only 15m stride.

    Every seed case uses the completed 5m source bar, starts only after 24 warmup bars,
    preserves a 120-minute same-session tail, and becomes available only after +120m.
    TARGET/STOP, entry/stop/target and option data never enter the seed.
    """
    rows, features = precompute_perception(candles)
    frozen_indices = [
        index for index, row in enumerate(rows)
        if parse_ist_timestamp(row[0]).astimezone(IST) <= FROZEN_RESEARCH_END
    ]
    frozen_rows = [rows[index] for index in frozen_indices]
    complete_days = {
        item["date"] for item in _complete_sessions(frozen_rows)
        if item.get("complete_for_20_click_research")
    }
    by_day = defaultdict(list)
    for index in frozen_indices:
        day = parse_ist_timestamp(rows[index][0]).astimezone(IST).date().isoformat()
        if day in complete_days:
            by_day[day].append(index)

    cases = []
    for day in sorted(by_day):
        indices = by_day[day]
        if len(indices) <= SEED_WARMUP_BARS + SEED_TAIL_BARS:
            continue
        available_to_close = {
            bar_visible_at(rows[index]).isoformat(): float(rows[index][4])
            for index in indices
        }
        eligible = indices[SEED_WARMUP_BARS : len(indices) - SEED_TAIL_BARS : SEED_STRIDE_BARS]
        for index in eligible:
            click = bar_visible_at(rows[index])
            base = float(rows[index][4])
            if base <= 0:
                continue
            future_returns = {}
            for minutes in HORIZONS:
                target = (click + timedelta(minutes=minutes)).isoformat()
                close = available_to_close.get(target)
                if close is None:
                    break
                future_returns[str(minutes)] = (close / base - 1.0) * 100.0
            if len(future_returns) != len(HORIZONS):
                continue
            cases.append(make_direction_case(
                snapshot=features[index],
                click_timestamp=click.isoformat(),
                available_at=(click + timedelta(minutes=max(HORIZONS))).isoformat(),
                future_returns_pct=future_returns,
            ))
    return {
        "mode": "CRUDE_OIL_MINI_DIRECTION_MEMORY_JUNE_AUG_SEED_V1",
        "cases": cases,
        "case_count": len(cases),
        "sha256": _sha256(cases),
        "source_window_end": FROZEN_RESEARCH_END.isoformat(),
        "stride_bars": SEED_STRIDE_BARS,
        "warmup_bars": SEED_WARMUP_BARS,
        "tail_bars": SEED_TAIL_BARS,
        "geometry_used": False,
        "option_pnl_used": False,
        "phase1_memory_policy": "FROZEN_JUNE_AUG_SEED_NO_ONLINE_EXPANSION",
    }


def context_records_from_probe(probe: dict, click_timestamp: str) -> list[dict]:
    """Translate completed hourly discovery bars into simple PIT directional context.

    The adapter is frozen before Phase 1: WTI, Brent and USDINR stance is the sign of
    the latest completed one-hour close-to-close return. DXY is journalled but does not
    form an independent Direction V2 family.
    """
    click = parse_ist_timestamp(click_timestamp).astimezone(IST)
    records = []
    for series in ("WTI_CRUDE", "BRENT_CRUDE", "USDINR", "DXY"):
        feed = (probe.get("feeds") or {}).get(series) or {}
        if feed.get("status") != "AVAILABLE":
            continue
        visible = []
        for row in feed.get("data") or []:
            try:
                available = parse_ist_timestamp(row["available_at"]).astimezone(IST)
                close = float(row["close"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if available <= click and close > 0:
                visible.append((available, row, close))
        visible.sort(key=lambda item: item[0])
        if not visible:
            continue
        latest_available, latest, latest_close = visible[-1]
        stance = "UNKNOWN"
        return_1h = None
        if len(visible) >= 2:
            previous_close = visible[-2][2]
            return_1h = (latest_close / previous_close - 1.0) * 100.0 if previous_close > 0 else None
            if return_1h is not None and return_1h != 0:
                stance = "BULLISH" if return_1h > 0 else "BEARISH"
        records.append({
            "series": series,
            "observed_at": latest["bar_start"],
            "available_at": latest_available.isoformat(),
            "source": "Yahoo Finance public chart",
            "quality": "E_DISCOVERY",
            "value": {
                "close": latest_close,
                "return_1h_pct": round(return_1h, 6) if return_1h is not None else None,
                "stance": stance,
                "adapter": "LATEST_COMPLETED_1H_CLOSE_TO_CLOSE_SIGN_V1",
            },
        })
    return records


async def _probe_live_context(now: datetime) -> dict:
    observed = parse_ist_timestamp(now).astimezone(IST)
    return await probe_crude_oil_pit_context(
        observed - timedelta(days=CONTEXT_LOOKBACK_DAYS),
        observed + timedelta(hours=1),
    )


def _operational_phase_view(report: dict) -> dict:
    return {
        "status": report.get("status"),
        "validation_phase": report.get("validation_phase"),
        "coverage": report.get("coverage"),
        "requirements": report.get("requirements"),
        "gates": report.get("gates"),
        "score_revealed": False,
        "sealed_until_ready_for_review": [
            "horizon_score",
            "confidence_score",
            "descriptive_evidence",
        ],
        "promotion_allowed": False,
    }


async def load_phase_report(capture_store: PostgresCrudeDirectionCaptureStore) -> dict:
    captures = await capture_store.list_captures()
    outcomes = await capture_store.list_outcomes()
    misses = await capture_store.list_misses()
    report = evaluate_phase(
        [row["capture"] for row in captures],
        [row["outcome"] for row in outcomes],
    )
    report["capture_misses"] = len(misses)
    return report


async def run_direction_v2_capture_tick(
    provider,
    candle_store,
    capture_store: PostgresCrudeDirectionCaptureStore,
    *,
    now=None,
) -> dict:
    """Run one idempotent prospective scheduler tick without touching Current Mind."""
    observed = parse_ist_timestamp(now or datetime.now(IST)).astimezone(IST)
    await capture_store.initialize()
    captures = await capture_store.list_captures()
    misses = await capture_store.list_misses()
    captured_clicks = {row["click_timestamp"] for row in captures}
    missed_clicks = {row["click_timestamp"] for row in misses}
    schedule_state = classify_schedule(
        phase_schedule(), observed, captured_clicks=captured_clicks, missed_clicks=missed_clicks,
    )

    new_misses = 0
    for row in schedule_state["expired"]:
        inserted = await capture_store.record_miss_once(
            click_timestamp=row["click_timestamp"],
            observed_at=observed,
            reason="CAPTURE_WINDOW_EXPIRED",
            detail={
                "max_capture_lateness_minutes": MAX_CAPTURE_LATENESS_MINUTES,
                "policy": "Never reconstruct a prospective thesis after the frozen capture window.",
            },
            validation_phase=VALIDATION_PHASE,
        )
        new_misses += int(inserted)

    outcomes = await capture_store.list_outcomes()
    outcome_fingerprints = {row["capture_fingerprint"] for row in outcomes}
    mature_candidates = [
        row for row in captures
        if row["capture_fingerprint"] not in outcome_fingerprints
        and observed >= parse_ist_timestamp(row["click_timestamp"]) + timedelta(minutes=max(HORIZONS))
    ]
    due = list(schedule_state["due"])
    tape_refresh = None
    tape_rows = None
    if due or mature_candidates:
        tape_refresh = await _refresh_live_mini_tape(provider, candle_store, observed)
        tape_rows = await _read_exact_mini_tape(candle_store, observed)

    inserted_captures = []
    if due:
        certification = await certify_frozen_research_tape(candle_store)
        if certification.get("status") != "CERTIFIED":
            raise RuntimeError("Canonical June-Aug CRUDEOILM tape failed certification before prospective capture")
        rows, features = precompute_perception(tape_rows)
        profiles = causal_profiles(rows, features)
        seed = build_seed_direction_memory(tape_rows)
        if seed["case_count"] < 20:
            raise RuntimeError("Direction V2 seed memory has insufficient geometry-free prior cases")
        context_probe = await _probe_live_context(observed)

        for scheduled in due:
            click = parse_ist_timestamp(scheduled["click_timestamp"]).astimezone(IST)
            visible_index = latest_visible_index(rows, click)
            if visible_index is None:
                continue
            snapshot = features[visible_index]
            if parse_ist_timestamp(snapshot["timestamp"]).date() != click.date():
                continue
            profile = profiles.get(click.date().isoformat()) or {}
            context_records = context_records_from_probe(context_probe, click.isoformat())
            capture = capture_shadow_direction(
                click_timestamp=click.isoformat(),
                snapshot=snapshot,
                profile=profile,
                context_records=context_records,
                direction_memory_cases=seed["cases"],
            )
            persist_clock = observed if now is not None else datetime.now(IST)
            if persist_clock >= click + timedelta(minutes=MAX_CAPTURE_LATENESS_MINUTES):
                await capture_store.record_miss_once(
                    click_timestamp=click,
                    observed_at=persist_clock,
                    reason="CAPTURE_COMPUTATION_FINISHED_LATE",
                    detail={
                        "max_capture_lateness_minutes": MAX_CAPTURE_LATENESS_MINUTES,
                        "capture_fingerprint_discarded": capture["capture_fingerprint"],
                    },
                    validation_phase=VALIDATION_PHASE,
                )
                continue
            source_state = {
                "mode": "CRUDE_OIL_MINI_DIRECTION_V2_SOURCE_STATE_V1",
                "click_timestamp": click.isoformat(),
                "trading_symbol": tape_refresh["contract"],
                "snapshot": snapshot,
                "profile": profile,
                "context_records": context_records,
                "direction_memory_seed": {
                    "mode": seed["mode"],
                    "case_count": seed["case_count"],
                    "sha256": seed["sha256"],
                    "source_window_end": seed["source_window_end"],
                    "phase1_memory_policy": seed["phase1_memory_policy"],
                },
                "future_outcome_visible": False,
                "trade_geometry_used": False,
                "option_pnl_used": False,
            }
            source_sha = _sha256(source_state)
            inserted = await capture_store.insert_capture_once(
                click_timestamp=click,
                captured_at=persist_clock,
                trading_symbol=tape_refresh["contract"],
                capture=capture,
                source_state=source_state,
                source_state_sha256=source_sha,
                validation_phase=VALIDATION_PHASE,
            )
            if inserted:
                inserted_captures.append({
                    "click_timestamp": click.isoformat(),
                    "capture_fingerprint": capture["capture_fingerprint"],
                    "direction": capture.get("direction"),
                    "direction_confidence": capture.get("direction_confidence"),
                })

    matured_outcomes = []
    if tape_rows is not None:
        # Re-read captures so newly inserted rows can mature only on later scheduler ticks.
        current_captures = await capture_store.list_captures()
        current_outcomes = await capture_store.list_outcomes()
        outcome_fingerprints = {row["capture_fingerprint"] for row in current_outcomes}
        for row in current_captures:
            capture = row["capture"]
            click = parse_ist_timestamp(capture["click_timestamp"])
            if capture["capture_fingerprint"] in outcome_fingerprints:
                continue
            if observed < click + timedelta(minutes=max(HORIZONS)):
                continue
            outcome = mature_underlying_outcome(capture, tape_rows, as_of=observed.isoformat())
            if any(
                (outcome.get("horizons") or {}).get(str(minutes), {}).get("status") != "MATURED"
                for minutes in HORIZONS
            ):
                continue
            inserted = await capture_store.insert_outcome_once(
                capture=capture,
                outcome=outcome,
                matured_at=observed,
            )
            if inserted:
                matured_outcomes.append({
                    "click_timestamp": capture["click_timestamp"],
                    "outcome_fingerprint": outcome["outcome_fingerprint"],
                })

    report = await load_phase_report(capture_store)
    store_status = await capture_store.status()
    return {
        "status": "TICK_COMPLETED",
        "mode": MODE,
        "validation_phase": VALIDATION_PHASE,
        "research_only": True,
        "shadow_only": True,
        "live_execution_enabled": False,
        "observed_at": observed.isoformat(),
        "max_capture_lateness_minutes": MAX_CAPTURE_LATENESS_MINUTES,
        "due_clicks_seen": len(due),
        "captures_inserted": inserted_captures,
        "outcomes_matured": matured_outcomes,
        "misses_recorded": new_misses,
        "tape_refresh": tape_refresh,
        "store": store_status,
        "phase": _operational_phase_view(report),
        "score_revealed": False,
    }


def runtime_contract() -> dict:
    return {
        "mode": "CRUDE_OIL_MINI_DIRECTION_V2_CAPTURE_RUNTIME_V1",
        "validation_phase": VALIDATION_PHASE,
        "research_only": True,
        "shadow_only": True,
        "max_capture_lateness_minutes": MAX_CAPTURE_LATENESS_MINUTES,
        "capture_must_precede_first_15m_outcome": True,
        "capture_store": "POSTGRES_APPEND_ONLY",
        "outcome_store": "POSTGRES_SEPARATE_APPEND_ONLY",
        "regular_crude_collector_changed": False,
        "current_mind_mutation_allowed": False,
        "live_execution_enabled": False,
        "direction_memory_seed": "JUNE_AUG_GEOMETRY_FREE_15M_STRIDE_FIXED_DURING_PHASE1",
        "global_context_adapter": "LATEST_COMPLETED_1H_CLOSE_TO_CLOSE_SIGN_V1",
        "global_context_source_grade": "E_DISCOVERY",
        "promotion_allowed": False,
    }
