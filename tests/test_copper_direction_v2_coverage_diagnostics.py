from __future__ import annotations

import unittest

from app.copper_direction_v2_prospective_store import (
    COVERAGE_ROWS_SQL,
    OPTION_PARTICIPATION_RULE_VERSION,
    summarize_prospective_coverage_rows,
)


def _family(name, stance="UNKNOWN", counts=False, state="UNKNOWN", detail=None):
    return {
        "family": name,
        "causal_origin": f"{name}_ORIGIN",
        "stance": stance,
        "counts_for_direction": counts,
        "state": state,
        "detail": detail or {},
    }


def _row(
    *,
    contract_version,
    board_as_of,
    direction,
    thesis_state,
    counted,
    supporting=None,
    opposing=None,
    local=None,
    option=None,
):
    families = {
        "LOCAL_STRUCTURE": local or _family("LOCAL_STRUCTURE"),
        "OPTION_PARTICIPATION": option or _family("OPTION_PARTICIPATION"),
        "GLOBAL_COPPER": _family(
            "GLOBAL_COPPER", state="LICENSED_FIRST_SEEN_GLOBAL_TAPE_NOT_CONNECTED"
        ),
        "CHINA_DEMAND": _family("CHINA_DEMAND", state="MACRO_UNAVAILABLE"),
        "EVENT_REACTION": _family(
            "EVENT_REACTION", state="NO_PROSPECTIVE_FIRST_DETECTED_NEWS"
        ),
        "EXPERIENCE_MEMORY": _family(
            "EXPERIENCE_MEMORY",
            state="NO_REGISTERED_PROSPECTIVE_DIRECTION_MEMORY_CONNECTED",
        ),
    }
    return {
        "contract_version": contract_version,
        "board_as_of": board_as_of,
        "direction": direction,
        "thesis_state": thesis_state,
        "supporting_families": supporting or [],
        "opposing_families": opposing or [],
        "counted_families": counted,
        "families": families,
    }


class CopperDirectionV2CoverageDiagnosticsTests(unittest.TestCase):
    def test_contracts_are_separated_and_existing_summary_is_preserved(self):
        rows = [
            _row(
                contract_version="COPPER_DIRECTION_BRAIN_V2_SHADOW_V1",
                board_as_of="2026-09-04T23:23:44+05:30",
                direction="UNKNOWN",
                thesis_state="INSUFFICIENT_INDEPENDENT_CONFIRMATION",
                counted=[],
                local=_family(
                    "LOCAL_STRUCTURE",
                    state="INTERNAL_LOCAL_CONTRADICTION",
                ),
                option=_family(
                    "OPTION_PARTICIPATION",
                    state="RAW_OPTION_POSITIONING_CONTEXT_ONLY",
                ),
            ),
            _row(
                contract_version="COPPER_DIRECTION_BRAIN_V2_SHADOW_V2",
                board_as_of="2026-09-07T09:20:00+05:30",
                direction="BULLISH",
                thesis_state="COHERENT_DIRECTION_THESIS",
                counted=["LOCAL_STRUCTURE", "OPTION_PARTICIPATION"],
                supporting=["LOCAL_STRUCTURE", "OPTION_PARTICIPATION"],
                local=_family(
                    "LOCAL_STRUCTURE",
                    stance="BULLISH",
                    counts=True,
                    state="STRUCTURE_CONFIRMED_BY_MOMENTUM",
                ),
                option=_family(
                    "OPTION_PARTICIPATION",
                    stance="BULLISH",
                    counts=True,
                    state="CROSS_SIDE_NEW_OI_BULLISH",
                    detail={"rule_version": OPTION_PARTICIPATION_RULE_VERSION},
                ),
            ),
        ]

        result = summarize_prospective_coverage_rows(rows)

        self.assertEqual(result["evaluations"], 2)
        self.assertEqual(result["directional_evaluations"], 1)
        self.assertEqual(result["abstentions"], 1)
        self.assertEqual(result["directional_coverage_pct"], 50.0)
        self.assertEqual(
            result["by_contract_version"],
            {
                "COPPER_DIRECTION_BRAIN_V2_SHADOW_V1": 1,
                "COPPER_DIRECTION_BRAIN_V2_SHADOW_V2": 1,
            },
        )
        self.assertEqual(result["by_direction"], {"UNKNOWN": 1, "BULLISH": 1})

        v2 = result["contract_diagnostics"]["COPPER_DIRECTION_BRAIN_V2_SHADOW_V2"]
        self.assertEqual(v2["evaluations"], 1)
        self.assertEqual(v2["directional_coverage_pct"], 100.0)
        self.assertEqual(v2["evaluations_with_at_least_two_counted_families"], 1)
        self.assertEqual(v2["at_least_two_counted_families_pct"], 100.0)
        self.assertEqual(v2["family_counted_vote_frequency"]["LOCAL_STRUCTURE"], 1)
        self.assertEqual(v2["family_counted_vote_frequency"]["OPTION_PARTICIPATION"], 1)
        self.assertEqual(
            v2["supporting_family_combinations"],
            {"LOCAL_STRUCTURE+OPTION_PARTICIPATION": 1},
        )

    def test_family_stance_state_conflict_and_option_readiness_are_descriptive(self):
        option_ready_bearish = _family(
            "OPTION_PARTICIPATION",
            stance="BEARISH",
            counts=True,
            state="CROSS_SIDE_NEW_OI_BEARISH",
            detail={"rule_version": OPTION_PARTICIPATION_RULE_VERSION},
        )
        option_not_ready = _family(
            "OPTION_PARTICIPATION",
            stance="UNKNOWN",
            counts=False,
            state="OPTION_PARTICIPATION_NOT_READY",
            detail={
                "rule_version": OPTION_PARTICIPATION_RULE_VERSION,
                "reason": "NO_PREVIOUS_VISIBLE_BUCKET",
            },
        )
        rows = [
            _row(
                contract_version="COPPER_DIRECTION_BRAIN_V2_SHADOW_V2",
                board_as_of="2026-09-07T09:35:00+05:30",
                direction="UNKNOWN",
                thesis_state="INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION",
                counted=["LOCAL_STRUCTURE", "OPTION_PARTICIPATION"],
                opposing=["LOCAL_STRUCTURE", "OPTION_PARTICIPATION"],
                local=_family(
                    "LOCAL_STRUCTURE",
                    stance="BULLISH",
                    counts=True,
                    state="STRUCTURE_CONFIRMED_BY_MOMENTUM",
                ),
                option=option_ready_bearish,
            ),
            _row(
                contract_version="COPPER_DIRECTION_BRAIN_V2_SHADOW_V2",
                board_as_of="2026-09-07T09:50:00+05:30",
                direction="UNKNOWN",
                thesis_state="INSUFFICIENT_INDEPENDENT_CONFIRMATION",
                counted=["LOCAL_STRUCTURE"],
                supporting=["LOCAL_STRUCTURE"],
                local=_family(
                    "LOCAL_STRUCTURE",
                    stance="BEARISH",
                    counts=True,
                    state="STRUCTURE_ONLY",
                ),
                option=option_not_ready,
            ),
        ]

        diagnostic = summarize_prospective_coverage_rows(rows)["contract_diagnostics"][
            "COPPER_DIRECTION_BRAIN_V2_SHADOW_V2"
        ]

        self.assertEqual(diagnostic["by_thesis_state"]["INDEPENDENT_CAUSAL_ORIGIN_CONTRADICTION"], 1)
        self.assertEqual(
            diagnostic["opposing_family_combinations"],
            {"LOCAL_STRUCTURE+OPTION_PARTICIPATION": 1},
        )
        self.assertEqual(
            diagnostic["family_stance_distribution"]["LOCAL_STRUCTURE"],
            {"BULLISH": 1, "BEARISH": 1},
        )
        self.assertEqual(
            diagnostic["family_state_distribution"]["OPTION_PARTICIPATION"],
            {
                "CROSS_SIDE_NEW_OI_BEARISH": 1,
                "OPTION_PARTICIPATION_NOT_READY": 1,
            },
        )
        option = diagnostic["option_participation"]
        self.assertEqual(option["rule_version_observations"], 2)
        self.assertEqual(option["ready_evaluations"], 1)
        self.assertEqual(option["readiness_pct"], 50.0)
        self.assertEqual(option["vote_evaluations"], 1)
        self.assertEqual(option["vote_rate_pct"], 50.0)

    def test_directional_support_combinations_exclude_abstentions(self):
        result = summarize_prospective_coverage_rows([
            _row(
                contract_version="COPPER_DIRECTION_BRAIN_V2_SHADOW_V2",
                board_as_of="2026-09-07T10:05:00+05:30",
                direction="UNKNOWN",
                thesis_state="INSUFFICIENT_INDEPENDENT_CONFIRMATION",
                counted=["LOCAL_STRUCTURE"],
                supporting=["LOCAL_STRUCTURE"],
                local=_family(
                    "LOCAL_STRUCTURE",
                    stance="BULLISH",
                    counts=True,
                    state="STRUCTURE_ONLY",
                ),
            )
        ])
        diagnostic = result["contract_diagnostics"]["COPPER_DIRECTION_BRAIN_V2_SHADOW_V2"]
        self.assertEqual(diagnostic["supporting_family_combinations"], {})

    def test_coverage_query_is_outcome_blind_and_has_no_external_join(self):
        sql = COVERAGE_ROWS_SQL.lower()
        self.assertNotIn(" join ", sql)
        for forbidden in (
            "outcome",
            "pnl",
            "future_return",
            "target_hit",
            "stop_hit",
            "r_multiple",
        ):
            self.assertNotIn(forbidden, sql)
        for required in (
            "contract_version",
            "direction",
            "thesis_state",
            "supporting_families",
            "opposing_families",
            "counted_families",
            "families",
        ):
            self.assertIn(required, sql)

    def test_empty_ledger_returns_zeroed_non_performance_summary(self):
        result = summarize_prospective_coverage_rows([])
        self.assertEqual(result["evaluations"], 0)
        self.assertEqual(result["directional_coverage_pct"], 0.0)
        self.assertEqual(result["contract_diagnostics"], {})
        self.assertEqual(result["by_contract_version"], {})


if __name__ == "__main__":
    unittest.main()
