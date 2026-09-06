from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, time as dt_time, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_current_mind import crude_oil_mini_current_mind_click
from .crude_oil_mini_data_probe import _complete_sessions
from .crude_oil_mini_direction_brain_v2_integrated import evaluate_integrated_direction_v2_shadow
from .crude_oil_mini_direction_memory import HORIZONS, make_direction_case
from .crude_oil_mini_experience_memory import build_experiences, memory_evidence
from .crude_oil_mini_market_perception import (
    bar_visible_at,
    causal_profiles,
    clean_ohlcv,
    latest_visible_index,
    market_regime_features,
    precompute_perception,
    price_evidence,
)
from .current_mind_crude_oil_mini_replay import (
    EXPECTED_CURRENT_CONTRACT,
    TARGET_R,
    _abstention_outcome,
    _dominant_direction,
    _geometry,
    _resolve_setup,
)

IST = ZoneInfo("Asia/Kolkata")
MODE = "CRUDE_OIL_MINI_SEP_3DAY_15M_REPLAY_V1"
WINDOW_START = "2026-06-01T00:00:00+05:30"
WINDOW_END = "2026-09-02T23:30:00+05:30"
EVALUATION_DAYS = ("2026-08-31", "2026-09-01", "2026-09-02")
CLICK_START = dt_time(10, 0)
CLICK_END = dt_time(22, 0)
CLICK_INTERVAL_MINUTES = 15
CLICKS_PER_DAY = 49
EXPECTED_CLICKS = len(EVALUATION_DAYS) * CLICKS_PER_DAY
SEED_STRIDE_BARS = 3
SEED_WARMUP_BARS = 24
SEED_TAIL_BARS = 24


def _fingerprint(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _bounded_rows(candles) -> list[list]:
    start = parse_ist_timestamp(WINDOW_START)
    end = parse_ist_timestamp(WINDOW_END)
    return [row for row in clean_ohlcv(candles) if start <= parse_ist_timestamp(row[0]) <= end]


def _click_schedule() -> list[dict]:
    clicks = []
    for day_text in EVALUATION_DAYS:
        day = datetime.fromisoformat(day_text).date()
        current = datetime.combine(day, CLICK_START, tzinfo=IST)
        end = datetime.combine(day, CLICK_END, tzinfo=IST)
        while current <= end:
            clicks.append({
                "session": day_text,
                "click_timestamp": current.isoformat(),
                "sampling": "FIXED_15_MINUTE_CLOCK_SCHEDULE",
            })
            current += timedelta(minutes=CLICK_INTERVAL_MINUTES)
    if len(clicks) != EXPECTED_CLICKS:
        raise RuntimeError(f"Expected {EXPECTED_CLICKS} clicks, got {len(clicks)}")
    return clicks


def build_click_manifest() -> dict:
    clicks = _click_schedule()
    counts = Counter(row["session"] for row in clicks)
    if any(counts.get(day) != CLICKS_PER_DAY for day in EVALUATION_DAYS):
        raise RuntimeError("Each evaluation day must contribute exactly 49 clicks")
    manifest = {
        "mode": "CRUDE_OIL_MINI_FIXED_15M_CLICK_MANIFEST_V1",
        "research_only": True,
        "outcome_blind_selection": True,
        "evaluation_days": list(EVALUATION_DAYS),
        "click_start_ist": "10:00",
        "click_end_ist": "22:00",
        "click_interval_minutes": CLICK_INTERVAL_MINUTES,
        "clicks_per_day": CLICKS_PER_DAY,
        "selected_click_count": len(clicks),
        "sampling": "FIXED_CLOCK_SCHEDULE_NOT_RANDOM",
        "clicks": clicks,
    }
    manifest["manifest_sha256"] = _fingerprint(manifest)
    return manifest


def _build_causal_direction_memory(rows: list[list], features: list[dict]) -> dict:
    complete_days = {
        item["date"]
        for item in _complete_sessions(rows)
        if item.get("complete_for_20_click_research")
    }
    by_day: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        day = parse_ist_timestamp(row[0]).astimezone(IST).date().isoformat()
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
        for index in indices[SEED_WARMUP_BARS : len(indices) - SEED_TAIL_BARS : SEED_STRIDE_BARS]:
            click = bar_visible_at(rows[index])
            base = float(rows[index][4])
            if base <= 0:
                continue
            future_returns = {}
            for minutes in HORIZONS:
                future_close = available_to_close.get((click + timedelta(minutes=minutes)).isoformat())
                if future_close is None:
                    break
                future_returns[str(minutes)] = (future_close / base - 1.0) * 100.0
            if len(future_returns) != len(HORIZONS):
                continue
            cases.append(
                make_direction_case(
                    snapshot=features[index],
                    click_timestamp=click.isoformat(),
                    available_at=(click + timedelta(minutes=max(HORIZONS))).isoformat(),
                    future_returns_pct=future_returns,
                )
            )
    return {
        "cases": cases,
        "case_count": len(cases),
        "sha256": _fingerprint(cases),
        "geometry_used": False,
        "option_pnl_used": False,
        "strict_point_in_time_query_filter": True,
    }


def _context_records_from_probe(probe: dict, click_timestamp: str) -> list[dict]:
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
        available, latest, close = visible[-1]
        records.append({
            "series": series,
            "observed_at": latest["bar_start"],
            "available_at": available.isoformat(),
            "source": "Yahoo Finance public chart",
            "quality": "E_DISCOVERY",
            "value": {"close": close},
        })
    return records


def _validate_click_data(rows: list[list], clicks: list[dict]) -> dict:
    by_day: dict[str, list[list]] = defaultdict(list)
    for row in rows:
        by_day[parse_ist_timestamp(row[0]).astimezone(IST).date().isoformat()].append(row)

    checks = []
    for item in clicks:
        click = parse_ist_timestamp(item["click_timestamp"])
        day_rows = by_day.get(item["session"], [])
        visible = [row for row in day_rows if bar_visible_at(row) <= click]
        future = [row for row in day_rows if bar_visible_at(row) > click]
        latest = visible[-1] if visible else None
        latest_visible_at = bar_visible_at(latest) if latest else None
        expected_latest_visible = click - timedelta(minutes=5)
        exact_preclick_bar = bool(
            latest
            and parse_ist_timestamp(latest[0]) == expected_latest_visible
            and latest_visible_at == click
        )
        checks.append({
            "session": item["session"],
            "click_timestamp": click.isoformat(),
            "same_session_visible_bar": bool(latest),
            "exact_completed_5m_bar_available_at_click": exact_preclick_bar,
            "future_session_bars_present": bool(future),
        })

    missing_visible = [row for row in checks if not row["same_session_visible_bar"]]
    missing_exact = [row for row in checks if not row["exact_completed_5m_bar_available_at_click"]]
    missing_future = [row for row in checks if not row["future_session_bars_present"]]
    if missing_visible or missing_exact or missing_future:
        raise RuntimeError(
            "CRUDEOILM replay data is incomplete at scheduled clicks: "
            f"missing_visible={len(missing_visible)}, missing_exact_preclick={len(missing_exact)}, "
            f"missing_future={len(missing_future)}"
        )
    return {
        "scheduled_clicks": len(checks),
        "same_session_visible_bar": len(checks) - len(missing_visible),
        "exact_completed_5m_bar_available_at_click": len(checks) - len(missing_exact),
        "future_session_bars_present": len(checks) - len(missing_future),
    }


def _summary(decisions: list[dict]) -> dict:
    actions = Counter(row["action"] for row in decisions)
    trades = [row for row in decisions if row["action"] in {"BUY_CE", "BUY_PE"}]
    waits = [row for row in decisions if row["action"] == "WAIT"]
    outcomes = Counter((row.get("outcome") or {}).get("result") for row in trades)
    resolved = [row for row in trades if (row.get("outcome") or {}).get("result") in {"TARGET", "STOP"}]
    entered = [row for row in trades if (row.get("outcome") or {}).get("entry_at")]
    realized = [float((row.get("outcome") or {}).get("realized_r") or 0.0) for row in trades]
    resolved_r = [float(row["outcome"]["realized_r"]) for row in resolved]
    mfe = [float(row["outcome"]["mfe_r"]) for row in entered if row["outcome"].get("mfe_r") is not None]
    mae = [float(row["outcome"]["mae_r"]) for row in entered if row["outcome"].get("mae_r") is not None]
    net_r = sum(realized)
    return {
        "clicks": len(decisions),
        "actions": dict(sorted(actions.items())),
        "trades": len(trades),
        "waits": len(waits),
        "targets": outcomes.get("TARGET", 0),
        "stops": outcomes.get("STOP", 0),
        "no_entry": outcomes.get("NO_ENTRY", 0),
        "session_end": outcomes.get("SESSION_END", 0),
        "resolved_trades": len(resolved),
        "win_rate_resolved_pct": round(outcomes.get("TARGET", 0) / len(resolved) * 100.0, 2) if resolved else None,
        "net_r": round(net_r, 4),
        "expectancy_r_per_trade": round(net_r / len(trades), 4) if trades else None,
        "expectancy_r_resolved": round(mean(resolved_r), 4) if resolved_r else None,
        "avg_mfe_r_entered": round(mean(mfe), 4) if mfe else None,
        "avg_mae_r_entered": round(mean(mae), 4) if mae else None,
        "missed_large_moves_after_wait": sum(
            bool((row.get("outcome") or {}).get("future_move_without_setup")) for row in waits
        ),
    }


def _overlay_classification(action: str, shadow: dict) -> str | None:
    if action not in {"BUY_CE", "BUY_PE"}:
        return None
    expected = "BULLISH" if action == "BUY_CE" else "BEARISH"
    opposite = "BEARISH" if expected == "BULLISH" else "BULLISH"
    direction = str(shadow.get("direction") or "UNKNOWN")
    confidence = str(shadow.get("direction_confidence") or "WEAK")
    if direction == expected:
        return "CONFIRMS"
    if direction == opposite:
        return "OPPOSES"
    if confidence == "CONFLICTED":
        return "CONFLICTS"
    return "ABSTAINS"


def _overlay_summary(decisions: list[dict]) -> dict:
    out = {}
    for label in ("CONFIRMS", "ABSTAINS", "CONFLICTS", "OPPOSES"):
        rows = [row for row in decisions if row.get("integrated_v2_overlay") == label]
        outcomes = Counter((row.get("outcome") or {}).get("result") for row in rows)
        net_r = sum(float((row.get("outcome") or {}).get("realized_r") or 0.0) for row in rows)
        resolved = outcomes.get("TARGET", 0) + outcomes.get("STOP", 0)
        out[label] = {
            "trades": len(rows),
            "targets": outcomes.get("TARGET", 0),
            "stops": outcomes.get("STOP", 0),
            "no_entry": outcomes.get("NO_ENTRY", 0),
            "session_end": outcomes.get("SESSION_END", 0),
            "resolved_win_rate_pct": round(outcomes.get("TARGET", 0) / resolved * 100.0, 2) if resolved else None,
            "net_r_existing_current_mind_geometry": round(net_r, 4),
            "descriptive_only_not_a_candidate_filter": True,
        }
    return out


def _option_coverage(option_readiness: dict | None) -> dict:
    if not option_readiness:
        return {
            "status": "UNAVAILABLE_FROM_WORKFLOW",
            "decision_effect": "NONE",
            "click_level_attachment": False,
            "reason": "Current Mind and Integrated V2 do not consume option snapshots; no snapshot is reconstructed.",
            "daily": {},
        }
    wanted = set(EVALUATION_DAYS)
    daily = {
        str(row.get("day")): row
        for row in option_readiness.get("daily") or []
        if str(row.get("day")) in wanted
    }
    return {
        "status": "AGGREGATE_PIT_COVERAGE_AVAILABLE",
        "decision_effect": "NONE",
        "click_level_attachment": False,
        "data_type": option_readiness.get("data_type"),
        "daily": daily,
        "note": (
            "The existing secured readiness endpoint exposes daily/bucket aggregates, not raw historical rows. "
            "No click-level option state is guessed or reconstructed, and option snapshots do not drive either brain in this replay."
        ),
    }


def replay_contract() -> dict:
    return {
        "mode": MODE,
        "research_only": True,
        "descriptive_only": True,
        "promotion_eligible": False,
        "current_mind_rules_changed": False,
        "integrated_v2_shadow_only": True,
        "fixed_clock_schedule": True,
        "click_interval_minutes": CLICK_INTERVAL_MINUTES,
        "clicks_per_day": CLICKS_PER_DAY,
        "evaluation_days": list(EVALUATION_DAYS),
        "full_remaining_session_path_used": True,
        "fixed_horizon_direction_scoring_used": False,
        "threshold_search_used": False,
        "parameter_optimization_used": False,
        "time_filter_selected": False,
        "pe_disabled": False,
        "synthetic_option_prices_used": False,
        "regular_crude_used": False,
        "event_archive_backfilled": False,
        "missing_futures_oi_reconstructed": False,
    }


def evaluate_three_day_15m_replay(
    candles,
    *,
    contract: dict | None = None,
    global_context_probe: dict | None = None,
    option_readiness: dict | None = None,
) -> dict:
    manifest = build_click_manifest()
    rows, features = precompute_perception(_bounded_rows(candles))
    if not rows:
        raise RuntimeError("No CRUDEOILM candles available in replay window")

    contract_info = dict(contract or {})
    reference_contract = contract_info.get("trading_symbol") or EXPECTED_CURRENT_CONTRACT
    if reference_contract != EXPECTED_CURRENT_CONTRACT:
        raise RuntimeError(
            f"Expected current CRUDEOILM contract {EXPECTED_CURRENT_CONTRACT}, got {reference_contract}"
        )

    click_data = _validate_click_data(rows, manifest["clicks"])
    profiles = causal_profiles(rows, features)
    complete_days = {
        item["date"]
        for item in _complete_sessions(rows)
        if item.get("complete_for_20_click_research")
    }
    experiences = build_experiences(rows, features, complete_days, sample_every_bars=3)
    direction_memory = _build_causal_direction_memory(rows, features)

    probe = global_context_probe or {}
    required_global = ("WTI_CRUDE", "BRENT_CRUDE")
    global_status = {
        series: ((probe.get("feeds") or {}).get(series) or {}).get("status", "UNAVAILABLE")
        for series in ("WTI_CRUDE", "BRENT_CRUDE", "USDINR", "DXY")
    }
    missing_required = [series for series in required_global if global_status.get(series) != "AVAILABLE"]
    if missing_required:
        raise RuntimeError(f"Required PIT global crude context unavailable: {missing_required}")

    by_day: dict[str, list[list]] = defaultdict(list)
    for row in rows:
        by_day[parse_ist_timestamp(row[0]).astimezone(IST).date().isoformat()].append(row)

    decisions = []
    current_mind_memory_cases = []
    for click_meta in manifest["clicks"]:
        click = parse_ist_timestamp(click_meta["click_timestamp"])
        visible_index = latest_visible_index(rows, click)
        if visible_index is None:
            raise RuntimeError(f"No completed CRUDEOILM bar visible at {click.isoformat()}")
        snapshot = features[visible_index]
        if parse_ist_timestamp(snapshot["timestamp"]).astimezone(IST).date() != click.date():
            raise RuntimeError(f"Click {click.isoformat()} has no same-session completed perception bar")

        profile = profiles.get(click.date().isoformat()) or {}
        evidence_items = price_evidence(snapshot, profile)
        evidence_items.append(memory_evidence(experiences, snapshot, click.isoformat()))
        current_direction = _dominant_direction(evidence_items)
        visible_session_rows = [
            row for row in by_day[click.date().isoformat()]
            if bar_visible_at(row) <= click
        ]
        market = market_regime_features(snapshot, profile)
        market.update(_geometry(visible_session_rows, current_direction) if current_direction else {})
        current_context = [{
            "series": "MCX_CRUDEOILM",
            "observed_at": snapshot["timestamp"],
            "available_at": bar_visible_at(rows[visible_index]).isoformat(),
            "source": reference_contract,
            "value": {"price": snapshot.get("price")},
            "quality": "OBSERVED",
        }]
        journal = crude_oil_mini_current_mind_click(
            click_timestamp=click.isoformat(),
            context_records=current_context,
            market_features=market,
            evidence_items=evidence_items,
            memory_cases=current_mind_memory_cases,
        )
        decision = journal.get("decision") or {}
        raw_action = str(decision.get("action") or "NO_TRADE")
        action = raw_action if raw_action in {"BUY_CE", "BUY_PE"} else "WAIT"
        geometry = {
            "entry_price": market.get("entry_price"),
            "stop_price": market.get("stop_price"),
            "target_price": market.get("target_price"),
        } if action in {"BUY_CE", "BUY_PE"} else None

        outcome = _resolve_setup(by_day[click.date().isoformat()], click, action, geometry)
        if outcome is None:
            atr_pct = snapshot.get("atr_pct")
            try:
                atr_pct = float(atr_pct) if atr_pct is not None else None
            except (TypeError, ValueError):
                atr_pct = None
            outcome = _abstention_outcome(
                by_day[click.date().isoformat()], click, float(snapshot["price"]), atr_pct
            )

        cross_market_records = _context_records_from_probe(probe, click.isoformat())
        shadow = evaluate_integrated_direction_v2_shadow(
            click_timestamp=click.isoformat(),
            snapshot=snapshot,
            profile=profile,
            mini_candles=rows,
            global_context_probe=probe,
            context_records=cross_market_records,
            event_records=[],
            direction_memory_cases=direction_memory["cases"],
        )
        participation = (shadow.get("families") or {}).get("PARTICIPATION") or {}
        global_family = (shadow.get("families") or {}).get("GLOBAL_CRUDE") or {}
        event_family = (shadow.get("families") or {}).get("EVENT_REACTION") or {}
        overlay = _overlay_classification(action, shadow)

        decisions.append({
            "session": click_meta["session"],
            "click_timestamp": click.isoformat(),
            "action": action,
            "raw_current_mind_action": raw_action,
            "current_mind_direction": decision.get("direction"),
            "current_mind_playbook": decision.get("playbook"),
            "entry_price": geometry.get("entry_price") if geometry else None,
            "stop_price": geometry.get("stop_price") if geometry else None,
            "target_price": geometry.get("target_price") if geometry else None,
            "risk_reward": TARGET_R if geometry else None,
            "outcome": outcome,
            "integrated_v2": {
                "direction": shadow.get("direction"),
                "confidence": shadow.get("direction_confidence"),
                "thesis_state": shadow.get("thesis_state"),
                "supporting_families": shadow.get("supporting_families") or [],
                "opposing_families": shadow.get("opposing_families") or [],
                "participation_state": participation.get("state"),
                "participation_counts_for_direction": bool(participation.get("counts_for_direction")),
                "participation_oi_available": bool((participation.get("detail") or {}).get("oi_available")),
                "global_direction": global_family.get("stance"),
                "global_state": global_family.get("state"),
                "event_state": event_family.get("state"),
                "event_counts_for_direction": bool(event_family.get("counts_for_direction")),
                "dependency_suppressed": (shadow.get("dependency_audit") or {}).get("suppressed") or [],
            },
            "integrated_v2_overlay": overlay,
        })
        current_mind_memory_cases.append({
            "available_at": outcome.get("resolved_at"),
            "regime": journal.get("regime"),
            "evidence": journal.get("evidence"),
            "action": action,
            "outcome": outcome,
            "decision_fingerprint": journal.get("decision_fingerprint"),
        })

    by_session: dict[str, list[dict]] = defaultdict(list)
    for row in decisions:
        by_session[row["session"]].append(row)
    daily = {day: _summary(by_session[day]) for day in EVALUATION_DAYS}

    integrated_directions = Counter(row["integrated_v2"]["direction"] for row in decisions)
    integrated_confidence = Counter(row["integrated_v2"]["confidence"] for row in decisions)
    participation_states = Counter(row["integrated_v2"]["participation_state"] for row in decisions)
    global_directions = Counter(row["integrated_v2"]["global_direction"] for row in decisions)
    event_states = Counter(row["integrated_v2"]["event_state"] for row in decisions)

    result = {
        "mode": MODE,
        "product": "CRUDE_OIL_MINI",
        "underlying_symbol": "CRUDEOILM",
        "reference_contract": reference_contract,
        "research_only": True,
        "descriptive_only": True,
        "promotion_eligible": False,
        "manifest": manifest,
        "daily": daily,
        "combined": _summary(decisions),
        "integrated_v2_shadow": {
            "directions": dict(sorted(integrated_directions.items())),
            "confidence": dict(sorted(integrated_confidence.items())),
            "participation_states": dict(sorted(participation_states.items())),
            "participation_directional_votes": sum(
                row["integrated_v2"]["participation_counts_for_direction"] for row in decisions
            ),
            "participation_clicks_with_oi": sum(
                row["integrated_v2"]["participation_oi_available"] for row in decisions
            ),
            "global_directions": dict(sorted(global_directions.items())),
            "event_states": dict(sorted(event_states.items())),
            "event_directional_votes": sum(
                row["integrated_v2"]["event_counts_for_direction"] for row in decisions
            ),
            "trade_overlay": _overlay_summary(decisions),
            "decision_effect": "NONE",
        },
        "data_coverage": {
            "candle_window": {
                "rows": len(rows),
                "first_bar": rows[0][0],
                "last_bar": rows[-1][0],
                "scheduled_click_integrity": click_data,
            },
            "global_context_feeds": global_status,
            "direction_memory_cases": direction_memory["case_count"],
            "option_snapshots": _option_coverage(option_readiness),
            "events": {
                "status": "NOT_INCLUDED_NO_SEP_1_2_FROZEN_PIT_ARCHIVE",
                "direction_effect": "NONE",
                "future_or_retrospective_news_backfill": False,
            },
            "underlying_futures_oi": {
                "status": "NOT_PRESENT_IN_HISTORICAL_CRUDEOILM_CANDLE_TAPE",
                "reconstructed": False,
            },
        },
        "contract": replay_contract(),
        "decisions": decisions,
    }
    result["result_sha256"] = _fingerprint({k: v for k, v in result.items() if k != "result_sha256"})
    return result
