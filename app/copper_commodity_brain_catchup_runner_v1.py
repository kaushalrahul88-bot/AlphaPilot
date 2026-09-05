from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .copper_commodity_brain_pit_replay_v1 import evaluate_copper_pit_replay

IST = ZoneInfo("Asia/Kolkata")
MODE = "COPPER_COMMODITY_BRAIN_CATCHUP_RUNNER_V1"
REPLAY_DATES = (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4))
SESSION_START = time(9, 0)
SESSION_END = time(23, 0)
CLICK_MINUTES = 30


def scheduled_clicks(day: date) -> list[datetime]:
    current = datetime.combine(day, SESSION_START, IST)
    end = datetime.combine(day, SESSION_END, IST)
    out: list[datetime] = []
    while current <= end:
        out.append(current)
        current += timedelta(minutes=CLICK_MINUTES)
    return out


def run_catchup(*, candles: list, option_rows: list[dict]) -> dict:
    """Outcome-blind Sep 1-4 catch-up using the already-frozen replay evaluator.

    The runner deliberately contains no outcomes, P&L, tuning, persistence or
    execution. Aug 31 is excluded because the exact expiring futures contract is
    incomplete in local storage after Groww stopped serving its history post-expiry.
    """
    evaluations: list[dict] = []
    for day in REPLAY_DATES:
        for click in scheduled_clicks(day):
            evaluations.append(
                evaluate_copper_pit_replay(
                    candles=candles,
                    option_rows=option_rows,
                    click_at=click,
                )
            )

    evaluated = [row for row in evaluations if row.get("status") == "EVALUATED"]
    insufficient = [row for row in evaluations if row.get("status") != "EVALUATED"]
    direction_counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    for row in evaluated:
        brain = row.get("brain") or {}
        direction = str(brain.get("direction") or "UNKNOWN")
        confidence = str(brain.get("confidence") or "UNKNOWN")
        direction_counts[direction] = direction_counts.get(direction, 0) + 1
        confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1

    return {
        "mode": MODE,
        "evaluation_class": "RETROSPECTIVE_PIT_REPLAY",
        "research_only": True,
        "prospective": False,
        "outcome_blind": True,
        "outcomes_joined": False,
        "pnl_computed": False,
        "replay_rule_tuning_allowed": False,
        "eligible_for_prospective_memory": False,
        "historical_predictions_rewritten": False,
        "live_execution_enabled": False,
        "broker_order_placement_enabled": False,
        "capital_committed": 0,
        "dates": [day.isoformat() for day in REPLAY_DATES],
        "excluded_dates": {
            "2026-08-31": "INCOMPLETE_EXACT_CONTRACT_UNDERLYING_5M_TAPE",
        },
        "cadence": "09:00-23:00 IST every 30 minutes",
        "scheduled_clicks": len(evaluations),
        "evaluated_clicks": len(evaluated),
        "insufficient_clicks": len(insufficient),
        "direction_counts": direction_counts,
        "confidence_counts": confidence_counts,
        "evaluations": evaluations,
    }
