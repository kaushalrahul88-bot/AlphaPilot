from app.crude_oil_domain_knowledge_context_map import crude_domain_context_map_v1


def test_crude_domain_context_map_is_metadata_only():
    report = crude_domain_context_map_v1()
    assert report["decision_effect"] == "NONE"
    assert report["policy"]["series_must_be_observed_point_in_time"] is True
    assert report["policy"]["missing_series_stays_unknown"] is True
    assert report["policy"]["mapping_does_not_create_directional_vote"] is True
    assert report["policy"]["news_series_remains_disabled_in_no_news_baseline"] is True
    assert report["policy"]["option_series_remains_disabled_in_underlying_market_brain_baseline"] is True


def test_crude_domain_context_map_covers_core_families():
    series = crude_domain_context_map_v1()["series_by_family"]
    assert "WTI_CRUDE" in series["cross_market"]
    assert "EIA_CRUDE_INVENTORY" in series["inventories"]
    assert "EIA_REFINERY_UTILIZATION" in series["refining"]
    assert "OPEC_POLICY" in series["supply"]
    assert "WTI_CURVE" in series["term_structure"]
    assert "MCX_CRUDEOILM_OPTION" in series["options_volatility"]
