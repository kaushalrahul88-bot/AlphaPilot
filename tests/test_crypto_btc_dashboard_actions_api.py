from app.crypto_btc_dashboard_actions_api import _request_id, architecture_contract


def test_server_generated_request_ids_are_unique_and_not_backdated_by_user():
    first = _request_id()
    second = _request_id()
    assert first.startswith("dashboard-")
    assert first != second


def test_dashboard_actions_remain_research_only():
    contract = architecture_contract()
    assert contract["user_backtest_allowed"] is True
    assert contract["user_live_shadow_setup_allowed"] is True
    assert contract["broker_order_placement_allowed"] is False
    assert contract["credentials_accepted_from_browser"] is False
    assert contract["caller_supplied_decision_time_allowed"] is False
    assert contract["futures_trade_generation_allowed"] is False
    assert contract["live_execution"] is False
    assert contract["capital_committed_inr"] == 0
