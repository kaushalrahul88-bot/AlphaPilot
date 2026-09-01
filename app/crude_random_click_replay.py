from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from .crude_directional_asymmetry_candidate import long_only_shadow_signal
from .crude_research_brain import _build_snapshot_clean, _f, brain_a_signal, clean_ohlcv

IST = ZoneInfo("Asia/Kolkata")
CLICKS_PER_SESSION = 20
CLICK_START = time(10, 0)
CLICK_END = time(22, 0)
CLICK_SEED_NAMESPACE = "ALPHAPILOT_CRUDE_NO_NEWS_RANDOM_CLICK_V1"
HORIZON_BARS = 12
ROUND_TRIP_COST_BPS = 4.0


def _seed_for_day(day):
    digest = hashlib.sha256(f"{CLICK_SEED_NAMESPACE}|{day.isoformat()}".encode()).hexdigest()
    return int(digest[:16], 16)


def _available_at(row):
    return row[0] + timedelta(minutes=5)


def _session_clicks(rows, day, count=CLICKS_PER_SESSION):
    eligible = []
    for row in rows:
        local = _available_at(row).astimezone(IST)
        if local.date() != day:
            continue
        if CLICK_START <= local.time() <= CLICK_END:
            eligible.append(local)
    eligible = sorted(set(eligible))
    if len(eligible) < count:
        return []
    rng = random.Random(_seed_for_day(day))
    return sorted(rng.sample(eligible, count))


def preregister_click_schedule(candles, clicks_per_session=CLICKS_PER_SESSION):
    rows = clean_ohlcv(candles)
    days = sorted({_available_at(r).astimezone(IST).date() for r in rows})
    schedule = []
    for day in days:
        clicks = _session_clicks(rows, day, max(1, int(clicks_per_session)))
        if len(clicks) != max(1, int(clicks_per_session)):
            continue
        schedule.extend({"date": day.isoformat(), "click_at": click.isoformat()} for click in clicks)
    return schedule


def _index_for_click(rows, click_at):
    # Last candle whose completion time is <= click. No partial bar is visible.
    index = None
    for i, row in enumerate(rows):
        if _available_at(row) <= click_at:
            index = i
        else:
            break
    return index


def _forward_60m(rows, index):
    if index is None or index + HORIZON_BARS >= len(rows):
        return None
    entry = rows[index][4]
    exit_price = rows[index + HORIZON_BARS][4]
    if entry <= 0:
        return None
    return (exit_price / entry - 1.0) * 100.0


def _score_trades(records, action_key):
    values = []
    for row in records:
        action = row[action_key]
        forward = _f(row.get("forward_60m_pct"))
        if action not in {"BUY", "SELL"} or forward is None:
            continue
        gross = forward if action == "BUY" else -forward
        values.append(gross - ROUND_TRIP_COST_BPS / 100.0)
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(values), 2) if values else 0.0,
        "avg_net_return_pct": round(mean(values), 4) if values else 0.0,
        "net_return_sum_pct": round(sum(values), 4),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
    }


def run_crude_random_click_replay(candles, *, trading_symbol, clicks_per_session=CLICKS_PER_SESSION):
    rows = clean_ohlcv(candles)
    if len(rows) < 80:
        raise RuntimeError(f"Insufficient exact-contract MCX Crude 5m history ({len(rows)} candles)")
    schedule = preregister_click_schedule(rows, clicks_per_session)
    decisions = []
    for scheduled in schedule:
        click_at = datetime.fromisoformat(scheduled["click_at"])
        index = _index_for_click(rows, click_at)
        if index is None or index < 50:
            continue
        forward = _forward_60m(rows, index)
        if forward is None:
            continue
        features = _build_snapshot_clean(rows, index)
        baseline_action = brain_a_signal(features)
        candidate_action = long_only_shadow_signal(features)
        decisions.append({
            "date": scheduled["date"],
            "click_at": click_at.isoformat(),
            "visible_bar_start": features["bar_start"],
            "visible_bar_available_at": features["available_at"],
            "baseline_action": baseline_action,
            "candidate_action": candidate_action,
            "forward_60m_pct": round(forward, 6),
        })

    by_day = defaultdict(list)
    for row in decisions:
        by_day[row["date"]].append(row)
    sessions = []
    for day in sorted(by_day):
        sample = by_day[day]
        sessions.append({
            "date": day,
            "clicks": len(sample),
            "baseline_actions": dict(Counter(x["baseline_action"] for x in sample)),
            "candidate_actions": dict(Counter(x["candidate_action"] for x in sample)),
            "baseline_score": _score_trades(sample, "baseline_action"),
            "candidate_score": _score_trades(sample, "candidate_action"),
        })

    removed_sells = [x for x in decisions if x["baseline_action"] == "SELL" and x["candidate_action"] == "NO_TRADE"]
    sell_net = [(-x["forward_60m_pct"] - ROUND_TRIP_COST_BPS / 100.0) for x in removed_sells]
    avoided_losses = [x for x in sell_net if x < 0]
    missed_wins = [x for x in sell_net if x > 0]
    return {
        "mode": "ALPHAPILOT_CRUDE_RANDOM_CLICK_REPLAY_V1",
        "commodity": "CRUDEOIL",
        "trading_symbol": str(trading_symbol),
        "research_only": True,
        "production_rules_changed": False,
        "live_execution_enabled": False,
        "news_enabled": False,
        "candidate_frozen_before_click_schedule": True,
        "click_schedule_outcome_blind": True,
        "click_seed_namespace": CLICK_SEED_NAMESPACE,
        "click_window_ist": {"start": "10:00", "end": "22:00"},
        "clicks_per_session": int(clicks_per_session),
        "bar_timing": "LAST_5M_BAR_WITH_BAR_START_PLUS_5_MINUTES_LE_CLICK",
        "coverage": {
            "mcx_5m_candles": len(rows),
            "scheduled_clicks": len(schedule),
            "evaluated_clicks": len(decisions),
            "sessions": len(sessions),
            "exact_click_coverage": len(schedule) == len(decisions),
        },
        "baseline_brain_a": {
            "actions": dict(Counter(x["baseline_action"] for x in decisions)),
            "score": _score_trades(decisions, "baseline_action"),
        },
        "long_only_shadow": {
            "actions": dict(Counter(x["candidate_action"] for x in decisions)),
            "score": _score_trades(decisions, "candidate_action"),
        },
        "wait_attribution": {
            "removed_baseline_sells": len(removed_sells),
            "avoided_losing_sells": len(avoided_losses),
            "missed_winning_sells": len(missed_wins),
            "removed_sell_net_sum_pct": round(sum(sell_net), 4),
            "interpretation": "Negative removed_sell_net_sum_pct means the removed SELL set lost money as trades, so WAIT added value.",
        },
        "sessions": sessions,
        "decisions": decisions,
        "limitations": [
            "Clicks are independent research observations; overlapping hypothetical trades are not a portfolio P&L simulation.",
            "Outcome is fixed 60-minute underlying return after 4 bps research cost, not option-premium P&L.",
            "The exact current contract history is not described as a continuous front-month series.",
            "News is forbidden from all decisions in this replay.",
        ],
        "next_gate": "If random-click behavior confirms the no-news candidate, proceed to assemble the Crude no-news Current Mind and then freeze it before any news comparison.",
    }
