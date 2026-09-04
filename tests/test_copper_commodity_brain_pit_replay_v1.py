from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from app.copper_commodity_brain_pit_replay_v1 import (
    RULE_FREEZE_COMMIT,
    evaluate_copper_pit_replay,
    replay_contract,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _candles(count: int = 70):
    start = datetime(2026, 9, 1, 9, 0, tzinfo=IST)
    rows = []
    for i in range(count):
        level = 850.0 + i * 0.12
        rows.append([
            (start + timedelta(minutes=5 * i)).isoformat(),
            level,
            level + 0.18,
            level - 0.08,
            level + 0.10,
            1000.0 + i * 10.0,
            None,
        ])
    return rows


def _option_row(
    *,
    bucket: datetime,
    option_type: str,
    premium: float,
    oi: float,
    collected_offset_minutes: int = 2,
):
    strike = 850.0
    symbol = f"COPPER-TEST-{int(strike)}-{option_type}"
    observed = bucket + timedelta(minutes=1)
    collected = bucket + timedelta(minutes=collected_offset_minutes)
    return {
        "provider": "GROWW",
        "underlying_symbol": "COPPER",
        "exchange": "MCX",
        "segment": "COMMODITY",
        "trading_symbol": symbol,
        "groww_symbol": symbol,
        "expiry_date": "2026-09-30",
        "strike": strike,
        "option_type": option_type,
        "lot_size": 2500,
        "sample_bucket_at": bucket.isoformat(),
        "observed_at": observed.isoformat(),
        "underlying_price": 857.0,
        "last_price": premium,
        "volume": 100.0,
        "open_interest": oi,
        "bid_price": premium - 0.05,
        "ask_price": premium + 0.05,
        "collected_at": collected.isoformat(),
    }


def _bullish_option_rows():
    previous = datetime(2026, 9, 1, 13, 55, tzinfo=IST)
    current = datetime(2026, 9, 1, 14, 0, tzinfo=IST)
    return [
        _option_row(bucket=previous, option_type="CE", premium=5.0, oi=1000.0),
        _option_row(bucket=previous, option_type="PE", premium=5.0, oi=1000.0),
        _option_row(bucket=current, option_type="CE", premium=5.6, oi=1100.0),
        _option_row(bucket=current, option_type="PE", premium=4.4, oi=1100.0),
    ]


class CopperCommodityBrainPitReplayTests(unittest.TestCase):
    def test_completed_bar_only_and_replay_provenance(self):
        candles = _candles()
        # Add a deliberately absurd 14:05 bar. At 14:07 it is still open and must
        # not enter perception; 14:00 is the latest completed bar.
        click = datetime(2026, 9, 1, 14, 7, tzinfo=IST)
        result = evaluate_copper_pit_replay(
            candles=candles,
            option_rows=_bullish_option_rows(),
            click_at=click,
        )
        self.assertEqual(result["status"], "EVALUATED")
        self.assertEqual(
            result["underlying_candle_provenance"]["latest_visible_candle_at"],
            datetime(2026, 9, 1, 14, 0, tzinfo=IST).isoformat(),
        )
        self.assertEqual(result["evaluation_class"], "PIT_REPLAY")
        self.assertFalse(result["prospective"])
        self.assertFalse(result["eligible_for_prospective_memory"])
        self.assertEqual(result["brain_rule_freeze_commit"], RULE_FREEZE_COMMIT)

    def test_future_and_late_option_rows_cannot_change_decision(self):
        click = datetime(2026, 9, 1, 14, 7, tzinfo=IST)
        base_rows = _bullish_option_rows()
        baseline = evaluate_copper_pit_replay(
            candles=_candles(), option_rows=base_rows, click_at=click
        )

        future_bucket = datetime(2026, 9, 1, 14, 10, tzinfo=IST)
        poisoned = copy.deepcopy(base_rows)
        poisoned.extend(
            [
                _option_row(
                    bucket=future_bucket,
                    option_type="CE",
                    premium=1.0,
                    oi=99999.0,
                    collected_offset_minutes=2,
                ),
                _option_row(
                    bucket=future_bucket,
                    option_type="PE",
                    premium=50.0,
                    oi=99999.0,
                    collected_offset_minutes=2,
                ),
            ]
        )
        after_future = evaluate_copper_pit_replay(
            candles=_candles(), option_rows=poisoned, click_at=click
        )
        self.assertEqual(baseline["brain"]["direction"], after_future["brain"]["direction"])
        self.assertEqual(
            baseline["brain"]["supporting_families"],
            after_future["brain"]["supporting_families"],
        )

        # A row from the current bucket whose final stored collection occurs after
        # the click is invisible in its entirety because the generic table upserts.
        late = copy.deepcopy(base_rows)
        late[-1]["collected_at"] = datetime(2026, 9, 1, 14, 8, tzinfo=IST).isoformat()
        after_late = evaluate_copper_pit_replay(
            candles=_candles(), option_rows=late, click_at=click
        )
        self.assertLess(
            after_late["option_provenance"]["visible_rows"],
            baseline["option_provenance"]["visible_rows"],
        )

    def test_generic_option_tape_is_never_mislabeled_immutable(self):
        result = evaluate_copper_pit_replay(
            candles=_candles(),
            option_rows=_bullish_option_rows(),
            click_at=datetime(2026, 9, 1, 14, 7, tzinfo=IST),
        )
        self.assertEqual(
            result["option_provenance"]["source_table"],
            "commodity_option_snapshots",
        )
        self.assertFalse(result["option_provenance"]["first_seen_immutable"])
        self.assertTrue(result["option_provenance"]["upsert_semantics_acknowledged"])
        participation = result["brain"]["families"]["OPTION_PARTICIPATION"]["detail"]
        self.assertFalse(participation["underlying_price_direction_used"])

    def test_replay_contains_no_outcome_or_trade_geometry(self):
        result = evaluate_copper_pit_replay(
            candles=_candles(),
            option_rows=_bullish_option_rows(),
            click_at=datetime(2026, 9, 1, 14, 7, tzinfo=IST),
        )
        self.assertTrue(result["outcome_blind_at_evaluation"])
        self.assertFalse(result["historical_records_changed"])
        self.assertFalse(result["brain"]["setup_geometry_generated"])
        self.assertFalse(result["brain"]["option_expression_generated"])
        self.assertEqual(result["execution"]["capital_committed"], 0)
        flattened = repr(result).lower()
        for forbidden in ("target_first", "stop_first", "realized_r", "option_pnl"):
            self.assertNotIn(forbidden, flattened)

    def test_same_visible_inputs_are_deterministic(self):
        kwargs = {
            "candles": _candles(),
            "option_rows": _bullish_option_rows(),
            "click_at": datetime(2026, 9, 1, 14, 7, tzinfo=IST),
        }
        first = evaluate_copper_pit_replay(**kwargs)
        second = evaluate_copper_pit_replay(**kwargs)
        self.assertEqual(first, second)

    def test_contract_forbids_prospective_relabeling_and_rule_tuning(self):
        contract = replay_contract()
        self.assertFalse(contract["prospective"])
        self.assertFalse(contract["eligible_for_prospective_memory"])
        self.assertFalse(contract["outcomes_used_at_evaluation"])
        self.assertFalse(contract["generic_live_option_tape_immutable"])
        self.assertFalse(contract["replay_rule_tuning_allowed"])
        self.assertFalse(contract["persistence_side_effects"])
        self.assertFalse(contract["live_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
