import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

from app.current_mind_copper_forward import (
    BAR_MINUTES,
    FORWARD_EXPECTED_CLICKS,
    FORWARD_PERCEPTION_FETCH_START,
    FORWARD_SCORE_START,
    FORWARD_WARMUP_START,
    _experience_available_at,
    _outcome_available_at,
    candle_completed_at,
    preregistered_phase1_click_schedule,
    run_forward_phase1_from_provider,
    safe_forward_experience_pool,
    scheduled_forward_clicks,
)


class CopperCurrentMindForwardTests(unittest.TestCase):
    def test_candle_is_visible_only_at_interval_completion(self):
        row = ["2026-09-02T10:00:00+05:30", 100, 101, 99, 100.5, 10, None]
        self.assertEqual(
            candle_completed_at(row),
            datetime.fromisoformat("2026-09-02T10:05:00+05:30"),
        )

    def test_daily_clicks_are_calendar_only_and_completion_timestamped(self):
        clicks = scheduled_forward_clicks(date(2026, 9, 2))
        self.assertEqual(len(clicks), 20)
        self.assertEqual(len({item["click_timestamp"] for item in clicks}), 20)
        for item in clicks:
            source = datetime.fromisoformat(item["source_candle_at"])
            click = datetime.fromisoformat(item["click_timestamp"])
            self.assertEqual((click - source).total_seconds() / 60, BAR_MINUTES)
            self.assertEqual(item["sampling"], "PREREGISTERED_TIMESTAMP_ONLY_DETERMINISTIC_RANDOM")

    def test_phase1_schedule_excludes_september_1_and_is_frozen_to_160_clicks(self):
        clicks = preregistered_phase1_click_schedule()
        self.assertEqual(len(clicks), FORWARD_EXPECTED_CLICKS)
        sessions = sorted({item["session"] for item in clicks})
        self.assertEqual(sessions[0], FORWARD_SCORE_START.date().isoformat())
        self.assertNotIn("2026-09-01", sessions)
        self.assertEqual(len(sessions), 8)

    def test_perception_fetch_padding_precedes_warmup_without_changing_score_start(self):
        self.assertLess(FORWARD_PERCEPTION_FETCH_START, FORWARD_WARMUP_START)
        self.assertLess(FORWARD_WARMUP_START, FORWARD_SCORE_START)
        self.assertEqual(FORWARD_WARMUP_START.date().isoformat(), "2026-09-01")
        self.assertEqual(FORWARD_SCORE_START.date().isoformat(), "2026-09-02")

    def test_experience_event_is_withheld_until_event_bar_has_completed(self):
        experience = {
            "timestamp": "2026-09-02T10:00:00+05:30",
            "minutes_to_event": 5,
            "vector": {},
        }
        self.assertEqual(
            _experience_available_at(experience),
            datetime.fromisoformat("2026-09-02T10:10:00+05:30"),
        )
        at_resolution = datetime.fromisoformat("2026-09-02T10:10:00+05:30")
        after_resolution = datetime.fromisoformat("2026-09-02T10:10:01+05:30")
        self.assertEqual(safe_forward_experience_pool([experience], at_resolution), [])
        self.assertEqual(len(safe_forward_experience_pool([experience], after_resolution)), 1)

    def test_no_event_experience_is_withheld_until_session_close(self):
        experience = {
            "timestamp": "2026-09-02T10:00:00+05:30",
            "minutes_to_event": None,
            "vector": {},
        }
        self.assertEqual(
            _experience_available_at(experience),
            datetime.fromisoformat("2026-09-02T23:30:00+05:30"),
        )
        at_close = datetime.fromisoformat("2026-09-02T23:30:00+05:30")
        next_day = datetime.fromisoformat("2026-09-03T09:05:00+05:30")
        self.assertEqual(safe_forward_experience_pool([experience], at_close), [])
        self.assertEqual(len(safe_forward_experience_pool([experience], next_day)), 1)

    def test_replay_exit_outcome_is_not_available_until_exit_bar_completes(self):
        click = datetime.fromisoformat("2026-09-02T10:05:00+05:30")
        outcome = {"result": "TARGET", "exit_at": "2026-09-02T10:15:00+05:30"}
        self.assertEqual(
            _outcome_available_at(outcome, click, click.date()),
            datetime.fromisoformat("2026-09-02T10:20:00+05:30"),
        )


class CopperCurrentMindForwardProviderTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.current_mind_copper_forward.evaluate_forward_window")
    @patch("app.current_mind_copper_forward._fetch_chunked", new_callable=AsyncMock)
    @patch("app.current_mind_copper_forward.resolve_nearest_mcx_future", new_callable=AsyncMock)
    async def test_provider_fetch_uses_pre_score_perception_padding(
        self,
        resolve_contract,
        fetch_chunked,
        evaluate_window,
    ):
        resolve_contract.return_value = {
            "trading_symbol": "COPPER30SEP26FUT",
            "groww_symbol": "MCX-COPPER-30SEP26-FUT",
            "expiry_date": "2026-09-30",
        }
        fetch_chunked.return_value = [["2026-08-25T09:00:00+05:30", 100, 101, 99, 100, 1, None]]
        evaluate_window.return_value = {"mode": "COPPER_CURRENT_MIND_FORWARD_PHASE1_V1"}
        as_of = datetime.fromisoformat("2026-09-03T23:30:00+05:30")
        provider = object()

        report = await run_forward_phase1_from_provider(provider, as_of=as_of)

        args = fetch_chunked.await_args.args
        self.assertIs(args[0], provider)
        self.assertEqual(args[2], BAR_MINUTES)
        self.assertEqual(args[3], FORWARD_PERCEPTION_FETCH_START)
        self.assertLess(args[3], FORWARD_WARMUP_START)
        self.assertEqual(
            report["contract_metadata"]["perception_fetch_start"],
            FORWARD_PERCEPTION_FETCH_START.isoformat(),
        )
        evaluate_window.assert_called_once()


if __name__ == "__main__":
    unittest.main()
