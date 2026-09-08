from app.crypto_btc_dashboard_actions_api import _public_job, _request_id, architecture_contract


def test_server_generated_request_ids_are_unique_and_not_backdated_by_user():
    first = _request_id()
    second = _request_id()
    assert first.startswith("dashboard-")
    assert first != second


def test_dashboard_actions_remain_research_only():
    contract = architecture_contract()
    assert contract["user_backtest_allowed"] is True
    assert contract["user_backtest_reports_real_progress"] is True
    assert contract["user_live_shadow_setup_allowed"] is True
    assert contract["broker_order_placement_allowed"] is False
    assert contract["credentials_accepted_from_browser"] is False
    assert contract["caller_supplied_decision_time_allowed"] is False
    assert contract["futures_trade_generation_allowed"] is False
    assert contract["live_execution"] is False
    assert contract["capital_committed_inr"] == 0


def test_public_job_reports_determinate_click_progress():
    result = _public_job({
        "job_id": "job-1", "status": "RUNNING", "phase": "PROCESSING_CLICKS",
        "completed_clicks": 37, "total_clicks": 96, "started_at": "now",
    })
    assert result["completed_clicks"] == 37
    assert result["total_clicks"] == 96
    assert result["progress_pct"] == 38.5
    assert result["result"] is None
