from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.crypto_btc_pit_replay_audit import (
    CONTINUOUS_REPLAY_SUMMARY_SQL,
    QUOTE_COMPLETENESS_SQL,
    REPLAY_SUMMARY_SQL,
    architecture_contract,
    audit_replay_points,
    build_continuous_replay_summary_sql,
    is_fresh,
    latest_contiguous_segment,
    latest_shared_continuity_window,
    quote_is_complete,
    select_latest_at_or_before,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)


class CryptoBtcPitReplayAuditTests(unittest.TestCase):
    def test_future_row_is_never_selected(self):
        rows = [
            {"snapshot_id": "past", "first_seen_at": T0 - timedelta(seconds=30)},
            {"snapshot_id": "future", "first_seen_at": T0 + timedelta(microseconds=1)},
        ]
        selected = select_latest_at_or_before(rows, T0, id_key="snapshot_id")
        self.assertIsNotNone(selected)
        self.assertEqual(selected.source_id, "past")
        self.assertLessEqual(selected.selected_at, T0)

    def test_exact_replay_timestamp_is_allowed(self):
        selected = select_latest_at_or_before(
            [{"snapshot_id": "exact", "first_seen_at": T0}],
            T0,
            id_key="snapshot_id",
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.source_id, "exact")
        self.assertEqual(selected.age_seconds, 0)

    def test_equal_timestamp_selection_is_deterministic(self):
        rows = [
            {"snapshot_id": "a", "first_seen_at": T0},
            {"snapshot_id": "b", "first_seen_at": T0},
        ]
        forward = select_latest_at_or_before(rows, T0, id_key="snapshot_id")
        reverse = select_latest_at_or_before(reversed(rows), T0, id_key="snapshot_id")
        self.assertEqual(forward.source_id, "b")
        self.assertEqual(reverse.source_id, "b")

    def test_freshness_is_diagnostic_and_age_bounded(self):
        selected = select_latest_at_or_before(
            [{"snapshot_id": "old", "first_seen_at": T0 - timedelta(seconds=121)}],
            T0,
            id_key="snapshot_id",
        )
        self.assertFalse(is_fresh(selected, 120))
        self.assertTrue(is_fresh(selected, 300))

    def test_quote_completeness_requires_execution_research_fields(self):
        complete = {
            "best_bid": 100,
            "best_ask": 102,
            "mark_price": 101,
            "greeks": {"delta": 0.5},
            "open_interest": 50,
            "volume": 10,
        }
        self.assertTrue(quote_is_complete(complete))
        incomplete = {**complete, "best_ask": None}
        self.assertFalse(quote_is_complete(incomplete))

    def test_audit_uses_only_latest_available_inputs(self):
        replay_times = [T0, T0 + timedelta(minutes=1)]
        delta = [
            {"snapshot_id": "d0", "first_seen_at": T0 - timedelta(seconds=20)},
            {"snapshot_id": "d1", "first_seen_at": T0 + timedelta(seconds=30)},
        ]
        pit = [
            {"natural_key": "p0", "first_seen_at": T0 - timedelta(seconds=10)},
            {"natural_key": "p1", "first_seen_at": T0 + timedelta(seconds=40)},
        ]
        result = audit_replay_points(replay_times, delta, pit)
        self.assertEqual(result["lookahead_violations"], 0)
        self.assertEqual(result["points"][0]["delta"].source_id, "d0")
        self.assertEqual(result["points"][0]["pit"].source_id, "p0")
        self.assertEqual(result["points"][1]["delta"].source_id, "d1")
        self.assertEqual(result["points"][1]["pit"].source_id, "p1")
        self.assertTrue(result["diagnostic_only"])

    def test_latest_contiguous_segment_starts_after_last_large_gap(self):
        rows = [
            {"first_seen_at": T0 - timedelta(minutes=20)},
            {"first_seen_at": T0 - timedelta(minutes=19)},
            {"first_seen_at": T0 - timedelta(minutes=4)},
            {"first_seen_at": T0 - timedelta(minutes=3)},
            {"first_seen_at": T0 - timedelta(minutes=2)},
        ]
        segment = latest_contiguous_segment(rows, max_gap_seconds=300)
        self.assertEqual(
            segment,
            (T0 - timedelta(minutes=4), T0 - timedelta(minutes=2)),
        )

    def test_latest_shared_continuity_window_intersects_current_segments(self):
        delta = [
            {"first_seen_at": T0 - timedelta(minutes=30)},
            {"first_seen_at": T0 - timedelta(minutes=29)},
            {"first_seen_at": T0 - timedelta(minutes=4)},
            {"first_seen_at": T0 - timedelta(minutes=3)},
            {"first_seen_at": T0 - timedelta(minutes=2)},
        ]
        pit = [
            {"first_seen_at": T0 - timedelta(minutes=25)},
            {"first_seen_at": T0 - timedelta(minutes=24)},
            {"first_seen_at": T0 - timedelta(minutes=4, seconds=30)},
            {"first_seen_at": T0 - timedelta(minutes=3, seconds=30)},
            {"first_seen_at": T0 - timedelta(minutes=1, seconds=30)},
        ]
        window = latest_shared_continuity_window(
            delta,
            pit,
            max_gap_seconds=300,
        )
        self.assertIsNotNone(window)
        self.assertEqual(window.start_at, T0 - timedelta(minutes=4))
        self.assertEqual(window.end_at, T0 - timedelta(minutes=2))
        self.assertEqual(window.max_gap_seconds, 300)
        self.assertEqual(window.span_seconds, 120)

    def test_continuity_window_returns_none_without_both_lanes(self):
        self.assertIsNone(
            latest_shared_continuity_window(
                [{"first_seen_at": T0}],
                [],
                max_gap_seconds=300,
            )
        )

    def test_continuity_threshold_and_grid_must_be_positive(self):
        with self.assertRaises(ValueError):
            latest_contiguous_segment(
                [{"first_seen_at": T0}],
                max_gap_seconds=0,
            )
        with self.assertRaises(ValueError):
            build_continuous_replay_summary_sql(max_gap_seconds=-1)
        with self.assertRaises(ValueError):
            build_continuous_replay_summary_sql(grid_seconds=0)

    def test_continuity_sql_is_parameterized_by_validated_integers(self):
        sql = build_continuous_replay_summary_sql(
            max_gap_seconds=600,
            grid_seconds=120,
        )
        self.assertIn("> 600", sql)
        self.assertIn("INTERVAL '120 seconds'", sql)
        self.assertIn("600::BIGINT AS max_gap_seconds", sql)
        self.assertIn("120::BIGINT AS grid_seconds", sql)

    def test_sql_is_select_only_and_does_not_read_resolution_tables(self):
        for sql in (
            REPLAY_SUMMARY_SQL,
            QUOTE_COMPLETENESS_SQL,
            CONTINUOUS_REPLAY_SUMMARY_SQL,
        ):
            lowered = sql.lower()
            self.assertTrue(lowered.lstrip().startswith("with"))
            for forbidden_statement in (
                "insert ",
                "update ",
                "delete ",
                "alter ",
                "drop ",
            ):
                self.assertNotIn(forbidden_statement, lowered)
            self.assertNotIn(
                "crypto_btc_prospective_thesis_resolutions_v1",
                lowered,
            )
            self.assertNotIn(
                "crypto_btc_prospective_thesis_decisions_v1",
                lowered,
            )
            self.assertNotIn("crypto_btc_live_shadow_clicks_v1", lowered)

    def test_architecture_contract_keeps_replay_research_only(self):
        contract = architecture_contract()
        self.assertTrue(contract["read_only"])
        self.assertFalse(contract["database_writes"])
        self.assertFalse(contract["decisions_created"])
        self.assertFalse(contract["prospective_outcomes_used_as_input"])
        self.assertFalse(contract["prospective_resolutions_used_as_input"])
        self.assertFalse(
            contract["diagnostic_freshness_cutoffs_are_strategy_policy"]
        )
        self.assertFalse(contract["continuity_gap_threshold_is_strategy_policy"])
        self.assertTrue(contract["continuity_window_is_diagnostic_only"])
        self.assertFalse(contract["live_execution"])
        self.assertEqual(contract["capital_committed"], 0)
        self.assertTrue(contract["options_and_futures_trade_generation_separate"])

    def test_naive_timestamps_fail_closed(self):
        naive = datetime(2026, 9, 6, 8, 0)
        with self.assertRaises(ValueError):
            select_latest_at_or_before(
                [{"snapshot_id": "x", "first_seen_at": naive}],
                T0,
                id_key="snapshot_id",
            )
        with self.assertRaises(ValueError):
            latest_contiguous_segment(
                [{"first_seen_at": naive}],
                max_gap_seconds=300,
            )


if __name__ == "__main__":
    unittest.main()
