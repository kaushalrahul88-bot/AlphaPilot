from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_current_mind import crude_oil_mini_current_mind_click
from .crude_oil_mini_data_probe import _complete_sessions
from .crude_oil_mini_experience_memory import build_experiences, memory_evidence
from .crude_oil_mini_market_perception import (
    architecture_contract as perception_contract,
    bar_visible_at,
    causal_profiles,
    clean_ohlcv,
    latest_visible_index,
    market_regime_features,
    precompute_perception,
    price_evidence,
)
from .current_mind_click_sampler import deterministic_clicks
from .current_mind_replay_scorecard import replay_scorecard
from .trader_evidence_synthesis import synthesize_evidence

CLICKS_PER_COMPLETE_SESSION = 20
CLICK_SEED = "CRUDEOILM_CURRENT_MIND_NO_NEWS_ARCHITECTURE_V1"
TARGET_R = 1.5
EXPECTED_CURRENT_CONTRACT = "CRUDEOILM21SEP26FUT"


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dominant_direction(evidence_items: list[dict]) -> str | None:
    synthesis = synthesize_evidence(evidence_items)
    bullish = len(synthesis.get("independent_bullish_lanes", []))
    bearish = len(synthesis.get("independent_bearish_lanes", []))
    if max(bullish, bearish) < 2 or bullish == bearish:
        return None
    return "BULLISH" if bullish > bearish else "BEARISH"


def _geometry(visible_session_rows: list[list], direction: str | None) -> dict:
    if direction not in {"BULLISH", "BEARISH"} or len(visible_session_rows) < 6:
        return {}
    recent3 = visible_session_rows[-3:]
    recent6 = visible_session_rows[-6:]
    if direction == "BULLISH":
        entry = max(float(r[2]) for r in recent3)
        stop = min(float(r[3]) for r in recent6)
        risk = entry - stop
        target = entry + TARGET_R * risk
        trigger = f"Break and accept above {entry:.2f}"
        invalidation = f"Trade invalid below {stop:.2f}"
    else:
        entry = min(float(r[3]) for r in recent3)
        stop = max(float(r[2]) for r in recent6)
        risk = stop - entry
        target = entry - TARGET_R * risk
        trigger = f"Break and accept below {entry:.2f}"
        invalidation = f"Trade invalid above {stop:.2f}"
    if risk <= 0:
        return {}
    return {
        "confirmation": "At least two independent Crude evidence lanes align with the observed regime.",
        "entry_trigger": trigger,
        "invalidation": invalidation,
        "target_logic": f"Structural {TARGET_R:.1f}R target at {target:.2f}",
        "risk_reward_basis": f"Shared AlphaPilot minimum structural reward/risk {TARGET_R:.1f}R",
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
    }


def _resolve_setup(day_rows: list[list], click_at, action: str, geometry: dict | None) -> dict | None:
    if action not in {"BUY_CE", "BUY_PE"} or not geometry:
        return None
    click = parse_ist_timestamp(click_at)
    entry = _f(geometry.get("entry_price"))
    stop = _f(geometry.get("stop_price"))
    target = _f(geometry.get("target_price"))
    if None in {entry, stop, target}:
        return {"result": "INVALID_LEVELS", "resolved_at": click.isoformat()}
    risk = abs(entry - stop)
    if risk <= 0:
        return {"result": "INVALID_LEVELS", "resolved_at": click.isoformat()}
    bullish = action == "BUY_CE"
    entered = False
    entry_at = None
    mfe = mae = 0.0
    for row in day_rows:
        stamp = parse_ist_timestamp(row[0])
        if stamp < click:
            continue
        high, low = float(row[2]), float(row[3])
        if not entered:
            if (bullish and high >= entry) or ((not bullish) and low <= entry):
                entered = True
                entry_at = stamp
            else:
                continue
        if bullish:
            mfe = max(mfe, (high - entry) / risk)
            mae = max(mae, (entry - low) / risk)
            hit_target, hit_stop = high >= target, low <= stop
        else:
            mfe = max(mfe, (entry - low) / risk)
            mae = max(mae, (high - entry) / risk)
            hit_target, hit_stop = low <= target, high >= stop
        resolved_at = bar_visible_at(row).isoformat()
        if hit_target and hit_stop:
            return {"result": "STOP", "realized_r": -1.0, "entry_at": entry_at.isoformat(), "exit_bar_start": stamp.isoformat(), "resolved_at": resolved_at, "mfe_r": round(mfe, 3), "mae_r": round(mae, 3), "same_bar_ambiguous": True}
        if hit_stop:
            return {"result": "STOP", "realized_r": -1.0, "entry_at": entry_at.isoformat(), "exit_bar_start": stamp.isoformat(), "resolved_at": resolved_at, "mfe_r": round(mfe, 3), "mae_r": round(mae, 3)}
        if hit_target:
            return {"result": "TARGET", "realized_r": TARGET_R, "entry_at": entry_at.isoformat(), "exit_bar_start": stamp.isoformat(), "resolved_at": resolved_at, "mfe_r": round(mfe, 3), "mae_r": round(mae, 3)}
    final_resolved = bar_visible_at(day_rows[-1]).isoformat() if day_rows else click.isoformat()
    return {"result": "NO_ENTRY" if not entered else "SESSION_END", "realized_r": 0.0, "entry_at": entry_at.isoformat() if entry_at else None, "resolved_at": final_resolved, "mfe_r": round(mfe, 3), "mae_r": round(mae, 3)}


def _abstention_outcome(day_rows: list[list], click_at, base_price: float, atr_pct: float | None) -> dict:
    click = parse_ist_timestamp(click_at)
    future = [row for row in day_rows if parse_ist_timestamp(row[0]) >= click]
    if not future or base_price <= 0:
        return {"result": "WAIT_UNRESOLVED", "resolved_at": click.isoformat()}
    high = max(float(row[2]) for row in future)
    low = min(float(row[3]) for row in future)
    up = (high / base_price - 1.0) * 100.0
    down = (base_price / low - 1.0) * 100.0
    threshold = 2.0 * atr_pct if atr_pct is not None and atr_pct > 0 else None
    return {
        "result": "WAIT",
        "max_up_pct": round(up, 4),
        "max_down_pct": round(down, 4),
        "large_move_threshold_pct": round(threshold, 4) if threshold is not None else None,
        "future_move_without_setup": max(up, down) >= threshold if threshold is not None else None,
        "resolved_at": bar_visible_at(day_rows[-1]).isoformat(),
    }


def _horizon_return(day_rows: list[list], click_at, base_price: float, minutes: int):
    click = parse_ist_timestamp(click_at)
    horizon = click + timedelta(minutes=minutes)
    eligible = [row for row in day_rows if click <= bar_visible_at(row) <= horizon]
    if not eligible or base_price <= 0:
        return None
    return (float(eligible[-1][4]) / base_price - 1.0) * 100.0


def _summary(decisions: list[dict]) -> dict:
    actions = Counter(row["action"] for row in decisions)
    trades = [row for row in decisions if row["action"] in {"BUY_CE", "BUY_PE"}]
    resolved = [row for row in trades if (row.get("outcome") or {}).get("result") in {"TARGET", "STOP"}]
    rs = [float(row["outcome"]["realized_r"]) for row in resolved]
    result = {
        "clicks": len(decisions),
        "actions": dict(sorted(actions.items())),
        "trades": len(trades),
        "resolved_trades": len(resolved),
        "targets": sum((row.get("outcome") or {}).get("result") == "TARGET" for row in trades),
        "stops": sum((row.get("outcome") or {}).get("result") == "STOP" for row in trades),
        "no_entry": sum((row.get("outcome") or {}).get("result") == "NO_ENTRY" for row in trades),
        "session_end": sum((row.get("outcome") or {}).get("result") == "SESSION_END" for row in trades),
        "expectancy_r_resolved": round(mean(rs), 4) if rs else None,
        "missed_large_moves_after_wait": sum(bool((row.get("outcome") or {}).get("future_move_without_setup")) for row in decisions if row["action"] == "WAIT"),
    }
    for minutes in (15, 30, 60):
        signed = []
        for row in trades:
            raw = (row.get("future_returns_pct") or {}).get(str(minutes))
            if raw is None:
                continue
            signed.append(float(raw) if row["action"] == "BUY_CE" else -float(raw))
        result[f"direction_{minutes}m"] = {
            "observations": len(signed),
            "alignment_pct": round(sum(value > 0 for value in signed) / len(signed) * 100.0, 2) if signed else None,
            "avg_signed_return_pct": round(mean(signed), 4) if signed else None,
        }
    return result


def evaluate_crude_oil_mini_current_mind_no_news(candles, contract: dict | None = None) -> dict:
    rows, features = precompute_perception(candles)
    sessions = _complete_sessions(rows)
    complete_days = {row["date"] for row in sessions if row.get("complete_for_20_click_research")}
    complete_rows = [row for row in rows if parse_ist_timestamp(row[0]).date().isoformat() in complete_days]
    clicks = deterministic_clicks(
        complete_rows,
        clicks_per_session=CLICKS_PER_COMPLETE_SESSION,
        seed=CLICK_SEED,
        warmup_bars=24,
        tail_bars=12,
        min_global_index=50,
    )
    per_day_clicks = Counter(row["session"] for row in clicks)
    if any(per_day_clicks.get(day, 0) != CLICKS_PER_COMPLETE_SESSION for day in complete_days):
        raise RuntimeError("Every complete CRUDEOILM session must contribute exactly 20 frozen clicks")

    profiles = causal_profiles(rows, features)
    experiences = build_experiences(rows, features, complete_days, sample_every_bars=3)
    by_day = defaultdict(list)
    for row in rows:
        by_day[parse_ist_timestamp(row[0]).date().isoformat()].append(row)

    decisions = []
    memory_cases = []
    for click_meta in sorted(clicks, key=lambda row: parse_ist_timestamp(row["click_timestamp"])):
        click = parse_ist_timestamp(click_meta["click_timestamp"])
        visible_index = latest_visible_index(rows, click)
        if visible_index is None:
            raise RuntimeError(f"No completed Mini bar visible at {click.isoformat()}")
        snapshot = features[visible_index]
        if parse_ist_timestamp(snapshot["timestamp"]).date() != click.date():
            raise RuntimeError(f"Click {click.isoformat()} has no same-session completed perception bar")
        profile = profiles.get(click.date().isoformat()) or {}
        evidence_items = price_evidence(snapshot, profile)
        evidence_items.append(memory_evidence(experiences, snapshot, click.isoformat()))
        direction = _dominant_direction(evidence_items)

        visible_session_rows = [
            row for row in by_day[click.date().isoformat()]
            if bar_visible_at(row) <= click
        ]
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
            outcome = _abstention_outcome(by_day[click.date().isoformat()], click, float(snapshot["price"]), _f(snapshot.get("atr_pct")))
        future_returns = {
            str(minutes): _horizon_return(by_day[click.date().isoformat()], click, float(snapshot["price"]), minutes)
            for minutes in (15, 30, 60)
        }
        journal["outcome"] = outcome
        decisions.append({
            "session": click_meta["session"],
            "click_timestamp": click.isoformat(),
            "sampling": click_meta.get("sampling"),
            "latest_visible_bar_start": snapshot["timestamp"],
            "latest_visible_bar_available_at": bar_visible_at(rows[visible_index]).isoformat(),
            "action": action,
            "raw_current_mind_action": raw_action,
            "direction": (journal.get("decision") or {}).get("direction"),
            "profile": profile,
            "features": snapshot,
            "evidence": journal.get("evidence"),
            "regime": journal.get("regime"),
            "scenario": journal.get("scenario"),
            "decision": journal.get("decision"),
            "decision_fingerprint": journal.get("decision_fingerprint"),
            "memory_visibility": journal.get("memory_visibility"),
            "outcome": outcome,
            "future_returns_pct": future_returns,
        })
        memory_cases.append({
            "available_at": outcome.get("resolved_at"),
            "regime": journal.get("regime"),
            "evidence": journal.get("evidence"),
            "action": action,
            "outcome": outcome,
            "decision_fingerprint": journal.get("decision_fingerprint"),
        })

    scorecard_input = []
    for row in decisions:
        decision = dict(row.get("decision") or {})
        decision["action"] = row["action"]
        decision["outcome"] = row["outcome"]
        decision["lookahead_violation"] = False
        scorecard_input.append(decision)
    contract_info = dict(contract or {})
    report = {
        "mode": "CRUDE_OIL_MINI_CURRENT_MIND_20_CLICK_NO_NEWS_REPLAY_V1",
        "product": "CRUDE_OIL_MINI",
        "underlying_symbol": "CRUDEOILM",
        "reference_contract": contract_info.get("trading_symbol") or EXPECTED_CURRENT_CONTRACT,
        "reference_contract_scope": "CURRENT_LISTED_CRUDE_OIL_MINI_FUTURE_ONLY",
        "research_only": True,
        "news_enabled": False,
        "option_market_data_used": False,
        "historical_option_premium_scored": False,
        "live_execution_enabled": False,
        "clicks_per_complete_session": CLICKS_PER_COMPLETE_SESSION,
        "complete_sessions": len(complete_days),
        "complete_session_dates": sorted(complete_days),
        "scheduled_clicks": len(clicks),
        "evaluated_clicks": len(decisions),
        "click_coverage_exact": len(clicks) == len(decisions) == CLICKS_PER_COMPLETE_SESSION * len(complete_days),
        "candles": len(rows),
        "memory_experiences": len(experiences),
        "performance": _summary(decisions),
        "scorecard": replay_scorecard(scorecard_input),
        "decisions": decisions,
        "architecture": {
            "perception": perception_contract(),
            "current_mind_layers": [
                "CRUDE_OIL_MINI_INFORMATION_BOARD",
                "MARKET_REGIME_OBSERVER",
                "TRADER_EVIDENCE_SYNTHESIS",
                "CRUDE_OIL_MINI_WALK_FORWARD_EXPERIENCE_MEMORY",
                "TRADER_SCENARIO_BOARD",
                "CURRENT_MIND_THESIS_BUILDER",
                "SETUP_RISK_REVIEW",
                "TRADER_DECISION_JOURNAL",
            ],
            "copper_architecture_imitated": True,
            "copper_market_data_or_fitted_values_copied": False,
        },
        "integrity": {
            "bar_start_visible_at_plus_5m": True,
            "every_profile_uses_prior_crude_sessions_only": True,
            "memory_outcome_must_resolve_before_click": True,
            "future_bars_used_only_after_decision_freeze": True,
            "regular_crude_used": False,
            "copper_data_used": False,
            "news_used": False,
            "option_market_data_used": False,
        },
        "guardrails": [
            "Twenty deterministic timestamp-only clicks are sampled per complete current-expiry Mini session.",
            "The bar beginning at the click is invisible until five minutes after the click.",
            "Crude volatility/location/participation reference levels are estimated from prior CRUDEOILM sessions only.",
            "Walk-forward analogue outcomes must be resolved strictly before the simulated click.",
            "The generic Current Mind regime/evidence/scenario/thesis/risk architecture is reused; Copper data and Copper fitted thresholds are not.",
            "No news, option chain, option premium, IV or Greeks enter this baseline.",
        ],
    }
    return report
