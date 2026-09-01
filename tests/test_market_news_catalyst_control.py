from __future__ import annotations

import copy
import unittest

from app.market_news_catalyst_control import assess_catalyst_control, catalyst_control_context


def _reaction(direction="UP"):
    return {
        "coverage_status": "CLASSIFIABLE",
        "event": {
            "headline": "test event",
            "source": "Reuters",
            "stance": "BEARISH" if direction == "UP" else "BULLISH",
            "disposition": "CONTEXT_ONLY",
        },
        "window": {
            "reaction_anchor_timestamp": "2026-08-10T09:00:00+05:30",
            "pre_event": {"timestamp": "2026-08-10T08:55:00+05:30", "price": 100.0},
            "assimilation": {
                "timestamp": "2026-08-10T10:00:00+05:30",
                "price": 102.0 if direction == "UP" else 98.0,
            },
        },
        "materiality_qualified_path": {
            "observation_status": "OBSERVED",
            "qualified_path_state": "UP_FOLLOW_THROUGH" if direction == "UP" else "DOWN_FOLLOW_THROUGH",
            "qualified_directions": {
                "immediate": direction,
                "confirmation": direction,
                "assimilation": direction,
            },
        },
    }


def _candle(ts, close):
    return {"timestamp": ts, "close": close}


class MarketNewsCatalystControlTests(unittest.TestCase):
    def test_active_requires_retention_of_accepted_level(self):
        result = assess_catalyst_control(
            _reaction("UP"),
            [_candle("2026-08-10T10:00:00+05:30", 102.0), _candle("2026-08-10T10:30:00+05:30", 102.5)],
            click_timestamp="2026-08-10T10:30:00+05:30",
            market_structure="UPTREND",
        )
        self.assertEqual(result["state"], "CONTROL_ACTIVE")
        self.assertTrue(result["controls_direction"])
        self.assertEqual(result["direction"], "BULLISH")

    def test_retreat_between_acceptance_and_origin_is_assimilating(self):
        result = assess_catalyst_control(
            _reaction("UP"),
            [_candle("2026-08-10T10:00:00+05:30", 102.0), _candle("2026-08-10T11:00:00+05:30", 101.0)],
            click_timestamp="2026-08-10T11:00:00+05:30",
            market_structure="RANGE",
        )
        self.assertEqual(result["state"], "CONTROL_ASSIMILATING")
        self.assertFalse(result["controls_direction"])

    def test_opposite_structure_contests_before_origin_break(self):
        result = assess_catalyst_control(
            _reaction("UP"),
            [_candle("2026-08-10T10:00:00+05:30", 102.0), _candle("2026-08-10T11:00:00+05:30", 102.2)],
            click_timestamp="2026-08-10T11:00:00+05:30",
            market_structure="DOWNTREND",
        )
        self.assertEqual(result["state"], "CONTROL_CONTESTED")
        self.assertFalse(result["controls_direction"])

    def test_origin_break_latches_override_even_after_recovery(self):
        result = assess_catalyst_control(
            _reaction("UP"),
            [
                _candle("2026-08-10T10:00:00+05:30", 102.0),
                _candle("2026-08-10T11:00:00+05:30", 99.8),
                _candle("2026-08-10T12:00:00+05:30", 103.0),
            ],
            click_timestamp="2026-08-10T12:00:00+05:30",
            market_structure="UPTREND",
        )
        self.assertEqual(result["state"], "CONTROL_OVERRIDDEN")
        self.assertFalse(result["controls_direction"])
        self.assertEqual(result["override_seen_at"], "2026-08-10T11:00:00+05:30")

    def test_future_candles_cannot_change_point_in_time_state(self):
        base = [
            _candle("2026-08-10T10:00:00+05:30", 102.0),
            _candle("2026-08-10T10:30:00+05:30", 102.4),
        ]
        first = assess_catalyst_control(
            _reaction("UP"), base,
            click_timestamp="2026-08-10T10:30:00+05:30",
            market_structure="UPTREND",
        )
        future = copy.deepcopy(base) + [_candle("2026-08-10T11:00:00+05:30", 99.0)]
        second = assess_catalyst_control(
            _reaction("UP"), future,
            click_timestamp="2026-08-10T10:30:00+05:30",
            market_structure="UPTREND",
        )
        self.assertEqual(first, second)
        self.assertEqual(first["state"], "CONTROL_ACTIVE")

    def test_headline_stance_cannot_supply_or_reverse_direction(self):
        reaction = _reaction("UP")
        reaction["event"]["stance"] = "BEARISH"
        result = assess_catalyst_control(
            reaction,
            [_candle("2026-08-10T10:00:00+05:30", 102.0)],
            click_timestamp="2026-08-10T10:00:00+05:30",
        )
        self.assertEqual(result["direction"], "BULLISH")

    def test_context_fails_closed_when_completed_catalysts_conflict(self):
        up = _reaction("UP")
        down = _reaction("DOWN")
        context = catalyst_control_context(
            [up, down],
            [_candle("2026-08-10T10:00:00+05:30", 100.0)],
            click_timestamp="2026-08-10T10:00:00+05:30",
        )
        self.assertEqual(context["state"], "CONFLICTING_CATALYST_DIRECTIONS")
        self.assertEqual(context["direction"], "UNKNOWN")
        self.assertFalse(context["controls_direction"])

    def test_outside_frozen_horizon_is_not_controlling(self):
        result = assess_catalyst_control(
            _reaction("UP"),
            [_candle("2026-08-10T18:00:00+05:30", 104.0)],
            click_timestamp="2026-08-10T18:05:00+05:30",
        )
        self.assertEqual(result["state"], "OUTSIDE_OBSERVATION_HORIZON")
        self.assertFalse(result["controls_direction"])


if __name__ == "__main__":
    unittest.main()
