from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from .commodity_time import parse_ist_timestamp
from .crude_oil_mini_august_20_click_backtest import (
    _bounded_rows,
    evaluate_august_20_new_clicks_per_day,
)
from .crude_oil_mini_data_probe import _complete_sessions
from .crude_oil_mini_direction_brain_v2 import evaluate_direction_brain_v2_shadow
from .crude_oil_mini_direction_memory import HORIZONS, make_direction_case
from .crude_oil_mini_market_perception import (
    bar_visible_at,
    causal_profiles,
    latest_visible_index,
    precompute_perception,
)

IST = ZoneInfo("Asia/Kolkata")
MODE = "CRUDE_OIL_MINI_AUGUST_DIRECTION_V2_FROZEN_DIAGNOSIS_V1"
SEED_STRIDE_BARS = 3
SEED_WARMUP_BARS = 24
SEED_TAIL_BARS = 24
DIRECTIONAL = {"BULLISH", "BEARISH"}


def _sha256(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_causal_direction_memory(candles) -> dict:
    """Build geometry-free June-Aug cases; query-time availability enforces PIT use."""
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
        "stride_bars": SEED_STRIDE_BARS,
        "warmup_bars": SEED_WARMUP_BARS,
        "tail_bars": SEED_TAIL_BARS,
        "geometry_used": False,
        "option_pnl_used": False,
        "query_filters_case_available_at_strictly_before_click": True,
    }


def context_records_from_probe(probe: dict, click_timestamp: str) -> list[dict]:
    """Use latest completed 1h close-to-close sign as a causal discovery context adapter."""
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


def _trade_stats(rows: list[dict]) -> dict:
    trades = [row for row in rows if row["current_action"] in {"BUY_CE", "BUY_PE"}]
    outcomes = Counter((row.get("outcome") or {}).get("result") for row in trades)
    resolved = [row for row in trades if (row.get("outcome") or {}).get("result") in {"TARGET", "STOP"}]
    net_r = sum(float((row.get("outcome") or {}).get("realized_r") or 0.0) for row in trades)
    return {
        "trades": len(trades),
        "targets": outcomes.get("TARGET", 0),
        "stops": outcomes.get("STOP", 0),
        "no_entry": outcomes.get("NO_ENTRY", 0),
        "session_end": outcomes.get("SESSION_END", 0),
        "resolved": len(resolved),
        "resolved_win_rate_pct": round(outcomes.get("TARGET", 0) / len(resolved) * 100.0, 2) if resolved else None,
        "net_r": round(net_r, 4),
        "expectancy_r_per_trade": round(net_r / len(trades), 4) if trades else None,
    }


def _family_stance_summary(rows: list[dict]) -> dict:
    out = {}
    for family in ("LOCAL_STRUCTURE", "PARTICIPATION", "GLOBAL_CRUDE", "EVENT_REACTION", "DIRECTION_MEMORY"):
        counts = Counter(
            str((((row.get("v2") or {}).get("families") or {}).get(family) or {}).get("stance") or "UNKNOWN")
            for row in rows
        )
        out[family] = dict(sorted(counts.items()))
    fx = Counter(
        str(((((row.get("v2") or {}).get("modifiers") or {}).get("FX_TRANSLATION") or {}).get("state") or "UNRESOLVED"))
        for row in rows
    )
    out["FX_TRANSLATION_STATE"] = dict(sorted(fx.items()))
    return out


def diagnose_direction_v2_on_frozen_august(candles, contract: dict | None, context_probe: dict) -> dict:
    """Compare unchanged shadow V2 with Current Mind on the exact frozen 420-click manifest.

    This is retrospective diagnosis only. It does not score fixed future direction horizons,
    select a candidate filter, tune V2, mutate Current Mind, or promote anything.
    """
    baseline = evaluate_august_20_new_clicks_per_day(candles, contract or {})
    if baseline["monthly"]["clicks"] != 420 or float(baseline["monthly"]["net_r"]) != -10.0:
        raise RuntimeError("Frozen August baseline identity changed")

    required_context = ("WTI_CRUDE", "BRENT_CRUDE", "USDINR")
    missing = [
        series for series in required_context
        if ((context_probe.get("feeds") or {}).get(series) or {}).get("status") != "AVAILABLE"
    ]
    if missing:
        raise RuntimeError(f"Required discovery context unavailable: {missing}")

    rows, features = precompute_perception(_bounded_rows(candles))
    profiles = causal_profiles(rows, features)
    memory = build_causal_direction_memory(candles)
    baseline_by_click = {row["click_timestamp"]: row for row in baseline["decisions"]}

    enriched = []
    context_series_counts = Counter()
    for click_meta in baseline["manifest"]["clicks"]:
        click = parse_ist_timestamp(click_meta["click_timestamp"])
        key = click.isoformat()
        current = baseline_by_click[key]
        visible_index = latest_visible_index(rows, click)
        if visible_index is None:
            raise RuntimeError(f"No visible CRUDEOILM state at {key}")
        snapshot = features[visible_index]
        profile = profiles.get(click.date().isoformat()) or {}
        context_records = context_records_from_probe(context_probe, key)
        for record in context_records:
            context_series_counts[record["series"]] += 1
        v2 = evaluate_direction_brain_v2_shadow(
            click_timestamp=key,
            snapshot=snapshot,
            profile=profile,
            context_records=context_records,
            direction_memory_cases=memory["cases"],
        )
        current_action = current["action"]
        action_direction = "BULLISH" if current_action == "BUY_CE" else "BEARISH" if current_action == "BUY_PE" else None
        v2_direction = v2["direction"]
        if action_direction is None:
            alignment = "CURRENT_WAIT"
        elif v2_direction == action_direction:
            alignment = "V2_CONFIRMS_CURRENT_ACTION"
        elif v2_direction in DIRECTIONAL:
            alignment = "V2_OPPOSES_CURRENT_ACTION"
        else:
            alignment = "V2_ABSTAINS_ON_CURRENT_ACTION"
        enriched.append({
            "session": current["session"],
            "click_timestamp": key,
            "current_action": current_action,
            "current_direction": current.get("direction"),
            "current_playbook": current.get("playbook"),
            "current_regime_labels": current.get("regime_labels") or [],
            "outcome": current.get("outcome") or {},
            "v2_alignment": alignment,
            "v2": {
                "direction": v2["direction"],
                "direction_confidence": v2["direction_confidence"],
                "thesis_state": v2["thesis_state"],
                "supporting_families": v2["supporting_families"],
                "opposing_families": v2["opposing_families"],
                "families": v2["families"],
                "modifiers": v2["modifiers"],
                "persistence": v2["persistence"],
                "available_context_series": v2["available_context_series"],
            },
        })

    if [row["click_timestamp"] for row in enriched] != [row["click_timestamp"] for row in baseline["decisions"]]:
        raise RuntimeError("Direction V2 diagnosis is not aligned to the frozen 420-click order")

    v2_direction_counts = Counter((row["v2"]["direction"] for row in enriched))
    v2_confidence_counts = Counter((row["v2"]["direction_confidence"] for row in enriched))
    trades = [row for row in enriched if row["current_action"] in {"BUY_CE", "BUY_PE"}]
    waits = [row for row in enriched if row["current_action"] == "WAIT"]

    alignment = {}
    for state in ("V2_CONFIRMS_CURRENT_ACTION", "V2_ABSTAINS_ON_CURRENT_ACTION", "V2_OPPOSES_CURRENT_ACTION"):
        alignment[state] = _trade_stats([row for row in trades if row["v2_alignment"] == state])

    by_current_action_and_v2 = {}
    for action in ("BUY_CE", "BUY_PE"):
        subset = [row for row in trades if row["current_action"] == action]
        for direction in ("BULLISH", "BEARISH", "UNKNOWN"):
            by_current_action_and_v2[f"{action}|V2_{direction}"] = _trade_stats(
                [row for row in subset if row["v2"]["direction"] == direction]
            )

    pe_stops = [row for row in trades if row["current_action"] == "BUY_PE" and row["outcome"].get("result") == "STOP"]
    pe_targets = [row for row in trades if row["current_action"] == "BUY_PE" and row["outcome"].get("result") == "TARGET"]
    ce_stops = [row for row in trades if row["current_action"] == "BUY_CE" and row["outcome"].get("result") == "STOP"]
    ce_targets = [row for row in trades if row["current_action"] == "BUY_CE" and row["outcome"].get("result") == "TARGET"]

    wait_directional = [row for row in waits if row["v2"]["direction"] in DIRECTIONAL]
    wait_direction_counts = Counter(row["v2"]["direction"] for row in wait_directional)
    wait_confidence_counts = Counter(row["v2"]["direction_confidence"] for row in waits)

    context_coverage = {
        series: {
            "clicks_with_visible_record": context_series_counts.get(series, 0),
            "coverage_pct": round(context_series_counts.get(series, 0) / 420 * 100.0, 2),
        }
        for series in ("WTI_CRUDE", "BRENT_CRUDE", "USDINR", "DXY")
    }

    return {
        "mode": MODE,
        "research_only": True,
        "descriptive_only": True,
        "promotion_allowed": False,
        "brain_rules_changed": False,
        "direction_v2_rules_changed": False,
        "click_schedule_changed": False,
        "fixed_horizon_direction_audit_added": False,
        "threshold_search_performed": False,
        "candidate_filter_selected": False,
        "manifest_sha256": baseline["manifest"]["manifest_sha256"],
        "baseline": baseline["monthly"],
        "direction_memory": {k: v for k, v in memory.items() if k != "cases"},
        "context": {
            "source_grade": "E_DISCOVERY",
            "adapter": "LATEST_COMPLETED_1H_CLOSE_TO_CLOSE_SIGN_V1",
            "coverage": context_coverage,
            "event_reaction_available": False,
            "news_available": False,
            "note": "WTI/Brent/USDINR/DXY discovery context is causal hourly context. Event/news family remains unavailable rather than reconstructed.",
        },
        "v2_direction_counts": dict(sorted(v2_direction_counts.items())),
        "v2_confidence_counts": dict(sorted(v2_confidence_counts.items())),
        "current_trade_outcomes_by_v2_alignment": alignment,
        "by_current_action_and_v2_direction": by_current_action_and_v2,
        "buy_pe_stop_family_stances": _family_stance_summary(pe_stops),
        "buy_pe_target_family_stances": _family_stance_summary(pe_targets),
        "buy_ce_stop_family_stances": _family_stance_summary(ce_stops),
        "buy_ce_target_family_stances": _family_stance_summary(ce_targets),
        "wait_v2": {
            "waits": len(waits),
            "v2_directional": len(wait_directional),
            "v2_directional_pct": round(len(wait_directional) / len(waits) * 100.0, 2) if waits else None,
            "direction_counts": dict(sorted(wait_direction_counts.items())),
            "confidence_counts": dict(sorted(wait_confidence_counts.items())),
            "interpretation_guardrail": "A V2 direction on a Current Mind WAIT is not a missed trade; V2 does not evaluate setup or entry readiness.",
        },
        "enriched_decisions": enriched,
        "integrity": {
            "same_frozen_420_click_manifest": True,
            "same_current_mind_outcomes": True,
            "current_mind_mutated": False,
            "direction_v2_mutated": False,
            "future_direction_scoring_used": False,
            "trade_geometry_used_by_v2": False,
            "option_pnl_used_by_v2": False,
            "event_or_news_backfill_used": False,
            "retrospective_filter_claim_allowed": False,
        },
    }
