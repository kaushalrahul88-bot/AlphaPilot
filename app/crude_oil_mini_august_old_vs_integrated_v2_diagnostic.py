from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_data_probe import _complete_sessions
from .crude_oil_mini_direction_brain_v2 import evaluate_direction_brain_v2_shadow
from .crude_oil_mini_direction_brain_v2_integrated import evaluate_integrated_direction_v2_shadow
from .crude_oil_mini_direction_memory import HORIZONS, make_direction_case
from .crude_oil_mini_market_perception import (
    bar_visible_at,
    causal_profiles,
    clean_ohlcv,
    latest_visible_index,
    precompute_perception,
)

IST = ZoneInfo("Asia/Kolkata")
MODE = "CRUDE_OIL_MINI_AUGUST_OLD_VS_INTEGRATED_V2_FROZEN_DIAGNOSTIC_V1"
WINDOW_START = "2026-06-01T00:00:00+05:30"
WINDOW_END = "2026-08-31T23:30:00+05:30"
EXPECTED_MANIFEST_SHA256 = "c1e813f9748b746b7f0e90e0558ed33df1afe1b579dee23e3c3193f46b934349"
EXPECTED_BASELINE_NET_R = -10.0
EXPECTED_CLICKS = 420
EXPECTED_TRADES = 119
EXPECTED_BUY_PE = 38
EXPECTED_OLD_DIRECTIONS = {"BEARISH": 67, "BULLISH": 122, "UNKNOWN": 231}
EXPECTED_OLD_CONFIDENCE = {"CONFLICTED": 115, "MODERATE": 121, "STRONG": 68, "WEAK": 116}
EXPECTED_EVENT_RECORDS = 26
EVENT_ARCHIVE_COMMIT = "3f55c02096b7d30d424b9cf1018774d54fe69ce7"
SEED_STRIDE_BARS = 3
SEED_WARMUP_BARS = 24
SEED_TAIL_BARS = 24
DIRECTIONAL = {"BULLISH", "BEARISH"}
EVENT_PARTS = (
    "crude_aug_2026_pit_events_01_07.json",
    "crude_aug_2026_pit_events_08_14.json",
    "crude_aug_2026_pit_events_15_21.json",
    "crude_aug_2026_pit_events_22_30.json",
)


def _sha256(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counter(values) -> dict:
    return dict(sorted(Counter(values).items()))


def _bounded_rows(candles) -> list[list]:
    start = parse_ist_timestamp(WINDOW_START)
    end = parse_ist_timestamp(WINDOW_END)
    return [row for row in clean_ohlcv(candles) if start <= parse_ist_timestamp(row[0]) <= end]


def _utc(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_expectations(event_payload: dict, release_utc: datetime) -> tuple[list[dict], list[dict]]:
    all_rows = []
    usable = []
    for raw in (event_payload or {}).get("expectations") or []:
        if not isinstance(raw, dict):
            continue
        row = deepcopy(raw)
        try:
            available = _utc(row.get("available_at_utc"))
            pre_release = available < release_utc
        except Exception:
            pre_release = False
        row["pre_release_usable"] = pre_release
        all_rows.append(row)
        if pre_release:
            usable.append(row)
    return all_rows, usable


def _archive_record_to_context(record: dict) -> dict | None:
    """Convert one frozen archive record without adding direction or reaction labels."""
    allowed = {"EIA_CRUDE_INVENTORY", "OPEC_SUPPLY", "CRUDE_MACRO_RELEASE", "CRUDE_NEWS"}
    if not isinstance(record, dict) or not bool(record.get("pit_usable")):
        return None
    series = str(record.get("series") or "").upper()
    if series not in allowed:
        return None
    available_at = str(record.get("available_at_ist") or "")
    published_at_utc = str(record.get("published_at_utc") or "")
    try:
        available = parse_ist_timestamp(available_at).astimezone(IST)
        release_utc = _utc(published_at_utc)
    except Exception:
        return None

    payload = deepcopy(record.get("event_payload") or {})
    expectations_all, expectations_pre_release = _clean_expectations(payload, release_utc)
    if payload:
        payload["expectations"] = expectations_all
        payload["expectations_pre_release"] = expectations_pre_release
        payload["pre_release_consensus_available"] = bool(expectations_pre_release)

    value = {
        "event_id": record.get("event_id"),
        "event_type": record.get("event_type"),
        "headline": record.get("headline"),
        "facts": record.get("facts"),
        "mechanism_tags": list(record.get("mechanism_tags") or []),
        "timestamp_quality": record.get("timestamp_quality"),
        "published_at_utc": published_at_utc,
        "event_payload": payload,
        "mechanism_stance": "UNKNOWN",
        "materiality_status": "UNASSESSED",
        "novelty_status": "UNASSESSED",
        "surprise_status": "UNASSESSED",
        "reaction": {"direction": "UNKNOWN", "confirmed": False, "confirmation_sources": []},
        "headline_sentiment_inferred": False,
        "outcome_used_for_enrichment": False,
    }
    return {
        "series": series,
        "observed_at": available.isoformat(),
        "available_at": available.isoformat(),
        "source": record.get("source") or "UNKNOWN",
        "quality": record.get("timestamp_quality") or "UNKNOWN",
        "event_id": record.get("event_id"),
        "event_type": record.get("event_type"),
        "value": value,
        "metadata": {
            "source_url": record.get("source_url"),
            "archive_pit_usable": True,
            "direction_inferred": False,
            "reaction_backfilled": False,
        },
    }


def load_frozen_event_archive(event_dir: str | Path) -> dict:
    root = Path(event_dir)
    records = []
    part_hashes = {}
    for name in EVENT_PARTS:
        path = root / name
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("records") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise RuntimeError(f"Invalid event archive part: {name}")
        records.extend(rows)
        part_hashes[name] = _file_sha256(path)
    if len(records) != EXPECTED_EVENT_RECORDS:
        raise RuntimeError(f"Frozen August event archive changed: {len(records)} records")
    context = [row for row in (_archive_record_to_context(item) for item in records) if row]
    context.sort(key=lambda row: (parse_ist_timestamp(row["available_at"]), row.get("event_id") or ""))
    if len(context) != EXPECTED_EVENT_RECORDS:
        raise RuntimeError(f"PIT event adapter did not preserve 26 records: {len(context)}")
    return {
        "archive_commit": EVENT_ARCHIVE_COMMIT,
        "record_count": len(records),
        "part_sha256": part_hashes,
        "context_records": context,
        "headline_direction_inferred": False,
        "reaction_backfilled": False,
        "outcome_used_for_enrichment": False,
    }


def build_causal_direction_memory(candles) -> dict:
    """Rebuild the exact causal geometry-free June-Aug memory used in the earlier V2 diagnosis."""
    rows, features = precompute_perception(_bounded_rows(candles))
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
                close = available_to_close.get((click + timedelta(minutes=minutes)).isoformat())
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
        "mode": "CRUDE_OIL_MINI_DIRECTION_MEMORY_CAUSAL_JUNE_AUG_DIAGNOSTIC_V1",
        "cases": cases,
        "case_count": len(cases),
        "sha256": _sha256(cases),
        "geometry_used": False,
        "option_pnl_used": False,
        "future_returns_used_only_for_historically_matured_memory_cases": True,
        "future_returns_used_for_diagnostic_scoring": False,
        "query_filters_case_available_at_strictly_before_click": True,
    }


def context_records_from_probe(probe: dict, click_timestamp: str) -> list[dict]:
    """Old-V2 compatibility adapter: latest completed hourly close-to-close sign."""
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
            if previous_close > 0:
                return_1h = (latest_close / previous_close - 1.0) * 100.0
                if return_1h != 0:
                    stance = "BULLISH" if return_1h > 0 else "BEARISH"
        records.append({
            "series": series,
            "observed_at": latest.get("bar_start") or latest_available.isoformat(),
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


def _expected_current_direction(action: str) -> str:
    return "BULLISH" if action == "BUY_CE" else "BEARISH" if action == "BUY_PE" else "UNKNOWN"


def _overlay_classification(action: str, integrated: dict) -> str:
    expected = _expected_current_direction(action)
    direction = str(integrated.get("direction") or "UNKNOWN").upper()
    confidence = str(integrated.get("direction_confidence") or "UNKNOWN").upper()
    if expected not in DIRECTIONAL:
        return "NOT_A_CURRENT_MIND_TRADE"
    if direction == expected:
        return "CONFIRMS"
    if direction in DIRECTIONAL and direction != expected:
        return "OPPOSES"
    if direction == "UNKNOWN" and confidence == "CONFLICTED":
        return "CONFLICTS"
    return "ABSTAINS"


def _outcome(row: dict) -> str:
    return str((row.get("outcome") or {}).get("result") or "UNKNOWN")


def _realized_r(row: dict) -> float:
    return float((row.get("outcome") or {}).get("realized_r") or 0.0)


def _trade_stats(rows: list[dict]) -> dict:
    trades = [row for row in rows if row["current_action"] in {"BUY_CE", "BUY_PE"}]
    outcomes = Counter(_outcome(row) for row in trades)
    resolved = outcomes.get("TARGET", 0) + outcomes.get("STOP", 0)
    targets = outcomes.get("TARGET", 0)
    return {
        "trades": len(trades),
        "targets": targets,
        "stops": outcomes.get("STOP", 0),
        "no_entry": outcomes.get("NO_ENTRY", 0),
        "session_end": outcomes.get("SESSION_END", 0),
        "resolved": resolved,
        "resolved_win_rate_pct": round(100.0 * targets / resolved, 4) if resolved else None,
        "net_r_existing_current_mind_geometry": round(sum(_realized_r(row) for row in trades), 4),
        "descriptive_only_not_a_candidate_filter": True,
    }


def _family(row: dict, family: str) -> dict:
    return ((row.get("families") or {}).get(family) or {})


def _cohort_summary(rows: list[dict]) -> dict:
    return {
        "clicks": len(rows),
        "old_direction_counts": _counter(row["old"]["direction"] for row in rows),
        "integrated_direction_counts": _counter(row["integrated"]["direction"] for row in rows),
        "old_confidence_counts": _counter(row["old"]["confidence"] for row in rows),
        "integrated_confidence_counts": _counter(row["integrated"]["confidence"] for row in rows),
        "trade_overlay_counts": _counter(
            row["overlay_classification"] for row in rows
            if row["current_action"] in {"BUY_CE", "BUY_PE"}
        ),
        "trade_stats": _trade_stats(rows),
    }


def compare_old_vs_integrated_v2(
    *,
    candles,
    frozen_baseline: dict,
    context_probe: dict,
    event_dir: str | Path,
) -> dict:
    """Run the approved descriptive comparison on the exact frozen 420 August clicks.

    This function never scores new fixed-horizon direction, never changes Current Mind,
    never generates geometry, and never selects a candidate filter or threshold.
    """
    manifest = frozen_baseline.get("manifest") or {}
    monthly = frozen_baseline.get("monthly") or {}
    decisions = frozen_baseline.get("decisions") or []
    if manifest.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256:
        raise RuntimeError("Frozen PR #318 manifest identity changed")
    if len(decisions) != EXPECTED_CLICKS or int(monthly.get("clicks") or 0) != EXPECTED_CLICKS:
        raise RuntimeError("Frozen PR #318 click count changed")
    if float(monthly.get("net_r")) != EXPECTED_BASELINE_NET_R:
        raise RuntimeError("Frozen PR #318 Current Mind net R changed")
    if sum(1 for row in decisions if row.get("action") in {"BUY_CE", "BUY_PE"}) != EXPECTED_TRADES:
        raise RuntimeError("Frozen Current Mind trade count changed")
    if sum(1 for row in decisions if row.get("action") == "BUY_PE") != EXPECTED_BUY_PE:
        raise RuntimeError("Frozen Current Mind BUY_PE count changed")

    required_context = ("WTI_CRUDE", "BRENT_CRUDE", "USDINR")
    missing = [
        series for series in required_context
        if ((context_probe.get("feeds") or {}).get(series) or {}).get("status") != "AVAILABLE"
    ]
    if missing:
        raise RuntimeError(f"Required PIT context unavailable: {missing}")

    rows, features = precompute_perception(_bounded_rows(candles))
    profiles = causal_profiles(rows, features)
    memory = build_causal_direction_memory(candles)
    archive = load_frozen_event_archive(event_dir)
    event_records = archive["context_records"]

    baseline_clicks = [parse_ist_timestamp(row["click_timestamp"]).isoformat() for row in decisions]
    manifest_clicks = [parse_ist_timestamp(item["click_timestamp"]).isoformat() for item in manifest.get("clicks") or []]
    if manifest_clicks != baseline_clicks:
        raise RuntimeError("Frozen manifest and decision order differ")
    by_click = {parse_ist_timestamp(row["click_timestamp"]).isoformat(): row for row in decisions}

    enriched = []
    for key in manifest_clicks:
        click = parse_ist_timestamp(key)
        current = by_click[key]
        visible_index = latest_visible_index(rows, click)
        if visible_index is None:
            raise RuntimeError(f"No visible Mini state at {key}")
        snapshot = features[visible_index]
        profile = profiles.get(click.date().isoformat()) or {}
        old_context = context_records_from_probe(context_probe, key)

        old = evaluate_direction_brain_v2_shadow(
            click_timestamp=key,
            snapshot=snapshot,
            profile=profile,
            context_records=old_context,
            direction_memory_cases=memory["cases"],
        )
        integrated = evaluate_integrated_direction_v2_shadow(
            click_timestamp=key,
            snapshot=snapshot,
            profile=profile,
            mini_candles=candles,
            global_context_probe=context_probe,
            context_records=old_context,
            event_records=event_records,
            direction_memory_cases=memory["cases"],
        )

        old_global = _family(old, "GLOBAL_CRUDE")
        new_global = _family(integrated, "GLOBAL_CRUDE")
        participation = _family(integrated, "PARTICIPATION")
        event = _family(integrated, "EVENT_REACTION")
        lifecycle = (event.get("detail") or {}).get("lifecycle") or {}
        enriched.append({
            "session": current.get("session"),
            "click_timestamp": key,
            "current_action": current.get("action"),
            "current_direction": current.get("direction"),
            "current_playbook": current.get("playbook"),
            "outcome": current.get("outcome") or {},
            "old": {
                "direction": old.get("direction"),
                "confidence": old.get("direction_confidence"),
                "thesis_state": old.get("thesis_state"),
                "supporting_families": old.get("supporting_families") or [],
                "global_crude": old_global,
                "participation": _family(old, "PARTICIPATION"),
            },
            "integrated": {
                "direction": integrated.get("direction"),
                "confidence": integrated.get("direction_confidence"),
                "thesis_state": integrated.get("thesis_state"),
                "supporting_families": integrated.get("supporting_families") or [],
                "opposing_families": integrated.get("opposing_families") or [],
                "global_crude": new_global,
                "participation": participation,
                "event_reaction": event,
                "dependency_suppressed": (integrated.get("dependency_audit") or {}).get("suppressed") or [],
            },
            "overlay_classification": _overlay_classification(str(current.get("action") or ""), integrated),
            "event_lifecycle": {
                "visible_event_count": int(lifecycle.get("visible_event_count") or 0),
                "active_context_count": int(lifecycle.get("active_context_count") or 0),
                "direction_eligible_count": int(lifecycle.get("direction_eligible_count") or 0),
                "states": _counter(str(item.get("state") or "UNKNOWN") for item in lifecycle.get("events") or []),
            },
        })

    old_direction_counts = _counter(row["old"]["direction"] for row in enriched)
    old_confidence_counts = _counter(row["old"]["confidence"] for row in enriched)
    if old_direction_counts != EXPECTED_OLD_DIRECTIONS:
        raise RuntimeError(f"Old V2 direction identity changed: {old_direction_counts}")
    if old_confidence_counts != EXPECTED_OLD_CONFIDENCE:
        raise RuntimeError(f"Old V2 confidence identity changed: {old_confidence_counts}")

    integrated_direction_counts = _counter(row["integrated"]["direction"] for row in enriched)
    integrated_confidence_counts = _counter(row["integrated"]["confidence"] for row in enriched)
    direction_transitions = _counter(f"{row['old']['direction']}->{row['integrated']['direction']}" for row in enriched)
    confidence_transitions = _counter(f"{row['old']['confidence']}->{row['integrated']['confidence']}" for row in enriched)

    # Global Crude redesign diagnostics.
    global_stance_transitions = _counter(
        f"{row['old']['global_crude'].get('stance', 'UNKNOWN')}->{row['integrated']['global_crude'].get('stance', 'UNKNOWN')}"
        for row in enriched
    )
    global_state_transitions = _counter(
        f"{row['old']['global_crude'].get('state', 'UNKNOWN')}->{row['integrated']['global_crude'].get('state', 'UNKNOWN')}"
        for row in enriched
    )
    benchmark_states = {}
    for series in ("WTI_CRUDE", "BRENT_CRUDE"):
        benchmark_states[series] = {
            "stance_counts": _counter(
                str(((row['integrated']['global_crude'].get('benchmarks') or {}).get(series) or {}).get('stance') or 'UNKNOWN')
                for row in enriched
            ),
            "state_counts": _counter(
                str(((row['integrated']['global_crude'].get('benchmarks') or {}).get(series) or {}).get('state') or 'UNKNOWN')
                for row in enriched
            ),
            "structure_counts": _counter(
                str(((row['integrated']['global_crude'].get('benchmarks') or {}).get(series) or {}).get('structure') or 'UNKNOWN')
                for row in enriched
            ),
        }

    # Participation integrity.
    participation_states = _counter(row["integrated"]["participation"].get("state") for row in enriched)
    participation_votes = sum(bool(row["integrated"]["participation"].get("counts_for_direction")) for row in enriched)
    missing_oi_delta = sum((row["integrated"]["participation"].get("detail") or {}).get("oi_delta") is None for row in enriched)
    old_participation_votes = sum(bool(row["old"]["participation"].get("counts_for_direction")) for row in enriched)

    # Event lifecycle / reaction diagnostics.
    event_family_states = _counter(row["integrated"]["event_reaction"].get("state") for row in enriched)
    lifecycle_state_occurrences = Counter()
    for row in enriched:
        lifecycle_state_occurrences.update(row["event_lifecycle"]["states"])
    clicks_with_visible_events = sum(row["event_lifecycle"]["visible_event_count"] > 0 for row in enriched)
    clicks_with_active_events = sum(row["event_lifecycle"]["active_context_count"] > 0 for row in enriched)
    clicks_with_direction_eligible_events = sum(row["event_lifecycle"]["direction_eligible_count"] > 0 for row in enriched)

    # Evidence dependency suppression.
    suppressed = [item for row in enriched for item in row["integrated"]["dependency_suppressed"]]
    suppression_reasons = _counter(str(item.get("reason") or "UNKNOWN") for item in suppressed)
    suppression_families = _counter(str(item.get("family") or "UNKNOWN") for item in suppressed)

    # Existing Current Mind trades, including the 38 PE cases.
    trades = [row for row in enriched if row["current_action"] in {"BUY_CE", "BUY_PE"}]
    overlay = {}
    for label in ("CONFIRMS", "ABSTAINS", "CONFLICTS", "OPPOSES"):
        subset = [row for row in trades if row["overlay_classification"] == label]
        overlay[label] = _trade_stats(subset)
    pe = [row for row in trades if row["current_action"] == "BUY_PE"]
    pe_old_bearish = [row for row in pe if row["old"]["direction"] == "BEARISH"]

    # Previously problematic PE cases where old V2 had Local + Global support.
    old_local_global_bearish_pe = [
        row for row in pe
        if row["old"]["direction"] == "BEARISH"
        and "LOCAL_STRUCTURE" in row["old"]["supporting_families"]
        and "GLOBAL_CRUDE" in row["old"]["supporting_families"]
    ]

    # Day cohorts are descriptive only and derived solely from the frozen Current Mind outcomes.
    day_net_r = defaultdict(float)
    for row in enriched:
        day_net_r[str(row["session"])] += _realized_r(row)
    positive_days = {day for day, net in day_net_r.items() if net > 0}
    negative_days = {day for day, net in day_net_r.items() if net < 0}
    flat_days = {day for day, net in day_net_r.items() if net == 0}
    positive_rows = [row for row in enriched if str(row["session"]) in positive_days]
    negative_rows = [row for row in enriched if str(row["session"]) in negative_days]

    return {
        "mode": MODE,
        "research_only": True,
        "diagnostic_only": True,
        "manifest_sha256": manifest.get("manifest_sha256"),
        "baseline": {
            "clicks": len(enriched),
            "trades": len(trades),
            "buy_pe": len(pe),
            "net_r": float(monthly.get("net_r")),
            "current_mind_decisions_recomputed": False,
            "current_mind_outcomes_read_only": True,
        },
        "old_v2": {
            "direction_counts": old_direction_counts,
            "confidence_counts": old_confidence_counts,
            "participation_directional_votes": old_participation_votes,
        },
        "integrated_v2": {
            "direction_counts": integrated_direction_counts,
            "confidence_counts": integrated_confidence_counts,
            "direction_transitions_from_old": direction_transitions,
            "confidence_transitions_from_old": confidence_transitions,
        },
        "global_crude_redesign": {
            "stance_transitions_old_to_integrated": global_stance_transitions,
            "state_transitions_old_to_integrated": global_state_transitions,
            "benchmark_diagnostics": benchmark_states,
            "old_directional_to_integrated_unknown": sum(
                row["old"]["global_crude"].get("stance") in DIRECTIONAL
                and row["integrated"]["global_crude"].get("stance") == "UNKNOWN"
                for row in enriched
            ),
            "old_and_integrated_same_directional_stance": sum(
                row["old"]["global_crude"].get("stance") in DIRECTIONAL
                and row["old"]["global_crude"].get("stance") == row["integrated"]["global_crude"].get("stance")
                for row in enriched
            ),
        },
        "participation_integrity": {
            "old_directional_votes": old_participation_votes,
            "integrated_directional_votes": participation_votes,
            "state_counts": participation_states,
            "clicks_missing_oi_delta": missing_oi_delta,
            "retroactive_oi_reconstruction_used": False,
        },
        "event_lifecycle": {
            "archive_commit": archive["archive_commit"],
            "archive_record_count": archive["record_count"],
            "archive_part_sha256": archive["part_sha256"],
            "event_family_state_counts": event_family_states,
            "lifecycle_state_occurrences_across_clicks": dict(sorted(lifecycle_state_occurrences.items())),
            "clicks_with_visible_events": clicks_with_visible_events,
            "clicks_with_active_events": clicks_with_active_events,
            "clicks_with_direction_eligible_events": clicks_with_direction_eligible_events,
            "headline_direction_inferred": False,
            "reaction_backfilled": False,
            "outcome_used_for_enrichment": False,
        },
        "dependency_audit": {
            "suppressed_vote_count": len(suppressed),
            "suppression_reason_counts": suppression_reasons,
            "suppression_family_counts": suppression_families,
        },
        "current_mind_trade_overlay": {
            "classification_counts": _counter(row["overlay_classification"] for row in trades),
            "by_classification": overlay,
            "all_trades": _trade_stats(trades),
        },
        "buy_pe_diagnostic": {
            "all_38": {
                "old_direction_counts": _counter(row["old"]["direction"] for row in pe),
                "integrated_direction_counts": _counter(row["integrated"]["direction"] for row in pe),
                "integrated_confidence_counts": _counter(row["integrated"]["confidence"] for row in pe),
                "overlay_counts": _counter(row["overlay_classification"] for row in pe),
                "trade_stats": _trade_stats(pe),
            },
            "old_v2_bearish_subset": {
                "count": len(pe_old_bearish),
                "integrated_direction_counts": _counter(row["integrated"]["direction"] for row in pe_old_bearish),
                "integrated_confidence_counts": _counter(row["integrated"]["confidence"] for row in pe_old_bearish),
                "overlay_counts": _counter(row["overlay_classification"] for row in pe_old_bearish),
                "trade_stats": _trade_stats(pe_old_bearish),
            },
            "old_local_plus_global_bearish_subset": {
                "count": len(old_local_global_bearish_pe),
                "integrated_direction_counts": _counter(row["integrated"]["direction"] for row in old_local_global_bearish_pe),
                "integrated_global_stance_counts": _counter(row["integrated"]["global_crude"].get("stance") for row in old_local_global_bearish_pe),
                "overlay_counts": _counter(row["overlay_classification"] for row in old_local_global_bearish_pe),
                "trade_stats": _trade_stats(old_local_global_bearish_pe),
            },
        },
        "day_cohorts": {
            "positive_days": sorted(positive_days),
            "negative_days": sorted(negative_days),
            "flat_days": sorted(flat_days),
            "positive": _cohort_summary(positive_rows),
            "negative": _cohort_summary(negative_rows),
            "descriptive_only_no_time_or_day_filter_selected": True,
        },
        "direction_memory": {k: v for k, v in memory.items() if k != "cases"},
        "integrity": {
            "same_frozen_420_click_manifest": True,
            "same_frozen_current_mind": True,
            "same_frozen_trade_outcomes": True,
            "old_v2_identity_asserted": True,
            "threshold_search_performed": False,
            "optimization_performed": False,
            "candidate_filter_selected": False,
            "new_geometry_generated": False,
            "option_pnl_evaluated": False,
            "fixed_horizon_direction_scoring_added": False,
            "retroactive_oi_reconstruction_used": False,
            "headline_sentiment_used": False,
            "event_reaction_backfilled": False,
            "current_mind_mutated": False,
            "click_schedule_changed": False,
            "promotion_allowed": False,
        },
        "enriched_clicks": enriched,
    }
