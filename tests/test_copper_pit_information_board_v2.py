from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from app.copper_pit_information_board_v2 import (
    LATEST_VISIBLE_CONTRACT_SQL,
    OPTION_LATEST_BUCKET_SQL,
    build_information_board,
    summarize_china_macro,
    summarize_market_tape,
    summarize_option_tape,
    summarize_slow_context,
)
from app.historical_context import HistoricalContext


IST = ZoneInfo("Asia/Kolkata")


def _market_rows(start: datetime, count: int = 60):
    rows = []
    price = 900.0
    for index in range(count):
        stamp = start + timedelta(minutes=5 * index)
        price += 0.25
        rows.append([
            stamp.isoformat(),
            price - 0.15,
            price + 0.35,
            price - 0.40,
            price,
            1000 + index * 10,
            50000 + index * 5,
        ])
    return rows


def _option_row(
    symbol: str,
    option_type: str,
    strike: float,
    oi: float,
    *,
    bucket: datetime,
    collected: datetime,
):
    return {
        "trading_symbol": symbol,
        "expiry_date": "2026-09-30",
        "strike": strike,
        "option_type": option_type,
        "lot_size": 250,
        "sample_bucket_at": bucket,
        "observed_at": bucket + timedelta(seconds=10),
        "underlying_price": 905.0,
        "last_price": 12.5,
        "volume": 100,
        "open_interest": oi,
        "bid_price": 12.4,
        "ask_price": 12.6,
        "collected_at": collected,
    }


class CopperPITInformationBoardV2Tests(unittest.TestCase):
    def test_sql_requires_actual_availability_before_click(self):
        candle_sql = " ".join(LATEST_VISIBLE_CONTRACT_SQL.upper().split())
        option_sql = " ".join(OPTION_LATEST_BUCKET_SQL.upper().split())
        self.assertIn("CANDLE_AT + (TIMEFRAME_MINUTES * INTERVAL '1 MINUTE') <= %S", candle_sql)
        self.assertIn("COLLECTED_AT <= %S", candle_sql)
        self.assertGreaterEqual(option_sql.count("SAMPLE_BUCKET_AT <= %S"), 2)
        self.assertGreaterEqual(option_sql.count("OBSERVED_AT <= %S"), 2)
        self.assertGreaterEqual(option_sql.count("COLLECTED_AT <= %S"), 2)

    def test_market_tape_builds_perception_from_immutable_rows_only(self):
        start = datetime(2026, 9, 4, 9, 0, tzinfo=IST)
        rows = _market_rows(start, 60)
        as_of = start + timedelta(minutes=5 * 60)
        summary = summarize_market_tape(
            rows,
            trading_symbol="COPPER30SEP26FUT",
            as_of=as_of,
        )

        self.assertEqual(summary["status"], "AVAILABLE")
        self.assertTrue(summary["first_seen_immutable"])
        self.assertFalse(summary["historical_backfill_used"])
        self.assertFalse(summary["mutable_generic_fallback_used"])
        self.assertEqual(summary["perception_status"], "READY")
        self.assertIsInstance(summary["perception_snapshot"], dict)
        self.assertEqual(summary["current_mind_effect"], "NONE")
        self.assertEqual(summary["decision_effect"], "NONE")

    def test_market_tape_reports_warmup_instead_of_fabricating_snapshot(self):
        start = datetime(2026, 9, 4, 9, 0, tzinfo=IST)
        summary = summarize_market_tape(
            _market_rows(start, 20),
            trading_symbol="COPPER30SEP26FUT",
            as_of=start + timedelta(hours=2),
        )
        self.assertEqual(summary["perception_status"], "WARMING_UP")
        self.assertIsNone(summary["perception_snapshot"])

    def test_option_tape_excludes_rows_not_yet_collected(self):
        bucket = datetime(2026, 9, 4, 14, 0, tzinfo=IST)
        as_of = bucket + timedelta(minutes=3)
        rows = [
            _option_row("COPPER30SEP26900CE", "CE", 900, 1000, bucket=bucket, collected=bucket + timedelta(minutes=2)),
            _option_row("COPPER30SEP26900PE", "PE", 900, 1500, bucket=bucket, collected=bucket + timedelta(minutes=2)),
            _option_row("COPPER30SEP26910CE", "CE", 910, 999999, bucket=bucket, collected=bucket + timedelta(minutes=5)),
        ]
        summary = summarize_option_tape(rows, as_of=as_of)

        self.assertEqual(summary["status"], "AVAILABLE")
        self.assertEqual(summary["contracts_visible"], 2)
        self.assertEqual(summary["ce_open_interest"], 1000.0)
        self.assertEqual(summary["pe_open_interest"], 1500.0)
        self.assertEqual(summary["put_call_oi_ratio"], 1.5)
        self.assertFalse(summary["raw_oi_directional_vote_allowed"])
        self.assertFalse(summary["historical_backfill_used"])
        self.assertEqual(summary["decision_effect"], "NONE")

    def test_slow_fx_and_cftc_are_never_intraday_direction_creators(self):
        items = [
            HistoricalContext(
                context_id="FX1",
                commodity="COPPER",
                kind="FX",
                observed_at="2026-09-03T00:00:00+00:00",
                available_at="2026-09-04T00:00:00+00:00",
                source_name="Federal Reserve H.10",
                source_url="",
                source_tier="A_PRIMARY",
                values={"usdinr": 95.5},
                frequency="daily",
                notes="Daily reference only",
            ),
            HistoricalContext(
                context_id="CFTC1",
                commodity="COPPER",
                kind="POSITIONING",
                observed_at="2026-09-01T00:00:00+00:00",
                available_at="2026-09-04T20:30:00+00:00",
                source_name="CFTC",
                source_url="",
                source_tier="A_PRIMARY",
                values={"managed_money_long": 100},
                frequency="weekly",
                notes="Weekly positioning",
            ),
        ]
        as_of = datetime(2026, 9, 5, 3, 0, tzinfo=IST)
        summary = summarize_slow_context(items, as_of=as_of)

        self.assertFalse(summary["intraday_direction_creation_allowed"])
        self.assertFalse(summary["series"]["FX"]["directional_vote_allowed"])
        self.assertFalse(summary["series"]["POSITIONING"]["directional_vote_allowed"])
        self.assertEqual(summary["decision_effect"], "NONE")

    def test_china_macro_obeys_release_availability(self):
        before_aug17 = summarize_china_macro(as_of="2026-08-10T12:00:00+05:30")
        events_before = [row["value"]["event"] for row in before_aug17["records"]]
        self.assertEqual(events_before, ["CHINA_MANUFACTURING_PMI"])

        after_aug17 = summarize_china_macro(as_of="2026-08-17T08:00:00+05:30")
        events_after = {row["value"]["event"] for row in after_aug17["records"]}
        self.assertEqual(
            events_after,
            {
                "CHINA_INDUSTRIAL_VALUE_ADDED",
                "CHINA_FIXED_ASSET_INVESTMENT",
                "CHINA_RETAIL_SALES",
            },
        )
        self.assertFalse(after_aug17["directional_vote_allowed"])

    def test_board_keeps_unproven_external_feeds_unavailable_and_has_no_decision_effect(self):
        market = {"status": "AVAILABLE", "series": "MCX_COPPER"}
        options = {"status": "AVAILABLE", "series": "MCX_COPPER_OPTION"}
        slow = {
            "series": {
                "FX": {"status": "AVAILABLE"},
                "POSITIONING": {"status": "AVAILABLE"},
            }
        }
        macro = {"status": "AVAILABLE"}
        board = build_information_board(
            as_of="2026-09-04T18:00:00+05:30",
            market_tape=market,
            option_tape=options,
            slow_context=slow,
            china_macro=macro,
        )

        self.assertEqual(board["status"], "AVAILABLE")
        self.assertEqual(board["groups"]["global_copper"]["COMEX_HG"]["status"], "UNAVAILABLE")
        self.assertFalse(board["groups"]["global_copper"]["COMEX_HG"]["public_yahoo_substitution_allowed"])
        self.assertEqual(board["groups"]["news"]["COPPER_NEWS"]["status"], "UNAVAILABLE")
        self.assertFalse(board["groups"]["news"]["COPPER_NEWS"]["historical_gdelt_substitution_allowed"])
        self.assertEqual(board["sealed_copper_current_mind_effect"], "NONE")
        self.assertEqual(board["direction_v2_effect"], "NONE")
        self.assertFalse(board["production_rules_changed"])
        self.assertFalse(board["live_execution_enabled"])
        self.assertFalse(board["broker_order_placement_enabled"])
        self.assertFalse(board["promotion_eligible"])


if __name__ == "__main__":
    unittest.main()
