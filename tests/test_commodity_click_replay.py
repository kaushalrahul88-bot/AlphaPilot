import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from app.commodity_click_replay import (
    CLICK_TIMES,
    EXTENDED_SESSION_PAIRS,
    EXTENDED_TARGET_DATES,
    IDENTIFIED_SETUP_AUDIT_POINTS,
    VALIDATION_SESSION_PAIRS,
    VALIDATION_TARGET_DATES,
    WEEKLY_CLICK_TIMES,
    WEEKLY_SESSION_PAIRS,
    _click_timeline,
    _data_quality,
    _deduplicate_ready_setups,
    _extended_click_times,
    _validation_click_times,
    _historical_mtf,
    _summary,
    _weekly_summary,
    audit_identified_setups,
    run_frozen_weekly_click_backtest,
    run_frozen_extended_click_backtest,
    run_frozen_july_validation_backtest,
    validate_frozen_tuesday_phase_a_data,
)


IST = ZoneInfo("Asia/Kolkata")


def _rows(day, start_hour, count, minutes):
    start = datetime(day.year, day.month, day.day, start_hour, 0, tzinfo=IST)
    return [[(start + timedelta(minutes=minutes * index)).isoformat(), 100, 101, 99, 100, 10] for index in range(count)]


class CommodityClickReplayTests(unittest.IsolatedAsyncioTestCase):
    def test_frozen_click_times_are_unchanged(self):
        self.assertEqual(CLICK_TIMES, ("09:35", "10:55", "11:05", "13:20", "13:35", "15:15", "15:25", "16:15", "16:40", "18:35"))

    def test_weekly_protocol_has_five_sessions_and_hourly_clicks(self):
        self.assertEqual(WEEKLY_CLICK_TIMES, ("10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00"))
        self.assertEqual(len(WEEKLY_SESSION_PAIRS), 5)
        self.assertEqual(WEEKLY_SESSION_PAIRS[-1], (date(2026, 8, 21), date(2026, 8, 24)))
        self.assertEqual(len(WEEKLY_CLICK_TIMES) * len(WEEKLY_SESSION_PAIRS) * 2, 100)

    def test_extended_protocol_is_fixed_at_20_sessions_without_optional_stopping(self):
        self.assertEqual(len(EXTENDED_TARGET_DATES), 20)
        self.assertEqual(EXTENDED_TARGET_DATES[0], date(2026, 7, 29))
        self.assertEqual(EXTENDED_TARGET_DATES[-1], date(2026, 8, 25))
        self.assertEqual(EXTENDED_SESSION_PAIRS[0], (date(2026, 7, 28), date(2026, 7, 29)))
        self.assertEqual(EXTENDED_SESSION_PAIRS[-1], (date(2026, 8, 24), date(2026, 8, 25)))
        self.assertEqual(len(EXTENDED_SESSION_PAIRS) * len(WEEKLY_CLICK_TIMES) * 2, 400)

    def test_extended_clicks_are_reproducible_irregular_and_inside_live_window(self):
        first = _extended_click_times(date(2026, 7, 29))
        second = _extended_click_times(date(2026, 7, 30))
        self.assertEqual(first, _extended_click_times(date(2026, 7, 29)))
        self.assertEqual(len(first), 10)
        self.assertEqual(len(set(first)), 10)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, WEEKLY_CLICK_TIMES)
        self.assertTrue(all("10:00" <= value <= "22:00" for value in first))

    def test_july_validation_protocol_is_independently_frozen_at_20_sessions(self):
        self.assertEqual(len(VALIDATION_TARGET_DATES), 20)
        self.assertEqual(VALIDATION_TARGET_DATES[0], date(2026, 7, 1))
        self.assertEqual(VALIDATION_TARGET_DATES[-1], date(2026, 7, 28))
        self.assertEqual(VALIDATION_SESSION_PAIRS[0], (date(2026, 6, 30), date(2026, 7, 1)))
        self.assertEqual(VALIDATION_SESSION_PAIRS[-1], (date(2026, 7, 27), date(2026, 7, 28)))
        self.assertEqual(len(VALIDATION_SESSION_PAIRS) * 10 * 2, 400)

    def test_july_validation_clicks_are_reproducible_and_use_a_distinct_salt(self):
        target = date(2026, 7, 15)
        clicks = _validation_click_times(target)
        self.assertEqual(clicks, _validation_click_times(target))
        self.assertEqual(len(clicks), len(set(clicks)))
        self.assertTrue(all("10:00" <= value <= "22:00" for value in clicks))
        self.assertNotEqual(clicks, _extended_click_times(target))

    def test_weekly_ready_snapshots_are_deduplicated_without_hiding_clicks(self):
        decisions = [
            {"symbol": "CRUDEOIL", "target_date": "2026-08-18", "status": "READY", "action": "BUY PE", "outcome": {"r_multiple": 1.5}},
            {"symbol": "CRUDEOIL", "target_date": "2026-08-18", "status": "READY", "action": "BUY PE", "outcome": {"r_multiple": 1.2}},
            {"symbol": "CRUDEOIL", "target_date": "2026-08-18", "status": "NO_TRADE", "action": "NO TRADE", "outcome": None},
            {"symbol": "CRUDEOIL", "target_date": "2026-08-18", "status": "READY", "action": "BUY PE", "outcome": {"r_multiple": -1.0}},
            {"symbol": "CRUDEOIL", "target_date": "2026-08-19", "status": "READY", "action": "BUY PE", "outcome": {"r_multiple": 0.5}},
        ]
        _deduplicate_ready_setups(decisions)
        self.assertEqual(len(decisions), 5)
        self.assertEqual(sum(row["independent_setup"] for row in decisions), 3)
        self.assertEqual(decisions[0]["trade_id"], decisions[1]["trade_id"])
        self.assertIsNone(decisions[1]["outcome"])
        self.assertNotEqual(decisions[3]["trade_id"], decisions[4]["trade_id"])
        summary = _weekly_summary(decisions)
        self.assertEqual(summary["decision_snapshots"], 5)
        self.assertEqual(summary["independent_setups"], 3)
        self.assertFalse(summary["additive_pnl_available"])

    def test_open_and_ambiguous_outcomes_are_not_reported_as_resolved(self):
        decisions = [
            {"status": "READY", "independent_setup": True, "outcome": {"outcome": "SL_HIT", "r_multiple": -1.1}},
            {"status": "READY", "independent_setup": True, "outcome": {"outcome": "OPEN", "r_multiple": 0.0}},
            {"status": "READY", "independent_setup": True, "outcome": {"outcome": "AMBIGUOUS", "r_multiple": 0.0}},
        ]
        summary = _weekly_summary(decisions)
        self.assertEqual(summary["resolved_underlying_proxies"], 1)
        self.assertEqual(summary["open_underlying_proxies"], 1)
        self.assertEqual(summary["ambiguous_underlying_proxies"], 1)
        self.assertEqual(summary["negative"], 1)
        self.assertEqual(summary["average_resolved_r_proxy"], -1.1)
        phase_a_summary = _summary(decisions)
        self.assertEqual(phase_a_summary["resolved_underlying_proxies"], 1)
        self.assertEqual(phase_a_summary["open_underlying_proxies"], 1)
        self.assertEqual(phase_a_summary["ambiguous_underlying_proxies"], 1)

    def test_click_timeline_looks_like_successive_user_clicks(self):
        decisions = []
        for _, target in WEEKLY_SESSION_PAIRS:
            for click_text in WEEKLY_CLICK_TIMES:
                for symbol in ("CRUDEOIL", "NATURALGAS"):
                    decisions.append({
                        "target_date": target.isoformat(), "click_time_ist": click_text,
                        "symbol": symbol, "status": "NO_TRADE", "action": "NO TRADE",
                        "current_mtf_strength": 50.0, "blockers": ["Frozen gate failed."],
                    })
        timeline = _click_timeline(decisions)
        self.assertEqual(len(timeline), 50)
        self.assertEqual(timeline[0]["display_label"], "Clicked at 10:00 IST")
        self.assertEqual(timeline[1]["display_label"], "Clicked at 11:00 IST")
        self.assertEqual(len(timeline[0]["cards"]), 2)

    def test_historical_mtf_always_returns_unpackable_snapshot(self):
        day = date(2026, 8, 25)
        rows = _rows(day, 9, 120, 5)
        frames, plan, snapshot = _historical_mtf(
            {"5m": rows, "15m": rows, "1h": rows},
            datetime(2026, 8, 25, 18, 35, tzinfo=IST),
        )
        self.assertEqual(set(frames), {"5m", "15m", "1h"})
        self.assertIn(snapshot["action"], {"BUY", "SELL", "NO TRADE"})
        self.assertTrue(snapshot["fresh_market_data"])
        self.assertTrue(plan is None or isinstance(plan, dict))

    def test_summary_does_not_present_overlapping_clicks_as_additive_pnl(self):
        decisions = [
            {"status": "READY", "outcome": {"outcome": "T1_HIT", "r_multiple": 1.4}},
            {"status": "READY", "outcome": {"outcome": "SL_HIT", "r_multiple": -1.0}},
            {"status": "WAIT", "outcome": None},
            {"status": "NO_TRADE", "outcome": None},
        ]
        result = _summary(decisions)
        self.assertEqual(result["ready_setups"], 2)
        self.assertEqual(result["average_resolved_r_proxy"], 0.2)
        self.assertNotIn("total_r", result)
        self.assertTrue(result["non_additive"])

    def test_data_quality_rejects_missing_target_session(self):
        target = date(2026, 8, 25)
        prior = _rows(date(2026, 8, 24), 9, 120, 5)
        quality = _data_quality(
            "CRUDEOIL",
            {"trading_symbol": "CRUDE"},
            {"5m": prior, "15m": prior[:40], "1h": prior[:10]},
            {"benchmark_symbol": "WTI", "candles": []},
            {"status": "SETUP", "underlying_direction": "BEARISH"},
            target,
        )
        self.assertEqual(quality["status"], "INVALID_TARGET_SESSION_SLICE")
        self.assertEqual(quality["target_candles"], {"5m": 0, "15m": 0, "1h": 0})

    def test_data_quality_accepts_complete_target_and_comparison_sessions(self):
        target = date(2026, 8, 25)
        comparison_5m = []
        for offset in range(1, 6):
            comparison_5m.extend(_rows(target - timedelta(days=offset), 9, 120, 5))
        target_5m = _rows(target, 9, 120, 5)
        quality = _data_quality(
            "CRUDEOIL",
            {"trading_symbol": "CRUDE"},
            {
                "5m": comparison_5m + target_5m,
                "15m": _rows(target, 9, 40, 15),
                "1h": _rows(target, 9, 10, 60),
            },
            {"benchmark_symbol": "WTI", "candles": []},
            {"status": "SETUP", "underlying_direction": "BEARISH"},
            target,
        )
        self.assertEqual(quality["status"], "VALID")
        self.assertTrue(all(quality["checks"].values()))

    def test_groww_naive_rows_survive_validation_and_first_click_handoff(self):
        target = date(2026, 8, 25)
        comparison_5m = []
        for offset in range(1, 6):
            comparison_5m.extend(_rows(target - timedelta(days=offset), 9, 120, 5))
        target_rows = _rows(target, 9, 120, 5)
        groww_rows = [[row[0].split("+")[0], *row[1:], None] for row in comparison_5m + target_rows]
        quality = _data_quality(
            "CRUDEOIL",
            {"trading_symbol": "CRUDE"},
            {
                "5m": groww_rows,
                "15m": _rows(target, 9, 40, 15),
                "1h": _rows(target, 9, 10, 60),
            },
            {"benchmark_symbol": "WTI", "candles": []},
            {"status": "SETUP", "underlying_direction": "BEARISH"},
            target,
        )
        self.assertEqual(quality["status"], "VALID")
        self.assertTrue(quality["checks"]["first_click_gate_handoff"])
        self.assertEqual(quality["target_first_at"], "2026-08-25T09:00:00+05:30")

    def test_groww_epoch_rows_reach_phase_a_gate_handoff(self):
        target = date(2026, 8, 25)
        comparison_5m = []
        for offset in range(1, 6):
            comparison_5m.extend(_rows(target - timedelta(days=offset), 9, 120, 5))
        target_rows = _rows(target, 9, 120, 5)

        def epoch_rows(rows, milliseconds=False):
            output = []
            for row in rows:
                raw = int(datetime.fromisoformat(row[0]).timestamp())
                output.append([raw * 1000 if milliseconds else raw, *row[1:]])
            return output

        quality = _data_quality(
            "CRUDEOIL",
            {"trading_symbol": "CRUDE"},
            {
                "5m": epoch_rows(comparison_5m + target_rows),
                "15m": epoch_rows(_rows(target, 9, 40, 15), milliseconds=True),
                "1h": epoch_rows(_rows(target, 9, 10, 60)),
            },
            {"benchmark_symbol": "WTI", "candles": []},
            {"status": "SETUP", "underlying_direction": "BEARISH"},
            target,
        )
        self.assertEqual(quality["status"], "VALID")
        self.assertTrue(quality["checks"]["first_click_gate_handoff"])
        self.assertEqual(quality["target_candles"]["5m"], 120)

    async def test_data_validation_route_never_generates_trade_decisions(self):
        target = date(2026, 8, 25)
        comparison_5m = []
        for offset in range(1, 6):
            comparison_5m.extend(_rows(target - timedelta(days=offset), 9, 120, 5))
        five = comparison_5m + _rows(target, 9, 120, 5)
        fifteen = _rows(target, 9, 40, 15)
        hourly = _rows(target, 9, 10, 60)
        contract = {"trading_symbol": "TESTFUT", "tick_size": 1}
        previous = {"status": "SETUP", "underlying_direction": "BEARISH"}
        with (
            patch("app.commodity_click_replay.resolve_nearest_mcx_future", new=AsyncMock(side_effect=[contract, contract])),
            patch("app.commodity_click_replay._fetch_chunked", new=AsyncMock(side_effect=[five, fifteen, hourly, five, fifteen, hourly])),
            patch("app.commodity_click_replay.build_next_session_plan", return_value=previous),
        ):
            result = await validate_frozen_tuesday_phase_a_data(object())
        self.assertEqual(result["status"], "VALID")
        self.assertFalse(result["generates_trade_decisions"])
        self.assertNotIn("decisions", result)

    async def test_weekly_replay_returns_exactly_100_auditable_snapshots(self):
        days = [date(2026, 8, day) for day in range(3, 25) if date(2026, 8, day).weekday() < 5]
        five = [row for day in days for row in _rows(day, 9, 120, 5)]
        fifteen = [row for day in days for row in _rows(day, 9, 40, 15)]
        hourly = [row for day in days for row in _rows(day, 9, 10, 60)]
        contract = {"trading_symbol": "TESTFUT", "tick_size": 1}
        previous = {"status": "SETUP", "underlying_direction": "BEARISH"}
        frames = {key: {"signal": "SELL"} for key in ("5m", "15m", "1h")}
        plan = {"action": "SELL", "strength": 80.0, "entry": 100, "stop": 110, "target1": 85}
        brain = {
            "status": "NO_TRADE", "action": "NO TRADE", "underlying_direction": "BEARISH",
            "blockers": ["RVOL failed."], "gates": {"rvol": False},
        }
        fetches = [five, fifteen, hourly, five, fifteen, hourly]
        with (
            patch("app.commodity_click_replay.resolve_nearest_mcx_future", new=AsyncMock(side_effect=[contract, contract])),
            patch("app.commodity_click_replay._fetch_chunked", new=AsyncMock(side_effect=fetches)),
            patch("app.commodity_click_replay.fetch_benchmark_candles", new=AsyncMock(return_value={"benchmark_symbol": "TEST", "candles": []})),
            patch("app.commodity_click_replay.build_next_session_plan", return_value=previous),
            patch("app.commodity_click_replay._historical_mtf", return_value=(frames, plan, {"action": "SELL", "alpha_score": 80.0, "fresh_market_data": True})),
            patch("app.commodity_click_replay.benchmark_confirmation", return_value={"passed": True}),
            patch("app.commodity_click_replay.evaluate_commodity_click", return_value=brain),
        ):
            result = await run_frozen_weekly_click_backtest(object())
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(len(result["decisions"]), 100)
        self.assertEqual(len(result["click_timeline"]), 50)
        self.assertEqual(result["summary"]["decision_snapshots"], 100)
        self.assertEqual(len(result["click_schedule"]), 5)

    async def test_extended_replay_returns_exactly_400_auditable_snapshots(self):
        days = []
        cursor = date(2026, 7, 1)
        while cursor <= date(2026, 8, 25):
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        five = [row for day in days for row in _rows(day, 9, 120, 5)]
        fifteen = [row for day in days for row in _rows(day, 9, 40, 15)]
        hourly = [row for day in days for row in _rows(day, 9, 10, 60)]
        contract = {"trading_symbol": "TESTFUT", "tick_size": 1}
        previous = {"status": "SETUP", "underlying_direction": "BEARISH"}
        frames = {key: {"signal": "SELL"} for key in ("5m", "15m", "1h")}
        plan = {"action": "SELL", "strength": 80.0, "entry": 100, "stop": 110, "target1": 85}
        brain = {
            "status": "NO_TRADE", "action": "NO TRADE", "underlying_direction": "BEARISH",
            "blockers": ["RVOL failed."], "gates": {"rvol": False},
        }
        with (
            patch("app.commodity_click_replay.resolve_nearest_mcx_future", new=AsyncMock(side_effect=[contract, contract])),
            patch("app.commodity_click_replay._fetch_chunked", new=AsyncMock(side_effect=[five, fifteen, hourly, five, fifteen, hourly])),
            patch("app.commodity_click_replay.fetch_benchmark_candles", new=AsyncMock(return_value={"benchmark_symbol": "TEST", "candles": []})),
            patch("app.commodity_click_replay.build_next_session_plan", return_value=previous),
            patch("app.commodity_click_replay._historical_mtf", return_value=(frames, plan, {"action": "SELL", "alpha_score": 80.0, "fresh_market_data": True})),
            patch("app.commodity_click_replay.benchmark_confirmation", return_value={"passed": True}),
            patch("app.commodity_click_replay.evaluate_commodity_click", return_value=brain),
        ):
            result = await run_frozen_extended_click_backtest(object())
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["mode"], "COMMODITY_FROZEN_20_SESSION_CLICK_BACKTEST_V1")
        self.assertEqual(len(result["decisions"]), 400)
        self.assertEqual(len(result["click_timeline"]), 200)
        self.assertEqual(result["summary"]["decision_snapshots"], 400)
        self.assertEqual(len(result["click_schedule"]), 20)
        self.assertEqual(len(result["click_schedule"][0]["click_times_ist"]), 10)
        self.assertNotEqual(
            result["click_schedule"][0]["click_times_ist"],
            result["click_schedule"][1]["click_times_ist"],
        )

    async def test_july_validation_replay_returns_exactly_400_auditable_snapshots(self):
        days = []
        cursor = date(2026, 6, 15)
        while cursor <= date(2026, 7, 28):
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        five = [row for day in days for row in _rows(day, 9, 120, 5)]
        fifteen = [row for day in days for row in _rows(day, 9, 40, 15)]
        hourly = [row for day in days for row in _rows(day, 9, 10, 60)]
        contract = {"trading_symbol": "TESTFUT", "tick_size": 1}
        previous = {"status": "SETUP", "underlying_direction": "BEARISH"}
        frames = {key: {"signal": "SELL"} for key in ("5m", "15m", "1h")}
        plan = {"action": "SELL", "strength": 80.0, "entry": 100, "stop": 110, "target1": 85}
        brain = {
            "status": "NO_TRADE", "action": "NO TRADE", "underlying_direction": "BEARISH",
            "blockers": ["RVOL failed."], "gates": {"rvol": False},
        }
        with (
            patch("app.commodity_click_replay.resolve_nearest_mcx_future", new=AsyncMock(side_effect=[contract, contract])),
            patch("app.commodity_click_replay._fetch_chunked", new=AsyncMock(side_effect=[five, fifteen, hourly, five, fifteen, hourly])),
            patch("app.commodity_click_replay.fetch_benchmark_candles", new=AsyncMock(return_value={"benchmark_symbol": "TEST", "candles": []})),
            patch("app.commodity_click_replay.build_next_session_plan", return_value=previous),
            patch("app.commodity_click_replay._historical_mtf", return_value=(frames, plan, {"action": "SELL", "alpha_score": 80.0, "fresh_market_data": True})),
            patch("app.commodity_click_replay.benchmark_confirmation", return_value={"passed": True}),
            patch("app.commodity_click_replay.evaluate_commodity_click", return_value=brain),
        ):
            result = await run_frozen_july_validation_backtest(object())
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["mode"], "COMMODITY_FROZEN_JULY_VALIDATION_BACKTEST_V1")
        self.assertEqual(len(result["decisions"]), 400)
        self.assertEqual(len(result["click_timeline"]), 200)
        self.assertEqual(result["summary"]["decision_snapshots"], 400)
        self.assertEqual(len(result["click_schedule"]), 20)

    async def test_identified_setup_audit_recomputes_only_three_points_without_outcomes(self):
        days = []
        cursor = date(2026, 7, 20)
        while cursor <= date(2026, 8, 25):
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        five = [row for day in days for row in _rows(day, 9, 120, 5)]
        fifteen = [row for day in days for row in _rows(day, 9, 40, 15)]
        hourly = [row for day in days for row in _rows(day, 9, 10, 60)]
        contract = {"trading_symbol": "TESTFUT", "tick_size": 1}
        previous = {"status": "SETUP", "underlying_direction": "BEARISH", "features": {"directional_score": -3, "votes": {}}}
        frames = {key: {"signal": "SELL", "market_structure": "DOWNTREND"} for key in ("5m", "15m", "1h")}
        brain = {
            "status": "READY", "action": "BUY PE", "underlying_direction": "BEARISH",
            "blockers": [], "gates": {"alignment": {"passed": True}},
        }
        with (
            patch("app.commodity_click_replay.resolve_nearest_mcx_future", new=AsyncMock(side_effect=[contract, contract])),
            patch("app.commodity_click_replay._fetch_chunked", new=AsyncMock(side_effect=[five, fifteen, hourly, five, fifteen, hourly])),
            patch("app.commodity_click_replay.fetch_benchmark_candles", new=AsyncMock(return_value={"benchmark_symbol": "TEST", "candles": []})),
            patch("app.commodity_click_replay.build_next_session_plan", return_value=previous),
            patch("app.commodity_click_replay._historical_mtf", return_value=(frames, None, {"action": "SELL", "alpha_score": 80.0, "fresh_market_data": True})),
            patch("app.commodity_click_replay.benchmark_confirmation", return_value={"passed": True}),
            patch("app.commodity_click_replay.evaluate_commodity_click", return_value=brain),
        ):
            result = await audit_identified_setups(object())
        self.assertEqual(len(IDENTIFIED_SETUP_AUDIT_POINTS), 3)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(len(result["records"]), 3)
        self.assertFalse(result["outcomes_scored"])
        self.assertFalse(result["performance_statistics_generated"])
        self.assertFalse(result["full_backtest_rerun"])
        self.assertTrue(all("outcome" not in record for record in result["records"]))


if __name__ == "__main__":
    unittest.main()
