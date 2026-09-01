from app.crude_oil_context_source_inventory import crude_oil_context_source_inventory_v1


def test_only_local_tape_is_currently_full_window_pit_ready():
    report = crude_oil_context_source_inventory_v1()
    feeds = report["feeds"]
    assert feeds["MCX_CRUDEOILM"]["status"] == "FULL_WINDOW_PIT_READY"
    assert feeds["MCX_CRUDEOILM"]["ablation_ready"] is True
    assert report["optional_context_ablation_ready"] == []
    assert report["full_window_context_ablation_ready"] is False


def test_current_snapshot_feeds_are_not_relabelled_as_historical_context():
    feeds = crude_oil_context_source_inventory_v1()["feeds"]
    for series in ("WTI_CRUDE", "BRENT_CRUDE", "USDINR", "DXY"):
        assert feeds[series]["decision_use_now"] is False
        assert feeds[series]["ablation_ready"] is False
        assert "HISTORICAL_INTRADAY_SOURCE_REQUIRED" == feeds[series]["status"]


def test_eia_reconstructed_consensus_is_not_directionally_eligible():
    eia = crude_oil_context_source_inventory_v1()["feeds"]["EIA_CRUDE_INVENTORY"]
    assert eia["status"] == "PARTIAL_PIT_ACTUALS_ONLY"
    assert eia["coverage"]["records"] > 0
    assert eia["coverage"]["pit_safe_expectation_records"] == 0
    assert eia["coverage"]["directional_surprise_ready"] is False
    assert eia["decision_use_now"] is False


def test_news_and_options_remain_outside_no_news_market_brain():
    feeds = crude_oil_context_source_inventory_v1()["feeds"]
    assert feeds["CRUDE_NEWS"]["status"] == "DISABLED_UNTIL_NO_NEWS_FREEZE"
    assert feeds["MCX_CRUDEOILM_OPTION"]["status"] == "FORWARD_COLLECTION_ONLY"
    assert feeds["CRUDE_NEWS"]["decision_use_now"] is False
    assert feeds["MCX_CRUDEOILM_OPTION"]["decision_use_now"] is False
