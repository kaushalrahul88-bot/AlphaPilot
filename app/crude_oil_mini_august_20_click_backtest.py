from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
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
from .current_mind_click_sampler import deterministic_clicks
from .current_mind_crude_oil_mini_replay import (
    CLICK_SEED as PREVIOUS_CLICK_SEED,
    CLICKS_PER_COMPLETE_SESSION,
    EXPECTED_CURRENT_CONTRACT,
    TARGET_R,
    _abstention_outcome,
    _dominant_direction,
    _geometry,
    _resolve_setup,
)

MODE = "CRUDE_OIL_MINI_AUGUST_20_NEW_CLICKS_PER_DAY_V1"
NEW_CLICK_SEED = "CRUDEOILM_AUGUST_NEW_20_PER_DAY_V1"
WINDOW_START = "2026-06-01T00:00:00+05:30"
WINDOW_END = "2026-08-31T23:30:00+05:30"
EVALUATION_MONTH = "2026-08"
WARMUP_BARS = 24
TAIL_BARS = 12
MIN_GLOBAL_INDEX = 50

# These August timestamps were consumed by the corrected one-off unused-click
# diagnostic and therefore must not be re-used in this backtest.
PRIOR_EXTRA_AUGUST_CLICKS = {
    "2026-08-04T15:40:00+05:30",
    "2026-08-07T14:50:00+05:30",
    "2026-08-12T21:10:00+05:30",
    "2026-08-14T14:05:00+05:30",
    "2026-08-14T22:05:00+05:30",
    "2026-08-21T20:45:00+05:30",
    "2026-08-26T15:55:00+05:30",
    "2026-08-26T20:35:00+05:30",
}


def _fingerprint(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _bounded_rows(candles) -> list[list]:
    start = parse_ist_timestamp(WINDOW_START)
    end = parse_ist_timestamp(WINDOW_END)
    return [row for row in clean_ohlcv(candles) if start <= parse_ist_timestamp(row[0]) <= end]


def _eligible_by_day(complete_rows: list[list]) -> dict[str, list[str]]:
    grouped: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for global_index, row in enumerate(complete_rows):
        stamp = str(row[0])
        day = parse_ist_timestamp(stamp).date().isoformat()
        grouped[day].append((stamp, global_index))

    result: dict[str, list[str]] = {}
    for day, items in sorted(grouped.items()):
        items.sort(key=lambda item: parse_ist_timestamp(item[0]))
        eligible = items[WARMUP_BARS : len(items) - TAIL_BARS]
        eligible = [item for item in eligible if item[1] >= MIN_GLOBAL_INDEX]
        result[day] = [parse_ist_timestamp(stamp).isoformat() for stamp, _ in eligible]
    return result


def build_august_click_manifest(candles) -> dict:
    """Freeze 20 outcome-blind, previously-unused clicks for every complete August session."""
    rows = _bounded_rows(candles)
    sessions = _complete_sessions(rows)
    incomplete_august = [
        row for row in sessions
        if str(row.get("date", "")).startswith(EVALUATION_MONTH)
        and not row.get("complete_for_20_click_research")
    ]
    if incomplete_august:
        raise RuntimeError(f"Observed August sessions are incomplete: {incomplete_august}")

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
    previous_set = {parse_ist_timestamp(row["click_timestamp"]).isoformat() for row in previous}
    if len(previous) != 1080:
        raise RuntimeError(f"Expected exact prior 1,080-click identity; got {len(previous)}")

    extra_set = {parse_ist_timestamp(stamp).isoformat() for stamp in PRIOR_EXTRA_AUGUST_CLICKS}
    if previous_set & extra_set:
        raise RuntimeError("Prior extra diagnostic clicks unexpectedly overlap original 1,080-click schedule")

    eligible = _eligible_by_day(complete_rows)
    august_days = sorted(day for day in complete_days if day.startswith(EVALUATION_MONTH))
    if not august_days:
        raise RuntimeError("No complete August CRUDEOILM sessions found")

    selected = []
    per_day_available = {}
    for day in august_days:
        unused = [stamp for stamp in eligible.get(day, []) if stamp not in previous_set and stamp not in extra_set]
        per_day_available[day] = len(unused)
        if len(unused) < CLICKS_PER_COMPLETE_SESSION:
            raise RuntimeError(f"Only {len(unused)} unused eligible clicks remain for {day}")
        day_seed = int(hashlib.sha256(f"{NEW_CLICK_SEED}:{day}".encode()).hexdigest()[:16], 16)
        chosen = sorted(random.Random(day_seed).sample(unused, CLICKS_PER_COMPLETE_SESSION), key=parse_ist_timestamp)
        selected.extend(
            {
                "session": day,
                "click_timestamp": stamp,
                "sampling": "DETERMINISTIC_RANDOM_UNUSED_WITHIN_AUGUST_SESSION",
            }
            for stamp in chosen
        )

    selected_set = {row["click_timestamp"] for row in selected}
    overlap_original = sorted(selected_set & previous_set)
    overlap_extra = sorted(selected_set & extra_set)
    per_day_counts = Counter(row["session"] for row in selected)
    if any(per_day_counts.get(day, 0) != CLICKS_PER_COMPLETE_SESSION for day in august_days):
        raise RuntimeError("Every complete August session must contribute exactly 20 new clicks")

    manifest = {
        "mode": "CRUDE_OIL_MINI_AUGUST_NEW_CLICK_MANIFEST_V1",
        "research_only": True,
        "outcome_blind_selection": True,
        "evaluation_period": {"start": "2026-08-01", "end": "2026-08-31"},
        "history_window_used_for_point_in_time_brain": {"start": WINDOW_START, "end": WINDOW_END},
        "new_seed": NEW_CLICK_SEED,
        "previous_seed": PREVIOUS_CLICK_SEED,
        "complete_sessions_june_august": len(complete_days),
        "previous_original_click_count": len(previous),
        "previous_extra_august_click_count": len(extra_set),
        "august_trading_sessions": len(august_days),
        "august_session_dates": august_days,
        "clicks_per_session": CLICKS_PER_COMPLETE_SESSION,
        "selected_click_count": len(selected),
        "per_day_available_unused_slots": per_day_available,
        "overlap_with_original_1080": len(overlap_original),
        "overlap_with_extra_diagnostic": len(overlap_extra),
        "clicks": selected,
    }
    manifest["manifest_sha256"] = _fingerprint(manifest)
    return manifest


def _summary(decisions: list[dict]) -> dict:
    actions = Counter(row["action"] for row in decisions)
    trades = [row for row in decisions if row["action"] in {"BUY_CE", "BUY_PE"}]
    waits = [row for row in decisions if row["action"] == "WAIT"]
    outcomes = Counter((row.get("outcome") or {}).get("result") for row in trades)
    resolved = [row for row in trades if (row.get("outcome") or {}).get("result") in {"TARGET", "STOP"}]
    entered = [row for row in trades if (row.get("outcome") or {}).get("entry_at")]
    realized_all = [float((row.get("outcome") or {}).get("realized_r") or 0.0) for row in trades]
    realized_resolved = [float(row["outcome"]["realized_r"]) for row in resolved]
    mfe = [float(row["outcome"]["mfe_r"]) for row in entered if row["outcome"].get("mfe_r") is not None]
    mae = [float(row["outcome"]["mae_r"]) for row in entered if row["outcome"].get("mae_r") is not None]
    net_r = sum(realized_all)
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
        "expectancy_r_resolved": round(mean(realized_resolved), 4) if realized_resolved else None,
        "avg_mfe_r_entered": round(mean(mfe), 4) if mfe else None,
        "avg_mae_r_entered": round(mean(mae), 4) if mae else None,
        "missed_large_moves_after_wait": sum(bool((row.get("outcome") or {}).get("future_move_without_setup")) for row in waits),
    }


def evaluate_august_20_new_clicks_per_day(candles, contract: dict | None = None) -> dict:
    """Run the unchanged Current Mind on the frozen August-only new-click manifest.

    No fixed-horizon direction scoring is computed. Trade setups are resolved only
    through the remaining session as TARGET/STOP/NO_ENTRY/SESSION_END. WAIT clicks
    retain only the existing full-session missed-move diagnostic.
    """
    manifest = build_august_click_manifest(candles)
    rows, features = precompute_perception(_bounded_rows(candles))
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
            atr_pct = snapshot.get("atr_pct")
            try:
                atr_pct = float(atr_pct) if atr_pct is not None else None
            except (TypeError, ValueError):
                atr_pct = None
            outcome = _abstention_outcome(
                by_day[click.date().isoformat()], click, float(snapshot["price"]), atr_pct
            )

        decision = journal.get("decision") or {}
        row = {
            "session": click_meta["session"],
            "click_timestamp": click.isoformat(),
            "action": action,
            "raw_current_mind_action": raw_action,
            "direction": decision.get("direction"),
            "playbook": decision.get("playbook"),
            "entry_price": geometry.get("entry_price") if geometry else None,
            "stop_price": geometry.get("stop_price") if geometry else None,
            "target_price": geometry.get("target_price") if geometry else None,
            "risk_reward": TARGET_R if geometry else None,
            "regime_labels": (journal.get("regime") or {}).get("regime_labels", []),
            "decision_fingerprint": journal.get("decision_fingerprint"),
            "outcome": outcome,
        }
        decisions.append(row)
        memory_cases.append({
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
    daily = {day: _summary(by_session[day]) for day in sorted(by_session)}
    contract_info = dict(contract or {})
    return {
        "mode": MODE,
        "product": "CRUDE_OIL_MINI",
        "underlying_symbol": "CRUDEOILM",
        "reference_contract": contract_info.get("trading_symbol") or EXPECTED_CURRENT_CONTRACT,
        "research_only": True,
        "descriptive_only": True,
        "promotion_eligible": False,
        "brain_rules_changed_for_test": False,
        "direction_audit_included": False,
        "news_used": False,
        "historical_option_premium_scoring": False,
        "rupee_pnl_available": False,
        "rupee_pnl_reason": "This baseline scores the frozen underlying setup geometry in R; no synthetic option premium is introduced.",
        "manifest": manifest,
        "daily": daily,
        "monthly": _summary(decisions),
        "decisions": decisions,
        "integrity": {
            "august_only_scored": True,
            "twenty_new_clicks_each_complete_august_session": True,
            "overlap_with_original_1080": manifest["overlap_with_original_1080"],
            "overlap_with_extra_diagnostic": manifest["overlap_with_extra_diagnostic"],
            "selection_outcome_blind": True,
            "same_current_mind_decision_mechanics": True,
            "full_remaining_session_path_used": True,
            "fixed_horizon_direction_scoring_used": False,
            "target_r": TARGET_R,
            "regular_crude_used": False,
            "synthetic_option_prices_used": False,
        },
    }
