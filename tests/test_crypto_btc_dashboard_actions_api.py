from app.crypto_btc_backtest_history import architecture_contract as history_contract, compact_backtest_result
from app.crypto_btc_dashboard_actions_api import _public_job, _request_id, architecture_contract


def test_server_generated_request_ids_are_unique_and_not_backdated_by_user():
    first = _request_id()
    second = _request_id()
    assert first.startswith("dashboard-")
    assert first != second


def test_dashboard_actions_remain_research_only_and_history_enabled():
    contract = architecture_contract()
    assert contract["user_backtest_allowed"] is True
    assert contract["user_backtest_reports_real_progress"] is True
    assert contract["user_backtest_progress_checkpointed"] is True
    assert contract["interrupted_backtests_reconciled"] is True
    assert contract["user_backtest_history_persisted"] is True
    assert contract["user_backtest_history_readable"] is True
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


def test_history_scorecard_omits_large_click_tape():
    compact = compact_backtest_result({
        "status": "COMPLETED",
        "window_start": "2026-09-05T00:00:00+00:00",
        "window_end_exclusive": "2026-09-06T00:00:00+00:00",
        "scheduled_clicks": 96,
        "summary": {"resolved_setups": 8, "total_r": 2.5},
        "clicks": [{"click_index": index} for index in range(96)],
    })
    assert compact == {
        "status": "COMPLETED",
        "window_start": "2026-09-05T00:00:00+00:00",
        "window_end_exclusive": "2026-09-06T00:00:00+00:00",
        "scheduled_clicks": 96,
        "summary": {"resolved_setups": 8, "total_r": 2.5},
    }
    assert "clicks" not in compact


def test_history_contract_is_durable_and_retains_full_terminal_result():
    contract = history_contract()
    assert contract["backend"] == "POSTGRES"
    assert contract["completed_runs_persisted"] is True
    assert contract["failed_runs_persisted"] is True
    assert contract["running_progress_checkpointed"] is True
    assert contract["stale_running_jobs_reconciled"] is True
    assert contract["survives_browser_refresh"] is True
    assert contract["survives_api_deploy"] is True
    assert contract["full_terminal_result_retained"] is True
    assert contract["history_list_uses_compact_scorecards"] is True
