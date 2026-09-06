from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median

from .commodity_time import parse_ist_timestamp
from . import crude_oil_mini_august_20_click_backtest as frozen

MODE = "CRUDE_OIL_MINI_AUGUST_FROZEN_DIAGNOSIS_V1"


def _avg(values):
    vals = [float(v) for v in values if v is not None]
    return round(mean(vals), 4) if vals else None


def _med(values):
    vals = [float(v) for v in values if v is not None]
    return round(median(vals), 4) if vals else None


def _entry_delay_minutes(click_timestamp: str, entry_at: str | None):
    if not entry_at:
        return None
    return round((parse_ist_timestamp(entry_at) - parse_ist_timestamp(click_timestamp)).total_seconds() / 60.0, 2)


def _time_bucket(click_timestamp: str) -> str:
    hour = parse_ist_timestamp(click_timestamp).hour
    if hour < 13:
        return "BEFORE_13"
    if hour < 16:
        return "13_TO_16"
    if hour < 19:
        return "16_TO_19"
    if hour < 22:
        return "19_TO_22"
    return "22_PLUS"


def _summarize_trades(rows: list[dict]) -> dict:
    outcomes = Counter(row["result"] for row in rows)
    realized = [float(row.get("realized_r") or 0.0) for row in rows]
    resolved = [row for row in rows if row["result"] in {"TARGET", "STOP"}]
    entered = [row for row in rows if row.get("entry_at")]
    return {
        "trades": len(rows),
        "targets": outcomes["TARGET"],
        "stops": outcomes["STOP"],
        "no_entry": outcomes["NO_ENTRY"],
        "session_end": outcomes["SESSION_END"],
        "resolved_trades": len(resolved),
        "resolved_win_rate_pct": round(outcomes["TARGET"] / len(resolved) * 100.0, 2) if resolved else None,
        "net_r": round(sum(realized), 4),
        "expectancy_r_per_trade": round(sum(realized) / len(rows), 4) if rows else None,
        "avg_mfe_r_entered": _avg(row.get("mfe_r") for row in entered),
        "avg_mae_r_entered": _avg(row.get("mae_r") for row in entered),
        "median_entry_delay_min": _med(row.get("entry_delay_min") for row in entered),
    }


def _group(rows: list[dict], key) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(key(row))].append(row)
    return {name: _summarize_trades(items) for name, items in sorted(grouped.items())}


def _capture_same_frozen_replay(candles, contract: dict | None):
    captures: dict[str, dict] = {}
    original = frozen.crude_oil_mini_current_mind_click

    def observing_click(*, click_timestamp, context_records, market_features, evidence_items, memory_cases=None):
        journal = original(
            click_timestamp=click_timestamp,
            context_records=context_records,
            market_features=market_features,
            evidence_items=evidence_items,
            memory_cases=memory_cases,
        )
        captures[parse_ist_timestamp(click_timestamp).isoformat()] = {
            "market_features": dict(market_features or {}),
            "evidence_items": list(evidence_items or []),
            "journal": journal,
        }
        return journal

    frozen.crude_oil_mini_current_mind_click = observing_click
    try:
        report = frozen.evaluate_august_20_new_clicks_per_day(candles, contract)
    finally:
        frozen.crude_oil_mini_current_mind_click = original
    return report, captures


def diagnose_frozen_august_replay(candles, contract: dict | None = None) -> dict:
    report, captures = _capture_same_frozen_replay(candles, contract)
    if report["monthly"]["clicks"] != 420 or float(report["monthly"]["net_r"]) != -10.0:
        raise RuntimeError("Diagnosis must attach to the exact frozen 420-click, -10R August baseline")

    enriched = []
    for base in report["decisions"]:
        click = parse_ist_timestamp(base["click_timestamp"]).isoformat()
        cap = captures.get(click)
        if not cap:
            raise RuntimeError(f"Missing click-time diagnostic capture for {click}")
        journal = cap["journal"] or {}
        decision = journal.get("decision") or {}
        evidence = journal.get("evidence") or {}
        regime = journal.get("regime") or {}
        market = cap["market_features"] or {}
        outcome = base.get("outcome") or {}
        enriched.append({
            **base,
            "result": outcome.get("result"),
            "realized_r": float(outcome.get("realized_r") or 0.0),
            "mfe_r": outcome.get("mfe_r"),
            "mae_r": outcome.get("mae_r"),
            "entry_at": outcome.get("entry_at"),
            "entry_delay_min": _entry_delay_minutes(click, outcome.get("entry_at")),
            "same_bar_ambiguous": bool(outcome.get("same_bar_ambiguous")),
            "future_move_without_setup": outcome.get("future_move_without_setup"),
            "max_up_pct": outcome.get("max_up_pct"),
            "max_down_pct": outcome.get("max_down_pct"),
            "time_bucket": _time_bucket(click),
            "decision_reason": decision.get("reason"),
            "evidence_quality": decision.get("evidence_quality"),
            "independent_bullish_lanes": evidence.get("independent_bullish_lanes", []),
            "independent_bearish_lanes": evidence.get("independent_bearish_lanes", []),
            "contradictory_lanes": evidence.get("contradictory_lanes", []),
            "trend_structure": (regime.get("observations") or {}).get("trend_structure") or market.get("trend_structure"),
            "volatility_regime": (regime.get("observations") or {}).get("volatility_regime") or market.get("volatility_regime"),
            "location": (regime.get("observations") or {}).get("location") or market.get("location"),
            "participation": (regime.get("observations") or {}).get("participation") or market.get("participation"),
            "opening_behavior": (regime.get("observations") or {}).get("opening_behavior") or market.get("opening_behavior"),
        })

    trades = [row for row in enriched if row["action"] in {"BUY_CE", "BUY_PE"}]
    waits = [row for row in enriched if row["action"] == "WAIT"]
    stops = [row for row in trades if row["result"] == "STOP"]

    label_presence = {}
    labels = sorted({label for row in trades for label in row.get("regime_labels", [])})
    for label in labels:
        label_presence[label] = _summarize_trades([row for row in trades if label in row.get("regime_labels", [])])

    stop_path = {
        "stops": len(stops),
        "mfe_under_0_25r": sum(float(row.get("mfe_r") or 0.0) < 0.25 for row in stops),
        "mfe_under_0_50r": sum(float(row.get("mfe_r") or 0.0) < 0.50 for row in stops),
        "mfe_at_least_0_50r": sum(float(row.get("mfe_r") or 0.0) >= 0.50 for row in stops),
        "mfe_at_least_1_00r": sum(float(row.get("mfe_r") or 0.0) >= 1.00 for row in stops),
        "mfe_at_least_1_25r": sum(float(row.get("mfe_r") or 0.0) >= 1.25 for row in stops),
        "same_bar_target_stop_collision": sum(bool(row.get("same_bar_ambiguous")) for row in stops),
        "avg_mfe_r": _avg(row.get("mfe_r") for row in stops),
        "avg_mae_r": _avg(row.get("mae_r") for row in stops),
        "median_entry_delay_min": _med(row.get("entry_delay_min") for row in stops),
        "entry_delay_at_least_60m": sum((row.get("entry_delay_min") or 0.0) >= 60.0 for row in stops),
        "entry_delay_at_least_120m": sum((row.get("entry_delay_min") or 0.0) >= 120.0 for row in stops),
        "note": "MFE and entry-delay buckets are descriptive diagnostics only, not candidate filters or strategy gates.",
    }

    wait_groups: dict[str, list[dict]] = defaultdict(list)
    for row in waits:
        key = f"{row.get('decision_reason')}|{row.get('evidence_quality')}"
        wait_groups[key].append(row)
    wait_diagnosis = {}
    for key, rows in sorted(wait_groups.items()):
        flagged = [row for row in rows if row.get("future_move_without_setup")]
        wait_diagnosis[key] = {
            "waits": len(rows),
            "large_move_flagged": len(flagged),
            "large_move_flag_rate_pct": round(len(flagged) / len(rows) * 100.0, 2) if rows else None,
            "contradiction_present": sum(bool(row.get("contradictory_lanes")) for row in rows),
            "bullish_lane_count_avg": _avg(len(row.get("independent_bullish_lanes") or []) for row in rows),
            "bearish_lane_count_avg": _avg(len(row.get("independent_bearish_lanes") or []) for row in rows),
        }

    daily = report["daily"]
    positive_days = {day for day, row in daily.items() if float(row.get("net_r") or 0.0) > 0}
    negative_days = {day for day, row in daily.items() if float(row.get("net_r") or 0.0) < 0}

    def cohort(days: set[str]) -> dict:
        rows = [row for row in trades if row["session"] in days]
        if not rows:
            return {}
        return {
            **_summarize_trades(rows),
            "days": sorted(days),
            "buy_pe_share_pct": round(sum(row["action"] == "BUY_PE" for row in rows) / len(rows) * 100.0, 2),
            "high_volatility_share_pct": round(sum(row.get("volatility_regime") == "HIGH" for row in rows) / len(rows) * 100.0, 2),
            "opening_expansion_share_pct": round(sum(row.get("opening_behavior") in {"BREAKOUT", "BREAKDOWN"} for row in rows) / len(rows) * 100.0, 2),
            "median_entry_delay_min": _med(row.get("entry_delay_min") for row in rows if row.get("entry_at")),
        }

    return {
        "mode": MODE,
        "research_only": True,
        "descriptive_only": True,
        "brain_rules_changed": False,
        "click_schedule_changed": False,
        "baseline": report["monthly"],
        "manifest_sha256": report["manifest"]["manifest_sha256"],
        "by_action": _group(trades, lambda row: row["action"]),
        "by_playbook": _group(trades, lambda row: row.get("playbook")),
        "by_playbook_and_action": _group(trades, lambda row: f"{row.get('playbook')}|{row['action']}"),
        "by_trend_structure": _group(trades, lambda row: row.get("trend_structure")),
        "by_volatility": _group(trades, lambda row: row.get("volatility_regime")),
        "by_location": _group(trades, lambda row: row.get("location")),
        "by_participation": _group(trades, lambda row: row.get("participation")),
        "by_opening_behavior": _group(trades, lambda row: row.get("opening_behavior")),
        "by_time_bucket": _group(trades, lambda row: row["time_bucket"]),
        "regime_label_presence": label_presence,
        "stop_path_diagnostics": stop_path,
        "wait_diagnosis": wait_diagnosis,
        "positive_day_cohort": cohort(positive_days),
        "negative_day_cohort": cohort(negative_days),
        "best_days": sorted(((day, float(row.get("net_r") or 0.0)) for day, row in daily.items()), key=lambda item: item[1], reverse=True)[:5],
        "worst_days": sorted(((day, float(row.get("net_r") or 0.0)) for day, row in daily.items()), key=lambda item: item[1])[:6],
        "enriched_decisions": enriched,
        "integrity": {
            "same_frozen_420_click_manifest": True,
            "same_frozen_current_mind": True,
            "same_trade_outcomes": True,
            "direction_audit_added": False,
            "threshold_search_performed": False,
            "candidate_filter_selected": False,
            "promotion_allowed": False,
        },
    }
