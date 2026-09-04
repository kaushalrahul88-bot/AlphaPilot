from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from app.copper_direction_brain_v2_shadow_v2 import (
    CONTRACT_VERSION,
    evaluate_copper_direction_v2_shadow,
    integration_contract,
    option_participation_family,
)
from app.copper_direction_v2_prospective_store import (
    DEFAULT_CONTRACT_VERSION,
    SCHEMA_SQL,
    build_prospective_record,
)
from app.copper_option_participation_v1 import (
    OPTION_PARTICIPATION_TWO_BUCKETS_SQL,
    RULE_VERSION,
    build_option_participation_snapshot,
)


IST = ZoneInfo("Asia/Kolkata")


def _row(
    symbol: str,
    option_type: str,
    strike: float,
    premium: float,
    oi: float,
    *,
    bucket: datetime,
    underlying: float = 1000.0,
    collected: datetime | None = None,
    expiry: str = "2026-09-23",
    volume: float = 1000.0,
) -> dict:
    return {
        "trading_symbol": symbol,
        "expiry_date": expiry,
        "strike": strike,
        "option_type": option_type,
        "lot_size": 250,
        "sample_bucket_at": bucket,
        "observed_at": bucket + timedelta(seconds=10),
        "underlying_price": underlying,
        "last_price": premium,
        "volume": volume,
        "open_interest": oi,
        "bid_price": None,
        "ask_price": None,
        "collected_at": collected or bucket + timedelta(seconds=30),
    }


def _pair_rows(
    *,
    previous_bucket: datetime,
    current_bucket: datetime,
    ce_previous: tuple[float, float],
    ce_current: tuple[float, float],
    pe_previous: tuple[float, float],
    pe_current: tuple[float, float],
    previous_underlying: float = 1000.0,
    current_underlying: float = 1001.0,
) -> list[dict]:
    return [
        _row(
            "COPPER23SEP261000CE",
            "CE",
            1000,
            ce_previous[0],
            ce_previous[1],
            bucket=previous_bucket,
            underlying=previous_underlying,
        ),
        _row(
            "COPPER23SEP261000PE",
            "PE",
            1000,
            pe_previous[0],
            pe_previous[1],
            bucket=previous_bucket,
            underlying=previous_underlying,
        ),
        _row(
            "COPPER23SEP261000CE",
            "CE",
            1000,
            ce_current[0],
            ce_current[1],
            bucket=current_bucket,
            underlying=current_underlying,
        ),
        _row(
            "COPPER23SEP261000PE",
            "PE",
            1000,
            pe_current[0],
            pe_current[1],
            bucket=current_bucket,
            underlying=current_underlying,
        ),
    ]


def _board(participation_snapshot: dict, *, local: str = "BULLISH") -> dict:
    bullish = local == "BULLISH"
    structure = "UPTREND" if bullish else "DOWNTREND"
    ret15 = 0.2 if bullish else -0.2
    ret60 = 0.5 if bullish else -0.5
    return {
        "as_of": "2026-09-04T23:00:00+05:30",
        "groups": {
            "primary_market": {
                "MCX_COPPER": {
                    "status": "AVAILABLE",
                    "perception_status": "READY",
                    "perception_snapshot": {
                        "structure": structure,
                        "return_15m_pct": ret15,
                        "return_60m_pct": ret60,
                        "session_vwap_gap_pct": 0.1 if bullish else -0.1,
                        "opening_range_break": "ABOVE" if bullish else "BELOW",
                        "price_oi_state": "CONTEXT_ONLY",
                    },
                }
            },
            "option_market": {
                "MCX_COPPER_OPTION": {
                    "status": "AVAILABLE",
                    "first_seen_immutable": True,
                    "participation_snapshot": participation_snapshot,
                    "registered_participation_rule_version": RULE_VERSION,
                    "registered_change_directional_vote_allowed": True,
                    "raw_oi_directional_vote_allowed": False,
                }
            },
            "global_copper": {
                "COMEX_HG": {"status": "UNAVAILABLE", "reason": "NO_TAPE"},
                "LME_COPPER": {"status": "UNAVAILABLE", "reason": "NO_TAPE"},
            },
            "china_macro": {"MACRO_RELEASE": {"status": "UNAVAILABLE"}},
            "news": {"COPPER_NEWS": {"status": "UNAVAILABLE"}},
            "currency": {
                "USDINR_INTRADAY": {"status": "UNAVAILABLE"},
                "SLOW_REFERENCE_FX": {"status": "UNAVAILABLE"},
            },
            "positioning": {"CFTC_COPPER": {"status": "UNAVAILABLE"}},
        },
    }


class CopperOptionParticipationV1Tests(unittest.TestCase):
    def setUp(self):
        self.previous = datetime(2026, 9, 4, 22, 40, tzinfo=IST)
        self.current = self.previous + timedelta(minutes=10)
        self.as_of = self.current + timedelta(minutes=1)

    def test_sql_enforces_two_point_in_time_buckets(self):
        sql = " ".join(OPTION_PARTICIPATION_TWO_BUCKETS_SQL.upper().split())
        self.assertIn("LIMIT 2", sql)
        self.assertGreaterEqual(sql.count("SAMPLE_BUCKET_AT <= %S"), 2)
        self.assertGreaterEqual(sql.count("OBSERVED_AT <= %S"), 2)
        self.assertGreaterEqual(sql.count("COLLECTED_AT <= %S"), 2)

    def test_cross_side_new_oi_can_vote_bullish(self):
        rows = _pair_rows(
            previous_bucket=self.previous,
            current_bucket=self.current,
            ce_previous=(10.0, 100),
            ce_current=(11.0, 110),
            pe_previous=(12.0, 100),
            pe_current=(11.0, 120),
        )
        snapshot = build_option_participation_snapshot(rows, as_of=self.as_of)
        family = option_participation_family(_board(snapshot))

        self.assertEqual(snapshot["status"], "READY")
        self.assertEqual(family["stance"], "BULLISH")
        self.assertTrue(family["counts_for_direction"])
        self.assertEqual(family["state"], "CROSS_SIDE_NEW_OI_BULLISH")
        self.assertFalse(family["detail"]["raw_oi_level_directional_vote_allowed"])
        self.assertFalse(family["detail"]["underlying_price_direction_used"])

    def test_cross_side_new_oi_can_vote_bearish(self):
        rows = _pair_rows(
            previous_bucket=self.previous,
            current_bucket=self.current,
            ce_previous=(10.0, 100),
            ce_current=(9.0, 110),
            pe_previous=(12.0, 100),
            pe_current=(13.0, 120),
        )
        snapshot = build_option_participation_snapshot(rows, as_of=self.as_of)
        family = option_participation_family(_board(snapshot, local="BEARISH"))

        self.assertEqual(family["stance"], "BEARISH")
        self.assertTrue(family["counts_for_direction"])
        self.assertEqual(family["state"], "CROSS_SIDE_NEW_OI_BEARISH")

    def test_same_side_only_does_not_satisfy_cross_side_gate(self):
        rows = _pair_rows(
            previous_bucket=self.previous,
            current_bucket=self.current,
            ce_previous=(10.0, 100),
            ce_current=(11.0, 110),
            pe_previous=(12.0, 100),
            pe_current=(11.0, 90),
        )
        snapshot = build_option_participation_snapshot(rows, as_of=self.as_of)
        family = option_participation_family(_board(snapshot))

        self.assertEqual(family["stance"], "UNKNOWN")
        self.assertFalse(family["counts_for_direction"])
        self.assertEqual(family["state"], "INSUFFICIENT_CROSS_SIDE_NEW_OI_CONFIRMATION")

    def test_opposing_new_oi_evidence_forces_abstention(self):
        rows = _pair_rows(
            previous_bucket=self.previous,
            current_bucket=self.current,
            ce_previous=(10.0, 100),
            ce_current=(11.0, 110),
            pe_previous=(12.0, 100),
            pe_current=(13.0, 120),
        )
        snapshot = build_option_participation_snapshot(rows, as_of=self.as_of)
        family = option_participation_family(_board(snapshot))

        self.assertEqual(family["stance"], "UNKNOWN")
        self.assertFalse(family["counts_for_direction"])
        self.assertEqual(family["state"], "OPPOSING_NEW_OI_OPTION_EVIDENCE")

    def test_oi_flat_or_decreasing_never_creates_evidence(self):
        rows = _pair_rows(
            previous_bucket=self.previous,
            current_bucket=self.current,
            ce_previous=(10.0, 100),
            ce_current=(11.0, 100),
            pe_previous=(12.0, 100),
            pe_current=(11.0, 90),
        )
        snapshot = build_option_participation_snapshot(rows, as_of=self.as_of)
        family = option_participation_family(_board(snapshot))

        self.assertEqual(snapshot["eligible_new_oi_evidence"], 0)
        self.assertTrue(all(not row["eligible_new_oi_evidence"] for row in snapshot["contract_evidence"]))
        self.assertEqual(family["stance"], "UNKNOWN")
        self.assertFalse(family["counts_for_direction"])

    def test_underlying_direction_cannot_change_option_participation_vote(self):
        common = dict(
            previous_bucket=self.previous,
            current_bucket=self.current,
            ce_previous=(10.0, 100),
            ce_current=(11.0, 110),
            pe_previous=(12.0, 100),
            pe_current=(11.0, 120),
        )
        rising = build_option_participation_snapshot(
            _pair_rows(**common, previous_underlying=1000, current_underlying=1010),
            as_of=self.as_of,
        )
        falling = build_option_participation_snapshot(
            _pair_rows(**common, previous_underlying=1000, current_underlying=990),
            as_of=self.as_of,
        )
        rising_family = option_participation_family(_board(rising))
        falling_family = option_participation_family(_board(falling))

        self.assertNotEqual(
            rising["latest_underlying_price_context"],
            falling["latest_underlying_price_context"],
        )
        self.assertEqual(rising_family["stance"], falling_family["stance"])
        self.assertEqual(rising_family["state"], falling_family["state"])
        self.assertFalse(rising["underlying_price_direction_used"])
        self.assertFalse(falling["underlying_price_direction_used"])

    def test_bucket_gap_over_15_minutes_cannot_vote(self):
        current = self.previous + timedelta(minutes=20)
        rows = _pair_rows(
            previous_bucket=self.previous,
            current_bucket=current,
            ce_previous=(10.0, 100),
            ce_current=(11.0, 110),
            pe_previous=(12.0, 100),
            pe_current=(11.0, 120),
        )
        snapshot = build_option_participation_snapshot(
            rows,
            as_of=current + timedelta(minutes=1),
        )

        self.assertEqual(snapshot["status"], "WARMING_UP")
        self.assertEqual(snapshot["reason"], "PREVIOUS_OPTION_BUCKET_OUTSIDE_15_MINUTE_WINDOW")

    def test_late_current_bucket_is_not_retroactively_visible(self):
        late_collected = self.as_of + timedelta(minutes=1)
        rows = [
            _row(
                "COPPER23SEP261000CE",
                "CE",
                1000,
                10.0,
                100,
                bucket=self.previous,
            ),
            _row(
                "COPPER23SEP261000PE",
                "PE",
                1000,
                12.0,
                100,
                bucket=self.previous,
            ),
            _row(
                "COPPER23SEP261000CE",
                "CE",
                1000,
                11.0,
                110,
                bucket=self.current,
                collected=late_collected,
            ),
            _row(
                "COPPER23SEP261000PE",
                "PE",
                1000,
                11.0,
                120,
                bucket=self.current,
                collected=late_collected,
            ),
        ]
        snapshot = build_option_participation_snapshot(rows, as_of=self.as_of)

        self.assertEqual(snapshot["status"], "WARMING_UP")
        self.assertEqual(snapshot["reason"], "NO_PREVIOUS_IMMUTABLE_OPTION_BUCKET")
        self.assertEqual(snapshot["latest_bucket_at"], self.previous.isoformat())

    def test_two_independent_families_are_still_required_for_direction(self):
        rows = _pair_rows(
            previous_bucket=self.previous,
            current_bucket=self.current,
            ce_previous=(10.0, 100),
            ce_current=(11.0, 110),
            pe_previous=(12.0, 100),
            pe_current=(11.0, 120),
        )
        snapshot = build_option_participation_snapshot(rows, as_of=self.as_of)
        result = evaluate_copper_direction_v2_shadow(_board(snapshot, local="BULLISH"))

        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["direction_confidence"], "MODERATE")
        self.assertEqual(result["thesis_state"], "COHERENT_DIRECTION_THESIS")
        self.assertEqual(
            set(result["counted_families"]),
            {"LOCAL_STRUCTURE", "OPTION_PARTICIPATION"},
        )
        self.assertIsNone(result["current_mind_action"])
        self.assertFalse(result["setup_geometry_generated"])
        self.assertFalse(result["option_expression_generated"])
        self.assertEqual(result["decision_effect"], "NONE")
        self.assertFalse(result["live_execution_enabled"])
        self.assertFalse(result["broker_order_placement_enabled"])
        self.assertEqual(result["capital_committed"], 0)
        self.assertFalse(result["promotion_eligible"])

    def test_contract_version_is_explicit_and_old_default_remains_v1(self):
        contract = integration_contract()
        self.assertEqual(contract["version"], CONTRACT_VERSION)
        self.assertEqual(contract["option_participation_rule_version"], RULE_VERSION)
        self.assertTrue(contract["option_participation_requires_new_oi"])
        self.assertTrue(contract["option_participation_requires_cross_side_ce_pe"])
        self.assertFalse(contract["option_participation_oi_flat_or_decrease_vote_allowed"])
        self.assertFalse(contract["option_participation_underlying_direction_vote_allowed"])
        self.assertFalse(contract["raw_option_oi_directional_vote_allowed"])
        self.assertFalse(contract["promotion_allowed"])
        self.assertIn("ADD COLUMN IF NOT EXISTS CONTRACT_VERSION", SCHEMA_SQL.upper())
        self.assertEqual(DEFAULT_CONTRACT_VERSION, "COPPER_DIRECTION_BRAIN_V2_SHADOW_V1")

        rows = _pair_rows(
            previous_bucket=self.previous,
            current_bucket=self.current,
            ce_previous=(10.0, 100),
            ce_current=(11.0, 110),
            pe_previous=(12.0, 100),
            pe_current=(11.0, 120),
        )
        snapshot = build_option_participation_snapshot(rows, as_of=self.as_of)
        board = _board(snapshot)
        evaluation = evaluate_copper_direction_v2_shadow(board)
        record = build_prospective_record(
            board,
            evaluation,
            evaluated_at="2026-09-04T23:00:01+05:30",
        )
        self.assertEqual(record["contract_version"], CONTRACT_VERSION)
        self.assertEqual(
            record["evaluation_snapshot"]["integration_contract"]["version"],
            CONTRACT_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
