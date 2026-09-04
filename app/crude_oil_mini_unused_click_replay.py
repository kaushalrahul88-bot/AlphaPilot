from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_current_mind import crude_oil_mini_current_mind_click
from .crude_oil_mini_data_probe import _complete_sessions
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
from .crude_oil_mini_research_tape import FROZEN_RESEARCH_END, FROZEN_RESEARCH_START
from .current_mind_click_sampler import deterministic_clicks
from .current_mind_crude_oil_mini_replay import (
    CLICK_SEED as PREVIOUS_CLICK_SEED,
    CLICKS_PER_COMPLETE_SESSION,
    EXPECTED_CURRENT_CONTRACT,
    TARGET_R,
    _abstention_outcome,
    _dominant_direction,
    _geometry,
    _horizon_return,
    _resolve_setup,
    _summary,
)

MODE = "CRUDE_OIL_MINI_UNUSED_20_CLICK_DIAGNOSTIC_V1"
NEW_CLICK_SEED = "CRUDEOILM_UNUSED_20_DIAGNOSTIC_V1"
NEW_CLICK_COUNT = 20
EXPECTED_FROZEN_COMPLETE_SESSIONS = 54
EXPECTED_PREVIOUS_CLICKS = 1080
WARMUP_BARS = 24
TAIL_BARS = 12
MIN_GLOBAL_INDEX = 50


def _fingerprint(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _frozen_rows(candles) -> list[list]:
    start = parse_ist_timestamp(FROZEN_RESEARCH_START)
    end = parse_ist_timestamp(FROZEN_RESEARCH_END)
    return [
        row for row in clean_ohlcv(candles)
        if start <= parse_ist_timestamp(row[0]) <= end
    ]


def _eligible_click_pool(complete_rows: list[list]) -> list[dict]:
    by_day: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for global_index, row in enumerate(complete_rows):
        stamp = str(row[0])
        day = parse_ist_timestamp(stamp).date().isoformat()
        by_day[day].append((stamp, global_index))

    pool = []
    for day, items in sorted(by_day.items()):
        items.sort(key=lambda item: parse_ist_timestamp(item[0]))
        eligible = items[WARMUP_BARS : len(items) - TAIL_BARS]
        eligible = [item for item in eligible if item[1] >= MIN_GLOBAL_INDEX]
        pool.extend({"session": day, "click_timestamp": stamp} for stamp, _ in eligible)
    return pool


def build_unused_click_manifest(candles) -> dict:
    """Freeze 20 timestamp-only clicks after excluding the exact prior 1,080 schedule."""
    rows = _frozen_rows(candles)
    sessions = _complete_sessions(rows)
    complete_days = {row["date"] for row in sessions if row.get("complete_for_20_click_research")}
    complete_rows = [row for row in rows if parse_ist_timestamp(row[0]).date().isoformat() in complete_days]

    previous = deterministic_clicks(
        complete_rows,
        clicks_per_session=CLICKS_PER_COMPLETE_SESSION,
        seed=PREVIOUS_CLICK_SEED,
        warmup_bars=WARMUP_BARS,
        tail_bars=TAIL_BARS,
        min_global_index=MIN_GLOBAL_INDEX,
    )
    if len(complete_days) != EXPECTED_FROZEN_COMPLETE_SESSIONS or len(previous) != EXPECTED_PREVIOUS_CLICKS:
        raise RuntimeError(
            f"Frozen replay identity mismatch: sessions={len(complete_days)}, previous_clicks={len(previous)}"
        )
    previous_set = {parse_ist_timestamp(row["click_timestamp"]).isoformat() for row in previous}

    unused = [
        row for row in _eligible_click_pool(complete_rows)
        if parse_ist_timestamp(row["click_timestamp"]).isoformat() not in previous_set
    ]
    if len(unused) < NEW_CLICK_COUNT:
        raise RuntimeError(f"Only {len(unused)} unused eligible Crude Oil Mini click slots remain")

    seed = int(hashlib.sha256(NEW_CLICK_SEED.encode("utf-8")).hexdigest()[:16], 16)
    selected = sorted(
        random.Random(seed).sample(unused, NEW_CLICK_COUNT),
        key=lambda row: parse_ist_timestamp(row["click_timestamp"]),
    )
    selected = [{
        "session": row["session"],
        "click_timestamp": parse_ist_timestamp(row["click_timestamp"]).isoformat(),
        "sampling": "DETERMINISTIC_RANDOM_FROM_UNUSED_FROZEN_JUNE_AUG_POOL",
    } for row in selected]
    overlap = sorted({row["click_timestamp"] for row in selected} & previous_set)
    manifest = {
        "mode": "CRUDE_OIL_MINI_UNUSED_CLICK_MANIFEST_V1",
        "research_only": True,
        "outcome_blind_selection": True,
        "frozen_window": {
            "start": parse_ist_timestamp(FROZEN_RESEARCH_START).isoformat(),
            "end": parse_ist_timestamp(FROZEN_RESEARCH_END).isoformat(),
        },
        "new_seed": NEW_CLICK_SEED,
        "previous_seed": PREVIOUS_CLICK_SEED,
        "complete_sessions": len(complete_days),
        "previous_click_count": len(previous),
        "eligible_unused_pool": len(unused),
        "selected_click_count": len(selected),
        "overlap_with_previous_clicks": len(overlap),
        "overlap_timestamps": overlap,
        "clicks": selected,
    }
    manifest["manifest_sha256"] = _fingerprint(manifest)
    return manifest


def _direction_120m(decisions: list[dict]) -> dict:
    signed = []
    for row in decisions:
        if row.get("action") not in {"BUY_CE", "BUY_PE"}:
            continue
        raw = (row.get("future_returns_pct") or {}).get("120")
        if raw is None:
            continue
        signed.append(float(raw) if row["action"] == "BUY_CE" else -float(raw))
    return {
        "observations": len(signed),
        "alignment_pct": round(sum(value > 0 for value in signed) / len(signed) * 100.0, 2) if signed else None,
        "avg_signed_return_pct": round(mean(signed), 4) if signed else None,
    }


def evaluate_unused_20_clicks(candles, contract: dict | None = None) -> dict:
    manifest = build_unused_click_manifest(candles)
    if manifest["overlap_with_previous_clicks"] != 0 or manifest["selected_click_count"] != NEW_CLICK_COUNT:
        raise RuntimeError("Unused-click manifest integrity failed")

    frozen = _frozen_rows(candles)
    rows, features = precompute_perception(frozen)
    sessions = _complete_sessions(rows)
    complete_days = {row["date"] for row in sessions if row.get("complete_for_20_click_research")}
    profiles = causal_profiles(rows, features)
    experiences = build_experiences(rows, features, complete_days, sample_every_bars=3)
    by_day: dict[str, list[list]] = defaultdict(list)
    for row in rows:
        by_day[parse_ist_timestamp(row[0]).date().isoformat()].append(row)

    decisions = []
    memory_cases = []
    for click_meta in manifest["clicks"]:
        click = parse_ist_timestamp(click_meta["click_timestamp"])
        visible_index = latest_visible_index(rows, click)
        if visible_index is None:
            raise RuntimeError(f"No completed CRUDEOILM bar visible at {click.isoformat()}")
        snapshot = features[visible_index]
        if parse_ist_timestamp(snapshot["timestamp"]).date() != click.date():
            raise RuntimeError(f"Click {click.isoformat()} has no same-session completed perception bar")

        profile = profiles.get(click.date().isoformat()) or {}
        evidence_items = price_evidence(snapshot, profile)
        evidence_items.append(memory_evidence(experiences, snapshot, click.isoformat()))
        direction = _dominant_direction(evidence_items)
        visible_session_rows = [row for row in by_day[click.date().isoformat()] if bar_visible_at(row) <= click]
        market = market_regime_features(snapshot, profile)
        market.update(_geometry(visible_session_rows, direction) if direction else {})

        context_records = [{
            "series": "MCX_CRUDEOILM",
            "observed_at": snapshot["timestamp"],
            "available_at": bar_visible_at(rows[visible_index]).isoformat(),
            "source": (contract or {}).get("trading_symbol") or EXPECTED_CURRENT_CONTRACT,
            "value": {"price": snapshot.get("price")},
            "quality": "OBSERVED",
        }]
        journal = crude_oil_mini_current_mind_click(
            click_timestamp=click.isoformat(),
            context_records=context_records,
            market_features=market,
            evidence_items=evidence_items,
            memory_cases=memory_cases,
        )
        raw_action = str((journal.get("decision") or {}).get("action") or "NO_TRADE")
        action = raw_action if raw_action in {"BUY_CE", "BUY_PE"} else "WAIT"
        geometry = {
            "entry_price": market.get("entry_price"),
            "stop_price": market.get("stop_price"),
            "target_price": market.get("target_price"),
        } if action in {"BUY_CE", "BUY_PE"} else None
        outcome = _resolve_setup(by_day[click.date().isoformat()], click, action, geometry)
        if outcome is None:
            outcome = _abstention_outcome(
                by_day[click.date().isoformat()], click, float(snapshot["price"]),
                float(snapshot["atr_pct"]) if snapshot.get("atr_pct") is not None else None,
            )
        future_returns = {
            str(minutes): _horizon_return(
                by_day[click.date().isoformat()], click, float(snapshot["price"]), minutes
            ) for minutes in (15, 30, 60, 120)
        }
        decision = {
            "session": click_meta["session"],
            "click_timestamp": click.isoformat(),
            "action": action,
            "direction": (journal.get("decision") or {}).get("direction"),
            "confidence": (journal.get("decision") or {}).get("confidence"),
            "playbook": (journal.get("decision") or {}).get("playbook"),
            "entry_price": geometry.get("entry_price") if geometry else None,
            "stop_price": geometry.get("stop_price") if geometry else None,
            "target_price": geometry.get("target_price") if geometry else None,
            "risk_reward": TARGET_R if geometry else None,
            "regime": journal.get("regime"),
            "outcome": outcome,
            "future_returns_pct": future_returns,
            "decision_fingerprint": journal.get("decision_fingerprint"),
        }
        decisions.append(decision)
        memory_cases.append({
            "available_at": outcome.get("resolved_at"),
            "regime": journal.get("regime"),
            "evidence": journal.get("evidence"),
            "action": action,
            "outcome": outcome,
            "decision_fingerprint": journal.get("decision_fingerprint"),
        })

    summary = _summary(decisions)
    summary["direction_120m"] = _direction_120m(decisions)
    contract_info = dict(contract or {})
    return {
        "mode": MODE,
        "research_only": True,
        "descriptive_only": True,
        "promotion_eligible": False,
        "promotion_reason": "June-August 2026 tape has already been inspected; these are unused timestamps, not untouched out-of-sample data.",
        "brain_rules_changed_for_test": False,
        "news_used": False,
        "historical_option_premium_scoring": False,
        "reference_contract": contract_info.get("trading_symbol") or EXPECTED_CURRENT_CONTRACT,
        "manifest": manifest,
        "summary": summary,
        "decisions": decisions,
        "integrity": {
            "new_clicks": NEW_CLICK_COUNT,
            "frozen_june_august_window_only": True,
            "previous_click_count": manifest["previous_click_count"],
            "overlap_with_previous_clicks": manifest["overlap_with_previous_clicks"],
            "selection_outcome_blind": True,
            "same_current_mind_decision_mechanics": True,
            "full_remaining_session_path_used": True,
            "target_r": TARGET_R,
            "regular_crude_used": False,
            "synthetic_option_prices_used": False,
        },
    }
