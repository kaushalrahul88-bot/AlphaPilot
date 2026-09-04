from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from app.crude_oil_mini_episode_ledger_v1 import analyze_episode_outcome, build_episode_capture
from app.crude_oil_mini_research_protocol_v1 import (
    BASELINE_ID,
    EXPECTED_CURRENT_MIND_MODE,
    PROTOCOL_ID,
    validate_baseline_result,
)


IST = ZoneInfo("Asia/Kolkata")


def _decision_result(action: str = "NO_TRADE") -> dict:
    return {
        "mode": EXPECTED_CURRENT_MIND_MODE,
        "click_at": "2026-09-04T15:00:30+05:30",
        "latest_completed_bar_available_at": "2026-09-04T15:00:00+05:30",
        "symbol": "CRUDEOILM",
        "trade_instrument": "OPTIONS_ONLY",
        "current_mind": {
            "action": action,
            "direction": "BULLISH" if action == "BUY_CE" else "BEARISH" if action == "BUY_PE" else None,
            "evidence_quality": "COHERENT" if action.startswith("BUY_") else "CONFLICTED",
            "entry_price": 101.0 if action == "BUY_CE" else 99.0 if action == "BUY_PE" else None,
            "stop_price": 99.0 if action == "BUY_CE" else 101.0 if action == "BUY_PE" else None,
            "target_price": 103.0 if action == "BUY_CE" else 97.0 if action == "BUY_PE" else None,
        },
        "integrated_v2_shadow": {
            "direction": "UNKNOWN",
            "confidence": "WEAK",
            "decision_effect": "NONE",
        },
        "execution": {
            "paper_signal_only": True,
            "live_execution_enabled": False,
            "broker_order_placement_enabled": False,
            "capital_committed": 0,
            "option_expression": None,
        },
        "journal": {"decision_fingerprint": "fingerprint-v1"},
    }


def _candles_for_atr() -> list[list]:
    start = datetime(2026, 9, 4, 13, 45, tzinfo=IST)
    rows = []
    close = 100.0
    for index in range(16):
        candle_at = start + timedelta(minutes=5 * index)
        open_price = close
        close = close + (0.25 if index % 2 == 0 else -0.1)
        rows.append([
            candle_at.isoformat(),
            open_price,
            max(open_price, close) + 1.0,
            min(open_price, close) - 1.0,
            close,
            1000 + index,
            None,
        ])
    return rows


def _bar(reference_at: datetime, minutes: int, *, high: float, low: float, close: float) -> dict:
    visible = reference_at + timedelta(minutes=minutes)
    return {
        "candle_at": visible - timedelta(minutes=5),
        "visible_at": visible,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100.0,
        "collected_at": visible + timedelta(seconds=20),
    }


def _episode(action: str = "NO_TRADE") -> dict:
    reference_at = datetime(2026, 9, 4, 15, 0, tzinfo=IST)
    return {
        "episode_id": "episode-test",
        "reference_at": reference_at,
        "click_at": reference_at + timedelta(seconds=30),
        "action": action,
        "reference_price": 100.0,
        "atr14": 2.0,
        "entry_price": 101.0 if action == "BUY_CE" else 99.0 if action == "BUY_PE" else None,
        "stop_price": 99.0 if action == "BUY_CE" else 101.0 if action == "BUY_PE" else None,
        "target_price": 103.0 if action == "BUY_CE" else 97.0 if action == "BUY_PE" else None,
        "option_premium_reference": 20.0 if action.startswith("BUY_") else None,
    }


class CrudeOilMiniEpisodeLedgerV1Tests(unittest.TestCase):
    def test_frozen_baseline_capture_is_immutable_and_pit_anchored(self):
        result = _decision_result("NO_TRADE")
        capture = build_episode_capture(
            result,
            _candles_for_atr(),
            captured_at="2026-09-04T15:00:31+05:30",
        )

        self.assertEqual(capture["baseline_id"], BASELINE_ID)
        self.assertEqual(capture["protocol_id"], PROTOCOL_ID)
        self.assertEqual(capture["action"], "NO_TRADE")
        self.assertEqual(capture["reference_at"].isoformat(), "2026-09-04T15:00:00+05:30")
        self.assertIsNotNone(capture["atr14"])
        self.assertGreater(capture["atr14"], 0)
        self.assertIn('"capture_rule":"IMMUTABLE_DECISION_BEFORE_FORWARD_OUTCOME"', capture["payload"])
        self.assertIs(capture["paper_signal_only"], True)
        self.assertIs(capture["live_execution_enabled"], False)
        self.assertIs(capture["broker_order_placement_enabled"], False)

    def test_frozen_baseline_rejects_execution_or_mode_drift(self):
        result = _decision_result("NO_TRADE")
        result["execution"]["live_execution_enabled"] = True
        with self.assertRaises(ValueError):
            validate_baseline_result(result)

        result = _decision_result("NO_TRADE")
        result["mode"] = "SOME_FUTURE_UNREGISTERED_MODE"
        with self.assertRaises(ValueError):
            validate_baseline_result(result)

    def test_abstention_diagnoses_clean_missed_bullish_expansion_at_15m(self):
        episode = _episode("NO_TRADE")
        reference = episode["reference_at"]
        candles = [
            _bar(reference, 5, high=101.0, low=99.8, close=100.8),
            _bar(reference, 10, high=102.5, low=100.3, close=102.0),
            _bar(reference, 15, high=104.0, low=101.5, close=103.5),
        ]
        outcome = analyze_episode_outcome(
            episode,
            candles,
            [],
            horizon_minutes=15,
            resolved_at=reference + timedelta(minutes=16),
        )

        self.assertEqual(outcome["resolution_status"], "RESOLVED")
        self.assertEqual(outcome["diagnosis"], "MISSED_BULLISH_CLEAN_EXPANSION")
        self.assertEqual(outcome["geometry_outcome"], "NOT_APPLICABLE")
        self.assertAlmostEqual(outcome["max_up_atr"], 2.0)
        self.assertAlmostEqual(outcome["max_down_atr"], 0.1)

    def test_buy_ce_geometry_resolves_target_first_without_hindsight_intrabar_order(self):
        episode = _episode("BUY_CE")
        reference = episode["reference_at"]
        candles = [
            _bar(reference, 5, high=101.2, low=100.2, close=101.0),
            _bar(reference, 10, high=102.2, low=100.7, close=102.0),
            _bar(reference, 15, high=103.2, low=101.4, close=103.0),
        ]
        outcome = analyze_episode_outcome(
            episode,
            candles,
            [],
            horizon_minutes=15,
            resolved_at=reference + timedelta(minutes=16),
        )

        self.assertEqual(outcome["geometry_outcome"], "TARGET_FIRST")
        self.assertEqual(outcome["diagnosis"], "TRADE_EPISODE")
        self.assertAlmostEqual(outcome["directional_favorable_points"], 3.2)
        self.assertAlmostEqual(outcome["directional_adverse_points"], 0.0)

    def test_directional_excursions_never_become_negative(self):
        episode = _episode("BUY_CE")
        reference = episode["reference_at"]
        candles = [
            _bar(reference, 5, high=102.0, low=100.5, close=101.5),
            _bar(reference, 10, high=102.5, low=100.4, close=102.0),
            _bar(reference, 15, high=102.8, low=100.3, close=102.5),
        ]
        outcome = analyze_episode_outcome(
            episode,
            candles,
            [],
            horizon_minutes=15,
            resolved_at=reference + timedelta(minutes=16),
        )
        self.assertEqual(outcome["max_down_points"], 0.0)
        self.assertEqual(outcome["directional_adverse_points"], 0.0)

    def test_same_bar_entry_and_exit_is_ambiguous_not_assumed_win(self):
        episode = _episode("BUY_CE")
        reference = episode["reference_at"]
        candles = [
            _bar(reference, 5, high=103.5, low=98.8, close=102.0),
            _bar(reference, 10, high=102.4, low=101.0, close=102.0),
            _bar(reference, 15, high=102.5, low=101.2, close=102.2),
        ]
        outcome = analyze_episode_outcome(
            episode,
            candles,
            [],
            horizon_minutes=15,
            resolved_at=reference + timedelta(minutes=16),
        )

        self.assertEqual(outcome["geometry_outcome"], "ENTRY_AND_EXIT_SAME_BAR_AMBIGUOUS")

    def test_outcome_does_not_resolve_before_preregistered_horizon(self):
        episode = _episode("NO_TRADE")
        reference = episode["reference_at"]
        candles = [_bar(reference, 5, high=101, low=99, close=100.5)]
        outcome = analyze_episode_outcome(
            episode,
            candles,
            [],
            horizon_minutes=15,
            resolved_at=reference + timedelta(minutes=10),
        )
        self.assertIsNone(outcome)

    def test_incomplete_short_horizon_never_uses_later_same_session_bars(self):
        episode = _episode("NO_TRADE")
        reference = episode["reference_at"]
        candles = [
            _bar(reference, 5, high=101.0, low=99.5, close=100.5),
            _bar(reference, 10, high=102.0, low=99.7, close=101.5),
            # The exact +15m endpoint is intentionally missing.
            _bar(reference, 20, high=120.0, low=98.0, close=118.0),
            _bar(reference, 25, high=125.0, low=97.0, close=124.0),
        ]
        outcome = analyze_episode_outcome(
            episode,
            candles,
            [],
            horizon_minutes=15,
            resolved_at=reference + timedelta(days=1),
        )

        self.assertEqual(outcome["resolution_status"], "TRUNCATED_OR_INCOMPLETE_HORIZON")
        self.assertAlmostEqual(outcome["max_up_points"], 2.0)
        self.assertAlmostEqual(outcome["max_down_points"], 0.5)
        self.assertNotEqual(outcome["underlying_end_close"], 118.0)
        self.assertNotEqual(outcome["underlying_end_close"], 124.0)

    def test_exact_contract_option_response_uses_only_observations_visible_by_horizon(self):
        episode = _episode("BUY_CE")
        episode["option_trading_symbol"] = "CRUDEOILM17SEP268500CE"
        reference = episode["reference_at"]
        candles = [
            _bar(reference, 5, high=101.2, low=100.0, close=101.0),
            _bar(reference, 10, high=102.0, low=100.8, close=101.8),
            _bar(reference, 15, high=102.5, low=101.0, close=102.0),
        ]
        option_rows = [
            {
                "sample_bucket_at": reference + timedelta(minutes=5),
                "observed_at": reference + timedelta(minutes=5),
                "collected_at": reference + timedelta(minutes=6),
                "last_price": 22.0,
            },
            {
                "sample_bucket_at": reference + timedelta(minutes=10),
                "observed_at": reference + timedelta(minutes=10),
                "collected_at": reference + timedelta(minutes=11),
                "last_price": 25.0,
            },
            {
                "sample_bucket_at": reference + timedelta(minutes=10),
                "observed_at": reference + timedelta(minutes=16),
                "collected_at": reference + timedelta(minutes=16),
                "last_price": 80.0,
            },
            {
                "sample_bucket_at": reference + timedelta(minutes=20),
                "observed_at": reference + timedelta(minutes=20),
                "collected_at": reference + timedelta(minutes=21),
                "last_price": 99.0,
            },
        ]
        outcome = analyze_episode_outcome(
            episode,
            candles,
            option_rows,
            horizon_minutes=15,
            resolved_at=reference + timedelta(minutes=22),
        )

        self.assertEqual(outcome["option_observations"], 2)
        self.assertAlmostEqual(outcome["option_end_premium"], 25.0)
        self.assertAlmostEqual(outcome["option_return_pct"], 25.0)
        self.assertAlmostEqual(outcome["option_max_premium"], 25.0)
        self.assertAlmostEqual(outcome["option_min_premium"], 22.0)


if __name__ == "__main__":
    unittest.main()
