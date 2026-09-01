from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta

from .crude_random_click_replay import _forward_60m, _index_for_click, _score_trades, preregister_click_schedule
from .crude_research_brain import _build_snapshot_clean, _f, clean_ohlcv
from .current_mind_crude_no_news import current_mind_crude_no_news_decision

NEWS_SHADOW_ID = "CRUDE_CURRENT_CONTRACT_NEWS_SHADOW_V1"
NEWS_ACTIVE_TRADING_HOURS = 8
SESSION_OPEN = time(9, 0)
REFERENCE_CONTRACT = "CRUDEOIL21SEP26FUT"

# Current-contract-window seed only. EIA expectations reconstructed after release are
# intentionally context-only; only point-in-time-defensible causal Reuters events vote.
_CURRENT_CONTRACT_NEWS = (
    {
        "event_id": "EIA_CRUDE_2026_08_05",
        "underlying_event_id": "EIA_CRUDE_2026_08_05",
        "available_at": "2026-08-05T20:00:00+05:30",
        "event_type": "EIA_CRUDE_INVENTORY",
        "effect": "UNKNOWN",
        "disposition": "CONTEXT_ONLY",
        "reason": "HISTORICAL_EXPECTATION_NOT_PROVEN_PIT_SAFE",
    },
    {
        "event_id": "EIA_CRUDE_2026_08_12",
        "underlying_event_id": "EIA_CRUDE_2026_08_12",
        "available_at": "2026-08-12T20:00:00+05:30",
        "event_type": "EIA_CRUDE_INVENTORY",
        "effect": "UNKNOWN",
        "disposition": "CONTEXT_ONLY",
        "reason": "HISTORICAL_EXPECTATION_NOT_PROVEN_PIT_SAFE",
    },
    {
        "event_id": "REUTERS_HORMUZ_20260818_011303Z",
        "underlying_event_id": "HORMUZ_CEASEFIRE_EXPIRY_20260818",
        "available_at": "2026-08-18T06:43:03+05:30",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "effect": "BULLISH",
        "disposition": "ALLOW",
        "reason": "CONFIRMED_OR_REPORTED_CRITICAL_SUPPLY_ROUTE_DISRUPTION",
    },
    {
        "event_id": "REUTERS_SHIPPING_20260818_075510Z",
        "underlying_event_id": "CHINA_SHIPPERS_CHOKEPOINT_AVOIDANCE_20260818",
        "available_at": "2026-08-18T13:25:10+05:30",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "effect": "UNKNOWN",
        "disposition": "CONTEXT_ONLY",
        "reason": "RELEVANT_BUT_DIRECTIONAL_EFFECT_NOT_DEFENSIBLE_EX_ANTE",
    },
    {
        "event_id": "REUTERS_HORMUZ_20260818_151824Z",
        "underlying_event_id": "HORMUZ_CEASEFIRE_EXPIRY_20260818",
        "available_at": "2026-08-18T20:48:24+05:30",
        "event_type": "HORMUZ_SHIPPING_DISRUPTION",
        "effect": "BULLISH",
        "disposition": "ALLOW",
        "material_update": True,
        "reason": "CONFIRMED_OR_REPORTED_CRITICAL_SUPPLY_ROUTE_DISRUPTION",
    },
    {
        "event_id": "EIA_CRUDE_2026_08_19",
        "underlying_event_id": "EIA_CRUDE_2026_08_19",
        "available_at": "2026-08-19T20:00:00+05:30",
        "event_type": "EIA_CRUDE_INVENTORY",
        "effect": "UNKNOWN",
        "disposition": "CONTEXT_ONLY",
        "reason": "HISTORICAL_EXPECTATION_NOT_PROVEN_PIT_SAFE",
    },
    {
        "event_id": "EIA_CRUDE_2026_08_26",
        "underlying_event_id": "EIA_CRUDE_2026_08_26",
        "available_at": "2026-08-26T20:00:00+05:30",
        "event_type": "EIA_CRUDE_INVENTORY",
        "effect": "UNKNOWN",
        "disposition": "CONTEXT_ONLY",
        "reason": "HISTORICAL_EXPECTATION_NOT_PROVEN_PIT_SAFE",
    },
    {
        "event_id": "REUTERS_ESCALATION_20260830_221207Z",
        "underlying_event_id": "US_IRAN_RENEWED_ATTACKS_20260831",
        "available_at": "2026-08-31T03:42:07+05:30",
        "event_type": "WAR_ESCALATION",
        "effect": "BULLISH",
        "disposition": "ALLOW",
        "reason": "ESCALATION_HAS_DIRECT_ENERGY_SUPPLY_RISK_CHANNEL",
    },
)


def current_contract_news_records():
    return [dict(row) for row in _CURRENT_CONTRACT_NEWS]


def _effective_start(event_at: datetime) -> datetime:
    local_open = event_at.replace(hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute, second=0, microsecond=0)
    return local_open if event_at < local_open else event_at


def active_news_at(click_at: datetime, records=None):
    """Return causal news visible and active at click time.

    The 8-trading-hour persistence is inherited from the already-frozen Copper
    research policy. It is intentionally not tuned from Crude outcomes.
    """
    active = []
    seen = set()
    for raw in sorted(records or current_contract_news_records(), key=lambda r: r["available_at"]):
        event_at = datetime.fromisoformat(raw["available_at"])
        if event_at > click_at:
            continue
        event_key = raw.get("underlying_event_id") or raw.get("event_id")
        duplicate = event_key in seen and not raw.get("material_update")
        if event_key:
            seen.add(event_key)
        if duplicate or raw.get("disposition") != "ALLOW":
            continue
        start = _effective_start(event_at)
        if start.date() != click_at.date():
            continue
        if start <= click_at <= start + timedelta(hours=NEWS_ACTIVE_TRADING_HOURS):
            active.append({**raw, "effective_start": start.isoformat()})
    return active


def _technical_pass_count(features):
    checks = (
        str(features.get("structure") or "") == "UPTREND",
        (_f(features.get("return_15m_pct"), 0.0) or 0.0) > 0,
        (_f(features.get("ema20_gap_pct"), 0.0) or 0.0) > 0,
        (_f(features.get("ema50_gap_pct"), 0.0) or 0.0) > 0,
    )
    return sum(bool(x) for x in checks)


def news_shadow_decision(features, no_news_action: str, active_news):
    effects = {row.get("effect") for row in active_news if row.get("effect") in {"BULLISH", "BEARISH"}}
    pass_count = _technical_pass_count(features)
    action = no_news_action
    reason = "NO_ACTIVE_DIRECTIONAL_NEWS"

    if effects == {"BULLISH", "BEARISH"}:
        reason = "CONFLICTING_ACTIVE_NEWS_NO_OVERRIDE"
    elif effects == {"BULLISH"}:
        if no_news_action == "BUY":
            reason = "BULLISH_NEWS_CONFIRMS_FROZEN_BUY"
        elif pass_count >= 3:
            action = "BUY"
            reason = "BULLISH_NEWS_UPGRADES_NEAR_READY_TECHNICAL_WAIT"
        else:
            reason = "BULLISH_NEWS_PRESENT_BUT_TECHNICAL_CONTEXT_NOT_NEAR_READY"
    elif effects == {"BEARISH"}:
        if no_news_action == "BUY":
            action = "WAIT"
            reason = "BEARISH_NEWS_VETOES_FROZEN_BUY"
        else:
            reason = "BEARISH_NEWS_DOES_NOT_CREATE_SHORT_IN_LONG_ONLY_V1"

    return {
        "news_shadow_id": NEWS_SHADOW_ID,
        "action": action,
        "base_action": no_news_action,
        "technical_pass_count": pass_count,
        "active_news_count": len(active_news),
        "active_news_effects": sorted(effects),
        "reason": reason,
        "news_can_create_sell": False,
        "news_policy_source": "COPPER_FROZEN_8_TRADING_HOUR_PERSISTENCE_PLUS_CRUDE_LONG_ONLY_V1",
    }


def run_same_click_news_comparison(candles, *, trading_symbol, clicks_per_session=20, news_records=None):
    rows = clean_ohlcv(candles)
    schedule = preregister_click_schedule(rows, clicks_per_session)
    decisions = []
    for scheduled in schedule:
        click_at = datetime.fromisoformat(scheduled["click_at"])
        index = _index_for_click(rows, click_at)
        if index is None or index < 50:
            continue

        # Freeze both decisions before the future label is attached.
        features = _build_snapshot_clean(rows, index)
        no_news = current_mind_crude_no_news_decision(features)
        active = active_news_at(click_at, news_records)
        news = news_shadow_decision(features, no_news["action"], active)
        forward = _forward_60m(rows, index)
        if forward is None:
            continue

        decisions.append({
            "date": scheduled["date"],
            "click_at": scheduled["click_at"],
            "no_news_action": no_news["action"],
            "news_action": news["action"],
            "news_reason": news["reason"],
            "active_news": active,
            "technical_pass_count": news["technical_pass_count"],
            "forward_60m_pct": round(float(forward), 6),
        })

    no_news_rows = [
        {"action": "BUY" if row["no_news_action"] == "BUY" else "NO_TRADE", "forward_60m_pct": row["forward_60m_pct"]}
        for row in decisions
    ]
    news_rows = [
        {"action": "BUY" if row["news_action"] == "BUY" else "NO_TRADE", "forward_60m_pct": row["forward_60m_pct"]}
        for row in decisions
    ]
    no_news_score = _score_trades(no_news_rows, "action")
    news_score = _score_trades(news_rows, "action")
    changed = [row for row in decisions if row["no_news_action"] != row["news_action"]]
    added_buys = [row for row in changed if row["no_news_action"] == "WAIT" and row["news_action"] == "BUY"]
    vetoed_buys = [row for row in changed if row["no_news_action"] == "BUY" and row["news_action"] == "WAIT"]

    return {
        "mode": "ALPHAPILOT_CRUDE_CURRENT_CONTRACT_SAME_CLICK_NEWS_COMPARISON_V1",
        "commodity": "CRUDEOIL",
        "trading_symbol": str(trading_symbol),
        "reference_contract": REFERENCE_CONTRACT,
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "option_premium_scored": False,
        "same_candles": True,
        "same_click_schedule": True,
        "click_seed_namespace_reused": "ALPHAPILOT_CRUDE_NO_NEWS_RANDOM_CLICK_V1",
        "clicks_per_session": int(clicks_per_session),
        "news_active_trading_hours": NEWS_ACTIVE_TRADING_HOURS,
        "news_window_tuned_on_crude_outcomes": False,
        "decision_frozen_before_outcome_attached": True,
        "coverage": {
            "scheduled_clicks": len(schedule),
            "evaluated_clicks": len(decisions),
            "sessions": len({row["date"] for row in decisions}),
            "exact_click_coverage": len(schedule) == len(decisions),
        },
        "no_news": {
            "actions": dict(Counter(row["no_news_action"] for row in decisions)),
            "score": no_news_score,
        },
        "with_news": {
            "actions": dict(Counter(row["news_action"] for row in decisions)),
            "score": news_score,
        },
        "decision_delta": {
            "changed_clicks": len(changed),
            "added_buys": len(added_buys),
            "vetoed_buys": len(vetoed_buys),
            "unchanged_clicks": len(decisions) - len(changed),
        },
        "score_delta": {
            "trades": news_score["trades"] - no_news_score["trades"],
            "win_rate_pp": round(news_score["win_rate_pct"] - no_news_score["win_rate_pct"], 2),
            "avg_net_return_pct": round(news_score["avg_net_return_pct"] - no_news_score["avg_net_return_pct"], 4),
            "net_return_sum_pct": round(news_score["net_return_sum_pct"] - no_news_score["net_return_sum_pct"], 4),
            "profit_factor": None if news_score["profit_factor"] is None or no_news_score["profit_factor"] is None else round(news_score["profit_factor"] - no_news_score["profit_factor"], 3),
        },
        "changed_decisions": changed,
        "decisions": decisions,
        "limitations": [
            "The audited current-contract news ledger is intentionally sparse and not exhaustive.",
            "Historical EIA consensus values without pre-release provenance are context-only and cannot vote.",
            "News may add/veto long trades but cannot create SELL in the frozen long-only V1 comparison.",
            "The 8-hour news persistence is inherited from Copper and is being tested, not optimized, for Crude.",
            "Underlying 60-minute move after 4 bps research cost is scored; option-premium P&L is not yet scored.",
        ],
    }
