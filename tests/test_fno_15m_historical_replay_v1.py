from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.fno_15m_historical_replay_v1 import (
    architecture_contract,
    click_schedule,
    completed_candles_at,
    resolve_historical_candidate,
)

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")


def _perception():
    return {
        "source": {"expiry_date": "2026-09-29"},
        "underlying": {"symbol": "RELIANCE", "ltp": 100.0},
        "derivatives": {"pcr_oi": 1.0, "atm_iv": 20.0},
    }


def _decision(action="BUY_CE"):
    candidate = {
        "option_type": "CE",
        "strike": 100.0,
        "trading_symbol": "RELIANCE26SEP100CE",
        "ltp": 50.0,
        "open_interest": 100,
        "volume": 50,
    }
    return {
        "research_action": action,
        "research_candidate": candidate if action != "NO_TRADE" else None,
    }


def _snapshot(at: datetime, ltp: float):
    return {
        "provider": "GROWW",
        "underlying_symbol": "RELIANCE",
        "expiry_date": "2026-09-29",
        "observed_at": at,
        "payload": {
            "data": {
                "payload": {
                    "underlying_ltp": 100.0,
                    "strikes": {
                        "100": {
                            "CE": {
                                "trading_symbol": "RELIANCE26SEP100CE",
                                "ltp": ltp,
                            },
                            "PE": {},
                        }
                    },
                }
            }
        },
    }


class Fno15mHistoricalReplayTests(unittest.TestCase):
    def test_click_schedule_is_exact_23_clicks(self):
        clicks = click_schedule(date(2026, 9, 1))
        self.assertEqual(len(clicks), 23)
        self.assertEqual(clicks[0].astimezone(IST).strftime("%H:%M"), "09:30")
        self.assertEqual(clicks[-1].astimezone(IST).strftime("%H:%M"), "15:00")
        gaps = [
            int((b - a).total_seconds() // 60)
            for a, b in zip(clicks, clicks[1:])
        ]
        self.assertEqual(set(gaps), {15})

    def test_completed_candles_excludes_current_interval(self):
        click = datetime(2026, 9, 1, 10, 0, tzinfo=IST).astimezone(UTC)
        rows = [
            ["2026-09-01T09:50:00+05:30", 100, 101, 99, 100.5, 10],
            ["2026-09-01T09:55:00+05:30", 100.5, 102, 100, 101.0, 10],
            ["2026-09-01T10:00:00+05:30", 101, 103, 100, 102.0, 10],
        ]
        filtered = completed_candles_at(rows, click, "5m")
        self.assertEqual([row[0][11:16] for row in filtered], ["09:50", "09:55"])

    def test_exact_option_outcome_requires_later_saved_snapshot(self):
        click = datetime(2026, 9, 1, 10, 0, tzinfo=IST).astimezone(UTC)
        candles = [
            ["2026-09-01T10:00:00+05:30", 100, 101, 99, 100.2, 10],
            ["2026-09-01T10:55:00+05:30", 100.2, 103, 100, 102.0, 10],
        ]
        incomplete = resolve_historical_candidate(
            _perception(),
            _decision(),
            [_snapshot(click - timedelta(seconds=30), 50.0)],
            candles,
            click_at=click,
        )
        self.assertEqual(
            incomplete["resolution_status"],
            "SELECTED_OPTION_TAPE_INCOMPLETE",
        )

        resolved = resolve_historical_candidate(
            _perception(),
            _decision(),
            [
                _snapshot(click - timedelta(seconds=30), 50.0),
                _snapshot(click + timedelta(minutes=30), 55.0),
            ],
            candles,
            click_at=click,
        )
        self.assertEqual(resolved["resolution_status"], "RESOLVED")
        self.assertEqual(resolved["classification"], "OPTION_GAIN")
        self.assertEqual(resolved["option_return_pct"], 10.0)

    def test_late_click_is_kept_but_outcome_ineligible(self):
        click = datetime(2026, 9, 1, 15, 0, tzinfo=IST).astimezone(UTC)
        result = resolve_historical_candidate(
            _perception(),
            _decision("NO_TRADE"),
            [],
            [],
            click_at=click,
        )
        self.assertEqual(
            result["resolution_status"],
            "INELIGIBLE_LATE_SESSION_HORIZON",
        )

    def test_contract_never_enables_execution(self):
        contract = architecture_contract()
        self.assertEqual(contract["scheduled_clicks_per_day"], 23)
        self.assertTrue(contract["strict_point_in_time_option_chain"])
        self.assertTrue(contract["completed_technical_candles_only"])
        self.assertFalse(contract["historical_option_chain_backfill"])
        self.assertFalse(contract["display_top_candidate_is_policy_selector"])
        self.assertFalse(contract["database_writes"])
        self.assertFalse(contract["strategy_policy_changed"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)


if __name__ == "__main__":
    unittest.main()
