from __future__ import annotations

from datetime import timedelta

from .commodity_backtest import _plan_at, _ts
from .commodity_click_brain import _valid_rows
from .commodities import analyze_commodity_candles


TIMEFRAMES = {"5m": 5, "15m": 15, "1h": 60}
FRESHNESS_MINUTES = {"5m": 15, "15m": 35, "1h": 90}


def completed_rows(rows, click_at, interval_minutes):
    """Return only candles whose full interval ended by the decision time."""
    click = _ts(click_at)
    duration = timedelta(minutes=int(interval_minutes))
    return [row for row in _valid_rows(rows) if row[0] + duration <= click]


def completed_mtf_snapshot(symbol, rows_by_timeframe, click_at, min_risk_reward=1.5):
    """Build replay/live MTF state with identical completion and freshness rules."""
    click = _ts(click_at)
    frames = {}
    freshness = {}
    for timeframe, interval in TIMEFRAMES.items():
        rows = completed_rows(rows_by_timeframe.get(timeframe, []), click, interval)[-260:]
        frames[timeframe] = analyze_commodity_candles(symbol, rows, min_risk_reward)
        if rows:
            completed_at = rows[-1][0] + timedelta(minutes=interval)
            age = max(0.0, (click - completed_at).total_seconds() / 60.0)
        else:
            completed_at = None
            age = None
        passed = age is not None and age <= FRESHNESS_MINUTES[timeframe]
        freshness[timeframe] = {
            "passed": passed,
            "last_completed_at": completed_at.isoformat() if completed_at else None,
            "age_minutes": round(age, 1) if age is not None else None,
            "maximum_minutes": FRESHNESS_MINUTES[timeframe],
        }
    plan = _plan_at(symbol, frames, min_risk_reward, 65.0)
    snapshot = {
        "action": plan.get("action") if plan else "NO TRADE",
        "alpha_score": plan.get("strength") if plan else 50.0,
        "fresh_market_data": all(value["passed"] for value in freshness.values()),
    }
    return frames, plan, snapshot, freshness
