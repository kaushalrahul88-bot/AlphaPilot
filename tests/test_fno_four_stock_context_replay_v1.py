from app import fno_candle_only_four_stock_backtest_v1 as baseline
from app.fno_four_stock_context_enrichment_v1 import PROTOCOL_ID, architecture_contract


def test_context_protocol_is_separate_from_baseline():
    assert PROTOCOL_ID != baseline.PROTOCOL_ID
    assert architecture_contract()["frozen_baseline_protocol"] == baseline.PROTOCOL_ID


def test_context_protocol_keeps_exact_four_stock_identity():
    assert architecture_contract()["frozen_stocks"] == ["ONGC", "LTIM", "SBIN", "SUNPHARMA"]


def test_context_protocol_cannot_use_outcomes_as_features():
    contract = architecture_contract()
    assert contract["outcome_used_to_build_context"] is False
    assert contract["thresholds_frozen_before_enriched_outcomes"] is True
