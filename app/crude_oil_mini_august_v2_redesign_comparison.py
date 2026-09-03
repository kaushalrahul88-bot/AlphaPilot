from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_data_probe import _complete_sessions
from .crude_oil_mini_direction_brain_v2 import evaluate_direction_brain_v2_shadow
from .crude_oil_mini_direction_brain_v2_legacy_snapshot import evaluate_direction_brain_v2_legacy_snapshot
from .crude_oil_mini_direction_memory import HORIZONS, make_direction_case
from .crude_oil_mini_event_archive import event_context_records
from .crude_oil_mini_market_perception import (
    bar_visible_at,
    causal_profiles,
    clean_ohlcv,
    latest_visible_index,
    precompute_perception,
)
from .crude_oil_mini_participation_v2 import build_participation_observation

IST = ZoneInfo("Asia/Kolkata")
MODE = "CRUDE_OIL_MINI_AUGUST_V2_REDESIGN_FROZEN_COMPARISON_V1"
WINDOW_START = "2026-06-01T00:00:00+05:30"
WINDOW_END = "2026-08-31T23:30:00+05:30"
EXPECTED_MANIFEST_SHA256 = "c1e813f9748b746b7f0e90e0558ed33df1afe1b579dee23e3c3193f46b934349"
EXPECTED_BASELINE_NET_R = -10.0
EXPECTED_CLICKS = 420
EXPECTED_OLD_DIRECTIONS = {"BEARISH": 67, "BULLISH": 122, "UNKNOWN": 231}
EXPECTED_OLD_CONFIDENCE = {"CONFLICTED": 115, "MODERATE": 121, "STRONG": 68, "WEAK": 116}
SEED_STRIDE_BARS = 3
SEED_WARMUP_BARS = 24
SEED_TAIL_BARS = 24
DIRECTIONAL = {"BULLISH", "BEARISH"}
EVENT_PARTS = (
    "research/crude_aug_2026_pit_events_01_07.json",
    "research/crude_aug_2026_pit_events_08_14.json",
    "research/crude_aug_2026_pit_events_15_21.json",
    "research/crude_aug_2026_pit_events_22_30.json",
)


def _sha256(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _bounded_rows(candles) -> list[list]:
    start = parse_ist_timestamp(WINDOW_START)
    end = parse_ist_timestamp(WINDOW_END)
    return [row for row in clean_ohlcv(candles) if start <= parse_ist_timestamp(row[0]) <= end]


def load_event_archive(repo_root: str | Path = ".") -> dict:
    root = Path(repo_root)
    records = []
    part_hashes = {}
    for relative in EVENT_PARTS:
        path = root / relative
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("records") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise RuntimeError(f"Invalid event archive part: {relative}")
        records.extend(rows)
        part_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "archive_id": "CRUDE_AUG_2026_PIT_EVENT_ARCHIVE_V2",
        "records": records,
        "record_count": len(records),
        "part_sha256": part_hashes,
        "research_only": True,
        "reaction_backfilled": False,
    }


def build_causal_direction_memory(candles) -> dict:
    """Exact causal June-Aug geometry-free seed construction used by the prior diagnosis."""
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
            cases.append(
                make_direction_case(
                    snapshot=features[index],
                    click_timestamp=click.isoformat(),
                    available_at=(click + timedelta(minutes=max(HORIZONS))).isoformat(),
                    future_returns_pct=future_returns,
                )
            )
    return {
        "mode": "CRUDE_OIL_MINI_DIRECTION_MEMORY_CAUSAL_JUNE_AUG_DIAGNOSTIC_V1",
        "cases": cases,
        "case_count": len(cases),
        "sha256": _sha256(cases),
        "geometry_used": False,
        "option_pnl_used": False,
        "query_filters_case_available_at_strictly_before_click": True,
    }


def context_records_from_probe(probe: dict, click_timestamp: str) -> list[dict]:
    """Exact causal hourly sign adapter used in the old August V2 diagnosis."""
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


def _counter(rows) -> dict:
    return dict(sorted(Counter(rows).items()))


def _transition(rows: list[dict], old_key: str, new_key: str) -> dict:
    return _counter(f"{row['old'][old_key]}->{row['new'][new_key]}" for row in rows)


def _outcome_counts(rows: list[dict]) -> dict:
    return _counter(str((row.get("outcome") or {}).get("result") or "UNKNOWN") for row in rows)


def _trade_stats(rows: list[dict]) -> dict:
    trades = [row for row in rows if row["current_action"] in {"BUY_CE", "BUY_PE"}]
    outcomes = Counter(str((row.get("outcome") or {}).get("result") or "UNKNOWN") for row in trades)
    net_r = sum(float((row.get("outcome") or {}).get("realized_r") or 0.0) for row in trades)
    return {
        "trades": len(trades),
        "targets": outcomes.get("TARGET", 0),
        "stops": outcomes.get("STOP", 0),
        "no_entry": outcomes.get("NO_ENTRY", 0),
        "session_end": outcomes.get("SESSION_END", 0),
        "net_r_existing_current_mind_geometry": round(net_r, 4),
        "descriptive_only_not_a_candidate_filter": True,
    }


def compare_old_and_redesigned_v2(
    *,
    candles,
    frozen_baseline: dict,
    context_probe: dict,
    repo_root: str | Path = ".",
) -> dict:
    """Describe how the shadow redesign changes reasoning on the exact frozen 420 clicks.

    No future direction label is computed. Existing Current Mind outcomes are carried only
    as frozen descriptive labels; redesigned V2 generates no geometry and no hypothetical P&L.
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

    required_context = ("WTI_CRUDE", "BRENT_CRUDE", "USDINR")
    missing = [series for series in required_context if ((context_probe.get("feeds") or {}).get(series) or {}).get("status") != "AVAILABLE"]
    if missing:
        raise RuntimeError(f"Required discovery context unavailable: {missing}")

    rows, features = precompute_perception(_bounded_rows(candles))
    profiles = causal_profiles(rows, features)
    memory = build_causal_direction_memory(candles)
    archive = load_event_archive(repo_root)
    event_records = event_context_records(archive)
    if archive["record_count"] != 26 or len(event_records) != 26:
        raise RuntimeError("August PIT event archive identity changed")

    by_click = {parse_ist_timestamp(row["click_timestamp"]).isoformat(): row for row in decisions}
    manifest_clicks = [parse_ist_timestamp(item["click_timestamp"]).isoformat() for item in manifest.get("clicks") or []]
    if manifest_clicks != [parse_ist_timestamp(row["click_timestamp"]).isoformat() for row in decisions]:
        raise RuntimeError("Frozen manifest and decision order differ")

    enriched = []
    for key in manifest_clicks:
        click = parse_ist_timestamp(key)
        current = by_click[key]
        visible_index = latest_visible_index(rows, click)
        if visible_index is None:
            raise RuntimeError(f"No visible Mini state at {key}")
        snapshot = features[visible_index]
        profile = profiles.get(click.date().isoformat()) or {}
        cross_market = context_records_from_probe(context_probe, key)

        old = evaluate_direction_brain_v2_legacy_snapshot(
            click_timestamp=key,
            snapshot=snapshot,
            profile=profile,
            context_records=cross_market,
            direction_memory_cases=memory["cases"],
        )
        participation = build_participation_observation(
            candles,
            click_timestamp=key,
            snapshot=snapshot,
            profile=profile,
        )
        new = evaluate_direction_brain_v2_shadow(
            click_timestamp=key,
            snapshot=snapshot,
            profile=profile,
            context_records=cross_market,
            direction_memory_cases=memory["cases"],
            participation_observation=participation,
            event_records=event_records,
        )
        event_detail = ((new.get("families") or {}).get("EVENT_REACTION") or {}).get("detail") or {}
        enriched.append({
            "session": current.get("session"),
            "click_timestamp": key,
            "current_action": current.get("action"),
            "current_direction": current.get("direction"),
            "current_playbook": current.get("playbook"),
            "outcome": current.get("outcome") or {},
            "old": {
                "direction": old["direction"],
                "confidence": old["direction_confidence"],
                "thesis_state": old["thesis_state"],
                "participation": old["families"]["PARTICIPATION"],
                "supporting_families": old["supporting_families"],
            },
            "new": {
                "direction": new["direction"],
                "confidence": new["direction_confidence"],
                "thesis_state": new["thesis_state"],
                "participation": new["families"]["PARTICIPATION"],
                "event_state": new["families"]["EVENT_REACTION"]["state"],
                "visible_event_count": int(event_detail.get("visible_event_count") or 0),
                "supporting_families": new["supporting_families"],
                "dependency_suppressed": (new.get("dependency_audit") or {}).get("suppressed") or [],
            },
        })

    old_directions = _counter(row["old"]["direction"] for row in enriched)
    old_confidence = _counter(row["old"]["confidence"] for row in enriched)
    if old_directions != EXPECTED_OLD_DIRECTIONS:
        raise RuntimeError(f"Legacy V2 direction identity changed: {old_directions}")
    if old_confidence != EXPECTED_OLD_CONFIDENCE:
        raise RuntimeError(f"Legacy V2 confidence identity changed: {old_confidence}")

    new_directions = _counter(row["new"]["direction"] for row in enriched)
    new_confidence = _counter(row["new"]["confidence"] for row in enriched)
    participation_states = _counter(row["new"]["participation"].get("state") for row in enriched)
    legacy_participation_votes = sum(bool(row["old"]["participation"].get("counts_for_direction")) for row in enriched)
    new_participation_votes = sum(bool(row["new"]["participation"].get("counts_for_direction")) for row in enriched)
    initiative_buying = participation_states.get("INITIATIVE_BUYING", 0)
    initiative_selling = participation_states.get("INITIATIVE_SELLING", 0)
    price_dependent = participation_states.get("PRICE_VOLUME_ONLY_DEPENDENT", 0)

    pe = [row for row in enriched if row["current_action"] == "BUY_PE"]
    pe_stops = [row for row in pe if (row.get("outcome") or {}).get("result") == "STOP"]
    pe_targets = [row for row in pe if (row.get("outcome") or {}).get("result") == "TARGET"]
    old_bearish_pe = [row for row in pe if row["old"]["direction"] == "BEARISH"]

    event_state_counts = _counter(row["new"]["event_state"] for row in enriched)
    visible_event_counts = [row["new"]["visible_event_count"] for row in enriched]
    clicks_with_events = sum(count > 0 for count in visible_event_counts)
    dependency_suppressed_rows = [item for row in enriched for item in row["new"]["dependency_suppressed"]]

    by_new_direction = {
        direction: _trade_stats([row for row in enriched if row["new"]["direction"] == direction])
        for direction in ("BULLISH", "BEARISH", "UNKNOWN")
    }

    return {
        "mode": MODE,
        "research_only": True,
        "descriptive_only": True,
        "promotion_allowed": False,
        "threshold_search_performed": False,
        "candidate_filter_selected": False,
        "current_mind_mutated": False,
        "click_schedule_changed": False,
        "future_direction_scoring_used": False,
        "geometry_generated_by_v2": False,
        "option_pnl_used_by_v2": False,
        "event_reaction_backfilled": False,
        "manifest_sha256": manifest["manifest_sha256"],
        "baseline": monthly,
        "direction_memory": {k: v for k, v in memory.items() if k != "cases"},
        "event_archive": {
            "archive_id": archive["archive_id"],
            "record_count": archive["record_count"],
            "reaction_backfilled": False,
            "clicks_with_at_least_one_visible_event": clicks_with_events,
            "coverage_pct": round(clicks_with_events / EXPECTED_CLICKS * 100.0, 2),
            "max_visible_events_at_click": max(visible_event_counts) if visible_event_counts else 0,
            "event_family_states": event_state_counts,
        },
        "old_v2": {
            "source_main_commit": "85d48dcd41ed41a0eddb2a111d08a1b75c963cdc",
            "direction_counts": old_directions,
            "confidence_counts": old_confidence,
            "legacy_participation_directional_votes": legacy_participation_votes,
        },
        "redesigned_v2": {
            "direction_counts": new_directions,
            "confidence_counts": new_confidence,
            "participation_state_counts": participation_states,
            "independent_participation_votes": new_participation_votes,
            "initiative_buying": initiative_buying,
            "initiative_selling": initiative_selling,
            "price_volume_only_dependent": price_dependent,
            "dependency_suppressions": len(dependency_suppressed_rows),
            "dependency_suppression_reasons": _counter(item.get("reason") for item in dependency_suppressed_rows),
        },
        "direction_transition_old_to_new": _transition(enriched, "direction", "direction"),
        "confidence_transition_old_to_new": _transition(enriched, "confidence", "confidence"),
        "buy_pe": {
            "count": len(pe),
            "old_direction_counts": _counter(row["old"]["direction"] for row in pe),
            "new_direction_counts": _counter(row["new"]["direction"] for row in pe),
            "direction_transitions": _transition(pe, "direction", "direction"),
            "old_bearish_count": len(old_bearish_pe),
            "old_bearish_to_new": _counter(row["new"]["direction"] for row in old_bearish_pe),
            "stops": {
                "count": len(pe_stops),
                "old_direction_counts": _counter(row["old"]["direction"] for row in pe_stops),
                "new_direction_counts": _counter(row["new"]["direction"] for row in pe_stops),
                "new_participation_states": _counter(row["new"]["participation"].get("state") for row in pe_stops),
            },
            "targets": {
                "count": len(pe_targets),
                "old_direction_counts": _counter(row["old"]["direction"] for row in pe_targets),
                "new_direction_counts": _counter(row["new"]["direction"] for row in pe_targets),
                "new_participation_states": _counter(row["new"]["participation"].get("state") for row in pe_targets),
            },
        },
        "existing_current_mind_outcomes_grouped_by_redesigned_direction": by_new_direction,
        "interpretation_guardrails": [
            "August was already inspected; this comparison cannot validate or promote the redesign.",
            "Existing trade outcomes are descriptive labels only; redesigned V2 does not generate those entries/stops/targets.",
            "No result from this comparison may be used to tune thresholds or select a retrospective trade filter.",
            "Event records are visible point-in-time facts only; no market reaction is reconstructed from future price action.",
        ],
        "enriched_clicks": enriched,
    }
