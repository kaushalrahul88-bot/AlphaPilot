from pathlib import Path


def test_manual_api_is_options_only_pit_and_never_executes_orders():
    source = Path("app/crude_oil_mini_manual_api.py").read_text(encoding="utf-8")
    assert '/v1/crude-oil-mini/current-mind/click' in source
    assert '"trade_instrument": "OPTIONS_ONLY"' in source
    assert '"paper_signal_only": True' in source
    assert '"live_execution_enabled": False' in source
    assert '"broker_order_placement_enabled": False' in source
    assert '"capital_committed": 0' in source
    assert 'read_crude_oil_mini_pit_candles' in source
    assert 'collect_crude_oil_mini_pit_candles' in source
    assert 'commodity_candles(' not in source
    assert 'CRUDEOIL21' not in source
    assert 'expensive_180_day_live_refetch_used' in source


def test_manual_api_reports_market_closed_instead_of_fabricating_result():
    source = Path("app/crude_oil_mini_manual_api.py").read_text(encoding="utf-8")
    assert 'SKIPPED_MARKET_CLOSED' in source
    assert 'if not session.get("is_open")' in source


def test_manual_api_attaches_premium_memory_without_allowing_decision_effect():
    source = Path("app/crude_oil_mini_manual_api.py").read_text(encoding="utf-8")
    assert 'read_crude_oil_mini_premium_memory' in source
    assert 'result["data"]["option_premium_memory"] = premium_memory' in source
    assert '"risk_translation_effect": "NONE"' in source
    assert '"current_mind_effect": "NONE"' in source
    assert '"promotion_eligible": False' in source
